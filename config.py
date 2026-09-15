import os
from pathlib import Path
from dotenv import load_dotenv

# Base directory of the project
BASE_DIR = Path(__file__).resolve().parent

# Load environment variables from .env file if present
load_dotenv(BASE_DIR / ".env")

class Config:
    """Application configuration loaded from environment variables."""
    SECRET_KEY = os.getenv("SECRET_KEY", "medtrack-dev-insecure-key-change-me")

    # MOCK_AWS toggle: True runs local SQLite/mock layer without AWS credentials.
    MOCK_AWS = os.getenv("MOCK_AWS", "true").lower() in ("true", "1", "yes")

    # AWS General Configuration
    AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
    AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", None)
    AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", None)

    # AWS DynamoDB Table Names
    DYNAMODB_USERS_TABLE = os.getenv("DYNAMODB_USERS_TABLE", "MedTrack_Users")
    DYNAMODB_APPOINTMENTS_TABLE = os.getenv("DYNAMODB_APPOINTMENTS_TABLE", "MedTrack_Appointments")
    DYNAMODB_DIAGNOSES_TABLE = os.getenv("DYNAMODB_DIAGNOSES_TABLE", "MedTrack_Diagnoses")
    DYNAMODB_NOTIFICATIONS_TABLE = os.getenv("DYNAMODB_NOTIFICATIONS_TABLE", "MedTrack_Notifications")

    # AWS SNS Topic ARN
    SNS_TOPIC_ARN = os.getenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:MedTrack-Alerts")

    # Local SQLite DB path for mock storage
    LOCAL_DB_PATH = BASE_DIR / "medtrack_local.db"
