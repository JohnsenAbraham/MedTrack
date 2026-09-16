"""
MedTrack AWS DynamoDB Service
Handles AWS DynamoDB operations using boto3 when running in AWS mode (MOCK_AWS=false).
"""

import boto3
import logging
from config import Config

logger = logging.getLogger("services.dynamodb")

class DynamoDBService:
    """AWS DynamoDB client for MedTrack."""

    def __init__(self):
        logger.info("Initializing DynamoDB client for region: %s", Config.AWS_REGION)
        self.dynamodb = boto3.resource("dynamodb", region_name=Config.AWS_REGION)
        self.users_table = self.dynamodb.Table(Config.DYNAMODB_USERS_TABLE)
        self.appointments_table = self.dynamodb.Table(Config.DYNAMODB_APPOINTMENTS_TABLE)
        self.diagnoses_table = self.dynamodb.Table(Config.DYNAMODB_DIAGNOSES_TABLE)
        self.notifications_table = self.dynamodb.Table(Config.DYNAMODB_NOTIFICATIONS_TABLE)
        self.medicines_table = self.dynamodb.Table(Config.DYNAMODB_MEDICINES_TABLE)
        self.intake_logs_table = self.dynamodb.Table(Config.DYNAMODB_INTAKE_LOGS_TABLE)

    # -------------------------------------------------------------
    # User Operations
    # -------------------------------------------------------------
    def create_user(self, user_data: dict) -> dict:
        self.users_table.put_item(Item=user_data)
        return user_data

    def get_user_by_id(self, user_id: str):
        response = self.users_table.get_item(Key={"user_id": user_id})
        return response.get("Item")

    def get_user_by_email(self, email: str):
        # Scan for matching email (for student-level demo)
        response = self.users_table.scan(
            FilterExpression="email = :email",
            ExpressionAttributeValues={":email": email.strip().lower()}
        )
        items = response.get("Items", [])
        return items[0] if items else None

    def update_user(self, user_id: str, update_data: dict) -> bool:
        update_expr = []
        expr_attr_values = {}
        expr_attr_names = {}

        for key, val in update_data.items():
            if key in ("name", "phone", "date_of_birth", "gender"):
                attr_alias = f"#{key}"
                val_alias = f":{key}"
                update_expr.append(f"{attr_alias} = {val_alias}")
                expr_attr_names[attr_alias] = key
                expr_attr_values[val_alias] = val

        if not update_expr:
            return False

        self.users_table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET " + ", ".join(update_expr),
            ExpressionAttributeNames=expr_attr_names,
            ExpressionAttributeValues=expr_attr_values
        )
        return True

    def get_doctors(self) -> list:
        response = self.users_table.scan(
            FilterExpression="#r = :role",
            ExpressionAttributeNames={"#r": "role"},
            ExpressionAttributeValues={":role": "doctor"}
        )
        return response.get("Items", [])

    # -------------------------------------------------------------
    # Appointment Operations
    # -------------------------------------------------------------
    def create_appointment(self, appointment_data: dict) -> dict:
        self.appointments_table.put_item(Item=appointment_data)
        return appointment_data

    def get_appointment_by_id(self, appointment_id: str):
        response = self.appointments_table.get_item(Key={"appointment_id": appointment_id})
        return response.get("Item")

    def get_appointments_by_patient(self, patient_id: str) -> list:
        response = self.appointments_table.scan(
            FilterExpression="patient_id = :pid",
            ExpressionAttributeValues={":pid": patient_id}
        )
        return sorted(response.get("Items", []), key=lambda x: x.get("appointment_date", ""), reverse=True)

    def get_appointments_by_doctor(self, doctor_id: str) -> list:
        response = self.appointments_table.scan(
            FilterExpression="doctor_id = :did",
            ExpressionAttributeValues={":did": doctor_id}
        )
        return sorted(response.get("Items", []), key=lambda x: x.get("appointment_date", ""))

    def update_appointment_status(self, appointment_id: str, status: str) -> bool:
        self.appointments_table.update_item(
            Key={"appointment_id": appointment_id},
            UpdateExpression="SET #s = :status",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":status": status}
        )
        return True

    # -------------------------------------------------------------
    # Diagnosis Operations
    # -------------------------------------------------------------
    def create_diagnosis(self, diagnosis_data: dict) -> dict:
        self.diagnoses_table.put_item(Item=diagnosis_data)
        return diagnosis_data

    def get_diagnoses_by_patient(self, patient_id: str) -> list:
        response = self.diagnoses_table.scan(
            FilterExpression="patient_id = :pid",
            ExpressionAttributeValues={":pid": patient_id}
        )
        return sorted(response.get("Items", []), key=lambda x: x.get("date", ""), reverse=True)

    def get_diagnoses_by_doctor(self, doctor_id: str) -> list:
        response = self.diagnoses_table.scan(
            FilterExpression="doctor_id = :did",
            ExpressionAttributeValues={":did": doctor_id}
        )
        return sorted(response.get("Items", []), key=lambda x: x.get("date", ""), reverse=True)

    # -------------------------------------------------------------
    # Notification Operations
    # -------------------------------------------------------------
    def create_notification(self, patient_id: str, message: str) -> dict:
        import uuid
        import datetime
        item = {
            "notification_id": f"ntf-{uuid.uuid4().hex[:8]}",
            "patient_id": patient_id,
            "message": message,
            "status": "UNREAD",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        self.notifications_table.put_item(Item=item)
        return item

    def get_notifications_by_patient(self, patient_id: str) -> list:
        response = self.notifications_table.scan(
            FilterExpression="patient_id = :pid",
            ExpressionAttributeValues={":pid": patient_id}
        )
        return sorted(response.get("Items", []), key=lambda x: x.get("created_at", ""), reverse=True)

    # -------------------------------------------------------------
    # Patient Medicine & Intake Operations
    # -------------------------------------------------------------
    def create_medicine(self, medicine_data: dict) -> dict:
        self.medicines_table.put_item(Item=medicine_data)
        return medicine_data

    def get_medicines_by_patient(self, patient_id: str) -> list:
        response = self.medicines_table.scan(
            FilterExpression="patient_id = :pid",
            ExpressionAttributeValues={":pid": patient_id}
        )
        return sorted(response.get("Items", []), key=lambda x: x.get("schedule_time", ""))

    def delete_medicine(self, medicine_id: str) -> bool:
        self.medicines_table.delete_item(Key={"medicine_id": medicine_id})
        return True

    def create_intake_log(self, log_data: dict) -> dict:
        self.intake_logs_table.put_item(Item=log_data)
        return log_data

    def get_intake_logs_by_patient(self, patient_id: str, log_date: str = None) -> list:
        if log_date:
            response = self.intake_logs_table.scan(
                FilterExpression="patient_id = :pid AND log_date = :ldate",
                ExpressionAttributeValues={":pid": patient_id, ":ldate": log_date}
            )
        else:
            response = self.intake_logs_table.scan(
                FilterExpression="patient_id = :pid",
                ExpressionAttributeValues={":pid": patient_id}
            )
        return sorted(response.get("Items", []), key=lambda x: x.get("created_at", ""), reverse=True)
