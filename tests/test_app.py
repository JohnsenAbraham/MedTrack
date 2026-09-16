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
        self.assertIn(b"Patient Health Center", response.data)


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

    def test_12_forgot_password_and_reset_workflow(self):
        """Verify password reset request, token validation, and password update."""
        email = f"reset_test_{int(datetime.datetime.now().timestamp())}@example.com"
        # Register user
        self.client.post("/register", data={
            "name": "Reset Test User",
            "email": email,
            "password": "OldPassword123",
            "confirm_password": "OldPassword123",
            "phone": "",
            "date_of_birth": "",
            "gender": "Male",
            "role": "patient"
        })

        # Request reset
        forgot_res = self.client.post("/forgot-password", data={"email": email})
        self.assertEqual(forgot_res.status_code, 200)
        self.assertIn(b"Password reset instructions", forgot_res.data)

        # Generate token using serializer
        from app import get_serializer
        s = get_serializer()
        token = s.dumps(email, salt="password-reset-salt")

        # Open reset page
        get_reset = self.client.get(f"/reset-password/{token}")
        self.assertEqual(get_reset.status_code, 200)
        self.assertIn(b"Set New Password", get_reset.data)

        # Submit new password
        post_reset = self.client.post(f"/reset-password/{token}", data={
            "password": "NewSecretPassword456",
            "confirm_password": "NewSecretPassword456"
        }, follow_redirects=True)
        self.assertEqual(post_reset.status_code, 200)
        self.assertIn(b"Your password has been reset successfully", post_reset.data)

        # Verify old password fails
        bad_login = self.client.post("/login", data={"email": email, "password": "OldPassword123"}, follow_redirects=True)
        self.assertIn(b"Invalid email address or password", bad_login.data)

        # Verify new password succeeds
        good_login = self.client.post("/login", data={"email": email, "password": "NewSecretPassword456"}, follow_redirects=True)
        self.assertIn(b"Welcome back", good_login.data)

    def test_13_google_authentication_workflow(self):
        """Verify Google authentication auto-provisions patient and creates session."""
        g_email = f"google_test_{int(datetime.datetime.now().timestamp())}@gmail.com"
        g_name = "Google User Test"

        response = self.client.post("/auth/google", data={
            "google_email": g_email,
            "google_name": g_name
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Signed in successfully with Google", response.data)
        self.assertIn(b"Dashboard", response.data)

        # Verify in database
        user = db.get_user_by_email(g_email)
        self.assertIsNotNone(user)
        self.assertEqual(user["name"], g_name)
        self.assertEqual(user["auth_provider"], "google")

    def test_14_evaluator_demo_login_personas(self):
        """Verify 1-click evaluator demo login switches cleanly between Doctor, Patient, and Admin."""
        # 1. Doctor Login
        doc_res = self.client.get("/demo-login/doctor", follow_redirects=True)
        self.assertEqual(doc_res.status_code, 200)
        self.assertIn(b"Dr. Sarah Jenkins", doc_res.data)
        self.assertIn(b"Consultation Queue", doc_res.data)

        # 2. Admin Login
        admin_res = self.client.get("/demo-login/admin", follow_redirects=True)
        self.assertEqual(admin_res.status_code, 200)
        self.assertIn(b"Hospital Operations", admin_res.data)
        self.assertIn(b"AWS Cloud", admin_res.data)

        # 3. Patient Login
        pat_res = self.client.get("/demo-login/patient", follow_redirects=True)
        self.assertEqual(pat_res.status_code, 200)
        self.assertIn(b"Jane Doe", pat_res.data)
        self.assertIn(b"Upcoming", pat_res.data)

    def test_15_role_based_access_control(self):
        """Verify non-doctors cannot access the doctor consultation queue."""
        # Sign in as regular patient
        self.client.get("/demo-login/patient")
        res = self.client.get("/doctor/queue", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Access restricted", res.data)

    def test_16_doctor_clinical_consultation_full_lifecycle(self):
        """Full clinical intake: vital signs, ICD-10 diagnosis, e-prescription generation, and status change."""
        # 1. Book an appointment as patient
        self.client.get("/demo-login/patient")
        patient_user = db.get_user_by_email("jane.doe@example.com")
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

        appt_res = self.client.post("/appointments/new", data={
            "doctor": "Dr. Sarah Jenkins, MD (Cardiology)",
            "department": "Cardiology",
            "date": tomorrow,
            "time": "10:30 AM",
            "reason": "Post-exertion chest tightness and palpitations"
        }, follow_redirects=True)
        self.assertEqual(appt_res.status_code, 200)

        appts = db.get_appointments_by_patient(patient_user["user_id"])
        target_appt = next(a for a in appts if a.get("date") == tomorrow)

        # 2. Doctor logs in and performs consultation
        self.client.get("/demo-login/doctor")
        consult_url = f"/doctor/consultation/{target_appt['appointment_id']}"
        consult_view = self.client.get(consult_url)
        self.assertEqual(consult_view.status_code, 200)
        self.assertIn(b"Objective Vital Signs Intake", consult_view.data)

        # 3. Submit Consultation with vitals and multi-drug prescription
        post_consult = self.client.post(consult_url, data={
            "blood_pressure": "128/84",
            "heart_rate": "78",
            "temperature": "98.7",
            "spo2": "99",
            "blood_sugar": "94",
            "symptoms": "Bilateral chest discomfort relieved by rest. Clear lungs on auscultation.",
            "icd10_code": "I10",
            "diagnosis": "Stage 1 Essential Hypertension with exertional palpitations.",
            "treatment_plan": "Rest, reduce caffeine, low sodium diet.",
            "follow_up_date": (datetime.date.today() + datetime.timedelta(days=30)).isoformat(),
            "med_name[]": ["Metoprolol Tartrate", "CoQ10"],
            "med_dosage[]": ["25 mg", "100 mg"],
            "med_frequency[]": ["Twice daily", "Once daily"],
            "med_duration[]": ["30 days", "60 days"],
            "med_notes[]": ["Take with morning meal", "Dietary supplement"]
        }, follow_redirects=True)

        self.assertEqual(post_consult.status_code, 200)
        self.assertIn(b"finalized successfully", post_consult.data)

        # 4. Verify Appointment status updated to Completed
        updated_appt = db.get_appointment_by_id(target_appt["appointment_id"])
        self.assertEqual(updated_appt["status"], "Completed")

        # 5. Verify Longitudinal Vitals recorded
        vitals = db.get_vitals_by_patient(patient_user["user_id"])
        self.assertGreaterEqual(len(vitals), 1)
        self.assertEqual(vitals[0]["blood_pressure"], "128/84")

        # 6. Verify Diagnosis and e-Prescriptions
        diags = db.get_diagnoses_by_patient(patient_user["user_id"])
        latest_diag = diags[0]
        self.assertEqual(latest_diag["icd10_code"], "I10")
        self.assertEqual(len(latest_diag["prescriptions"]), 2)
        self.assertEqual(latest_diag["prescriptions"][0]["medication"], "Metoprolol Tartrate")

    def test_17_patient_record_view_ehr(self):
        """Verify physician can view longitudinal patient electronic health record."""
        self.client.get("/demo-login/doctor")
        patient_user = db.get_user_by_email("jane.doe@example.com")
        res = self.client.get(f"/doctor/patient/{patient_user['user_id']}")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Longitudinal Vital Signs Progression", res.data)
        self.assertIn(b"Prescribed Medications", res.data)

    def test_18_admin_analytics_and_hipaa_audit_trail(self):
        """Verify hospital administration can review hospital metrics and HIPAA audit logs."""
        self.client.get("/demo-login/admin")
        res = self.client.get("/admin/analytics")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"AWS Cloud Infrastructure Live Telemetry", res.data)
        self.assertIn(b"HIPAA Security Rule", res.data)
        self.assertIn(b"Registered Patients", res.data)

    def test_19_deep_cloud_health_check_payload(self):
        """Verify deep health check returns structured JSON with component status."""
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("dynamodb", data["components"])
        self.assertIn("sns", data["components"])
        self.assertEqual(data["components"]["dynamodb"]["status"], "healthy")

    def test_20_signup_route_alias(self):
        """Verify /signup route aliases seamlessly to registration page."""
        res = self.client.get("/signup")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Clinician Registration", res.data)
        self.assertIn(b"Blood Group", res.data)

    def test_21_diagnostic_document_vault_flow(self):
        """Verify authenticated patient can access vault, upload a diagnostic PDF, and download it."""
        import io
        self.client.get("/demo-login/patient")
        
        # 1. View vault
        res = self.client.get("/reports")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Diagnostic Document & Pathology Vault", res.data)

        # 2. Upload valid mock PDF
        dummy_pdf = io.BytesIO(b"%PDF-1.4 mock content %%EOF")
        upload_data = {
            "title": "Complete Blood Count (CBC) Panel",
            "category": "Pathology / Lab",
            "notes": "Hemoglobin 14.2 g/dL, Platelets 260k/uL.",
            "report_file": (dummy_pdf, "Jane_Doe_CBC_Panel.pdf")
        }
        res_upload = self.client.post("/reports/upload", data=upload_data, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(res_upload.status_code, 200)
        self.assertIn(b"Complete Blood Count (CBC) Panel", res_upload.data)

        # 3. Retrieve uploaded report ID
        patient = db.get_user_by_email("jane.doe@example.com")
        reports = db.get_reports_by_patient(patient["user_id"])
        cbc_report = next((r for r in reports if r["title"] == "Complete Blood Count (CBC) Panel"), None)
        self.assertIsNotNone(cbc_report)

        # 4. Download file
        res_dl = self.client.get(f"/reports/{cbc_report['report_id']}/download")
        self.assertEqual(res_dl.status_code, 200)
        self.assertIn(b"%PDF-1.4 mock content %%EOF", res_dl.data)

    def test_22_diagnostic_vault_mime_validation(self):
        """Verify invalid file extensions are blocked by MIME & extension validation."""
        import io
        self.client.get("/demo-login/patient")
        bad_file = io.BytesIO(b"malicious script contents")
        upload_data = {
            "title": "Unauthorized Script",
            "category": "Other",
            "report_file": (bad_file, "payload.exe")
        }
        res = self.client.post("/reports/upload", data=upload_data, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Invalid file extension", res.data)

if __name__ == "__main__":
    unittest.main()


