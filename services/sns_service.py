"""
MedTrack Service Abstraction Layer: Amazon SNS Interface
Dispatches clinical notifications for the 4 mandatory events:
1. Appointment booked
2. Appointment cancelled
3. Appointment confirmed
4. Diagnosis submitted
"""

import logging
from config import Config

logger = logging.getLogger("services.sns")

class SNSService:
    """Service abstraction for Amazon SNS notifications."""

    def __init__(self):
        self.mock_aws = Config.MOCK_AWS
        self.topic_arn = Config.SNS_TOPIC_ARN
        self.region = Config.AWS_REGION

        if not self.mock_aws:
            import boto3
            self.sns_client = boto3.client("sns", region_name=self.region)
        else:
            self.sns_client = None

    def publish_notification(self, patient_id: str, message: str, subject: str = "MedTrack Notification") -> bool:
        """
        Publish notification message.
        In local mode: logs the simulated alert.
        In AWS mode: publishes message to the configured Amazon SNS topic.
        """
        if self.mock_aws:
            safe_sub = subject.encode("ascii", "replace").decode("ascii")
            safe_msg = message.strip().encode("ascii", "replace").decode("ascii")
            print("\n" + "=" * 55)
            print("[SIMULATED AMAZON SNS DISPATCH]")
            print(f"Topic ARN : {self.topic_arn}")
            print(f"Patient ID: {patient_id}")
            print(f"Subject   : {safe_sub}")
            print(f"Message   : {safe_msg}")
            print("=" * 55 + "\n")
            logger.info("Simulated SNS dispatch to patient %s: %s", patient_id, safe_sub)
            return True

        try:
            response = self.sns_client.publish(
                TopicArn=self.topic_arn,
                Subject=subject[:100],
                Message=message,
                MessageAttributes={
                    "patient_id": {
                        "DataType": "String",
                        "StringValue": patient_id
                    }
                }
            )
            logger.info("Published message to SNS Topic: MsgId=%s", response.get("MessageId"))
            return True
        except Exception as e:
            logger.error("Failed to publish to Amazon SNS: %s", e)
            return False

    # -------------------------------------------------------------
    # The 4 Mandatory Application Events
    # -------------------------------------------------------------
    def notify_appointment_booked(self, patient_id: str, patient_name: str, doctor_name: str, date: str, time: str):
        subject = "Appointment Booked - MedTrack"
        message = (
            f"Hello {patient_name},\n\n"
            f"Your appointment request with {doctor_name} on {date} at {time} has been received.\n"
            f"Current status: PENDING confirmation by the physician."
        )
        return self.publish_notification(patient_id, message, subject)

    def notify_appointment_confirmed(self, patient_id: str, patient_name: str, doctor_name: str, date: str, time: str):
        subject = "Appointment Confirmed - MedTrack"
        message = (
            f"Hello {patient_name},\n\n"
            f"Good news! Your appointment with {doctor_name} on {date} at {time} has been CONFIRMED."
        )
        return self.publish_notification(patient_id, message, subject)

    def notify_appointment_cancelled(self, patient_id: str, patient_name: str, doctor_name: str, date: str, time: str):
        subject = "Appointment Cancelled - MedTrack"
        message = (
            f"Hello {patient_name},\n\n"
            f"Your appointment with {doctor_name} on {date} at {time} has been CANCELLED."
        )
        return self.publish_notification(patient_id, message, subject)

    def notify_diagnosis_submitted(self, patient_id: str, patient_name: str, doctor_name: str, date: str):
        subject = "New Diagnosis Record - MedTrack"
        message = (
            f"Hello {patient_name},\n\n"
            f"{doctor_name} has submitted a new clinical diagnosis for your consultation on {date}.\n"
            f"Please log in to your MedTrack patient dashboard to review your medical record."
        )
        return self.publish_notification(patient_id, message, subject)

    # -------------------------------------------------------------
    # Patient Medicine Intake & Dose Reminder Events
    # -------------------------------------------------------------
    def notify_dose_reminder(self, patient_id: str, patient_name: str, medicine_name: str, dosage: str, scheduled_time: str, meal_timing: str = "After Food"):
        """Send automated medication intake dose reminder to patient."""
        subject = f"[REMINDER] Time to take {medicine_name} ({dosage})"
        message = (
            f"Hello {patient_name},\n\n"
            f"This is your MedTrack intake reminder!\n\n"
            f"- Medicine: {medicine_name}\n"
            f"- Dosage: {dosage}\n"
            f"- Scheduled Time: {scheduled_time}\n"
            f"- Instructions: {meal_timing}\n\n"
            f"Please take your medicine as prescribed and mark it as TAKEN in your MedTrack dashboard."
        )
        return self.publish_notification(patient_id, message, subject)

    def notify_medicine_added(self, patient_id: str, patient_name: str, medicine_name: str, dosage: str, scheduled_time: str):
        """Notify patient when a new medicine is scheduled."""
        subject = f"New Medication Scheduled: {medicine_name}"
        message = (
            f"Hello {patient_name},\n\n"
            f"You have registered a new medication on MedTrack:\n\n"
            f"- Medicine: {medicine_name}\n"
            f"- Dosage: {dosage}\n"
            f"- Schedule: {scheduled_time}\n\n"
            f"MedTrack will monitor your schedule and alert you when it's time for each dose."
        )
        return self.publish_notification(patient_id, message, subject)
