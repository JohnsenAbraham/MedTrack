"""
MedTrack – AWS Cloud-Enabled Healthcare Management System
Student demonstration web application for AWS Cloud Practitioner / SkillWallet.
"""

import os
import uuid
import datetime
from zoneinfo import ZoneInfo
import functools
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, g, jsonify, abort, make_response, send_file
)
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.security import generate_password_hash, check_password_hash
from config import Config
from services import DatabaseService, SNSService, rate_limiter, storage_service

def get_current_date() -> str:
    """Resolve current business date (YYYY-MM-DD) in Config.APP_TIMEZONE (default Asia/Kolkata)."""
    tz_name = getattr(Config, "APP_TIMEZONE", "Asia/Kolkata")
    return datetime.datetime.now(ZoneInfo(tz_name)).date().isoformat()

app = Flask(__name__)
app.config.from_object(Config)

# Session Security Configuration
app.config.update(
    SESSION_COOKIE_NAME=Config.SESSION_COOKIE_NAME,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=Config.SESSION_COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=Config.PERMANENT_SESSION_LIFETIME
)

# Reverse Proxy & HTTPS Configuration (ProxyFix)
# Security Model:
# 1. Nginx is the sole reverse proxy facing incoming traffic and overwrites X-Forwarded-* headers.
# 2. Gunicorn is bound exclusively to loopback (127.0.0.1:8000), preventing direct external access.
# 3. ProxyFix is bounded to exactly 1 proxy hop (x_for=1, x_proto=1, x_host=1) to trust Nginx on localhost.
if Config.USE_PROXY_FIX:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# CSRF Protection
csrf = CSRFProtect(app)

# Initialize Services
db = DatabaseService()
sns = SNSService()

# -----------------------------------------------------------------
# Authentication & Authorization Helpers
# -----------------------------------------------------------------
@app.before_request
def enforce_session_inactivity():
    """Load logged-in user from session and enforce 60-minute active inactivity timeout."""
    g.user = None
    user_id = session.get("user_id")
    if user_id is not None:
        last_activity = session.get("last_activity")
        now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        if last_activity and (now - last_activity > Config.SESSION_INACTIVITY_TIMEOUT):
            session.clear()
            flash("Your session has expired due to 60 minutes of inactivity. Please sign in again.", "warning")
            return redirect(url_for("login"))
        session["last_activity"] = now
        g.user = db.get_user_by_id(user_id)
        if not g.user:
            session.clear()

@app.context_processor
def inject_user_context():
    """Make user authentication state and demo mode flag available in all Jinja templates."""
    notif_count = 0
    user = getattr(g, "user", None)
    if user:
        try:
            if user.get("role") == "patient":
                notif_count = len(db.get_notifications_by_patient(user["user_id"]))
            elif user.get("role") == "doctor":
                notif_count = len(db.get_notifications_by_doctor(user["user_id"]))
        except Exception:
            notif_count = 0

    return {
        "current_user": user,
        "is_authenticated": user is not None,
        "user_role": user.get("role") if user else None,
        "unread_notifications_count": notif_count,
        "mock_aws": Config.MOCK_AWS,
        "demo_mode": app.config.get("DEMO_MODE", Config.DEMO_MODE),
    }

def login_required(view):
    """Ensure user is logged in."""
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            flash("Please sign in to access this page.", "warning")
            return redirect(url_for("login"))
        return view(**kwargs)
    return wrapped_view

def patient_required(view):
    """Ensure user is logged in with patient role."""
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            flash("Please sign in as a patient.", "warning")
            return redirect(url_for("login"))
        if g.user.get("role") != "patient":
            flash("Access restricted: This section is for registered patients only.", "danger")
            return redirect(url_for("doctor_dashboard"))
        return view(**kwargs)
    return wrapped_view

def doctor_required(view):
    """Ensure user is logged in with doctor role."""
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            flash("Please sign in with physician credentials.", "warning")
            return redirect(url_for("login"))
        if g.user.get("role") != "doctor":
            flash("Access restricted: This workspace is for attending doctors only.", "danger")
            return redirect(url_for("dashboard"))
        return view(**kwargs)
    return wrapped_view

# -----------------------------------------------------------------
# Public Routes (Home, Health)
# -----------------------------------------------------------------
@app.route("/")
def index():
    """Home landing page with MedTrack system overview."""
    return render_template("index.html")

@app.route("/health")
def health():
    """Health check endpoint for system telemetry."""
    return jsonify({
        "status": "healthy",
        "mock_aws": Config.MOCK_AWS,
        "region": Config.AWS_REGION,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }), 200

# -----------------------------------------------------------------
# Authentication Routes (Register, Login, Logout, Evaluator Demo)
# -----------------------------------------------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    """Patient registration with input and duplicate email validation."""
    if g.user:
        return redirect(url_for("dashboard") if g.user.get("role") == "patient" else url_for("doctor_dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        phone = request.form.get("phone", "").strip()
        date_of_birth = request.form.get("date_of_birth", "").strip()
        gender = request.form.get("gender", "").strip()

        # Input Validation
        if not name or not email or not password or not phone or not date_of_birth or not gender:
            flash("All registration fields are required.", "danger")
            return render_template("register.html", form=request.form)

        if "@" not in email or "." not in email:
            flash("Please enter a valid email address.", "danger")
            return render_template("register.html", form=request.form)

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "danger")
            return render_template("register.html", form=request.form)

        # Prevent duplicate email registration
        existing_user = db.get_user_by_email(email)
        if existing_user:
            flash("An account with this email address already exists. Please sign in.", "warning")
            return redirect(url_for("login"))

        password_hash = generate_password_hash(password)
        new_user = {
            "name": name,
            "email": email,
            "password_hash": password_hash,
            "phone": phone,
            "date_of_birth": date_of_birth,
            "gender": gender,
            "role": "patient",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }

        created = db.create_user(new_user)
        db.create_notification(
            patient_id=created["user_id"],
            message=f"Welcome to MedTrack, {name}! Your patient portal account has been created."
        )

        flash("Registration successful! You can now sign in with your credentials.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", form={})

@app.route("/login", methods=["GET", "POST"])
def login():
    """User authentication with dual-key rate limiting, brute-force defense, and session fixation protection."""
    if g.user:
        return redirect(url_for("dashboard") if g.user.get("role") == "patient" else url_for("doctor_dashboard"))

    if request.method == "POST":
        client_ip = request.headers.get("X-Forwarded-For", request.remote_addr).split(",")[0].strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Please enter both email and password.", "danger")
            return render_template("login.html", form=request.form)

        # 1. Rate limiter evaluation before checking credentials
        is_allowed, remaining, retry_after, blocked_by = rate_limiter.is_allowed(client_ip, email)
        if not is_allowed:
            flash(f"Too many failed login attempts. Access is temporarily restricted. Try again in {retry_after} seconds.", "danger")
            resp = make_response(render_template(
                "error.html",
                error_title="Rate Limit Exceeded",
                error_code=429,
                error_message=f"Too many failed login attempts. Access is temporarily restricted. Please retry in {retry_after} seconds.",
                back_url=url_for("login"),
                retry_after=retry_after
            ), 429)
            resp.headers["Retry-After"] = str(retry_after)
            return resp

        user = db.get_user_by_email(email)
        if not user or not check_password_hash(user["password_hash"], password):
            rate_limiter.record_failure(client_ip, email)
            flash("Invalid email address or password.", "danger")
            return render_template("login.html", form=request.form)

        # 2. Reset failed attempts for the specific account on success (IP bucket preserved)
        rate_limiter.record_success(email)

        # 3. Session Fixation Defense: purge pre-authentication state and generate fresh signed session
        session.clear()
        session["_auth_token"] = str(uuid.uuid4())
        session["user_id"] = user["user_id"]
        session["user_name"] = user["name"]
        session["user_role"] = user["role"]
        session["last_activity"] = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

        flash(f"Welcome back, {user['name']}!", "success")
        if user["role"] == "doctor":
            return redirect(url_for("doctor_dashboard"))
        return redirect(url_for("dashboard"))

    return render_template("login.html", form={})

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    """Terminate user session safely via POST with CSRF protection."""
    session.clear()
    flash("You have been signed out safely.", "info")
    return redirect(url_for("login"))

@app.route("/logout", methods=["GET"])
def logout_get():
    """
    Strictly non-mutating GET /logout endpoint.
    Prevents Logout CSRF via <img>/<script> tags by requiring an explicit POST form submission.
    """
    if g.user:
        flash("To sign out securely, please use the Sign Out button.", "info")
        if g.user.get("role") == "doctor":
            return redirect(url_for("doctor_dashboard"))
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/demo-login/<role>")
def demo_login(role):
    """
    Convenience 1-click evaluator login for local testing environments.
    Strictly disabled and returns 403 Forbidden unless DEMO_MODE=True.
    """
    if not app.config.get("DEMO_MODE", Config.DEMO_MODE):
        abort(403)

    if role == "doctor":
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        if doc:
            session.clear()
            session["_auth_token"] = str(uuid.uuid4())
            session["user_id"] = doc["user_id"]
            session["user_name"] = doc["name"]
            session["user_role"] = "doctor"
            session["last_activity"] = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            flash(f"Logged in as Demo Doctor ({doc['name']}).", "success")
            return redirect(url_for("doctor_dashboard"))

    elif role == "patient":
        demo_email = "patient.demo@medtrack.local"
        patient = db.get_user_by_email(demo_email)
        if not patient:
            patient = db.create_user({
                "name": "Alex Taylor",
                "email": demo_email,
                "password_hash": generate_password_hash("PatientPass123!"),
                "phone": "+1-555-0155",
                "date_of_birth": "1992-08-15",
                "gender": "Other",
                "role": "patient",
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            })

        # Ensure initial demo medicines exist for live evaluation
        meds = db.get_medicines_by_patient(patient["user_id"])
        if not meds:
            db.create_medicine(patient["user_id"], "Paracetamol 500mg", "1 Tablet (500mg)", "08:00 AM", "Daily", "After Food", "Take with breakfast")
            db.create_medicine(patient["user_id"], "Vitamin D3 1000 IU", "1 Softgel", "01:00 PM", "Daily", "With Food", "Fat-soluble vitamin with lunch")
            db.create_medicine(patient["user_id"], "Amoxicillin 250mg", "1 Capsule (250mg)", "08:00 PM", "Daily", "After Food", "Complete antibiotic course")

        session.clear()
        session["_auth_token"] = str(uuid.uuid4())
        session["user_id"] = patient["user_id"]
        session["user_name"] = patient["name"]
        session["user_role"] = "patient"
        session["last_activity"] = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        flash(f"Logged in as Demo Patient ({patient['name']}).", "success")
        return redirect(url_for("dashboard"))

    flash("Requested demo profile not found.", "warning")
    return redirect(url_for("login"))

# -----------------------------------------------------------------
# Patient Routes
# -----------------------------------------------------------------
@app.route("/dashboard")
@patient_required
def dashboard():
    """Patient dashboard showing overview metrics, schedule, and recent records."""
    patient_id = g.user["user_id"]
    all_appointments = db.get_appointments_by_patient(patient_id)
    diagnoses = db.get_diagnoses_by_patient(patient_id)
    notifications = db.get_notifications_by_patient(patient_id)

    today = get_current_date()
    upcoming = [a for a in all_appointments if a.get("appointment_date", "") >= today and a.get("status") != "CANCELLED"]
    pending = [a for a in all_appointments if a.get("status") == "PENDING"]
    completed = [a for a in all_appointments if a.get("status") == "COMPLETED"]
    past = [a for a in all_appointments if a.get("appointment_date", "") < today or a.get("status") in ("COMPLETED", "CANCELLED")]
    medicine_schedule = db.get_patient_schedule(patient_id)

    return render_template(
        "dashboard.html",
        patient=g.user,
        all_appointments=all_appointments,
        upcoming_appointments=upcoming,
        pending_appointments=pending,
        completed_appointments=completed,
        past_appointments=past,
        diagnoses=diagnoses,
        notifications=notifications,
        medicine_schedule=medicine_schedule
    )

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """Unified user profile and contact information settings."""
    if request.method == "POST":
        name = request.form.get("name", "").strip() or g.user["name"]
        phone = request.form.get("phone", "").strip()
        date_of_birth = request.form.get("date_of_birth", "").strip()
        gender = request.form.get("gender", "").strip()

        db.update_user(g.user["user_id"], {
            "name": name,
            "phone": phone,
            "date_of_birth": date_of_birth,
            "gender": gender
        })
        g.user = db.get_user_by_id(g.user["user_id"])
        flash("Profile information updated successfully.", "success")
        return redirect(url_for("profile"))

    if g.user.get("role") == "doctor":
        appointments = db.get_appointments_by_doctor(g.user["user_id"])
        diagnoses = db.get_diagnoses_by_doctor(g.user["user_id"])
        return render_template("doctor_profile.html", doctor=g.user, appointments=appointments, diagnoses=diagnoses)

    return render_template("profile.html", patient=g.user)

@app.route("/schedule")
@app.route("/patient/schedule")
@patient_required
def patient_schedule():
    """Full calendar view of medication doses and consultation schedule."""
    patient_id = g.user["user_id"]
    all_appointments = db.get_appointments_by_patient(patient_id)
    medicine_schedule = db.get_patient_schedule(patient_id)
    medicines_list = db.get_medicines_by_patient(patient_id)
    return render_template(
        "patient_schedule.html",
        patient=g.user,
        appointments=all_appointments,
        medicine_schedule=medicine_schedule,
        medicines=medicines_list
    )

@app.route("/notifications")
@patient_required
def view_notifications():
    """View patient clinical and dose notifications."""
    patient_id = g.user["user_id"]
    notifications = db.get_notifications_by_patient(patient_id)
    return render_template("notifications.html", notifications=notifications)

@app.route("/appointments")
@patient_required
def view_appointments():
    """List all appointments booked by the patient."""
    patient_id = g.user["user_id"]
    appointments = db.get_appointments_by_patient(patient_id)
    return render_template("appointments.html", appointments=appointments)

@app.route("/search-doctors")
@app.route("/doctors")
@patient_required
def search_doctors():
    """Browse and search clinical physicians and specialists."""
    query = request.args.get("q", "").strip()
    specialty = request.args.get("specialty", "").strip()

    all_doctors = db.get_doctors()
    filtered = []
    for doc in all_doctors:
        name = doc.get("name", "")
        # Extract specialty from name if in format 'Dr. Name (Specialty)' or default
        doc_specialty = "General Physician"
        if "(" in name and ")" in name:
            doc_specialty = name.split("(")[1].split(")")[0]
        doc["specialty"] = doc_specialty

        match_query = (not query) or (query.lower() in name.lower()) or (query.lower() in doc.get("email", "").lower()) or (query.lower() in doc_specialty.lower())
        match_specialty = (not specialty) or (specialty.lower() in doc_specialty.lower())

        if match_query and match_specialty:
            filtered.append(doc)

    return render_template("doctors_search.html", doctors=filtered, query=query, specialty=specialty)

@app.route("/appointments/new", methods=["GET", "POST"])
@patient_required
def book_appointment():
    """Book a new medical consultation with an attending doctor."""
    doctors = db.get_doctors()
    preselected_doctor = request.args.get("doctor_id", "").strip()

    if request.method == "POST":
        doctor_id = request.form.get("doctor_id", "").strip()
        appointment_date = request.form.get("appointment_date", "").strip()
        appointment_time = request.form.get("appointment_time", "").strip()
        reason = request.form.get("reason", "").strip()

        if not doctor_id or not appointment_date or not appointment_time or not reason:
            flash("All appointment details are required.", "danger")
            return render_template("appointment.html", doctors=doctors, preselected_doctor=preselected_doctor, form=request.form)

        # Verify chosen doctor exists
        doctor = db.get_user_by_id(doctor_id)
        if not doctor or doctor.get("role") != "doctor":
            flash("Selected doctor could not be verified.", "danger")
            return render_template("appointment.html", doctors=doctors, preselected_doctor=preselected_doctor, form=request.form)

        appointment_data = {
            "patient_id": g.user["user_id"],
            "doctor_id": doctor_id,
            "appointment_date": appointment_date,
            "appointment_time": appointment_time,
            "reason": reason,
            "status": "PENDING"
        }

        created = db.create_appointment(appointment_data)

        # Trigger SNS Notification (Event 1: Appointment Booked)
        sns.notify_appointment_booked(
            patient_id=g.user["user_id"],
            patient_name=g.user["name"],
            doctor_name=doctor["name"],
            date=appointment_date,
            time=appointment_time
        )
        db.create_notification(
            patient_id=g.user["user_id"],
            message=f"Appointment booked with {doctor['name']} for {appointment_date} at {appointment_time}. Status: PENDING."
        )

        flash("Appointment scheduled successfully! Awaiting confirmation from physician.", "success")
        return redirect(url_for("view_appointments"))

    return render_template("appointment.html", doctors=doctors, preselected_doctor=preselected_doctor, form={})

@app.route("/appointments/<appointment_id>/cancel", methods=["POST"])
@patient_required
def cancel_appointment(appointment_id):
    """Cancel a pending or confirmed appointment."""
    appointment = db.get_appointment_by_id(appointment_id)
    if not appointment:
        flash("Appointment not found.", "danger")
        return redirect(url_for("view_appointments"))

    # Security check: patients can only cancel their own appointments
    if appointment["patient_id"] != g.user["user_id"]:
        flash("Unauthorized: You cannot cancel another patient's appointment.", "danger")
        return redirect(url_for("view_appointments"))

    db.update_appointment_status(appointment_id, "CANCELLED")

    # Trigger SNS Notification (Event 2: Appointment Cancelled)
    sns.notify_appointment_cancelled(
        patient_id=g.user["user_id"],
        patient_name=g.user["name"],
        doctor_name=appointment.get("doctor_name", "your physician"),
        date=appointment["appointment_date"],
        time=appointment["appointment_time"]
    )
    db.create_notification(
        patient_id=g.user["user_id"],
        message=f"Your appointment with {appointment.get('doctor_name', 'physician')} on {appointment['appointment_date']} was cancelled."
    )

    flash("Appointment has been cancelled.", "info")
    return redirect(url_for("view_appointments"))

@app.route("/diagnoses")
@patient_required
def view_diagnoses():
    """View personal diagnosis records."""
    patient_id = g.user["user_id"]
    diagnoses = db.get_diagnoses_by_patient(patient_id)
    return render_template("diagnoses.html", diagnoses=diagnoses)

@app.route("/prescriptions")
@patient_required
def patient_prescriptions():
    """Patient clinical prescriptions overview."""
    patient_id = g.user["user_id"]
    prescriptions = db.get_prescriptions_by_patient(patient_id)
    return render_template("patient_prescriptions.html", prescriptions=prescriptions)

@app.route("/prescriptions/<prescription_id>")
@patient_required
def patient_prescription_detail(prescription_id):
    """View confidential clinical details of a prescription issued to the patient."""
    patient_id = g.user["user_id"]
    rx = db.get_prescription_by_id(prescription_id)
    if not rx or rx["patient_id"] != patient_id:
        abort(404)
    return render_template("prescription_detail.html", rx=rx, is_doctor=False)

# -----------------------------------------------------------------
# Medical Reports Routes (Phase 8)
# -----------------------------------------------------------------
@app.route("/reports")
@patient_required
def patient_reports():
    """Patient medical diagnostic reports overview."""
    patient_id = g.user["user_id"]
    reports = db.get_reports_by_patient(patient_id)
    return render_template("patient_reports.html", reports=reports)

@app.route("/reports/upload", methods=["GET", "POST"])
@patient_required
def upload_report():
    """Upload a new medical diagnostic report (PDF/PNG/JPEG)."""
    patient_id = g.user["user_id"]
    appointments = db.get_appointments_by_patient(patient_id)
    doctors = db.get_doctors()

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        notes = request.form.get("notes", "").strip()
        doctor_id = request.form.get("doctor_id", "").strip() or None
        appointment_id = request.form.get("appointment_id", "").strip() or None

        if not title:
            flash("Report title is mandatory.", "danger")
            return render_template("report_form.html", appointments=appointments, doctors=doctors, form=request.form)

        if "report_file" not in request.files:
            flash("Please select a valid report file to upload.", "danger")
            return render_template("report_form.html", appointments=appointments, doctors=doctors, form=request.form)

        file = request.files["report_file"]
        if not file or not file.filename:
            flash("Please select a valid report file.", "danger")
            return render_template("report_form.html", appointments=appointments, doctors=doctors, form=request.form)

        # Validate appointment and doctor authorization if provided
        if appointment_id:
            appt = db.get_appointment_by_id(appointment_id)
            if not appt or appt["patient_id"] != patient_id:
                flash("Invalid appointment selected.", "danger")
                return render_template("report_form.html", appointments=appointments, doctors=doctors, form=request.form)
            if doctor_id and appt.get("doctor_id") != doctor_id:
                flash("Appointment doctor mismatch.", "danger")
                return render_template("report_form.html", appointments=appointments, doctors=doctors, form=request.form)
            doctor_id = appt.get("doctor_id")
        elif doctor_id:
            if not db.is_doctor_authorized_for_patient(doctor_id, patient_id):
                flash("You do not have an established appointment relationship with this physician.", "danger")
                return render_template("report_form.html", appointments=appointments, doctors=doctors, form=request.form)

        # 1. Validate file format, size, magic bytes
        try:
            safe_name, content_type, file_size = storage_service.validate_file(file.stream, file.filename)
        except ValueError as e:
            flash(str(e), "danger")
            return render_template("report_form.html", appointments=appointments, doctors=doctors, form=request.form)

        report_id = f"rep-{uuid.uuid4().hex[:8]}"

        # 2. Upload to storage (S3 or local)
        try:
            storage_info = storage_service.save_file(patient_id, report_id, file.stream, safe_name)
        except Exception as e:
            flash(f"Failed to store report file: {str(e)}", "danger")
            return render_template("report_form.html", appointments=appointments, doctors=doctors, form=request.form)

        # 3. Insert metadata into database
        try:
            db.create_report({
                "report_id": report_id,
                "patient_id": patient_id,
                "doctor_id": doctor_id,
                "appointment_id": appointment_id,
                "title": title,
                "file_name": storage_info["file_name"],
                "file_type": storage_info["file_type"],
                "file_size": storage_info["file_size"],
                "storage_path": storage_info["storage_path"],
                "notes": notes
            })
        except Exception as e:
            # Compensation cleanup: delete uploaded object on DB failure
            storage_service.delete_file(storage_info["storage_path"])
            flash(f"Database error saving report metadata: {str(e)}", "danger")
            return render_template("report_form.html", appointments=appointments, doctors=doctors, form=request.form)

        flash(f"Medical report '{title}' uploaded successfully.", "success")
        return redirect(url_for("patient_reports"))

    return render_template("report_form.html", appointments=appointments, doctors=doctors, form={})

@app.route("/reports/<report_id>/download")
@login_required
def download_report(report_id):
    """Secure, authorized download/viewing of medical diagnostic report."""
    report = db.get_report_by_id(report_id)
    if not report:
        abort(404)

    user_id = g.user["user_id"]
    user_role = g.user.get("role")

    # Patient authorization: must be report owner
    if user_role == "patient":
        if report["patient_id"] != user_id:
            abort(404)
    # Doctor authorization: must have established doctor-patient relationship
    elif user_role == "doctor":
        is_auth = db.is_doctor_authorized_for_patient(user_id, report["patient_id"])
        if not is_auth:
            abort(404)
    else:
        abort(403)

    try:
        access_info = storage_service.get_access_url_or_path(report["storage_path"], expires_in=600)
    except FileNotFoundError:
        flash("The requested report file is unavailable in storage.", "danger")
        return redirect(url_for("patient_reports") if user_role == "patient" else url_for("doctor_dashboard"))

    if access_info["type"] == "s3":
        return redirect(access_info["url"])
    else:
        return send_file(
            access_info["path"],
            as_attachment=False,
            download_name=report["file_name"],
            mimetype=report["file_type"]
        )

@app.route("/reports/<report_id>/delete", methods=["POST"])
@login_required
def delete_report_route(report_id):
    """Delete a medical report from storage and database with explicit failure compensation."""
    # 1. Authorize first and verify report exists
    report = db.get_report_by_id(report_id)
    if not report:
        abort(404)

    user_id = g.user["user_id"]
    user_role = g.user.get("role")

    if user_role == "patient":
        if report["patient_id"] != user_id:
            abort(404)
    elif user_role == "doctor":
        is_auth = db.is_doctor_authorized_for_patient(user_id, report["patient_id"])
        if not is_auth:
            abort(404)
    else:
        abort(403)

    # 2. Retain backup of storage artifact in memory for compensation if DB deletion fails
    backup_data = None
    try:
        if storage_service.is_s3_enabled:
            s3 = storage_service.get_s3_client()
            resp = s3.get_object(Bucket=storage_service.bucket_name, Key=report["storage_path"])
            backup_data = resp["Body"].read()
        else:
            local_file = (storage_service.local_dir / report["storage_path"]).resolve()
            if local_file.is_file():
                with open(local_file, "rb") as f:
                    backup_data = f.read()
    except Exception:
        backup_data = None

    # 3. Attempt storage deletion
    storage_service.delete_file(report["storage_path"])

    # 4. Delete database metadata
    try:
        deleted = db.delete_report(report_id, user_id)
        if not deleted:
            raise RuntimeError("Database record deletion returned False.")
    except Exception as e:
        # Compensation: attempt to restore storage artifact if DB deletion failed
        if backup_data is not None:
            try:
                if storage_service.is_s3_enabled:
                    s3 = storage_service.get_s3_client()
                    s3.put_object(
                        Bucket=storage_service.bucket_name,
                        Key=report["storage_path"],
                        Body=backup_data,
                        ContentType=report.get("file_type", "application/octet-stream"),
                        ServerSideEncryption="AES256"
                    )
                else:
                    local_file = (storage_service.local_dir / report["storage_path"]).resolve()
                    local_file.parent.mkdir(parents=True, exist_ok=True)
                    with open(local_file, "wb") as f:
                        f.write(backup_data)
            except Exception:
                pass  # Best effort compensation

        flash(f"Failed to delete medical report record: {str(e)}", "danger")
        if user_role == "patient":
            return redirect(url_for("patient_reports"))
        return redirect(request.referrer or url_for("doctor_dashboard"))

    flash(f"Report '{report['title']}' was deleted.", "info")
    if user_role == "patient":
        return redirect(url_for("patient_reports"))
    return redirect(request.referrer or url_for("doctor_dashboard"))


# -----------------------------------------------------------------
# Doctor Routes
# -----------------------------------------------------------------
@app.route("/doctor/dashboard")
@doctor_required
def doctor_dashboard():
    """Doctor dashboard overview showing metrics, upcoming consultations, and diagnosis queue."""
    doctor_id = g.user["user_id"]
    appointments = db.get_appointments_by_doctor(doctor_id)
    diagnoses = db.get_diagnoses_by_doctor(doctor_id)
    today = get_current_date()

    today_visits = [a for a in appointments if a.get("appointment_date", "") == today and a.get("status") != "CANCELLED"]
    pending_requests = [a for a in appointments if a.get("status") == "PENDING"]
    completed_consults = [a for a in appointments if a.get("status") == "COMPLETED"]
    unique_patients = len(set(a.get("patient_id") for a in appointments if a.get("patient_id")))
    upcoming_appointments = [a for a in appointments if a.get("status") in ("PENDING", "CONFIRMED")]

    return render_template(
        "doctor_dashboard.html",
        doctor=g.user,
        appointments=appointments,
        upcoming_appointments=upcoming_appointments,
        today_visits=today_visits,
        pending_requests=pending_requests,
        completed_consults=completed_consults,
        unique_patients=unique_patients,
        diagnoses=diagnoses
    )

@app.route("/doctor/appointments/<appointment_id>/status", methods=["POST"])
@doctor_required
def update_appointment_status(appointment_id):
    """Doctor confirms, completes, or cancels an assigned appointment."""
    appointment = db.get_appointment_by_id(appointment_id)
    if not appointment:
        flash("Appointment record not found.", "danger")
        return redirect(url_for("doctor_dashboard"))

    # Security check: doctors can only update appointments assigned to them
    if appointment["doctor_id"] != g.user["user_id"]:
        flash("Unauthorized: This appointment is not assigned to your schedule.", "danger")
        return redirect(url_for("doctor_dashboard"))

    new_status = request.form.get("status", "").strip().upper()
    if new_status not in ("CONFIRMED", "COMPLETED", "CANCELLED"):
        flash("Invalid status update.", "danger")
        return redirect(url_for("doctor_dashboard"))

    db.update_appointment_status(appointment_id, new_status)

    patient_id = appointment["patient_id"]
    patient_name = appointment.get("patient_name", "Patient")

    # Trigger SNS Notifications for Events 3 (Confirmed) & 2 (Cancelled)
    if new_status == "CONFIRMED":
        sns.notify_appointment_confirmed(
            patient_id=patient_id,
            patient_name=patient_name,
            doctor_name=g.user["name"],
            date=appointment["appointment_date"],
            time=appointment["appointment_time"]
        )
        db.create_notification(
            patient_id=patient_id,
            message=f"{g.user['name']} has confirmed your appointment on {appointment['appointment_date']} at {appointment['appointment_time']}."
        )
        flash("Appointment confirmed successfully.", "success")

    elif new_status == "CANCELLED":
        sns.notify_appointment_cancelled(
            patient_id=patient_id,
            patient_name=patient_name,
            doctor_name=g.user["name"],
            date=appointment["appointment_date"],
            time=appointment["appointment_time"]
        )
        db.create_notification(
            patient_id=patient_id,
            message=f"{g.user['name']} cancelled the appointment scheduled for {appointment['appointment_date']}."
        )
        flash("Appointment cancelled.", "info")
    else:
        flash("Appointment status updated.", "info")

    return redirect(url_for("doctor_dashboard"))

@app.route("/doctor/diagnosis/new/<appointment_id>", methods=["GET", "POST"])
@doctor_required
def submit_diagnosis(appointment_id):
    """Doctor submits clinical diagnosis upon consultation completion."""
    appointment = db.get_appointment_by_id(appointment_id)
    if not appointment:
        flash("Appointment record not found.", "danger")
        return redirect(url_for("doctor_dashboard"))

    # Security check: only assigned doctor can submit diagnosis
    if appointment["doctor_id"] != g.user["user_id"]:
        flash("Unauthorized: You are not the assigned physician for this appointment.", "danger")
        return redirect(url_for("doctor_dashboard"))

    if request.method == "POST":
        diagnosis_text = request.form.get("diagnosis", "").strip()
        date_str = request.form.get("date", "").strip() or get_current_date()

        if not diagnosis_text:
            flash("Clinical diagnosis text cannot be blank.", "danger")
            return render_template("diagnosis.html", appointment=appointment, today=get_current_date())

        diagnosis_record = {
            "patient_id": appointment["patient_id"],
            "doctor_id": g.user["user_id"],
            "diagnosis": diagnosis_text,
            "date": date_str
        }

        db.create_diagnosis(diagnosis_record)
        db.update_appointment_status(appointment_id, "COMPLETED")

        # Trigger SNS Notification (Event 4: Diagnosis Submitted)
        sns.notify_diagnosis_submitted(
            patient_id=appointment["patient_id"],
            patient_name=appointment.get("patient_name", "Patient"),
            doctor_name=g.user["name"],
            date=date_str
        )
        db.create_notification(
            patient_id=appointment["patient_id"],
            message=f"{g.user['name']} recorded a clinical diagnosis for your visit on {date_str}."
        )

        flash("Clinical diagnosis saved and appointment marked as COMPLETED.", "success")
        return redirect(url_for("doctor_dashboard"))

    today = get_current_date()
    return render_template("diagnosis.html", appointment=appointment, today=today)

@app.route("/doctor/schedule")
@doctor_required
def doctor_schedule():
    """Physician agenda and consultation calendar schedule."""
    doctor_id = g.user["user_id"]
    appointments = db.get_appointments_by_doctor(doctor_id)
    return render_template("doctor_schedule.html", doctor=g.user, appointments=appointments)

@app.route("/doctor/patients")
@doctor_required
def doctor_patients():
    """Physician patient roster with clinical visit history."""
    doctor_id = g.user["user_id"]
    patients = db.get_patients_by_doctor(doctor_id)
    return render_template("doctor_patients.html", doctor=g.user, patients=patients)

@app.route("/doctor/reports")
@app.route("/doctor/diagnoses")
@doctor_required
def doctor_reports():
    """Physician diagnosis reports and clinical evaluations repository."""
    doctor_id = g.user["user_id"]
    diagnoses = db.get_diagnoses_by_doctor(doctor_id)
    return render_template("doctor_reports.html", doctor=g.user, diagnoses=diagnoses)

@app.route("/doctor/patients/<patient_id>/reports")
@doctor_required
def doctor_patient_reports(patient_id):
    """Doctor view of authorized patient's diagnostic reports."""
    doctor_id = g.user["user_id"]
    if not db.is_doctor_authorized_for_patient(doctor_id, patient_id):
        abort(404)

    patient = db.get_user_by_id(patient_id)
    if not patient:
        abort(404)

    reports = db.get_reports_by_doctor(doctor_id, patient_id=patient_id)
    return render_template("doctor_patient_reports.html", doctor=g.user, patient=patient, reports=reports)


@app.route("/doctor/prescriptions")
@doctor_required
def doctor_prescriptions():
    """Overview of clinical prescriptions issued by the physician."""
    doctor_id = g.user["user_id"]
    prescriptions = db.get_prescriptions_by_doctor(doctor_id)
    return render_template("doctor_prescriptions.html", doctor=g.user, prescriptions=prescriptions)

@app.route("/doctor/prescriptions/new", methods=["GET", "POST"])
@doctor_required
def doctor_new_prescription():
    """Physician clinical prescription issuance workspace."""
    doctor_id = g.user["user_id"]
    patients = db.get_patients_by_doctor(doctor_id)

    if request.method == "POST":
        patient_id = request.form.get("patient_id", "").strip()
        appointment_id = request.form.get("appointment_id", "").strip() or None
        medicine_name = request.form.get("medicine_name", "").strip()
        dosage = request.form.get("dosage", "").strip()
        schedule_time = request.form.get("schedule_time", "").strip()
        schedule_times_input = request.form.get("schedule_times", "").strip()
        frequency = request.form.get("frequency", "Daily").strip()
        meal_timing = request.form.get("meal_timing", "After Food").strip()
        instructions = request.form.get("instructions", "").strip()
        valid_until = request.form.get("valid_until", "").strip() or None

        if not patient_id:
            flash("Please select a valid patient.", "danger")
            return redirect(url_for("doctor_new_prescription"))

        if not medicine_name or not dosage:
            flash("Medication name and dosage are mandatory.", "danger")
            return redirect(url_for("doctor_new_prescription", patient_id=patient_id, appointment_id=appointment_id or ""))

        # Determine schedule times list
        raw_schedule = schedule_times_input or schedule_time
        if raw_schedule:
            times = [t.strip() for t in raw_schedule.split(",") if t.strip()]
        else:
            times = ["08:00 AM"]

        try:
            rx = db.create_prescription(
                doctor_id=doctor_id,
                patient_id=patient_id,
                appointment_id=appointment_id,
                medicine_name=medicine_name,
                dosage=dosage,
                schedule_times=times,
                instructions=instructions,
                valid_until=valid_until,
                frequency=frequency,
                meal_timing=meal_timing
            )

            # In-app notification & Simulated SNS alert
            pat = db.get_user_by_id(patient_id)
            pat_name = pat.get("name", "Patient") if pat else "Patient"
            db.create_notification(
                patient_id=patient_id,
                message=f"{g.user['name']} has prescribed {medicine_name} ({dosage}). It has been added to your daily schedule."
            )
            sns.notify_medicine_added(
                patient_id=patient_id,
                patient_name=pat_name,
                medicine_name=medicine_name,
                dosage=dosage,
                scheduled_time=times[0]
            )

            flash(f"Prescription for {medicine_name} ({dosage}) successfully issued and added to patient schedule.", "success")
            return redirect(url_for("doctor_prescriptions"))
        except ValueError as e:
            flash(str(e), "danger")
            return redirect(url_for("doctor_new_prescription", patient_id=patient_id, appointment_id=appointment_id or ""))

    # GET request
    selected_patient_id = request.args.get("patient_id", "").strip()
    selected_appointment_id = request.args.get("appointment_id", "").strip()
    appointment = None
    if selected_appointment_id:
        appointment = db.get_appointment_by_id(selected_appointment_id)
        if appointment and appointment.get("doctor_id") == doctor_id:
            selected_patient_id = appointment.get("patient_id")

    return render_template(
        "prescription_form.html",
        doctor=g.user,
        patients=patients,
        selected_patient_id=selected_patient_id,
        selected_appointment_id=selected_appointment_id,
        appointment=appointment,
        today=get_current_date()
    )

@app.route("/doctor/prescriptions/<prescription_id>")
@doctor_required
def doctor_prescription_detail(prescription_id):
    """View clinical details of a specific issued prescription."""
    doctor_id = g.user["user_id"]
    rx = db.get_prescription_by_id(prescription_id)
    if not rx or rx["doctor_id"] != doctor_id:
        abort(404)
    return render_template("prescription_detail.html", rx=rx, is_doctor=True)

@app.route("/doctor/prescriptions/<prescription_id>/discontinue", methods=["POST"])
@doctor_required
def doctor_discontinue_prescription(prescription_id):
    """Discontinue an active prescription and deactivate its linked medicine."""
    doctor_id = g.user["user_id"]
    rx = db.get_prescription_by_id(prescription_id)
    if not rx or rx["doctor_id"] != doctor_id:
        abort(404)

    if rx["status"] != "ACTIVE":
        flash(f"Prescription {prescription_id} is already {rx['status']}.", "info")
        return redirect(url_for("doctor_prescriptions"))

    db.discontinue_prescription(prescription_id, doctor_id)

    db.create_notification(
        patient_id=rx["patient_id"],
        message=f"{g.user['name']} has discontinued your prescription for {rx['medicine_name']}."
    )
    flash(f"Prescription for {rx['medicine_name']} marked as DISCONTINUED. Linked medication deactivated.", "info")
    return redirect(url_for("doctor_prescriptions"))

@app.route("/doctor/notifications")
@doctor_required
def doctor_notifications():
    """Physician notifications and appointment alerts stream."""
    doctor_id = g.user["user_id"]
    notifications = db.get_notifications_by_doctor(doctor_id)
    return render_template("doctor_notifications.html", doctor=g.user, notifications=notifications)

@app.route("/doctor/profile")
@doctor_required
def doctor_profile():
    """Physician credentials, specialty details, and account settings."""
    doctor_id = g.user["user_id"]
    appointments = db.get_appointments_by_doctor(doctor_id)
    diagnoses = db.get_diagnoses_by_doctor(doctor_id)
    return render_template("doctor_profile.html", doctor=g.user, appointments=appointments, diagnoses=diagnoses)

# -----------------------------------------------------------------
# Patient Medicine Tracking & Dose Reminders
# -----------------------------------------------------------------
@app.route("/medicines")
@patient_required
def view_medicines():
    """View patient's personal medicine cabinet and prescription schedule."""
    patient_id = g.user["user_id"]
    medicines_list = db.get_medicines_by_patient(patient_id)
    return render_template("medicines.html", medicines=medicines_list)

@app.route("/medicines/new", methods=["GET", "POST"])
@patient_required
def add_medicine():
    """Register a new prescription with dosage and scheduled intake time."""
    patient_id = g.user["user_id"]

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        dosage = request.form.get("dosage", "").strip()
        schedule_time = request.form.get("schedule_time", "").strip()
        frequency = request.form.get("frequency", "Daily").strip()
        meal_timing = request.form.get("meal_timing", "After Food").strip()
        notes = request.form.get("notes", "").strip()

        if not name or not dosage or not schedule_time:
            flash("Please provide Medicine Name, Dosage, and Scheduled Intake Time.", "danger")
            return render_template("medicine_form.html", form=request.form)

        new_med = db.create_medicine(
            patient_id=patient_id,
            name=name,
            dosage=dosage,
            schedule_time=schedule_time,
            frequency=frequency,
            meal_timing=meal_timing,
            notes=notes
        )

        # Notify via SNS and log
        sns.notify_medicine_added(
            patient_id=patient_id,
            patient_name=g.user["name"],
            medicine_name=name,
            dosage=dosage,
            scheduled_time=schedule_time
        )
        db.create_notification(
            patient_id=patient_id,
            message=f"Added {name} ({dosage}) to your medication schedule at {schedule_time}."
        )

        flash(f"Successfully scheduled {name} ({dosage}) at {schedule_time}.", "success")
        return redirect(url_for("view_medicines"))

    return render_template("medicine_form.html", form={
        "schedule_time": "08:00 AM",
        "frequency": "Daily",
        "meal_timing": "After Food"
    })

@app.route("/medicines/<medicine_id>/delete", methods=["POST"])
@patient_required
def delete_medicine_route(medicine_id):
    """Remove a medicine from the cabinet."""
    patient_id = g.user["user_id"]
    med = db.get_medicine_by_id(medicine_id)
    if not med or med["patient_id"] != patient_id:
        flash("Medication record not found.", "danger")
        return redirect(url_for("view_medicines"))

    if med.get("prescription_id") is not None:
        flash("Prescription medications cannot be removed by patients. Please consult your prescribing physician.", "warning")
        return redirect(url_for("view_medicines"))

    med_name = med["name"]
    db.delete_medicine(medicine_id, patient_id)
    flash(f"Removed {med_name} from your medication cabinet.", "info")
    return redirect(url_for("view_medicines"))

@app.route("/medicines/<medicine_id>/remind", methods=["POST"])
@patient_required
def trigger_dose_reminder(medicine_id):
    """
    Evaluator Demonstration Trigger:
    Instantly dispatches an Amazon SNS dose reminder for this medication.
    """
    patient_id = g.user["user_id"]
    med = db.get_medicine_by_id(medicine_id)
    if not med or med["patient_id"] != patient_id:
        flash("Medication record not found.", "danger")
        return redirect(url_for("dashboard"))

    # Dispatch via SNS
    sns.notify_dose_reminder(
        patient_id=patient_id,
        patient_name=g.user["name"],
        medicine_name=med["name"],
        dosage=med["dosage"],
        scheduled_time=med["schedule_time"],
        meal_timing=med.get("meal_timing", "After Food")
    )

    db.create_notification(
        patient_id=patient_id,
        message=f"[SNS Reminder] Time to take {med['name']} ({med['dosage']}) scheduled for {med['schedule_time']}."
    )

    flash(
        f"Amazon SNS Dose Reminder dispatched for {med['name']}! (Simulated console alert broadcast).",
        "success"
    )
    return redirect(request.referrer or url_for("dashboard"))

@app.route("/medicines/intake/log", methods=["POST"])
@patient_required
def log_dose_intake():
    """Record medicine intake as TAKEN or SKIPPED."""
    patient_id = g.user["user_id"]
    medicine_id = request.form.get("medicine_id")
    status = request.form.get("status", "").upper()
    scheduled_date = request.form.get("scheduled_date")
    scheduled_time = request.form.get("scheduled_time")

    if not medicine_id or status not in ("TAKEN", "SKIPPED"):
        flash("Invalid intake logging request.", "danger")
        return redirect(url_for("dashboard"))

    try:
        log_data = db.record_intake(
            patient_id=patient_id,
            medicine_id=medicine_id,
            status=status,
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time
        )
        dose_time = log_data.get("scheduled_time", "")
        now_time = log_data.get("taken_time") or "now"

        db.create_notification(
            patient_id=patient_id,
            message=f"Dose of {log_data['medicine_name']} ({dose_time}) marked as {status}."
        )

        if status == "TAKEN":
            flash(f"Logged dose of {log_data['medicine_name']} ({log_data['dosage']}) for {dose_time} as TAKEN at {now_time}.", "success")
        else:
            flash(f"Marked dose of {log_data['medicine_name']} for {dose_time} as SKIPPED.", "warning")

    except Exception as e:
        flash(f"Failed to record intake: {str(e)}", "danger")

    return redirect(request.referrer or url_for("dashboard"))

@app.route("/medicines/history")
@patient_required
def view_intake_history():
    """Review past medicine intake compliance history."""
    patient_id = g.user["user_id"]
    logs = db.get_intake_history(patient_id)
    return render_template("history.html", logs=logs)

# -----------------------------------------------------------------
# Security Response Headers & Error Handlers
# -----------------------------------------------------------------
@app.after_request
def apply_security_headers(response):
    """Inject strict defense-in-depth HTTP security headers into every response."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=(), payment=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "frame-ancestors 'self'; "
        "form-action 'self'; "
        "connect-src 'self';"
    )
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response

@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    return render_template(
        "error.html",
        error_title="Security Validation Failed",
        error_code=400,
        error_message="The security token for this request was missing or invalid. Please reload the form and try again.",
        back_url=request.referrer or url_for("index")
    ), 400

@app.errorhandler(400)
def handle_bad_request(e):
    return render_template(
        "error.html",
        error_title="Bad Request",
        error_code=400,
        error_message="The request could not be understood or was missing required parameters.",
        back_url=url_for("index")
    ), 400

@app.errorhandler(403)
def handle_forbidden(e):
    return render_template(
        "error.html",
        error_title="Access Forbidden",
        error_code=403,
        error_message="You do not have permission to access the requested medical resource or workspace.",
        back_url=url_for("index")
    ), 403

@app.errorhandler(404)
def handle_not_found(e):
    return render_template(
        "error.html",
        error_title="Page Not Found",
        error_code=404,
        error_message="The requested clinical page, record, or endpoint does not exist.",
        back_url=url_for("index")
    ), 404

@app.errorhandler(405)
def handle_method_not_allowed(e):
    return render_template(
        "error.html",
        error_title="Method Not Allowed",
        error_code=405,
        error_message="The HTTP method used for this request is not permitted on this clinical route.",
        back_url=url_for("index")
    ), 405

@app.errorhandler(429)
def handle_too_many_requests(e):
    return render_template(
        "error.html",
        error_title="Too Many Requests",
        error_code=429,
        error_message="Access has been temporarily restricted due to excessive requests. Please try again shortly.",
        back_url=url_for("login")
    ), 429

@app.errorhandler(500)
def handle_internal_server_error(e):
    return render_template(
        "error.html",
        error_title="Internal System Error",
        error_code=500,
        error_message="An unexpected condition was encountered. Our clinical engineering team has logged this event.",
        back_url=url_for("index")
    ), 500

# -----------------------------------------------------------------
# Entry Point
# -----------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=Config.DEBUG)

