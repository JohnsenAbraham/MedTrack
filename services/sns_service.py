import uuid
import datetime
import logging
from config import Config

logger = logging.getLogger(__name__)

class SNSService:
    """
    AWS Simple Notification Service (SNS) Client.
    Supports dual execution:
      - MOCK_AWS=True: Simulates SNS dispatches locally, logs to console, and stores in notifications table.
      - MOCK_AWS=False: Publishes directly to AWS SNS Topic using boto3.
    """

    def __init__(self, mock_aws=None, dynamodb_service=None):
        self.mock_aws = Config.MOCK_AWS if mock_aws is None else mock_aws
        self.topic_arn = Config.SNS_TOPIC_ARN
        self.dynamo_service = dynamodb_service

        if not self.mock_aws:
            import boto3
            boto_kwargs = {"region_name": Config.AWS_REGION}
            if Config.AWS_ACCESS_KEY_ID and Config.AWS_SECRET_ACCESS_KEY:
                boto_kwargs["aws_access_key_id"] = Config.AWS_ACCESS_KEY_ID
                boto_kwargs["aws_secret_access_key"] = Config.AWS_SECRET_ACCESS_KEY
            self.sns_client = boto3.client("sns", **boto_kwargs)
            logger.info("Initializing SNSService in LIVE AWS mode with Topic: %s", self.topic_arn)
        else:
            self.sns_client = None
            logger.info("Initializing SNSService in MOCK mode (simulating SNS dispatches)")

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
                logger.error("AWS SNS publish error: %s", e)
                return {
                    "Error": str(e),
                    "Status": "FAILED"
                }

    def send_appointment_booked_notification(self, patient_id: str, patient_name: str, doctor: str, date: str, time: str) -> dict:
        """Trigger alert when appointment is confirmed."""
        subject = "Appointment Confirmed - MedTrack"
        message = (
            f"Hello {patient_name},\n\n"
            f"Your appointment with {doctor} has been confirmed for {date} at {time}.\n"
            f"Please arrive 15 minutes before your scheduled appointment.\n\n"
            f"-- MedTrack Healthcare Team"
        )
        return self.publish_notification(patient_id=patient_id, message=message, subject=subject)

    def send_appointment_cancelled_notification(self, patient_id: str, patient_name: str, doctor: str, date: str, time: str) -> dict:
        """Trigger alert when appointment is cancelled."""
        subject = "Appointment Cancelled - MedTrack"
        message = (
            f"Hello {patient_name},\n\n"
            f"Your appointment with {doctor} scheduled for {date} at {time} has been cancelled.\n"
            f"You can rebook anytime from your MedTrack patient dashboard.\n\n"
            f"-- MedTrack Healthcare Team"
        )
        return self.publish_notification(patient_id=patient_id, message=message, subject=subject)

    def send_diagnosis_recorded_notification(self, patient_id: str, patient_name: str, doctor: str, diagnosis: str) -> dict:
        """Trigger alert when new diagnosis is entered."""
        subject = "Medical Record Update - MedTrack"
        message = (
            f"Hello {patient_name},\n\n"
            f"A new clinical diagnosis has been recorded in your patient file by {doctor}.\n"
            f"Diagnosis: {diagnosis}\n"
            f"Log in to your MedTrack patient portal to review complete treatment details.\n\n"
            f"-- MedTrack Healthcare Team"
        )
        return self.publish_notification(patient_id=patient_id, message=message, subject=subject)
