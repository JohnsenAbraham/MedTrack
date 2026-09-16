import os
from pathlib import Path
from dotenv import load_dotenv

# Base directory of the project
BASE_DIR = Path(__file__).resolve().parent

# Load environment variables from .env file if present
load_dotenv(BASE_DIR / ".env")

class Config:
    """Application configuration for MedTrack AWS Cloud-Enabled Healthcare System."""
    SECRET_KEY = os.getenv("SECRET_KEY", "medtrack-secret-key-college-demo-2026")

    # Local vs AWS Toggle: True runs local SQLite/simulated SNS; False uses boto3 DynamoDB & SNS
    MOCK_AWS = os.getenv("MOCK_AWS", "true").lower() in ("true", "1", "yes")

    # AWS Configuration
    AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

    # Amazon DynamoDB Table Names
    DYNAMODB_USERS_TABLE = os.getenv("DYNAMODB_USERS_TABLE", "MedTrack_Users")
    DYNAMODB_APPOINTMENTS_TABLE = os.getenv("DYNAMODB_APPOINTMENTS_TABLE", "MedTrack_Appointments")
    DYNAMODB_DIAGNOSES_TABLE = os.getenv("DYNAMODB_DIAGNOSES_TABLE", "MedTrack_Diagnoses")
    DYNAMODB_NOTIFICATIONS_TABLE = os.getenv("DYNAMODB_NOTIFICATIONS_TABLE", "MedTrack_Notifications")
    DYNAMODB_MEDICINES_TABLE = os.getenv("DYNAMODB_MEDICINES_TABLE", "MedTrack_Medicines")
    DYNAMODB_INTAKE_LOGS_TABLE = os.getenv("DYNAMODB_INTAKE_LOGS_TABLE", "MedTrack_IntakeLogs")

    # Amazon SNS Topic ARN
    SNS_TOPIC_ARN = os.getenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:MedTrack_Alerts")

    # Local SQLite Database Path
    LOCAL_DB_PATH = BASE_DIR / "medtrack_local.db"
