"""
MedTrack Storage Service - Medical Reports & Private Storage
Handles secure server-side file validation, storage key derivation,
and multi-tier storage operations:
  - Production: Private Amazon S3 with SSE-AES256 & short-lived pre-signed URLs
  - Local/Testing: Isolated filesystem directory with path traversal protection
"""

import os
import re
from pathlib import Path
from werkzeug.utils import secure_filename
from config import Config

# Allowed MIME types and extensions for medical diagnostic reports
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
ALLOWED_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg"
}

# Magic bytes signatures for MIME spoofing prevention
MAGIC_BYTES = {
    ".pdf": b"%PDF-",
    ".png": b"\x89PNG\r\n\x1a\n",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff"
}

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 Megabytes


class StorageService:
    """Manages medical report artifacts across local filesystem and Amazon S3."""

    def __init__(self, bucket_name=None, local_dir=None, region_name=None):
        self.bucket_name = bucket_name if bucket_name is not None else getattr(Config, "S3_REPORTS_BUCKET", "")
        self.region_name = region_name or getattr(Config, "AWS_REGION", "us-east-1")

        if local_dir:
            self._local_dir = Path(local_dir)
        else:
            self._local_dir = Config.BASE_DIR / "uploads" / "reports"

        self._local_dir.mkdir(parents=True, exist_ok=True)
        self._s3_client = None

    @property
    def local_dir(self) -> Path:
        return self._local_dir

    @local_dir.setter
    def local_dir(self, value):
        self._local_dir = Path(value) if value else Config.BASE_DIR / "uploads" / "reports"
        self._local_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_s3_enabled(self) -> bool:
        """Returns True if an S3 reports bucket is configured."""
        return bool(self.bucket_name and self.bucket_name.strip())

    def get_s3_client(self):
        """Lazy initialization of boto3 S3 client using IAM role or AWS environment."""
        if self._s3_client is None:
            import boto3
            self._s3_client = boto3.client("s3", region_name=self.region_name)
        return self._s3_client

    def validate_file(self, file_stream, filename: str) -> tuple[str, str, int]:
        """
        Validate file extension, size, magic bytes, and filename safety.
        Returns: (safe_filename, content_type, file_size)
        Raises: ValueError if file fails security constraints.
        """
        if not filename or not filename.strip():
            raise ValueError("File name cannot be empty.")

        # Strip directory components to prevent path traversal
        raw_name = Path(filename).name
        ext = Path(raw_name).suffix.lower()

        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError(f"Unsupported file format '{ext}'. Allowed formats: PDF, PNG, JPG, JPEG.")

        # Sanitize filename
        safe_name = secure_filename(raw_name)
        if not safe_name or safe_name.startswith("."):
            safe_name = f"report{ext}"

        # Read stream and check size and emptiness
        file_stream.seek(0, os.SEEK_END)
        file_size = file_stream.tell()
        file_stream.seek(0)

        if file_size == 0:
            raise ValueError("Uploaded file is empty (0 bytes).")

        if file_size > MAX_FILE_SIZE:
            raise ValueError(f"File size ({file_size / (1024*1024):.2f} MB) exceeds maximum allowed limit of 10 MB.")

        # Verify magic bytes header
        expected_magic = MAGIC_BYTES.get(ext)
        if expected_magic:
            header = file_stream.read(len(expected_magic))
            file_stream.seek(0)
            if not header.startswith(expected_magic):
                raise ValueError(f"File content does not match the expected signature for '{ext}'.")

        content_type = ALLOWED_MIME_TYPES.get(ext, "application/octet-stream")
        return safe_name, content_type, file_size

    def generate_storage_key(self, patient_id: str, report_id: str, safe_filename: str) -> str:
        """
        Generate a deterministic, safe storage key without directory traversal risk.
        Format: reports/<patient_id>/<report_id>/<safe_filename>
        """
        clean_patient = re.sub(r"[^a-zA-Z0-9_\-]", "", patient_id)
        clean_report = re.sub(r"[^a-zA-Z0-9_\-]", "", report_id)
        clean_file = secure_filename(safe_filename).replace("..", "").strip(".")
        if not clean_file:
            clean_file = "document.bin"
        return f"reports/{clean_patient}/{clean_report}/{clean_file}"

    def save_file(self, patient_id: str, report_id: str, file_stream, filename: str) -> dict:
        """
        Save file to S3 or local directory.
        Returns: {storage_path, file_name, file_type, file_size}
        """
        safe_name, content_type, file_size = self.validate_file(file_stream, filename)
        storage_key = self.generate_storage_key(patient_id, report_id, safe_name)

        if self.is_s3_enabled:
            # Upload to Amazon S3 with Server-Side Encryption (AES256)
            s3 = self.get_s3_client()
            file_stream.seek(0)
            s3.put_object(
                Bucket=self.bucket_name,
                Key=storage_key,
                Body=file_stream.read(),
                ContentType=content_type,
                ServerSideEncryption="AES256"
            )
        else:
            # Save to local safe storage directory
            target_path = self.local_dir / storage_key
            # Defense-in-depth: resolve and ensure target stays inside local_dir
            resolved_target = target_path.resolve()
            resolved_root = self.local_dir.resolve()
            if not str(resolved_target).startswith(str(resolved_root)):
                raise ValueError("Path traversal violation detected in storage key.")

            resolved_target.parent.mkdir(parents=True, exist_ok=True)
            file_stream.seek(0)
            with open(resolved_target, "wb") as f:
                f.write(file_stream.read())

        return {
            "storage_path": storage_key,
            "file_name": safe_name,
            "file_type": content_type,
            "file_size": file_size
        }

    def get_access_url_or_path(self, storage_key: str, expires_in: int = 600) -> dict:
        """
        Generate short-lived pre-signed GET URL for S3, or resolve safe local filesystem path.
        Returns: {"type": "s3", "url": "..."} or {"type": "local", "path": "..."}
        """
        if self.is_s3_enabled:
            s3 = self.get_s3_client()
            presigned_url = s3.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self.bucket_name,
                    "Key": storage_key
                },
                ExpiresIn=expires_in
            )
            return {"type": "s3", "url": presigned_url}

        target_path = (self.local_dir / storage_key).resolve()
        resolved_root = self.local_dir.resolve()
        if not str(target_path).startswith(str(resolved_root)) or not target_path.is_file():
            raise FileNotFoundError("Medical report file not found in local storage.")

        return {"type": "local", "path": str(target_path)}

    def delete_file(self, storage_key: str) -> bool:
        """
        Delete file from S3 or local directory. Safe and idempotent.
        """
        if not storage_key:
            return False

        if self.is_s3_enabled:
            try:
                s3 = self.get_s3_client()
                s3.delete_object(Bucket=self.bucket_name, Key=storage_key)
                return True
            except Exception:
                return False

        target_path = (self.local_dir / storage_key).resolve()
        resolved_root = self.local_dir.resolve()
        if str(target_path).startswith(str(resolved_root)) and target_path.is_file():
            try:
                target_path.unlink()
                return True
            except Exception:
                return False
        return True


# Global default instance
storage_service = StorageService()
