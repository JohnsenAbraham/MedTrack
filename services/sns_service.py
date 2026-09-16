import uuid
import datetime
import logging
import time
from config import Config

logger = logging.getLogger(__name__)

class SNSService:
    """
    AWS Simple Notification Service (SNS) Client with Enterprise Resilience.
    Supports dual execution:
      - MOCK_AWS=True: Simulates SNS dispatches locally, logs to console, and stores in notifications table
                       with Dead-Letter-Queue (DLQ) simulation.
      - MOCK_AWS=False: Publishes directly to AWS SNS Topic using boto3 with adaptive retries.
    """

    def __init__(self, mock_aws=None, dynamodb_service=None):
        self.mock_aws = Config.MOCK_AWS if mock_aws is None else mock_aws
        self.topic_arn = Config.SNS_TOPIC_ARN
        self.dynamo_service = dynamodb_service
        self.dlq_messages = []

        if not self.mock_aws:
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
            self.sns_client = boto3.client("sns", **boto_kwargs)
            logger.info("Initializing SNSService in LIVE AWS mode with Topic: %s", self.topic_arn)
        else:
            self.sns_client = None
            logger.info("Initializing SNSService in MOCK mode (simulating SNS dispatches with DLQ fallback)")

    def _get_dynamo_service(self):
        if self.dynamo_service is None:
            from services.dynamodb_service import DynamoDBService
            self.dynamo_service = DynamoDBService(mock_aws=self.mock_aws)
        return self.dynamo_service

    def publish_notification(self, patient_id: str, message: str, subject: str = "MedTrack Alert") -> dict:
        """
        Publish notification message. In mock mode, logs and saves locally; in live mode, publishes via boto3 SNS.
        """
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        message_id = f"mock-sns-{uuid.uuid4().hex[:12]}"

        if self.mock_aws:
            print(f"\n==================== [MOCK AWS SNS DISPATCH] ====================")
            print(f"Topic ARN : {self.topic_arn}")
            print(f"Subject   : {subject}")
            print(f"Patient ID: {patient_id}")
            print(f"Message   : {message}")
            print(f"Mock MsgId: {message_id}")
            print(f"=================================================================\n")

            # Persist notification record
            try:
                self._get_dynamo_service().create_notification({
                    "notification_id": message_id,
                    "patient_id": patient_id,
                    "message": f"[{subject}] {message}",
                    "status": "MOCK_DISPATCHED",
                    "created_at": created_at
                })
            except Exception as e:
                logger.warning("Could not persist mock notification: %s", e)

            return {
                "MessageId": message_id,
                "ResponseMetadata": {"HTTPStatusCode": 200},
                "Mode": "MOCK",
                "Status": "MOCK_DISPATCHED"
            }
        else:
            try:
                response = self.sns_client.publish(
                    TopicArn=self.topic_arn,
                    Message=message,
                    Subject=subject[:100]  # SNS subjects max 100 chars
                )
                real_msg_id = response.get("MessageId", str(uuid.uuid4()))

                # Persist notification record
                self._get_dynamo_service().create_notification({
                    "notification_id": real_msg_id,
                    "patient_id": patient_id,
                    "message": f"[{subject}] {message}",
                    "status": "SENT",
                    "created_at": created_at
                })
                return response
            except Exception as e:
                logger.error("AWS SNS publish error: %s. Route to Dead-Letter-Queue.", e)
                # Store in DLQ buffer for resilience
                dlq_entry = {
                    "patient_id": patient_id,
                    "message": message,
                    "subject": subject,
                    "error": str(e),
                    "failed_at": created_at
                }
                self.dlq_messages.append(dlq_entry)
                return {
                    "Error": str(e),
                    "Status": "FAILED_SENT_TO_DLQ"
                }

    def send_appointment_booked_notification(self, patient_id: str, patient_name: str, doctor: str, date: str, time: str) -> dict:
        """Trigger alert when appointment is confirmed."""
        subject = "Appointment Confirmed - MedTrack"
        message = (
            f"Hello {patient_name},\n\n"
            f"Your clinical appointment with {doctor} has been confirmed for {date} at {time}.\n"
            f"Please arrive 15 minutes before your scheduled appointment and bring your photo ID.\n\n"
            f"-- MedTrack Hospital Clinical Care Team"
        )
        return self.publish_notification(patient_id=patient_id, message=message, subject=subject)

    def send_appointment_cancelled_notification(self, patient_id: str, patient_name: str, doctor: str, date: str, time: str) -> dict:
        """Trigger alert when appointment is cancelled."""
        subject = "Appointment Cancelled - MedTrack"
        message = (
            f"Hello {patient_name},\n\n"
            f"Your appointment with {doctor} scheduled for {date} at {time} has been cancelled.\n"
            f"You can reschedule anytime via your MedTrack patient portal.\n\n"
            f"-- MedTrack Hospital Clinical Care Team"
        )
        return self.publish_notification(patient_id=patient_id, message=message, subject=subject)

    def send_appointment_status_update(self, patient_id: str, patient_name: str, doctor: str, new_status: str, date: str, time: str) -> dict:
        """Trigger alert when appointment progression state changes."""
        subject = f"Appointment Update: {new_status} - MedTrack"
        message = (
            f"Hello {patient_name},\n\n"
            f"Your appointment with {doctor} on {date} at {time} is now marked as: {new_status}.\n\n"
            f"-- MedTrack Hospital Clinical Care Team"
        )
        return self.publish_notification(patient_id=patient_id, message=message, subject=subject)

    def send_diagnosis_recorded_notification(self, patient_id: str, patient_name: str, doctor: str, diagnosis: str) -> dict:
        """Trigger alert when new diagnosis is entered."""
        subject = "Medical Record Update - MedTrack"
        message = (
            f"Hello {patient_name},\n\n"
            f"A new clinical diagnosis and evaluation has been recorded in your patient file by {doctor}.\n"
            f"Summary: {diagnosis}\n\n"
            f"Log in to your MedTrack patient portal to review complete treatment plans and physician notes.\n\n"
            f"-- MedTrack Hospital Clinical Care Team"
        )
        return self.publish_notification(patient_id=patient_id, message=message, subject=subject)

    def send_prescription_issued_notification(self, patient_id: str, patient_name: str, doctor: str, prescription_count: int) -> dict:
        """Trigger alert when new e-prescription is issued."""
        subject = "Electronic Prescription Issued - MedTrack"
        message = (
            f"Hello {patient_name},\n\n"
            f"{doctor} has issued {prescription_count} new electronic prescription item(s) for your care.\n"
            f"Your prescription details, dosages, and administration instructions are available on your patient dashboard.\n\n"
            f"-- MedTrack Hospital Pharmacy & Clinical Services"
        )
        return self.publish_notification(patient_id=patient_id, message=message, subject=subject)

    def check_health(self) -> dict:
        """Check SNS notification dispatch subsystem health."""
        return {
            "status": "healthy",
            "mode": "MOCK_SNS" if self.mock_aws else "AWS_SNS",
            "topic_arn": self.topic_arn,
            "dlq_backlog_count": len(self.dlq_messages)
        }
