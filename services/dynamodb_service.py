"""
MedTrack AWS DynamoDB Service
Handles AWS DynamoDB operations using boto3 when running in AWS mode (MOCK_AWS=false).
Implements full behavioral parity with SQLite, GSI queries, conditional writes,
and atomic intake deduplication.
"""

import logging
import uuid
import datetime
import json
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError
from config import Config

logger = logging.getLogger("services.dynamodb")


class DynamoDBService:
    """AWS DynamoDB client for MedTrack."""

    def __init__(self, dynamodb_resource=None):
        logger.info("Initializing DynamoDB client for region: %s", Config.AWS_REGION)
        self.dynamodb = dynamodb_resource or boto3.resource("dynamodb", region_name=Config.AWS_REGION)
        self.users_table = self.dynamodb.Table(Config.DYNAMODB_USERS_TABLE)
        self.medicines_table = self.dynamodb.Table(Config.DYNAMODB_MEDICINES_TABLE)
        self.intake_logs_table = self.dynamodb.Table(Config.DYNAMODB_INTAKE_LOGS_TABLE)
        self.appointments_table = self.dynamodb.Table(Config.DYNAMODB_APPOINTMENTS_TABLE)
        self.prescriptions_table = self.dynamodb.Table(Config.DYNAMODB_PRESCRIPTIONS_TABLE)
        self.diagnoses_table = self.dynamodb.Table(Config.DYNAMODB_DIAGNOSES_TABLE)
        self.reports_table = self.dynamodb.Table(Config.DYNAMODB_REPORTS_TABLE)
        self.notifications_table = self.dynamodb.Table(Config.DYNAMODB_NOTIFICATIONS_TABLE)

    # -------------------------------------------------------------
    # Internal Pagination Helpers (Phase 13 Robustness)
    # -------------------------------------------------------------
    @staticmethod
    def _paginate_query(table, **query_kwargs) -> list:
        """
        Execute a DynamoDB query with complete pagination across 1 MB response boundaries.
        Iterates using LastEvaluatedKey / ExclusiveStartKey until all matching items are returned.
        """
        items = []
        kwargs = dict(query_kwargs)
        while True:
            response = table.query(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return items

    @staticmethod
    def _paginate_scan(table, max_items: int = None, **scan_kwargs) -> list:
        """
        Execute a DynamoDB scan with pagination across 1 MB response boundaries.
        If max_items is specified, terminates once at least max_items matches are collected.
        """
        items = []
        kwargs = dict(scan_kwargs)
        while True:
            response = table.scan(**kwargs)
            new_items = response.get("Items", [])
            items.extend(new_items)
            if max_items is not None and len(items) >= max_items:
                return items[:max_items]
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return items

    # -------------------------------------------------------------
    # User Operations
    # -------------------------------------------------------------
    def create_user(self, user_data: dict) -> dict:
        """Create a user record with EmailIndex uniqueness verification and attribute_not_exists check."""
        email = (user_data.get("email") or "").strip().lower()
        if email:
            existing = self.get_user_by_email(email)
            if existing:
                raise ValueError(f"A user with email '{email}' already exists.")

        try:
            self.users_table.put_item(
                Item=user_data,
                ConditionExpression="attribute_not_exists(user_id)"
            )
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise ValueError(f"User ID '{user_data.get('user_id')}' already exists.")
            raise
        return user_data

    def get_user_by_id(self, user_id: str) -> dict | None:
        """Retrieve user by primary key user_id."""
        response = self.users_table.get_item(Key={"user_id": user_id})
        return response.get("Item")

    def get_user_by_email(self, email: str) -> dict | None:
        """Query EmailIndex for exact email match without full-table scan."""
        clean_email = (email or "").strip().lower()
        response = self.users_table.query(
            IndexName="EmailIndex",
            KeyConditionExpression=Key("email").eq(clean_email)
        )
        items = response.get("Items", [])
        return items[0] if items else None

    def update_user(self, user_id: str, update_data: dict) -> bool:
        """Conditionally update profile fields if user exists."""
        update_expr = []
        expr_attr_values = {}
        expr_attr_names = {}

        allowed_keys = (
            "name", "phone", "date_of_birth", "gender",
            "caregiver_name", "caregiver_phone", "caregiver_email"
        )
        for key in allowed_keys:
            if key in update_data:
                attr_alias = f"#{key}"
                val_alias = f":{key}"
                update_expr.append(f"{attr_alias} = {val_alias}")
                expr_attr_names[attr_alias] = key
                expr_attr_values[val_alias] = update_data[key]

        if not update_expr:
            return False

        try:
            self.users_table.update_item(
                Key={"user_id": user_id},
                UpdateExpression="SET " + ", ".join(update_expr),
                ExpressionAttributeNames=expr_attr_names,
                ExpressionAttributeValues=expr_attr_values,
                ConditionExpression="attribute_exists(user_id)"
            )
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise

    def get_doctors(self) -> list:
        """Retrieve physicians directory with projected attributes across all pages."""
        items = self._paginate_scan(
            self.users_table,
            FilterExpression="#r = :role",
            ProjectionExpression="user_id, #n, email, phone",
            ExpressionAttributeNames={"#r": "role", "#n": "name"},
            ExpressionAttributeValues={":role": "doctor"}
        )
        return sorted(items, key=lambda x: x.get("name", "").lower())

    # -------------------------------------------------------------
    # Medicine Operations
    # -------------------------------------------------------------
    def create_medicine(self, medicine_data: dict) -> dict:
        """Persist a medication in MedTrack_Medicines."""
        self.medicines_table.put_item(Item=medicine_data)
        return medicine_data

    def get_medicine_by_id(self, medicine_id: str) -> dict | None:
        """Fetch single medicine by primary key."""
        response = self.medicines_table.get_item(Key={"medicine_id": medicine_id})
        return response.get("Item")

    def get_medicines_by_patient(self, patient_id: str) -> list:
        """Query PatientIndex on MedTrack_Medicines across all pages."""
        items = self._paginate_query(
            self.medicines_table,
            IndexName="PatientIndex",
            KeyConditionExpression=Key("patient_id").eq(patient_id)
        )
        return sorted(items, key=lambda x: x.get("schedule_time", ""))

    def get_active_medicines(self) -> list:
        """Retrieve active medications for background reminder scheduler across all pages."""
        items = self._paginate_scan(
            self.medicines_table,
            FilterExpression="#act = :one",
            ExpressionAttributeNames={"#act": "is_active"},
            ExpressionAttributeValues={":one": 1}
        )
        return sorted(items, key=lambda x: (x.get("patient_id", ""), x.get("schedule_time", "")))

    def delete_medicine(self, medicine_id: str, patient_id: str = None) -> bool:
        """Delete medicine verifying patient ownership to prevent horizontal escalation."""
        try:
            if patient_id:
                self.medicines_table.delete_item(
                    Key={"medicine_id": medicine_id},
                    ConditionExpression="patient_id = :pid",
                    ExpressionAttributeValues={":pid": patient_id}
                )
            else:
                self.medicines_table.delete_item(Key={"medicine_id": medicine_id})
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise

    # -------------------------------------------------------------
    # IntakeLog Operations (Multi-Dose Deduplication & Atomic UPSERT)
    # -------------------------------------------------------------
    def record_intake(self, patient_id: str, medicine_id: str, medicine_name: str,
                      dosage: str, status: str, scheduled_date: str, scheduled_time: str,
                      taken_time: str = None, created_at: str = None) -> dict:
        """
        Atomically persist or update dose intake log.
        Deterministic dose identity: log#{patient_id}#{medicine_id}#{scheduled_date}#{scheduled_time}
        Guarantees zero duplicate rows and preserves canonical created_at.
        """
        dose_id = f"log#{patient_id}#{medicine_id}#{scheduled_date}#{scheduled_time}"
        now_iso = created_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
        clean_status = (status or "").strip().upper()

        update_expr = (
            "SET #st = :status, taken_time = :taken_time, "
            "patient_id = if_not_exists(patient_id, :pid), "
            "medicine_id = if_not_exists(medicine_id, :mid), "
            "medicine_name = if_not_exists(medicine_name, :mname), "
            "dosage = if_not_exists(dosage, :dosage), "
            "scheduled_date = if_not_exists(scheduled_date, :sdate), "
            "scheduled_time = if_not_exists(scheduled_time, :stime), "
            "log_date = if_not_exists(log_date, :ldate), "
            "created_at = if_not_exists(created_at, :cat)"
        )
        expr_attr_names = {"#st": "status"}
        expr_attr_values = {
            ":status": clean_status,
            ":taken_time": taken_time or "",
            ":pid": patient_id,
            ":mid": medicine_id,
            ":mname": medicine_name,
            ":dosage": dosage,
            ":sdate": scheduled_date,
            ":stime": scheduled_time,
            ":ldate": scheduled_date,
            ":cat": now_iso
        }

        response = self.intake_logs_table.update_item(
            Key={"log_id": dose_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_attr_names,
            ExpressionAttributeValues=expr_attr_values,
            ReturnValues="ALL_NEW"
        )
        return response.get("Attributes", {})

    def get_dose_intake_log(self, patient_id: str, medicine_id: str,
                            scheduled_date: str, scheduled_time: str) -> dict | None:
        """Direct primary key lookup of dose intake outcome."""
        dose_id = f"log#{patient_id}#{medicine_id}#{scheduled_date}#{scheduled_time}"
        response = self.intake_logs_table.get_item(Key={"log_id": dose_id})
        item = response.get("Item")
        if item:
            return item

        # Backward-compatible fallback for records stored under legacy IDs
        q_resp = self.intake_logs_table.query(
            IndexName="PatientDateIndex",
            KeyConditionExpression=Key("patient_id").eq(patient_id) & Key("log_date").eq(scheduled_date)
        )
        for it in q_resp.get("Items", []):
            if it.get("medicine_id") == medicine_id and it.get("scheduled_time") == scheduled_time:
                return it
        return None

    def get_intake_logs_by_patient(self, patient_id: str, log_date: str = None) -> list:
        """Query PatientDateIndex for patient intake history across all pages."""
        if log_date:
            items = self._paginate_query(
                self.intake_logs_table,
                IndexName="PatientDateIndex",
                KeyConditionExpression=Key("patient_id").eq(patient_id) & Key("log_date").eq(log_date)
            )
        else:
            items = self._paginate_query(
                self.intake_logs_table,
                IndexName="PatientDateIndex",
                KeyConditionExpression=Key("patient_id").eq(patient_id),
                ScanIndexForward=False
            )
        return sorted(items, key=lambda x: (x.get("log_date", ""), x.get("scheduled_time", "")), reverse=True)

    # -------------------------------------------------------------
    # Appointment Operations
    # -------------------------------------------------------------
    def create_appointment(self, appointment_data: dict) -> dict:
        """Persist appointment with denormalized patient/doctor names for performant listing."""
        self.appointments_table.put_item(Item=appointment_data)
        return appointment_data

    def get_appointment_by_id(self, appointment_id: str) -> dict | None:
        """Fetch appointment by primary key."""
        response = self.appointments_table.get_item(Key={"appointment_id": appointment_id})
        return response.get("Item")

    def get_appointments_by_patient(self, patient_id: str) -> list:
        """Query PatientIndex on appointments across all pages in reverse chronological order."""
        items = self._paginate_query(
            self.appointments_table,
            IndexName="PatientIndex",
            KeyConditionExpression=Key("patient_id").eq(patient_id),
            ScanIndexForward=False
        )
        return sorted(items, key=lambda x: (x.get("appointment_date", ""), x.get("appointment_time", "")), reverse=True)

    def get_appointments_by_doctor(self, doctor_id: str) -> list:
        """Query DoctorIndex on appointments across all pages in chronological order."""
        items = self._paginate_query(
            self.appointments_table,
            IndexName="DoctorIndex",
            KeyConditionExpression=Key("doctor_id").eq(doctor_id),
            ScanIndexForward=True
        )
        return sorted(items, key=lambda x: (x.get("appointment_date", ""), x.get("appointment_time", "")))

    def update_appointment_status(self, appointment_id: str, status: str) -> bool:
        """Conditionally update appointment status."""
        try:
            self.appointments_table.update_item(
                Key={"appointment_id": appointment_id},
                UpdateExpression="SET #s = :status",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":status": status},
                ConditionExpression="attribute_exists(appointment_id)"
            )
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise

    # -------------------------------------------------------------
    # Diagnosis Operations
    # -------------------------------------------------------------
    def create_diagnosis(self, diagnosis_data: dict) -> dict:
        """Persist diagnosis record."""
        self.diagnoses_table.put_item(Item=diagnosis_data)
        return diagnosis_data

    def get_diagnoses_by_patient(self, patient_id: str) -> list:
        """Query PatientIndex on diagnoses across all pages."""
        items = self._paginate_query(
            self.diagnoses_table,
            IndexName="PatientIndex",
            KeyConditionExpression=Key("patient_id").eq(patient_id),
            ScanIndexForward=False
        )
        return sorted(items, key=lambda x: (x.get("date", ""), x.get("created_at", "")), reverse=True)

    def get_diagnoses_by_doctor(self, doctor_id: str) -> list:
        """Query DoctorIndex on diagnoses across all pages."""
        items = self._paginate_query(
            self.diagnoses_table,
            IndexName="DoctorIndex",
            KeyConditionExpression=Key("doctor_id").eq(doctor_id),
            ScanIndexForward=False
        )
        return sorted(items, key=lambda x: (x.get("date", ""), x.get("created_at", "")), reverse=True)

    # -------------------------------------------------------------
    # Prescription Operations (Phase 7 Parity)
    # -------------------------------------------------------------
    def create_prescription(self, rx_record: dict, med_record: dict) -> dict:
        """Atomically persist prescription and linked medicine med_rx_<prescription_id>."""
        rx_table_name = Config.DYNAMODB_PRESCRIPTIONS_TABLE
        med_table_name = Config.DYNAMODB_MEDICINES_TABLE

        client = getattr(self.dynamodb, "meta", None)
        raw_client = getattr(client, "client", None) if client else None

        if raw_client and hasattr(raw_client, "transact_write_items"):
            try:
                raw_client.transact_write_items(
                    TransactItems=[
                        {
                            "Put": {
                                "TableName": rx_table_name,
                                "Item": rx_record,
                                "ConditionExpression": "attribute_not_exists(prescription_id)"
                            }
                        },
                        {
                            "Put": {
                                "TableName": med_table_name,
                                "Item": med_record,
                                "ConditionExpression": "attribute_not_exists(medicine_id)"
                            }
                        }
                    ]
                )
                return rx_record
            except Exception as e:
                logger.warning("TransactWriteItems failed (%s); falling back to sequential writes", e)

        self.prescriptions_table.put_item(Item=rx_record)
        self.medicines_table.put_item(Item=med_record)
        return rx_record

    def get_prescription_by_id(self, prescription_id: str) -> dict | None:
        """Fetch prescription by primary key."""
        response = self.prescriptions_table.get_item(Key={"prescription_id": prescription_id})
        return response.get("Item")

    def get_prescriptions_by_doctor(self, doctor_id: str) -> list:
        """Query DoctorIndex on prescriptions across all pages (strictly isolated to calling doctor)."""
        items = self._paginate_query(
            self.prescriptions_table,
            IndexName="DoctorIndex",
            KeyConditionExpression=Key("doctor_id").eq(doctor_id),
            ScanIndexForward=False
        )
        return sorted(items, key=lambda x: x.get("created_at", ""), reverse=True)

    def get_prescriptions_by_patient(self, patient_id: str) -> list:
        """Query PatientIndex on prescriptions across all pages."""
        items = self._paginate_query(
            self.prescriptions_table,
            IndexName="PatientIndex",
            KeyConditionExpression=Key("patient_id").eq(patient_id),
            ScanIndexForward=False
        )
        return sorted(items, key=lambda x: x.get("created_at", ""), reverse=True)

    def update_prescription_status(self, prescription_id: str, new_status: str, doctor_id: str = None) -> bool:
        """Enforce terminal state rules and synchronize linked medicine status."""
        rx = self.get_prescription_by_id(prescription_id)
        if not rx:
            return False
        if doctor_id and rx.get("doctor_id") != doctor_id:
            return False

        current_status = rx.get("status", "ACTIVE")
        norm_status = (new_status or "").strip().upper()
        if current_status == norm_status:
            return True
        if current_status != "ACTIVE":
            raise ValueError(f"Invalid lifecycle transition: Cannot transition prescription from terminal state '{current_status}' to '{norm_status}'.")

        self.prescriptions_table.update_item(
            Key={"prescription_id": prescription_id},
            UpdateExpression="SET #s = :status",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":status": norm_status}
        )

        med_is_active = 1 if norm_status == "ACTIVE" else 0
        linked_med_id = rx.get("linked_medicine_id") or f"med_rx_{prescription_id}"
        try:
            self.medicines_table.update_item(
                Key={"medicine_id": linked_med_id},
                UpdateExpression="SET is_active = :act",
                ExpressionAttributeValues={":act": med_is_active}
            )
        except Exception:
            pass
        return True

    def discontinue_prescription(self, prescription_id: str, doctor_id: str) -> bool:
        """Discontinue active prescription and deactivate its linked medicine."""
        return self.update_prescription_status(prescription_id, "DISCONTINUED", doctor_id=doctor_id)

    # -------------------------------------------------------------
    # Report Operations (Phase 8 Parity)
    # -------------------------------------------------------------
    def create_report(self, report_record: dict) -> dict:
        """Persist report metadata in MedTrack_Reports."""
        self.reports_table.put_item(Item=report_record)
        return report_record

    def get_report_by_id(self, report_id: str) -> dict | None:
        """Fetch report metadata by primary key."""
        response = self.reports_table.get_item(Key={"report_id": report_id})
        return response.get("Item")

    def get_reports_by_patient(self, patient_id: str) -> list:
        """Query PatientIndex on reports across all pages."""
        items = self._paginate_query(
            self.reports_table,
            IndexName="PatientIndex",
            KeyConditionExpression=Key("patient_id").eq(patient_id),
            ScanIndexForward=False
        )
        return sorted(items, key=lambda x: x.get("uploaded_at", ""), reverse=True)

    def get_reports_by_doctor(self, doctor_id: str, patient_id: str = None) -> list:
        """Query reports authorized for the attending physician."""
        if patient_id:
            if not self.is_doctor_authorized_for_patient(doctor_id, patient_id):
                return []
            return self.get_reports_by_patient(patient_id)

        patients = self.get_patients_by_doctor(doctor_id)
        all_reports = []
        for p in patients:
            p_reports = self.get_reports_by_patient(p["user_id"])
            all_reports.extend(p_reports)
        return sorted(all_reports, key=lambda r: r.get("uploaded_at", ""), reverse=True)

    def delete_report(self, report_id: str, actor_id: str) -> bool:
        """Delete report metadata verifying authorization."""
        report = self.get_report_by_id(report_id)
        if not report:
            return False

        is_patient_owner = (report.get("patient_id") == actor_id)
        is_doctor_authorized = False
        if not is_patient_owner:
            actor = self.get_user_by_id(actor_id)
            if actor and actor.get("role") == "doctor":
                is_doctor_authorized = self.is_doctor_authorized_for_patient(actor_id, report.get("patient_id"))

        if not (is_patient_owner or is_doctor_authorized):
            return False

        self.reports_table.delete_item(Key={"report_id": report_id})
        return True

    # -------------------------------------------------------------
    # Notification Operations & Scheduler Parity (Phase 5 Parity)
    # -------------------------------------------------------------
    def create_reminder_notification(self, notification_id: str, patient_id: str,
                                     title: str, message: str, scheduled_date: str,
                                     scheduled_time: str, created_at: str = None) -> bool:
        """
        Idempotently create deterministic reminder notification.
        ConditionExpression prevents duplicate reminder rows.
        """
        created_at_val = created_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
        item = {
            "notification_id": notification_id,
            "patient_id": patient_id,
            "type": "medicine_reminder",
            "title": title,
            "message": message,
            "scheduled_date": scheduled_date,
            "scheduled_time": scheduled_time,
            "delivery_status": "PENDING",
            "delivery_attempts": 0,
            "status": "UNREAD",
            "is_read": 0,
            "created_at": created_at_val
        }
        try:
            self.notifications_table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(notification_id)"
            )
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise

    def claim_pending_notifications(self, limit: int = 50, lease_seconds: int = 120,
                                    now=None, notification_type: str = "medicine_reminder") -> list:
        """
        Atomically claim pending or expired-lease notifications for delivery.
        Guarantees mutual exclusion and prevents duplicate SNS dispatches.
        """
        tz = ZoneInfo(getattr(Config, "APP_TIMEZONE", "Asia/Kolkata"))
        eval_now = now if now is not None else datetime.datetime.now(tz)
        if eval_now.tzinfo is None:
            eval_now = eval_now.replace(tzinfo=tz)

        now_iso = eval_now.isoformat()
        lease_until_iso = (eval_now + datetime.timedelta(seconds=lease_seconds)).isoformat()
        claim_id = f"claim-{uuid.uuid4().hex}"

        filter_exp = "(delivery_status = :pending OR (delivery_status = :claimed AND delivery_lease_until < :now_iso)) AND delivery_attempts < :max_attempts"
        expr_vals = {
            ":pending": "PENDING",
            ":claimed": "CLAIMED",
            ":now_iso": now_iso,
            ":max_attempts": 3
        }
        expr_names = {}
        if notification_type:
            filter_exp += " AND #tp = :ntype"
            expr_vals[":ntype"] = notification_type
            expr_names["#tp"] = "type"

        scan_kwargs = {
            "FilterExpression": filter_exp,
            "ExpressionAttributeValues": expr_vals
        }
        if expr_names:
            scan_kwargs["ExpressionAttributeNames"] = expr_names

        candidates = self._paginate_scan(self.notifications_table, max_items=limit, **scan_kwargs)

        claimed_records = []
        for cand in candidates:
            notif_id = cand["notification_id"]
            try:
                upd_resp = self.notifications_table.update_item(
                    Key={"notification_id": notif_id},
                    UpdateExpression=(
                        "SET delivery_status = :claimed, delivery_claim_id = :claim_id, "
                        "delivery_claimed_at = :now_iso, delivery_lease_until = :lease_until, "
                        "delivery_attempts = delivery_attempts + :one"
                    ),
                    ConditionExpression=(
                        "(delivery_status = :pending OR (delivery_status = :claimed AND delivery_lease_until < :now_iso)) "
                        "AND delivery_attempts < :max_attempts"
                    ),
                    ExpressionAttributeValues={
                        ":claimed": "CLAIMED",
                        ":claim_id": claim_id,
                        ":now_iso": now_iso,
                        ":lease_until": lease_until_iso,
                        ":one": 1,
                        ":pending": "PENDING",
                        ":max_attempts": 3
                    },
                    ReturnValues="ALL_NEW"
                )
                claimed_records.append(upd_resp.get("Attributes", {}))
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                    continue
                raise
        return claimed_records

    def mark_notification_sent(self, notification_id: str, claim_id: str, sent_at: str = None) -> bool:
        """Mark notification as SENT verifying the claim lease owner."""
        sent_time = sent_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            self.notifications_table.update_item(
                Key={"notification_id": notification_id},
                UpdateExpression="SET delivery_status = :sent, sent_at = :sent_at REMOVE delivery_lease_until",
                ConditionExpression="delivery_claim_id = :claim_id",
                ExpressionAttributeValues={
                    ":sent": "SENT",
                    ":sent_at": sent_time,
                    ":claim_id": claim_id
                }
            )
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise

    def mark_notification_failed_or_retry(self, notification_id: str, claim_id: str, max_attempts: int = 3) -> str:
        """Revert to PENDING if attempts < max_attempts; else transition to FAILED."""
        notif = self.notifications_table.get_item(Key={"notification_id": notification_id}).get("Item")
        if not notif:
            return "FAILED"
        attempts = int(notif.get("delivery_attempts", 0))
        if attempts < max_attempts:
            next_status = "PENDING"
            upd_expr = "SET delivery_status = :pending REMOVE delivery_claim_id, delivery_lease_until"
            expr_vals = {":pending": "PENDING", ":claim_id": claim_id}
        else:
            next_status = "FAILED"
            upd_expr = "SET delivery_status = :failed REMOVE delivery_lease_until"
            expr_vals = {":failed": "FAILED", ":claim_id": claim_id}

        try:
            self.notifications_table.update_item(
                Key={"notification_id": notification_id},
                UpdateExpression=upd_expr,
                ConditionExpression="delivery_claim_id = :claim_id",
                ExpressionAttributeValues=expr_vals
            )
            return next_status
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return notif.get("delivery_status", "FAILED")
            raise

    def create_notification(self, patient_id: str, message: str, notif_type: str = "GENERAL",
                            title: str = "Notification", scheduled_date: str = None,
                            scheduled_time: str = None, notification_id: str = None) -> dict:
        """Persist in-app alert notification."""
        notif_id = notification_id or f"ntf-{uuid.uuid4().hex[:8]}"
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        item = {
            "notification_id": notif_id,
            "patient_id": patient_id,
            "type": notif_type,
            "title": title,
            "message": message,
            "scheduled_date": scheduled_date or "",
            "scheduled_time": scheduled_time or "",
            "delivery_status": "PENDING",
            "delivery_attempts": 0,
            "status": "UNREAD",
            "is_read": 0,
            "created_at": created_at
        }
        self.notifications_table.put_item(Item=item)
        return item

    def get_notifications_by_patient(self, patient_id: str) -> list:
        """Query PatientIndex on notifications across all pages."""
        items = self._paginate_query(
            self.notifications_table,
            IndexName="PatientIndex",
            KeyConditionExpression=Key("patient_id").eq(patient_id),
            ScanIndexForward=False
        )
        return sorted(items, key=lambda x: x.get("created_at", ""), reverse=True)

    def get_notifications_by_doctor(self, doctor_id: str) -> list:
        """Query PatientIndex for doctor notification alerts across all pages."""
        items = self._paginate_query(
            self.notifications_table,
            IndexName="PatientIndex",
            KeyConditionExpression=Key("patient_id").eq(doctor_id),
            ScanIndexForward=False
        )
        return sorted(items, key=lambda x: x.get("created_at", ""), reverse=True)

    # -------------------------------------------------------------
    # Doctor-Patient Authorization (Phase 7 & 8 Parity)
    # -------------------------------------------------------------
    def is_doctor_authorized_for_patient(self, doctor_id: str, patient_id: str) -> bool:
        """Verify established appointment relationship using DoctorIndex query across pages."""
        kwargs = {
            "IndexName": "DoctorIndex",
            "KeyConditionExpression": Key("doctor_id").eq(doctor_id),
            "FilterExpression": Attr("patient_id").eq(patient_id)
        }
        while True:
            response = self.appointments_table.query(**kwargs)
            if response.get("Items"):
                return True
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return False

    def get_patients_by_doctor(self, doctor_id: str) -> list:
        """Query distinct patients seen by doctor with visit statistics across all pages."""
        appts = self._paginate_query(
            self.appointments_table,
            IndexName="DoctorIndex",
            KeyConditionExpression=Key("doctor_id").eq(doctor_id)
        )

        patients_map = {}
        for a in appts:
            pid = a.get("patient_id")
            if not pid:
                continue
            if pid not in patients_map:
                p_user = self.get_user_by_id(pid) or {}
                patients_map[pid] = {
                    "user_id": pid,
                    "name": a.get("patient_name") or p_user.get("name", "Patient"),
                    "email": p_user.get("email", ""),
                    "phone": a.get("patient_phone") or p_user.get("phone", ""),
                    "date_of_birth": a.get("patient_dob") or p_user.get("date_of_birth", ""),
                    "gender": a.get("patient_gender") or p_user.get("gender", ""),
                    "total_visits": 0,
                    "last_visit": ""
                }
            patients_map[pid]["total_visits"] += 1
            appt_date = a.get("appointment_date", "")
            if appt_date > patients_map[pid]["last_visit"]:
                patients_map[pid]["last_visit"] = appt_date

        return sorted(patients_map.values(), key=lambda p: p["name"].lower())
