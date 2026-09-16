"""
MedTrack – AWS Cloud-Enabled Healthcare Management System
Student demonstration web application for AWS Cloud Practitioner / SkillWallet.
"""

import os
import datetime
import functools
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, g, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from config import Config
from services import DatabaseService, SNSService

app = Flask(__name__)
app.config.from_object(Config)

# Session Security Configuration
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False  # Local development; set True over HTTPS in production
)

# Initialize Services
db = DatabaseService()
sns = SNSService()

# -----------------------------------------------------------------
# Authentication & Authorization Helpers
# -----------------------------------------------------------------
@app.before_request
def load_logged_in_user():
    """Load logged-in user from session into request context."""
    user_id = session.get("user_id")
    if user_id is None:
        g.user = None
    else:
        g.user = db.get_user_by_id(user_id)
        if not g.user:
            session.clear()

@app.context_processor
def inject_user_context():
    """Make user authentication state available in all Jinja templates."""
    return {
        "current_user": g.user,
        "is_authenticated": g.user is not None,
        "user_role": g.user.get("role") if g.user else None,
        "mock_aws": Config.MOCK_AWS
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
    """User authentication and role routing."""
    if g.user:
        return redirect(url_for("dashboard") if g.user.get("role") == "patient" else url_for("doctor_dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Please enter both email and password.", "danger")
            return render_template("login.html", form=request.form)

        user = db.get_user_by_email(email)
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email address or password.", "danger")
            return render_template("login.html", form=request.form)

        session.clear()
        session["user_id"] = user["user_id"]
        session["user_name"] = user["name"]
        session["user_role"] = user["role"]

        flash(f"Welcome back, {user['name']}!", "success")
        if user["role"] == "doctor":
            return redirect(url_for("doctor_dashboard"))
        return redirect(url_for("dashboard"))

    return render_template("login.html", form={})

@app.route("/logout")
def logout():
    """Terminate user session."""
    session.clear()
    flash("You have been signed out safely.", "info")
    return redirect(url_for("login"))

@app.route("/demo-login/<role>")
def demo_login(role):
    """Convenience 1-click evaluator login for local testing."""
    if role == "doctor":
        doc = db.get_user_by_email("doctor.vance@medtrack.local")
        if doc:
            session.clear()
            session["user_id"] = doc["user_id"]
            session["user_name"] = doc["name"]
            session["user_role"] = "doctor"
            flash(f"Logged in as Demo Doctor ({doc['name']}).", "success")
            return redirect(url_for("doctor_dashboard"))

    elif role == "patient":
        # Find any patient or create a clean demonstration patient
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

        session.clear()
        session["user_id"] = patient["user_id"]
        session["user_name"] = patient["name"]
        session["user_role"] = "patient"
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
    """Patient dashboard showing appointments, diagnosis records, and notifications."""
    patient_id = g.user["user_id"]
    all_appointments = db.get_appointments_by_patient(patient_id)
    diagnoses = db.get_diagnoses_by_patient(patient_id)
    notifications = db.get_notifications_by_patient(patient_id)

    today = datetime.date.today().isoformat()
    upcoming = [a for a in all_appointments if a.get("appointment_date", "") >= today and a.get("status") != "CANCELLED"]
    past = [a for a in all_appointments if a.get("appointment_date", "") < today or a.get("status") in ("COMPLETED", "CANCELLED")]
    medicine_schedule = db.get_patient_schedule(patient_id)

    return render_template(
        "dashboard.html",
        patient=g.user,
        upcoming_appointments=upcoming,
        past_appointments=past,
        diagnoses=diagnoses,
        notifications=notifications,
        medicine_schedule=medicine_schedule
    )

@app.route("/appointments")
@patient_required
def view_appointments():
    """List all appointments booked by the patient."""
    patient_id = g.user["user_id"]
    appointments = db.get_appointments_by_patient(patient_id)
    return render_template("appointments.html", appointments=appointments)

@app.route("/appointments/new", methods=["GET", "POST"])
@patient_required
def book_appointment():
    """Book a new medical consultation with an attending doctor."""
    doctors = db.get_doctors()

    if request.method == "POST":
        doctor_id = request.form.get("doctor_id", "").strip()
        appointment_date = request.form.get("appointment_date", "").strip()
        appointment_time = request.form.get("appointment_time", "").strip()
        reason = request.form.get("reason", "").strip()

        if not doctor_id or not appointment_date or not appointment_time or not reason:
            flash("All appointment details are required.", "danger")
            return render_template("appointment.html", doctors=doctors, form=request.form)

        # Verify chosen doctor exists
        doctor = db.get_user_by_id(doctor_id)
        if not doctor or doctor.get("role") != "doctor":
            flash("Selected doctor could not be verified.", "danger")
            return render_template("appointment.html", doctors=doctors, form=request.form)

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

    return render_template("appointment.html", doctors=doctors, form={})

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

# -----------------------------------------------------------------
# Doctor Routes
# -----------------------------------------------------------------
@app.route("/doctor/dashboard")
@doctor_required
def doctor_dashboard():
    """Doctor dashboard showing assigned appointments and action controls."""
    doctor_id = g.user["user_id"]
    appointments = db.get_appointments_by_doctor(doctor_id)
    return render_template("doctor_dashboard.html", doctor=g.user, appointments=appointments)

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
        date_str = request.form.get("date", "").strip() or datetime.date.today().isoformat()

        if not diagnosis_text:
            flash("Clinical diagnosis text cannot be blank.", "danger")
            return render_template("diagnosis.html", appointment=appointment, today=datetime.date.today().isoformat())

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

    today = datetime.date.today().isoformat()
    return render_template("diagnosis.html", appointment=appointment, today=today)

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

    if not medicine_id or status not in ("TAKEN", "SKIPPED"):
        flash("Invalid intake logging request.", "danger")
        return redirect(url_for("dashboard"))

    try:
        log_data = db.record_intake(patient_id, medicine_id, status)
        now_time = log_data["taken_time"]

        db.create_notification(
            patient_id=patient_id,
            message=f"Dose of {log_data['medicine_name']} marked as {status} at {now_time}."
        )

        if status == "TAKEN":
            flash(f"Logged dose of {log_data['medicine_name']} ({log_data['dosage']}) as TAKEN at {now_time}.", "success")
        else:
            flash(f"Marked {log_data['medicine_name']} as SKIPPED for today.", "warning")

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
# Entry Point
# -----------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
