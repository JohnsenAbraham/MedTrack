import sqlite3
import datetime
import uuid
import logging
from config import Config

logger = logging.getLogger(__name__)

class DynamoDBService:
    """
    DynamoDB Data Access Service.
    Supports dual execution:
      - MOCK_AWS=True: Uses local SQLite database with identical schema semantics.
      - MOCK_AWS=False: Connects to real AWS DynamoDB via boto3.
    """

    def __init__(self, mock_aws=None):
        self.mock_aws = Config.MOCK_AWS if mock_aws is None else mock_aws
        self.db_path = Config.LOCAL_DB_PATH

        if self.mock_aws:
            logger.info("Initializing DynamoDBService in MOCK mode (SQLite storage: %s)", self.db_path)
            self._init_sqlite()
        else:
            logger.info("Initializing DynamoDBService in LIVE AWS mode (Region: %s)", Config.AWS_REGION)
            import boto3
            boto_kwargs = {"region_name": Config.AWS_REGION}
            if Config.AWS_ACCESS_KEY_ID and Config.AWS_SECRET_ACCESS_KEY:
                boto_kwargs["aws_access_key_id"] = Config.AWS_ACCESS_KEY_ID
                boto_kwargs["aws_secret_access_key"] = Config.AWS_SECRET_ACCESS_KEY

            self.dynamodb = boto3.resource("dynamodb", **boto_kwargs)
            self.users_table = self.dynamodb.Table(Config.DYNAMODB_USERS_TABLE)
            self.appointments_table = self.dynamodb.Table(Config.DYNAMODB_APPOINTMENTS_TABLE)
            self.diagnoses_table = self.dynamodb.Table(Config.DYNAMODB_DIAGNOSES_TABLE)
            self.notifications_table = self.dynamodb.Table(Config.DYNAMODB_NOTIFICATIONS_TABLE)

    # -------------------------------------------------------------
    # MOCK SQLite Helpers
    # -------------------------------------------------------------
    def _get_sqlite_conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_sqlite(self):
        """Create tables for local mock simulation."""
        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            # Users table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    phone TEXT,
                    date_of_birth TEXT,
                    gender TEXT,
                    role TEXT DEFAULT 'patient',
                    created_at TEXT NOT NULL
                )
            """)
            # Appointments table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS appointments (
                    appointment_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    doctor TEXT NOT NULL,
                    date TEXT NOT NULL,
                    time TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (patient_id) REFERENCES users (user_id)
                )
            """)
            # Diagnoses table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS diagnoses (
                    diagnosis_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    doctor TEXT NOT NULL,
                    diagnosis TEXT NOT NULL,
                    date TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (patient_id) REFERENCES users (user_id)
                )
            """)
            # Notifications table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notifications (
                    notification_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (patient_id) REFERENCES users (user_id)
                )
            """)
            conn.commit()

    # -------------------------------------------------------------
    # User Operations
    # -------------------------------------------------------------
    def create_user(self, user_data: dict) -> dict:
        """Create a new user record."""
        if not user_data.get("user_id"):
            user_data["user_id"] = str(uuid.uuid4())
        if not user_data.get("created_at"):
            user_data["created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if not user_data.get("role"):
            user_data["role"] = "patient"

        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO users (user_id, name, email, password_hash, phone, date_of_birth, gender, role, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    user_data["user_id"],
                    user_data["name"],
                    user_data["email"].strip().lower(),
                    user_data["password_hash"],
                    user_data.get("phone", ""),
                    user_data.get("date_of_birth", ""),
                    user_data.get("gender", ""),
                    user_data.get("role", "patient"),
                    user_data["created_at"]
                ))
                conn.commit()
            return user_data
        else:
            user_item = {
                "user_id": user_data["user_id"],
                "name": user_data["name"],
                "email": user_data["email"].strip().lower(),
                "password_hash": user_data["password_hash"],
                "phone": user_data.get("phone", ""),
                "date_of_birth": user_data.get("date_of_birth", ""),
                "gender": user_data.get("gender", ""),
                "role": user_data.get("role", "patient"),
                "created_at": user_data["created_at"],
            }
            self.users_table.put_item(Item=user_item)
            return user_item

    def get_user_by_email(self, email: str) -> dict | None:
        """Retrieve user by email address."""
        email_clean = email.strip().lower()
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE email = ?", (email_clean,))
                row = cursor.fetchone()
                return dict(row) if row else None
        else:
            from boto3.dynamodb.conditions import Key, Attr
            try:
                # Try query on GSI EmailIndex if configured
                response = self.users_table.query(
                    IndexName="EmailIndex",
                    KeyConditionExpression=Key("email").eq(email_clean)
                )
                items = response.get("Items", [])
                if items:
                    return items[0]
            except Exception:
                # Fallback to scan if index not created
                response = self.users_table.scan(
                    FilterExpression=Attr("email").eq(email_clean)
                )
                items = response.get("Items", [])
                if items:
                    return items[0]
            return None

    def get_user_by_id(self, user_id: str) -> dict | None:
        """Retrieve user by user_id primary key."""
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        else:
            response = self.users_table.get_item(Key={"user_id": user_id})
            return response.get("Item")

    def get_all_users(self) -> list:
        """Get all registered users (useful for doctor/staff selection)."""
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id, name, email, role, phone, date_of_birth, gender FROM users ORDER BY name ASC")
                return [dict(row) for row in cursor.fetchall()]
        else:
            response = self.users_table.scan()
            return response.get("Items", [])

    # -------------------------------------------------------------
    # Appointment Operations
    # -------------------------------------------------------------
    def create_appointment(self, appt_data: dict) -> dict:
        """Create a new appointment."""
        if not appt_data.get("appointment_id"):
            appt_data["appointment_id"] = str(uuid.uuid4())
        if not appt_data.get("created_at"):
            appt_data["created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if not appt_data.get("status"):
            appt_data["status"] = "Confirmed"

        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO appointments (appointment_id, patient_id, doctor, date, time, reason, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    appt_data["appointment_id"],
                    appt_data["patient_id"],
                    appt_data["doctor"],
                    appt_data["date"],
                    appt_data["time"],
                    appt_data["reason"],
                    appt_data["status"],
                    appt_data["created_at"]
                ))
                conn.commit()
            return appt_data
        else:
            self.appointments_table.put_item(Item=appt_data)
            return appt_data

    def get_appointments_by_patient(self, patient_id: str) -> list:
        """Get all appointments for a patient sorted by date and time descending."""
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM appointments
                    WHERE patient_id = ?
                    ORDER BY date DESC, time DESC
                """, (patient_id,))
                return [dict(row) for row in cursor.fetchall()]
        else:
            from boto3.dynamodb.conditions import Key, Attr
            try:
                response = self.appointments_table.query(
                    IndexName="PatientIndex",
                    KeyConditionExpression=Key("patient_id").eq(patient_id)
                )
                items = response.get("Items", [])
            except Exception:
                response = self.appointments_table.scan(
                    FilterExpression=Attr("patient_id").eq(patient_id)
                )
                items = response.get("Items", [])
            items.sort(key=lambda x: (x.get("date", ""), x.get("time", "")), reverse=True)
            return items

    def get_appointment_by_id(self, appointment_id: str) -> dict | None:
        """Retrieve single appointment by appointment_id."""
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM appointments WHERE appointment_id = ?", (appointment_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        else:
            response = self.appointments_table.get_item(Key={"appointment_id": appointment_id})
            return response.get("Item")

    def cancel_appointment(self, appointment_id: str, patient_id: str | None = None) -> bool:
        """Cancel an appointment."""
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                if patient_id:
                    cursor.execute("""
                        UPDATE appointments
                        SET status = 'Cancelled'
                        WHERE appointment_id = ? AND patient_id = ?
                    """, (appointment_id, patient_id))
                else:
                    cursor.execute("""
                        UPDATE appointments
                        SET status = 'Cancelled'
                        WHERE appointment_id = ?
                    """, (appointment_id,))
                conn.commit()
                return cursor.rowcount > 0
        else:
            try:
                self.appointments_table.update_item(
                    Key={"appointment_id": appointment_id},
                    UpdateExpression="SET #s = :status",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":status": "Cancelled"}
                )
                return True
            except Exception as e:
                logger.error("Failed to cancel DynamoDB appointment: %s", e)
                return False

    def get_all_appointments(self) -> list:
        """Get all appointments across all patients (for clinical view)."""
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT a.*, u.name as patient_name, u.email as patient_email
                    FROM appointments a
                    JOIN users u ON a.patient_id = u.user_id
                    ORDER BY a.date DESC, a.time DESC
                """)
                return [dict(row) for row in cursor.fetchall()]
        else:
            response = self.appointments_table.scan()
            items = response.get("Items", [])
            items.sort(key=lambda x: (x.get("date", ""), x.get("time", "")), reverse=True)
            return items

    # -------------------------------------------------------------
    # Diagnosis Operations
    # -------------------------------------------------------------
    def create_diagnosis(self, diag_data: dict) -> dict:
        """Create a new clinical diagnosis record."""
        if not diag_data.get("diagnosis_id"):
            diag_data["diagnosis_id"] = str(uuid.uuid4())
        if not diag_data.get("created_at"):
            diag_data["created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO diagnoses (diagnosis_id, patient_id, doctor, diagnosis, date, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    diag_data["diagnosis_id"],
                    diag_data["patient_id"],
                    diag_data["doctor"],
                    diag_data["diagnosis"],
                    diag_data["date"],
                    diag_data["created_at"]
                ))
                conn.commit()
            return diag_data
        else:
            self.diagnoses_table.put_item(Item=diag_data)
            return diag_data

    def get_diagnoses_by_patient(self, patient_id: str) -> list:
        """Retrieve all diagnoses recorded for a specific patient."""
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM diagnoses
                    WHERE patient_id = ?
                    ORDER BY date DESC, created_at DESC
                """, (patient_id,))
                return [dict(row) for row in cursor.fetchall()]
        else:
            from boto3.dynamodb.conditions import Key, Attr
            try:
                response = self.diagnoses_table.query(
                    IndexName="PatientIndex",
                    KeyConditionExpression=Key("patient_id").eq(patient_id)
                )
                items = response.get("Items", [])
            except Exception:
                response = self.diagnoses_table.scan(
                    FilterExpression=Attr("patient_id").eq(patient_id)
                )
                items = response.get("Items", [])
            items.sort(key=lambda x: x.get("date", ""), reverse=True)
            return items

    def get_all_diagnoses(self) -> list:
        """Retrieve all diagnoses across all patients."""
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT d.*, u.name as patient_name, u.email as patient_email
                    FROM diagnoses d
                    JOIN users u ON d.patient_id = u.user_id
                    ORDER BY d.date DESC, d.created_at DESC
                """)
                return [dict(row) for row in cursor.fetchall()]
        else:
            response = self.diagnoses_table.scan()
            items = response.get("Items", [])
            items.sort(key=lambda x: x.get("date", ""), reverse=True)
            return items

    # -------------------------------------------------------------
    # Notification Operations
    # -------------------------------------------------------------
    def create_notification(self, notif_data: dict) -> dict:
        """Store notification record."""
        if not notif_data.get("notification_id"):
            notif_data["notification_id"] = str(uuid.uuid4())
        if not notif_data.get("created_at"):
            notif_data["created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if not notif_data.get("status"):
            notif_data["status"] = "MOCK_DISPATCHED" if self.mock_aws else "SENT"

        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO notifications (notification_id, patient_id, message, status, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    notif_data["notification_id"],
                    notif_data["patient_id"],
                    notif_data["message"],
                    notif_data["status"],
                    notif_data["created_at"]
                ))
                conn.commit()
            return notif_data
        else:
            self.notifications_table.put_item(Item=notif_data)
            return notif_data

    def get_notifications_by_patient(self, patient_id: str, limit: int = 10) -> list:
        """Retrieve recent notifications for patient."""
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM notifications
                    WHERE patient_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (patient_id, limit))
                return [dict(row) for row in cursor.fetchall()]
        else:
            from boto3.dynamodb.conditions import Key, Attr
            try:
                response = self.notifications_table.query(
                    IndexName="PatientIndex",
                    KeyConditionExpression=Key("patient_id").eq(patient_id)
                )
                items = response.get("Items", [])
            except Exception:
                response = self.notifications_table.scan(
                    FilterExpression=Attr("patient_id").eq(patient_id)
                )
                items = response.get("Items", [])
            items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return items[:limit]
