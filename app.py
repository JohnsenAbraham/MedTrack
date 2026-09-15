import os
import datetime
import functools
from flask import Flask, render_template, request, redirect, url_for, flash, session, g
from werkzeug.security import generate_password_hash, check_password_hash

from config import Config
from services.dynamodb_service import DynamoDBService
from services.sns_service import SNSService

app = Flask(__name__)
app.config.from_object(Config)

# Initialize service layer
db = DynamoDBService()
sns = SNSService(dynamodb_service=db)

# Predefined list of healthcare specialists for appointments
SPECIALIST_DOCTORS = [
    {"name": "Dr. Sarah Jenkins", "specialty": "Cardiology", "availability": "Mon - Fri"},
    {"name": "Dr. Mark Thorne", "specialty": "Neurology & Spine", "availability": "Tue - Sat"},
    {"name": "Dr. Priya Sharma", "specialty": "General Medicine & Internal Health", "availability": "Mon - Sat"},
    {"name": "Dr. David Chen", "specialty": "Pediatrics & Child Care", "availability": "Mon - Thu"},
    {"name": "Dr. Elena Rostova", "specialty": "Dermatology & Skin Wellness", "availability": "Wed - Sun"},
    {"name": "Dr. Marcus Vance", "specialty": "Orthopedics & Sports Medicine", "availability": "Mon - Fri"}
]

# -----------------------------------------------------------------
# Authentication Helper & Decorator
# -----------------------------------------------------------------
def login_required(view):
    """Decorator to require login on protected routes."""
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if "user_id" not in session:
            flash("Please sign in to access this page.", "warning")
            return redirect(url_for("login", next=request.url))
        return view(**kwargs)
    return wrapped_view

@app.before_request
def load_logged_in_user():
    """Load current user profile into Flask g before each request."""
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
    return {
        "current_user": g.user,
        "is_authenticated": g.user is not None,
        "mock_aws": Config.MOCK_AWS,
        "current_year": datetime.datetime.now().year,
        "specialist_doctors": SPECIALIST_DOCTORS
    }

# -----------------------------------------------------------------
# Core Web Routes
# -----------------------------------------------------------------
@app.route("/")
def index():
    """Home / Landing page."""
    return render_template("index.html")

@app.route("/health")
def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "mock_aws": Config.MOCK_AWS,
        "region": Config.AWS_REGION,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }

@app.route("/register", methods=["GET", "POST"])
def register():
    """Patient registration."""
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
            flash("An account with this email address already exists. Please log in.", "warning")
            return redirect(url_for("login"))

        password_hash = generate_password_hash(password)

        new_user = {
            "name": name,
            "email": email,
            "password_hash": password_hash,
            "phone": phone,
            "date_of_birth": date_of_birth,
            "gender": gender,
            "role": role if role in ["patient", "doctor"] else "patient"
        }

        created = db.create_user(new_user)
        # Trigger welcome notification via SNS
        sns.publish_notification(
            patient_id=created["user_id"],
            message=f"Welcome to MedTrack, {name}! Your patient account has been successfully created.",
            subject="Welcome to MedTrack"
        )

        flash("Account registered successfully! Please log in with your credentials.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", form={})

@app.route("/login", methods=["GET", "POST"])
def login():
    """User authentication and session establishment."""
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
            flash("Invalid email address or password. Please try again.", "danger")
            return render_template("login.html")

        # Session setup
        session.clear()
        session["user_id"] = user["user_id"]
        session["user_name"] = user["name"]
        session["user_email"] = user["email"]
        session["user_role"] = user.get("role", "patient")

        flash(f"Welcome back, {user['name']}!", "success")
        next_page = request.args.get("next")
        if next_page and next_page.startswith("/"):
            return redirect(next_page)
        return redirect(url_for("dashboard"))

    return render_template("login.html")

@app.route("/logout")
def logout():
    """Clear session and log user out."""
    session.clear()
    flash("You have been signed out safely. Thank you for using MedTrack.", "info")
    return redirect(url_for("login"))

# -----------------------------------------------------------------
# Healthcare Dashboard & Records
# -----------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    """Patient overview dashboard."""
    patient_id = g.user["user_id"]

    appointments = db.get_appointments_by_patient(patient_id)
    diagnoses = db.get_diagnoses_by_patient(patient_id)
    notifications = db.get_notifications_by_patient(patient_id, limit=5)

    today_str = datetime.date.today().isoformat()

    # Split into upcoming and past
    upcoming_appointments = [
        a for a in appointments
        if a.get("status") == "Confirmed" and a.get("date", "") >= today_str
    ]
    past_appointments = [
        a for a in appointments
        if a.get("status") == "Cancelled" or a.get("date", "") < today_str
    ]

    stats = {
        "total_appointments": len(appointments),
        "upcoming_count": len(upcoming_appointments),
        "diagnoses_count": len(diagnoses),
        "notifications_count": len(notifications)
    }

    return render_template(
        "dashboard.html",
        patient=g.user,
        upcoming_appointments=upcoming_appointments[:3],
        past_appointments=past_appointments[:3],
        diagnoses=diagnoses[:3],
        notifications=notifications,
        stats=stats
    )

# -----------------------------------------------------------------
# Appointment Management
# -----------------------------------------------------------------
@app.route("/appointments")
@login_required
def appointments():
    """View appointment history."""
    patient_id = g.user["user_id"]
    all_appointments = db.get_appointments_by_patient(patient_id)
    status_filter = request.args.get("status", "all").lower()

    if status_filter in ["confirmed", "cancelled"]:
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
    """Book a new medical appointment."""
    today_str = datetime.date.today().isoformat()

    if request.method == "POST":
        doctor = request.form.get("doctor", "").strip()
        date = request.form.get("date", "").strip()
        time = request.form.get("time", "").strip()
        reason = request.form.get("reason", "").strip()

        if not doctor or not date or not time or not reason:
            flash("Please fill in all appointment fields.", "danger")
            return render_template("appointment.html", today=today_str, form=request.form)

        if date < today_str:
            flash("Appointment date cannot be in the past.", "warning")
            return render_template("appointment.html", today=today_str, form=request.form)

        appt_data = {
            "patient_id": g.user["user_id"],
            "doctor": doctor,
            "date": date,
            "time": time,
            "reason": reason,
            "status": "Confirmed"
        }

        created = db.create_appointment(appt_data)

        # Send SNS Notification
        sns.send_appointment_booked_notification(
            patient_id=g.user["user_id"],
            patient_name=g.user["name"],
            doctor=doctor,
            date=date,
            time=time
        )

        flash("Your medical appointment has been scheduled successfully! A notification has been dispatched.", "success")
        return redirect(url_for("appointments"))

    return render_template("appointment.html", today=today_str, form={})

@app.route("/appointments/<appointment_id>/cancel", methods=["POST"])
@login_required
def cancel_appointment(appointment_id):
    """Cancel an appointment."""
    patient_id = g.user["user_id"]
    appt = db.get_appointment_by_id(appointment_id)

    if not appt:
        flash("Appointment not found.", "danger")
        return redirect(url_for("appointments"))

    # Ensure patient owns this appointment
    if appt.get("patient_id") != patient_id and g.user.get("role") != "admin":
        flash("You are not authorized to cancel this appointment.", "danger")
        return redirect(url_for("appointments"))

    success = db.cancel_appointment(appointment_id, patient_id)
    if success:
        sns.send_appointment_cancelled_notification(
            patient_id=patient_id,
            patient_name=g.user["name"],
            doctor=appt.get("doctor", "Doctor"),
            date=appt.get("date", ""),
            time=appt.get("time", "")
        )
        flash("Appointment has been cancelled successfully. Notification updated.", "info")
    else:
        flash("Failed to cancel appointment. Please try again.", "danger")

    return redirect(url_for("appointments"))

# -----------------------------------------------------------------
# Diagnosis Management
# -----------------------------------------------------------------
@app.route("/diagnoses")
@login_required
def diagnoses():
    """View patient diagnosis history."""
    patient_id = g.user["user_id"]
    records = db.get_diagnoses_by_patient(patient_id)
    return render_template("diagnoses.html", diagnoses=records)

@app.route("/diagnoses/new", methods=["GET", "POST"])
@login_required
def new_diagnosis():
    """Record a clinical diagnosis."""
    today_str = datetime.date.today().isoformat()
    # List registered patients if user is clinical staff or let user record for self
    all_users = db.get_all_users()

    if request.method == "POST":
        patient_id = request.form.get("patient_id", "").strip() or g.user["user_id"]
        doctor = request.form.get("doctor", "").strip()
        diagnosis_text = request.form.get("diagnosis", "").strip()
        date = request.form.get("date", "").strip() or today_str

        if not doctor or not diagnosis_text:
            flash("Doctor name and diagnosis details are required.", "danger")
            return render_template("diagnosis.html", today=today_str, all_users=all_users, form=request.form)

        diag_data = {
            "patient_id": patient_id,
            "doctor": doctor,
            "diagnosis": diagnosis_text,
            "date": date
        }

        created = db.create_diagnosis(diag_data)

        # Notify the patient
        target_patient = db.get_user_by_id(patient_id)
        patient_name = target_patient["name"] if target_patient else "Patient"
        sns.send_diagnosis_recorded_notification(
            patient_id=patient_id,
            patient_name=patient_name,
            doctor=doctor,
            diagnosis=diagnosis_text
        )

        flash("Clinical diagnosis record has been logged successfully! Patient notification sent.", "success")
        return redirect(url_for("diagnoses"))

    return render_template("diagnosis.html", today=today_str, all_users=all_users, form={})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
