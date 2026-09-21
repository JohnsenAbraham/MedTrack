import os
import datetime
from pathlib import Path
from dotenv import load_dotenv

# Base directory of the project
BASE_DIR = Path(__file__).resolve().parent

# Load environment variables from .env file if present
load_dotenv(BASE_DIR / ".env")

class Config:
    """Application configuration for MedTrack AWS Cloud-Enabled Healthcare System."""
    BASE_DIR = BASE_DIR

    # Debug Mode (Disabled by default; enable via FLASK_DEBUG=true for local diagnostics)
    DEBUG = os.getenv("FLASK_DEBUG", "false").lower() in ("true", "1")

    # Local vs AWS Toggle: True runs local SQLite/simulated SNS; False uses boto3 DynamoDB & SNS
    MOCK_AWS = os.getenv("MOCK_AWS", "true").lower() in ("true", "1", "yes")

    # Secret Key Configuration & Production Validation
    SECRET_KEY = os.getenv("SECRET_KEY", "medtrack-secret-key-college-demo-2026")
    if os.getenv("FLASK_ENV") == "production" or not MOCK_AWS:
        insecure_placeholders = {
            "medtrack-secret-key-college-demo-2026",
            "medtrack-super-secret-key-change-in-production-2026",
            "medtrack-dev-insecure-key-local-only",
            "replace-me",
            ""
        }
        if not SECRET_KEY or SECRET_KEY in insecure_placeholders:
            raise RuntimeError(
                "CRITICAL SECURITY CONFIGURATION ERROR: Production deployment requires a unique, "
                "cryptographically strong SECRET_KEY environment variable. Halting startup."
            )

    # Session Security
    SESSION_COOKIE_NAME = "medtrack_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() in ("true", "1")
    PERMANENT_SESSION_LIFETIME = datetime.timedelta(minutes=int(os.getenv("SESSION_LIFETIME_MINUTES", "60")))
    SESSION_INACTIVITY_TIMEOUT = int(os.getenv("SESSION_INACTIVITY_TIMEOUT", "3600"))

    # Reverse Proxy & TLS Integration (Enable when behind TLS-terminating reverse proxy)
    USE_PROXY_FIX = os.getenv("USE_PROXY_FIX", "false").lower() in ("true", "1")
    DOMAIN_NAME = os.getenv("DOMAIN_NAME", "")
    SSL_CERTIFICATE_PATH = os.getenv("SSL_CERTIFICATE_PATH", "")
    SSL_CERTIFICATE_KEY_PATH = os.getenv("SSL_CERTIFICATE_KEY_PATH", "")

    # CSRF Protection
    WTF_CSRF_ENABLED = os.getenv("WTF_CSRF_ENABLED", "true").lower() in ("true", "1")
    WTF_CSRF_TIME_LIMIT = int(os.getenv("WTF_CSRF_TIME_LIMIT", "3600"))

    # Demo Mode: Allows 1-click test login in demo/development environments; strictly disabled by default
    DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() in ("true", "1", "yes")

    # Canonical Application Timezone (Asia/Kolkata default for patient schedules and dose calculation)
    APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Kolkata")

    # AWS Configuration
    AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

    # Amazon DynamoDB Table Names (8 Core Tables)
    DYNAMODB_USERS_TABLE = os.getenv("DYNAMODB_USERS_TABLE", "MedTrack_Users")
    DYNAMODB_MEDICINES_TABLE = os.getenv("DYNAMODB_MEDICINES_TABLE", "MedTrack_Medicines")
    DYNAMODB_INTAKE_LOGS_TABLE = os.getenv("DYNAMODB_INTAKE_LOGS_TABLE", "MedTrack_IntakeLogs")
    DYNAMODB_APPOINTMENTS_TABLE = os.getenv("DYNAMODB_APPOINTMENTS_TABLE", "MedTrack_Appointments")
    DYNAMODB_PRESCRIPTIONS_TABLE = os.getenv("DYNAMODB_PRESCRIPTIONS_TABLE", "MedTrack_Prescriptions")
    DYNAMODB_DIAGNOSES_TABLE = os.getenv("DYNAMODB_DIAGNOSES_TABLE", "MedTrack_Diagnoses")
    DYNAMODB_REPORTS_TABLE = os.getenv("DYNAMODB_REPORTS_TABLE", "MedTrack_Reports")
    DYNAMODB_NOTIFICATIONS_TABLE = os.getenv("DYNAMODB_NOTIFICATIONS_TABLE", "MedTrack_Notifications")

    # Amazon SNS Topic ARN (Provided via environment / CloudFormation in production; empty in local/mock)
    SNS_TOPIC_ARN = os.getenv("SNS_TOPIC_ARN", "")

    # Amazon S3 Bucket for Medical Reports (Provided by CloudFormation in production; empty in local/mock)
    S3_REPORTS_BUCKET = os.getenv("S3_REPORTS_BUCKET", "")

    # Local SQLite Database Path
    LOCAL_DB_PATH = BASE_DIR / "medtrack_local.db"

