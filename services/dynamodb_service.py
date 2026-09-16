import sqlite3
import datetime
import uuid
import logging
import json
import time
from config import Config

logger = logging.getLogger(__name__)

# Standard ICD-10 Reference for Hospital Consultation
ICD10_REFERENCE = [
    {"code": "I10", "name": "Essential (primary) hypertension", "department": "Cardiology"},
    {"code": "I25.10", "name": "Atherosclerotic heart disease", "department": "Cardiology"},
    {"code": "E11.9", "name": "Type 2 diabetes mellitus without complications", "department": "Internal Medicine"},
    {"code": "J45.909", "name": "Unspecified asthma, uncomplicated", "department": "Pulmonology"},
    {"code": "G43.909", "name": "Migraine, unspecified, not intractable", "department": "Neurology"},
    {"code": "M54.5", "name": "Low back pain", "department": "Orthopedics"},
    {"code": "L20.9", "name": "Atopic dermatitis, unspecified", "department": "Dermatology"},
    {"code": "K21.9", "name": "Gastro-esophageal reflux disease without esophagitis", "department": "Gastroenterology"},
    {"code": "J06.9", "name": "Acute upper respiratory infection, unspecified", "department": "General Medicine"},
    {"code": "Z00.00", "name": "Encounter for general adult medical examination", "department": "General Medicine"}
]

class DynamoDBService:
    """
    Enterprise-Grade DynamoDB & Local Data Access Service.
    Supports dual execution:
      - MOCK_AWS=True: Uses local SQLite database with identical schema semantics,
                       audit logging, and clinical vitals/prescriptions.
      - MOCK_AWS=False: Connects to AWS DynamoDB via boto3 with exponential backoff retries.
    """

    def __init__(self, mock_aws=None):
        self.mock_aws = Config.MOCK_AWS if mock_aws is None else mock_aws
        self.db_path = Config.LOCAL_DB_PATH

        if self.mock_aws:
            logger.info("Initializing DynamoDBService in MOCK mode (SQLite storage: %s)", self.db_path)
            self._init_sqlite()
            self.seed_hospital_demo_data()
        else:
            logger.info("Initializing DynamoDBService in LIVE AWS mode (Region: %s)", Config.AWS_REGION)
            import boto3
            from botocore.config import Config as BotoConfig
            boto_kwargs = {
                "region_name": Config.AWS_REGION,
                "config": BotoConfig(
                    retries={"max_attempts": 5, "mode": "adaptive"},
                    connect_timeout=5,
                    read_timeout=10
                )
            }
            if Config.AWS_ACCESS_KEY_ID and Config.AWS_SECRET_ACCESS_KEY:
                boto_kwargs["aws_access_key_id"] = Config.AWS_ACCESS_KEY_ID
                boto_kwargs["aws_secret_access_key"] = Config.AWS_SECRET_ACCESS_KEY

            self.dynamodb = boto3.resource("dynamodb", **boto_kwargs)
            self.users_table = self.dynamodb.Table(Config.DYNAMODB_USERS_TABLE)
            self.appointments_table = self.dynamodb.Table(Config.DYNAMODB_APPOINTMENTS_TABLE)
            self.diagnoses_table = self.dynamodb.Table(Config.DYNAMODB_DIAGNOSES_TABLE)
            self.notifications_table = self.dynamodb.Table(Config.DYNAMODB_NOTIFICATIONS_TABLE)

    # -------------------------------------------------------------
    # Resilience & Retry Helper
    # -------------------------------------------------------------
    def _execute_with_retry(self, operation, max_retries=3, base_delay=0.2):
        """Execute a callable with exponential backoff and jitter."""
        retries = 0
        while True:
            try:
                return operation()
            except Exception as e:
                retries += 1
                if retries >= max_retries:
                    logger.error("Operation failed after %d retries: %s", max_retries, e)
                    raise
                sleep_time = base_delay * (2 ** (retries - 1))
                logger.warning("Transient error: %s. Retrying in %.2fs (attempt %d/%d)", e, sleep_time, retries, max_retries)
                time.sleep(sleep_time)

    # -------------------------------------------------------------
    # MOCK SQLite Helpers & Migrations
    # -------------------------------------------------------------
    def _get_sqlite_conn(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_sqlite(self):
        """Create tables and apply non-destructive column migrations."""
        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()

            # 1. Users table (Patients, Doctors, Admins)
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
                    auth_provider TEXT DEFAULT 'local',
                    specialty TEXT DEFAULT '',
                    license_number TEXT DEFAULT '',
                    department TEXT DEFAULT 'General Medicine',
                    room TEXT DEFAULT '',
                    blood_group TEXT DEFAULT 'O+',
                    allergies TEXT DEFAULT 'None',
                    emergency_contact TEXT DEFAULT '',
                    insurance_provider TEXT DEFAULT 'Standard Health Coverage',
                    created_at TEXT NOT NULL
                )
            """)

            # Add columns if migrating from older schema
            user_cols = [
                ("specialty", "TEXT DEFAULT ''"),
                ("license_number", "TEXT DEFAULT ''"),
                ("department", "TEXT DEFAULT 'General Medicine'"),
                ("room", "TEXT DEFAULT ''"),
                ("blood_group", "TEXT DEFAULT 'O+'"),
                ("allergies", "TEXT DEFAULT 'None'"),
                ("emergency_contact", "TEXT DEFAULT ''"),
                ("insurance_provider", "TEXT DEFAULT 'Standard Health Coverage'"),
                ("auth_provider", "TEXT DEFAULT 'local'")
            ]
            for col, col_type in user_cols:
                try:
                    cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass

            # 2. Appointments table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS appointments (
                    appointment_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    doctor_id TEXT DEFAULT '',
                    doctor TEXT NOT NULL,
                    department TEXT DEFAULT 'General Medicine',
                    date TEXT NOT NULL,
                    time TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'Confirmed',
                    notes TEXT DEFAULT '',
                    vitals_recorded INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT DEFAULT '',
                    FOREIGN KEY (patient_id) REFERENCES users (user_id)
                )
            """)

            appt_cols = [
                ("doctor_id", "TEXT DEFAULT ''"),
                ("department", "TEXT DEFAULT 'General Medicine'"),
                ("notes", "TEXT DEFAULT ''"),
                ("vitals_recorded", "INTEGER DEFAULT 0"),
                ("updated_at", "TEXT DEFAULT ''")
            ]
            for col, col_type in appt_cols:
                try:
                    cursor.execute(f"ALTER TABLE appointments ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass

            # 3. Diagnoses / Clinical Records table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS diagnoses (
                    diagnosis_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    doctor_id TEXT DEFAULT '',
                    doctor TEXT NOT NULL,
                    diagnosis TEXT NOT NULL,
                    icd10_code TEXT DEFAULT '',
                    symptoms TEXT DEFAULT '',
                    vitals_json TEXT DEFAULT '{}',
                    prescriptions_json TEXT DEFAULT '[]',
                    treatment_plan TEXT DEFAULT '',
                    follow_up_date TEXT DEFAULT '',
                    date TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (patient_id) REFERENCES users (user_id)
                )
            """)

            diag_cols = [
                ("doctor_id", "TEXT DEFAULT ''"),
                ("icd10_code", "TEXT DEFAULT ''"),
                ("symptoms", "TEXT DEFAULT ''"),
                ("vitals_json", "TEXT DEFAULT '{}'"),
                ("prescriptions_json", "TEXT DEFAULT '[]'"),
                ("treatment_plan", "TEXT DEFAULT ''"),
                ("follow_up_date", "TEXT DEFAULT ''")
            ]
            for col, col_type in diag_cols:
                try:
                    cursor.execute(f"ALTER TABLE diagnoses ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass

            # 4. Notifications table
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

            # 5. HIPAA Compliance Audit Logs table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    log_id TEXT PRIMARY KEY,
                    actor_id TEXT NOT NULL,
                    actor_name TEXT NOT NULL,
                    actor_role TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target_resource TEXT NOT NULL,
                    ip_address TEXT NOT NULL,
                    status TEXT DEFAULT 'SUCCESS',
                    created_at TEXT NOT NULL
                )
            """)

            # 6. Longitudinal Vitals History table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS vitals_history (
                    vital_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    recorded_by TEXT NOT NULL,
                    blood_pressure TEXT NOT NULL,
                    heart_rate INTEGER,
                    temperature REAL,
                    spo2 INTEGER,
                    blood_sugar INTEGER,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY (patient_id) REFERENCES users (user_id)
                )
            """)

            conn.commit()

    # -------------------------------------------------------------
    # Seeding Realistic Clinical Hospital Demo Data
    # -------------------------------------------------------------
    def seed_hospital_demo_data(self):
        """Seed realistic hospital records if database has no doctors or clinical records."""
        from werkzeug.security import generate_password_hash
        default_pwd = generate_password_hash("MedTrack2026!")

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'doctor'")
            doc_count = cursor.fetchone()[0]

            if doc_count > 0:
                return  # Already seeded

            logger.info("Seeding realistic clinical hospital demo accounts and longitudinal records...")

            # 1. Attending Physicians
            doctors = [
                {
                    "user_id": "doc-jenkins-01",
                    "name": "Dr. Sarah Jenkins, MD",
                    "email": "dr.jenkins@medtrack.health",
                    "password_hash": default_pwd,
                    "phone": "+1 (555) 432-1001",
                    "role": "doctor",
                    "specialty": "Cardiology & Cardiovascular Medicine",
                    "department": "Cardiology",
                    "license_number": "MED-NY-849201",
                    "room": "Suite 402 - Heart Center",
                    "gender": "Female",
                    "created_at": "2026-01-10T08:00:00Z"
                },
                {
                    "user_id": "doc-thorne-02",
                    "name": "Dr. Mark Thorne, MD",
                    "email": "dr.thorne@medtrack.health",
                    "password_hash": default_pwd,
                    "phone": "+1 (555) 432-1002",
                    "role": "doctor",
                    "specialty": "Neurology & Spine Care",
                    "department": "Neurology",
                    "license_number": "MED-NY-773419",
                    "room": "Suite 310 - Neuro Pavilion",
                    "gender": "Male",
                    "created_at": "2026-01-11T08:00:00Z"
                },
                {
                    "user_id": "doc-sharma-03",
                    "name": "Dr. Priya Sharma, MD",
                    "email": "dr.sharma@medtrack.health",
                    "password_hash": default_pwd,
                    "phone": "+1 (555) 432-1003",
                    "role": "doctor",
                    "specialty": "Internal Medicine & Chronic Disease",
                    "department": "Internal Medicine",
                    "license_number": "MED-NY-652093",
                    "room": "Suite 205 - Outpatient Wing",
                    "gender": "Female",
                    "created_at": "2026-01-12T08:00:00Z"
                }
            ]

            for d in doctors:
                cursor.execute("""
                    INSERT OR IGNORE INTO users (user_id, name, email, password_hash, phone, role, specialty, department, license_number, room, gender, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    d["user_id"], d["name"], d["email"], d["password_hash"], d["phone"],
                    d["role"], d["specialty"], d["department"], d["license_number"], d["room"],
                    d["gender"], d["created_at"]
                ))

            # 2. Chief Medical Officer / Admin
            admin = {
                "user_id": "admin-cmo-01",
                "name": "Dr. Arthur Vance, CMO",
                "email": "admin@medtrack.health",
                "password_hash": default_pwd,
                "phone": "+1 (555) 432-0000",
                "role": "admin",
                "specialty": "Healthcare Administration & Clinical Governance",
                "department": "Executive Medical Board",
                "license_number": "MED-ADMIN-0001",
                "room": "Executive Tower - Suite 900",
                "gender": "Male",
                "created_at": "2026-01-01T08:00:00Z"
            }
            cursor.execute("""
                INSERT OR IGNORE INTO users (user_id, name, email, password_hash, phone, role, specialty, department, license_number, room, gender, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                admin["user_id"], admin["name"], admin["email"], admin["password_hash"], admin["phone"],
                admin["role"], admin["specialty"], admin["department"], admin["license_number"], admin["room"],
                admin["gender"], admin["created_at"]
            ))

            # 3. Demo Patient: Jane Doe
            patient = {
                "user_id": "patient-jane-doe",
                "name": "Jane Doe",
                "email": "jane.doe@example.com",
                "password_hash": default_pwd,
                "phone": "+1 (555) 234-5678",
                "date_of_birth": "1992-06-14",
                "gender": "Female",
                "role": "patient",
                "blood_group": "A+",
                "allergies": "Penicillin (Moderate rash), Shellfish",
                "emergency_contact": "Robert Doe (Spouse) - +1 (555) 987-6543",
                "insurance_provider": "BlueCross Shield Premier PPO #BCS-948201",
                "created_at": "2026-02-01T09:00:00Z"
            }
            cursor.execute("""
                INSERT OR IGNORE INTO users (user_id, name, email, password_hash, phone, date_of_birth, gender, role, blood_group, allergies, emergency_contact, insurance_provider, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                patient["user_id"], patient["name"], patient["email"], patient["password_hash"],
                patient["phone"], patient["date_of_birth"], patient["gender"], patient["role"],
                patient["blood_group"], patient["allergies"], patient["emergency_contact"],
                patient["insurance_provider"], patient["created_at"]
            ))

            # 4. Demo Appointments
            today_str = datetime.date.today().isoformat()
            tomorrow_str = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
            next_week_str = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()
            past_date_str = (datetime.date.today() - datetime.timedelta(days=14)).isoformat()

            appts = [
                {
                    "appointment_id": "appt-demo-01",
                    "patient_id": "patient-jane-doe",
                    "doctor_id": "doc-jenkins-01",
                    "doctor": "Dr. Sarah Jenkins, MD",
                    "department": "Cardiology",
                    "date": today_str,
                    "time": "10:00 AM",
                    "reason": "Cardiac stress evaluation & recurrent palpitations",
                    "status": "Checked-In",
                    "notes": "Patient arrived at reception. Vitals ready for intake.",
                    "created_at": f"{today_str}T08:30:00Z"
                },
                {
                    "appointment_id": "appt-demo-02",
                    "patient_id": "patient-jane-doe",
                    "doctor_id": "doc-thorne-02",
                    "doctor": "Dr. Mark Thorne, MD",
                    "department": "Neurology",
                    "date": tomorrow_str,
                    "time": "02:30 PM",
                    "reason": "Follow-up consultation for ocular migraine",
                    "status": "Confirmed",
                    "notes": "Bring previous MRI reports.",
                    "created_at": f"{today_str}T09:15:00Z"
                },
                {
                    "appointment_id": "appt-demo-03",
                    "patient_id": "patient-jane-doe",
                    "doctor_id": "doc-sharma-03",
                    "doctor": "Dr. Priya Sharma, MD",
                    "department": "Internal Medicine",
                    "date": past_date_str,
                    "time": "11:15 AM",
                    "reason": "Routine biannual metabolic and wellness screening",
                    "status": "Completed",
                    "notes": "Consultation concluded. Blood panel ordered.",
                    "created_at": f"{past_date_str}T08:00:00Z"
                }
            ]

            for a in appts:
                cursor.execute("""
                    INSERT OR IGNORE INTO appointments (appointment_id, patient_id, doctor_id, doctor, department, date, time, reason, status, notes, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    a["appointment_id"], a["patient_id"], a["doctor_id"], a["doctor"], a["department"],
                    a["date"], a["time"], a["reason"], a["status"], a["notes"], a["created_at"]
                ))

            # 5. Longitudinal Diagnoses & Prescriptions
            vitals_sample = {
                "blood_pressure": "124/82 mmHg",
                "heart_rate": 74,
                "temperature": 98.6,
                "spo2": 99,
                "blood_sugar": 96
            }
            prescriptions_sample = [
                {
                    "medication": "Amlodipine Besylate",
                    "dosage": "5 mg",
                    "frequency": "Once daily in the morning",
                    "duration": "90 days",
                    "notes": "For blood pressure optimization. Take with food."
                },
                {
                    "medication": "Coenzyme Q10",
                    "dosage": "100 mg",
                    "frequency": "Once daily",
                    "duration": "60 days",
                    "notes": "Cardiovascular cellular energy supplement."
                }
            ]

            cursor.execute("""
                INSERT OR IGNORE INTO diagnoses (diagnosis_id, patient_id, doctor_id, doctor, diagnosis, icd10_code, symptoms, vitals_json, prescriptions_json, treatment_plan, follow_up_date, date, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "diag-demo-01",
                "patient-jane-doe",
                "doc-jenkins-01",
                "Dr. Sarah Jenkins, MD",
                "Borderline Essential Hypertension with episodic sinus tachycardia during exertion.",
                "I10",
                "Palpitations after climbing stairs, mild tension headaches in late afternoons.",
                json.dumps(vitals_sample),
                json.dumps(prescriptions_sample),
                "Low-sodium Mediterranean diet, 30 min daily walking, hydration, 24-hr Holter monitor follow-up.",
                next_week_str,
                past_date_str,
                f"{past_date_str}T11:45:00Z"
            ))

            # 6. Longitudinal Vitals
            cursor.execute("""
                INSERT OR IGNORE INTO vitals_history (vital_id, patient_id, recorded_by, blood_pressure, heart_rate, temperature, spo2, blood_sugar, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "vital-demo-01",
                "patient-jane-doe",
                "Dr. Sarah Jenkins, MD",
                "124/82",
                74,
                98.6,
                99,
                96,
                f"{past_date_str}T11:20:00Z"
            ))

            # 7. Initial Audit Log
            cursor.execute("""
                INSERT OR IGNORE INTO audit_logs (log_id, actor_id, actor_name, actor_role, action, target_resource, ip_address, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "audit-init-01",
                "system",
                "MedTrack Cloud Core",
                "system",
                "SEED_DEMO_RECORDS",
                "Hospital System Bootstrap",
                "127.0.0.1",
                "SUCCESS",
                datetime.datetime.now(datetime.timezone.utc).isoformat()
            ))

            conn.commit()
            logger.info("Demo data seeding completed successfully.")

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
        if not user_data.get("auth_provider"):
            user_data["auth_provider"] = "local"
        if not user_data.get("department"):
            user_data["department"] = "General Medicine"

        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO users (
                        user_id, name, email, password_hash, phone, date_of_birth, gender,
                        role, auth_provider, specialty, license_number, department, room,
                        blood_group, allergies, emergency_contact, insurance_provider, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    user_data["user_id"],
                    user_data["name"],
                    user_data["email"].strip().lower(),
                    user_data["password_hash"],
                    user_data.get("phone", ""),
                    user_data.get("date_of_birth", ""),
                    user_data.get("gender", ""),
                    user_data.get("role", "patient"),
                    user_data.get("auth_provider", "local"),
                    user_data.get("specialty", ""),
                    user_data.get("license_number", ""),
                    user_data.get("department", "General Medicine"),
                    user_data.get("room", ""),
                    user_data.get("blood_group", "O+"),
                    user_data.get("allergies", "None"),
                    user_data.get("emergency_contact", ""),
                    user_data.get("insurance_provider", "Standard Health Coverage"),
                    user_data["created_at"]
                ))
                conn.commit()
            return user_data
        else:
            def _put():
                self.users_table.put_item(Item=user_data)
            self._execute_with_retry(_put)
            return user_data

    def update_user_password(self, user_id: str, new_password_hash: str) -> bool:
        """Update password hash for a registered user."""
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET password_hash = ? WHERE user_id = ?", (new_password_hash, user_id))
                conn.commit()
                return cursor.rowcount > 0
        else:
            def _update():
                self.users_table.update_item(
                    Key={"user_id": user_id},
                    UpdateExpression="SET password_hash = :p",
                    ExpressionAttributeValues={":p": new_password_hash}
                )
                return True
            try:
                return self._execute_with_retry(_update)
            except Exception as e:
                logger.error("Failed to update DynamoDB password: %s", e)
                return False

    def delete_user_by_email(self, email: str) -> bool:
        """Delete a user by email."""
        email_clean = email.strip().lower()
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM users WHERE email = ?", (email_clean,))
                conn.commit()
                return cursor.rowcount > 0
        else:
            user = self.get_user_by_email(email_clean)
            if user:
                def _del():
                    self.users_table.delete_item(Key={"user_id": user["user_id"]})
                self._execute_with_retry(_del)
                return True
            return False

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
                response = self.users_table.query(
                    IndexName="EmailIndex",
                    KeyConditionExpression=Key("email").eq(email_clean)
                )
                items = response.get("Items", [])
                if items:
                    return items[0]
            except Exception:
                response = self.users_table.scan(FilterExpression=Attr("email").eq(email_clean))
                items = response.get("Items", [])
                if items:
                    return items[0]
            return None

    def get_user_by_id(self, user_id: str) -> dict | None:
        """Retrieve user by primary key."""
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        else:
            def _get():
                response = self.users_table.get_item(Key={"user_id": user_id})
                return response.get("Item")
            return self._execute_with_retry(_get)

    def get_all_users(self, role: str | None = None) -> list:
        """Get all registered users, optionally filtered by role."""
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                if role:
                    cursor.execute("SELECT * FROM users WHERE role = ? ORDER BY name ASC", (role,))
                else:
                    cursor.execute("SELECT * FROM users ORDER BY name ASC")
                return [dict(row) for row in cursor.fetchall()]
        else:
            from boto3.dynamodb.conditions import Attr
            if role:
                response = self.users_table.scan(FilterExpression=Attr("role").eq(role))
            else:
                response = self.users_table.scan()
            return response.get("Items", [])

    def get_all_doctors(self) -> list:
        """Get all verified clinical doctors."""
        return self.get_all_users(role="doctor")

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
        if not appt_data.get("department"):
            appt_data["department"] = "General Medicine"

        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO appointments (
                        appointment_id, patient_id, doctor_id, doctor, department,
                        date, time, reason, status, notes, vitals_recorded, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    appt_data["appointment_id"],
                    appt_data["patient_id"],
                    appt_data.get("doctor_id", ""),
                    appt_data["doctor"],
                    appt_data.get("department", "General Medicine"),
                    appt_data["date"],
                    appt_data["time"],
                    appt_data["reason"],
                    appt_data["status"],
                    appt_data.get("notes", ""),
                    appt_data.get("vitals_recorded", 0),
                    appt_data["created_at"],
                    appt_data.get("updated_at", appt_data["created_at"])
                ))
                conn.commit()
            return appt_data
        else:
            def _put():
                self.appointments_table.put_item(Item=appt_data)
            self._execute_with_retry(_put)
            return appt_data

    def get_appointments_by_patient(self, patient_id: str) -> list:
        """Get all appointments for a patient sorted by date and time descending."""
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT a.*, u.name as doctor_name, u.room as doctor_room
                    FROM appointments a
                    LEFT JOIN users u ON a.doctor_id = u.user_id
                    WHERE a.patient_id = ?
                    ORDER BY a.date DESC, a.time DESC
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
                response = self.appointments_table.scan(FilterExpression=Attr("patient_id").eq(patient_id))
                items = response.get("Items", [])
            items.sort(key=lambda x: (x.get("date", ""), x.get("time", "")), reverse=True)
            return items

    def get_appointments_by_doctor(self, doctor_id: str = "", doctor_name: str = "") -> list:
        """Retrieve appointments assigned to a specific doctor or all active queue."""
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                if doctor_id:
                    cursor.execute("""
                        SELECT a.*, u.name as patient_name, u.email as patient_email, u.phone as patient_phone,
                               u.gender as patient_gender, u.date_of_birth as patient_dob, u.blood_group as patient_blood
                        FROM appointments a
                        JOIN users u ON a.patient_id = u.user_id
                        WHERE a.doctor_id = ? OR a.doctor LIKE ?
                        ORDER BY a.date ASC, a.time ASC
                    """, (doctor_id, f"%{doctor_name}%" if doctor_name else doctor_id))
                else:
                    cursor.execute("""
                        SELECT a.*, u.name as patient_name, u.email as patient_email, u.phone as patient_phone,
                               u.gender as patient_gender, u.date_of_birth as patient_dob, u.blood_group as patient_blood
                        FROM appointments a
                        JOIN users u ON a.patient_id = u.user_id
                        ORDER BY a.date ASC, a.time ASC
                    """)
                return [dict(row) for row in cursor.fetchall()]
        else:
            all_appts = self.get_all_appointments()
            if doctor_id or doctor_name:
                return [a for a in all_appts if a.get("doctor_id") == doctor_id or doctor_name in a.get("doctor", "")]
            return all_appts

    def get_appointment_by_id(self, appointment_id: str) -> dict | None:
        """Retrieve single appointment by ID."""
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT a.*, u.name as patient_name, u.email as patient_email, u.phone as patient_phone,
                           u.date_of_birth as patient_dob, u.gender as patient_gender,
                           u.blood_group as patient_blood, u.allergies as patient_allergies
                    FROM appointments a
                    JOIN users u ON a.patient_id = u.user_id
                    WHERE a.appointment_id = ?
                """, (appointment_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        else:
            def _get():
                response = self.appointments_table.get_item(Key={"appointment_id": appointment_id})
                return response.get("Item")
            return self._execute_with_retry(_get)

    def update_appointment_status(self, appointment_id: str, new_status: str, notes: str = "") -> bool:
        """Update appointment lifecycle state (Confirmed, Checked-In, In-Consultation, Completed, Cancelled)."""
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                if notes:
                    cursor.execute("""
                        UPDATE appointments
                        SET status = ?, notes = ?, updated_at = ?
                        WHERE appointment_id = ?
                    """, (new_status, notes, now_str, appointment_id))
                else:
                    cursor.execute("""
                        UPDATE appointments
                        SET status = ?, updated_at = ?
                        WHERE appointment_id = ?
                    """, (new_status, now_str, appointment_id))
                conn.commit()
                return cursor.rowcount > 0
        else:
            def _update():
                self.appointments_table.update_item(
                    Key={"appointment_id": appointment_id},
                    UpdateExpression="SET #s = :s, updated_at = :u",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":s": new_status, ":u": now_str}
                )
                return True
            try:
                return self._execute_with_retry(_update)
            except Exception as e:
                logger.error("Failed to update appointment status: %s", e)
                return False

    def cancel_appointment(self, appointment_id: str, patient_id: str | None = None) -> bool:
        """Cancel an appointment."""
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                if patient_id:
                    cursor.execute("""
                        UPDATE appointments
                        SET status = 'Cancelled', updated_at = ?
                        WHERE appointment_id = ? AND patient_id = ?
                    """, (datetime.datetime.now(datetime.timezone.utc).isoformat(), appointment_id, patient_id))
                else:
                    cursor.execute("""
                        UPDATE appointments
                        SET status = 'Cancelled', updated_at = ?
                        WHERE appointment_id = ?
                    """, (datetime.datetime.now(datetime.timezone.utc).isoformat(), appointment_id))
                conn.commit()
                return cursor.rowcount > 0
        else:
            return self.update_appointment_status(appointment_id, "Cancelled")

    def get_all_appointments(self) -> list:
        """Get all appointments across all patients."""
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
    # Diagnosis & Longitudinal Clinical Records
    # -------------------------------------------------------------
    def create_diagnosis(self, diag_data: dict) -> dict:
        """Create a new clinical diagnosis and treatment record."""
        if not diag_data.get("diagnosis_id"):
            diag_data["diagnosis_id"] = str(uuid.uuid4())
        if not diag_data.get("created_at"):
            diag_data["created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Handle vitals and prescriptions JSON
        vitals_json = diag_data.get("vitals_json")
        if isinstance(vitals_json, dict):
            vitals_str = json.dumps(vitals_json)
        elif isinstance(vitals_json, str):
            vitals_str = vitals_json
        else:
            vitals_str = "{}"

        rx_json = diag_data.get("prescriptions_json")
        if isinstance(rx_json, (list, dict)):
            rx_str = json.dumps(rx_json)
        elif isinstance(rx_json, str):
            rx_str = rx_json
        else:
            rx_str = "[]"

        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO diagnoses (
                        diagnosis_id, patient_id, doctor_id, doctor, diagnosis,
                        icd10_code, symptoms, vitals_json, prescriptions_json,
                        treatment_plan, follow_up_date, date, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    diag_data["diagnosis_id"],
                    diag_data["patient_id"],
                    diag_data.get("doctor_id", ""),
                    diag_data["doctor"],
                    diag_data["diagnosis"],
                    diag_data.get("icd10_code", ""),
                    diag_data.get("symptoms", ""),
                    vitals_str,
                    rx_str,
                    diag_data.get("treatment_plan", ""),
                    diag_data.get("follow_up_date", ""),
                    diag_data["date"],
                    diag_data["created_at"]
                ))
                conn.commit()
            return diag_data
        else:
            diag_item = dict(diag_data)
            diag_item["vitals_json"] = vitals_str
            diag_item["prescriptions_json"] = rx_str
            def _put():
                self.diagnoses_table.put_item(Item=diag_item)
            self._execute_with_retry(_put)
            return diag_item

    def get_diagnoses_by_patient(self, patient_id: str) -> list:
        """Retrieve all diagnoses for a patient with parsed JSON fields."""
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT d.*, u.name as doctor_full_name, u.specialty as doctor_specialty
                    FROM diagnoses d
                    LEFT JOIN users u ON d.doctor_id = u.user_id
                    WHERE d.patient_id = ?
                    ORDER BY d.date DESC, d.created_at DESC
                """, (patient_id,))
                records = [dict(row) for row in cursor.fetchall()]
        else:
            from boto3.dynamodb.conditions import Key, Attr
            try:
                response = self.diagnoses_table.query(
                    IndexName="PatientIndex",
                    KeyConditionExpression=Key("patient_id").eq(patient_id)
                )
                records = response.get("Items", [])
            except Exception:
                response = self.diagnoses_table.scan(FilterExpression=Attr("patient_id").eq(patient_id))
                records = response.get("Items", [])
            records.sort(key=lambda x: x.get("date", ""), reverse=True)

        # Parse JSON structures
        for r in records:
            v_raw = r.get("vitals_json")
            if isinstance(v_raw, str) and v_raw:
                try:
                    r["vitals"] = json.loads(v_raw)
                except Exception:
                    r["vitals"] = {}
            else:
                r["vitals"] = v_raw or {}

            p_raw = r.get("prescriptions_json")
            if isinstance(p_raw, str) and p_raw:
                try:
                    r["prescriptions"] = json.loads(p_raw)
                except Exception:
                    r["prescriptions"] = []
            else:
                r["prescriptions"] = p_raw or []

        return records

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
                records = [dict(row) for row in cursor.fetchall()]
        else:
            response = self.diagnoses_table.scan()
            records = response.get("Items", [])
            records.sort(key=lambda x: x.get("date", ""), reverse=True)

        for r in records:
            v_raw = r.get("vitals_json")
            if isinstance(v_raw, str) and v_raw:
                try:
                    r["vitals"] = json.loads(v_raw)
                except Exception:
                    r["vitals"] = {}
            p_raw = r.get("prescriptions_json")
            if isinstance(p_raw, str) and p_raw:
                try:
                    r["prescriptions"] = json.loads(p_raw)
                except Exception:
                    r["prescriptions"] = []
        return records

    # -------------------------------------------------------------
    # Longitudinal Vitals Operations
    # -------------------------------------------------------------
    def record_vitals(self, patient_id: str, recorded_by: str, vitals: dict) -> dict:
        """Record vital signs snapshot into vitals history."""
        vital_id = str(uuid.uuid4())
        recorded_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        bp = vitals.get("blood_pressure", "120/80")
        hr = int(vitals.get("heart_rate") or 72)
        temp = float(vitals.get("temperature") or 98.6)
        spo2 = int(vitals.get("spo2") or 98)
        bs = int(vitals.get("blood_sugar") or 100)

        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO vitals_history (vital_id, patient_id, recorded_by, blood_pressure, heart_rate, temperature, spo2, blood_sugar, recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (vital_id, patient_id, recorded_by, bp, hr, temp, spo2, bs, recorded_at))
                conn.commit()

        return {
            "vital_id": vital_id,
            "patient_id": patient_id,
            "recorded_by": recorded_by,
            "blood_pressure": bp,
            "heart_rate": hr,
            "temperature": temp,
            "spo2": spo2,
            "blood_sugar": bs,
            "recorded_at": recorded_at
        }

    def get_vitals_by_patient(self, patient_id: str, limit: int = 10) -> list:
        """Retrieve longitudinal vitals history for patient."""
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM vitals_history
                    WHERE patient_id = ?
                    ORDER BY recorded_at DESC
                    LIMIT ?
                """, (patient_id, limit))
                return [dict(row) for row in cursor.fetchall()]
        return []

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
            def _put():
                self.notifications_table.put_item(Item=notif_data)
            self._execute_with_retry(_put)
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
                response = self.notifications_table.scan(FilterExpression=Attr("patient_id").eq(patient_id))
                items = response.get("Items", [])
            items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return items[:limit]

    # -------------------------------------------------------------
    # HIPAA Compliance & Audit Logging
    # -------------------------------------------------------------
    def log_audit_event(self, actor_id: str, actor_name: str, actor_role: str,
                        action: str, target_resource: str, ip_address: str = "127.0.0.1", status: str = "SUCCESS"):
        """Record an immutable access/action log entry for HIPAA Security Rule compliance."""
        log_id = str(uuid.uuid4())
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO audit_logs (log_id, actor_id, actor_name, actor_role, action, target_resource, ip_address, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (log_id, actor_id, actor_name, actor_role, action, target_resource, ip_address, status, created_at))
                conn.commit()
        return log_id

    def get_recent_audit_logs(self, limit: int = 30) -> list:
        """Retrieve recent HIPAA audit log trail."""
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM audit_logs
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,))
                return [dict(row) for row in cursor.fetchall()]
        return []

    # -------------------------------------------------------------
    # Hospital Analytics & Deep Health Check
    # -------------------------------------------------------------
    def get_hospital_analytics(self) -> dict:
        """Calculate hospital-wide metrics for administration."""
        if self.mock_aws:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'patient'")
                patient_count = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'doctor'")
                doctor_count = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM appointments")
                total_appts = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM appointments WHERE status = 'Confirmed' OR status = 'Checked-In'")
                active_appts = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM diagnoses")
                total_diagnoses = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM audit_logs")
                total_audit_events = cursor.fetchone()[0]

                cursor.execute("""
                    SELECT department, COUNT(*) as count
                    FROM appointments
                    GROUP BY department
                    ORDER BY count DESC
                """)
                dept_distribution = [dict(row) for row in cursor.fetchall()]

                return {
                    "total_patients": patient_count,
                    "total_doctors": doctor_count,
                    "total_appointments": total_appts,
                    "active_appointments": active_appts,
                    "total_diagnoses": total_diagnoses,
                    "total_audit_events": total_audit_events,
                    "department_distribution": dept_distribution
                }
        return {
            "total_patients": 0, "total_doctors": 0, "total_appointments": 0,
            "active_appointments": 0, "total_diagnoses": 0, "total_audit_events": 0,
            "department_distribution": []
        }

    def check_health(self) -> dict:
        """Deep health check verifying latency and connectivity."""
        start_time = time.time()
        status = "healthy"
        error_msg = None

        try:
            if self.mock_aws:
                with self._get_sqlite_conn() as conn:
                    conn.execute("SELECT 1").fetchone()
            else:
                self.users_table.meta.client.describe_table(TableName=Config.DYNAMODB_USERS_TABLE)
        except Exception as e:
            status = "unhealthy"
            error_msg = str(e)

        latency_ms = round((time.time() - start_time) * 1000, 2)
        return {
            "status": status,
            "mode": "MOCK_SQLITE" if self.mock_aws else "AWS_DYNAMODB",
            "latency_ms": latency_ms,
            "error": error_msg
        }
