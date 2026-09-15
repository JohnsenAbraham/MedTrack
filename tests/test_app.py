import os
import unittest
import datetime
from app import app, db, sns
from config import Config

class MedTrackTestCase(unittest.TestCase):
    """Automated integration and end-to-end tests for MedTrack healthcare application."""

    def setUp(self):
        # Configure app for testing
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.config["SECRET_KEY"] = "test-secret-key-123"
        self.client = app.test_client()

        # Generate unique test email to keep tests isolated
        self.test_email = f"patient_{int(datetime.datetime.now().timestamp())}@example.com"
        self.test_password = "SecurePassword123!"
        self.test_name = "Jane Doe"

    def test_01_syntax_and_config(self):
        """Verify configuration values and mock mode."""
        self.assertTrue(Config.MOCK_AWS)
        self.assertIsNotNone(Config.DYNAMODB_USERS_TABLE)
        self.assertIsNotNone(Config.SNS_TOPIC_ARN)

    def test_02_home_page(self):
        """Verify Home/Landing page renders with MedTrack branding and navigation."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"MedTrack", response.data)
        self.assertIn(b"Smart, Scalable Healthcare", response.data)
        self.assertIn(b"Mock AWS Mode", response.data)

    def test_03_health_check_endpoint(self):
        """Verify the health check endpoint returns 200 and healthy JSON."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        json_data = response.get_json()
        self.assertEqual(json_data.get("status"), "healthy")
        self.assertTrue(json_data.get("mock_aws"))

    def test_04_patient_registration_success(self):
        """Verify patient registration creates user and triggers mock welcome SNS."""
        response = self.client.post("/register", data={
            "name": self.test_name,
            "email": self.test_email,
            "password": self.test_password,
            "confirm_password": self.test_password,
            "phone": "+1 (555) 234-5678",
            "date_of_birth": "1995-06-15",
            "gender": "Female",
            "role": "patient"
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Account registered successfully", response.data)

        # Verify in database
        user = db.get_user_by_email(self.test_email)
        self.assertIsNotNone(user)
        self.assertEqual(user["name"], self.test_name)
        self.assertEqual(user["role"], "patient")
        self.assertNotEqual(user["password_hash"], self.test_password)  # Must be hashed

        # Verify welcome notification
        notifications = db.get_notifications_by_patient(user["user_id"])
        self.assertGreaterEqual(len(notifications), 1)
        self.assertIn("Welcome to MedTrack", notifications[0]["message"])

    def test_05_patient_registration_duplicate_email(self):
        """Verify duplicate email registration is rejected."""
        # First registration
        email = f"dup_{int(datetime.datetime.now().timestamp())}@example.com"
        self.client.post("/register", data={
            "name": "First User",
            "email": email,
            "password": "Password123",
            "confirm_password": "Password123",
            "phone": "",
            "date_of_birth": "",
            "gender": "Male",
            "role": "patient"
        })

        # Second registration with same email
        response = self.client.post("/register", data={
            "name": "Second User",
            "email": email,
            "password": "Password123",
            "confirm_password": "Password123",
            "phone": "",
            "date_of_birth": "",
            "gender": "Male",
            "role": "patient"
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"already exists", response.data)

    def test_06_login_invalid_credentials(self):
        """Verify login rejects invalid passwords."""
        response = self.client.post("/login", data={
            "email": self.test_email,
            "password": "WrongPassword!"
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Invalid email address or password", response.data)

    def test_07_login_and_logout_flow(self):
        """Verify login establishes session and logout clears session."""
        # Ensure user exists
        email = f"auth_{int(datetime.datetime.now().timestamp())}@example.com"
        self.client.post("/register", data={
            "name": "Auth Tester",
            "email": email,
            "password": "Password123",
            "confirm_password": "Password123",
            "phone": "",
            "date_of_birth": "",
            "gender": "Male",
            "role": "patient"
        })

        # Login
        response = self.client.post("/login", data={
            "email": email,
            "password": "Password123"
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Welcome back", response.data)
        self.assertIn(b"Auth Tester", response.data)

        # Logout
        logout_res = self.client.get("/logout", follow_redirects=True)
        self.assertEqual(logout_res.status_code, 200)
        self.assertIn(b"signed out safely", logout_res.data)

    def test_08_dashboard_access_control(self):
        """Verify unauthenticated dashboard access is redirected to login."""
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_09_appointment_workflow_and_sns(self):
        """Full appointment lifecycle: schedule, verify, list, cancel, and test SNS alerts."""
        email = f"appt_{int(datetime.datetime.now().timestamp())}@example.com"
        # Register & Login
        self.client.post("/register", data={
            "name": "Appointment Patient",
            "email": email,
            "password": "Password123",
            "confirm_password": "Password123",
            "phone": "555-111-2222",
            "date_of_birth": "1990-01-01",
            "gender": "Male",
            "role": "patient"
        })
        self.client.post("/login", data={"email": email, "password": "Password123"})

        user = db.get_user_by_email(email)
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

        # 1. Book Appointment
        book_res = self.client.post("/appointments/new", data={
            "doctor": "Dr. Sarah Jenkins (Cardiology)",
            "date": tomorrow,
            "time": "10:30 AM",
            "reason": "Routine cardiovascular consultation"
        }, follow_redirects=True)

        self.assertEqual(book_res.status_code, 200)
        self.assertIn(b"appointment has been scheduled successfully", book_res.data)

        # 2. Verify Appointment in database
        appts = db.get_appointments_by_patient(user["user_id"])
        self.assertEqual(len(appts), 1)
        appt = appts[0]
        self.assertEqual(appt["doctor"], "Dr. Sarah Jenkins (Cardiology)")
        self.assertEqual(appt["status"], "Confirmed")

        # 3. Verify Appointments List Page
        list_res = self.client.get("/appointments")
        self.assertEqual(list_res.status_code, 200)
        self.assertIn(b"Dr. Sarah Jenkins (Cardiology)", list_res.data)
        self.assertIn(b"Confirmed", list_res.data)

        # 4. Cancel Appointment
        cancel_res = self.client.post(f"/appointments/{appt['appointment_id']}/cancel", follow_redirects=True)
        self.assertEqual(cancel_res.status_code, 200)
        self.assertIn(b"Appointment has been cancelled successfully", cancel_res.data)

        # Verify status is Cancelled
        updated_appt = db.get_appointment_by_id(appt["appointment_id"])
        self.assertEqual(updated_appt["status"], "Cancelled")

        # Verify notifications include booking and cancellation
        notifications = db.get_notifications_by_patient(user["user_id"])
        messages_text = " ".join([n["message"] for n in notifications])
        self.assertIn("Appointment Confirmed", messages_text)
        self.assertIn("Appointment Cancelled", messages_text)

    def test_10_diagnosis_workflow_and_sns(self):
        """Full diagnosis lifecycle: create diagnosis record, view timeline, check SNS alerts."""
        email = f"diag_{int(datetime.datetime.now().timestamp())}@example.com"
        # Register & Login
        self.client.post("/register", data={
            "name": "Diagnosis Patient",
            "email": email,
            "password": "Password123",
            "confirm_password": "Password123",
            "phone": "555-333-4444",
            "date_of_birth": "1988-03-20",
            "gender": "Female",
            "role": "patient"
        })
        self.client.post("/login", data={"email": email, "password": "Password123"})

        user = db.get_user_by_email(email)
        today = datetime.date.today().isoformat()

        # 1. Record Diagnosis
        diag_res = self.client.post("/diagnoses/new", data={
            "patient_id": user["user_id"],
            "doctor": "Dr. Mark Thorne, MD",
            "date": today,
            "diagnosis": "Mild Migraine with aura. Prescribed Sumatriptan 50mg and rest."
        }, follow_redirects=True)

        self.assertEqual(diag_res.status_code, 200)
        self.assertIn(b"Clinical diagnosis record has been logged successfully", diag_res.data)

        # 2. Verify Diagnosis in database
        diagnoses = db.get_diagnoses_by_patient(user["user_id"])
        self.assertEqual(len(diagnoses), 1)
        self.assertEqual(diagnoses[0]["doctor"], "Dr. Mark Thorne, MD")
        self.assertIn("Mild Migraine with aura", diagnoses[0]["diagnosis"])

        # 3. Verify Diagnoses Page displays record
        view_res = self.client.get("/diagnoses")
        self.assertEqual(view_res.status_code, 200)
        self.assertIn(b"Dr. Mark Thorne, MD", view_res.data)
        self.assertIn(b"Sumatriptan 50mg", view_res.data)

        # 4. Verify SNS Notification dispatched
        notifications = db.get_notifications_by_patient(user["user_id"])
        messages_text = " ".join([n["message"] for n in notifications])
        self.assertIn("Medical Record Update", messages_text)

    def test_11_mock_sns_dispatch_direct(self):
        """Direct verification of SNSService in mock mode."""
        res = sns.publish_notification(
            patient_id="mock-patient-123",
            message="Test mock SNS dispatch notification.",
            subject="Test Alert"
        )
        self.assertEqual(res.get("Status"), "MOCK_DISPATCHED")
        self.assertTrue(res.get("MessageId").startswith("mock-sns-"))
        self.assertEqual(res.get("ResponseMetadata", {}).get("HTTPStatusCode"), 200)

if __name__ == "__main__":
    unittest.main()
