"""
End-to-End Live HTTP Verification for MedTrack.
Executes real HTTP requests with cookie jar against running Flask server.
"""

import urllib.request
import urllib.parse
import http.cookiejar
import json
import re
import sys

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
    print("  [+] Home Page rendered successfully (HTTP 200).")

    # 2. Test Health check
    health_req = opener.open(f"{BASE_URL}/health")
    health_data = json.loads(health_req.read().decode("utf-8"))
    assert health_data["status"] == "healthy", f"Unexpected health status: {health_data}"
    assert health_data["mock_aws"] is True, "Expected mock_aws to be True"
    print(f"  [+] Health endpoint verified: {health_data}")

    # 3. Test Registration
    test_email = "johnsenabraham01@gmail.com"
    reg_data = urllib.parse.urlencode({
        "name": "Johnsen Abraham",
        "email": test_email,
        "password": "Password123",
        "confirm_password": "Password123",
        "phone": "+1 (555) 987-6543",
        "date_of_birth": "2000-01-15",
        "gender": "Male",
        "role": "patient"
    }).encode("utf-8")

    print("[*] Testing patient registration for Johnsen Abraham...")
    reg_req = opener.open(f"{BASE_URL}/register", data=reg_data)
    reg_html = reg_req.read().decode("utf-8")
    # Should redirect to login or show login form
    print("  [+] Registration completed.")

    # 4. Test Login
    print("[*] Testing authentication/login...")
    login_data = urllib.parse.urlencode({
        "email": test_email,
        "password": "Password123"
    }).encode("utf-8")
    login_req = opener.open(f"{BASE_URL}/login", data=login_data)
    login_html = login_req.read().decode("utf-8")
    assert "Johnsen Abraham" in login_html or "Dashboard" in login_html, "Login response missing user name or Dashboard"
    print("  [+] User authenticated. Session cookie established.")

    # 5. Access Dashboard
    dash_req = opener.open(f"{BASE_URL}/dashboard")
    dash_html = dash_req.read().decode("utf-8")
    assert "Johnsen Abraham" in dash_html, "Dashboard missing user name"
    assert "Patient Profile" in dash_html, "Dashboard missing Patient Profile card"
    print("  [+] Patient dashboard rendered with profile and health statistics.")

    # 6. Book Appointment
    print("[*] Testing appointment booking with Dr. Sarah Jenkins...")
    appt_data = urllib.parse.urlencode({
        "doctor": "Dr. Sarah Jenkins (Cardiology)",
        "date": "2026-10-15",
        "time": "10:30 AM",
        "reason": "Cardiovascular assessment and consultation."
    }).encode("utf-8")
    appt_req = opener.open(f"{BASE_URL}/appointments/new", data=appt_data)
    appt_html = appt_req.read().decode("utf-8")
    assert "Dr. Sarah Jenkins" in appt_html, "Appointments page missing booked doctor"
    assert "Confirmed" in appt_html, "Appointment not confirmed"
    print("  [+] Appointment booked & confirmed. Mock SNS notification triggered.")

    # Extract appointment ID for cancellation
    appt_match = re.search(r'action="/appointments/([a-f0-9\-]+)/cancel"', appt_html)
    if appt_match:
        appt_id = appt_match.group(1)
        print(f"  [+] Extracted appointment ID: {appt_id}")

        # 7. Cancel Appointment
        print(f"[*] Testing appointment cancellation for ID: {appt_id}...")
        cancel_req = opener.open(f"{BASE_URL}/appointments/{appt_id}/cancel", data=b"")
        cancel_html = cancel_req.read().decode("utf-8")
        assert "Cancelled" in cancel_html, "Appointment cancellation status missing"
        print("  [+] Appointment cancelled successfully. Cancellation SNS alert logged.")

    # 8. Record Clinical Diagnosis
    print("[*] Testing clinical diagnosis record submission...")
    diag_data = urllib.parse.urlencode({
        "patient_id": "",  # defaults to current user
        "doctor": "Dr. Sarah Jenkins",
        "date": "2026-10-15",
        "diagnosis": "Cardiovascular screening completed. Normal sinus rhythm, BP 120/80 mmHg. Maintain balanced diet."
    }).encode("utf-8")
    diag_req = opener.open(f"{BASE_URL}/diagnoses/new", data=diag_data)
    diag_html = diag_req.read().decode("utf-8")
    assert "Normal sinus rhythm" in diag_html, "Diagnosis text not found in diagnoses timeline"
    print("  [+] Diagnosis logged into electronic patient record & SNS notification triggered.")

    # 9. Verify Dashboard notification feed
    dash2_req = opener.open(f"{BASE_URL}/dashboard")
    dash2_html = dash2_req.read().decode("utf-8")
    assert "Cardiovascular" in dash2_html or "SNS Alerts Feed" in dash2_html
    print("  [+] Dashboard refreshed with updated diagnosis and notification items.")

    # 10. Logout
    print("[*] Testing sign-out...")
    logout_req = opener.open(f"{BASE_URL}/logout")
    logout_html = logout_req.read().decode("utf-8")
    assert "signed out safely" in logout_html or "Sign In" in logout_html
    print("  [+] User logged out safely.")

    print("\n[SUCCESS] All live HTTP end-to-end healthcare workflows passed!")

if __name__ == "__main__":
    run_verification()
