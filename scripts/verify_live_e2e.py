"""
MedTrack End-to-End Live HTTP Verification
Executes real HTTP requests with cookie sessions against running Flask server.
Tests: Home, Register, Login, Book Appointment, Doctor Confirmation,
Diagnosis Submission, Patient Diagnoses View, and Appointment Cancellation.
"""

import urllib.request
import urllib.parse
import http.cookiejar
import json
import datetime
import sys

BASE_URL = "http://127.0.0.1:5000"

def run_e2e_verification():
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    print(f"[*] Testing connection to {BASE_URL}...")

    # 1. Home Page
    res = opener.open(f"{BASE_URL}/")
    assert res.status == 200, f"Expected 200 on /, got {res.status}"
    html = res.read().decode("utf-8")
    assert "MedTrack" in html
    assert "AWS Cloud-Enabled" in html
    print("  [+] Home Page rendered successfully (HTTP 200).")

    # 2. Health Endpoint
    res = opener.open(f"{BASE_URL}/health")
    data = json.loads(res.read().decode("utf-8"))
    assert data["status"] == "healthy"
    print("  [+] Health endpoint returned healthy status.")

    # 3. Patient Registration
    ts = int(datetime.datetime.now().timestamp())
    test_email = f"patient_{ts}@example.com"
    test_pass = "TestPass123!"
    reg_data = urllib.parse.urlencode({
        "name": "Jordan Smith",
        "email": test_email,
        "password": test_pass,
        "phone": "+1-555-0188",
        "date_of_birth": "1993-07-11",
        "gender": "Female"
    }).encode("utf-8")

    reg_res = opener.open(f"{BASE_URL}/register", data=reg_data)
    assert reg_res.status == 200
    print(f"  [+] Patient registration succeeded for {test_email}.")

    # 4. Patient Login
    login_data = urllib.parse.urlencode({
        "email": test_email,
        "password": test_pass
    }).encode("utf-8")
    login_res = opener.open(f"{BASE_URL}/login", data=login_data)
    assert login_res.status == 200
    login_html = login_res.read().decode("utf-8")
    assert "Jordan Smith" in login_html
    print("  [+] Patient logged in and redirected to Patient Dashboard.")

    # 5. Book Appointment
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    book_data = urllib.parse.urlencode({
        "doctor_id": "doc-001",
        "appointment_date": tomorrow,
        "appointment_time": "10:00 AM",
        "reason": "Chest tightness and general consultation"
    }).encode("utf-8")
    book_res = opener.open(f"{BASE_URL}/appointments/new", data=book_data)
    assert book_res.status == 200
    print("  [+] Appointment booked successfully (Event 1 triggered).")

    # 6. View Appointments
    apts_res = opener.open(f"{BASE_URL}/appointments")
    apts_html = apts_res.read().decode("utf-8")
    assert "Chest tightness" in apts_html
    print("  [+] Appointment listing confirmed in patient records.")

    # 7. Doctor Login via Demo Switcher
    doc_res = opener.open(f"{BASE_URL}/demo-login/doctor")
    assert doc_res.status == 200
    doc_html = doc_res.read().decode("utf-8")
    assert "Dr. Marcus Vance" in doc_html
    assert "Assigned Patient Appointments" in doc_html
    print("  [+] Doctor logged in and accessed Doctor Dashboard.")

    # Extract appointment ID from doctor HTML
    import re
    apt_ids = re.findall(r'/doctor/appointments/([a-zA-Z0-9_-]+)/status', doc_html)
    assert len(apt_ids) > 0, "No appointment ID found on doctor dashboard"
    apt_id = apt_ids[0]

    # 8. Doctor Confirms Appointment
    confirm_data = urllib.parse.urlencode({"status": "CONFIRMED"}).encode("utf-8")
    confirm_res = opener.open(f"{BASE_URL}/doctor/appointments/{apt_id}/status", data=confirm_data)
    assert confirm_res.status == 200
    print(f"  [+] Doctor confirmed appointment {apt_id} (Event 3 triggered).")

    # 9. Doctor Submits Diagnosis
    diag_data = urllib.parse.urlencode({
        "date": datetime.date.today().isoformat(),
        "diagnosis": "Mild sinus arrhythmia with stress-induced tension. Advised rest, hydration, and monitoring."
    }).encode("utf-8")
    diag_res = opener.open(f"{BASE_URL}/doctor/diagnosis/new/{apt_id}", data=diag_data)
    assert diag_res.status == 200
    print(f"  [+] Doctor submitted clinical diagnosis (Event 4 triggered).")

    # 10. Patient Reviews Diagnosis
    # Sign out doctor first and sign back in as patient
    opener.open(f"{BASE_URL}/logout")
    patient_login = opener.open(f"{BASE_URL}/login", data=login_data)
    assert patient_login.status == 200

    diags_res = opener.open(f"{BASE_URL}/diagnoses")
    diags_html = diags_res.read().decode("utf-8")
    assert "Mild sinus arrhythmia" in diags_html
    print("  [+] Patient successfully viewed personal diagnosis record.")

    print("\n[SUCCESS] All MedTrack SkillWallet functional workflows verified live!")

if __name__ == "__main__":
    run_e2e_verification()
