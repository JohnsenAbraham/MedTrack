"""
MedTrack Service Abstraction Layer: Database Interface
Seamlessly abstracts between local development (SQLite) and AWS production (DynamoDB).
"""

import sqlite3
import uuid
import datetime
import logging
from contextlib import contextmanager
from werkzeug.security import generate_password_hash
from config import Config

logger = logging.getLogger("services.database")

class DatabaseService:
    """Unified service interface for MedTrack data persistence."""

    def __init__(self):
        self.mock_aws = Config.MOCK_AWS
        if self.mock_aws:
            self.db_path = str(Config.LOCAL_DB_PATH)
            self._init_sqlite()
        else:
            from services.dynamodb_service import DynamoDBService
            self.dynamo = DynamoDBService()

    @contextmanager
    def _get_sqlite_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_sqlite(self):
        """Initialize local SQLite tables matching the DynamoDB schema."""
        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()

            # USERS table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    phone TEXT,
                    date_of_birth TEXT,
                    gender TEXT,
                    role TEXT NOT NULL CHECK(role IN ('patient', 'doctor')),
                    created_at TEXT NOT NULL
                )
            """)

            # APPOINTMENTS table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS appointments (
                    appointment_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    doctor_id TEXT NOT NULL,
                    appointment_date TEXT NOT NULL,
                    appointment_time TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('PENDING', 'CONFIRMED', 'COMPLETED', 'CANCELLED')),
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(patient_id) REFERENCES users(user_id),
                    FOREIGN KEY(doctor_id) REFERENCES users(user_id)
                )
            """)

            # DIAGNOSES table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS diagnoses (
                    diagnosis_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    doctor_id TEXT NOT NULL,
                    diagnosis TEXT NOT NULL,
                    date TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(patient_id) REFERENCES users(user_id),
                    FOREIGN KEY(doctor_id) REFERENCES users(user_id)
                )
            """)

            # NOTIFICATIONS table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notifications (
                    notification_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'UNREAD',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(patient_id) REFERENCES users(user_id)
                )
            """)

            # MEDICINES table (Patient Medication Cabinet)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS medicines (
                    medicine_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    dosage TEXT NOT NULL,
                    schedule_time TEXT NOT NULL,
                    frequency TEXT NOT NULL DEFAULT 'Daily',
                    meal_timing TEXT NOT NULL DEFAULT 'After Food',
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(patient_id) REFERENCES users(user_id)
                )
            """)

            # INTAKE_LOGS table (Patient Daily Intake History)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS intake_logs (
                    log_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    medicine_id TEXT NOT NULL,
                    medicine_name TEXT NOT NULL,
                    dosage TEXT NOT NULL,
                    scheduled_time TEXT NOT NULL,
                    taken_time TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('TAKEN', 'SKIPPED', 'PENDING')),
                    log_date TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(patient_id) REFERENCES users(user_id),
                    FOREIGN KEY(medicine_id) REFERENCES medicines(medicine_id)
                )
            """)
            conn.commit()

        self._seed_demo_doctors()

    def _seed_demo_doctors(self):
        """Seed initial demonstration doctors for local evaluation."""
        demo_doctors = [
            {
                "user_id": "doc-001",
                "name": "Dr. Marcus Vance (Cardiology)",
                "email": "doctor.vance@medtrack.local",
                "password_hash": generate_password_hash("DoctorPass123!"),
                "phone": "+1-555-0101",
                "date_of_birth": "1980-05-12",
                "gender": "Male",
                "role": "doctor",
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            },
            {
                "user_id": "doc-002",
                "name": "Dr. Emily Chen (General Practice)",
                "email": "doctor.chen@medtrack.local",
                "password_hash": generate_password_hash("DoctorPass123!"),
                "phone": "+1-555-0102",
                "date_of_birth": "1985-09-24",
                "gender": "Female",
                "role": "doctor",
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            },
            {
                "user_id": "doc-003",
                "name": "Dr. Priya Nair (General Physician)",
                "email": "doctor.nair@medtrack.local",
                "password_hash": generate_password_hash("DoctorPass123!"),
                "phone": "+1-555-0103",
                "date_of_birth": "1988-11-18",
                "gender": "Female",
                "role": "doctor",
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
        ]

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            for doc in demo_doctors:
                cursor.execute("SELECT user_id FROM users WHERE email = ?", (doc["email"],))
                if not cursor.fetchone():
                    cursor.execute("""
                        INSERT INTO users (user_id, name, email, password_hash, phone, date_of_birth, gender, role, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        doc["user_id"], doc["name"], doc["email"], doc["password_hash"],
                        doc["phone"], doc["date_of_birth"], doc["gender"], doc["role"], doc["created_at"]
                    ))
            conn.commit()

    # -------------------------------------------------------------
    # User Methods
    # -------------------------------------------------------------
    def create_user(self, user_data: dict) -> dict:
        if not self.mock_aws:
            return self.dynamo.create_user(user_data)

        user_id = user_data.get("user_id") or f"usr-{uuid.uuid4().hex[:8]}"
        created_at = user_data.get("created_at") or datetime.datetime.now(datetime.timezone.utc).isoformat()

        record = {
            "user_id": user_id,
            "name": user_data["name"],
            "email": user_data["email"].strip().lower(),
            "password_hash": user_data["password_hash"],
            "phone": user_data.get("phone", ""),
            "date_of_birth": user_data.get("date_of_birth", ""),
            "gender": user_data.get("gender", ""),
            "role": user_data.get("role", "patient"),
            "created_at": created_at
        }

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO users (user_id, name, email, password_hash, phone, date_of_birth, gender, role, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record["user_id"], record["name"], record["email"], record["password_hash"],
                record["phone"], record["date_of_birth"], record["gender"], record["role"], record["created_at"]
            ))
            conn.commit()
        return record

    def get_user_by_id(self, user_id: str):
        if not self.mock_aws:
            return self.dynamo.get_user_by_id(user_id)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_user_by_email(self, email: str):
        if not self.mock_aws:
            return self.dynamo.get_user_by_email(email)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_user(self, user_id: str, update_data: dict) -> bool:
        if not self.mock_aws:
            return self.dynamo.update_user(user_id, update_data)

        fields = []
        params = []
        for key in ("name", "phone", "date_of_birth", "gender"):
            if key in update_data:
                fields.append(f"{key} = ?")
                params.append(update_data[key])

        if not fields:
            return False

        params.append(user_id)
        query = f"UPDATE users SET {', '.join(fields)} WHERE user_id = ?"

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            conn.commit()
            return cursor.rowcount > 0

    def get_doctors(self) -> list:
        if not self.mock_aws:
            return self.dynamo.get_doctors()

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, name, email, phone FROM users WHERE role = 'doctor' ORDER BY name ASC")
            return [dict(row) for row in cursor.fetchall()]

    # -------------------------------------------------------------
    # Appointment Methods
    # -------------------------------------------------------------
    def create_appointment(self, appointment_data: dict) -> dict:
        if not self.mock_aws:
            return self.dynamo.create_appointment(appointment_data)

        appt_id = appointment_data.get("appointment_id") or f"apt-{uuid.uuid4().hex[:8]}"
        created_at = appointment_data.get("created_at") or datetime.datetime.now(datetime.timezone.utc).isoformat()
        status = appointment_data.get("status", "PENDING")

        record = {
            "appointment_id": appt_id,
            "patient_id": appointment_data["patient_id"],
            "doctor_id": appointment_data["doctor_id"],
            "appointment_date": appointment_data["appointment_date"],
            "appointment_time": appointment_data["appointment_time"],
            "reason": appointment_data.get("reason", ""),
            "status": status,
            "created_at": created_at
        }

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO appointments (appointment_id, patient_id, doctor_id, appointment_date, appointment_time, reason, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record["appointment_id"], record["patient_id"], record["doctor_id"],
                record["appointment_date"], record["appointment_time"], record["reason"],
                record["status"], record["created_at"]
            ))
            conn.commit()
        return record

    def get_appointment_by_id(self, appointment_id: str):
        if not self.mock_aws:
            return self.dynamo.get_appointment_by_id(appointment_id)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT a.*, p.name as patient_name, d.name as doctor_name
                FROM appointments a
                LEFT JOIN users p ON a.patient_id = p.user_id
                LEFT JOIN users d ON a.doctor_id = d.user_id
                WHERE a.appointment_id = ?
            """, (appointment_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_appointments_by_patient(self, patient_id: str) -> list:
        if not self.mock_aws:
            return self.dynamo.get_appointments_by_patient(patient_id)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT a.*, d.name as doctor_name
                FROM appointments a
                LEFT JOIN users d ON a.doctor_id = d.user_id
                WHERE a.patient_id = ?
                ORDER BY a.appointment_date DESC, a.appointment_time DESC
            """, (patient_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_appointments_by_doctor(self, doctor_id: str) -> list:
        if not self.mock_aws:
            return self.dynamo.get_appointments_by_doctor(doctor_id)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT a.*, p.name as patient_name, p.phone as patient_phone, p.gender as patient_gender, p.date_of_birth as patient_dob
                FROM appointments a
                LEFT JOIN users p ON a.patient_id = p.user_id
                WHERE a.doctor_id = ?
                ORDER BY a.appointment_date ASC, a.appointment_time ASC
            """, (doctor_id,))
            return [dict(row) for row in cursor.fetchall()]

    def update_appointment_status(self, appointment_id: str, status: str) -> bool:
        if not self.mock_aws:
            return self.dynamo.update_appointment_status(appointment_id, status)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE appointments SET status = ? WHERE appointment_id = ?
            """, (status, appointment_id))
            conn.commit()
            return cursor.rowcount > 0

    # -------------------------------------------------------------
    # Diagnosis Methods
    # -------------------------------------------------------------
    def create_diagnosis(self, diagnosis_data: dict) -> dict:
        if not self.mock_aws:
            return self.dynamo.create_diagnosis(diagnosis_data)

        diag_id = diagnosis_data.get("diagnosis_id") or f"diag-{uuid.uuid4().hex[:8]}"
        created_at = diagnosis_data.get("created_at") or datetime.datetime.now(datetime.timezone.utc).isoformat()
        date_str = diagnosis_data.get("date") or datetime.date.today().isoformat()

        record = {
            "diagnosis_id": diag_id,
            "patient_id": diagnosis_data["patient_id"],
            "doctor_id": diagnosis_data["doctor_id"],
            "diagnosis": diagnosis_data["diagnosis"],
            "date": date_str,
            "created_at": created_at
        }

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO diagnoses (diagnosis_id, patient_id, doctor_id, diagnosis, date, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                record["diagnosis_id"], record["patient_id"], record["doctor_id"],
                record["diagnosis"], record["date"], record["created_at"]
            ))
            conn.commit()
        return record

    def get_diagnoses_by_patient(self, patient_id: str) -> list:
        if not self.mock_aws:
            return self.dynamo.get_diagnoses_by_patient(patient_id)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT dg.*, d.name as doctor_name
                FROM diagnoses dg
                LEFT JOIN users d ON dg.doctor_id = d.user_id
                WHERE dg.patient_id = ?
                ORDER BY dg.date DESC, dg.created_at DESC
            """, (patient_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_diagnoses_by_doctor(self, doctor_id: str) -> list:
        if not self.mock_aws:
            return self.dynamo.get_diagnoses_by_doctor(doctor_id)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT dg.*, p.name as patient_name
                FROM diagnoses dg
                LEFT JOIN users p ON dg.patient_id = p.user_id
                WHERE dg.doctor_id = ?
                ORDER BY dg.date DESC
            """, (doctor_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_patients_by_doctor(self, doctor_id: str) -> list:
        if not self.mock_aws:
            return []

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT u.user_id, u.name, u.email, u.phone, u.date_of_birth, u.gender,
                       COUNT(a.appointment_id) as total_visits,
                       MAX(a.appointment_date) as last_visit
                FROM users u
                JOIN appointments a ON u.user_id = a.patient_id
                WHERE a.doctor_id = ?
                GROUP BY u.user_id
                ORDER BY u.name ASC
            """, (doctor_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_prescriptions_by_doctor(self, doctor_id: str) -> list:
        if not self.mock_aws:
            return []

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT m.*, u.name as patient_name
                FROM medicines m
                JOIN users u ON m.patient_id = u.user_id
                WHERE m.patient_id IN (
                    SELECT DISTINCT patient_id FROM appointments WHERE doctor_id = ?
                )
                ORDER BY m.created_at DESC
            """, (doctor_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_notifications_by_doctor(self, doctor_id: str) -> list:
        if not self.mock_aws:
            return []

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM notifications
                WHERE patient_id = ?
                ORDER BY created_at DESC
            """, (doctor_id,))
            return [dict(row) for row in cursor.fetchall()]

    # -------------------------------------------------------------
    # Notification Methods
    # -------------------------------------------------------------
    def create_notification(self, patient_id: str, message: str) -> dict:
        if not self.mock_aws:
            return self.dynamo.create_notification(patient_id, message)

        notif_id = f"ntf-{uuid.uuid4().hex[:8]}"
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        record = {
            "notification_id": notif_id,
            "patient_id": patient_id,
            "message": message,
            "status": "UNREAD",
            "created_at": created_at
        }

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO notifications (notification_id, patient_id, message, status, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                record["notification_id"], record["patient_id"], record["message"],
                record["status"], record["created_at"]
            ))
            conn.commit()
        return record

    def get_notifications_by_patient(self, patient_id: str) -> list:
        if not self.mock_aws:
            return self.dynamo.get_notifications_by_patient(patient_id)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM notifications
                WHERE patient_id = ?
                ORDER BY created_at DESC
            """, (patient_id,))
            return [dict(row) for row in cursor.fetchall()]

    # -------------------------------------------------------------
    # Patient Medicine Management & Dose Tracking
    # -------------------------------------------------------------
    def create_medicine(self, patient_id: str, name: str, dosage: str, schedule_time: str,
                        frequency: str = "Daily", meal_timing: str = "After Food", notes: str = "") -> dict:
        medicine_id = f"med-{uuid.uuid4().hex[:8]}"
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        record = {
            "medicine_id": medicine_id,
            "patient_id": patient_id,
            "name": name.strip(),
            "dosage": dosage.strip(),
            "schedule_time": schedule_time.strip(),
            "frequency": frequency.strip(),
            "meal_timing": meal_timing.strip(),
            "notes": notes.strip() if notes else "",
            "created_at": created_at
        }

        if not self.mock_aws:
            return self.dynamo.create_medicine(record)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO medicines (medicine_id, patient_id, name, dosage, schedule_time, frequency, meal_timing, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record["medicine_id"], record["patient_id"], record["name"],
                record["dosage"], record["schedule_time"], record["frequency"],
                record["meal_timing"], record["notes"], record["created_at"]
            ))
            conn.commit()
        return record

    def get_medicine_by_id(self, medicine_id: str):
        if not self.mock_aws:
            response = self.dynamo.medicines_table.get_item(Key={"medicine_id": medicine_id})
            return response.get("Item")

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM medicines WHERE medicine_id = ?", (medicine_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_medicines_by_patient(self, patient_id: str) -> list:
        if not self.mock_aws:
            return self.dynamo.get_medicines_by_patient(patient_id)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM medicines WHERE patient_id = ? ORDER BY schedule_time ASC", (patient_id,))
            return [dict(row) for row in cursor.fetchall()]

    def delete_medicine(self, medicine_id: str, patient_id: str) -> bool:
        med = self.get_medicine_by_id(medicine_id)
        if not med or med["patient_id"] != patient_id:
            return False

        if not self.mock_aws:
            return self.dynamo.delete_medicine(medicine_id)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM intake_logs WHERE medicine_id = ? AND patient_id = ?", (medicine_id, patient_id))
            cursor.execute("DELETE FROM medicines WHERE medicine_id = ? AND patient_id = ?", (medicine_id, patient_id))
            conn.commit()
        return True

    def record_intake(self, patient_id: str, medicine_id: str, status: str, log_date: str = None) -> dict:
        if status not in ("TAKEN", "SKIPPED"):
            raise ValueError("Status must be either 'TAKEN' or 'SKIPPED'.")

        med = self.get_medicine_by_id(medicine_id)
        if not med or med["patient_id"] != patient_id:
            raise ValueError("Medication record not found.")

        today_str = log_date or datetime.date.today().isoformat()
        now_time = datetime.datetime.now().strftime("%I:%M %p")
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        log_id = f"log-{uuid.uuid4().hex[:8]}"

        log_data = {
            "log_id": log_id,
            "patient_id": patient_id,
            "medicine_id": medicine_id,
            "medicine_name": med["name"],
            "dosage": med["dosage"],
            "scheduled_time": med["schedule_time"],
            "taken_time": now_time,
            "status": status,
            "log_date": today_str,
            "created_at": created_at
        }

        if not self.mock_aws:
            self.dynamo.create_intake_log(log_data)
            return log_data

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT log_id FROM intake_logs
                WHERE patient_id = ? AND medicine_id = ? AND log_date = ?
            """, (patient_id, medicine_id, today_str))
            existing = cursor.fetchone()

            if existing:
                cursor.execute("""
                    UPDATE intake_logs
                    SET status = ?, taken_time = ?, created_at = ?
                    WHERE log_id = ?
                """, (status, now_time, created_at, existing["log_id"]))
                log_data["log_id"] = existing["log_id"]
            else:
                cursor.execute("""
                    INSERT INTO intake_logs (log_id, patient_id, medicine_id, medicine_name, dosage, scheduled_time, taken_time, status, log_date, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    log_id, patient_id, medicine_id, med["name"], med["dosage"],
                    med["schedule_time"], now_time, status, today_str, created_at
                ))
            conn.commit()
        return log_data

    def get_patient_schedule(self, patient_id: str, target_date: str = None) -> dict:
        today_str = target_date or datetime.date.today().isoformat()
        medicines = self.get_medicines_by_patient(patient_id)

        if not self.mock_aws:
            logs = self.dynamo.get_intake_logs_by_patient(patient_id, log_date=today_str)
        else:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM intake_logs WHERE patient_id = ? AND log_date = ?", (patient_id, today_str))
                logs = [dict(r) for r in cursor.fetchall()]

        logs_by_med = {l["medicine_id"]: l for l in logs}

        doses = []
        taken_count = 0
        skipped_count = 0
        pending_count = 0

        for med in medicines:
            log = logs_by_med.get(med["medicine_id"])
            if log:
                status = log["status"]
                taken_time = log.get("taken_time", "")
                log_id = log["log_id"]
            else:
                status = "PENDING"
                taken_time = ""
                log_id = None

            if status == "TAKEN":
                taken_count += 1
            elif status == "SKIPPED":
                skipped_count += 1
            else:
                pending_count += 1

            doses.append({
                "medicine_id": med["medicine_id"],
                "name": med["name"],
                "dosage": med["dosage"],
                "schedule_time": med["schedule_time"],
                "frequency": med["frequency"],
                "meal_timing": med["meal_timing"],
                "notes": med.get("notes", ""),
                "status": status,
                "taken_time": taken_time,
                "log_id": log_id
            })

        total_doses = len(medicines)
        adherence_pct = round((taken_count / total_doses * 100)) if total_doses > 0 else 100

        return {
            "date": today_str,
            "doses": doses,
            "total_doses": total_doses,
            "taken_count": taken_count,
            "skipped_count": skipped_count,
            "pending_count": pending_count,
            "adherence_pct": adherence_pct
        }

    def get_intake_history(self, patient_id: str, limit: int = 50) -> list:
        if not self.mock_aws:
            logs = self.dynamo.get_intake_logs_by_patient(patient_id)
            return logs[:limit]

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM intake_logs
                WHERE patient_id = ?
                ORDER BY log_date DESC, scheduled_time DESC
                LIMIT ?
            """, (patient_id, limit))
            return [dict(r) for r in cursor.fetchall()]
