"""
Phase 7 Prescription Management & Doctor Isolation Test Suite
Comprehensive testing for:
1. Deterministic medicine synchronization (medicine_id = 'med_rx_' + prescription_id)
2. Strict doctor isolation (cross-doctor access denial)
3. Strict patient isolation (cross-patient access denial)
4. Shielding of patient OTC medications from doctor oversight
5. Clinical lifecycle preservation (discontinue without hard delete)
6. Protection of doctor-prescribed medicines from patient deletion
7. Appointment ownership & status verification
8. CSRF protection on mutation endpoints
9. Database integrity and test isolation
"""

import os
import re
import json
import datetime
import unittest
from unittest import mock
import tempfile
import shutil

from app import app, db
from config import Config
from services import rate_limiter


class Phase7PrescriptionsTestCase(unittest.TestCase):
    """Dedicated verification test suite for Phase 7 prescription architecture."""

    @classmethod
    def setUpClass(cls):
        """Create an isolated temporary copy of the canonical database for test execution."""
        cls.temp_fd, cls.temp_db_path = tempfile.mkstemp(suffix="_test_phase7.db")
        os.close(cls.temp_fd)
        shutil.copy2(Config.LOCAL_DB_PATH, cls.temp_db_path)
        cls.orig_db_path = db.db_path
        db.db_path = cls.temp_db_path

    @classmethod
    def tearDownClass(cls):
        """Restore original database path and remove temporary database file."""
        db.db_path = cls.orig_db_path
        if os.path.exists(cls.temp_db_path):
            try:
                os.remove(cls.temp_db_path)
            except Exception:
                pass

    def setUp(self):
        """Configure test client with strict security settings for each test."""
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = True
        app.config["SECRET_KEY"] = "phase7-test-key-328f899b-e88a-499b-b8c3"
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

    def login_as_patient_alex(self):
        token = self.get_csrf_token()
        return self.client.post("/login", data={
            "email": "patient.demo@medtrack.local",
            "password": "PatientPass123!",
            "csrf_token": token
        }, follow_redirects=True)

    def get_or_create_second_patient(self):
        """Retrieve a distinct secondary patient from the test database."""
        u = db.get_user_by_email("target.patient@medtrack.local")
        if not u:
            u = db.create_user({
                "email": "target.patient@medtrack.local",
                "password_hash": "dummy",
                "name": "Target Patient",
                "role": "patient"
            })
        return u

    def get_or_create_appointment(self, doctor_id, patient_id, status="CONFIRMED"):
        """Ensure an appointment exists between doctor and patient with specific status."""
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
    # 1. Prescription Creation & Synchronization Tests (Tests 1 - 6)
    # =========================================================================

    def test_01_prescription_creation_succeeds(self):
        """1. Prescription creation succeeds with valid doctor, patient, and schedule data."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Amoxicillin 500mg",
            dosage="1 Capsule",
            schedule_times=["08:00 AM", "08:00 PM"],
            instructions="Take after food. Complete full 7-day course.",
            valid_until="2026-10-01"
        )

        self.assertIsNotNone(rx)
        self.assertTrue(rx["prescription_id"].startswith("rx-"))
        self.assertEqual(rx["status"], "ACTIVE")
        self.assertEqual(rx["medicine_name"], "Amoxicillin 500mg")
        self.assertEqual(rx["dosage"], "1 Capsule")
        self.assertEqual(rx["doctor_id"], doc["user_id"])
        self.assertEqual(rx["patient_id"], pat["user_id"])

    def test_02_prescription_creates_exactly_one_linked_medicine(self):
        """2. An active prescription creates exactly one linked medicine in the medicines table."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Metformin 500mg",
            dosage="1 Tablet",
            schedule_times=["08:00 AM"]
        )

        meds = [m for m in db.get_medicines_by_patient(pat["user_id"]) if m.get("prescription_id") == rx["prescription_id"]]
        self.assertEqual(len(meds), 1)

    def test_03_medicine_id_deterministic_format(self):
        """3. Linked medicine ID must be deterministic: med_rx_<prescription_id>."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Lisinopril 10mg",
            dosage="1 Tablet",
            schedule_times=["09:00 AM"]
        )

        expected_med_id = f"med_rx_{rx['prescription_id']}"
        med = db.get_medicine_by_id(expected_med_id)
        self.assertIsNotNone(med)
        self.assertEqual(med["medicine_id"], expected_med_id)

    def test_04_medicine_prescription_id_stored(self):
        """4. Linked medicine must store prescription_id = prescription.prescription_id."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Atorvastatin 20mg",
            dosage="1 Tablet",
            schedule_times=["09:00 PM"]
        )

        med = db.get_medicine_by_id(f"med_rx_{rx['prescription_id']}")
        self.assertEqual(med["prescription_id"], rx["prescription_id"])

    def test_05_medicine_starts_active(self):
        """5. Linked medicine starts in active state (is_active = 1)."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Omeprazole 20mg",
            dosage="1 Capsule",
            schedule_times=["07:30 AM"]
        )

        med = db.get_medicine_by_id(f"med_rx_{rx['prescription_id']}")
        self.assertEqual(med["is_active"], 1)

    def test_06_otc_medicine_remains_prescription_id_null(self):
        """6. Self-added OTC medicines must have prescription_id = NULL."""
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        otc = db.create_medicine(
            patient_id=pat["user_id"],
            name="Vitamin C 500mg (OTC)",
            dosage="1 Chewable",
            schedule_time="08:00 AM"
        )
        self.assertIsNone(otc.get("prescription_id"))

        retrieved = db.get_medicine_by_id(otc["medicine_id"])
        self.assertIsNone(retrieved["prescription_id"])

    # =========================================================================
    # 2. Doctor Isolation & OTC Shielding Tests (Tests 7 - 9)
    # =========================================================================

    def test_07_doctor_sees_only_their_own_prescriptions(self):
        """7. Doctor Vance sees only prescriptions issued by Vance."""
        doc_vance = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc_vance["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc_vance["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Vance Specific Drug",
            dosage="100mg",
            schedule_times=["10:00 AM"]
        )

        prescriptions = db.get_prescriptions_by_doctor(doc_vance["user_id"])
        rx_ids = [p["prescription_id"] for p in prescriptions]
        self.assertIn(rx["prescription_id"], rx_ids)
        for p in prescriptions:
            self.assertEqual(p["doctor_id"], doc_vance["user_id"])

    def test_08_doctor_a_cannot_see_doctor_b_prescription(self):
        """8. Doctor Chen cannot see prescriptions issued by Doctor Vance for the same patient."""
        doc_vance = db.get_user_by_email("doctor.vance@medtrack.local")
        doc_chen = db.get_user_by_email("doctor.chen@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")

        # Patient has consultations with both doctors
        appt_vance = self.get_or_create_appointment(doc_vance["user_id"], pat["user_id"], "CONFIRMED")
        appt_chen = self.get_or_create_appointment(doc_chen["user_id"], pat["user_id"], "CONFIRMED")

        # Doctor Vance prescribes
        rx_vance = db.create_prescription(
            doctor_id=doc_vance["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt_vance["appointment_id"],
            medicine_name="Vance Heart Med",
            dosage="50mg",
            schedule_times=["08:00 AM"]
        )

        # Doctor Chen accesses prescriptions
        chen_prescriptions = db.get_prescriptions_by_doctor(doc_chen["user_id"])
        chen_rx_ids = [p["prescription_id"] for p in chen_prescriptions]
        self.assertNotIn(rx_vance["prescription_id"], chen_rx_ids)

    def test_09_doctor_cannot_see_patient_otc_medicine(self):
        """9. Patient OTC medicines must NEVER appear in doctor's prescription list."""
        doc_vance = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        self.get_or_create_appointment(doc_vance["user_id"], pat["user_id"], "CONFIRMED")

        # Patient adds private OTC medication
        otc = db.create_medicine(
            patient_id=pat["user_id"],
            name="Confidential Private Supplement",
            dosage="2 Tablets",
            schedule_time="09:00 AM"
        )

        vance_prescriptions = db.get_prescriptions_by_doctor(doc_vance["user_id"])
        med_names = [p["medicine_name"] for p in vance_prescriptions]
        self.assertNotIn("Confidential Private Supplement", med_names)

    # =========================================================================
    # 3. Patient Isolation Tests (Tests 10 - 11)
    # =========================================================================

    def test_10_patient_sees_only_their_own_prescriptions(self):
        """10. Patient Alex sees only prescriptions issued to Alex."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Alex Daily Med",
            dosage="1 Tablet",
            schedule_times=["08:00 AM"]
        )

        alex_prescriptions = db.get_prescriptions_by_patient(pat["user_id"])
        rx_ids = [p["prescription_id"] for p in alex_prescriptions]
        self.assertIn(rx["prescription_id"], rx_ids)

    def test_11_patient_a_cannot_see_patient_b_prescription(self):
        """11. Patient B cannot see prescriptions issued to Patient A."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat_a = db.get_user_by_email("patient.demo@medtrack.local")
        pat_b = self.get_or_create_second_patient()

        appt_a = self.get_or_create_appointment(doc["user_id"], pat_a["user_id"], "CONFIRMED")
        rx_a = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat_a["user_id"],
            appointment_id=appt_a["appointment_id"],
            medicine_name="Patient A Exclusive Med",
            dosage="10mg",
            schedule_times=["08:00 AM"]
        )

        pat_b_prescriptions = db.get_prescriptions_by_patient(pat_b["user_id"])
        rx_ids = [p["prescription_id"] for p in pat_b_prescriptions]
        self.assertNotIn(rx_a["prescription_id"], rx_ids)

    # =========================================================================
    # 4. Authentication & Authorization Boundaries (Tests 12 - 15)
    # =========================================================================

    def test_12_unauthenticated_prescription_access_denied(self):
        """12. Unauthenticated requests to prescription endpoints redirect to login."""
        res1 = self.client.get("/doctor/prescriptions")
        self.assertEqual(res1.status_code, 302)
        self.assertIn("/login", res1.headers["Location"])

        res2 = self.client.get("/prescriptions")
        self.assertEqual(res2.status_code, 302)
        self.assertIn("/login", res2.headers["Location"])

        res3 = self.client.get("/doctor/prescriptions/new")
        self.assertEqual(res3.status_code, 302)

    def test_13_patient_cannot_create_prescription(self):
        """13. Logged-in patient cannot access prescription creation endpoints."""
        self.login_as_patient_alex()
        res_get = self.client.get("/doctor/prescriptions/new")
        self.assertEqual(res_get.status_code, 302)

        token = self.get_csrf_token()
        res_post = self.client.post("/doctor/prescriptions/new", data={
            "patient_id": "usr-c36949c5",
            "medicine_name": "Unauthorized Med",
            "dosage": "500mg",
            "csrf_token": token
        })
        self.assertEqual(res_post.status_code, 302)

    def test_14_doctor_cannot_discontinue_another_doctor_prescription(self):
        """14. Doctor Chen cannot discontinue Doctor Vance's prescription."""
        doc_vance = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc_vance["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc_vance["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Vance Managed Med",
            dosage="25mg",
            schedule_times=["08:00 AM"]
        )

        # Authenticate as Doctor Chen
        self.login_as_doctor_chen()
        token = self.get_csrf_token()

        res = self.client.post(f"/doctor/prescriptions/{rx['prescription_id']}/discontinue", data={
            "csrf_token": token
        })
        self.assertEqual(res.status_code, 404)

        # Verify prescription remains ACTIVE
        refreshed_rx = db.get_prescription_by_id(rx["prescription_id"])
        self.assertEqual(refreshed_rx["status"], "ACTIVE")

    def test_15_correct_doctor_can_discontinue_own_prescription(self):
        """15. Prescribing doctor can discontinue their own prescription."""
        doc_vance = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc_vance["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc_vance["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Vance Removable Med",
            dosage="10mg",
            schedule_times=["08:00 AM"]
        )

        self.login_as_doctor_vance()
        token = self.get_csrf_token()

        res = self.client.post(f"/doctor/prescriptions/{rx['prescription_id']}/discontinue", data={
            "csrf_token": token
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        refreshed_rx = db.get_prescription_by_id(rx["prescription_id"])
        self.assertEqual(refreshed_rx["status"], "DISCONTINUED")

    # =========================================================================
    # 5. Clinical Lifecycle & Deactivation Synchronization (Tests 16 - 18)
    # =========================================================================

    def test_16_discontinuation_sets_linked_medicine_inactive(self):
        """16. Discontinuing prescription sets linked medicine is_active = 0."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Deactivation Target Drug",
            dosage="100mg",
            schedule_times=["08:00 AM"]
        )

        med_id = f"med_rx_{rx['prescription_id']}"
        self.assertEqual(db.get_medicine_by_id(med_id)["is_active"], 1)

        db.discontinue_prescription(rx["prescription_id"], doc["user_id"])
        self.assertEqual(db.get_medicine_by_id(med_id)["is_active"], 0)

    def test_17_discontinued_prescription_remains_in_database(self):
        """17. Discontinued prescription is preserved in DB for clinical audit history."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Audited Historic Drug",
            dosage="50mg",
            schedule_times=["08:00 AM"]
        )

        db.discontinue_prescription(rx["prescription_id"], doc["user_id"])
        saved_rx = db.get_prescription_by_id(rx["prescription_id"])
        self.assertIsNotNone(saved_rx)
        self.assertEqual(saved_rx["status"], "DISCONTINUED")

    def test_18_discontinued_medicine_excluded_from_active_schedule(self):
        """18. Discontinued medicine is excluded from patient active schedule."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Schedule Exclusion Drug",
            dosage="20mg",
            schedule_times=["08:00 AM"]
        )

        sched_active = db.get_patient_schedule(pat["user_id"])
        dose_names = [d["name"] for d in sched_active["doses"]]
        self.assertIn("Schedule Exclusion Drug", dose_names)

        db.discontinue_prescription(rx["prescription_id"], doc["user_id"])

        sched_after = db.get_patient_schedule(pat["user_id"])
        dose_names_after = [d["name"] for d in sched_after["doses"]]
        self.assertNotIn("Schedule Exclusion Drug", dose_names_after)

    # =========================================================================
    # 6. Appointment Ownership Validation (Tests 19 - 23)
    # =========================================================================

    def test_19_appointment_doctor_mismatch_rejected(self):
        """19. Prescribing with appointment assigned to a different doctor is rejected."""
        doc_vance = db.get_user_by_email("doctor.vance@medtrack.local")
        doc_chen = db.get_user_by_email("doctor.chen@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt_chen = self.get_or_create_appointment(doc_chen["user_id"], pat["user_id"], "CONFIRMED")

        with self.assertRaises(ValueError) as ctx:
            db.create_prescription(
                doctor_id=doc_vance["user_id"],
                patient_id=pat["user_id"],
                appointment_id=appt_chen["appointment_id"],
                medicine_name="Mismatch Drug",
                dosage="10mg"
            )
        self.assertIn("doctor mismatch", str(ctx.exception).lower())

    def test_20_appointment_patient_mismatch_rejected(self):
        """20. Prescribing with appointment belonging to a different patient is rejected."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat_a = db.get_user_by_email("patient.demo@medtrack.local")
        pat_b = self.get_or_create_second_patient()

        appt_b = self.get_or_create_appointment(doc["user_id"], pat_b["user_id"], "CONFIRMED")

        with self.assertRaises(ValueError) as ctx:
            db.create_prescription(
                doctor_id=doc["user_id"],
                patient_id=pat_a["user_id"],
                appointment_id=appt_b["appointment_id"],
                medicine_name="Patient Mismatch Drug",
                dosage="10mg"
            )
        self.assertIn("patient mismatch", str(ctx.exception).lower())

    def test_21_invalid_appointment_rejected(self):
        """21. Prescribing with non-existent appointment ID is rejected."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")

        with self.assertRaises(ValueError) as ctx:
            db.create_prescription(
                doctor_id=doc["user_id"],
                patient_id=pat["user_id"],
                appointment_id="nonexistent-appt-9999",
                medicine_name="Fake Appt Drug",
                dosage="10mg"
            )
        self.assertIn("not found", str(ctx.exception).lower())

    def test_22_unsupported_appointment_status_rejected(self):
        """22. Prescribing with PENDING or CANCELLED appointment is rejected."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt_pending = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "PENDING")

        with self.assertRaises(ValueError) as ctx:
            db.create_prescription(
                doctor_id=doc["user_id"],
                patient_id=pat["user_id"],
                appointment_id=appt_pending["appointment_id"],
                medicine_name="Pending Appt Drug",
                dosage="10mg"
            )
        self.assertIn("invalid appointment status", str(ctx.exception).lower())

    def test_23_valid_appointment_succeeds(self):
        """23. Prescribing with CONFIRMED and COMPLETED appointments succeeds."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")

        appt_confirmed = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")
        rx1 = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt_confirmed["appointment_id"],
            medicine_name="Confirmed Appt Drug",
            dosage="10mg"
        )
        self.assertEqual(rx1["status"], "ACTIVE")

        appt_completed = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "COMPLETED")
        rx2 = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt_completed["appointment_id"],
            medicine_name="Completed Appt Drug",
            dosage="10mg"
        )
        self.assertEqual(rx2["status"], "ACTIVE")

    # =========================================================================
    # 7. Uniqueness, Deletion & IDOR Protection (Tests 24 - 30)
    # =========================================================================

    def test_24_duplicate_active_medicine_prevented(self):
        """24. Database uniqueness prevents creating duplicate active medicine for one prescription."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Unique Drug Test",
            dosage="10mg"
        )

        # Attempt to manually insert a second active medicine pointing to the same prescription
        with self.assertRaises(Exception):
            with db._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO medicines (
                        medicine_id, patient_id, name, dosage, schedule_times,
                        schedule_time, frequency, meal_timing, is_active, prescription_id, created_at
                    ) VALUES ('med_rx_dup_test', ?, 'Dup', '10mg', '["08:00"]', '08:00', 'Daily', 'After Food', 1, ?, '2026-09-19T00:00:00Z')
                """, (pat["user_id"], rx["prescription_id"]))
                conn.commit()

    def test_25_patient_cannot_delete_doctor_prescribed_medicine(self):
        """25. Patient cannot casually delete a doctor-prescribed medicine."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Non-Deletable Rx Med",
            dosage="10mg"
        )
        med_id = f"med_rx_{rx['prescription_id']}"

        # Service level denial
        res_svc = db.delete_medicine(med_id, pat["user_id"])
        self.assertFalse(res_svc)

        # HTTP Route level denial
        self.login_as_patient_alex()
        token = self.get_csrf_token()
        res_http = self.client.post(f"/medicines/{med_id}/delete", data={
            "csrf_token": token
        }, follow_redirects=True)
        self.assertIn(b"Prescription medications cannot be removed by patients", res_http.data)

        # Medicine remains in DB
        self.assertIsNotNone(db.get_medicine_by_id(med_id))

    def test_26_patient_can_delete_otc_medicine(self):
        """26. Patient can still delete their own OTC medicines."""
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        otc = db.create_medicine(
            patient_id=pat["user_id"],
            name="Deletable OTC Supplement",
            dosage="1 Pill",
            schedule_time="08:00 AM"
        )
        med_id = otc["medicine_id"]

        self.login_as_patient_alex()
        token = self.get_csrf_token()
        res = self.client.post(f"/medicines/{med_id}/delete", data={
            "csrf_token": token
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Removed Deletable OTC Supplement", res.data)
        self.assertIsNone(db.get_medicine_by_id(med_id))

    def test_27_cross_patient_detail_access_denied(self):
        """27. Patient B attempting to view Patient A's prescription detail receives 404."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat_a = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat_a["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat_a["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Confidential Med A",
            dosage="10mg"
        )

        # Log in as second patient
        pat_b = self.get_or_create_second_patient()
        token = self.get_csrf_token()
        self.client.post("/login", data={
            "email": pat_b["email"],
            "password": "WrongPasswordOrSkipAuth",
            "csrf_token": token
        })

        # Set session directly for test patient B
        with self.client.session_transaction() as sess:
            sess["user_id"] = pat_b["user_id"]
            sess["role"] = "patient"
            sess["_auth_token"] = "valid-test-token"
            sess["last_activity"] = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

        res = self.client.get(f"/prescriptions/{rx['prescription_id']}")
        self.assertEqual(res.status_code, 404)

    def test_28_cross_doctor_detail_access_denied(self):
        """28. Doctor Chen attempting to view Doctor Vance's prescription detail receives 404."""
        doc_vance = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc_vance["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc_vance["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Vance Cardiology Spec",
            dosage="10mg"
        )

        self.login_as_doctor_chen()
        res = self.client.get(f"/doctor/prescriptions/{rx['prescription_id']}")
        self.assertEqual(res.status_code, 404)

    def test_29_prescription_status_displayed_correctly(self):
        """29. Prescription detail view displays accurate clinical status."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Status Check Med",
            dosage="50mg"
        )

        self.login_as_doctor_vance()
        res = self.client.get(f"/doctor/prescriptions/{rx['prescription_id']}")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"ACTIVE", res.data)
        self.assertIn(b"Status Check Med", res.data)

    def test_30_prescription_history_remains_after_discontinuation(self):
        """30. Discontinued prescription continues to be returned in history queries."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Historic Persistent Drug",
            dosage="50mg"
        )
        db.discontinue_prescription(rx["prescription_id"], doc["user_id"])

        # Prescriptions by doctor includes discontinued
        doc_rx = db.get_prescriptions_by_doctor(doc["user_id"])
        rx_match = [p for p in doc_rx if p["prescription_id"] == rx["prescription_id"]]
        self.assertEqual(len(rx_match), 1)
        self.assertEqual(rx_match[0]["status"], "DISCONTINUED")

        # Prescriptions by patient includes discontinued
        pat_rx = db.get_prescriptions_by_patient(pat["user_id"])
        rx_match_pat = [p for p in pat_rx if p["prescription_id"] == rx["prescription_id"]]
        self.assertEqual(len(rx_match_pat), 1)
        self.assertEqual(rx_match_pat[0]["status"], "DISCONTINUED")

    # =========================================================================
    # 8. CSRF Protection on New Prescription Endpoints (Tests 31 - 32)
    # =========================================================================

    def test_31_csrf_required_on_prescription_create(self):
        """31. POST /doctor/prescriptions/new without CSRF token is rejected with HTTP 400."""
        self.login_as_doctor_vance()
        res = self.client.post("/doctor/prescriptions/new", data={
            "patient_id": "usr-c36949c5",
            "medicine_name": "CSRF Missing Med",
            "dosage": "100mg"
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn(b"Security Validation Failed", res.data)

    def test_32_csrf_required_on_prescription_discontinue(self):
        """32. POST /doctor/prescriptions/<id>/discontinue without CSRF token is rejected with 400."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="CSRF Discontinue Drug",
            dosage="10mg"
        )

        self.login_as_doctor_vance()
        res = self.client.post(f"/doctor/prescriptions/{rx['prescription_id']}/discontinue", data={})
        self.assertEqual(res.status_code, 400)
        self.assertIn(b"Security Validation Failed", res.data)

    # =========================================================================
    # 9. Atomic Transaction Rollback & Lifecycle Verification (Tests 33 - 37)
    # =========================================================================

    def test_33_atomic_rollback_on_linked_medicine_failure(self):
        """33. Failure during linked medicine insertion rolls back prescription; leaves no orphans."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        fake_hex = "deadbeef12345678"
        target_rx_id = f"rx-{fake_hex[:8]}"
        target_med_id = f"med_rx_{target_rx_id}"

        # Pre-insert a conflicting row with target_med_id so the second INSERT fails with PRIMARY KEY collision
        with db._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO medicines (
                    medicine_id, patient_id, name, dosage, schedule_times,
                    schedule_time, frequency, meal_timing, is_active, created_at
                ) VALUES (?, ?, 'Conflicting Drug', '10mg', '["08:00"]', '08:00 AM', 'Daily', 'After Food', 1, '2026-09-19T00:00:00Z')
            """, (target_med_id, pat["user_id"]))
            conn.commit()

        # Mock uuid.uuid4 to produce the deterministic conflicting ID
        mock_obj = mock.MagicMock()
        mock_obj.hex = fake_hex
        with mock.patch("uuid.uuid4", return_value=mock_obj):
            with self.assertRaises(ValueError) as ctx:
                db.create_prescription(
                    doctor_id=doc["user_id"],
                    patient_id=pat["user_id"],
                    appointment_id=appt["appointment_id"],
                    medicine_name="Rollback Test Drug",
                    dosage="25mg"
                )
            self.assertIn("Integrity violation", str(ctx.exception))

        # Verify NO orphan prescription exists in the database
        with db._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM prescriptions WHERE prescription_id = ?", (target_rx_id,))
            orphan_rx = cursor.fetchone()
            self.assertIsNone(orphan_rx, "Prescription must be rolled back if medicine insert fails!")

            cursor.execute("SELECT * FROM medicines WHERE prescription_id = ?", (target_rx_id,))
            orphan_med = cursor.fetchone()
            self.assertIsNone(orphan_med, "No linked medicine should reference rolled back prescription!")

            # Verify DB integrity is completely intact
            cursor.execute("PRAGMA integrity_check;")
            integrity = [tuple(r) for r in cursor.fetchall()]
            self.assertEqual(integrity, [("ok",)])

            cursor.execute("PRAGMA foreign_key_check;")
            fk_violations = cursor.fetchall()
            self.assertEqual(fk_violations, [])

    def test_34_valid_until_past_date_rejected(self):
        """34. create_prescription rejects valid_until dates in the past."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        with self.assertRaises(ValueError) as ctx:
            db.create_prescription(
                doctor_id=doc["user_id"],
                patient_id=pat["user_id"],
                appointment_id=appt["appointment_id"],
                medicine_name="Expired Creation Drug",
                dosage="10mg",
                valid_until="2020-01-01"
            )
        self.assertIn("cannot be in the past", str(ctx.exception).lower())

    def test_35_valid_until_stored_and_retrieved(self):
        """35. Valid future valid_until date is stored in prescription and linked medicine end_date."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        future_date = "2027-12-31"
        rx = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Long Term Maintenance Drug",
            dosage="20mg",
            valid_until=future_date
        )
        self.assertEqual(rx["valid_until"], future_date)

        saved_rx = db.get_prescription_by_id(rx["prescription_id"])
        self.assertEqual(saved_rx["valid_until"], future_date)

        linked_med = db.get_medicine_by_id(f"med_rx_{rx['prescription_id']}")
        self.assertEqual(linked_med["end_date"], future_date)

    def test_36_prescription_lifecycle_expired_and_superseded(self):
        """36. Transitions to EXPIRED and SUPERSEDED deactivate linked medicine."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        # Test EXPIRED lifecycle
        rx_expired = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Lifecycle Expired Drug",
            dosage="10mg"
        )
        med_expired_id = f"med_rx_{rx_expired['prescription_id']}"
        self.assertEqual(db.get_medicine_by_id(med_expired_id)["is_active"], 1)

        db.update_prescription_status(rx_expired["prescription_id"], "EXPIRED", doctor_id=doc["user_id"])
        self.assertEqual(db.get_prescription_by_id(rx_expired["prescription_id"])["status"], "EXPIRED")
        self.assertEqual(db.get_medicine_by_id(med_expired_id)["is_active"], 0)

        # Test SUPERSEDED lifecycle
        rx_superseded = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Lifecycle Superseded Drug",
            dosage="10mg"
        )
        med_superseded_id = f"med_rx_{rx_superseded['prescription_id']}"
        self.assertEqual(db.get_medicine_by_id(med_superseded_id)["is_active"], 1)

        db.update_prescription_status(rx_superseded["prescription_id"], "SUPERSEDED", doctor_id=doc["user_id"])
        self.assertEqual(db.get_prescription_by_id(rx_superseded["prescription_id"])["status"], "SUPERSEDED")
        self.assertEqual(db.get_medicine_by_id(med_superseded_id)["is_active"], 0)

    def test_37_invalid_lifecycle_transitions_rejected(self):
        """37. Terminal prescriptions cannot transition to other statuses or back to ACTIVE."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="Terminal Drug",
            dosage="10mg"
        )

        # Transition to DISCONTINUED
        db.discontinue_prescription(rx["prescription_id"], doc["user_id"])

        # Attempt to revive to ACTIVE must be rejected
        with self.assertRaises(ValueError) as ctx:
            db.update_prescription_status(rx["prescription_id"], "ACTIVE", doc["user_id"])
        self.assertIn("terminal state", str(ctx.exception).lower())

        # Attempt to transition to another terminal state must be rejected
        with self.assertRaises(ValueError) as ctx:
            db.update_prescription_status(rx["prescription_id"], "EXPIRED", doc["user_id"])
        self.assertIn("terminal state", str(ctx.exception).lower())

        # Unsupported status must be rejected
        with self.assertRaises(ValueError) as ctx:
            db.update_prescription_status(rx["prescription_id"], "NONEXISTENT_STATUS")
        self.assertIn("invalid prescription status", str(ctx.exception).lower())

    # =========================================================================
    # 7. DEF-002 Regression: Multi-Dose Prescription Schedule Parsing (Tests 38 - 40)
    # =========================================================================

    def test_38_prescription_form_multi_dose_comma_separated_parsing(self):
        """38. [DEF-002] Submitting comma-separated schedule_time via form parses into independent times."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        self.login_as_doctor_vance()
        token = self.get_csrf_token(path="/doctor/prescriptions/new")

        # Submit with schedule_time (singular form name for backward compatibility)
        res = self.client.post("/doctor/prescriptions/new", data={
            "patient_id": pat["user_id"],
            "appointment_id": appt["appointment_id"],
            "medicine_name": "DEF002 Form Drug",
            "dosage": "100mg",
            "schedule_time": "08:00 AM, 02:00 PM, 08:00 PM",
            "frequency": "Three Times Daily",
            "meal_timing": "After Food",
            "csrf_token": token
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        # Retrieve prescription from DB and verify exact list representation
        rx_list = [r for r in db.get_prescriptions_by_patient(pat["user_id"]) if r["medicine_name"] == "DEF002 Form Drug"]
        self.assertEqual(len(rx_list), 1)
        rx = rx_list[0]
        parsed_times = json.loads(rx["schedule_times"])
        self.assertEqual(parsed_times, ["08:00 AM", "02:00 PM", "08:00 PM"])

        # Also verify linked medicine schedule_times matches
        med = db.get_medicine_by_id(f"med_rx_{rx['prescription_id']}")
        self.assertIsNotNone(med)
        med_times = json.loads(med["schedule_times"])
        self.assertEqual(med_times, ["08:00 AM", "02:00 PM", "08:00 PM"])

    def test_39_prescription_multi_dose_creates_three_independent_patient_doses(self):
        """39. [DEF-002] Three-dose prescription creates three independent dose slots in patient schedule."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="DEF002 Multi-Dose Tablet",
            dosage="20mg",
            schedule_times=["08:00 AM, 02:00 PM, 08:00 PM"]  # tests list containing comma-separated string
        )

        # Verify normalizer split the list element
        self.assertEqual(json.loads(rx["schedule_times"]), ["08:00 AM", "02:00 PM", "08:00 PM"])

        schedule = db.get_patient_schedule(pat["user_id"])
        drug_doses = [d for d in schedule["doses"] if d["name"] == "DEF002 Multi-Dose Tablet"]
        self.assertEqual(len(drug_doses), 3)

        dose_times = [d["schedule_time"] for d in drug_doses]
        self.assertEqual(set(dose_times), {"08:00 AM", "02:00 PM", "08:00 PM"})

    def test_40_prescription_multi_dose_independent_intake_recording(self):
        """40. [DEF-002] Taking 08:00 AM dose does not mark 02:00 PM or 08:00 PM doses as taken."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        pat = db.get_user_by_email("patient.demo@medtrack.local")
        appt = self.get_or_create_appointment(doc["user_id"], pat["user_id"], "CONFIRMED")

        rx = db.create_prescription(
            doctor_id=doc["user_id"],
            patient_id=pat["user_id"],
            appointment_id=appt["appointment_id"],
            medicine_name="DEF002 Intake Drug",
            dosage="50mg",
            schedule_times=["08:00 AM", "02:00 PM", "08:00 PM"]
        )
        med_id = f"med_rx_{rx['prescription_id']}"

        # Record intake for ONLY the 08:00 AM dose
        today_str = datetime.date.today().isoformat()
        db.record_intake(
            patient_id=pat["user_id"],
            medicine_id=med_id,
            status="TAKEN",
            scheduled_date=today_str,
            scheduled_time="08:00 AM"
        )

        schedule = db.get_patient_schedule(pat["user_id"], target_date=today_str)
        drug_doses = {d["schedule_time"]: d["status"] for d in schedule["doses"] if d["name"] == "DEF002 Intake Drug"}

        self.assertIn("08:00 AM", drug_doses)
        self.assertIn("02:00 PM", drug_doses)
        self.assertIn("08:00 PM", drug_doses)

        # 08:00 AM must be TAKEN
        self.assertEqual(drug_doses["08:00 AM"], "TAKEN")

        # 02:00 PM and 08:00 PM must NOT be TAKEN
        self.assertNotEqual(drug_doses["02:00 PM"], "TAKEN")
        self.assertNotEqual(drug_doses["08:00 PM"], "TAKEN")


if __name__ == "__main__":
    unittest.main()
