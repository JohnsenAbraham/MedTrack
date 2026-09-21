"""
Phase 8 Medical Reports & Private S3 Storage Test Suite
Comprehensive testing for:
 1. Secure medical report file validation (PDF, PNG, JPEG, size limits, magic bytes)
 2. Path traversal defense in uploads and storage key generation
 3. Strict patient authorization & IDOR shielding (cross-patient access returns 404)
 4. Physician relationship verification for patient report access
 5. Anonymous access denial
 6. Atomic compensation semantics (storage cleanup on DB error, no orphan records on storage failure)
 7. Private S3 integration (SSE-AES256, pre-signed URL generation with 600s TTL)
 8. Local filesystem storage isolation without static AWS credentials
 9. CloudFormation IaC private bucket and least-privilege policy verification
 10. CSRF protection on report mutation endpoints
"""

import os
import io
import re
import datetime
import sqlite3
from pathlib import Path
import unittest
from unittest import mock
import tempfile
import shutil

from app import app, db
from config import Config
from services import rate_limiter, storage_service, StorageService


class Phase8ReportsTestCase(unittest.TestCase):
    """Dedicated verification test suite for Phase 8 medical reports & storage architecture."""

    @classmethod
    def setUpClass(cls):
        """Create an isolated temporary copy of the canonical database and uploads folder."""
        cls.temp_fd, cls.temp_db_path = tempfile.mkstemp(suffix="_test_phase8.db")
        os.close(cls.temp_fd)
        shutil.copy2(Config.LOCAL_DB_PATH, cls.temp_db_path)
        cls.orig_db_path = db.db_path
        db.db_path = cls.temp_db_path

        # Create isolated temporary local storage directory for reports
        cls.temp_storage_dir = tempfile.mkdtemp(suffix="_reports_storage")
        cls.orig_storage_dir = storage_service.local_dir
        storage_service.local_dir = cls.temp_storage_dir

    @classmethod
    def tearDownClass(cls):
        """Restore canonical paths and clean up temporary test artifacts."""
        db.db_path = cls.orig_db_path
        storage_service.local_dir = cls.orig_storage_dir

        if os.path.exists(cls.temp_db_path):
            try:
                os.remove(cls.temp_db_path)
            except Exception:
                pass

        if os.path.exists(cls.temp_storage_dir):
            try:
                shutil.rmtree(cls.temp_storage_dir)
            except Exception:
                pass

    def setUp(self):
        """Configure test client with strict security settings for each test."""
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = True
        app.config["SECRET_KEY"] = "phase8-test-key-328f899b-e88a-499b-b8c3"
        app.config["DEMO_MODE"] = False
        self.client = app.test_client()
        rate_limiter.reset_all()

    def tearDown(self):
        rate_limiter.reset_all()

    def get_csrf_token(self, client=None, path="/login"):
        """Extract a valid CSRF token from the meta tag or form of a GET request."""
        c = client or self.client
        resp = c.get(path, follow_redirects=True)
        html = resp.get_data(as_text=True)

        m = re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', html)
        if m:
            return m.group(1)

        m = re.search(r'<input[^>]+name=["\']csrf_token["\'][^>]+value=["\']([^"\']+)["\']', html)
        if m:
            return m.group(1)

        with app.test_request_context():
            from flask_wtf.csrf import generate_csrf
            return generate_csrf()

    def login_as_patient_alex(self):
        token = self.get_csrf_token()
        return self.client.post("/login", data={
            "email": "patient.demo@medtrack.local",
            "password": "PatientPass123!",
            "csrf_token": token
        }, follow_redirects=True)

    def login_as_doctor_vance(self):
        token = self.get_csrf_token()
        return self.client.post("/login", data={
            "email": "doctor.vance@medtrack.local",
            "password": "DoctorPass123!",
            "csrf_token": token
        }, follow_redirects=True)

    def login_as_doctor_chen(self):
        token = self.get_csrf_token()
        return self.client.post("/login", data={
            "email": "doctor.chen@medtrack.local",
            "password": "DoctorPass123!",
            "csrf_token": token
        }, follow_redirects=True)

    def get_or_create_second_patient(self):
        """Retrieve or create a distinct secondary patient."""
        u = db.get_user_by_email("target.patient@medtrack.local")
        if not u:
            u = db.create_user({
                "email": "target.patient@medtrack.local",
                "password_hash": "dummy",
                "name": "Target Patient",
                "role": "patient"
            })
        return u

    def get_or_create_unrelated_patient(self):
        """Retrieve or create a distinct patient with zero appointments with any doctor."""
        u = db.get_user_by_email("unrelated.patient@medtrack.local")
        if not u:
            u = db.create_user({
                "email": "unrelated.patient@medtrack.local",
                "password_hash": "dummy",
                "name": "Unrelated Patient",
                "role": "patient"
            })
        return u

    def get_or_create_appointment(self, doctor_id, patient_id, status="CONFIRMED"):
        """Ensure an appointment exists between doctor and patient."""
        today = datetime.date.today().isoformat()
        return db.create_appointment({
            "doctor_id": doctor_id,
            "patient_id": patient_id,
            "appointment_date": today,
            "appointment_time": "10:00 AM",
            "reason": "Routine Consultation",
            "status": status
        })

    # =========================================================================
    # Group 1: File Validation Tests (Tests 1 - 6)
    # =========================================================================

    def test_01_patient_can_upload_valid_pdf(self):
        """1. Patient can upload a valid PDF file with proper magic bytes."""
        self.login_as_patient_alex()
        token = self.get_csrf_token(path="/reports/upload")

        pdf_content = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
        data = {
            "title": "Complete Blood Count Panel",
            "notes": "Annual baseline laboratory test",
            "report_file": (io.BytesIO(pdf_content), "cbc_panel.pdf"),
            "csrf_token": token
        }

        res = self.client.post("/reports/upload", data=data, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Complete Blood Count Panel", res.data)

        # Verify persisted metadata
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        reports = db.get_reports_by_patient(pat["user_id"])
        matching = [r for r in reports if r["title"] == "Complete Blood Count Panel"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["file_type"], "application/pdf")
        self.assertEqual(matching[0]["file_size"], len(pdf_content))

    def test_02_patient_can_upload_valid_png_and_jpeg(self):
        """2. Patient can upload valid PNG and JPEG diagnostic images."""
        self.login_as_patient_alex()

        # Test PNG
        token = self.get_csrf_token(path="/reports/upload")
        png_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
        res_png = self.client.post("/reports/upload", data={
            "title": "Chest X-Ray PNG",
            "report_file": (io.BytesIO(png_content), "chest_xray.png"),
            "csrf_token": token
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(res_png.status_code, 200)
        self.assertIn(b"Chest X-Ray PNG", res_png.data)

        # Test JPEG
        token = self.get_csrf_token(path="/reports/upload")
        jpeg_content = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00"
        res_jpeg = self.client.post("/reports/upload", data={
            "title": "Dermatology Scan JPEG",
            "report_file": (io.BytesIO(jpeg_content), "derma_scan.jpg"),
            "csrf_token": token
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(res_jpeg.status_code, 200)
        self.assertIn(b"Dermatology Scan JPEG", res_jpeg.data)

    def test_03_unsupported_file_extension_rejected(self):
        """3. Uploading dangerous or unsupported extensions (.exe, .sh, .txt) is rejected."""
        self.login_as_patient_alex()
        token = self.get_csrf_token(path="/reports/upload")

        for bad_ext in [".exe", ".sh", ".txt", ".html", ".js"]:
            data = {
                "title": f"Malicious {bad_ext} file",
                "report_file": (io.BytesIO(b"echo harmful"), f"exploit{bad_ext}"),
                "csrf_token": token
            }
            res = self.client.post("/reports/upload", data=data, content_type="multipart/form-data", follow_redirects=True)
            self.assertIn(b"Unsupported file format", res.data)

    def test_04_oversized_file_rejected(self):
        """4. Reports exceeding 10 MB are rejected."""
        self.login_as_patient_alex()
        token = self.get_csrf_token(path="/reports/upload")

        # Fake PDF header with >10MB stream
        header = b"%PDF-1.4\n"
        oversized_stream = io.BytesIO(header + b"0" * (10 * 1024 * 1024 + 100))

        data = {
            "title": "Oversized MRI Scan",
            "report_file": (oversized_stream, "huge_mri.pdf"),
            "csrf_token": token
        }
        res = self.client.post("/reports/upload", data=data, content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(b"exceeds maximum allowed limit", res.data)

    def test_05_empty_file_rejected(self):
        """5. Uploading an empty 0-byte file is rejected."""
        self.login_as_patient_alex()
        token = self.get_csrf_token(path="/reports/upload")

        data = {
            "title": "Zero Byte Report",
            "report_file": (io.BytesIO(b""), "empty.pdf"),
            "csrf_token": token
        }
        res = self.client.post("/reports/upload", data=data, content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(b"Uploaded file is empty", res.data)

    def test_06_path_traversal_filename_normalized(self):
        """6. Path traversal attempts in filenames are safely normalized to basename."""
        svc = StorageService(local_dir=self.temp_storage_dir)
        traversal_names = [
            "../../../etc/passwd.pdf",
            "..\\..\\windows\\system32\\cmd.exe.pdf",
            "/absolute/root/path.pdf"
        ]

        for bad_name in traversal_names:
            pdf_bytes = io.BytesIO(b"%PDF-1.4 header")
            safe_name, _, _ = svc.validate_file(pdf_bytes, bad_name)
            self.assertNotIn("/", safe_name)
            self.assertNotIn("\\", safe_name)
            self.assertNotIn("..", safe_name)

    # =========================================================================
    # Group 2: Authorization & IDOR Shielding (Tests 7 - 13)
    # =========================================================================

    def test_07_patient_can_view_own_report(self):
        """7. Patient can view/download their own uploaded medical report."""
        self.login_as_patient_alex()
        pat = db.get_user_by_email("patient.demo@medtrack.local")

        # Create report in test database and local storage
        rep_id = "rep-test07view"
        pdf_bytes = io.BytesIO(b"%PDF-1.4\nTest Report 07")
        storage_info = storage_service.save_file(pat["user_id"], rep_id, pdf_bytes, "test07.pdf")

        db.create_report({
            "report_id": rep_id,
            "patient_id": pat["user_id"],
            "title": "Patient 07 Viewable Report",
            "file_name": storage_info["file_name"],
            "file_type": storage_info["file_type"],
            "file_size": storage_info["file_size"],
            "storage_path": storage_info["storage_path"]
        })

        res = self.client.get(f"/reports/{rep_id}/download")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"%PDF-1.4", res.data)

    def test_08_patient_cannot_view_another_patient_report(self):
        """8. Patient B attempting to access Patient A's report receives 404 (IDOR shield)."""
        pat_a = db.get_user_by_email("patient.demo@medtrack.local")
        pat_b = self.get_or_create_second_patient()

        rep_id = "rep-confidential-a"
        pdf_bytes = io.BytesIO(b"%PDF-1.4\nConfidential Patient A File")
        storage_info = storage_service.save_file(pat_a["user_id"], rep_id, pdf_bytes, "private_a.pdf")

        db.create_report({
            "report_id": rep_id,
            "patient_id": pat_a["user_id"],
            "title": "Confidential Patient A File",
            "file_name": storage_info["file_name"],
            "file_type": storage_info["file_type"],
            "file_size": storage_info["file_size"],
            "storage_path": storage_info["storage_path"]
        })

        # Set session as Patient B
        with self.client.session_transaction() as sess:
            sess["user_id"] = pat_b["user_id"]
            sess["role"] = "patient"
            sess["_auth_token"] = "auth-pat-b"
            sess["last_activity"] = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

        res = self.client.get(f"/reports/{rep_id}/download")
        self.assertEqual(res.status_code, 404)

    def test_09_patient_cannot_download_another_patient_report(self):
        """9. Direct download attempts by cross-patient ID manipulation fail with 404."""
        pat_a = db.get_user_by_email("patient.demo@medtrack.local")
        pat_b = self.get_or_create_second_patient()

        rep_id = "rep-download-shield"
        pdf_bytes = io.BytesIO(b"%PDF-1.4\nPatient A Biopsy")
        storage_info = storage_service.save_file(pat_a["user_id"], rep_id, pdf_bytes, "biopsy.pdf")

        db.create_report({
            "report_id": rep_id,
            "patient_id": pat_a["user_id"],
            "title": "Patient A Biopsy",
            "file_name": storage_info["file_name"],
            "file_type": storage_info["file_type"],
            "file_size": storage_info["file_size"],
            "storage_path": storage_info["storage_path"]
        })

        # Login as Patient B
        with self.client.session_transaction() as sess:
            sess["user_id"] = pat_b["user_id"]
            sess["role"] = "patient"
            sess["_auth_token"] = "auth-pat-b-2"
            sess["last_activity"] = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

        res = self.client.get(f"/reports/{rep_id}/download")
        self.assertEqual(res.status_code, 404)

    def test_10_doctor_with_authorized_relationship_can_view_report(self):
        """10. Doctor Vance can access reports for patients with established appointment history."""
        doc_vance = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        self.get_or_create_appointment(doc_vance["user_id"], pat["user_id"], "CONFIRMED")

        rep_id = "rep-doc-auth-visit"
        pdf_bytes = io.BytesIO(b"%PDF-1.4\nVance Authorized Report")
        storage_info = storage_service.save_file(pat["user_id"], rep_id, pdf_bytes, "vance_report.pdf")

        db.create_report({
            "report_id": rep_id,
            "patient_id": pat["user_id"],
            "doctor_id": doc_vance["user_id"],
            "title": "Vance Authorized Report",
            "file_name": storage_info["file_name"],
            "file_type": storage_info["file_type"],
            "file_size": storage_info["file_size"],
            "storage_path": storage_info["storage_path"]
        })

        self.login_as_doctor_vance()
        res = self.client.get(f"/reports/{rep_id}/download")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"%PDF-1.4", res.data)

    def test_11_doctor_without_relationship_cannot_view_report(self):
        """11. Doctor Chen cannot view reports for patients without an established appointment."""
        doc_chen = db.get_user_by_email("doctor.chen@medtrack.local")
        unrelated_patient = self.get_or_create_second_patient()

        rep_id = "rep-unrelated-patient"
        pdf_bytes = io.BytesIO(b"%PDF-1.4\nUnrelated Patient File")
        storage_info = storage_service.save_file(unrelated_patient["user_id"], rep_id, pdf_bytes, "unrelated.pdf")

        db.create_report({
            "report_id": rep_id,
            "patient_id": unrelated_patient["user_id"],
            "title": "Unrelated Patient File",
            "file_name": storage_info["file_name"],
            "file_type": storage_info["file_type"],
            "file_size": storage_info["file_size"],
            "storage_path": storage_info["storage_path"]
        })

        self.login_as_doctor_chen()
        res = self.client.get(f"/reports/{rep_id}/download")
        self.assertEqual(res.status_code, 404)

    def test_12_anonymous_user_cannot_access_report(self):
        """12. Unauthenticated requests to report endpoints redirect to login."""
        res_list = self.client.get("/reports")
        self.assertEqual(res_list.status_code, 302)
        self.assertIn("/login", res_list.headers["Location"])

        res_upload = self.client.get("/reports/upload")
        self.assertEqual(res_upload.status_code, 302)

        res_down = self.client.get("/reports/rep-anonymous-test/download")
        self.assertEqual(res_down.status_code, 302)

    def test_13_cross_patient_report_id_manipulation_fails(self):
        """13. Attempting to access non-existent or foreign report IDs fails with 404."""
        self.login_as_patient_alex()
        res = self.client.get("/reports/rep-nonexistent-9999/download")
        self.assertEqual(res.status_code, 404)

    # =========================================================================
    # Group 3: Appointment Validation & CSRF (Tests 14 - 17)
    # =========================================================================

    def test_14_appointment_patient_mismatch_rejected(self):
        """14. Uploading report with appointment belonging to different patient is rejected."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat_a = db.get_user_by_email("patient.demo@medtrack.local")
        pat_b = self.get_or_create_second_patient()

        appt_b = self.get_or_create_appointment(doc["user_id"], pat_b["user_id"], "CONFIRMED")

        with self.assertRaises(ValueError) as ctx:
            db.create_report({
                "patient_id": pat_a["user_id"],
                "appointment_id": appt_b["appointment_id"],
                "title": "Mismatch Test",
                "file_name": "test.pdf",
                "storage_path": "reports/test/test.pdf"
            })
        self.assertIn("patient mismatch", str(ctx.exception).lower())

    def test_15_appointment_doctor_mismatch_rejected(self):
        """15. Uploading report with doctor not assigned to appointment is rejected."""
        doc_vance = db.get_user_by_email("doctor.vance@medtrack.local")
        doc_chen = db.get_user_by_email("doctor.chen@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")

        appt_vance = self.get_or_create_appointment(doc_vance["user_id"], pat["user_id"], "CONFIRMED")

        with self.assertRaises(ValueError) as ctx:
            db.create_report({
                "patient_id": pat["user_id"],
                "doctor_id": doc_chen["user_id"],
                "appointment_id": appt_vance["appointment_id"],
                "title": "Doctor Mismatch",
                "file_name": "test.pdf",
                "storage_path": "reports/test/test.pdf"
            })
        self.assertIn("doctor mismatch", str(ctx.exception).lower())

    def test_16_invalid_appointment_rejected(self):
        """16. Uploading report with non-existent appointment is rejected."""
        pat = db.get_user_by_email("patient.demo@medtrack.local")

        with self.assertRaises(ValueError) as ctx:
            db.create_report({
                "patient_id": pat["user_id"],
                "appointment_id": "appt-nonexistent-12345",
                "title": "Fake Appt Report",
                "file_name": "test.pdf",
                "storage_path": "reports/test/test.pdf"
            })
        self.assertIn("not found", str(ctx.exception).lower())

    def test_17_missing_csrf_rejected_on_upload(self):
        """17. POST /reports/upload without CSRF token is rejected with HTTP 400."""
        self.login_as_patient_alex()
        pdf_bytes = io.BytesIO(b"%PDF-1.4\nCSRF test")

        res = self.client.post("/reports/upload", data={
            "title": "No CSRF Report",
            "report_file": (pdf_bytes, "nocsrf.pdf")
        }, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 400)
        self.assertIn(b"Security Validation Failed", res.data)

    # =========================================================================
    # Group 4: Deletion & Compensation Ordering (Tests 18 - 22)
    # =========================================================================

    def test_18_unauthorized_delete_rejected(self):
        """18. Patient B attempting to delete Patient A's report is rejected with 404."""
        pat_a = db.get_user_by_email("patient.demo@medtrack.local")
        pat_b = self.get_or_create_second_patient()

        rep_id = "rep-delete-shield"
        pdf_bytes = io.BytesIO(b"%PDF-1.4\nDelete Shield Test")
        storage_info = storage_service.save_file(pat_a["user_id"], rep_id, pdf_bytes, "shield.pdf")

        db.create_report({
            "report_id": rep_id,
            "patient_id": pat_a["user_id"],
            "title": "Protected File",
            "file_name": storage_info["file_name"],
            "file_type": storage_info["file_type"],
            "file_size": storage_info["file_size"],
            "storage_path": storage_info["storage_path"]
        })

        # Login as Patient B
        with self.client.session_transaction() as sess:
            sess["user_id"] = pat_b["user_id"]
            sess["role"] = "patient"
            sess["_auth_token"] = "auth-pat-b-del"
            sess["last_activity"] = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

        token = self.get_csrf_token(path="/dashboard")
        res = self.client.post(f"/reports/{rep_id}/delete", data={"csrf_token": token})
        self.assertEqual(res.status_code, 404)

        # File and DB record must still exist
        self.assertIsNotNone(db.get_report_by_id(rep_id))

    def test_19_authorized_delete_works(self):
        """19. Report owner can delete their report; both storage object and DB record removed."""
        self.login_as_patient_alex()
        pat = db.get_user_by_email("patient.demo@medtrack.local")

        rep_id = "rep-delete-ok"
        pdf_bytes = io.BytesIO(b"%PDF-1.4\nDeletable File")
        storage_info = storage_service.save_file(pat["user_id"], rep_id, pdf_bytes, "deletable.pdf")

        db.create_report({
            "report_id": rep_id,
            "patient_id": pat["user_id"],
            "title": "Deletable File",
            "file_name": storage_info["file_name"],
            "file_type": storage_info["file_type"],
            "file_size": storage_info["file_size"],
            "storage_path": storage_info["storage_path"]
        })

        token = self.get_csrf_token(path="/reports")
        res = self.client.post(f"/reports/{rep_id}/delete", data={"csrf_token": token}, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        # DB record removed
        self.assertIsNone(db.get_report_by_id(rep_id))

        # Storage file removed
        local_path = storage_service.local_dir / storage_info["storage_path"]
        self.assertFalse(local_path.exists())

    def test_20_storage_failure_does_not_leave_orphan_db_record(self):
        """20. If storage upload fails, no orphan report record is created in the database."""
        self.login_as_patient_alex()
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        token = self.get_csrf_token(path="/reports/upload")

        pdf_bytes = io.BytesIO(b"%PDF-1.4\nSimulated Storage Fail")

        with mock.patch.object(storage_service, "save_file", side_effect=IOError("Simulated disk error")):
            res = self.client.post("/reports/upload", data={
                "title": "Storage Failure Drug Test",
                "report_file": (pdf_bytes, "fail.pdf"),
                "csrf_token": token
            }, content_type="multipart/form-data", follow_redirects=True)
            self.assertIn(b"Failed to store report file", res.data)

        # Verify no orphan record in DB
        reports = db.get_reports_by_patient(pat["user_id"])
        matching = [r for r in reports if r["title"] == "Storage Failure Drug Test"]
        self.assertEqual(len(matching), 0)

    def test_21_database_failure_after_storage_triggers_cleanup(self):
        """21. Compensation semantics: If DB insert fails after storage save, storage object is cleaned up."""
        self.login_as_patient_alex()
        token = self.get_csrf_token(path="/reports/upload")

        pdf_bytes = io.BytesIO(b"%PDF-1.4\nCompensation Test File")

        with mock.patch.object(db, "create_report", side_effect=Exception("Simulated SQLite constraint error")):
            with mock.patch.object(storage_service, "delete_file", wraps=storage_service.delete_file) as mock_del:
                res = self.client.post("/reports/upload", data={
                    "title": "Compensation File Test",
                    "report_file": (pdf_bytes, "compensation.pdf"),
                    "csrf_token": token
                }, content_type="multipart/form-data", follow_redirects=True)
                self.assertIn(b"Database error saving report metadata", res.data)
                # Verify compensation deletion was triggered
                self.assertTrue(mock_del.called)

    def test_22_generated_storage_key_never_contains_path_traversal(self):
        """22. Storage keys never contain path traversal components."""
        svc = StorageService(local_dir=self.temp_storage_dir)
        key = svc.generate_storage_key("usr-..%2f..%2f123", "rep-..%2f456", "test..filename.pdf")
        self.assertNotIn("..", key)
        self.assertFalse(key.startswith("/"))
        self.assertTrue(key.startswith("reports/"))

    # =========================================================================
    # Group 5: S3 & Local Multi-Tier Storage (Tests 23 - 29)
    # =========================================================================

    def test_23_s3_mode_uses_private_object_workflow(self):
        """23. S3 storage uses SSE-AES256 encryption and no public ACLs."""
        mock_s3 = mock.MagicMock()
        mock_s3.put_object.return_value = {"ETag": "fake-etag"}
        mock_s3.generate_presigned_url.return_value = "https://s3.amazonaws.com/test-bucket/report.pdf?X-Amz-Signature=fake"

        svc = StorageService(bucket_name="test-medtrack-private-reports", region_name="us-east-1")
        svc._s3_client = mock_s3

        # Test save
        pdf_bytes = io.BytesIO(b"%PDF-1.4\nS3 Encrypted Test")
        res_save = svc.save_file("usr-test-pat", "rep-s3-test", pdf_bytes, "s3_test.pdf")
        self.assertTrue(svc.is_s3_enabled)

        # Verify put_object used AES256 server-side encryption
        mock_s3.put_object.assert_called_once()
        _, kwargs = mock_s3.put_object.call_args
        self.assertEqual(kwargs["ServerSideEncryption"], "AES256")
        self.assertEqual(kwargs["Bucket"], "test-medtrack-private-reports")
        self.assertNotIn("ACL", kwargs, "No public ACL should ever be passed to S3")

        # Verify pre-signed URL generated with 600s TTL
        access = svc.get_access_url_or_path(res_save["storage_path"], expires_in=600)
        self.assertEqual(access["type"], "s3")
        self.assertIn("https://", access["url"])
        mock_s3.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "test-medtrack-private-reports", "Key": res_save["storage_path"]},
            ExpiresIn=600
        )

    def test_24_local_mode_works_without_aws_credentials(self):
        """24. Local storage mode operates entirely offline with zero AWS calls."""
        svc = StorageService(bucket_name="", local_dir=self.temp_storage_dir)
        self.assertFalse(svc.is_s3_enabled)

        pdf_bytes = io.BytesIO(b"%PDF-1.4\nLocal Offline Test")
        res = svc.save_file("usr-offline", "rep-offline-01", pdf_bytes, "offline.pdf")
        self.assertTrue(Path(self.temp_storage_dir, res["storage_path"]).is_file())

        access = svc.get_access_url_or_path(res["storage_path"])
        self.assertEqual(access["type"], "local")
        self.assertTrue(os.path.isabs(access["path"]))

    def test_25_report_metadata_persists_correctly(self):
        """25. Report fields (title, doctor_id, appointment_id, notes) persist faithfully in SQLite."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        rep_id = "rep-metadata-persistence"
        rep = db.create_report({
            "report_id": rep_id,
            "patient_id": pat["user_id"],
            "doctor_id": doc["user_id"],
            "appointment_id": appt["appointment_id"],
            "title": "Lipid Profile & Glucose Panel",
            "file_name": "lipid_glucose.pdf",
            "file_type": "application/pdf",
            "file_size": 15420,
            "storage_path": "reports/usr-demo/rep-meta/lipid_glucose.pdf",
            "notes": "Fasting 12 hours prior to draw."
        })

        self.assertEqual(rep["title"], "Lipid Profile & Glucose Panel")
        self.assertEqual(rep["doctor_id"], doc["user_id"])
        self.assertEqual(rep["appointment_id"], appt["appointment_id"])
        self.assertEqual(rep["notes"], "Fasting 12 hours prior to draw.")

        retrieved = db.get_report_by_id(rep_id)
        self.assertEqual(retrieved["patient_name"], pat["name"])
        self.assertEqual(retrieved["doctor_name"], doc["name"])

    def test_26_file_size_and_type_metadata_accurate(self):
        """26. File metadata correctly reflects byte count and validated MIME type."""
        svc = StorageService(local_dir=self.temp_storage_dir)
        content = b"%PDF-1.4\n1234567890"
        _, mime, size = svc.validate_file(io.BytesIO(content), "sample.pdf")
        self.assertEqual(mime, "application/pdf")
        self.assertEqual(size, len(content))

    def test_27_presigned_url_generation_only_after_authorization(self):
        """27. S3 pre-signed URL generation is protected by authorization check."""
        pat_a = db.get_user_by_email("patient.demo@medtrack.local")
        pat_b = self.get_or_create_second_patient()

        rep_id = "rep-presigned-auth-check"
        db.create_report({
            "report_id": rep_id,
            "patient_id": pat_a["user_id"],
            "title": "Private S3 Document",
            "file_name": "private.pdf",
            "file_type": "application/pdf",
            "file_size": 100,
            "storage_path": "reports/private.pdf"
        })

        # When unauthorized Patient B attempts access, storage_service is never called
        with self.client.session_transaction() as sess:
            sess["user_id"] = pat_b["user_id"]
            sess["role"] = "patient"
            sess["_auth_token"] = "auth-pat-b-presigned"
            sess["last_activity"] = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

        with mock.patch.object(storage_service, "get_access_url_or_path") as mock_storage:
            res = self.client.get(f"/reports/{rep_id}/download")
            self.assertEqual(res.status_code, 404)
            mock_storage.assert_not_called()

    def test_28_cloudformation_s3_bucket_is_private(self):
        """28. CloudFormation template defines a private S3 bucket with AES256 and PublicAccessBlock."""
        cf_path = Config.BASE_DIR / "aws" / "cloudformation.yaml"
        self.assertTrue(cf_path.is_file())
        with open(cf_path, "r", encoding="utf-8") as f:
            cf_text = f.read()

        self.assertIn("ReportsBucket:", cf_text)
        self.assertIn("BlockPublicAcls: true", cf_text)
        self.assertIn("BlockPublicPolicy: true", cf_text)
        self.assertIn("IgnorePublicAcls: true", cf_text)
        self.assertIn("RestrictPublicBuckets: true", cf_text)
        self.assertIn("SSEAlgorithm: AES256", cf_text)
        self.assertIn("DeletionPolicy: Retain", cf_text)
        self.assertNotIn("s3:ListBucket", cf_text)
        self.assertIn("${ReportsBucket.Arn}/*", cf_text)

    def test_29_static_aws_credentials_not_required(self):
        """29. Application and tests run safely with no AWS_ACCESS_KEY_ID defined."""
        # Ensure Config does not demand hardcoded AWS credentials
        self.assertFalse(hasattr(Config, "AWS_ACCESS_KEY_ID"))
        self.assertFalse(hasattr(Config, "AWS_SECRET_ACCESS_KEY"))

    def test_30_existing_locked_functionality_unaffected(self):
        """30. Prescriptions, medicines, appointments, and authentication continue to operate normally."""
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        meds = db.get_medicines_by_patient(pat["user_id"])
        self.assertGreater(len(meds), 0)

        appts = db.get_appointments_by_patient(pat["user_id"])
        self.assertIsInstance(appts, list)

        rxs = db.get_prescriptions_by_patient(pat["user_id"])
        self.assertIsInstance(rxs, list)

    def test_31_doctor_with_patient_a_relationship_cannot_access_patient_b_report_or_archive(self):
        """31. Doctor A with relationship to Patient A cannot access Patient B's report or report archive."""
        unrelated_pat = self.get_or_create_unrelated_patient()
        rep_id = "rep-patient-b-confidential"
        pdf_bytes = io.BytesIO(b"%PDF-1.4\nConfidential Patient B Document")
        storage_info = storage_service.save_file(unrelated_pat["user_id"], rep_id, pdf_bytes, "patient_b.pdf")

        db.create_report({
            "report_id": rep_id,
            "patient_id": unrelated_pat["user_id"],
            "title": "Patient B Private Biopsy",
            "file_name": storage_info["file_name"],
            "file_type": storage_info["file_type"],
            "file_size": storage_info["file_size"],
            "storage_path": storage_info["storage_path"]
        })

        # Doctor Vance is authorized for Patient A (Alex), but has NO relationship to unrelated_pat
        self.login_as_doctor_vance()

        # Doctor Vance attempting to download unrelated_pat's report must receive 404
        res_dl = self.client.get(f"/reports/{rep_id}/download")
        self.assertEqual(res_dl.status_code, 404)

        # Doctor Vance attempting to access unrelated_pat's report archive must receive 404
        res_arch = self.client.get(f"/doctor/patients/{unrelated_pat['user_id']}/reports")
        self.assertEqual(res_arch.status_code, 404)

    def test_32_doctor_authorization_checked_before_presigned_url_generation(self):
        """32. Doctor authorization is verified strictly before triggering pre-signed S3 URL generation."""
        unrelated_pat = self.get_or_create_unrelated_patient()
        rep_id = "rep-presigned-doc-check"
        db.create_report({
            "report_id": rep_id,
            "patient_id": unrelated_pat["user_id"],
            "title": "Doctor Presigned Guard",
            "file_name": "guard.pdf",
            "file_type": "application/pdf",
            "file_size": 250,
            "storage_path": "reports/guard.pdf"
        })

        # Doctor Vance is not authorized for unrelated_pat
        self.login_as_doctor_vance()

        with mock.patch.object(storage_service, "get_access_url_or_path") as mock_storage:
            res = self.client.get(f"/reports/{rep_id}/download")
            self.assertEqual(res.status_code, 404)
            mock_storage.assert_not_called()

    def test_33_database_failure_after_storage_deletion_triggers_compensation(self):
        """33. If DB deletion fails after storage deletion, failure is reported and storage artifact restored."""
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        rep_id = "rep-del-compensation-test"
        content = b"%PDF-1.4\nAtomic Deletion Failure Test"
        pdf_bytes = io.BytesIO(content)
        storage_info = storage_service.save_file(pat["user_id"], rep_id, pdf_bytes, "comp_test.pdf")

        db.create_report({
            "report_id": rep_id,
            "patient_id": pat["user_id"],
            "title": "Compensation Test Report",
            "file_name": storage_info["file_name"],
            "file_type": storage_info["file_type"],
            "file_size": storage_info["file_size"],
            "storage_path": storage_info["storage_path"]
        })

        local_path = (storage_service.local_dir / storage_info["storage_path"]).resolve()
        self.assertTrue(local_path.is_file())

        self.login_as_patient_alex()
        token = self.get_csrf_token(path="/reports")

        # Force database deletion to fail
        with mock.patch.object(db, "delete_report", side_effect=sqlite3.OperationalError("Simulated database failure during report deletion")):
            res = self.client.post(f"/reports/{rep_id}/delete", data={"csrf_token": token}, follow_redirects=True)
            self.assertEqual(res.status_code, 200)
            self.assertIn(b"Failed to delete medical report record", res.data)
            self.assertNotIn(b"was deleted", res.data)

        # Compensation verification: file was restored to storage
        self.assertTrue(local_path.is_file())
        with open(local_path, "rb") as f:
            self.assertEqual(f.read(), content)

        # Database record still exists
        rep = db.get_report_by_id(rep_id)
        self.assertIsNotNone(rep)

    def test_34_upload_ownership_enforced_from_authenticated_session(self):
        """34. Upload route enforces patient ownership from authenticated session, ignoring client-supplied patient_id."""
        pat_a = db.get_user_by_email("patient.demo@medtrack.local")
        pat_b = self.get_or_create_second_patient()

        self.login_as_patient_alex()
        token = self.get_csrf_token(path="/reports/upload")

        pdf_content = b"%PDF-1.4\nSession Ownership Enforcement"
        data = {
            "title": "Ownership Enforcement Test",
            "patient_id": pat_b["user_id"],  # Client parameter tampering attempt
            "report_file": (io.BytesIO(pdf_content), "ownership.pdf"),
            "csrf_token": token
        }

        res = self.client.post("/reports/upload", data=data, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"uploaded successfully", res.data)

        # Verify report belongs to Patient A (authenticated session), not Patient B
        reps_a = [r for r in db.get_reports_by_patient(pat_a["user_id"]) if r["title"] == "Ownership Enforcement Test"]
        self.assertEqual(len(reps_a), 1)

        reps_b = [r for r in db.get_reports_by_patient(pat_b["user_id"]) if r["title"] == "Ownership Enforcement Test"]
        self.assertEqual(len(reps_b), 0)

    def test_35_upload_unauthorized_doctor_association_rejected(self):
        """35. Patient cannot associate a report with a physician without an established appointment relationship."""
        doc_nair = db.get_user_by_email("doctor.nair@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")

        # Patient Alex has no appointments with Doctor Nair
        self.login_as_patient_alex()
        token = self.get_csrf_token(path="/reports/upload")

        pdf_content = b"%PDF-1.4\nUnauthorized Doctor Assignment"
        data = {
            "title": "Unauthorized Doctor Report",
            "doctor_id": doc_nair["user_id"],  # Doctor with no established appointment
            "report_file": (io.BytesIO(pdf_content), "unauth_doc.pdf"),
            "csrf_token": token
        }

        res = self.client.post("/reports/upload", data=data, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"You do not have an established appointment relationship with this physician", res.data)

        # Verify no report was persisted in database
        reps = [r for r in db.get_reports_by_patient(pat["user_id"]) if r["title"] == "Unauthorized Doctor Report"]
        self.assertEqual(len(reps), 0)


if __name__ == "__main__":
    unittest.main()
