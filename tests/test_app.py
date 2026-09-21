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

if __name__ == "__main__":
    unittest.main()
