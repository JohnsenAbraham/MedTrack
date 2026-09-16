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

    # AWS SNS Configuration
    SNS_TOPIC_ARN = os.getenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:MedTrack-Alerts")
    SNS_DLQ_ARN = os.getenv("SNS_DLQ_ARN", "arn:aws:sqs:us-east-1:123456789012:MedTrack-Alerts-DLQ")

    # AWS CloudWatch Logging
    CLOUDWATCH_LOG_GROUP = os.getenv("CLOUDWATCH_LOG_GROUP", "/aws/ec2/medtrack-production")

    # Local SQLite DB path for mock storage
    LOCAL_DB_PATH = BASE_DIR / "medtrack_local.db"

    # Google Authentication Configuration
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

    # Hospital Branding & Clinical Specialty Configuration
    HOSPITAL_NAME = os.getenv("HOSPITAL_NAME", "MedTrack Health System")
    HOSPITAL_DEPARTMENTS = [
        "Cardiology",
        "Neurology",
        "Internal Medicine",
        "Orthopedics & Sports",
        "Pediatrics & Child Health",
        "Dermatology",
        "General Medicine"
    ]
