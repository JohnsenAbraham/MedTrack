"""
MedTrack Authentication Rate Limiter
Lightweight, thread-safe, in-memory dual-key sliding-window rate limiter.
Tracks failed authentication attempts by client IP and account email.
"""

import time
import threading
from collections import defaultdict


class LoginRateLimiter:
    """
    In-memory dual-key sliding-window rate limiter for authentication endpoints.
    Protects against brute-force and credential stuffing attacks without external infrastructure dependencies.
    """

    def __init__(self,
                 ip_max_attempts: int = 5,
                 ip_window_seconds: int = 300,        # 5 minutes
                 account_max_attempts: int = 5,
                 account_window_seconds: int = 900,   # 15 minutes
                 max_keys: int = 10000):
        self.ip_max_attempts = ip_max_attempts
        self.ip_window_seconds = ip_window_seconds
        self.account_max_attempts = account_max_attempts
        self.account_window_seconds = account_window_seconds
        self.max_keys = max_keys

        # Mapping: key -> list of float timestamps of failed attempts
        self._attempts = defaultdict(list)
        self._lock = threading.Lock()

    def _purge_old_timestamps(self, key: str, window: int, now: float) -> list:
        """Purge timestamps older than the evaluation window for a key."""
        valid = [ts for ts in self._attempts[key] if (now - ts) < window]
        if valid:
            self._attempts[key] = valid
        else:
            self._attempts.pop(key, None)
        return valid

    def _enforce_memory_cap(self):
        """Evict oldest entries if total keys exceed maximum capacity."""
        if len(self._attempts) > self.max_keys:
            now = time.time()
            # First pass: drop any keys whose entries are all older than max window
            keys_to_drop = []
            for k, timestamps in list(self._attempts.items()):
                if not timestamps or (now - max(timestamps) >= self.account_window_seconds):
                    keys_to_drop.append(k)
            for k in keys_to_drop:
                self._attempts.pop(k, None)

            # Second pass if still over cap: evict oldest 2000 entries
            if len(self._attempts) > self.max_keys:
                sorted_keys = sorted(
                    self._attempts.keys(),
                    key=lambda k: max(self._attempts[k]) if self._attempts[k] else 0
                )
                for k in sorted_keys[:2000]:
                    self._attempts.pop(k, None)

    def check_rate_limit(self, ip: str, email: str) -> tuple[bool, int]:
        """
        Check whether login is blocked for either the client IP or the account.
        Returns:
            (is_blocked: bool, retry_after: int)
        """
        now = time.time()
        ip_key = f"ip:{ip.strip()}"
        acct_key = f"account:{email.strip().lower()}" if email else None

        with self._lock:
            # 1. Evaluate IP boundary
            ip_attempts = self._purge_old_timestamps(ip_key, self.ip_window_seconds, now)
            ip_blocked = len(ip_attempts) >= self.ip_max_attempts
            ip_retry = 0
            if ip_blocked:
                oldest_relevant = ip_attempts[0]
                ip_retry = max(1, int(self.ip_window_seconds - (now - oldest_relevant)))

            # 2. Evaluate Account boundary
            acct_blocked = False
            acct_retry = 0
            if acct_key:
                acct_attempts = self._purge_old_timestamps(acct_key, self.account_window_seconds, now)
                acct_blocked = len(acct_attempts) >= self.account_max_attempts
                if acct_blocked:
                    oldest_relevant = acct_attempts[0]
                    acct_retry = max(1, int(self.account_window_seconds - (now - oldest_relevant)))

            if ip_blocked or acct_blocked:
                retry_after = max(ip_retry, acct_retry)
                return True, retry_after

            return False, 0

    def record_failed_attempt(self, ip: str, email: str):
        """
        Record a failed authentication attempt against both IP and account.
        """
        now = time.time()
        ip_key = f"ip:{ip.strip()}"
        acct_key = f"account:{email.strip().lower()}" if email else None

        with self._lock:
            self._attempts[ip_key].append(now)
            if acct_key:
                self._attempts[acct_key].append(now)
            self._enforce_memory_cap()

    def record_successful_login(self, email: str):
        """
        On successful authentication, clear ONLY the account failure bucket.
        Preserves the IP counter to prevent attackers from using valid credentials
        to wipe the IP-level failure history.
        """
        if not email:
            return
        acct_key = f"account:{email.strip().lower()}"
        with self._lock:
            self._attempts.pop(acct_key, None)

    def is_allowed(self, ip: str, email: str) -> tuple[bool, int, int, str]:
        """Convenience wrapper returning (is_allowed, remaining, retry_after, blocked_by)."""
        is_blocked, retry_after = self.check_rate_limit(ip, email)
        return (not is_blocked), 0, retry_after, ("ip_or_account" if is_blocked else "")

    def record_failure(self, ip: str, email: str):
        """Alias for record_failed_attempt."""
        self.record_failed_attempt(ip, email)

    def record_success(self, email: str):
        """Alias for record_successful_login."""
        self.record_successful_login(email)

    def reset_all(self):
        """Clear all rate limiting state (used for testing)."""
        with self._lock:
            self._attempts.clear()


# Global singleton instance for application use
rate_limiter = LoginRateLimiter()
