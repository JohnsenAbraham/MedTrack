"""
MedTrack Service Abstraction Layer: Database Interface
Seamlessly abstracts between local development (SQLite) and AWS production (DynamoDB).
"""

import sqlite3
import uuid
import datetime
from zoneinfo import ZoneInfo
import json
import logging
from contextlib import contextmanager
from werkzeug.security import generate_password_hash
from config import Config

logger = logging.getLogger("services.database")


def parse_scheduled_time(time_str: str) -> datetime.time:
    """Parse a time string in 24h ('%H:%M', '%H:%M:%S') or 12h ('%I:%M %p', '%I:%M%p') format."""
    if not time_str or not str(time_str).strip():
        return datetime.time(8, 0)
    raw = str(time_str).strip()
    formats = (
        "%H:%M", "%H:%M:%S",
        "%I:%M %p", "%I:%M%p",
        "%I:%M:%S %p", "%I:%M:%S%p"
    )
    for fmt in formats:
        try:
            return datetime.datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    try:
        parts = raw.split(":")
        h = int(parts[0])
        m = int(parts[1][:2]) if len(parts) > 1 else 0
        return datetime.time(h, m)
    except Exception:
        return datetime.time(8, 0)


def calculate_dose_state(scheduled_date, scheduled_time, intake_status=None, now=None, timezone_name=None) -> str:
    """Calculate the runtime dose state without persisting any rows.

    Evaluation rules:
      - Persistent outcomes take absolute precedence:
        - TAKEN: Persistent intake record exists with status 'TAKEN'
        - SKIPPED: Persistent intake record exists with status 'SKIPPED'
        - MISSED: Persistent intake record exists with status 'MISSED' (backward compatibility)
      - If no persistent intake record exists:
        - UPCOMING: now < scheduled_datetime - 30 minutes
        - DUE: scheduled_datetime - 30 minutes <= now <= scheduled_datetime + 60 minutes
        - MISSED: now > scheduled_datetime + 60 minutes

    This function is strictly non-persistent and executes purely in memory.
    """
    if intake_status:
        clean = str(intake_status).strip().upper()
        if clean in ("TAKEN", "SKIPPED", "MISSED"):
            return clean

    tz_name = timezone_name or getattr(Config, "APP_TIMEZONE", "Asia/Kolkata")
    tz = ZoneInfo(tz_name)

    if now is None:
        current_dt = datetime.datetime.now(tz)
    elif isinstance(now, datetime.datetime):
        if now.tzinfo is None:
            current_dt = now.replace(tzinfo=tz)
        else:
            current_dt = now.astimezone(tz)
    else:
        current_dt = datetime.datetime.now(tz)

    if isinstance(scheduled_date, str):
        try:
            d = datetime.date.fromisoformat(scheduled_date.strip())
        except Exception:
            d = current_dt.date()
    elif isinstance(scheduled_date, datetime.date):
        d = scheduled_date
    else:
        d = current_dt.date()

    t = parse_scheduled_time(scheduled_time)
    sched_dt = datetime.datetime.combine(d, t, tzinfo=tz)

    due_start = sched_dt - datetime.timedelta(minutes=30)
    due_end = sched_dt + datetime.timedelta(minutes=60)

    if current_dt < due_start:
        return "UPCOMING"
    elif current_dt <= due_end:
        return "DUE"
    else:
        return "MISSED"


class DatabaseService:
    """Unified service interface for MedTrack data persistence."""

    parse_scheduled_time = staticmethod(parse_scheduled_time)
    calculate_dose_state = staticmethod(calculate_dose_state)

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
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
        finally:
            conn.close()

    def _init_sqlite(self):
        """Initialize local SQLite tables and indexes matching the locked architecture."""
        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()

            # 1. USERS table
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
                    caregiver_name TEXT,
                    caregiver_phone TEXT,
                    caregiver_email TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            # 2. APPOINTMENTS table
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
                    FOREIGN KEY(patient_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(doctor_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # 3. PRESCRIPTIONS table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prescriptions (
                    prescription_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    doctor_id TEXT NOT NULL,
                    appointment_id TEXT,
                    medicine_name TEXT NOT NULL,
                    dosage TEXT NOT NULL,
                    instructions TEXT,
                    schedule_times TEXT NOT NULL DEFAULT '["08:00"]',
                    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'DISCONTINUED', 'EXPIRED', 'SUPERSEDED')),
                    issued_date TEXT NOT NULL,
                    valid_until TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(patient_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(doctor_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(appointment_id) REFERENCES appointments(appointment_id) ON DELETE SET NULL
                )
            """)

            # 4. DIAGNOSES table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS diagnoses (
                    diagnosis_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    doctor_id TEXT NOT NULL,
                    appointment_id TEXT,
                    diagnosis TEXT NOT NULL,
                    date TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(patient_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(doctor_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(appointment_id) REFERENCES appointments(appointment_id) ON DELETE SET NULL
                )
            """)

            # 5. REPORTS table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reports (
                    report_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    doctor_id TEXT,
                    appointment_id TEXT,
                    title TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    file_type TEXT NOT NULL DEFAULT 'application/pdf',
                    file_size INTEGER NOT NULL DEFAULT 0,
                    storage_path TEXT NOT NULL,
                    notes TEXT,
                    uploaded_at TEXT NOT NULL,
                    FOREIGN KEY(patient_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(doctor_id) REFERENCES users(user_id) ON DELETE SET NULL,
                    FOREIGN KEY(appointment_id) REFERENCES appointments(appointment_id) ON DELETE SET NULL
                )
            """)

            # 6. MEDICINES table (Patient Medication Cabinet)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS medicines (
                    medicine_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    dosage TEXT NOT NULL,
                    schedule_times TEXT NOT NULL DEFAULT '["08:00"]',
                    schedule_time TEXT,
                    frequency TEXT NOT NULL DEFAULT 'Daily',
                    meal_timing TEXT NOT NULL DEFAULT 'After Food',
                    instructions TEXT,
                    notes TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
                    prescription_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(patient_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(prescription_id) REFERENCES prescriptions(prescription_id) ON DELETE SET NULL
                )
            """)

            # 7. INTAKE_LOGS table (Patient Daily Intake History)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS intake_logs (
                    log_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    medicine_id TEXT,
                    medicine_name TEXT NOT NULL,
                    dosage TEXT NOT NULL,
                    scheduled_date TEXT NOT NULL,
                    scheduled_time TEXT NOT NULL,
                    taken_time TEXT,
                    status TEXT NOT NULL CHECK(status IN ('TAKEN', 'SKIPPED', 'MISSED')),
                    log_date TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(patient_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(medicine_id) REFERENCES medicines(medicine_id) ON DELETE SET NULL
                )
            """)

            # 8. NOTIFICATIONS table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notifications (
                    notification_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    type TEXT NOT NULL DEFAULT 'GENERAL',
                    title TEXT NOT NULL DEFAULT 'Notification',
                    message TEXT NOT NULL,
                    scheduled_date TEXT,
                    scheduled_time TEXT,
                    delivery_status TEXT NOT NULL DEFAULT 'PENDING' CHECK(delivery_status IN ('PENDING', 'CLAIMED', 'SENT', 'FAILED')),
                    delivery_attempts INTEGER NOT NULL DEFAULT 0,
                    last_delivery_attempt TEXT,
                    delivery_claim_id TEXT,
                    delivery_claimed_at TEXT,
                    delivery_lease_until TEXT,
                    sent_at TEXT,
                    status TEXT NOT NULL DEFAULT 'UNREAD' CHECK(status IN ('UNREAD', 'READ')),
                    is_read INTEGER NOT NULL DEFAULT 0 CHECK(is_read IN (0, 1)),
                    read_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(patient_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # Perform non-destructive migrations on existing databases
            self._migrate_sqlite(conn)

            # Create Indexes & Constraints
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_med_prescription_active ON medicines(prescription_id) WHERE is_active = 1 AND prescription_id IS NOT NULL;")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_medicines_patient ON medicines(patient_id);")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_intake_unique_dose ON intake_logs(patient_id, medicine_id, scheduled_date, scheduled_time);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_intake_patient_date ON intake_logs(patient_id, scheduled_date);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_appointments_patient_date ON appointments(patient_id, appointment_date);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_appointments_doctor_date ON appointments(doctor_id, appointment_date);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_prescriptions_patient_date ON prescriptions(patient_id, issued_date);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_prescriptions_doctor_date ON prescriptions(doctor_id, issued_date);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_diagnoses_patient_created ON diagnoses(patient_id, created_at);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_diagnoses_doctor_created ON diagnoses(doctor_id, created_at);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_reports_patient_uploaded ON reports(patient_id, uploaded_at);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_notifications_patient_created ON notifications(patient_id, created_at);")

            conn.commit()

        self._seed_demo_doctors()

    def _migrate_sqlite(self, conn: sqlite3.Connection):
        """Safely migrate and backfill existing tables without data loss."""
        cursor = conn.cursor()

        # Users table migrations
        cursor.execute("PRAGMA table_info(users)")
        u_cols = [c[1] for c in cursor.fetchall()]
        for col, ctype in [("caregiver_name", "TEXT"), ("caregiver_phone", "TEXT"), ("caregiver_email", "TEXT")]:
            if col not in u_cols:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {ctype}")

        # Medicines table migrations
        cursor.execute("PRAGMA table_info(medicines)")
        m_cols = [c[1] for c in cursor.fetchall()]
        for col, ctype, default in [
            ("schedule_times", "TEXT", "'[\"08:00\"]'"),
            ("instructions", "TEXT", "NULL"),
            ("notes", "TEXT", "NULL"),
            ("start_date", "TEXT", "NULL"),
            ("end_date", "TEXT", "NULL"),
            ("is_active", "INTEGER", "1"),
            ("prescription_id", "TEXT", "NULL")
        ]:
            if col not in m_cols:
                cursor.execute(f"ALTER TABLE medicines ADD COLUMN {col} {ctype} DEFAULT {default}")

        # Backfill schedule_times from legacy schedule_time if single dose exists
        cursor.execute("SELECT medicine_id, schedule_time, schedule_times FROM medicines WHERE schedule_time IS NOT NULL")
        for row in cursor.fetchall():
            try:
                parsed = json.loads(row["schedule_times"]) if row["schedule_times"] else []
            except Exception:
                parsed = []
            if not parsed and row["schedule_time"]:
                cursor.execute("UPDATE medicines SET schedule_times = ? WHERE medicine_id = ?", (json.dumps([row["schedule_time"]]), row["medicine_id"]))

        # Ensure medicines table state is clean and recover from any prior interrupted migrations
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('medicines', 'medicines_new');")
        existing_med_tables = {row[0] for row in cursor.fetchall()}

        # Recovery safeguard: If previous migration was terminated after dropping medicines but before renaming medicines_new
        if "medicines" not in existing_med_tables and "medicines_new" in existing_med_tables:
            cursor.execute("ALTER TABLE medicines_new RENAME TO medicines;")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_med_prescription_active ON medicines(prescription_id) WHERE is_active = 1 AND prescription_id IS NOT NULL;")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_medicines_patient ON medicines(patient_id);")
            conn.commit()
            existing_med_tables = {"medicines"}
        elif "medicines" in existing_med_tables and "medicines_new" in existing_med_tables:
            # Check row counts: if medicines was recreated empty after an interrupted swap, recover from medicines_new
            cursor.execute("SELECT COUNT(*) FROM medicines;")
            meds_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM medicines_new;")
            new_count = cursor.fetchone()[0]
            if meds_count == 0 and new_count > 0:
                cursor.execute("DROP TABLE medicines;")
                cursor.execute("ALTER TABLE medicines_new RENAME TO medicines;")
                cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_med_prescription_active ON medicines(prescription_id) WHERE is_active = 1 AND prescription_id IS NOT NULL;")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_medicines_patient ON medicines(patient_id);")
                conn.commit()
            else:
                # Clean up abandoned temporary table from an interrupted migration attempt
                cursor.execute("DROP TABLE IF EXISTS medicines_new;")
                conn.commit()

        # Medicines foreign-key migration (ensure prescription_id REFERENCES prescriptions(prescription_id) ON DELETE SET NULL)
        # Idempotency check: inspect PRAGMA foreign_key_list(medicines)
        has_prescription_fk = False
        if "medicines" in existing_med_tables:
            cursor.execute("PRAGMA foreign_key_list(medicines);")
            m_fks = cursor.fetchall()
            has_prescription_fk = any(fk[2] == "prescriptions" and fk[3] == "prescription_id" and fk[6] == "SET NULL" for fk in m_fks)

        if not has_prescription_fk and "medicines" in existing_med_tables:
            # SQLite 12-step table recreation procedure:
            # Note: SQLite PRAGMA foreign_keys is connection-scoped and cannot be toggled within an active transaction.
            # Therefore, this operation is not a single atomic ACID transaction across the FK pragma toggle boundary.
            # Instead, it is designed as a failure-safe, guard-validated procedure:
            # 1. Source existence and row-count verification before and after data copy
            # 2. PRAGMA foreign_key_check prior to dropping the source table
            # 3. Defensive cleanup / recovery of intermediate state on failure
            conn.commit()
            cursor.execute("PRAGMA foreign_keys = OFF;")
            try:
                cursor.execute("DROP TABLE IF EXISTS medicines_new;")
                cursor.execute("""
                    CREATE TABLE medicines_new (
                        medicine_id TEXT PRIMARY KEY,
                        patient_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        dosage TEXT NOT NULL,
                        schedule_times TEXT NOT NULL DEFAULT '["08:00"]',
                        schedule_time TEXT,
                        frequency TEXT NOT NULL DEFAULT 'Daily',
                        meal_timing TEXT NOT NULL DEFAULT 'After Food',
                        instructions TEXT,
                        notes TEXT,
                        start_date TEXT,
                        end_date TEXT,
                        is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
                        prescription_id TEXT,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(patient_id) REFERENCES users(user_id) ON DELETE CASCADE,
                        FOREIGN KEY(prescription_id) REFERENCES prescriptions(prescription_id) ON DELETE SET NULL
                    )
                """)
                cursor.execute("SELECT COUNT(*) FROM medicines;")
                expected_meds = cursor.fetchone()[0]

                cursor.execute("""
                    INSERT INTO medicines_new (
                        medicine_id, patient_id, name, dosage, schedule_times, schedule_time,
                        frequency, meal_timing, instructions, notes, start_date, end_date,
                        is_active, prescription_id, created_at
                    )
                    SELECT 
                        medicine_id, patient_id, name, dosage, 
                        COALESCE(schedule_times, '["08:00"]'), schedule_time,
                        COALESCE(frequency, 'Daily'), COALESCE(meal_timing, 'After Food'),
                        instructions, notes, start_date, end_date,
                        COALESCE(is_active, 1), prescription_id, created_at
                    FROM medicines;
                """)
                cursor.execute("SELECT COUNT(*) FROM medicines_new;")
                copied_meds = cursor.fetchone()[0]
                if copied_meds != expected_meds:
                    raise RuntimeError(f"Medicines migration count mismatch: expected {expected_meds}, copied {copied_meds}")

                # Verify foreign-key integrity on the newly populated table
                cursor.execute("PRAGMA foreign_key_check(medicines_new);")
                fk_violations = cursor.fetchall()
                if fk_violations:
                    raise RuntimeError(f"Medicines migration foreign key check failed: {fk_violations}")

                cursor.execute("DROP TABLE medicines;")
                cursor.execute("ALTER TABLE medicines_new RENAME TO medicines;")
                cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_med_prescription_active ON medicines(prescription_id) WHERE is_active = 1 AND prescription_id IS NOT NULL;")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_medicines_patient ON medicines(patient_id);")
                conn.commit()
            except Exception as e:
                conn.rollback()
                try:
                    # Clean up if source table medicines still exists; or recover medicines_new if medicines was dropped
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('medicines', 'medicines_new');")
                    post_err_tables = {row[0] for row in cursor.fetchall()}
                    if "medicines" in post_err_tables:
                        cursor.execute("DROP TABLE IF EXISTS medicines_new;")
                    elif "medicines_new" in post_err_tables:
                        cursor.execute("ALTER TABLE medicines_new RENAME TO medicines;")
                    conn.commit()
                except Exception:
                    pass
                raise RuntimeError(f"Medicines table recreation migration failed, handled safely: {e}") from e
            finally:
                cursor.execute("PRAGMA foreign_keys = ON;")

        # IntakeLogs table migrations (scheduled_date, CHECK constraint without PENDING, ON DELETE SET NULL)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('intake_logs', 'intake_logs_new');")
        existing_i_tables = {row[0] for row in cursor.fetchall()}
        if "intake_logs" not in existing_i_tables and "intake_logs_new" in existing_i_tables:
            cursor.execute("ALTER TABLE intake_logs_new RENAME TO intake_logs;")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_intake_unique_dose ON intake_logs(patient_id, medicine_id, scheduled_date, scheduled_time);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_intake_patient_date ON intake_logs(patient_id, scheduled_date);")
            conn.commit()
            existing_i_tables = {"intake_logs"}
        elif "intake_logs" in existing_i_tables and "intake_logs_new" in existing_i_tables:
            cursor.execute("SELECT COUNT(*) FROM intake_logs;")
            logs_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM intake_logs_new;")
            new_logs_count = cursor.fetchone()[0]
            if logs_count == 0 and new_logs_count > 0:
                cursor.execute("DROP TABLE intake_logs;")
                cursor.execute("ALTER TABLE intake_logs_new RENAME TO intake_logs;")
                cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_intake_unique_dose ON intake_logs(patient_id, medicine_id, scheduled_date, scheduled_time);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_intake_patient_date ON intake_logs(patient_id, scheduled_date);")
                conn.commit()
            else:
                cursor.execute("DROP TABLE IF EXISTS intake_logs_new;")
                conn.commit()

        if "intake_logs" in existing_i_tables:
            cursor.execute("PRAGMA table_info(intake_logs)")
            i_cols = [c[1] for c in cursor.fetchall()]
            if "scheduled_date" not in i_cols:
                # Failure-safe table recreation procedure with row count validation
                conn.commit()
                cursor.execute("PRAGMA foreign_keys = OFF;")
                try:
                    cursor.execute("DROP TABLE IF EXISTS intake_logs_new;")
                    cursor.execute("""
                        CREATE TABLE intake_logs_new (
                            log_id TEXT PRIMARY KEY,
                            patient_id TEXT NOT NULL,
                            medicine_id TEXT,
                            medicine_name TEXT NOT NULL,
                            dosage TEXT NOT NULL,
                            scheduled_date TEXT NOT NULL,
                            scheduled_time TEXT NOT NULL,
                            taken_time TEXT,
                            status TEXT NOT NULL CHECK(status IN ('TAKEN', 'SKIPPED', 'MISSED')),
                            log_date TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY(patient_id) REFERENCES users(user_id) ON DELETE CASCADE,
                            FOREIGN KEY(medicine_id) REFERENCES medicines(medicine_id) ON DELETE SET NULL
                        )
                    """)
                    cursor.execute("SELECT COUNT(*) FROM intake_logs;")
                    expected_logs = cursor.fetchone()[0]

                    cursor.execute("""
                        INSERT INTO intake_logs_new (log_id, patient_id, medicine_id, medicine_name, dosage, scheduled_date, scheduled_time, taken_time, status, log_date, created_at)
                        SELECT log_id, patient_id, medicine_id, medicine_name, dosage, log_date, scheduled_time, taken_time, status, log_date, created_at
                        FROM intake_logs
                    """)
                    cursor.execute("SELECT COUNT(*) FROM intake_logs_new;")
                    copied_logs = cursor.fetchone()[0]
                    if copied_logs != expected_logs:
                        raise RuntimeError(f"Intake logs migration count mismatch: expected {expected_logs}, copied {copied_logs}")

                    cursor.execute("PRAGMA foreign_key_check(intake_logs_new);")
                    fk_violations = cursor.fetchall()
                    if fk_violations:
                        raise RuntimeError(f"Intake logs migration foreign key check failed: {fk_violations}")

                    cursor.execute("DROP TABLE intake_logs;")
                    cursor.execute("ALTER TABLE intake_logs_new RENAME TO intake_logs;")
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    try:
                        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('intake_logs', 'intake_logs_new');")
                        post_err_tables = {row[0] for row in cursor.fetchall()}
                        if "intake_logs" in post_err_tables:
                            cursor.execute("DROP TABLE IF EXISTS intake_logs_new;")
                        elif "intake_logs_new" in post_err_tables:
                            cursor.execute("ALTER TABLE intake_logs_new RENAME TO intake_logs;")
                        conn.commit()
                    except Exception:
                        pass
                    raise RuntimeError(f"IntakeLogs table recreation migration failed, handled safely: {e}") from e
                finally:
                    cursor.execute("PRAGMA foreign_keys = ON;")


        # Diagnoses table migrations
        cursor.execute("PRAGMA table_info(diagnoses)")
        d_cols = [c[1] for c in cursor.fetchall()]
        if "appointment_id" not in d_cols:
            cursor.execute("ALTER TABLE diagnoses ADD COLUMN appointment_id TEXT")

        # Notifications table migrations
        cursor.execute("PRAGMA table_info(notifications)")
        n_cols = [c[1] for c in cursor.fetchall()]
        for col, ctype, default in [
            ("type", "TEXT", "'GENERAL'"),
            ("title", "TEXT", "'Notification'"),
            ("scheduled_date", "TEXT", "NULL"),
            ("scheduled_time", "TEXT", "NULL"),
            ("delivery_status", "TEXT", "'PENDING'"),
            ("delivery_attempts", "INTEGER", "0"),
            ("last_delivery_attempt", "TEXT", "NULL"),
            ("delivery_claim_id", "TEXT", "NULL"),
            ("delivery_claimed_at", "TEXT", "NULL"),
            ("delivery_lease_until", "TEXT", "NULL"),
            ("sent_at", "TEXT", "NULL"),
            ("is_read", "INTEGER", "0"),
            ("read_at", "TEXT", "NULL")
        ]:
            if col not in n_cols:
                cursor.execute(f"ALTER TABLE notifications ADD COLUMN {col} {ctype} DEFAULT {default}")


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
        user_id = user_data.get("user_id") or f"usr-{uuid.uuid4().hex[:8]}"
        created_at = user_data.get("created_at") or datetime.datetime.now(datetime.timezone.utc).isoformat()

        record = {
            "user_id": user_id,
            "name": user_data.get("name", "User"),
            "email": (user_data.get("email") or f"{user_id}@example.com").strip().lower(),
            "password_hash": user_data.get("password_hash") or "pbkdf2:sha256:mock",
            "phone": user_data.get("phone", ""),
            "date_of_birth": user_data.get("date_of_birth", ""),
            "gender": user_data.get("gender", ""),
            "role": user_data.get("role", "patient"),
            "caregiver_name": user_data.get("caregiver_name", ""),
            "caregiver_phone": user_data.get("caregiver_phone", ""),
            "caregiver_email": user_data.get("caregiver_email", ""),
            "created_at": created_at
        }

        if not self.mock_aws:
            return self.dynamo.create_user(record)

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
        appt_id = appointment_data.get("appointment_id") or f"apt-{uuid.uuid4().hex[:8]}"
        created_at = appointment_data.get("created_at") or datetime.datetime.now(datetime.timezone.utc).isoformat()
        status = appointment_data.get("status", "PENDING")

        doc = self.get_user_by_id(appointment_data["doctor_id"]) or {}
        pat = self.get_user_by_id(appointment_data["patient_id"]) or {}

        record = {
            "appointment_id": appt_id,
            "patient_id": appointment_data["patient_id"],
            "doctor_id": appointment_data["doctor_id"],
            "appointment_date": appointment_data["appointment_date"],
            "appointment_time": appointment_data["appointment_time"],
            "reason": appointment_data.get("reason", ""),
            "status": status,
            "created_at": created_at,
            "doctor_name": doc.get("name", "Physician"),
            "patient_name": pat.get("name", "Patient"),
            "patient_phone": pat.get("phone", ""),
            "patient_dob": pat.get("date_of_birth", ""),
            "patient_gender": pat.get("gender", "")
        }

        if not self.mock_aws:
            return self.dynamo.create_appointment(record)

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
        diag_id = diagnosis_data.get("diagnosis_id") or f"diag-{uuid.uuid4().hex[:8]}"
        created_at = diagnosis_data.get("created_at") or datetime.datetime.now(datetime.timezone.utc).isoformat()
        tz = ZoneInfo(getattr(Config, "APP_TIMEZONE", "Asia/Kolkata"))
        date_str = diagnosis_data.get("date") or datetime.datetime.now(tz).date().isoformat()

        doc = self.get_user_by_id(diagnosis_data["doctor_id"]) or {}
        pat = self.get_user_by_id(diagnosis_data["patient_id"]) or {}

        record = {
            "diagnosis_id": diag_id,
            "patient_id": diagnosis_data["patient_id"],
            "doctor_id": diagnosis_data["doctor_id"],
            "diagnosis": diagnosis_data["diagnosis"],
            "date": date_str,
            "created_at": created_at,
            "doctor_name": doc.get("name", "Physician"),
            "patient_name": pat.get("name", "Patient")
        }

        if not self.mock_aws:
            return self.dynamo.create_diagnosis(record)

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
            return self.dynamo.get_patients_by_doctor(doctor_id)

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

    # -------------------------------------------------------------
    # Medical Reports Methods
    # -------------------------------------------------------------
    def create_report(self, report_data: dict) -> dict:
        """
        Record a verified medical diagnostic report metadata record in the reports table.
        Validates patient existence, doctor/appointment consistency if provided.
        """
        patient_id = report_data.get("patient_id")
        doctor_id = report_data.get("doctor_id") or None
        appointment_id = report_data.get("appointment_id") or None
        title = (report_data.get("title") or "").strip()
        file_name = (report_data.get("file_name") or "").strip()
        file_type = (report_data.get("file_type") or "application/pdf").strip()
        file_size = int(report_data.get("file_size") or 0)
        storage_path = (report_data.get("storage_path") or "").strip()
        notes = (report_data.get("notes") or "").strip()

        if not patient_id:
            raise ValueError("Patient ID is required.")
        if not title:
            raise ValueError("Report title cannot be blank.")
        if not file_name:
            raise ValueError("File name cannot be blank.")
        if not storage_path:
            raise ValueError("Storage path cannot be blank.")

        patient = self.get_user_by_id(patient_id)
        if not patient or patient.get("role") != "patient":
            raise ValueError("Invalid patient: User does not exist or is not a registered patient.")

        if doctor_id:
            doctor = self.get_user_by_id(doctor_id)
            if not doctor or doctor.get("role") != "doctor":
                raise ValueError("Invalid doctor: User does not exist or is not a registered physician.")

        if appointment_id:
            appt = self.get_appointment_by_id(appointment_id)
            if not appt:
                raise ValueError(f"Appointment '{appointment_id}' not found.")
            if appt.get("patient_id") != patient_id:
                raise ValueError("Appointment patient mismatch: Appointment belongs to a different patient.")
            if doctor_id and appt.get("doctor_id") != doctor_id:
                raise ValueError("Appointment doctor mismatch: Appointment is assigned to a different physician.")

        report_id = report_data.get("report_id") or f"rep-{uuid.uuid4().hex[:8]}"
        uploaded_at = report_data.get("uploaded_at") or datetime.datetime.now(datetime.timezone.utc).isoformat()

        record = {
            "report_id": report_id,
            "patient_id": patient_id,
            "doctor_id": doctor_id,
            "appointment_id": appointment_id,
            "title": title,
            "file_name": file_name,
            "file_type": file_type,
            "file_size": file_size,
            "storage_path": storage_path,
            "notes": notes,
            "uploaded_at": uploaded_at
        }

        if not self.mock_aws:
            doc = self.get_user_by_id(doctor_id) if doctor_id else {}
            pat = self.get_user_by_id(patient_id) or {}
            record["doctor_name"] = doc.get("name", "") if doc else ""
            record["doctor_email"] = doc.get("email", "") if doc else ""
            record["patient_name"] = pat.get("name", "")
            record["patient_email"] = pat.get("email", "")
            return self.dynamo.create_report(record)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO reports (
                    report_id, patient_id, doctor_id, appointment_id,
                    title, file_name, file_type, file_size, storage_path,
                    notes, uploaded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record["report_id"], record["patient_id"], record["doctor_id"], record["appointment_id"],
                record["title"], record["file_name"], record["file_type"], record["file_size"], record["storage_path"],
                record["notes"], record["uploaded_at"]
            ))
            conn.commit()
        return record

    def get_report_by_id(self, report_id: str) -> dict | None:
        """Retrieve a specific medical report with patient, doctor, and appointment metadata."""
        if not self.mock_aws:
            return self.dynamo.get_report_by_id(report_id)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT r.*, p.name as patient_name, p.email as patient_email,
                       d.name as doctor_name, d.email as doctor_email,
                       a.appointment_date, a.appointment_time
                FROM reports r
                JOIN users p ON r.patient_id = p.user_id
                LEFT JOIN users d ON r.doctor_id = d.user_id
                LEFT JOIN appointments a ON r.appointment_id = a.appointment_id
                WHERE r.report_id = ?
            """, (report_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_reports_by_patient(self, patient_id: str) -> list:
        """Retrieve all medical reports for a specific patient ordered chronologically."""
        if not self.mock_aws:
            return self.dynamo.get_reports_by_patient(patient_id)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT r.*, d.name as doctor_name, d.email as doctor_email,
                       a.appointment_date, a.appointment_time
                FROM reports r
                LEFT JOIN users d ON r.doctor_id = d.user_id
                LEFT JOIN appointments a ON r.appointment_id = a.appointment_id
                WHERE r.patient_id = ?
                ORDER BY r.uploaded_at DESC
            """, (patient_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_reports_by_doctor(self, doctor_id: str, patient_id: str = None) -> list:
        """
        Retrieve reports accessible to an attending doctor based on established appointments.
        Doctor can only view reports for patients who have had appointments with that doctor.
        """
        if not self.mock_aws:
            return self.dynamo.get_reports_by_doctor(doctor_id, patient_id=patient_id)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            if patient_id:
                cursor.execute("""
                    SELECT 1 FROM appointments
                    WHERE doctor_id = ? AND patient_id = ?
                    LIMIT 1
                """, (doctor_id, patient_id))
                if not cursor.fetchone():
                    return []

                cursor.execute("""
                    SELECT r.*, p.name as patient_name, d.name as doctor_name
                    FROM reports r
                    JOIN users p ON r.patient_id = p.user_id
                    LEFT JOIN users d ON r.doctor_id = d.user_id
                    WHERE r.patient_id = ?
                    ORDER BY r.uploaded_at DESC
                """, (patient_id,))
            else:
                cursor.execute("""
                    SELECT r.*, p.name as patient_name, d.name as doctor_name
                    FROM reports r
                    JOIN users p ON r.patient_id = p.user_id
                    LEFT JOIN users d ON r.doctor_id = d.user_id
                    WHERE r.patient_id IN (
                        SELECT DISTINCT patient_id FROM appointments WHERE doctor_id = ?
                    )
                    ORDER BY r.uploaded_at DESC
                """, (doctor_id,))
            return [dict(row) for row in cursor.fetchall()]

    def is_doctor_authorized_for_patient(self, doctor_id: str, patient_id: str) -> bool:
        """Check if an established doctor-patient appointment relationship exists."""
        if not self.mock_aws:
            return self.dynamo.is_doctor_authorized_for_patient(doctor_id, patient_id)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 1 FROM appointments
                WHERE doctor_id = ? AND patient_id = ?
                LIMIT 1
            """, (doctor_id, patient_id))
            return bool(cursor.fetchone())

    def delete_report(self, report_id: str, actor_id: str) -> bool:
        """
        Delete a medical report from the reports table after verifying actor ownership.
        Actor must be the patient owner or an authorized attending physician.
        """
        if not self.mock_aws:
            return self.dynamo.delete_report(report_id, actor_id)

        report = self.get_report_by_id(report_id)
        if not report:
            return False

        is_patient_owner = (report["patient_id"] == actor_id)
        is_doctor_authorized = False
        if not is_patient_owner:
            actor = self.get_user_by_id(actor_id)
            if actor and actor.get("role") == "doctor":
                is_doctor_authorized = self.is_doctor_authorized_for_patient(actor_id, report["patient_id"])

        if not (is_patient_owner or is_doctor_authorized):
            return False

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM reports WHERE report_id = ?", (report_id,))
            conn.commit()
            return cursor.rowcount > 0

    # -------------------------------------------------------------
    # Prescription Methods
    # -------------------------------------------------------------
    def create_prescription(self, doctor_id: str, patient_id: str, appointment_id: str = None,
                            medicine_name: str = "", dosage: str = "", schedule_times: list = None,
                            instructions: str = "", valid_until: str = None,
                            frequency: str = "Daily", meal_timing: str = "After Food") -> dict:
        """
        Create a clinical prescription and atomically instantiate exactly one linked active medicine.
        Enforces doctor existence, patient existence, appointment boundary, and deterministic medicine ID.
        """
        # 1. Verify doctor exists and role == 'doctor'
        doctor = self.get_user_by_id(doctor_id)
        if not doctor or doctor.get("role") != "doctor":
            raise ValueError("Invalid doctor: User does not exist or is not a registered physician.")

        # 2. Verify patient exists and role == 'patient'
        patient = self.get_user_by_id(patient_id)
        if not patient or patient.get("role") != "patient":
            raise ValueError("Invalid patient: User does not exist or is not a registered patient.")

        # 3. Appointment verification or established doctor-patient relationship
        if appointment_id:
            appt = self.get_appointment_by_id(appointment_id)
            if not appt:
                raise ValueError(f"Appointment '{appointment_id}' not found.")
            if appt.get("doctor_id") != doctor_id:
                raise ValueError("Appointment doctor mismatch: Appointment is assigned to a different physician.")
            if appt.get("patient_id") != patient_id:
                raise ValueError("Appointment patient mismatch: Appointment belongs to a different patient.")
            if appt.get("status") not in ("CONFIRMED", "COMPLETED"):
                raise ValueError(f"Invalid appointment status '{appt.get('status')}': Must be CONFIRMED or COMPLETED to issue prescription.")
        else:
            # Established doctor-patient relationship from appointments history
            if not self.is_doctor_authorized_for_patient(doctor_id, patient_id):
                raise ValueError("Doctor-patient relationship required: Patient must have an established appointment history with this physician.")

        # 4. Input validation
        if not medicine_name or not medicine_name.strip():
            raise ValueError("Medicine name cannot be blank.")
        if not dosage or not dosage.strip():
            raise ValueError("Dosage cannot be blank.")

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        created_at = now_utc.isoformat()
        issued_date = now_utc.strftime("%Y-%m-%d")

        if valid_until:
            valid_until_clean = str(valid_until).strip()
            try:
                vu_date = datetime.date.fromisoformat(valid_until_clean)
            except ValueError:
                raise ValueError(f"Invalid valid_until date format '{valid_until}'. Expected YYYY-MM-DD.")
            if vu_date < now_utc.date():
                raise ValueError(f"Prescription valid_until date '{valid_until_clean}' cannot be in the past.")
            valid_until = valid_until_clean

        times = self.normalize_schedule_times(schedule_times)
        schedule_times_json = json.dumps(times)
        schedule_time = times[0] if times else "08:00 AM"

        prescription_id = f"rx-{uuid.uuid4().hex[:8]}"
        medicine_id = f"med_rx_{prescription_id}"

        doc_name = doctor.get("name", "Physician")
        notes = f"Prescribed by {doc_name}"
        clean_instructions = instructions.strip() if instructions else ""
        if clean_instructions:
            notes = f"{notes}. {clean_instructions}"

        rx_record = {
            "prescription_id": prescription_id,
            "patient_id": patient_id,
            "doctor_id": doctor_id,
            "appointment_id": appointment_id,
            "medicine_name": medicine_name.strip(),
            "dosage": dosage.strip(),
            "instructions": clean_instructions,
            "schedule_times": schedule_times_json,
            "status": "ACTIVE",
            "issued_date": issued_date,
            "valid_until": valid_until,
            "created_at": created_at,
            "doctor_name": doctor.get("name", "Physician"),
            "doctor_email": doctor.get("email", ""),
            "patient_name": patient.get("name", "Patient"),
            "patient_email": patient.get("email", "")
        }

        med_record = {
            "medicine_id": medicine_id,
            "patient_id": patient_id,
            "name": medicine_name.strip(),
            "dosage": dosage.strip(),
            "schedule_times": schedule_times_json,
            "schedule_time": schedule_time,
            "frequency": frequency.strip() if frequency else "Daily",
            "meal_timing": meal_timing.strip() if meal_timing else "After Food",
            "instructions": clean_instructions,
            "notes": notes,
            "start_date": issued_date,
            "end_date": valid_until,
            "is_active": 1,
            "prescription_id": prescription_id,
            "created_at": created_at
        }

        if not self.mock_aws:
            self.dynamo.create_prescription(rx_record, med_record)
            res = dict(rx_record)
            res["linked_medicine_id"] = medicine_id
            return res

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            try:
                # 5. Insert Prescription with ACTIVE status
                cursor.execute("""
                    INSERT INTO prescriptions (
                        prescription_id, patient_id, doctor_id, appointment_id,
                        medicine_name, dosage, instructions, schedule_times,
                        status, issued_date, valid_until, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?)
                """, (
                    prescription_id, patient_id, doctor_id, appointment_id,
                    medicine_name.strip(), dosage.strip(), clean_instructions,
                    schedule_times_json, issued_date, valid_until, created_at
                ))

                # 6. Insert linked active medicine in SAME transaction with deterministic ID
                cursor.execute("""
                    INSERT INTO medicines (
                        medicine_id, patient_id, name, dosage, schedule_times,
                        schedule_time, frequency, meal_timing, instructions, notes,
                        start_date, end_date, is_active, prescription_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """, (
                    medicine_id, patient_id, medicine_name.strip(), dosage.strip(),
                    schedule_times_json, schedule_time, frequency.strip() if frequency else "Daily",
                    meal_timing.strip() if meal_timing else "After Food",
                    clean_instructions, notes, issued_date, valid_until,
                    prescription_id, created_at
                ))
                conn.commit()
            except sqlite3.IntegrityError as e:
                conn.rollback()
                raise ValueError(f"Integrity violation creating prescription or linked medicine: {str(e)}")
            except Exception:
                conn.rollback()
                raise

        return {
            "prescription_id": prescription_id,
            "patient_id": patient_id,
            "doctor_id": doctor_id,
            "appointment_id": appointment_id,
            "medicine_name": medicine_name.strip(),
            "dosage": dosage.strip(),
            "instructions": clean_instructions,
            "schedule_times": schedule_times_json,
            "status": "ACTIVE",
            "issued_date": issued_date,
            "valid_until": valid_until,
            "created_at": created_at,
            "linked_medicine_id": medicine_id
        }

    def get_prescription_by_id(self, prescription_id: str) -> dict | None:
        """Retrieve a specific prescription with patient, doctor, and appointment metadata."""
        if not self.mock_aws:
            return self.dynamo.get_prescription_by_id(prescription_id)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT p.*, u.name as patient_name, u.email as patient_email,
                       d.name as doctor_name, d.email as doctor_email,
                       a.appointment_date, a.appointment_time
                FROM prescriptions p
                JOIN users u ON p.patient_id = u.user_id
                JOIN users d ON p.doctor_id = d.user_id
                LEFT JOIN appointments a ON p.appointment_id = a.appointment_id
                WHERE p.prescription_id = ?
            """, (prescription_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_prescriptions_by_doctor(self, doctor_id: str) -> list:
        """
        Retrieve all prescriptions issued by the specified doctor.
        Strictly isolated: does NOT query medicines or return patient OTC medications.
        """
        if not self.mock_aws:
            return self.dynamo.get_prescriptions_by_doctor(doctor_id)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT p.*, u.name as patient_name, u.email as patient_email,
                       a.appointment_date, a.appointment_time
                FROM prescriptions p
                JOIN users u ON p.patient_id = u.user_id
                LEFT JOIN appointments a ON p.appointment_id = a.appointment_id
                WHERE p.doctor_id = ?
                ORDER BY p.created_at DESC
            """, (doctor_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_prescriptions_by_patient(self, patient_id: str) -> list:
        """Retrieve all prescriptions issued to the specified patient."""
        if not self.mock_aws:
            return self.dynamo.get_prescriptions_by_patient(patient_id)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT p.*, d.name as doctor_name, d.email as doctor_email,
                       a.appointment_date, a.appointment_time
                FROM prescriptions p
                JOIN users d ON p.doctor_id = d.user_id
                LEFT JOIN appointments a ON p.appointment_id = a.appointment_id
                WHERE p.patient_id = ?
                ORDER BY p.created_at DESC
            """, (patient_id,))
            return [dict(row) for row in cursor.fetchall()]

    def update_prescription_status(self, prescription_id: str, new_status: str, doctor_id: str = None) -> bool:
        """
        Execute an authorized clinical prescription lifecycle transition.
        Supported statuses: ACTIVE, DISCONTINUED, EXPIRED, SUPERSEDED.
        Enforces:
        - Terminal state protection (non-ACTIVE cannot be transitioned to another state)
        - Synchronization with linked medicine (DISCONTINUED/EXPIRED/SUPERSEDED -> is_active = 0)
        - Doctor ownership check if doctor_id provided
        """
        allowed_statuses = ("ACTIVE", "DISCONTINUED", "EXPIRED", "SUPERSEDED")
        norm_status = (new_status or "").strip().upper()
        if norm_status not in allowed_statuses:
            raise ValueError(f"Invalid prescription status '{new_status}'. Allowed statuses: {', '.join(allowed_statuses)}.")

        rx = self.get_prescription_by_id(prescription_id)
        if not rx:
            return False
        if doctor_id and rx["doctor_id"] != doctor_id:
            return False

        current_status = rx["status"]
        if current_status == norm_status:
            return True

        if current_status != "ACTIVE":
            raise ValueError(f"Invalid lifecycle transition: Cannot transition prescription from terminal state '{current_status}' to '{norm_status}'.")

        if not self.mock_aws:
            return self.dynamo.update_prescription_status(prescription_id, norm_status)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE prescriptions
                SET status = ?
                WHERE prescription_id = ?
            """, (norm_status, prescription_id))

            med_is_active = 1 if norm_status == "ACTIVE" else 0
            cursor.execute("""
                UPDATE medicines
                SET is_active = ?
                WHERE prescription_id = ?
            """, (med_is_active, prescription_id))
            conn.commit()
        return True

    def discontinue_prescription(self, prescription_id: str, doctor_id: str) -> bool:
        """
        Discontinue an active prescription and deactivate its linked medicine.
        Prescription record is preserved for clinical history; linked medicine is deactivated.
        """
        return self.update_prescription_status(prescription_id, "DISCONTINUED", doctor_id=doctor_id)

    def get_notifications_by_doctor(self, doctor_id: str) -> list:
        if not self.mock_aws:
            return self.dynamo.get_notifications_by_doctor(doctor_id)

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
    def create_notification(self, patient_id: str, message: str, title: str = "Notification",
                            notif_type: str = "GENERAL", scheduled_date: str = None,
                            scheduled_time: str = None, notification_id: str = None) -> dict:
        notif_id = notification_id or f"ntf-{uuid.uuid4().hex[:8]}"
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        record = {
            "notification_id": notif_id,
            "patient_id": patient_id,
            "type": notif_type,
            "title": title,
            "message": message,
            "scheduled_date": scheduled_date,
            "scheduled_time": scheduled_time,
            "delivery_status": "PENDING",
            "delivery_attempts": 0,
            "status": "UNREAD",
            "is_read": 0,
            "created_at": created_at
        }

        if not self.mock_aws:
            return self.dynamo.create_notification(record)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO notifications (notification_id, patient_id, type, title, message, scheduled_date, scheduled_time, delivery_status, delivery_attempts, status, is_read, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record["notification_id"], record["patient_id"], record["type"],
                record["title"], record["message"], record["scheduled_date"],
                record["scheduled_time"], record["delivery_status"],
                record["delivery_attempts"], record["status"], record["is_read"],
                record["created_at"]
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

    def create_reminder_notification(self, notification_id: str, patient_id: str,
                                     title: str, message: str, scheduled_date: str,
                                     scheduled_time: str, created_at: str = None) -> bool:
        """
        Atomically persist a deterministic reminder notification.
        Notification persistence is deterministic and idempotent, guaranteeing at most one
        persisted notification per dose identity through the unique notification_id and
        atomic ON CONFLICT DO NOTHING.
        """
        if not self.mock_aws:
            return self.dynamo.create_reminder_notification(
                notification_id=notification_id,
                patient_id=patient_id,
                title=title,
                message=message,
                scheduled_date=scheduled_date,
                scheduled_time=scheduled_time,
                created_at=created_at
            )

        created_at_val = created_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO notifications (
                    notification_id, patient_id, type, title, message,
                    scheduled_date, scheduled_time, delivery_status, delivery_attempts,
                    status, is_read, created_at
                ) VALUES (?, ?, 'medicine_reminder', ?, ?, ?, ?, 'PENDING', 0, 'UNREAD', 0, ?)
                ON CONFLICT(notification_id) DO NOTHING;
            """, (
                notification_id, patient_id, title, message,
                scheduled_date, scheduled_time, created_at_val
            ))
            conn.commit()
            return cursor.rowcount > 0

    def claim_pending_notifications(self, limit: int = 50, lease_seconds: int = 120,
                                    now: datetime.datetime = None,
                                    notification_type: str = "medicine_reminder") -> list:
        """
        Atomically claim pending or expired-lease notifications for delivery.
        Only claims notifications matching notification_type (defaults to 'medicine_reminder').
        Uses conditional UPDATE as the concurrency boundary, followed by a restricted
        SELECT filtering by both notification_id and delivery_claim_id to ensure
        the worker only receives notifications it legitimately owns.
        """
        if not self.mock_aws:
            return self.dynamo.claim_pending_notifications(
                limit=limit,
                lease_seconds=lease_seconds,
                now=now,
                notification_type=notification_type
            )

        tz = ZoneInfo(getattr(Config, "APP_TIMEZONE", "Asia/Kolkata"))
        eval_now = now if now is not None else datetime.datetime.now(tz)
        if eval_now.tzinfo is None:
            eval_now = eval_now.replace(tzinfo=tz)

        now_iso = eval_now.isoformat()
        lease_until_iso = (eval_now + datetime.timedelta(seconds=lease_seconds)).isoformat()
        claim_id = f"claim-{uuid.uuid4().hex}"

        type_clause = " AND type = ?" if notification_type else ""
        type_params = (notification_type,) if notification_type else ()

        claimed_records = []
        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            # 1. Fetch candidate IDs (PENDING or expired CLAIMED with attempts < 3) filtered by type
            cursor.execute(f"""
                SELECT notification_id FROM notifications
                WHERE (delivery_status = 'PENDING' OR (delivery_status = 'CLAIMED' AND delivery_lease_until < ?))
                  AND delivery_attempts < 3
                  {type_clause}
                ORDER BY created_at ASC
                LIMIT ?;
            """, (now_iso, *type_params, limit))
            candidates = [r["notification_id"] for r in cursor.fetchall()]

            for notif_id in candidates:
                # 2. Atomic conditional UPDATE: concurrency boundary
                cursor.execute(f"""
                    UPDATE notifications
                    SET delivery_status = 'CLAIMED',
                        delivery_claim_id = ?,
                        delivery_claimed_at = ?,
                        delivery_lease_until = ?,
                        delivery_attempts = delivery_attempts + 1,
                        last_delivery_attempt = ?
                    WHERE notification_id = ?
                      AND (delivery_status = 'PENDING' OR (delivery_status = 'CLAIMED' AND delivery_lease_until < ?))
                      AND delivery_attempts < 3
                      {type_clause};
                """, (
                    claim_id, now_iso, lease_until_iso, now_iso,
                    notif_id, now_iso, *type_params
                ))
                if cursor.rowcount == 1:
                    # 3. Retrieve claimed record ONLY using BOTH notification_id and delivery_claim_id
                    cursor.execute("""
                        SELECT * FROM notifications
                        WHERE notification_id = ? AND delivery_claim_id = ?;
                    """, (notif_id, claim_id))
                    row = cursor.fetchone()
                    if row:
                        claimed_records.append(dict(row))

            conn.commit()

        return claimed_records

    def mark_notification_sent(self, notification_id: str, claim_id: str, sent_at: str = None) -> bool:
        """
        Mark a claimed notification as SENT upon successful delivery.
        Requires both notification_id and delivery_claim_id to enforce ownership.
        """
        if not self.mock_aws:
            return self.dynamo.mark_notification_sent(
                notification_id=notification_id,
                claim_id=claim_id,
                sent_at=sent_at
            )

        sent_at_val = sent_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE notifications
                SET delivery_status = 'SENT',
                    sent_at = ?
                WHERE notification_id = ? AND delivery_claim_id = ?;
            """, (sent_at_val, notification_id, claim_id))
            conn.commit()
            return cursor.rowcount > 0

    def mark_notification_failed_or_retry(self, notification_id: str, claim_id: str, max_attempts: int = 3) -> str:
        """
        Handle delivery failure for a claimed notification.
        If attempts < max_attempts: revert to PENDING and clear claim fields for future retry.
        If attempts >= max_attempts: transition to FAILED.
        Requires both notification_id and delivery_claim_id.
        """
        if not self.mock_aws:
            return self.dynamo.mark_notification_failed_or_retry(
                notification_id=notification_id,
                claim_id=claim_id,
                max_attempts=max_attempts
            )

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT delivery_attempts FROM notifications
                WHERE notification_id = ? AND delivery_claim_id = ?;
            """, (notification_id, claim_id))
            row = cursor.fetchone()
            if not row:
                return "NOT_FOUND"

            attempts = row["delivery_attempts"]
            if attempts < max_attempts:
                cursor.execute("""
                    UPDATE notifications
                    SET delivery_status = 'PENDING',
                        delivery_claim_id = NULL,
                        delivery_claimed_at = NULL,
                        delivery_lease_until = NULL
                    WHERE notification_id = ? AND delivery_claim_id = ?;
                """, (notification_id, claim_id))
                conn.commit()
                return "PENDING"
            else:
                cursor.execute("""
                    UPDATE notifications
                    SET delivery_status = 'FAILED'
                    WHERE notification_id = ? AND delivery_claim_id = ?;
                """, (notification_id, claim_id))
                conn.commit()
                return "FAILED"

    # -------------------------------------------------------------
    # Patient Medicine Management & Dose Tracking
    # -------------------------------------------------------------
    def create_medicine(self, patient_id: str, name: str, dosage: str, schedule_time: str = None,
                        frequency: str = "Daily", meal_timing: str = "After Food", notes: str = "",
                        schedule_times: list = None, prescription_id: str = None,
                        start_date: str = None, end_date: str = None) -> dict:
        medicine_id = f"med-{uuid.uuid4().hex[:8]}"
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        times = schedule_times if schedule_times else ([schedule_time.strip()] if schedule_time else ["08:00"])
        schedule_times_json = json.dumps(times)

        record = {
            "medicine_id": medicine_id,
            "patient_id": patient_id,
            "name": name.strip(),
            "dosage": dosage.strip(),
            "schedule_times": schedule_times_json,
            "schedule_time": schedule_time.strip() if schedule_time else times[0],
            "frequency": frequency.strip(),
            "meal_timing": meal_timing.strip(),
            "instructions": notes.strip() if notes else "",
            "notes": notes.strip() if notes else "",
            "start_date": start_date,
            "end_date": end_date,
            "is_active": 1,
            "prescription_id": prescription_id,
            "created_at": created_at
        }

        if not self.mock_aws:
            return self.dynamo.create_medicine(record)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO medicines (medicine_id, patient_id, name, dosage, schedule_times, schedule_time, frequency, meal_timing, instructions, notes, start_date, end_date, is_active, prescription_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record["medicine_id"], record["patient_id"], record["name"],
                record["dosage"], record["schedule_times"], record["schedule_time"],
                record["frequency"], record["meal_timing"], record["instructions"],
                record["notes"], record["start_date"], record["end_date"],
                record["is_active"], record["prescription_id"], record["created_at"]
            ))
            conn.commit()
        return record

    def get_medicine_by_id(self, medicine_id: str):
        if not self.mock_aws:
            return self.dynamo.get_medicine_by_id(medicine_id)

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

        # Doctor-prescribed medicines cannot be casually deleted from clinical record
        if med.get("prescription_id") is not None:
            return False

        if not self.mock_aws:
            return self.dynamo.delete_medicine(medicine_id, patient_id=patient_id)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            # Preserve historical intake records with medicine_id = NULL (defense-in-depth for ON DELETE SET NULL)
            cursor.execute("UPDATE intake_logs SET medicine_id = NULL WHERE medicine_id = ? AND patient_id = ?", (medicine_id, patient_id))
            cursor.execute("DELETE FROM medicines WHERE medicine_id = ? AND patient_id = ?", (medicine_id, patient_id))
            conn.commit()
        return True

    def get_active_medicines(self) -> list:
        """Retrieve all currently active patient medicines."""
        if not self.mock_aws:
            return self.dynamo.get_active_medicines()

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM medicines
                WHERE is_active = 1
                ORDER BY patient_id ASC, schedule_time ASC;
            """)
            return [dict(row) for row in cursor.fetchall()]

    def get_dose_intake_log(self, patient_id: str, medicine_id: str,
                            scheduled_date: str, scheduled_time: str) -> dict | None:
        """Retrieve intake log for a specific scheduled dose if recorded."""
        if not self.mock_aws:
            return self.dynamo.get_dose_intake_log(patient_id, medicine_id, scheduled_date, scheduled_time)

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM intake_logs
                WHERE patient_id = ? AND medicine_id = ?
                  AND scheduled_date = ? AND scheduled_time = ?;
            """, (patient_id, medicine_id, scheduled_date, scheduled_time))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def normalize_schedule_times(schedule_times_val, legacy_schedule_time=None) -> list:
        """Return a validated list of time strings from schedule_times or fallback to legacy schedule_time."""
        times = []
        if schedule_times_val:
            if isinstance(schedule_times_val, list):
                times = [str(t).strip() for t in schedule_times_val if str(t).strip()]
            elif isinstance(schedule_times_val, str):
                try:
                    parsed = json.loads(schedule_times_val)
                    if isinstance(parsed, list):
                        times = [str(t).strip() for t in parsed if str(t).strip()]
                    elif isinstance(parsed, str) and parsed.strip():
                        times = [parsed.strip()]
                except Exception:
                    times = [t.strip() for t in schedule_times_val.split(",") if t.strip()]
        if not times and legacy_schedule_time:
            times = [str(legacy_schedule_time).strip()]
        if not times:
            times = ["08:00"]
        return times

    def record_intake(self, patient_id: str, medicine_id: str, status: str,
                      scheduled_date: str = None, scheduled_time: str = None,
                      log_date: str = None) -> dict:
        status_clean = (status or "").strip().upper()
        if status_clean not in ("TAKEN", "SKIPPED"):
            raise ValueError(f"Status must be 'TAKEN' or 'SKIPPED' (got '{status}'). PENDING is not a valid persistent intake status.")

        med = self.get_medicine_by_id(medicine_id)
        if not med or med["patient_id"] != patient_id:
            raise ValueError("Medication record not found.")

        tz = ZoneInfo(getattr(Config, "APP_TIMEZONE", "Asia/Kolkata"))
        now_tz = datetime.datetime.now(tz)
        target_date = scheduled_date or log_date or now_tz.date().isoformat()
        if scheduled_time and scheduled_time.strip():
            target_time = scheduled_time.strip()
        else:
            st_list = self.normalize_schedule_times(med.get("schedule_times"), med.get("schedule_time"))
            target_time = st_list[0] if st_list else "08:00"

        now_time = now_tz.strftime("%I:%M %p") if status_clean == "TAKEN" else None
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        new_log_id = f"log-{uuid.uuid4().hex[:8]}"

        if not self.mock_aws:
            return self.dynamo.record_intake(
                patient_id=patient_id,
                medicine_id=medicine_id,
                medicine_name=med["name"],
                dosage=med["dosage"],
                status=status_clean,
                scheduled_date=target_date,
                scheduled_time=target_time,
                taken_time=now_time,
                created_at=created_at
            )

        with self._get_sqlite_conn() as conn:
            cursor = conn.cursor()
            # Atomic UPSERT based on authoritative dose identity (patient_id, medicine_id, scheduled_date, scheduled_time)
            # No SELECT-then-INSERT existence check is used to eliminate race conditions.
            # created_at is NOT modified on conflict; it preserves the original creation timestamp.
            cursor.execute("""
                INSERT INTO intake_logs (
                    log_id, patient_id, medicine_id, medicine_name, dosage,
                    scheduled_date, scheduled_time, taken_time, status, log_date, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(patient_id, medicine_id, scheduled_date, scheduled_time) DO UPDATE SET
                    status = excluded.status,
                    taken_time = excluded.taken_time;
            """, (
                new_log_id, patient_id, medicine_id, med["name"], med["dosage"],
                target_date, target_time, now_time, status_clean, target_date, created_at
            ))
            conn.commit()

            # Retrieve the authoritative recorded row (preserves original log_id on update)
            cursor.execute("""
                SELECT * FROM intake_logs
                WHERE patient_id = ? AND medicine_id = ? AND scheduled_date = ? AND scheduled_time = ?;
            """, (patient_id, medicine_id, target_date, target_time))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_patient_schedule(self, patient_id: str, target_date: str = None, now: datetime.datetime = None) -> dict:
        tz_name = getattr(Config, "APP_TIMEZONE", "Asia/Kolkata")
        tz = ZoneInfo(tz_name)

        if now is None:
            eval_now = datetime.datetime.now(tz)
        elif isinstance(now, datetime.datetime):
            if now.tzinfo is None:
                eval_now = now.replace(tzinfo=tz)
            else:
                eval_now = now.astimezone(tz)
        else:
            eval_now = datetime.datetime.now(tz)

        today_str = target_date or eval_now.strftime("%Y-%m-%d")
        medicines = self.get_medicines_by_patient(patient_id)

        if not self.mock_aws:
            logs = self.dynamo.get_intake_logs_by_patient(patient_id, log_date=today_str)
        else:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM intake_logs WHERE patient_id = ? AND scheduled_date = ?", (patient_id, today_str))
                logs = [dict(r) for r in cursor.fetchall()]

        logs_by_dose = {(l["medicine_id"], l["scheduled_time"]): l for l in logs}

        doses = []
        taken_count = 0
        skipped_count = 0
        due_count = 0
        upcoming_count = 0
        missed_count = 0

        for med in medicines:
            if med.get("is_active") == 0:
                continue
            st_list = self.normalize_schedule_times(med.get("schedule_times"), med.get("schedule_time"))
            for st in st_list:
                log = logs_by_dose.get((med["medicine_id"], st))
                raw_status = log["status"] if log else None
                taken_time = log.get("taken_time", "") if log else ""
                log_id = log["log_id"] if log else None

                # Calculate runtime dose state (non-persistent calculation)
                status = calculate_dose_state(
                    scheduled_date=today_str,
                    scheduled_time=st,
                    intake_status=raw_status,
                    now=eval_now,
                    timezone_name=tz_name
                )

                if status == "TAKEN":
                    taken_count += 1
                elif status == "SKIPPED":
                    skipped_count += 1
                elif status == "DUE":
                    due_count += 1
                elif status == "UPCOMING":
                    upcoming_count += 1
                elif status == "MISSED":
                    missed_count += 1

                doses.append({
                    "medicine_id": med["medicine_id"],
                    "name": med["name"],
                    "dosage": med["dosage"],
                    "schedule_time": st,
                    "frequency": med["frequency"],
                    "meal_timing": med["meal_timing"],
                    "notes": med.get("notes", ""),
                    "status": status,
                    "intake_status": raw_status,
                    "taken_time": taken_time,
                    "log_id": log_id
                })

        doses.sort(key=lambda d: d["schedule_time"])
        total_doses = len(doses)
        pending_count = due_count + upcoming_count + missed_count
        adherence_pct = round((taken_count / total_doses * 100)) if total_doses > 0 else 100

        return {
            "date": today_str,
            "doses": doses,
            "total_doses": total_doses,
            "taken_count": taken_count,
            "skipped_count": skipped_count,
            "due_count": due_count,
            "upcoming_count": upcoming_count,
            "missed_count": missed_count,
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
