"""
Phase 5 Tests: Autonomous Medicine Scheduler
Verifies deterministic notification idempotency, dose window math, persistent intake exclusions,
atomic conditional claim/lease concurrency, retry policies, failure terminal states,
timezone handling (Asia/Kolkata), non-creation of MISSED intake rows, and schema immutability.
"""

import os
import sys
import tempfile
import unittest
import datetime
import uuid
import threading
import sqlite3
from zoneinfo import ZoneInfo

from config import Config
from services.database import DatabaseService
from services.sns_service import SNSService
from workers.medicine_scheduler import run_scheduler_once


class MockFailingSNSService:
    """Mock SNS Service that simulates delivery failure."""
    def __init__(self):
        self.published = []

    def publish_notification(self, patient_id: str, message: str, subject: str = "MedTrack Notification") -> bool:
        return False


class MockSuccessfulSNSService:
    """Mock SNS Service that captures dispatches and succeeds."""
    def __init__(self):
        self.published = []

    def publish_notification(self, patient_id: str, message: str, subject: str = "MedTrack Notification") -> bool:
        self.published.append({
            "patient_id": patient_id,
            "message": message,
            "subject": subject
        })
        return True


class TestPhase5Scheduler(unittest.TestCase):
    """Phase 5 Autonomous Medicine Reminder Scheduler Test Suite."""

    def setUp(self):
        # Isolated temporary database for every test
        self.temp_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_fd)

        self.db = DatabaseService()
        self.db.db_path = self.temp_db_path
        self.db._init_sqlite()

        # Enable WAL mode for smooth multithreaded concurrency in tests
        with self.db._get_sqlite_conn() as conn:
            conn.execute("PRAGMA journal_mode = WAL;")

        self.tz = ZoneInfo(getattr(Config, "APP_TIMEZONE", "Asia/Kolkata"))
        self.test_date = "2026-09-19"
        self.sns = MockSuccessfulSNSService()

        # Create test patient
        self.patient = self.db.create_user({
            "name": "Phase 5 Patient",
            "email": f"p5_{uuid.uuid4().hex[:8]}@medtrack.local",
            "password_hash": "hash123",
            "phone": "+919876543210",
            "date_of_birth": "1990-01-01",
            "gender": "Other",
            "role": "patient",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        })
        self.patient_id = self.patient["user_id"]

    def tearDown(self):
        if os.path.exists(self.temp_db_path):
            try:
                os.remove(self.temp_db_path)
            except Exception:
                pass

    def _create_test_med(self, times, name="SchedMed", patient_id=None):
        pid = patient_id or self.patient_id
        return self.db.create_medicine(
            patient_id=pid,
            name=name,
            dosage="50mg",
            schedule_time=times[0],
            schedule_times=times,
            frequency="Daily",
            meal_timing="After Food"
        )

    def test_01_eligible_unlogged_dose_creates_exactly_one_notification(self):
        """TEST 1: Eligible unlogged dose in reminder window creates exactly one notification."""
        med = self._create_test_med(["08:00"])
        eval_now = datetime.datetime(2026, 9, 19, 8, 0, 0, tzinfo=self.tz)

        metrics = run_scheduler_once(db=self.db, sns=self.sns, now=eval_now)
        self.assertEqual(metrics["eligible"], 1)
        self.assertEqual(metrics["created"], 1)
        self.assertEqual(metrics["sent"], 1)

        expected_id = f"remind#{self.patient_id}#{med['medicine_id']}#2026-09-19#08:00"
        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT notification_id, delivery_status, sent_at FROM notifications WHERE notification_id = ?", (expected_id,))
            row = c.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["delivery_status"], "SENT")
            self.assertIsNotNone(row["sent_at"])

    def test_02_scheduler_worker_idempotency_run_twice(self):
        """TEST 2: Running scheduler twice for the same window creates exactly one notification."""
        med = self._create_test_med(["08:00"])
        eval_now = datetime.datetime(2026, 9, 19, 8, 10, 0, tzinfo=self.tz)

        run1 = run_scheduler_once(db=self.db, sns=self.sns, now=eval_now)
        self.assertEqual(run1["created"], 1)
        self.assertEqual(run1["sent"], 1)

        run2 = run_scheduler_once(db=self.db, sns=self.sns, now=eval_now)
        self.assertEqual(run2["created"], 0, "Second run must not create duplicate notification")
        self.assertEqual(run2["claimed"], 0, "Second run must not re-claim already sent notification")

        expected_id = f"remind#{self.patient_id}#{med['medicine_id']}#2026-09-19#08:00"
        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM notifications WHERE notification_id = ?", (expected_id,))
            self.assertEqual(c.fetchone()[0], 1)

    def test_03_taken_dose_creates_no_reminder(self):
        """TEST 3: Dose already marked TAKEN creates no reminder notification."""
        med = self._create_test_med(["08:00"])
        self.db.record_intake(self.patient_id, med["medicine_id"], "TAKEN",
                              scheduled_date=self.test_date, scheduled_time="08:00")

        eval_now = datetime.datetime(2026, 9, 19, 8, 0, 0, tzinfo=self.tz)
        metrics = run_scheduler_once(db=self.db, sns=self.sns, now=eval_now)
        self.assertEqual(metrics["eligible"], 0)
        self.assertEqual(metrics["created"], 0)

        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM notifications WHERE patient_id = ?", (self.patient_id,))
            self.assertEqual(c.fetchone()[0], 0)

    def test_04_skipped_dose_creates_no_reminder(self):
        """TEST 4: Dose already marked SKIPPED creates no reminder notification."""
        med = self._create_test_med(["08:00"])
        self.db.record_intake(self.patient_id, med["medicine_id"], "SKIPPED",
                              scheduled_date=self.test_date, scheduled_time="08:00")

        eval_now = datetime.datetime(2026, 9, 19, 8, 0, 0, tzinfo=self.tz)
        metrics = run_scheduler_once(db=self.db, sns=self.sns, now=eval_now)
        self.assertEqual(metrics["eligible"], 0)
        self.assertEqual(metrics["created"], 0)

        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM notifications WHERE patient_id = ?", (self.patient_id,))
            self.assertEqual(c.fetchone()[0], 0)

    def test_05_dose_before_window_creates_no_reminder(self):
        """TEST 5: Dose before reminder window (now < scheduled - 30m) creates no reminder."""
        self._create_test_med(["08:00"])
        # Window opens at 07:30. At 07:25 -> before window
        eval_now = datetime.datetime(2026, 9, 19, 7, 25, 0, tzinfo=self.tz)

        metrics = run_scheduler_once(db=self.db, sns=self.sns, now=eval_now)
        self.assertEqual(metrics["eligible"], 0)
        self.assertEqual(metrics["created"], 0)

        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM notifications WHERE patient_id = ?", (self.patient_id,))
            self.assertEqual(c.fetchone()[0], 0)

    def test_06_dose_after_window_creates_no_new_reminder(self):
        """TEST 6: Dose after reminder window (now > scheduled + 60m) creates no reminder."""
        self._create_test_med(["08:00"])
        # Window closes at 09:00. At 09:05 -> after window (MISSED in Phase 4)
        eval_now = datetime.datetime(2026, 9, 19, 9, 5, 0, tzinfo=self.tz)

        metrics = run_scheduler_once(db=self.db, sns=self.sns, now=eval_now)
        self.assertEqual(metrics["eligible"], 0)
        self.assertEqual(metrics["created"], 0)

        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM notifications WHERE patient_id = ?", (self.patient_id,))
            self.assertEqual(c.fetchone()[0], 0)

    def test_07_multiple_doses_create_independent_notifications(self):
        """TEST 7: Multiple distinct dose times create independent notifications."""
        med = self._create_test_med(["08:00", "14:00", "20:00"])

        # Run at 08:00 -> 08:00 created & sent
        run1 = run_scheduler_once(db=self.db, sns=self.sns, now=datetime.datetime(2026, 9, 19, 8, 0, 0, tzinfo=self.tz))
        self.assertEqual(run1["created"], 1)

        # Run at 14:00 -> 14:00 created & sent
        run2 = run_scheduler_once(db=self.db, sns=self.sns, now=datetime.datetime(2026, 9, 19, 14, 0, 0, tzinfo=self.tz))
        self.assertEqual(run2["created"], 1)

        id1 = f"remind#{self.patient_id}#{med['medicine_id']}#2026-09-19#08:00"
        id2 = f"remind#{self.patient_id}#{med['medicine_id']}#2026-09-19#14:00"
        self.assertNotEqual(id1, id2)

        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM notifications WHERE patient_id = ?", (self.patient_id,))
            self.assertEqual(c.fetchone()[0], 2)

    def test_08_two_patients_cannot_collide_on_notification_ids(self):
        """TEST 8: Two patients with identical medicine schedules generate distinct notification IDs."""
        patient_b = self.db.create_user({
            "name": "Patient B",
            "email": "pat_b@medtrack.local",
            "password_hash": "hash123",
            "phone": "+919876543211",
            "date_of_birth": "1992-02-02",
            "gender": "Female",
            "role": "patient",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        })
        med_a = self._create_test_med(["08:00"], patient_id=self.patient_id)
        med_b = self._create_test_med(["08:00"], patient_id=patient_b["user_id"])

        eval_now = datetime.datetime(2026, 9, 19, 8, 0, 0, tzinfo=self.tz)
        metrics = run_scheduler_once(db=self.db, sns=self.sns, now=eval_now)
        self.assertEqual(metrics["created"], 2)

        id_a = f"remind#{self.patient_id}#{med_a['medicine_id']}#2026-09-19#08:00"
        id_b = f"remind#{patient_b['user_id']}#{med_b['medicine_id']}#2026-09-19#08:00"
        self.assertNotEqual(id_a, id_b)

        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM notifications")
            self.assertEqual(c.fetchone()[0], 2)

    def test_09_atomic_notification_creation_is_idempotent(self):
        """TEST 9: Atomic notification creation: duplicate INSERT triggers ON CONFLICT DO NOTHING."""
        notif_id = f"remind#{self.patient_id}#med-1#2026-09-19#08:00"
        ins1 = self.db.create_reminder_notification(
            notification_id=notif_id,
            patient_id=self.patient_id,
            title="Reminder 1",
            message="Msg 1",
            scheduled_date="2026-09-19",
            scheduled_time="08:00"
        )
        self.assertTrue(ins1, "First insert should succeed and return True")

        # Second insert with identical ID
        ins2 = self.db.create_reminder_notification(
            notification_id=notif_id,
            patient_id=self.patient_id,
            title="Reminder 2",
            message="Msg 2",
            scheduled_date="2026-09-19",
            scheduled_time="08:00"
        )
        self.assertFalse(ins2, "Duplicate insert should be ignored by ON CONFLICT and return False")

        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*), title FROM notifications WHERE notification_id = ?", (notif_id,))
            cnt, title = c.fetchone()
            self.assertEqual(cnt, 1)
            self.assertEqual(title, "Reminder 1", "Original row must remain unmodified")

    def test_10_concurrent_claim_race_condition(self):
        """
        TEST 10: True concurrent claim attempts against the same notification.
        Verifies:
        - exactly one worker wins the claim
        - exactly one CLAIMED notification in the DB
        - delivery_attempts increments exactly once (0 to 1)
        - losing worker gets 0 claimed rows and cannot publish
        """
        notif_id = f"remind#{self.patient_id}#med-conc#2026-09-19#08:00"
        self.db.create_reminder_notification(
            notification_id=notif_id,
            patient_id=self.patient_id,
            title="Reminder",
            message="Msg",
            scheduled_date="2026-09-19",
            scheduled_time="08:00"
        )

        eval_now = datetime.datetime(2026, 9, 19, 8, 0, 0, tzinfo=self.tz)
        worker_a_claims = []
        worker_b_claims = []

        barrier = threading.Barrier(2)

        def worker_a_action():
            db_a = DatabaseService()
            db_a.db_path = self.temp_db_path
            barrier.wait()
            claims = db_a.claim_pending_notifications(limit=10, lease_seconds=120, now=eval_now)
            worker_a_claims.extend(claims)

        def worker_b_action():
            db_b = DatabaseService()
            db_b.db_path = self.temp_db_path
            barrier.wait()
            claims = db_b.claim_pending_notifications(limit=10, lease_seconds=120, now=eval_now)
            worker_b_claims.extend(claims)

        t1 = threading.Thread(target=worker_a_action)
        t2 = threading.Thread(target=worker_b_action)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one worker wins the claim
        total_claims = len(worker_a_claims) + len(worker_b_claims)
        self.assertEqual(total_claims, 1, f"Expected exactly 1 claim across concurrent workers, got {total_claims}")

        winning_claim = worker_a_claims[0] if worker_a_claims else worker_b_claims[0]
        self.assertEqual(winning_claim["notification_id"], notif_id)

        # Verify DB state
        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT delivery_status, delivery_attempts, delivery_claim_id FROM notifications WHERE notification_id = ?", (notif_id,))
            row = c.fetchone()
            self.assertEqual(row["delivery_status"], "CLAIMED")
            self.assertEqual(row["delivery_attempts"], 1, "delivery_attempts must increment exactly once")
            self.assertEqual(row["delivery_claim_id"], winning_claim["delivery_claim_id"])

    def test_11_active_claim_prevents_duplicate_delivery(self):
        """TEST 11: Active claim with valid lease prevents subsequent worker from claiming."""
        notif_id = f"remind#{self.patient_id}#med-act#2026-09-19#08:00"
        self.db.create_reminder_notification(
            notification_id=notif_id,
            patient_id=self.patient_id,
            title="Reminder",
            message="Msg",
            scheduled_date="2026-09-19",
            scheduled_time="08:00"
        )

        now1 = datetime.datetime(2026, 9, 19, 8, 0, 0, tzinfo=self.tz)
        claims1 = self.db.claim_pending_notifications(limit=10, lease_seconds=120, now=now1)
        self.assertEqual(len(claims1), 1)

        # Worker 2 runs 30 seconds later (lease still valid for 90s)
        now2 = datetime.datetime(2026, 9, 19, 8, 0, 30, tzinfo=self.tz)
        claims2 = self.db.claim_pending_notifications(limit=10, lease_seconds=120, now=now2)
        self.assertEqual(len(claims2), 0, "Worker 2 must not claim an actively leased notification")

    def test_12_expired_claim_can_be_reclaimed(self):
        """TEST 12: Expired claim lease can be reclaimed by subsequent worker."""
        notif_id = f"remind#{self.patient_id}#med-exp#2026-09-19#08:00"
        self.db.create_reminder_notification(
            notification_id=notif_id,
            patient_id=self.patient_id,
            title="Reminder",
            message="Msg",
            scheduled_date="2026-09-19",
            scheduled_time="08:00"
        )

        now1 = datetime.datetime(2026, 9, 19, 8, 0, 0, tzinfo=self.tz)
        claims1 = self.db.claim_pending_notifications(limit=10, lease_seconds=120, now=now1)
        self.assertEqual(len(claims1), 1)
        claim_id_1 = claims1[0]["delivery_claim_id"]

        # Worker 2 runs after lease expires (130 seconds later)
        now2 = datetime.datetime(2026, 9, 19, 8, 2, 10, tzinfo=self.tz)
        claims2 = self.db.claim_pending_notifications(limit=10, lease_seconds=120, now=now2)
        self.assertEqual(len(claims2), 1, "Expired lease should be successfully reclaimed")
        claim_id_2 = claims2[0]["delivery_claim_id"]

        self.assertNotEqual(claim_id_1, claim_id_2, "New claim must have a new claim_id")
        self.assertEqual(claims2[0]["delivery_attempts"], 2, "delivery_attempts must be incremented to 2")

    def test_13_successful_sns_publish_results_in_sent(self):
        """TEST 13: Successful SNS publish results in delivery_status = SENT and sent_at recorded."""
        self._create_test_med(["08:00"])
        eval_now = datetime.datetime(2026, 9, 19, 8, 0, 0, tzinfo=self.tz)

        metrics = run_scheduler_once(db=self.db, sns=self.sns, now=eval_now)
        self.assertEqual(metrics["sent"], 1)

        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT delivery_status, sent_at, delivery_attempts FROM notifications")
            row = c.fetchone()
            self.assertEqual(row["delivery_status"], "SENT")
            self.assertIsNotNone(row["sent_at"])
            self.assertEqual(row["delivery_attempts"], 1)

    def test_14_failed_publish_retries_and_clears_claim(self):
        """TEST 14: Failed SNS publish with attempt 1 reverts to PENDING and clears claim fields."""
        self._create_test_med(["08:00"])
        eval_now = datetime.datetime(2026, 9, 19, 8, 0, 0, tzinfo=self.tz)
        failing_sns = MockFailingSNSService()

        metrics = run_scheduler_once(db=self.db, sns=failing_sns, now=eval_now)
        self.assertEqual(metrics["retried"], 1)
        self.assertEqual(metrics["sent"], 0)

        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT delivery_status, delivery_attempts, delivery_claim_id, delivery_lease_until FROM notifications")
            row = c.fetchone()
            self.assertEqual(row["delivery_status"], "PENDING", "Should revert to PENDING for retry")
            self.assertEqual(row["delivery_attempts"], 1)
            self.assertIsNone(row["delivery_claim_id"], "Claim ID must be cleared for retry")
            self.assertIsNone(row["delivery_lease_until"], "Lease until must be cleared for retry")

    def test_15_third_failure_results_in_failed(self):
        """TEST 15: Third delivery failure transitions notification to FAILED."""
        notif_id = f"remind#{self.patient_id}#med-fail#2026-09-19#08:00"
        self.db.create_reminder_notification(
            notification_id=notif_id,
            patient_id=self.patient_id,
            title="Reminder",
            message="Msg",
            scheduled_date="2026-09-19",
            scheduled_time="08:00"
        )

        failing_sns = MockFailingSNSService()
        base_time = datetime.datetime(2026, 9, 19, 8, 0, 0, tzinfo=self.tz)

        # Attempt 1
        run_scheduler_once(db=self.db, sns=failing_sns, now=base_time)
        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT delivery_status, delivery_attempts FROM notifications WHERE notification_id = ?", (notif_id,))
            s1, a1 = c.fetchone()
            self.assertEqual(s1, "PENDING")
            self.assertEqual(a1, 1)

        # Attempt 2
        run_scheduler_once(db=self.db, sns=failing_sns, now=base_time + datetime.timedelta(minutes=5))
        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT delivery_status, delivery_attempts FROM notifications WHERE notification_id = ?", (notif_id,))
            s2, a2 = c.fetchone()
            self.assertEqual(s2, "PENDING")
            self.assertEqual(a2, 2)

        # Attempt 3 -> FAILED
        run_scheduler_once(db=self.db, sns=failing_sns, now=base_time + datetime.timedelta(minutes=10))
        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT delivery_status, delivery_attempts FROM notifications WHERE notification_id = ?", (notif_id,))
            s3, a3 = c.fetchone()
            self.assertEqual(s3, "FAILED", "Third failure must transition to FAILED")
            self.assertEqual(a3, 3)

    def test_16_notification_remains_visible_after_failure(self):
        """TEST 16: Notification is never deleted upon failure and remains user-visible."""
        notif_id = f"remind#{self.patient_id}#med-vis#2026-09-19#08:00"
        self.db.create_reminder_notification(
            notification_id=notif_id,
            patient_id=self.patient_id,
            title="Important Dose Alert",
            message="Take your med",
            scheduled_date="2026-09-19",
            scheduled_time="08:00"
        )
        # Mark FAILED directly
        with self.db._get_sqlite_conn() as conn:
            conn.execute("UPDATE notifications SET delivery_status = 'FAILED', delivery_attempts = 3 WHERE notification_id = ?", (notif_id,))
            conn.commit()

        # Patient views notifications
        patient_notifs = self.db.get_notifications_by_patient(self.patient_id)
        self.assertEqual(len(patient_notifs), 1)
        self.assertEqual(patient_notifs[0]["notification_id"], notif_id)
        self.assertEqual(patient_notifs[0]["delivery_status"], "FAILED")

    def test_17_asia_kolkata_timezone_accuracy(self):
        """TEST 17: Scheduler evaluates reminder eligibility against Config.APP_TIMEZONE (Asia/Kolkata)."""
        self._create_test_med(["08:00"])
        # 08:00 IST is 02:30 UTC.
        # When UTC is 02:30, it is 08:00 IST -> inside reminder window -> eligible
        now_utc_due = datetime.datetime(2026, 9, 19, 2, 30, 0, tzinfo=datetime.timezone.utc)
        metrics_due = run_scheduler_once(db=self.db, sns=self.sns, now=now_utc_due)
        self.assertEqual(metrics_due["eligible"], 1)

        # 07:25 IST is 01:55 UTC -> before window -> not eligible
        now_utc_before = datetime.datetime(2026, 9, 19, 1, 55, 0, tzinfo=datetime.timezone.utc)
        metrics_before = run_scheduler_once(db=self.db, sns=self.sns, now=now_utc_before)
        self.assertEqual(metrics_before["eligible"], 0)

    def test_18_scheduler_does_not_create_missed_intake_rows(self):
        """TEST 18: Scheduler does not insert, update, or create any MISSED intake_logs rows."""
        self._create_test_med(["08:00"])
        # Evaluate at 15:00 (far past window)
        eval_now = datetime.datetime(2026, 9, 19, 15, 0, 0, tzinfo=self.tz)

        metrics = run_scheduler_once(db=self.db, sns=self.sns, now=eval_now)
        self.assertEqual(metrics["eligible"], 0)

        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM intake_logs WHERE patient_id = ?", (self.patient_id,))
            self.assertEqual(c.fetchone()[0], 0, "Scheduler must NEVER create intake_logs rows")

    def test_19_schema_immutability(self):
        """TEST 19: Database schemas for medicines, intake_logs, and notifications remain unaltered."""
        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()

            # Check medicines columns
            c.execute("PRAGMA table_info(medicines)")
            med_cols = {row[1] for row in c.fetchall()}
            required_med_cols = {
                "medicine_id", "patient_id", "name", "dosage", "schedule_times",
                "schedule_time", "frequency", "meal_timing", "is_active", "created_at"
            }
            self.assertTrue(required_med_cols.issubset(med_cols))

            # Check intake_logs columns
            c.execute("PRAGMA table_info(intake_logs)")
            intake_cols = {row[1] for row in c.fetchall()}
            required_intake_cols = {
                "log_id", "patient_id", "medicine_id", "medicine_name",
                "dosage", "scheduled_date", "scheduled_time", "status", "created_at"
            }
            self.assertTrue(required_intake_cols.issubset(intake_cols))

            # Check notifications columns
            c.execute("PRAGMA table_info(notifications)")
            notif_cols = {row[1] for row in c.fetchall()}
            required_notif_cols = {
                "notification_id", "patient_id", "type", "title", "message",
                "scheduled_date", "scheduled_time", "delivery_status", "delivery_attempts",
                "delivery_claim_id", "delivery_claimed_at", "delivery_lease_until",
                "sent_at", "status", "is_read", "created_at"
            }
            self.assertTrue(required_notif_cols.issubset(notif_cols))

    def test_20_claim_only_medicine_reminders_isolation(self):
        """
        TEST 20 (ISSUE 1 REGRESSION): Scheduler / claim_pending_notifications claims ONLY 'medicine_reminder'
        notifications. Unrelated PENDING notifications (GENERAL, APPOINTMENT) are NOT claimed.
        """
        # Create general notification
        self.db.create_notification(
            patient_id=self.patient_id,
            title="General System Update",
            message="System maintenance scheduled",
            notif_type="GENERAL"
        )
        # Create appointment notification
        self.db.create_notification(
            patient_id=self.patient_id,
            title="Doctor Appointment",
            message="Appointment tomorrow at 10 AM",
            notif_type="APPOINTMENT"
        )
        # Create medicine reminder notification
        med_notif_id = f"remind#{self.patient_id}#med-1#2026-09-19#08:00"
        self.db.create_reminder_notification(
            notification_id=med_notif_id,
            patient_id=self.patient_id,
            title="Medicine Reminder",
            message="Take 50mg Aspirin",
            scheduled_date="2026-09-19",
            scheduled_time="08:00"
        )

        eval_now = datetime.datetime(2026, 9, 19, 8, 0, 0, tzinfo=self.tz)
        claimed = self.db.claim_pending_notifications(
            limit=50,
            lease_seconds=120,
            now=eval_now,
            notification_type="medicine_reminder"
        )

        # Only the medicine_reminder should be claimed
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["notification_id"], med_notif_id)
        self.assertEqual(claimed[0]["type"], "medicine_reminder")

        # Verify other notifications remain untouched with delivery_status = PENDING
        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT notification_id, type, delivery_status FROM notifications WHERE type != 'medicine_reminder'")
            other_rows = c.fetchall()
            self.assertEqual(len(other_rows), 2)
            for r in other_rows:
                self.assertEqual(r["delivery_status"], "PENDING")

    def test_21_midnight_window_2330_dose_at_2315_same_day_eligible(self):
        """
        TEST 21 (ISSUE 2.1 REGRESSION): 23:30 dose evaluated at 23:15 same day -> eligible.
        """
        med = self._create_test_med(["23:30"])
        # Same day 23:15 is inside reminder window [23:00, 00:30 next day]
        eval_now = datetime.datetime(2026, 9, 18, 23, 15, 0, tzinfo=self.tz)

        metrics = run_scheduler_once(db=self.db, sns=self.sns, now=eval_now)
        self.assertEqual(metrics["eligible"], 1)
        self.assertEqual(metrics["created"], 1)
        self.assertEqual(metrics["sent"], 1)

        expected_id = f"remind#{self.patient_id}#{med['medicine_id']}#2026-09-18#23:30"
        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT notification_id, delivery_status FROM notifications WHERE notification_id = ?", (expected_id,))
            row = c.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["delivery_status"], "SENT")

    def test_22_midnight_window_2330_dose_at_0015_next_day_eligible(self):
        """
        TEST 22 (ISSUE 2.2 & 2.5 REGRESSION): 23:30 dose evaluated at 00:15 next day -> still eligible,
        and notification ID uses previous day's scheduled_date.
        """
        med = self._create_test_med(["23:30"])
        # Next calendar day at 00:15 IST (dose belongs to previous date 2026-09-18)
        # Window: 2026-09-18 23:00 to 2026-09-19 00:30
        eval_now = datetime.datetime(2026, 9, 19, 0, 15, 0, tzinfo=self.tz)

        metrics = run_scheduler_once(db=self.db, sns=self.sns, now=eval_now)
        self.assertEqual(metrics["eligible"], 1)
        self.assertEqual(metrics["created"], 1)
        self.assertEqual(metrics["sent"], 1)

        # Must use previous day's scheduled_date (2026-09-18)
        expected_id = f"remind#{self.patient_id}#{med['medicine_id']}#2026-09-18#23:30"
        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT notification_id, scheduled_date, scheduled_time, delivery_status FROM notifications WHERE notification_id = ?", (expected_id,))
            row = c.fetchone()
            self.assertIsNotNone(row, f"Expected notification ID {expected_id} not found in DB")
            self.assertEqual(row["scheduled_date"], "2026-09-18", "Notification must preserve previous calendar date")
            self.assertEqual(row["scheduled_time"], "23:30")
            self.assertEqual(row["delivery_status"], "SENT")

    def test_23_midnight_window_2330_dose_at_0031_next_day_not_eligible(self):
        """
        TEST 23 (ISSUE 2.3 REGRESSION): 23:30 dose evaluated at 00:31 next day -> window expired (+61 min) -> not eligible.
        """
        self._create_test_med(["23:30"])
        # Window ended at 00:30:00. At 00:31:00 -> not eligible
        eval_now = datetime.datetime(2026, 9, 19, 0, 31, 0, tzinfo=self.tz)

        metrics = run_scheduler_once(db=self.db, sns=self.sns, now=eval_now)
        self.assertEqual(metrics["eligible"], 0)
        self.assertEqual(metrics["created"], 0)
        self.assertEqual(metrics["sent"], 0)

        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM notifications")
            self.assertEqual(c.fetchone()[0], 0)

    def test_24_midnight_window_run_twice_at_0015_exactly_one_notification(self):
        """
        TEST 24 (ISSUE 2.4 REGRESSION): Running scheduler twice at 00:15 next day produces exactly one notification.
        """
        med = self._create_test_med(["23:30"])
        eval_now = datetime.datetime(2026, 9, 19, 0, 15, 0, tzinfo=self.tz)

        run1 = run_scheduler_once(db=self.db, sns=self.sns, now=eval_now)
        self.assertEqual(run1["created"], 1)
        self.assertEqual(run1["sent"], 1)

        run2 = run_scheduler_once(db=self.db, sns=self.sns, now=eval_now)
        self.assertEqual(run2["created"], 0, "Second run at 00:15 must not create duplicate notification")
        self.assertEqual(run2["claimed"], 0, "Second run must not re-claim already sent notification")

        expected_id = f"remind#{self.patient_id}#{med['medicine_id']}#2026-09-18#23:30"
        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM notifications WHERE notification_id = ?", (expected_id,))
            self.assertEqual(c.fetchone()[0], 1)

    def test_25_midnight_window_evaluated_at_2315_and_0015_no_duplicate(self):
        """
        TEST 25 (ISSUE 2 BOUNDARY REGRESSION): Dose evaluated at 23:15 same day, then evaluated again at 00:15 next day
        (both within window) creates NO duplicate notification and no re-delivery.
        """
        med = self._create_test_med(["23:30"])
        now_2315 = datetime.datetime(2026, 9, 18, 23, 15, 0, tzinfo=self.tz)
        run1 = run_scheduler_once(db=self.db, sns=self.sns, now=now_2315)
        self.assertEqual(run1["created"], 1)
        self.assertEqual(run1["sent"], 1)

        now_0015 = datetime.datetime(2026, 9, 19, 0, 15, 0, tzinfo=self.tz)
        run2 = run_scheduler_once(db=self.db, sns=self.sns, now=now_0015)
        self.assertEqual(run2["created"], 0, "No duplicate notification created across midnight boundary")
        self.assertEqual(run2["claimed"], 0, "No re-claim of already sent notification across midnight boundary")

        expected_id = f"remind#{self.patient_id}#{med['medicine_id']}#2026-09-18#23:30"
        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM notifications WHERE notification_id = ?", (expected_id,))
            self.assertEqual(c.fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
