"""
MedTrack Automated Test Suite
Covers all 16 mandatory scenarios specified in the SkillWallet project requirements:
1. Home page
2. Registration
3. Duplicate registration
4. Login
5. Invalid login
6. Logout
7. Patient dashboard
8. Appointment creation
9. Appointment listing
10. Appointment cancellation
11. Doctor dashboard
12. Doctor appointment confirmation
13. Diagnosis creation
14. Diagnosis viewing (patient viewing own records)
15. Unauthorized record access (protection against cross-patient data access)
16. Mock SNS notification
"""

import unittest
import datetime
import tempfile
import shutil
import os
from app import app, db, sns
from config import Config

class MedTrackTestCase(unittest.TestCase):
    """Integration and scenario tests for MedTrack healthcare application."""

    @classmethod
    def setUpClass(cls):
        """Create an isolated temporary copy of the canonical database for test execution."""
        cls.temp_fd, cls.temp_db_path = tempfile.mkstemp(suffix="_test_app.db")
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
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.config["SECRET_KEY"] = "test-secret-key-college-demo"
        app.config["DEMO_MODE"] = True
        self.client = app.test_client()

        self.test_email = f"patient_{int(datetime.datetime.now().timestamp())}@example.com"
        self.test_password = "SecurePassword123!"
        self.test_name = "Alex Taylor"

    def test_01_home_page(self):
        """1. Verify home landing page renders MedTrack branding and navigation."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"MedTrack", response.data)
        self.assertIn(b"AWS Cloud-Enabled", response.data)
        self.assertIn(b"Appointment Scheduling", response.data)

    def test_02_registration_success(self):
        """2. Verify patient registration creates record with hashed password."""
        response = self.client.post("/register", data={
            "name": self.test_name,
            "email": self.test_email,
            "password": self.test_password,
            "phone": "+1-555-0199",
            "date_of_birth": "1995-04-20",
            "gender": "Female"
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Registration successful", response.data)

        # Verify record in database
        user = db.get_user_by_email(self.test_email)
        self.assertIsNotNone(user)
        self.assertEqual(user["name"], self.test_name)
        self.assertEqual(user["role"], "patient")
        self.assertNotEqual(user["password_hash"], self.test_password)

    def test_03_duplicate_registration_prevention(self):
        """3. Verify duplicate email registration is rejected."""
        email = f"dup_{int(datetime.datetime.now().timestamp())}@example.com"
        # First registration
        self.client.post("/register", data={
            "name": "First Patient",
            "email": email,
            "password": "Password123!",
            "phone": "555-0100",
            "date_of_birth": "1990-01-01",
            "gender": "Male"
        })

        # Second registration with identical email
        response = self.client.post("/register", data={
            "name": "Second Patient",
            "email": email,
            "password": "Password456!",
            "phone": "555-0200",
            "date_of_birth": "1992-02-02",
            "gender": "Female"
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"already exists", response.data)

    def test_04_login_success(self):
        """4. Verify successful authentication establishes session."""
        # Create user
        email = f"login_user_{int(datetime.datetime.now().timestamp())}@example.com"
        self.client.post("/register", data={
            "name": "Login Tester",
            "email": email,
            "password": "Password123!",
            "phone": "555-0103",
            "date_of_birth": "1994-06-15",
            "gender": "Other"
        })

        response = self.client.post("/login", data={
            "email": email,
            "password": "Password123!"
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Welcome back", response.data)
        self.assertIn(b"Patient Portal", response.data)

    def test_05_invalid_login_rejection(self):
        """5. Verify invalid password or unknown email is rejected."""
        response = self.client.post("/login", data={
            "email": "unknown_patient@example.com",
            "password": "WrongPassword!"
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Invalid email address or password", response.data)

    def test_06_logout(self):
        """6. Verify logout terminates user session."""
        self.client.get("/demo-login/patient")
        response = self.client.post("/logout", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"signed out safely", response.data)

        # Attempting to access dashboard should now redirect to login
        dash_res = self.client.get("/dashboard", follow_redirects=True)
        self.assertIn(b"Please sign in", dash_res.data)

    def test_07_patient_dashboard(self):
        """7. Verify patient dashboard displays personal info and widgets."""
        self.client.get("/demo-login/patient")
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Welcome,", response.data)
        self.assertIn(b"Upcoming Visits", response.data)
        self.assertIn(b"Patient Information", response.data)

    def test_08_appointment_creation(self):
        """8. Verify patient can select doctor, date, time, and book appointment."""
        self.client.get("/demo-login/patient")
        doctors = db.get_doctors()
        self.assertGreater(len(doctors), 0)
        doctor_id = doctors[0]["user_id"]

        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        response = self.client.post("/appointments/new", data={
            "doctor_id": doctor_id,
            "appointment_date": tomorrow,
            "appointment_time": "10:00 AM",
            "reason": "Routine clinical checkup and consultation"
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Appointment scheduled successfully", response.data)

    def test_09_appointment_listing(self):
        """9. Verify patient can view their list of booked appointments."""
        self.client.get("/demo-login/patient")
        response = self.client.get("/appointments")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"My Appointments History", response.data)

    def test_10_appointment_cancellation(self):
        """10. Verify patient can cancel an appointment."""
        self.client.get("/demo-login/patient")
        patient_user = db.get_user_by_email("patient.demo@medtrack.local")
        doctors = db.get_doctors()

        # Create appointment to cancel
        appt = db.create_appointment({
            "patient_id": patient_user["user_id"],
            "doctor_id": doctors[0]["user_id"],
            "appointment_date": (datetime.date.today() + datetime.timedelta(days=3)).isoformat(),
            "appointment_time": "02:00 PM",
            "reason": "Headache consultation",
            "status": "PENDING"
        })

        response = self.client.post(f"/appointments/{appt['appointment_id']}/cancel", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Appointment has been cancelled", response.data)

        # Verify status in database
        updated = db.get_appointment_by_id(appt["appointment_id"])
        self.assertEqual(updated["status"], "CANCELLED")

    def test_11_doctor_dashboard(self):
        """11. Verify doctor dashboard displays assigned patient appointments."""
        self.client.get("/demo-login/doctor")
        response = self.client.get("/doctor/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Physician Clinical Console", response.data)
        self.assertIn(b"Assigned Patient Appointments", response.data)

    def test_12_doctor_appointment_confirmation(self):
        """12. Verify doctor can confirm an assigned appointment."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        self.client.get("/demo-login/patient")
        patient = db.get_user_by_email("patient.demo@medtrack.local")

        # Book appointment
        appt = db.create_appointment({
            "patient_id": patient["user_id"],
            "doctor_id": doc["user_id"],
            "appointment_date": (datetime.date.today() + datetime.timedelta(days=2)).isoformat(),
            "appointment_time": "11:00 AM",
            "reason": "Joint pain assessment",
            "status": "PENDING"
        })

        # Doctor logs in and confirms
        self.client.get("/demo-login/doctor")
        response = self.client.post(f"/doctor/appointments/{appt['appointment_id']}/status", data={
            "status": "CONFIRMED"
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Appointment confirmed successfully", response.data)

        updated = db.get_appointment_by_id(appt["appointment_id"])
        self.assertEqual(updated["status"], "CONFIRMED")

    def test_13_diagnosis_creation(self):
        """13. Verify doctor can submit clinical diagnosis upon visit completion."""
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        self.client.get("/demo-login/patient")
        patient = db.get_user_by_email("patient.demo@medtrack.local")

        appt = db.create_appointment({
            "patient_id": patient["user_id"],
            "doctor_id": doc["user_id"],
            "appointment_date": datetime.date.today().isoformat(),
            "appointment_time": "09:00 AM",
            "reason": "Respiratory checkup",
            "status": "CONFIRMED"
        })

        self.client.get("/demo-login/doctor")
        response = self.client.post(f"/doctor/diagnosis/new/{appt['appointment_id']}", data={
            "date": datetime.date.today().isoformat(),
            "diagnosis": "Mild allergic rhinitis. Advised saline nasal spray and hydration."
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Clinical diagnosis saved", response.data)

        # Verify appointment updated to COMPLETED
        updated_appt = db.get_appointment_by_id(appt["appointment_id"])
        self.assertEqual(updated_appt["status"], "COMPLETED")

        # Verify diagnosis created
        diags = db.get_diagnoses_by_patient(patient["user_id"])
        self.assertGreaterEqual(len(diags), 1)
        self.assertIn("Mild allergic rhinitis", diags[0]["diagnosis"])
        self.assertEqual(diags[0]["appointment_id"], appt["appointment_id"])

    def test_14_diagnosis_viewing(self):
        """14. Verify patient can view their own diagnosis records."""
        self.client.get("/demo-login/patient")
        response = self.client.get("/diagnoses")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Confidential Medical Diagnoses", response.data)

    def test_15_unauthorized_record_access_prevention(self):
        """15. Verify role boundaries and cross-patient isolation."""
        # Patient cannot access doctor dashboard
        self.client.get("/demo-login/patient")
        doc_dash = self.client.get("/doctor/dashboard", follow_redirects=True)
        self.assertIn(b"Access restricted", doc_dash.data)

        # Patient cannot cancel another patient's appointment
        other_patient = db.create_user({
            "name": "Other Patient",
            "email": f"other_{int(datetime.datetime.now().timestamp())}@example.com",
            "password_hash": "dummyhash",
            "role": "patient"
        })
        doctors = db.get_doctors()
        other_appt = db.create_appointment({
            "patient_id": other_patient["user_id"],
            "doctor_id": doctors[0]["user_id"],
            "appointment_date": "2026-10-01",
            "appointment_time": "10:00 AM",
            "reason": "Checkup",
            "status": "PENDING"
        })

        cancel_attempt = self.client.post(f"/appointments/{other_appt['appointment_id']}/cancel", follow_redirects=True)
        self.assertIn(b"Unauthorized", cancel_attempt.data)

    def test_16_mock_sns_notifications(self):
        """16. Verify mock SNS notification dispatch for all 4 application events."""
        patient_id = "test-patient-sns"
        patient_name = "Alex Taylor"
        doctor_name = "Dr. Marcus Vance"

        # 1. Appointment Booked
        res1 = sns.notify_appointment_booked(patient_id, patient_name, doctor_name, "2026-10-10", "10:00 AM")
        self.assertTrue(res1)

        # 2. Appointment Confirmed
        res2 = sns.notify_appointment_confirmed(patient_id, patient_name, doctor_name, "2026-10-10", "10:00 AM")
        self.assertTrue(res2)

        # 3. Appointment Cancelled
        res3 = sns.notify_appointment_cancelled(patient_id, patient_name, doctor_name, "2026-10-10", "10:00 AM")
        self.assertTrue(res3)

        # 4. Diagnosis Submitted
        res4 = sns.notify_diagnosis_submitted(patient_id, patient_name, doctor_name, "2026-10-10")
        self.assertTrue(res4)

    def test_17_patient_medicine_creation(self):
        """17. Verify patient can add a medication prescription to their schedule."""
        self.client.get("/demo-login/patient")
        response = self.client.post("/medicines/new", data={
            "name": "Paracetamol 500mg",
            "dosage": "1 Tablet (500mg)",
            "schedule_time": "08:00 AM",
            "frequency": "Daily",
            "meal_timing": "After Food",
            "notes": "Take with breakfast"
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Successfully scheduled Paracetamol 500mg", response.data)

    def test_18_patient_medicine_cabinet_listing(self):
        """18. Verify patient can view their medicine cabinet."""
        self.client.get("/demo-login/patient")
        response = self.client.get("/medicines")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"My Medicine Cabinet", response.data)

    def test_19_dose_intake_logging(self):
        """19. Verify patient can log a scheduled dose as TAKEN or SKIPPED."""
        self.client.get("/demo-login/patient")
        patient = db.get_user_by_email("patient.demo@medtrack.local")
        med = db.create_medicine(patient["user_id"], "Vitamin C 500mg", "1 Tablet", "09:00 AM", "Daily", "With Food")

        response = self.client.post("/medicines/intake/log", data={
            "medicine_id": med["medicine_id"],
            "status": "TAKEN"
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"TAKEN", response.data)

    def test_20_sns_dose_reminder_dispatch(self):
        """20. Verify patient can trigger simulated Amazon SNS dose reminder."""
        self.client.get("/demo-login/patient")
        patient = db.get_user_by_email("patient.demo@medtrack.local")
        med = db.create_medicine(patient["user_id"], "Amoxicillin 250mg", "1 Capsule", "08:00 PM", "Daily", "After Food")

        response = self.client.post(f"/medicines/{med['medicine_id']}/remind", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Amazon SNS Dose Reminder dispatched", response.data)

    def test_21_intake_history_view(self):
        """21. Verify patient can view their medicine intake compliance history."""
        self.client.get("/demo-login/patient")
        response = self.client.get("/medicines/history")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Medication Intake History", response.data)

    def test_22_profile_caregiver_update(self):
        """22. TEST A — Profile caregiver update: save caregiver_name, caregiver_phone, caregiver_email."""
        self.client.get("/demo-login/patient")
        patient = db.get_user_by_email("patient.demo@medtrack.local")

        post_data = {
            "name": patient["name"],
            "phone": patient.get("phone", ""),
            "date_of_birth": patient.get("date_of_birth", ""),
            "gender": patient.get("gender", ""),
            "caregiver_name": "Test Caregiver",
            "caregiver_phone": "+91 9876543210",
            "caregiver_email": "caregiver@example.com"
        }
        res = self.client.post("/profile", data=post_data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        updated = db.get_user_by_id(patient["user_id"])
        self.assertEqual(updated["caregiver_name"], "Test Caregiver")
        self.assertEqual(updated["caregiver_phone"], "+91 9876543210")
        self.assertEqual(updated["caregiver_email"], "caregiver@example.com")

    def test_23_profile_get_roundtrip(self):
        """23. TEST B — Profile GET round-trip: verify response contains saved caregiver values."""
        self.client.get("/demo-login/patient")
        patient = db.get_user_by_email("patient.demo@medtrack.local")

        db.update_user(patient["user_id"], {
            "caregiver_name": "Test Caregiver",
            "caregiver_phone": "+91 9876543210",
            "caregiver_email": "caregiver@example.com"
        })

        res = self.client.get("/profile")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Test Caregiver", html)
        self.assertIn("+91 9876543210", html)
        self.assertIn("caregiver@example.com", html)

    def test_24_profile_caregiver_update_existing(self):
        """24. TEST C — Update existing caregiver data: change values and verify database contains new values."""
        self.client.get("/demo-login/patient")
        patient = db.get_user_by_email("patient.demo@medtrack.local")

        post_data = {
            "name": patient["name"],
            "phone": patient.get("phone", ""),
            "date_of_birth": patient.get("date_of_birth", ""),
            "gender": patient.get("gender", ""),
            "caregiver_name": "Updated Caregiver",
            "caregiver_phone": "+1-555-0199",
            "caregiver_email": "updated.caregiver@example.com"
        }
        res = self.client.post("/profile", data=post_data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        updated = db.get_user_by_id(patient["user_id"])
        self.assertEqual(updated["caregiver_name"], "Updated Caregiver")
        self.assertEqual(updated["caregiver_phone"], "+1-555-0199")
        self.assertEqual(updated["caregiver_email"], "updated.caregiver@example.com")

    def test_25_profile_caregiver_clear_data(self):
        """25. TEST D — Clear caregiver data: submit empty values and verify database values are cleared."""
        self.client.get("/demo-login/patient")
        patient = db.get_user_by_email("patient.demo@medtrack.local")

        db.update_user(patient["user_id"], {
            "caregiver_name": "Initial Caregiver",
            "caregiver_phone": "+1-555-0199",
            "caregiver_email": "initial@example.com"
        })

        clear_data = {
            "name": patient["name"],
            "phone": patient.get("phone", ""),
            "date_of_birth": patient.get("date_of_birth", ""),
            "gender": patient.get("gender", ""),
            "caregiver_name": "",
            "caregiver_phone": "",
            "caregiver_email": ""
        }
        res = self.client.post("/profile", data=clear_data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        updated = db.get_user_by_id(patient["user_id"])
        self.assertEqual(updated["caregiver_name"], "")
        self.assertEqual(updated["caregiver_phone"], "")
        self.assertEqual(updated["caregiver_email"], "")

    def test_26_profile_authorization_isolation(self):
        """26. TEST E — Authorization isolation: patient cannot update another patient's profile using user_id."""
        self.client.get("/demo-login/patient")
        doctor = db.get_user_by_id("doc-001")
        original_doc_name = doctor["name"]

        post_data = {
            "user_id": doctor["user_id"],
            "name": "Malicious Name Attempt",
            "caregiver_name": "Hacked Caregiver"
        }
        res = self.client.post("/profile", data=post_data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        doctor_after = db.get_user_by_id(doctor["user_id"])
        self.assertEqual(doctor_after["name"], original_doc_name)
        self.assertNotEqual(doctor_after.get("caregiver_name"), "Hacked Caregiver")

    def test_27_profile_caregiver_validation(self):
        """27. TEST F — Validation: invalid caregiver phone or email is rejected with warning."""
        self.client.get("/demo-login/patient")
        patient = db.get_user_by_email("patient.demo@medtrack.local")

        # Invalid phone format rejected
        invalid_phone_data = {
            "name": patient["name"],
            "caregiver_name": "Caregiver",
            "caregiver_phone": "invalid-phone-abc",
            "caregiver_email": "valid@example.com"
        }
        res = self.client.post("/profile", data=invalid_phone_data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Please enter a valid caregiver phone number.", res.data)

        # Invalid email format rejected
        invalid_email_data = {
            "name": patient["name"],
            "caregiver_name": "Caregiver",
            "caregiver_phone": "+1-555-0199",
            "caregiver_email": "not-an-email"
        }
        res = self.client.post("/profile", data=invalid_email_data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Please enter a valid caregiver email address.", res.data)

    def test_28_def003_diagnosis_appointment_fk_link(self):
        """28. DEF-003 A-C: Diagnosis created via route links appointment_id, completes appointment, scopes patient."""
        patient = db.get_user_by_email("patient.demo@medtrack.local")
        doc = db.get_user_by_email("doctor.vance@medtrack.local")

        appt = db.create_appointment({
            "patient_id": patient["user_id"],
            "doctor_id": doc["user_id"],
            "appointment_date": datetime.date.today().isoformat(),
            "appointment_time": "11:00 AM",
            "reason": "Cardiac consultation",
            "status": "CONFIRMED"
        })

        self.client.get("/demo-login/doctor")
        response = self.client.post(f"/doctor/diagnosis/new/{appt['appointment_id']}", data={
            "date": datetime.date.today().isoformat(),
            "diagnosis": "Mild sinus bradycardia observed. Routine monitoring recommended."
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Clinical diagnosis saved", response.data)

        # A. Verify diagnosis record has appointment_id populated
        diags = db.get_diagnoses_by_patient(patient["user_id"])
        matching_diag = next((d for d in diags if d.get("appointment_id") == appt["appointment_id"]), None)
        self.assertIsNotNone(matching_diag, "Diagnosis record must contain the linked appointment_id")
        self.assertEqual(matching_diag["appointment_id"], appt["appointment_id"])

        # B. Verify appointment transitioned to COMPLETED
        updated_appt = db.get_appointment_by_id(appt["appointment_id"])
        self.assertEqual(updated_appt["status"], "COMPLETED")

        # C. Verify patient_id matches the appointment patient
        self.assertEqual(matching_diag["patient_id"], patient["user_id"])
        self.assertEqual(matching_diag["doctor_id"], doc["user_id"])

    def test_29_def003_create_diagnosis_backward_compatibility(self):
        """29. DEF-003 F: Direct create_diagnosis() without appointment_id stores NULL safely."""
        patient = db.get_user_by_email("patient.demo@medtrack.local")
        doc = db.get_user_by_email("doctor.vance@medtrack.local")

        created = db.create_diagnosis({
            "patient_id": patient["user_id"],
            "doctor_id": doc["user_id"],
            "diagnosis": "Legacy format diagnosis entry without appointment reference",
            "date": datetime.date.today().isoformat()
        })

        self.assertIsNone(created.get("appointment_id"))

        # Verify from database that appointment_id is NULL
        diags = db.get_diagnoses_by_patient(patient["user_id"])
        legacy_diag = next((d for d in diags if d["diagnosis_id"] == created["diagnosis_id"]), None)
        self.assertIsNotNone(legacy_diag)
        self.assertIsNone(legacy_diag.get("appointment_id"))

    def test_30_def003_diagnosis_doctor_authorization_enforced(self):
        """30. DEF-003 D,G: Doctor cannot diagnose an appointment assigned to a different doctor."""
        patient = db.get_user_by_email("patient.demo@medtrack.local")
        other_doc = db.get_user_by_id("doc-002")  # Dr. Emily Chen

        # Appointment assigned to doc-002
        appt = db.create_appointment({
            "patient_id": patient["user_id"],
            "doctor_id": other_doc["user_id"],
            "appointment_date": datetime.date.today().isoformat(),
            "appointment_time": "02:00 PM",
            "reason": "Dermatology review",
            "status": "CONFIRMED"
        })

        # Log in as doc-001 (Dr. Marcus Vance)
        self.client.get("/demo-login/doctor")
        response = self.client.post(f"/doctor/diagnosis/new/{appt['appointment_id']}", data={
            "date": datetime.date.today().isoformat(),
            "diagnosis": "Attempted unauthorized diagnosis entry"
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Unauthorized: You are not the assigned physician", response.data)

        # Appointment remains CONFIRMED, not COMPLETED
        appt_after = db.get_appointment_by_id(appt["appointment_id"])
        self.assertEqual(appt_after["status"], "CONFIRMED")

        # No diagnosis created for this appointment
        diags = db.get_diagnoses_by_patient(patient["user_id"])
        unauthorized_diag = next((d for d in diags if d.get("appointment_id") == appt["appointment_id"]), None)
        self.assertIsNone(unauthorized_diag)

    def test_31_def004_completed_appointment_get_readonly(self):
        """31. DEF-004 A: GET on completed appointment displays existing diagnosis in read-only mode without form."""
        patient = db.get_user_by_email("patient.demo@medtrack.local")
        doc = db.get_user_by_email("doctor.vance@medtrack.local")

        appt = db.create_appointment({
            "patient_id": patient["user_id"],
            "doctor_id": doc["user_id"],
            "appointment_date": datetime.date.today().isoformat(),
            "appointment_time": "10:30 AM",
            "reason": "DEF-004 Readonly Check",
            "status": "CONFIRMED"
        })

        self.client.get("/demo-login/doctor")
        # Submit valid first diagnosis
        post_res = self.client.post(f"/doctor/diagnosis/new/{appt['appointment_id']}", data={
            "date": datetime.date.today().isoformat(),
            "diagnosis": "Initial clinical evaluation for DEF-004 read-only test."
        }, follow_redirects=True)
        self.assertEqual(post_res.status_code, 200)

        # Confirm appointment is COMPLETED
        appt_after = db.get_appointment_by_id(appt["appointment_id"])
        self.assertEqual(appt_after["status"], "COMPLETED")

        # GET on completed appointment
        get_res = self.client.get(f"/doctor/diagnosis/new/{appt['appointment_id']}")
        self.assertEqual(get_res.status_code, 200)

        # Assert existing diagnosis text is displayed
        self.assertIn(b"Initial clinical evaluation for DEF-004 read-only test.", get_res.data)
        # Assert read-only badge/heading is displayed
        self.assertIn(b"Clinical Diagnosis Record (Completed)", get_res.data)
        self.assertIn(b"Completed Visit", get_res.data)
        # Assert Return to Dashboard action is present
        self.assertIn(b"Return to Dashboard", get_res.data)

        # Assert submission form and button are NOT present
        self.assertNotIn(b"Finalize Diagnosis &amp; Complete Visit", get_res.data)
        self.assertNotIn(b"Finalize Diagnosis & Complete Visit", get_res.data)
        self.assertNotIn(b"<textarea", get_res.data)

    def test_32_def004_completed_appointment_duplicate_post_rejected(self):
        """32. DEF-004 B: Duplicate POST to completed appointment is rejected, counts unchanged, status unchanged."""
        patient = db.get_user_by_email("patient.demo@medtrack.local")
        doc = db.get_user_by_email("doctor.vance@medtrack.local")

        appt = db.create_appointment({
            "patient_id": patient["user_id"],
            "doctor_id": doc["user_id"],
            "appointment_date": datetime.date.today().isoformat(),
            "appointment_time": "11:30 AM",
            "reason": "DEF-004 Duplicate POST Check",
            "status": "CONFIRMED"
        })

        self.client.get("/demo-login/doctor")
        # Legitimate first diagnosis
        self.client.post(f"/doctor/diagnosis/new/{appt['appointment_id']}", data={
            "date": datetime.date.today().isoformat(),
            "diagnosis": "Original primary diagnosis record."
        }, follow_redirects=True)

        # Record counts before duplicate POST
        diags_before = len(db.get_diagnoses_by_patient(patient["user_id"]))
        notifs_before = len(db.get_notifications_by_patient(patient["user_id"]))

        # Attempt second diagnosis submission to same completed appointment
        dup_res = self.client.post(f"/doctor/diagnosis/new/{appt['appointment_id']}", data={
            "date": datetime.date.today().isoformat(),
            "diagnosis": "Illegitimate duplicate diagnosis entry."
        }, follow_redirects=True)

        self.assertEqual(dup_res.status_code, 200)
        self.assertIn(b"Clinical diagnosis has already been submitted", dup_res.data)

        # Assert diagnosis count is unchanged
        diags_after = len(db.get_diagnoses_by_patient(patient["user_id"]))
        self.assertEqual(diags_after, diags_before)

        # Assert notification count is unchanged
        notifs_after = len(db.get_notifications_by_patient(patient["user_id"]))
        self.assertEqual(notifs_after, notifs_before)

        # Assert appointment remains COMPLETED
        appt_final = db.get_appointment_by_id(appt["appointment_id"])
        self.assertEqual(appt_final["status"], "COMPLETED")

        # Assert original diagnosis text remains unchanged and single
        diags_for_appt = [d for d in db.get_diagnoses_by_patient(patient["user_id"]) if d.get("appointment_id") == appt["appointment_id"]]
        self.assertEqual(len(diags_for_appt), 1)
        self.assertEqual(diags_for_appt[0]["diagnosis"], "Original primary diagnosis record.")

    def test_33_def004_completed_appointment_safe_fallback_when_no_diagnosis_record(self):
        """33. DEF-004 Edge Case: Completed appointment without diagnosis record displays safe read-only message."""
        patient = db.get_user_by_email("patient.demo@medtrack.local")
        doc = db.get_user_by_email("doctor.vance@medtrack.local")

        # Create appointment directly in COMPLETED status without a diagnosis record
        appt = db.create_appointment({
            "patient_id": patient["user_id"],
            "doctor_id": doc["user_id"],
            "appointment_date": datetime.date.today().isoformat(),
            "appointment_time": "03:00 PM",
            "reason": "Legacy completed consultation without diagnosis",
            "status": "COMPLETED"
        })

        self.client.get("/demo-login/doctor")
        res = self.client.get(f"/doctor/diagnosis/new/{appt['appointment_id']}")
        self.assertEqual(res.status_code, 200)

        # Safe fallback message displayed
        self.assertIn(b"No written diagnosis text was recorded for this completed appointment.", res.data)
        # Entry form not rendered
        self.assertNotIn(b"Finalize Diagnosis &amp; Complete Visit", res.data)
        self.assertNotIn(b"<textarea", res.data)
        self.assertIn(b"Return to Dashboard", res.data)

if __name__ == "__main__":
    unittest.main()
