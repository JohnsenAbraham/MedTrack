"""
MedTrack Service Abstraction Layer: Database Interface
Seamlessly abstracts between local development (SQLite) and AWS production (DynamoDB).
"""

import sqlite3
import uuid
import datetime
import logging
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

    def _get_sqlite_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

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
