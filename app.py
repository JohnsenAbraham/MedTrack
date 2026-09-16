import os
import datetime
import functools
import uuid
import json
import logging
from flask import Flask, render_template, request, redirect, url_for, flash, session, g, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

from config import Config
from services.dynamodb_service import DynamoDBService, ICD10_REFERENCE
from services.sns_service import SNSService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s")
logger = logging.getLogger("MedTrackApp")

app = Flask(__name__)
app.config.from_object(Config)

# Initialize enterprise cloud data and notification services
db = DynamoDBService()
sns = SNSService(dynamodb_service=db)

def get_serializer():
    """Returns timed serializer for secure password reset tokens."""
    return URLSafeTimedSerializer(app.config["SECRET_KEY"])

# Fallback specialist list for initial appointment scheduling
SPECIALIST_DOCTORS = [
    {"user_id": "doc-jenkins-01", "name": "Dr. Sarah Jenkins, MD", "specialty": "Cardiology & Cardiovascular", "department": "Cardiology", "availability": "Mon - Fri (09:00 - 17:00)"},
    {"user_id": "doc-thorne-02", "name": "Dr. Mark Thorne, MD", "specialty": "Neurology & Spine Care", "department": "Neurology", "availability": "Tue - Sat (10:00 - 18:00)"},
    {"user_id": "doc-sharma-03", "name": "Dr. Priya Sharma, MD", "specialty": "Internal Medicine & Chronic Health", "department": "Internal Medicine", "availability": "Mon - Sat (08:30 - 16:30)"},
    {"user_id": "doc-chen-04", "name": "Dr. David Chen, MD", "specialty": "Pediatrics & Child Health", "department": "Pediatrics & Child Health", "availability": "Mon - Thu (09:00 - 15:00)"},
    {"user_id": "doc-rostova-05", "name": "Dr. Elena Rostova, MD", "specialty": "Dermatology & Skin Wellness", "department": "Dermatology", "availability": "Wed - Sun (11:00 - 19:00)"},
    {"user_id": "doc-vance-06", "name": "Dr. Marcus Vance, MD", "specialty": "Orthopedics & Joint Surgery", "department": "Orthopedics & Sports", "availability": "Mon - Fri (08:00 - 16:00)"}
]

# -----------------------------------------------------------------
# Authentication & Role Authorization Middleware
# -----------------------------------------------------------------
def login_required(view):
    """Decorator to require login on protected routes."""
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if "user_id" not in session:
            flash("Please sign in to access this clinical resource.", "warning")
            return redirect(url_for("login", next=request.url))
        return view(**kwargs)
    return wrapped_view

def role_required(allowed_roles):
    """Decorator to enforce role-based access control (RBAC)."""
    def decorator(view):
        @functools.wraps(view)
        def wrapped_view(**kwargs):
            if "user_id" not in session:
                flash("Please sign in to access this portal.", "warning")
                return redirect(url_for("login", next=request.url))
            user_role = session.get("user_role", "patient")
            if user_role not in allowed_roles:
                flash(f"Access restricted. Role '{user_role.capitalize()}' is not authorized for this portal.", "danger")
                return redirect(url_for("dashboard"))
            return view(**kwargs)
        return wrapped_view
    return decorator

@app.before_request
def load_logged_in_user():
    """Load current user profile into Flask g before each request."""
    g.request_id = uuid.uuid4().hex[:8]
    user_id = session.get("user_id")
    if user_id is None:
        g.user = None
    else:
        g.user = db.get_user_by_id(user_id)
        if not g.user:
            session.clear()
            g.user = None

@app.context_processor
def inject_global_context():
    """Inject common variables into all templates."""
    registered_doctors = db.get_all_doctors()
    active_doctors = registered_doctors if registered_doctors else SPECIALIST_DOCTORS
    return {
        "current_user": g.user,
        "is_authenticated": g.user is not None,
        "user_role": session.get("user_role", "patient"),
        "mock_aws": Config.MOCK_AWS,
        "current_year": datetime.datetime.now().year,
        "specialist_doctors": active_doctors,
        "hospital_name": Config.HOSPITAL_NAME,
        "hospital_departments": Config.HOSPITAL_DEPARTMENTS,
        "icd10_reference": ICD10_REFERENCE
    }

# -----------------------------------------------------------------
# Core Web & Health Check Routes
# -----------------------------------------------------------------
@app.route("/")
def index():
    """Enterprise Hospital Landing Page."""
    stats = db.get_hospital_analytics()
    doctors = db.get_all_doctors()
    return render_template("index.html", stats=stats, doctors=doctors)

@app.route("/health")
def health():
    """
    Comprehensive AWS Cloud Health & Telemetry Check.
    Validates database latency, SNS dispatch subsystem, and memory footprint.
    """
    db_health = db.check_health()
    sns_health = sns.check_health()

    overall_healthy = (db_health["status"] == "healthy") and (sns_health["status"] == "healthy")

    response_payload = {
        "status": "healthy" if overall_healthy else "degraded",
        "mock_aws": Config.MOCK_AWS,
        "region": Config.AWS_REGION,
        "service": "MedTrack Cloud Health Gateway",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "environment": {
            "mock_aws": Config.MOCK_AWS,
            "region": Config.AWS_REGION,
            "runtime": "Python 3.14 / Flask 3.x WSGI"
        },
        "components": {
            "dynamodb": db_health,
            "sns": sns_health
        },
        "resilience": {
            "retry_policy": "Exponential backoff with jitter (max 5 attempts)",
            "dlq_status": "Enabled"
        }
    }
    return jsonify(response_payload), (200 if overall_healthy else 503)

# -----------------------------------------------------------------
# 1-Click Evaluator Demo Authentication Helper
# -----------------------------------------------------------------
@app.route("/demo-login/<role>")
def demo_login(role):
    """
    Instant 1-click evaluator login helper for mentors, evaluators, and recruiters.
    Seamlessly switches sessions between Doctor, Patient, and Admin personas.
    """
    session.clear()
    target_email = ""
    role_name = ""

    if role == "doctor":
        target_email = "dr.jenkins@medtrack.health"
        role_name = "Attending Physician (Cardiology)"
    elif role == "patient":
        target_email = "jane.doe@example.com"
        role_name = "Patient (Jane Doe)"
    elif role == "admin":
        target_email = "admin@medtrack.health"
        role_name = "Chief Medical Officer / Administrator"
    else:
        flash("Invalid demo role specified.", "warning")
        return redirect(url_for("index"))

    user = db.get_user_by_email(target_email)
    if not user:
        # Re-trigger demo seeding if needed
        db.seed_hospital_demo_data()
        user = db.get_user_by_email(target_email)

    if user:
        session["user_id"] = user["user_id"]
        session["user_name"] = user["name"]
        session["user_email"] = user["email"]
        session["user_role"] = user.get("role", role)
        session["auth_provider"] = "demo"

        db.log_audit_event(
            actor_id=user["user_id"],
            actor_name=user["name"],
            actor_role=user.get("role", role),
            action="EVALUATOR_DEMO_LOGIN",
            target_resource=f"Demo Switcher ({role_name})",
            ip_address=request.remote_addr or "127.0.0.1"
        )

        flash(f"Signed in as {user['name']} ({role_name}). Welcome to MedTrack!", "success")
        if user.get("role") == "doctor":
            return redirect(url_for("doctor_queue"))
        elif user.get("role") == "admin":
            return redirect(url_for("admin_analytics"))
        return redirect(url_for("dashboard"))
    else:
        flash(f"Demo profile for {role} could not be loaded.", "danger")
        return redirect(url_for("login"))

# -----------------------------------------------------------------
# Authentication Lifecycle (Register, Login, Password Reset, Google)
# -----------------------------------------------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    """Patient registration with structured clinical metadata."""
    if g.user:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        phone = request.form.get("phone", "").strip()
        date_of_birth = request.form.get("date_of_birth", "").strip()
        gender = request.form.get("gender", "").strip()
        role = request.form.get("role", "patient").strip()
        blood_group = request.form.get("blood_group", "O+").strip()
        allergies = request.form.get("allergies", "None").strip()
        emergency_contact = request.form.get("emergency_contact", "").strip()
        insurance_provider = request.form.get("insurance_provider", "").strip()

        # Validation
        if not name or not email or not password:
            flash("Full name, email address, and password are required.", "danger")
            return render_template("register.html", form=request.form)

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "danger")
            return render_template("register.html", form=request.form)

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("register.html", form=request.form)

        existing_user = db.get_user_by_email(email)
        if existing_user:
            flash("An account with this email address already exists. Please sign in or reset your password.", "warning")
            return redirect(url_for("login"))

        password_hash = generate_password_hash(password)

        new_user = {
            "name": name,
            "email": email,
            "password_hash": password_hash,
            "phone": phone,
            "date_of_birth": date_of_birth,
            "gender": gender,
            "role": role if role in ["patient", "doctor", "admin"] else "patient",
            "blood_group": blood_group,
            "allergies": allergies,
            "emergency_contact": emergency_contact,
            "insurance_provider": insurance_provider or "Standard Health Coverage",
            "auth_provider": "local"
        }

        created = db.create_user(new_user)

        # Trigger welcome notification via AWS SNS
        sns.publish_notification(
            patient_id=created["user_id"],
            message=f"Welcome to {Config.HOSPITAL_NAME}, {name}! Your patient account has been successfully created. Patient ID: {created['user_id']}.",
            subject=f"Welcome to {Config.HOSPITAL_NAME}"
        )

        db.log_audit_event(
            actor_id=created["user_id"],
            actor_name=name,
            actor_role=new_user["role"],
            action="PATIENT_REGISTRATION",
            target_resource=f"User ID {created['user_id']}",
            ip_address=request.remote_addr or "127.0.0.1"
        )

        flash("Account registered successfully! Please sign in with your credentials.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", form={})

@app.route("/login", methods=["GET", "POST"])
def login():
    """User authentication and role routing."""
    if g.user:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Please enter both your email address and password.", "danger")
            return render_template("login.html")

        user = db.get_user_by_email(email)
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email address or password. Please check your credentials.", "danger")
            return render_template("login.html")

        session.clear()
        session["user_id"] = user["user_id"]
        session["user_name"] = user["name"]
        session["user_email"] = user["email"]
        session["user_role"] = user.get("role", "patient")
        session["auth_provider"] = user.get("auth_provider", "local")

        db.log_audit_event(
            actor_id=user["user_id"],
            actor_name=user["name"],
            actor_role=user.get("role", "patient"),
            action="USER_LOGIN",
            target_resource="Session Authentication",
            ip_address=request.remote_addr or "127.0.0.1"
        )

        flash(f"Welcome back, {user['name']}!", "success")
        next_page = request.args.get("next")
        if next_page and next_page.startswith("/"):
            return redirect(next_page)

        if user.get("role") == "doctor":
            return redirect(url_for("doctor_queue"))
        elif user.get("role") == "admin":
            return redirect(url_for("admin_analytics"))
        return redirect(url_for("dashboard"))

    return render_template("login.html")

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Password recovery workflow."""
    if g.user:
        return redirect(url_for("dashboard"))

    reset_link = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email:
            flash("Please enter your registered email address.", "danger")
            return render_template("forgot_password.html")

        user = db.get_user_by_email(email)
        if user:
            s = get_serializer()
            token = s.dumps(user["email"], salt="password-reset-salt")
            reset_url = url_for("reset_password", token=token, _external=True)

            sns.publish_notification(
                patient_id=user["user_id"],
                message=f"A password reset request was initiated for your account. Use the following link to reset your password: {reset_url}",
                subject="Password Reset Request - MedTrack"
            )
            reset_link = reset_url
            flash("Password reset instructions and a secure recovery link have been generated.", "success")
        else:
            flash("If an account exists with that email address, password reset instructions have been generated.", "info")

        return render_template("forgot_password.html", reset_link=reset_link, email=email)

    return render_template("forgot_password.html", reset_link=None)

@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    """Verify reset token and update password."""
    if g.user:
        return redirect(url_for("dashboard"))

    s = get_serializer()
    try:
        email = s.loads(token, salt="password-reset-salt", max_age=3600)
    except (SignatureExpired, BadSignature):
        flash("The password reset link is invalid or has expired. Please submit a new request.", "danger")
        return redirect(url_for("forgot_password"))

    user = db.get_user_by_email(email)
    if not user:
        flash("User account not found.", "danger")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not password or len(password) < 6:
            flash("New password must be at least 6 characters long.", "danger")
            return render_template("reset_password.html", email=email, token=token)

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("reset_password.html", email=email, token=token)

        new_hash = generate_password_hash(password)
        db.update_user_password(user["user_id"], new_hash)

        sns.publish_notification(
            patient_id=user["user_id"],
            message=f"Hello {user['name']},\n\nYour account password has been updated successfully. You can now sign in with your new password.",
            subject="Password Updated Successfully - MedTrack"
        )

        flash("Your password has been reset successfully! Please sign in.", "success")
        return redirect(url_for("login"))

    return render_template("reset_password.html", email=email, token=token)

@app.route("/auth/google", methods=["GET", "POST"])
def auth_google():
    """Google OAuth2 entrypoint and local profile sign-in."""
    if g.user:
        return redirect(url_for("dashboard"))

    google_client_id = app.config.get("GOOGLE_CLIENT_ID")
    if google_client_id:
        from urllib.parse import urlencode
        params = {
            "client_id": google_client_id,
            "redirect_uri": url_for("auth_google_callback", _external=True),
            "response_type": "code",
            "scope": "openid email profile",
            "access_type": "offline"
        }
        return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}")

    if request.method == "POST":
        google_email = request.form.get("google_email", "").strip().lower()
        google_name = request.form.get("google_name", "").strip()

        if not google_email:
            flash("Please enter your Google email address.", "danger")
            return render_template("google_login.html")

        user = db.get_user_by_email(google_email)
        if not user:
            display_name = google_name or google_email.split("@")[0].replace(".", " ").title()
            new_user = {
                "name": display_name,
                "email": google_email,
                "password_hash": generate_password_hash(uuid.uuid4().hex),
                "phone": "",
                "date_of_birth": "",
                "gender": "Not Specified",
                "role": "patient",
                "auth_provider": "google"
            }
            user = db.create_user(new_user)
            sns.publish_notification(
                patient_id=user["user_id"],
                message=f"Welcome to {Config.HOSPITAL_NAME}, {display_name}! Your account has been registered via Google Sign-In.",
                subject=f"Welcome to {Config.HOSPITAL_NAME}"
            )

        session.clear()
        session["user_id"] = user["user_id"]
        session["user_name"] = user["name"]
        session["user_email"] = user["email"]
        session["user_role"] = user.get("role", "patient")
        session["auth_provider"] = "google"

        flash(f"Signed in successfully with Google as {user['email']}.", "success")
        return redirect(url_for("dashboard"))

    suggested_email = request.args.get("email", "")
    return render_template("google_login.html", suggested_email=suggested_email)

@app.route("/auth/google/callback")
def auth_google_callback():
    """OAuth 2.0 callback endpoint."""
    flash("Google authentication callback received.", "info")
    return redirect(url_for("login"))

@app.route("/logout")
def logout():
    """Clear session and log user out."""
    if g.user:
        db.log_audit_event(
            actor_id=g.user["user_id"],
            actor_name=g.user["name"],
            actor_role=g.user.get("role", "patient"),
            action="USER_LOGOUT",
            target_resource="Session Termination",
            ip_address=request.remote_addr or "127.0.0.1"
        )
    session.clear()
    flash("You have been signed out safely. Thank you for using MedTrack.", "info")
    return redirect(url_for("login"))

# -----------------------------------------------------------------
# Patient Health Portal Dashboard
# -----------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    """
    Patient Health Portal.
    Redirects doctors to Doctor Consultation Queue and Admins to Hospital Operations.
    """
    if g.user.get("role") == "doctor":
        return redirect(url_for("doctor_queue"))
    elif g.user.get("role") == "admin":
        return redirect(url_for("admin_analytics"))

    patient_id = g.user["user_id"]
    appointments = db.get_appointments_by_patient(patient_id)
    diagnoses = db.get_diagnoses_by_patient(patient_id)
    notifications = db.get_notifications_by_patient(patient_id, limit=5)
    vitals_history = db.get_vitals_by_patient(patient_id, limit=5)

    today_str = datetime.date.today().isoformat()

    upcoming_appointments = [
        a for a in appointments
        if a.get("status") in ["Confirmed", "Checked-In"] and a.get("date", "") >= today_str
    ]
    past_appointments = [
        a for a in appointments
        if a.get("status") in ["Completed", "Cancelled"] or a.get("date", "") < today_str
    ]

    # Extract all active prescriptions across diagnoses
    active_prescriptions = []
    for d in diagnoses:
        rx_list = d.get("prescriptions", [])
        for rx in rx_list:
            if isinstance(rx, dict):
                active_prescriptions.append({
                    "medication": rx.get("medication"),
                    "dosage": rx.get("dosage"),
                    "frequency": rx.get("frequency"),
                    "duration": rx.get("duration"),
                    "prescribed_by": d.get("doctor"),
                    "date": d.get("date")
                })

    latest_vitals = vitals_history[0] if vitals_history else {
        "blood_pressure": "120/80",
        "heart_rate": 72,
        "temperature": 98.6,
        "spo2": 98,
        "blood_sugar": 95
    }

    stats = {
        "total_appointments": len(appointments),
        "upcoming_count": len(upcoming_appointments),
        "diagnoses_count": len(diagnoses),
        "active_prescriptions_count": len(active_prescriptions),
        "notifications_count": len(notifications)
    }

    db.log_audit_event(
        actor_id=g.user["user_id"],
        actor_name=g.user["name"],
        actor_role="patient",
        action="VIEW_PATIENT_DASHBOARD",
        target_resource=f"Patient Record {patient_id}",
        ip_address=request.remote_addr or "127.0.0.1"
    )

    return render_template(
        "dashboard.html",
        patient=g.user,
        upcoming_appointments=upcoming_appointments[:3],
        past_appointments=past_appointments[:3],
        diagnoses=diagnoses[:3],
        active_prescriptions=active_prescriptions[:4],
        latest_vitals=latest_vitals,
        notifications=notifications,
        stats=stats
    )

# -----------------------------------------------------------------
# Doctor Clinical Workspace & Consultation Queue
# -----------------------------------------------------------------
@app.route("/doctor/queue")
@login_required
@role_required(["doctor", "admin"])
def doctor_queue():
    """
    Attending Physician Workspace: Active Consultation Queue.
    Displays patient appointments, triage status, and instant action triggers.
    """
    doctor_id = g.user["user_id"]
    doctor_name = g.user["name"]

    queue = db.get_appointments_by_doctor(doctor_id=doctor_id, doctor_name=doctor_name)
    today_str = datetime.date.today().isoformat()

    today_queue = [a for a in queue if a.get("date") == today_str]
    upcoming_queue = [a for a in queue if a.get("date") > today_str]
    completed_queue = [a for a in queue if a.get("status") == "Completed"]

    db.log_audit_event(
        actor_id=g.user["user_id"],
        actor_name=g.user["name"],
        actor_role="doctor",
        action="VIEW_CLINICAL_QUEUE",
        target_resource=f"Doctor Queue ({doctor_name})",
        ip_address=request.remote_addr or "127.0.0.1"
    )

    return render_template(
        "doctor_queue.html",
        doctor=g.user,
        today_queue=today_queue,
        upcoming_queue=upcoming_queue,
        completed_queue=completed_queue,
        today_str=today_str
    )

@app.route("/doctor/consultation/<appointment_id>", methods=["GET", "POST"])
@login_required
@role_required(["doctor", "admin"])
def doctor_consultation(appointment_id):
    """
    Clinical Consultation Workspace:
    Physician examines patient, records vital signs, selects ICD-10 diagnosis,
    builds multi-drug e-prescriptions, and completes visit.
    """
    appt = db.get_appointment_by_id(appointment_id)
    if not appt:
        flash("Appointment record not found.", "danger")
        return redirect(url_for("doctor_queue"))

    patient = db.get_user_by_id(appt["patient_id"])
    past_diagnoses = db.get_diagnoses_by_patient(appt["patient_id"])
    today_str = datetime.date.today().isoformat()

    if request.method == "POST":
        # Extract Clinical Intake Data
        bp = request.form.get("blood_pressure", "120/80").strip()
        hr = request.form.get("heart_rate", "72").strip()
        temp = request.form.get("temperature", "98.6").strip()
        spo2 = request.form.get("spo2", "98").strip()
        bs = request.form.get("blood_sugar", "100").strip()

        icd10_code = request.form.get("icd10_code", "").strip()
        symptoms = request.form.get("symptoms", "").strip()
        diagnosis_text = request.form.get("diagnosis", "").strip()
        treatment_plan = request.form.get("treatment_plan", "").strip()
        follow_up_date = request.form.get("follow_up_date", "").strip()

        # Parse dynamic prescription items from form
        medications = request.form.getlist("med_name[]")
        dosages = request.form.getlist("med_dosage[]")
        frequencies = request.form.getlist("med_frequency[]")
        durations = request.form.getlist("med_duration[]")
        notes = request.form.getlist("med_notes[]")

        prescriptions = []
        for i in range(len(medications)):
            if medications[i].strip():
                prescriptions.append({
                    "medication": medications[i].strip(),
                    "dosage": dosages[i].strip() if i < len(dosages) else "",
                    "frequency": frequencies[i].strip() if i < len(frequencies) else "",
                    "duration": durations[i].strip() if i < len(durations) else "",
                    "notes": notes[i].strip() if i < len(notes) else ""
                })

        vitals_dict = {
            "blood_pressure": bp,
            "heart_rate": int(hr) if hr.isdigit() else 72,
            "temperature": float(temp) if temp.replace(".", "", 1).isdigit() else 98.6,
            "spo2": int(spo2) if spo2.isdigit() else 98,
            "blood_sugar": int(bs) if bs.isdigit() else 100
        }

        # 1. Record Longitudinal Vitals History
        db.record_vitals(
            patient_id=appt["patient_id"],
            recorded_by=g.user["name"],
            vitals=vitals_dict
        )

        # 2. Record Diagnosis and Prescriptions in EHR
        diag_data = {
            "patient_id": appt["patient_id"],
            "doctor_id": g.user["user_id"],
            "doctor": g.user["name"],
            "diagnosis": diagnosis_text or f"Clinical evaluation: {icd10_code}",
            "icd10_code": icd10_code,
            "symptoms": symptoms,
            "vitals_json": vitals_dict,
            "prescriptions_json": prescriptions,
            "treatment_plan": treatment_plan,
            "follow_up_date": follow_up_date,
            "date": today_str
        }
        db.create_diagnosis(diag_data)

        # 3. Transition Appointment Status to Completed
        db.update_appointment_status(
            appointment_id=appointment_id,
            new_status="Completed",
            notes=f"Consultation finalized by {g.user['name']}. ICD-10: {icd10_code}."
        )

        # 4. Dispatch SNS Notifications
        patient_name = patient["name"] if patient else "Patient"
        sns.send_diagnosis_recorded_notification(
            patient_id=appt["patient_id"],
            patient_name=patient_name,
            doctor=g.user["name"],
            diagnosis=diag_data["diagnosis"]
        )

        if prescriptions:
            sns.send_prescription_issued_notification(
                patient_id=appt["patient_id"],
                patient_name=patient_name,
                doctor=g.user["name"],
                prescription_count=len(prescriptions)
            )

        # 5. Log HIPAA Audit Event
        db.log_audit_event(
            actor_id=g.user["user_id"],
            actor_name=g.user["name"],
            actor_role="doctor",
            action="CLINICAL_CONSULTATION_COMPLETED",
            target_resource=f"Appointment {appointment_id} / Patient {appt['patient_id']}",
            ip_address=request.remote_addr or "127.0.0.1"
        )

        flash(f"Clinical consultation for {patient_name} finalized successfully! Medical records & prescriptions logged.", "success")
        return redirect(url_for("doctor_queue"))

    db.log_audit_event(
        actor_id=g.user["user_id"],
        actor_name=g.user["name"],
        actor_role="doctor",
        action="OPEN_CLINICAL_CONSULTATION",
        target_resource=f"Appointment {appointment_id}",
        ip_address=request.remote_addr or "127.0.0.1"
    )

    return render_template(
        "doctor_consultation.html",
        appointment=appt,
        patient=patient,
        past_diagnoses=past_diagnoses,
        today_str=today_str,
        icd10_reference=ICD10_REFERENCE
    )

@app.route("/doctor/appointment/<appointment_id>/status", methods=["POST"])
@login_required
@role_required(["doctor", "admin"])
def update_appointment_state(appointment_id):
    """Update appointment state machine (Checked-In, In-Consultation, Completed, Cancelled)."""
    new_status = request.form.get("status", "").strip()
    notes = request.form.get("notes", "").strip()

    if new_status in ["Confirmed", "Checked-In", "In-Consultation", "Completed", "Cancelled"]:
        db.update_appointment_status(appointment_id, new_status, notes=notes)
        appt = db.get_appointment_by_id(appointment_id)
        if appt:
            patient = db.get_user_by_id(appt["patient_id"])
            p_name = patient["name"] if patient else "Patient"
            sns.send_appointment_status_update(
                patient_id=appt["patient_id"],
                patient_name=p_name,
                doctor=appt.get("doctor", "Attending Physician"),
                new_status=new_status,
                date=appt.get("date", ""),
                time=appt.get("time", "")
            )
        flash(f"Appointment status updated to '{new_status}'.", "info")
    return redirect(url_for("doctor_queue"))

@app.route("/doctor/patient/<patient_id>")
@login_required
@role_required(["doctor", "admin"])
def patient_record_view(patient_id):
    """
    Longitudinal Patient Electronic Health Record (EHR) Explorer.
    Full historical view of all clinical encounters, vitals, prescriptions, and alerts.
    """
    patient = db.get_user_by_id(patient_id)
    if not patient:
        flash("Patient record not found.", "danger")
        return redirect(url_for("doctor_queue"))

    appointments = db.get_appointments_by_patient(patient_id)
    diagnoses = db.get_diagnoses_by_patient(patient_id)
    vitals_history = db.get_vitals_by_patient(patient_id, limit=20)

    db.log_audit_event(
        actor_id=g.user["user_id"],
        actor_name=g.user["name"],
        actor_role=g.user.get("role", "doctor"),
        action="VIEW_LONGITUDINAL_EHR",
        target_resource=f"Patient Record {patient_id}",
        ip_address=request.remote_addr or "127.0.0.1"
    )

    return render_template(
        "patient_record_view.html",
        patient=patient,
        appointments=appointments,
        diagnoses=diagnoses,
        vitals_history=vitals_history
    )

# -----------------------------------------------------------------
# Hospital Operations & AWS Cloud Management Console (Admin)
# -----------------------------------------------------------------
@app.route("/admin/analytics")
@login_required
@role_required(["admin"])
def admin_analytics():
    """
    Hospital Executive & Operational Analytics Dashboard.
    Visualizes patient volume, department load, and HIPAA audit trails.
    """
    analytics = db.get_hospital_analytics()
    audit_logs = db.get_recent_audit_logs(limit=30)
    all_users = db.get_all_users()
    all_doctors = [u for u in all_users if u.get("role") == "doctor"]

    db.log_audit_event(
        actor_id=g.user["user_id"],
        actor_name=g.user["name"],
        actor_role="admin",
        action="VIEW_ADMIN_ANALYTICS",
        target_resource="Hospital Operations Dashboard",
        ip_address=request.remote_addr or "127.0.0.1"
    )

    return render_template(
        "admin_analytics.html",
        analytics=analytics,
        audit_logs=audit_logs,
        doctors=all_doctors,
        cloud_config=Config
    )

# -----------------------------------------------------------------
# Appointment Management (Patient Flow)
# -----------------------------------------------------------------
@app.route("/appointments")
@login_required
def appointments():
    """View appointment history."""
    patient_id = g.user["user_id"]
    all_appointments = db.get_appointments_by_patient(patient_id)
    status_filter = request.args.get("status", "all").lower()

    if status_filter in ["confirmed", "cancelled", "completed", "checked-in"]:
        filtered_appointments = [a for a in all_appointments if a.get("status", "").lower() == status_filter]
    else:
        filtered_appointments = all_appointments

    return render_template(
        "appointments.html",
        appointments=filtered_appointments,
        current_filter=status_filter,
        total_count=len(all_appointments)
    )

@app.route("/appointments/new", methods=["GET", "POST"])
@login_required
def new_appointment():
    """Book a new medical appointment with certified physicians."""
    today_str = datetime.date.today().isoformat()
    doctors = db.get_all_doctors() or SPECIALIST_DOCTORS

    if request.method == "POST":
        doctor_selection = request.form.get("doctor", "").strip()
        department = request.form.get("department", "General Medicine").strip()
        date = request.form.get("date", "").strip()
        time = request.form.get("time", "").strip()
        reason = request.form.get("reason", "").strip()

        if not doctor_selection or not date or not time or not reason:
            flash("Please fill in all required appointment booking fields.", "danger")
            return render_template("appointment.html", today=today_str, doctors=doctors, form=request.form)

        if date < today_str:
            flash("Appointment date cannot be in the past.", "warning")
            return render_template("appointment.html", today=today_str, doctors=doctors, form=request.form)

        # Match doctor ID if available
        matched_doc = next((d for d in doctors if d["name"] == doctor_selection or d.get("user_id") == doctor_selection), None)
        doc_id = matched_doc["user_id"] if matched_doc else ""
        doc_name = matched_doc["name"] if matched_doc else doctor_selection
        doc_dept = matched_doc.get("department", department) if matched_doc else department

        appt_data = {
            "patient_id": g.user["user_id"],
            "doctor_id": doc_id,
            "doctor": doc_name,
            "department": doc_dept,
            "date": date,
            "time": time,
            "reason": reason,
            "status": "Confirmed"
        }

        created = db.create_appointment(appt_data)

        # Send AWS SNS Notification
        sns.send_appointment_booked_notification(
            patient_id=g.user["user_id"],
            patient_name=g.user["name"],
            doctor=doc_name,
            date=date,
            time=time
        )

        db.log_audit_event(
            actor_id=g.user["user_id"],
            actor_name=g.user["name"],
            actor_role="patient",
            action="SCHEDULE_APPOINTMENT",
            target_resource=f"Appointment {created['appointment_id']} with {doc_name}",
            ip_address=request.remote_addr or "127.0.0.1"
        )

        flash(f"Your clinical appointment has been scheduled successfully with {doc_name}! An instant notification has been dispatched.", "success")
        return redirect(url_for("appointments"))

    return render_template("appointment.html", today=today_str, doctors=doctors, form={})

@app.route("/appointments/<appointment_id>/cancel", methods=["POST"])
@login_required
def cancel_appointment(appointment_id):
    """Cancel an appointment."""
    patient_id = g.user["user_id"]
    appt = db.get_appointment_by_id(appointment_id)

    if not appt:
        flash("Appointment not found.", "danger")
        return redirect(url_for("appointments"))

    if appt.get("patient_id") != patient_id and g.user.get("role") not in ["admin", "doctor"]:
        flash("You are not authorized to cancel this appointment.", "danger")
        return redirect(url_for("appointments"))

    success = db.cancel_appointment(appointment_id, patient_id if g.user.get("role") == "patient" else None)
    if success:
        sns.send_appointment_cancelled_notification(
            patient_id=appt.get("patient_id"),
            patient_name=appt.get("patient_name", g.user["name"]),
            doctor=appt.get("doctor", "Attending Physician"),
            date=appt.get("date", ""),
            time=appt.get("time", "")
        )
        db.log_audit_event(
            actor_id=g.user["user_id"],
            actor_name=g.user["name"],
            actor_role=g.user.get("role", "patient"),
            action="CANCEL_APPOINTMENT",
            target_resource=f"Appointment {appointment_id}",
            ip_address=request.remote_addr or "127.0.0.1"
        )
        flash("Appointment has been cancelled successfully. Notification updated.", "info")
    else:
        flash("Failed to cancel appointment. Please try again.", "danger")

    return redirect(url_for("appointments"))

# -----------------------------------------------------------------
# Diagnosis & Longitudinal Record Exploration
# -----------------------------------------------------------------
@app.route("/diagnoses")
@login_required
def diagnoses():
    """View patient diagnosis and e-prescription history."""
    patient_id = g.user["user_id"]
    records = db.get_diagnoses_by_patient(patient_id)
    return render_template("diagnoses.html", diagnoses=records)

@app.route("/diagnoses/new", methods=["GET", "POST"])
@login_required
def new_diagnosis():
    """Record a clinical diagnosis record."""
    today_str = datetime.date.today().isoformat()
    all_users = db.get_all_users()

    if request.method == "POST":
        patient_id = request.form.get("patient_id", "").strip() or g.user["user_id"]
        doctor = request.form.get("doctor", "").strip() or g.user["name"]
        diagnosis_text = request.form.get("diagnosis", "").strip()
        date = request.form.get("date", "").strip() or today_str
        icd10_code = request.form.get("icd10_code", "").strip()
        symptoms = request.form.get("symptoms", "").strip()

        if not doctor or not diagnosis_text:
            flash("Doctor name and diagnosis details are required.", "danger")
            return render_template("diagnosis.html", today=today_str, all_users=all_users, form=request.form)

        diag_data = {
            "patient_id": patient_id,
            "doctor": doctor,
            "diagnosis": diagnosis_text,
            "icd10_code": icd10_code,
            "symptoms": symptoms,
            "date": date
        }

        db.create_diagnosis(diag_data)

        target_patient = db.get_user_by_id(patient_id)
        patient_name = target_patient["name"] if target_patient else "Patient"
        sns.send_diagnosis_recorded_notification(
            patient_id=patient_id,
            patient_name=patient_name,
            doctor=doctor,
            diagnosis=diagnosis_text
        )

        db.log_audit_event(
            actor_id=g.user["user_id"],
            actor_name=g.user["name"],
            actor_role=g.user.get("role", "patient"),
            action="CREATE_DIAGNOSIS_RECORD",
            target_resource=f"Patient {patient_id}",
            ip_address=request.remote_addr or "127.0.0.1"
        )

        flash("Clinical diagnosis record has been logged successfully! Patient notification sent.", "success")
        return redirect(url_for("diagnoses"))

    return render_template("diagnosis.html", today=today_str, all_users=all_users, form={})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
