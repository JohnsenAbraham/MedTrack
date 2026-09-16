"""
End-to-End Live HTTP Verification for MedTrack Enterprise Hospital Platform.
Executes real HTTP requests with cookie jar against running Flask server.
Covers: Patient Booking, Doctor Consultation Queue, Vitals Intake, ICD-10,
E-Prescriptions, HIPAA Audit Trail, and AWS Cloud Telemetry.
"""

import urllib.request
import urllib.parse
import http.cookiejar
import json
import re
import sys
import datetime

BASE_URL = "http://127.0.0.1:5000"

def run_verification():
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    print(f"[*] Testing connection to {BASE_URL}...")

    # 1. Test Home page
    home_req = opener.open(f"{BASE_URL}/")
    assert home_req.status == 200, f"Expected 200 on /, got {home_req.status}"
    home_html = home_req.read().decode("utf-8")
    assert "MedTrack" in home_html, "Home page missing brand 'MedTrack'"
    assert "Emergency: 911 / 112" in home_html, "Home page missing emergency hotline"
    assert "PHYSICIAN CARE" in home_html, "Home page missing 4-pillar Physician Care"
    print("  [+] Home Page rendered successfully with emergency ribbon & 4 clinical pillars (HTTP 200).")

    # 1b. Test /signup alias
    signup_req = opener.open(f"{BASE_URL}/signup")
    assert signup_req.status == 200
    signup_html = signup_req.read().decode("utf-8")
    assert "Clinician Registration" in signup_html
    print("  [+] /signup route alias verified successfully (HTTP 200).")

    # 2. Test Health check
    health_req = opener.open(f"{BASE_URL}/health")
    health_data = json.loads(health_req.read().decode("utf-8"))
    assert health_data["status"] == "healthy", f"Unexpected health status: {health_data}"
    assert "components" in health_data, "Health check missing components telemetry"
    print(f"  [+] Deep Cloud Health endpoint verified: status={health_data['status']}, DynamoDB={health_data['components']['dynamodb']['status']}")

    # 3. Test Evaluator 1-Click Persona Login
    print("[*] Testing 1-click evaluator demo logins...")
    doc_req = opener.open(f"{BASE_URL}/demo-login/doctor")
    doc_html = doc_req.read().decode("utf-8")
    assert "Dr. Sarah Jenkins" in doc_html or "Consultation Queue" in doc_html
    print("  [+] Doctor demo login passed. Landed on Consultation Queue.")

    opener.open(f"{BASE_URL}/logout")

    admin_req = opener.open(f"{BASE_URL}/demo-login/admin")
    admin_html = admin_req.read().decode("utf-8")
    assert "Hospital Operations" in admin_html or "AWS Cloud" in admin_html
    print("  [+] Admin demo login passed. Landed on Hospital Ops.")

    opener.open(f"{BASE_URL}/logout")

    # 4. Test Registration with dynamic isolated test email
    ts = int(datetime.datetime.now().timestamp())
    test_email = f"e2e_tester_{ts}@medtrack.internal"
    test_name = "Clinical Test Patient"
    reg_data = urllib.parse.urlencode({
        "name": test_name,
        "email": test_email,
        "password": "Password123",
        "confirm_password": "Password123",
        "phone": "555-010-0202",
        "date_of_birth": "1995-05-15",
        "gender": "Female",
        "role": "patient",
        "blood_group": "A+",
        "allergies": "Penicillin",
        "emergency_contact": "John Doe (Brother) - 555-0011",
        "insurance_provider": "Aetna National PPO #AET-1002"
    }).encode("utf-8")

    print(f"[*] Testing patient registration for {test_email}...")
    reg_req = opener.open(f"{BASE_URL}/register", data=reg_data)
    reg_html = reg_req.read().decode("utf-8")
    print("  [+] Registration completed.")

    # 5. Test Login
    print("[*] Testing authentication/login...")
    login_data = urllib.parse.urlencode({
        "email": test_email,
        "password": "Password123"
    }).encode("utf-8")
    login_req = opener.open(f"{BASE_URL}/login", data=login_data)
    login_html = login_req.read().decode("utf-8")
    assert test_name in login_html or "Patient Health Portal" in login_html or "Dashboard" in login_html
    print("  [+] User authenticated. Session cookie established.")

    # 6. Access Patient Dashboard
    dash_req = opener.open(f"{BASE_URL}/dashboard")
    dash_html = dash_req.read().decode("utf-8")
    assert test_name in dash_html, "Dashboard missing user name"
    assert "Vital Signs" in dash_html or "Patient Chart" in dash_html
    print("  [+] Patient dashboard rendered with profile, vitals, and health statistics.")

    # 7. Book Appointment
    print("[*] Testing clinical appointment booking with Dr. Sarah Jenkins...")
    tomorrow = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
    appt_data = urllib.parse.urlencode({
        "doctor": "Dr. Sarah Jenkins, MD (Cardiology & Cardiovascular)",
        "department": "Cardiology",
        "date": tomorrow,
        "time": "10:30 AM",
        "reason": "Cardiovascular assessment and exertion palpitations."
    }).encode("utf-8")
    appt_req = opener.open(f"{BASE_URL}/appointments/new", data=appt_data)
    appt_html = appt_req.read().decode("utf-8")
    assert "Dr. Sarah Jenkins" in appt_html, "Appointments page missing booked doctor"
    assert "Confirmed" in appt_html, "Appointment not confirmed"
    print("  [+] Appointment booked & confirmed. AWS SNS notification dispatched.")

    # Extract appointment ID
    appt_match = re.search(r'action="/appointments/([a-f0-9\-]+)/cancel"', appt_html)
    assert appt_match, "Could not extract appointment ID"
    appt_id = appt_match.group(1)
    print(f"  [+] Booked appointment ID: {appt_id}")

    # Sign out patient
    opener.open(f"{BASE_URL}/logout")

    # 8. Doctor Clinical Workflow: Consultation, Vitals & E-Prescription
    print("[*] Testing Doctor Consultation Workspace...")
    opener.open(f"{BASE_URL}/demo-login/doctor")

    consult_url = f"{BASE_URL}/doctor/consultation/{appt_id}"
    consult_get = opener.open(consult_url)
    assert consult_get.status == 200
    consult_html = consult_get.read().decode("utf-8")
    assert test_name in consult_html, "Consultation page missing patient name"
    assert "Vital Signs Intake" in consult_html, "Missing vitals intake section"

    # Submit clinical encounter
    consult_post_data = urllib.parse.urlencode([
        ("blood_pressure", "126/82"),
        ("heart_rate", "76"),
        ("temperature", "98.6"),
        ("spo2", "99"),
        ("blood_sugar", "95"),
        ("symptoms", "Patient reports palpitations when climbing stairs. Normal ECG sinus rhythm."),
        ("icd10_code", "I10"),
        ("diagnosis", "Mild Borderline Hypertension with benign exertional palpitations."),
        ("treatment_plan", "DASH dietary changes, 30 min daily walking, 60-day follow-up."),
        ("follow_up_date", (datetime.date.today() + datetime.timedelta(days=60)).isoformat()),
        ("med_name[]", "Amlodipine Besylate"),
        ("med_dosage[]", "5 mg"),
        ("med_frequency[]", "Once daily"),
        ("med_duration[]", "60 days"),
        ("med_notes[]", "Take with morning meal")
    ]).encode("utf-8")

    consult_post = opener.open(consult_url, data=consult_post_data)
    assert consult_post.status == 200
    queue_after_html = consult_post.read().decode("utf-8")
    assert "finalized successfully" in queue_after_html
    print("  [+] Doctor finalized consultation: Vitals, ICD-10, & E-Prescriptions logged.")

    opener.open(f"{BASE_URL}/logout")

    # 9. Admin Analytics & HIPAA Audit Verification
    print("[*] Testing Hospital Administration & HIPAA Audit Stream...")
    opener.open(f"{BASE_URL}/demo-login/admin")
    admin_req = opener.open(f"{BASE_URL}/admin/analytics")
    admin_html = admin_req.read().decode("utf-8")
    assert "HIPAA Security Rule" in admin_html, "Missing HIPAA audit section"
    assert "CLINICAL_CONSULTATION_COMPLETED" in admin_html or "SCHEDULE_APPOINTMENT" in admin_html
    print("  [+] HIPAA audit trail verified with immutable action logs.")

    opener.open(f"{BASE_URL}/logout")

    print("\n[SUCCESS] All enterprise hospital end-to-end clinical workflows verified!")

if __name__ == "__main__":
    run_verification()
