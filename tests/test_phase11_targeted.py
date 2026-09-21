"""
Unit and integration tests for MedTrack Phase 11:
Targeted Test Enhancements

Protected behaviors:
  1. Multi-threaded SQLite Intake UPSERT concurrency (atomicity, 0 duplicate rows)
  2. Explicit SQLite foreign-key enforcement (sqlite3.IntegrityError on invalid keys, valid key succeeds)
  3. Direct StorageService unit tests (sanitization, path traversal, magic bytes, size limits, local/S3 tiers)
  4. Session inactivity exact-boundary verification (3599s valid, 3600s valid, 3601s expired)
"""

import os
import sys
import io
import tempfile
import shutil
import sqlite3
import threading
import datetime
from unittest.mock import patch, MagicMock
import unittest

sys.path.insert(0, os.path.abspath("."))
from config import Config
from services.database import DatabaseService
from services.storage_service import StorageService
from app import app, db as app_db, rate_limiter


# =============================================================================
# 1. MULTI-THREADED INTAKE UPSERT CONCURRENCY TEST
# =============================================================================

class TestIntakeUpsertConcurrency(unittest.TestCase):
    """Concurrency test verifying atomic UPSERT semantics on record_intake() across 5 concurrent threads."""

    def setUp(self):
        # Create an isolated temporary database for concurrency testing
        self.temp_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_fd)

        self.db = DatabaseService()
        self.db.db_path = self.temp_db_path
        self.db._init_sqlite()

        # Create one patient
        self.patient = self.db.create_user({
            "name": "Concurrency Test Patient",
            "email": "concurrency_patient@medtrack.local",
            "password_hash": "hash_concurrent",
            "phone": "+1-555-0199",
            "date_of_birth": "1985-05-15",
            "gender": "Female",
            "role": "patient",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        })
        self.patient_id = self.patient["user_id"]

        # Create one medicine
        self.med = self.db.create_medicine(
            patient_id=self.patient_id,
            name="Metformin 500mg",
            dosage="1 Tablet",
            schedule_time="08:00",
            schedule_times=["08:00"]
        )
        self.medicine_id = self.med["medicine_id"]
        self.scheduled_date = "2026-09-20"
        self.scheduled_time = "08:00"

    def tearDown(self):
        if os.path.exists(self.temp_db_path):
            try:
                os.remove(self.temp_db_path)
            except Exception:
                pass

    def test_01_concurrent_upsert_five_threads_same_dose_identity(self):
        """5 concurrent threads calling record_intake() for the SAME dose identity produce exactly 1 row with 0 duplicates."""
        worker_count = 5
        results = []
        exceptions = []
        threads = []

        def worker(thread_idx: int):
            try:
                status = "TAKEN" if thread_idx % 2 == 0 else "SKIPPED"
                res = self.db.record_intake(
                    patient_id=self.patient_id,
                    medicine_id=self.medicine_id,
                    status=status,
                    scheduled_date=self.scheduled_date,
                    scheduled_time=self.scheduled_time
                )
                results.append((thread_idx, res))
            except Exception as e:
                exceptions.append((thread_idx, e))

        for i in range(worker_count):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)

        # Launch all 5 worker threads concurrently
        for t in threads:
            t.start()

        # Wait for all workers to complete
        for t in threads:
            t.join(timeout=10.0)

        # Assertions
        self.assertEqual(len(exceptions), 0, f"Concurrent workers encountered unexpected exceptions: {exceptions}")
        self.assertEqual(len(results), worker_count, f"Expected {worker_count} worker results, got {len(results)}")

        # Query the isolated database directly to verify deduplication and row count
        with self.db._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT log_id, patient_id, medicine_id, scheduled_date, scheduled_time, status
                FROM intake_logs
                WHERE patient_id = ? AND medicine_id = ? AND scheduled_date = ? AND scheduled_time = ?;
            """, (self.patient_id, self.medicine_id, self.scheduled_date, self.scheduled_time))
            rows = cursor.fetchall()

            # Exactly ONE row must exist for the dose identity
            self.assertEqual(len(rows), 1, f"Expected exactly 1 intake row, found {len(rows)}")

            row = rows[0]
            self.assertEqual(row["patient_id"], self.patient_id)
            self.assertEqual(row["medicine_id"], self.medicine_id)
            self.assertEqual(row["scheduled_date"], self.scheduled_date)
            self.assertEqual(row["scheduled_time"], self.scheduled_time)
            self.assertIn(row["status"], ("TAKEN", "SKIPPED"))

            # Verify total intake_logs count across the whole table is exactly 1
            cursor.execute("SELECT COUNT(*) as cnt FROM intake_logs;")
            total_count = cursor.fetchone()["cnt"]
            self.assertEqual(total_count, 1, f"Expected total table count 1, found {total_count}")

    def test_02_concurrent_upsert_rapid_ten_threads_preserves_uniqueness(self):
        """10 concurrent threads rapidly updating same dose identity preserve atomic uniqueness constraint."""
        worker_count = 10
        results = []
        exceptions = []
        threads = []

        def worker(thread_idx: int):
            try:
                status = "TAKEN" if thread_idx % 2 == 0 else "SKIPPED"
                res = self.db.record_intake(
                    patient_id=self.patient_id,
                    medicine_id=self.medicine_id,
                    status=status,
                    scheduled_date=self.scheduled_date,
                    scheduled_time=self.scheduled_time
                )
                results.append((thread_idx, res))
            except Exception as e:
                exceptions.append((thread_idx, e))

        for i in range(worker_count):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)

        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=10.0)

        self.assertEqual(len(exceptions), 0, f"Rapid concurrent workers encountered exceptions: {exceptions}")
        self.assertEqual(len(results), worker_count)

        with self.db._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM intake_logs
                WHERE patient_id = ? AND medicine_id = ? AND scheduled_date = ? AND scheduled_time = ?;
            """, (self.patient_id, self.medicine_id, self.scheduled_date, self.scheduled_time))
            cnt = cursor.fetchone()["cnt"]
            self.assertEqual(cnt, 1, "Duplicate intake rows were created under rapid concurrency!")


# =============================================================================
# 2. EXPLICIT SQLITE FOREIGN KEY ENFORCEMENT TEST
# =============================================================================

class TestSQLiteForeignKeyEnforcement(unittest.TestCase):
    """Database test proving that SQLite PRAGMA foreign_keys = ON actively enforces referential integrity."""

    def setUp(self):
        self.temp_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_fd)

        self.db = DatabaseService()
        self.db.db_path = self.temp_db_path
        self.db._init_sqlite()

        # Seed 1 valid doctor and 1 valid patient
        self.doctor = self.db.create_user({
            "name": "Dr. Validus",
            "email": "dr.validus@medtrack.local",
            "password_hash": "hash_doc",
            "role": "doctor",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        })
        self.doctor_id = self.doctor["user_id"]

        self.patient = self.db.create_user({
            "name": "Valid Patient",
            "email": "valid_patient@medtrack.local",
            "password_hash": "hash_pat",
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

    def test_01_fk_enforcement_invalid_doctor_id_raises_integrity_error(self):
        """Attempting to insert an appointment with a non-existent doctor_id raises sqlite3.IntegrityError."""
        fake_doctor_id = "usr-nonexistent-doctor-999"

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.create_appointment({
                "patient_id": self.patient_id,
                "doctor_id": fake_doctor_id,
                "appointment_date": "2026-09-25",
                "appointment_time": "10:00 AM",
                "reason": "Cardiology consultation",
                "status": "PENDING"
            })

    def test_02_fk_enforcement_invalid_patient_id_raises_integrity_error(self):
        """Attempting to insert an appointment with a non-existent patient_id raises sqlite3.IntegrityError."""
        fake_patient_id = "usr-nonexistent-patient-888"

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.create_appointment({
                "patient_id": fake_patient_id,
                "doctor_id": self.doctor_id,
                "appointment_date": "2026-09-25",
                "appointment_time": "11:00 AM",
                "reason": "General checkup",
                "status": "PENDING"
            })

    def test_03_fk_enforcement_both_ids_invalid_raises_integrity_error(self):
        """Attempting to insert an appointment where neither patient nor doctor exists raises sqlite3.IntegrityError."""
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.create_appointment({
                "patient_id": "usr-fake-pat-777",
                "doctor_id": "usr-fake-doc-777",
                "appointment_date": "2026-09-25",
                "appointment_time": "02:00 PM",
                "reason": "Neurology consult",
                "status": "PENDING"
            })

    def test_04_fk_enforcement_valid_appointment_succeeds(self):
        """Inserting an appointment with valid doctor_id and patient_id succeeds without error."""
        appt = self.db.create_appointment({
            "patient_id": self.patient_id,
            "doctor_id": self.doctor_id,
            "appointment_date": "2026-09-25",
            "appointment_time": "03:00 PM",
            "reason": "Routine hypertension follow-up",
            "status": "CONFIRMED"
        })

        self.assertIsNotNone(appt)
        self.assertTrue(appt["appointment_id"].startswith("apt-"))
        self.assertEqual(appt["patient_id"], self.patient_id)
        self.assertEqual(appt["doctor_id"], self.doctor_id)

        # Verify persisted row in SQLite
        with self.db._get_sqlite_conn() as conn:
            row = conn.execute("SELECT * FROM appointments WHERE appointment_id = ?", (appt["appointment_id"],)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["reason"], "Routine hypertension follow-up")

    def test_05_fk_enforcement_reports_table_invalid_patient_raises_integrity_error(self):
        """Direct insert into reports table with a non-existent patient_id raises sqlite3.IntegrityError."""
        fake_patient_id = "usr-nonexistent-report-pat"

        with self.assertRaises(sqlite3.IntegrityError):
            with self.db._get_sqlite_conn() as conn:
                conn.execute("""
                    INSERT INTO reports (
                        report_id, patient_id, doctor_id, appointment_id,
                        title, file_name, file_type, file_size, storage_path, notes, uploaded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    "rep-fk-001", fake_patient_id, self.doctor_id, None,
                    "Blood Test", "blood.pdf", "application/pdf", 1024,
                    "reports/fake/blood.pdf", "Notes", "2026-09-20T00:00:00Z"
                ))
                conn.commit()


# =============================================================================
# 3. DIRECT STORAGE SERVICE UNIT TESTS
# =============================================================================

class TestStorageServiceDirect(unittest.TestCase):
    """Direct unit tests for StorageService helpers independently of Flask routes."""

    def setUp(self):
        self.temp_storage_dir = tempfile.mkdtemp()
        self.local_service = StorageService(bucket_name="", local_dir=self.temp_storage_dir)

    def tearDown(self):
        if os.path.exists(self.temp_storage_dir):
            shutil.rmtree(self.temp_storage_dir, ignore_errors=True)

    def test_01_storage_key_generation_deterministic_and_sanitized(self):
        """generate_storage_key creates deterministic keys and cleans forbidden characters."""
        # Standard input
        key = self.local_service.generate_storage_key("pat_001", "rep_002", "diagnostic_report.pdf")
        self.assertEqual(key, "reports/pat_001/rep_002/diagnostic_report.pdf")

        # Special characters in patient_id and report_id are stripped
        dirty_key = self.local_service.generate_storage_key("pat@!#$001", "rep*&^%002", "safe_scan.png")
        self.assertEqual(dirty_key, "reports/pat001/rep002/safe_scan.png")

        # Directory traversal in safe_filename is sanitized
        traversal_key = self.local_service.generate_storage_key("pat_001", "rep_002", "../../../evil.pdf")
        self.assertEqual(traversal_key, "reports/pat_001/rep_002/evil.pdf")

        # Empty sanitized filename fallback to document.bin
        empty_key = self.local_service.generate_storage_key("pat_001", "rep_002", "...///...")
        self.assertEqual(empty_key, "reports/pat_001/rep_002/document.bin")

    def test_02_filename_sanitization_and_path_traversal_defense(self):
        """validate_file strips directory traversal sequences from incoming filenames."""
        stream = io.BytesIO(b"%PDF-1.4 valid content")
        safe_name, content_type, size = self.local_service.validate_file(stream, "../../path/traversal/test.pdf")
        self.assertEqual(safe_name, "test.pdf")
        self.assertEqual(content_type, "application/pdf")
        self.assertEqual(size, 22)

    def test_03_pdf_magic_byte_validation(self):
        """PDF magic bytes (%PDF-) validate genuine PDFs and reject spoofed extensions."""
        # Valid PDF header
        valid_stream = io.BytesIO(b"%PDF-1.5 %binary payload here")
        safe_name, c_type, size = self.local_service.validate_file(valid_stream, "valid.pdf")
        self.assertEqual(safe_name, "valid.pdf")
        self.assertEqual(c_type, "application/pdf")

        # Spoofed text disguised as PDF
        invalid_stream = io.BytesIO(b"Hello world, I am just a plain text file pretending to be PDF.")
        with self.assertRaises(ValueError) as ctx:
            self.local_service.validate_file(invalid_stream, "spoofed.pdf")
        self.assertIn("File content does not match the expected signature for '.pdf'", str(ctx.exception))

    def test_04_png_magic_byte_validation(self):
        """PNG magic bytes (\\x89PNG\\r\\n\\x1a\\n) validate genuine PNGs and reject spoofed files."""
        # Valid PNG header
        valid_stream = io.BytesIO(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR...")
        safe_name, c_type, size = self.local_service.validate_file(valid_stream, "xray.png")
        self.assertEqual(safe_name, "xray.png")
        self.assertEqual(c_type, "image/png")

        # Spoofed PNG
        invalid_stream = io.BytesIO(b"Not a real PNG header at all")
        with self.assertRaises(ValueError) as ctx:
            self.local_service.validate_file(invalid_stream, "xray.png")
        self.assertIn("File content does not match the expected signature for '.png'", str(ctx.exception))

    def test_05_jpeg_magic_byte_validation(self):
        """JPEG magic bytes (\\xff\\xd8\\xff) validate .jpg and .jpeg files and reject spoofed files."""
        # Valid JPEG header
        valid_stream = io.BytesIO(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00...")
        safe_name, c_type, size = self.local_service.validate_file(valid_stream, "mri_scan.jpg")
        self.assertEqual(safe_name, "mri_scan.jpg")
        self.assertEqual(c_type, "image/jpeg")

        # Valid JPEG with .jpeg extension
        valid_stream.seek(0)
        safe_name2, c_type2, _ = self.local_service.validate_file(valid_stream, "mri_scan.jpeg")
        self.assertEqual(safe_name2, "mri_scan.jpeg")
        self.assertEqual(c_type2, "image/jpeg")

        # Spoofed JPEG
        invalid_stream = io.BytesIO(b"Not a JPEG image")
        with self.assertRaises(ValueError) as ctx:
            self.local_service.validate_file(invalid_stream, "mri_scan.jpg")
        self.assertIn("File content does not match the expected signature for '.jpg'", str(ctx.exception))

    def test_06_unsupported_file_format_rejection(self):
        """StorageService rejects unsupported extensions (e.g. .txt, .exe, .sh, .docx)."""
        stream = io.BytesIO(b"echo 'malicious'")
        for bad_name in ("malware.exe", "notes.txt", "doc.docx", "script.sh", "page.html"):
            with self.assertRaises(ValueError) as ctx:
                self.local_service.validate_file(stream, bad_name)
            self.assertIn("Unsupported file format", str(ctx.exception))

    def test_07_empty_file_and_empty_filename_rejection(self):
        """StorageService rejects 0-byte files and empty filenames."""
        # Empty filename
        stream = io.BytesIO(b"%PDF-1.4 content")
        with self.assertRaises(ValueError) as ctx:
            self.local_service.validate_file(stream, "")
        self.assertIn("File name cannot be empty", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            self.local_service.validate_file(stream, "   ")
        self.assertIn("File name cannot be empty", str(ctx.exception))

        # 0-byte file stream
        empty_stream = io.BytesIO(b"")
        with self.assertRaises(ValueError) as ctx:
            self.local_service.validate_file(empty_stream, "empty.pdf")
        self.assertIn("Uploaded file is empty (0 bytes)", str(ctx.exception))

    def test_08_maximum_file_size_enforcement(self):
        """StorageService enforces the 10 MB maximum file size limit."""
        # File stream exceeding 10 MB
        oversized_bytes = b"%PDF-" + b"A" * (10 * 1024 * 1024 + 10)
        oversized_stream = io.BytesIO(oversized_bytes)

        with self.assertRaises(ValueError) as ctx:
            self.local_service.validate_file(oversized_stream, "huge.pdf")
        self.assertIn("exceeds maximum allowed limit of 10 MB", str(ctx.exception))

    def test_09_local_storage_write_and_read_behavior(self):
        """save_file writes files to local isolated directory, and get_access_url_or_path returns valid path."""
        payload = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>startxref\n0\n%%EOF"
        stream = io.BytesIO(payload)

        save_result = self.local_service.save_file(
            patient_id="pat_alpha",
            report_id="rep_beta",
            file_stream=stream,
            filename="blood_work.pdf"
        )

        self.assertEqual(save_result["file_name"], "blood_work.pdf")
        self.assertEqual(save_result["file_type"], "application/pdf")
        self.assertEqual(save_result["file_size"], len(payload))
        storage_path = save_result["storage_path"]
        self.assertEqual(storage_path, "reports/pat_alpha/rep_beta/blood_work.pdf")

        # Resolve access path
        access = self.local_service.get_access_url_or_path(storage_path)
        self.assertEqual(access["type"], "local")
        local_file_path = access["path"]
        self.assertTrue(os.path.exists(local_file_path))

        # Verify content on disk matches exactly
        with open(local_file_path, "rb") as f:
            disk_content = f.read()
        self.assertEqual(disk_content, payload)

    def test_10_local_storage_delete_behavior(self):
        """delete_file unlinks file from local storage and operates idempotently."""
        payload = b"%PDF-1.4 test deletion content"
        stream = io.BytesIO(payload)

        save_result = self.local_service.save_file(
            patient_id="pat_del",
            report_id="rep_del",
            file_stream=stream,
            filename="delete_me.pdf"
        )
        storage_path = save_result["storage_path"]

        # File exists
        access = self.local_service.get_access_url_or_path(storage_path)
        self.assertTrue(os.path.exists(access["path"]))

        # Delete file
        del_result = self.local_service.delete_file(storage_path)
        self.assertTrue(del_result)
        self.assertFalse(os.path.exists(access["path"]))

        # Subsequent get_access_url_or_path raises FileNotFoundError
        with self.assertRaises(FileNotFoundError):
            self.local_service.get_access_url_or_path(storage_path)

        # Idempotent: deleting an already deleted file returns True without error
        idempotent_result = self.local_service.delete_file(storage_path)
        self.assertTrue(idempotent_result)

        # Deleting empty/None key returns False
        self.assertFalse(self.local_service.delete_file(""))
        self.assertFalse(self.local_service.delete_file(None))

    def test_11_s3_mode_save_and_delete_with_mocked_client(self):
        """StorageService in S3 mode writes via put_object and deletes via delete_object using mocked S3 client."""
        s3_service = StorageService(bucket_name="mock-medtrack-reports", local_dir=self.temp_storage_dir)
        self.assertTrue(s3_service.is_s3_enabled)

        mock_s3 = MagicMock()
        s3_service._s3_client = mock_s3

        payload = b"%PDF-1.4 s3 upload payload"
        stream = io.BytesIO(payload)

        save_result = s3_service.save_file(
            patient_id="pat_s3",
            report_id="rep_s3",
            file_stream=stream,
            filename="s3_report.pdf"
        )

        expected_key = "reports/pat_s3/rep_s3/s3_report.pdf"
        self.assertEqual(save_result["storage_path"], expected_key)

        # Verify S3 put_object called with SSE-AES256 and expected parameters
        mock_s3.put_object.assert_called_once_with(
            Bucket="mock-medtrack-reports",
            Key=expected_key,
            Body=payload,
            ContentType="application/pdf",
            ServerSideEncryption="AES256"
        )

        # Verify S3 delete_file calls delete_object
        del_result = s3_service.delete_file(expected_key)
        self.assertTrue(del_result)
        mock_s3.delete_object.assert_called_once_with(
            Bucket="mock-medtrack-reports",
            Key=expected_key
        )

    def test_12_s3_presigned_url_generation(self):
        """StorageService in S3 mode generates short-lived pre-signed download URLs via mocked S3 client."""
        s3_service = StorageService(bucket_name="mock-medtrack-reports", local_dir=self.temp_storage_dir)
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://mock-medtrack-reports.s3.amazonaws.com/presigned-url"
        s3_service._s3_client = mock_s3

        storage_key = "reports/pat_s3/rep_s3/s3_report.pdf"
        access = s3_service.get_access_url_or_path(storage_key, expires_in=300)

        self.assertEqual(access["type"], "s3")
        self.assertEqual(access["url"], "https://mock-medtrack-reports.s3.amazonaws.com/presigned-url")

        mock_s3.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "mock-medtrack-reports", "Key": storage_key},
            ExpiresIn=300
        )


# =============================================================================
# 4. SESSION INACTIVITY EXACT BOUNDARY TEST
# =============================================================================

class TestSessionInactivityBoundary(unittest.TestCase):
    """Exact-boundary test verifying the 60-minute (3600-second) session inactivity timeout.

    Application comparison logic in app.py:
      now - last_activity > Config.SESSION_INACTIVITY_TIMEOUT (3600s)

    Boundary semantics:
      - 3599 seconds elapsed: 3599 > 3600 is False -> Session remains VALID
      - 3600 seconds elapsed: 3600 > 3600 is False -> Session remains VALID
      - 3601 seconds elapsed: 3601 > 3600 is True  -> Session EXPIRES (cleared & redirected to /login)
    """

    def setUp(self):
        # Create an isolated temporary database for the Flask test client to avoid touching medtrack_local.db
        self.temp_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_fd)

        self.orig_db_path = app_db.db_path
        app_db.db_path = self.temp_db_path
        app_db._init_sqlite()

        # Create a test patient user in the temporary database
        self.test_patient = app_db.create_user({
            "name": "Boundary Patient",
            "email": "boundary_patient@medtrack.local",
            "password_hash": "hash_boundary",
            "phone": "+1-555-0155",
            "date_of_birth": "1992-08-20",
            "gender": "Female",
            "role": "patient",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        })
        self.patient_id = self.test_patient["user_id"]

        rate_limiter.reset_all()
        self.client = app.test_client()

        # Fixed deterministic UTC reference time to eliminate clock drift
        self.fixed_now = datetime.datetime(2026, 9, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)
        self.fixed_timestamp = int(self.fixed_now.timestamp())

        # Subclass datetime.datetime to freeze now() in app.py without altering other methods
        fixed_dt = self.fixed_now
        class MockDatetime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                if tz:
                    return fixed_dt.astimezone(tz)
                return fixed_dt

        self.mock_datetime_cls = MockDatetime

    def tearDown(self):
        app_db.db_path = self.orig_db_path
        rate_limiter.reset_all()
        if os.path.exists(self.temp_db_path):
            try:
                os.remove(self.temp_db_path)
            except Exception:
                pass

    def test_01_boundary_3599_seconds_session_remains_valid(self):
        """At 3599s since last activity (delta < 3600), the session remains valid and last_activity is refreshed."""
        with patch("app.datetime.datetime", self.mock_datetime_cls):
            with self.client.session_transaction() as sess:
                sess["user_id"] = self.patient_id
                sess["role"] = "patient"
                sess["name"] = self.test_patient["name"]
                sess["last_activity"] = self.fixed_timestamp - 3599

            response = self.client.get("/dashboard")

            # Session remains valid: HTTP 200 OK (access granted to protected dashboard)
            self.assertEqual(response.status_code, 200)

            # User session is preserved and last_activity timestamp is refreshed to current time
            with self.client.session_transaction() as sess:
                self.assertEqual(sess.get("user_id"), self.patient_id)
                self.assertEqual(sess.get("role"), "patient")
                self.assertEqual(sess.get("last_activity"), self.fixed_timestamp)

    def test_02_boundary_3600_seconds_session_remains_valid(self):
        """At exactly 3600s since last activity (delta == 3600), delta > 3600 evaluates False; session remains valid."""
        with patch("app.datetime.datetime", self.mock_datetime_cls):
            with self.client.session_transaction() as sess:
                sess["user_id"] = self.patient_id
                sess["role"] = "patient"
                sess["name"] = self.test_patient["name"]
                sess["last_activity"] = self.fixed_timestamp - 3600

            response = self.client.get("/dashboard")

            # At exact 3600-second boundary, strict inequality (delta > 3600) means session is STILL valid
            self.assertEqual(response.status_code, 200)

            with self.client.session_transaction() as sess:
                self.assertEqual(sess.get("user_id"), self.patient_id)
                self.assertEqual(sess.get("role"), "patient")
                self.assertEqual(sess.get("last_activity"), self.fixed_timestamp)

    def test_03_boundary_3601_seconds_session_expires(self):
        """At 3601s since last activity (delta > 3600), the session expires, is cleared, and redirects to /login."""
        with patch("app.datetime.datetime", self.mock_datetime_cls):
            with self.client.session_transaction() as sess:
                sess["user_id"] = self.patient_id
                sess["role"] = "patient"
                sess["name"] = self.test_patient["name"]
                sess["last_activity"] = self.fixed_timestamp - 3601

            response = self.client.get("/dashboard")

            # Session expired: HTTP 302 redirect to /login
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers.get("Location", "").endswith("/login"))

            # Session has been completely cleared
            with self.client.session_transaction() as sess:
                self.assertIsNone(sess.get("user_id"))
                self.assertIsNone(sess.get("role"))

            # Follow redirect to verify flash message is rendered
            follow_resp = self.client.get("/dashboard", follow_redirects=True)
            self.assertIn(b"session has expired due to 60 minutes of inactivity", follow_resp.data)

    def test_04_session_timeout_protects_against_off_by_one_regressions(self):
        """Explicit contract test verifying exact strict inequality semantics: (now - last_activity > 3600)."""
        timeout = Config.SESSION_INACTIVITY_TIMEOUT
        self.assertEqual(timeout, 3600)

        # Contract definition:
        # delta = 3599 -> expired: False
        # delta = 3600 -> expired: False
        # delta = 3601 -> expired: True
        self.assertFalse(3599 > timeout, "3599 must not trigger timeout")
        self.assertFalse(3600 > timeout, "3600 must not trigger timeout under strict > comparison")
        self.assertTrue(3601 > timeout, "3601 must trigger timeout")


if __name__ == "__main__":
    unittest.main()
