"""
Phase 9 — DynamoDB Parity & GSI Optimization Test Suite
Validates complete DynamoDB behavioral parity with the locked SQLite baseline:
- Exactly 8 DynamoDB tables
- All required GSIs with Query execution (no unnecessary Scans)
- Multi-dose intake deduplication & atomic UPSERT
- Deterministic reminder notification creation & idempotency
- Atomic notification claiming with 120-second lease
- Delivery retry lifecycle (max 3 attempts -> FAILED)
- Prescriptions atomic creation & lifecycle synchronization with linked medicines
- Medical reports metadata authorization and ownership
- Doctor-patient relationship authorization
- Autonomous reminder scheduler execution in pure DynamoDB mode (no SQLite)
- Strictly isolated in-memory DynamoDB mock; canonical medtrack_local.db is NEVER touched.
"""

import copy
import datetime
import json
import re
import unittest
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo
from botocore.exceptions import ClientError

from config import Config
from services.database import DatabaseService
from services.dynamodb_service import DynamoDBService
from workers.medicine_scheduler import run_scheduler_once


class MockTable:
    """In-memory DynamoDB Table emulator supporting PK, GSI Query, Scans, Atomic Updates, and Conditional Checks."""

    def __init__(self, name: str, primary_key: str, gsi_specs: dict = None):
        self.name = name
        self.primary_key = primary_key
        self.gsi_specs = gsi_specs or {}
        self.items = {}  # {pk_value: item_dict}
        self.query_log = []
        self.scan_log = []
        self.put_log = []
        self.update_log = []
        self.delete_log = []

    def _eval_condition_obj(self, condition, item):
        if condition is None:
            return True
        expr = condition.get_expression()
        op = expr.get("operator")
        if op == "AND":
            return all(self._eval_condition_obj(v, item) for v in expr["values"])
        elif op == "OR":
            return any(self._eval_condition_obj(v, item) for v in expr["values"])
        elif op == "NOT":
            return not self._eval_condition_obj(expr["values"][0], item)
        elif op == "=":
            attr_name = expr["values"][0].name
            val = expr["values"][1]
            return item.get(attr_name) == val
        elif op == "<":
            attr_name = expr["values"][0].name
            val = expr["values"][1]
            return str(item.get(attr_name, "")) < str(val)
        elif op == "<=":
            attr_name = expr["values"][0].name
            val = expr["values"][1]
            return str(item.get(attr_name, "")) <= str(val)
        elif op == ">":
            attr_name = expr["values"][0].name
            val = expr["values"][1]
            return str(item.get(attr_name, "")) > str(val)
        elif op == ">=":
            attr_name = expr["values"][0].name
            val = expr["values"][1]
            return str(item.get(attr_name, "")) >= str(val)
        return True

    def put_item(self, Item: dict, ConditionExpression: str = None, **kwargs):
        self.put_log.append({"item": Item, "condition": ConditionExpression})
        pk_val = Item.get(self.primary_key)

        if ConditionExpression:
            # Check attribute_not_exists
            m_not_exists = re.match(r"attribute_not_exists\((\w+)\)", ConditionExpression)
            if m_not_exists:
                attr = m_not_exists.group(1)
                if attr == self.primary_key and pk_val in self.items:
                    raise ClientError(
                        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "Item already exists"}},
                        "PutItem"
                    )

        self.items[pk_val] = copy.deepcopy(Item)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_item(self, Key: dict, **kwargs):
        pk_val = Key.get(self.primary_key)
        item = self.items.get(pk_val)
        if item:
            return {"Item": copy.deepcopy(item)}
        return {}

    def delete_item(self, Key: dict, ConditionExpression: str = None,
                    ExpressionAttributeNames: dict = None, ExpressionAttributeValues: dict = None, **kwargs):
        self.delete_log.append({"key": Key, "condition": ConditionExpression})
        pk_val = Key.get(self.primary_key)
        item = self.items.get(pk_val)

        if ConditionExpression and item is not None:
            # Handle patient_id = :pid
            if "patient_id = :pid" in ConditionExpression:
                expected_pid = ExpressionAttributeValues.get(":pid")
                if item.get("patient_id") != expected_pid:
                    raise ClientError(
                        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "Ownership mismatch"}},
                        "DeleteItem"
                    )

        if pk_val in self.items:
            del self.items[pk_val]
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def update_item(self, Key: dict, UpdateExpression: str, ConditionExpression: str = None,
                    ExpressionAttributeNames: dict = None, ExpressionAttributeValues: dict = None,
                    ReturnValues: str = None, **kwargs):
        names = ExpressionAttributeNames or {}
        values = ExpressionAttributeValues or {}
        pk_val = Key.get(self.primary_key)
        existing = self.items.get(pk_val)

        self.update_log.append({
            "key": Key,
            "update_expr": UpdateExpression,
            "condition": ConditionExpression
        })

        # Evaluate ConditionExpression if item exists or doesn't exist
        if ConditionExpression:
            if ConditionExpression == "attribute_exists(appointment_id)":
                if existing is None:
                    raise ClientError(
                        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "Does not exist"}},
                        "UpdateItem"
                    )
            elif "delivery_claim_id = :claim_id" in ConditionExpression:
                if not existing or existing.get("delivery_claim_id") != values.get(":claim_id"):
                    raise ClientError(
                        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "Claim mismatch"}},
                        "UpdateItem"
                    )
            elif "delivery_status = :claimed AND delivery_claim_id = :claim_id" in ConditionExpression:
                if not existing:
                    raise ClientError(
                        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "Not found"}},
                        "UpdateItem"
                    )
                claimed_st = values.get(":claimed", "CLAIMED")
                claim_id_val = values.get(":claim_id")
                if existing.get("delivery_status") != claimed_st or existing.get("delivery_claim_id") != claim_id_val:
                    raise ClientError(
                        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "Claim mismatch"}},
                        "UpdateItem"
                    )
            elif "delivery_lease_until" in ConditionExpression or "delivery_status" in ConditionExpression:
                # Concurrency check for notification claim
                if not existing:
                    raise ClientError(
                        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "Not found"}},
                        "UpdateItem"
                    )
                st = existing.get("delivery_status")
                lease_until = existing.get("delivery_lease_until", "")
                now_str = values.get(":now_iso") or values.get(":now", "")
                attempts = existing.get("delivery_attempts", 0)
                max_att = values.get(":max_attempts", values.get(":max_att", 3))
                ntype = existing.get("type")
                exp_type = values.get(":ntype", values.get(":type"))

                is_pending = (st == values.get(":pending", "PENDING"))
                is_expired_claim = (st == values.get(":claimed", "CLAIMED") and lease_until < now_str)
                type_ok = (exp_type is None or ntype == exp_type)
                attempts_ok = (attempts < max_att)

                if not ((is_pending or is_expired_claim) and attempts_ok and type_ok):
                    raise ClientError(
                        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "Claim rejected"}},
                        "UpdateItem"
                    )

        # Initialize item if missing (UPSERT)
        if existing is None:
            existing = {self.primary_key: pk_val}
            self.items[pk_val] = existing

        # Parse and apply UpdateExpression (SET and/or REMOVE)
        set_part = None
        remove_part = None
        if "SET " in UpdateExpression:
            after_set = UpdateExpression.split("SET ", 1)[1]
            if " REMOVE " in after_set:
                set_part, remove_part = after_set.split(" REMOVE ", 1)
            else:
                set_part = after_set
        elif "REMOVE " in UpdateExpression:
            remove_part = UpdateExpression.split("REMOVE ", 1)[1]

        if set_part:
            clauses = re.split(r",\s*(?![^()]*\))", set_part.strip())
            for clause in clauses:
                clause = clause.strip()
                if "=" not in clause:
                    continue
                lhs, rhs = [x.strip() for x in clause.split("=", 1)]
                field = names.get(lhs, lhs)

                if "if_not_exists" in rhs:
                    m = re.match(r"if_not_exists\((\w+),\s*([:\w]+)\)", rhs)
                    if m:
                        attr_n, val_placeholder = m.group(1), m.group(2)
                        if field not in existing or existing[field] is None:
                            existing[field] = values.get(val_placeholder)
                elif rhs == "NULL":
                    existing[field] = None
                elif "+" in rhs:
                    left_op, addend = [x.strip() for x in rhs.split("+", 1)]
                    curr_num = int(existing.get(left_op, 0))
                    if addend.startswith(":"):
                        addend_val = int(values.get(addend, 1))
                    elif addend.isdigit():
                        addend_val = int(addend)
                    else:
                        addend_val = 1
                    existing[field] = curr_num + addend_val
                else:
                    if rhs.startswith(":"):
                        existing[field] = values.get(rhs)
                    else:
                        existing[field] = rhs

        if remove_part:
            for rem_field in remove_part.split(","):
                rem_field = rem_field.strip()
                real_f = names.get(rem_field, rem_field)
                if real_f in existing:
                    del existing[real_f]

        if ReturnValues == "ALL_NEW":
            return {"Attributes": copy.deepcopy(existing)}
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def query(self, IndexName: str = None, KeyConditionExpression=None, ScanIndexForward: bool = True,
              FilterExpression=None, **kwargs):
        self.query_log.append({
            "index": IndexName,
            "condition": KeyConditionExpression,
            "forward": ScanIndexForward,
            "kwargs": kwargs
        })
        results = []
        for item in self.items.values():
            if self._eval_condition_obj(KeyConditionExpression, item):
                if FilterExpression is None or self._eval_condition_obj(FilterExpression, item):
                    results.append(copy.deepcopy(item))

        # Sort if GSI has sort key
        if IndexName and IndexName in self.gsi_specs:
            sk = self.gsi_specs[IndexName].get("sk")
            if sk:
                results.sort(key=lambda x: str(x.get(sk, "")), reverse=not ScanIndexForward)

        return {"Items": results}

    def scan(self, FilterExpression=None, ExpressionAttributeNames=None, ExpressionAttributeValues=None, **kwargs):
        self.scan_log.append({
            "filter": FilterExpression,
            "names": ExpressionAttributeNames,
            "values": ExpressionAttributeValues
        })
        results = []
        names = ExpressionAttributeNames or {}
        values = ExpressionAttributeValues or {}

        for item in self.items.values():
            match = True
            if FilterExpression is not None:
                if hasattr(FilterExpression, "get_expression"):
                    match = self._eval_condition_obj(FilterExpression, item)
                elif isinstance(FilterExpression, str):
                    if "#r = :role" in FilterExpression or "role = :role" in FilterExpression:
                        match = (item.get("role") == values.get(":role"))
                    elif "#act = :one" in FilterExpression:
                        match = (item.get("is_active") == values.get(":one"))
                    elif "delivery_status" in FilterExpression:
                        st = item.get("delivery_status")
                        lease = item.get("delivery_lease_until", "")
                        now_val = values.get(":now_iso", "")
                        attempts = int(item.get("delivery_attempts", 0))
                        max_att = int(values.get(":max_attempts", 3))
                        ntype = item.get("type")
                        req_type = values.get(":ntype")

                        is_pending = (st == values.get(":pending", "PENDING"))
                        is_expired = (st == values.get(":claimed", "CLAIMED") and lease < now_val)
                        att_ok = (attempts < max_att)
                        type_ok = (req_type is None or ntype == req_type)
                        match = (is_pending or is_expired) and att_ok and type_ok
            if match:
                results.append(copy.deepcopy(item))
        return {"Items": results}


class MockClient:
    """Mock for boto3.meta.client supporting transact_write_items."""

    def __init__(self, tables_map):
        self.tables_map = tables_map

    def transact_write_items(self, TransactItems: list):
        for tx in TransactItems:
            if "Put" in tx:
                put_info = tx["Put"]
                tbl_name = put_info["TableName"]
                item = put_info["Item"]
                table = self.tables_map.get(tbl_name)
                if table:
                    table.put_item(item)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


class MockDynamoResource:
    """Mock boto3 resource returned by boto3.resource('dynamodb')."""

    def __init__(self, tables_dict):
        self.tables = tables_dict
        self.meta = MagicMock()
        self.meta.client = MockClient(self.tables)

    def Table(self, name: str):
        return self.tables[name]


class TestPhase9DynamoDBParity(unittest.TestCase):
    """30-Point Comprehensive DynamoDB Parity Test Suite for MedTrack Phase 9."""

    def setUp(self):
        # Build exact 8 tables with exact GSI configurations
        self.mock_tables = {
            Config.DYNAMODB_USERS_TABLE: MockTable(
                Config.DYNAMODB_USERS_TABLE, "user_id",
                {"EmailIndex": {"pk": "email"}, "RoleIndex": {"pk": "role", "sk": "name"}}
            ),
            Config.DYNAMODB_MEDICINES_TABLE: MockTable(
                Config.DYNAMODB_MEDICINES_TABLE, "medicine_id",
                {"PatientIndex": {"pk": "patient_id", "sk": "created_at"}}
            ),
            Config.DYNAMODB_INTAKE_LOGS_TABLE: MockTable(
                Config.DYNAMODB_INTAKE_LOGS_TABLE, "log_id",
                {"PatientDateIndex": {"pk": "patient_id", "sk": "log_date"}}
            ),
            Config.DYNAMODB_APPOINTMENTS_TABLE: MockTable(
                Config.DYNAMODB_APPOINTMENTS_TABLE, "appointment_id",
                {"PatientIndex": {"pk": "patient_id", "sk": "appointment_date"},
                 "DoctorIndex": {"pk": "doctor_id", "sk": "appointment_date"}}
            ),
            Config.DYNAMODB_PRESCRIPTIONS_TABLE: MockTable(
                Config.DYNAMODB_PRESCRIPTIONS_TABLE, "prescription_id",
                {"PatientIndex": {"pk": "patient_id", "sk": "issued_date"},
                 "DoctorIndex": {"pk": "doctor_id", "sk": "issued_date"}}
            ),
            Config.DYNAMODB_DIAGNOSES_TABLE: MockTable(
                Config.DYNAMODB_DIAGNOSES_TABLE, "diagnosis_id",
                {"PatientIndex": {"pk": "patient_id", "sk": "created_at"},
                 "DoctorIndex": {"pk": "doctor_id", "sk": "created_at"}}
            ),
            Config.DYNAMODB_REPORTS_TABLE: MockTable(
                Config.DYNAMODB_REPORTS_TABLE, "report_id",
                {"PatientIndex": {"pk": "patient_id", "sk": "uploaded_at"}}
            ),
            Config.DYNAMODB_NOTIFICATIONS_TABLE: MockTable(
                Config.DYNAMODB_NOTIFICATIONS_TABLE, "notification_id",
                {"PatientIndex": {"pk": "patient_id", "sk": "created_at"}}
            ),
        }

        self.mock_resource = MockDynamoResource(self.mock_tables)

        # Patch boto3.resource so DynamoDBService uses our mock
        self.boto_patcher = patch("boto3.resource", return_value=self.mock_resource)
        self.boto_patcher.start()

        # Initialize DynamoDBService directly
        self.dynamo_svc = DynamoDBService()

        # Initialize DatabaseService with MOCK_AWS=False (DynamoDB routing mode)
        with patch.object(Config, "MOCK_AWS", False):
            self.db = DatabaseService()
            # Link mocked dynamo service to database service
            self.db.dynamo = self.dynamo_svc

    def tearDown(self):
        self.boto_patcher.stop()

    # -------------------------------------------------------------
    # 1. User creation and retrieval
    # -------------------------------------------------------------
    def test_01_user_creation_and_retrieval(self):
        user_data = {
            "user_id": "usr-test-01",
            "name": "Jane Patient",
            "email": "jane@example.com",
            "password_hash": "scrypt:mockhash",
            "role": "patient",
            "created_at": "2026-09-20T10:00:00Z"
        }
        res = self.db.create_user(user_data)
        self.assertEqual(res["user_id"], "usr-test-01")

        retrieved = self.db.get_user_by_id("usr-test-01")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["name"], "Jane Patient")
        self.assertEqual(retrieved["email"], "jane@example.com")

    # -------------------------------------------------------------
    # 2. Email lookup via GSI (EmailIndex Query, NO Scan)
    # -------------------------------------------------------------
    def test_02_email_lookup_via_gsi(self):
        user_data = {
            "user_id": "usr-test-02",
            "name": "John Doe",
            "email": "john.gsi@example.com",
            "password_hash": "scrypt:mockhash",
            "role": "patient",
            "created_at": "2026-09-20T10:00:00Z"
        }
        self.db.create_user(user_data)

        user_tbl = self.mock_tables[Config.DYNAMODB_USERS_TABLE]
        user_tbl.query_log.clear()
        user_tbl.scan_log.clear()

        retrieved = self.db.get_user_by_email("john.gsi@example.com")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["user_id"], "usr-test-02")

        # Verify Query executed on EmailIndex, NOT Scan
        self.assertEqual(len(user_tbl.query_log), 1)
        self.assertEqual(user_tbl.query_log[0]["index"], "EmailIndex")
        self.assertEqual(len(user_tbl.scan_log), 0)

    # -------------------------------------------------------------
    # 3. Doctor listing
    # -------------------------------------------------------------
    def test_03_doctor_listing(self):
        doc1 = {"user_id": "doc-01", "name": "Dr. Sarah Adams", "email": "sarah@med.com", "role": "doctor"}
        doc2 = {"user_id": "doc-02", "name": "Dr. Alan Turing", "email": "alan@med.com", "role": "doctor"}
        pat = {"user_id": "pat-01", "name": "Patient Bob", "email": "bob@med.com", "role": "patient"}

        self.db.create_user(doc1)
        self.db.create_user(doc2)
        self.db.create_user(pat)

        doctors = self.db.get_doctors()
        doc_ids = [d["user_id"] for d in doctors]
        self.assertIn("doc-01", doc_ids)
        self.assertIn("doc-02", doc_ids)
        self.assertNotIn("pat-01", doc_ids)

    # -------------------------------------------------------------
    # 4. Medicine creation
    # -------------------------------------------------------------
    def test_04_medicine_creation(self):
        med = self.db.create_medicine(
            patient_id="usr-pat-04",
            name="Atorvastatin",
            dosage="20mg",
            schedule_times=["08:00 AM", "08:00 PM"],
            frequency="Twice Daily",
            meal_timing="After Food",
            notes="Take with water"
        )
        self.assertTrue(med["medicine_id"].startswith("med-"))
        self.assertEqual(med["name"], "Atorvastatin")
        self.assertEqual(med["dosage"], "20mg")
        self.assertEqual(med["is_active"], 1)

    # -------------------------------------------------------------
    # 5. Medicine patient query (PatientIndex GSI)
    # -------------------------------------------------------------
    def test_05_medicine_patient_query(self):
        self.db.create_medicine(patient_id="usr-pat-05", name="Aspirin", dosage="100mg", schedule_time="09:00 AM")
        self.db.create_medicine(patient_id="usr-pat-05", name="Metformin", dosage="500mg", schedule_time="07:00 PM")
        self.db.create_medicine(patient_id="other-pat", name="Lisinopril", dosage="10mg", schedule_time="08:00 AM")

        med_tbl = self.mock_tables[Config.DYNAMODB_MEDICINES_TABLE]
        med_tbl.query_log.clear()

        patient_meds = self.db.get_medicines_by_patient("usr-pat-05")
        self.assertEqual(len(patient_meds), 2)
        med_names = [m["name"] for m in patient_meds]
        self.assertIn("Aspirin", med_names)
        self.assertIn("Metformin", med_names)
        self.assertNotIn("Lisinopril", med_names)

        # Check PatientIndex query
        self.assertEqual(len(med_tbl.query_log), 1)
        self.assertEqual(med_tbl.query_log[0]["index"], "PatientIndex")

    # -------------------------------------------------------------
    # 6. Medicine ownership on deletion & clinical protection
    # -------------------------------------------------------------
    def test_06_medicine_ownership_deletion(self):
        med = self.db.create_medicine(patient_id="patient-A", name="Vitamin C", dosage="500mg")
        med_id = med["medicine_id"]

        # Attempt deletion by wrong patient must fail
        deleted_by_wrong = self.db.delete_medicine(med_id, patient_id="patient-B")
        self.assertFalse(deleted_by_wrong)
        self.assertIsNotNone(self.db.get_medicine_by_id(med_id))

        # Successful deletion by legitimate owner
        deleted = self.db.delete_medicine(med_id, patient_id="patient-A")
        self.assertTrue(deleted)
        self.assertIsNone(self.db.get_medicine_by_id(med_id))

    # -------------------------------------------------------------
    # 7. Multi-dose intake identity
    # -------------------------------------------------------------
    def test_07_multidose_intake_identity(self):
        med = self.db.create_medicine(
            patient_id="usr-dose-07",
            name="Amoxicillin",
            dosage="250mg",
            schedule_times=["08:00 AM", "02:00 PM", "08:00 PM"]
        )
        med_id = med["medicine_id"]

        log1 = self.db.record_intake("usr-dose-07", med_id, "TAKEN", scheduled_date="2026-09-20", scheduled_time="08:00 AM")
        log2 = self.db.record_intake("usr-dose-07", med_id, "TAKEN", scheduled_date="2026-09-20", scheduled_time="02:00 PM")

        # Must generate distinct deterministic dose log IDs
        self.assertEqual(log1["log_id"], f"log#usr-dose-07#{med_id}#2026-09-20#08:00 AM")
        self.assertEqual(log2["log_id"], f"log#usr-dose-07#{med_id}#2026-09-20#02:00 PM")
        self.assertNotEqual(log1["log_id"], log2["log_id"])

    # -------------------------------------------------------------
    # 8. Repeated intake update preserves created_at
    # -------------------------------------------------------------
    def test_08_repeated_intake_update(self):
        med = self.db.create_medicine(patient_id="usr-rep-08", name="Thyroxine", dosage="50mcg", schedule_time="06:00 AM")
        med_id = med["medicine_id"]

        # First submission
        t1 = "2026-09-20T06:05:00Z"
        log1 = self.dynamo_svc.record_intake(
            patient_id="usr-rep-08", medicine_id=med_id, medicine_name="Thyroxine", dosage="50mcg",
            status="SKIPPED", scheduled_date="2026-09-20", scheduled_time="06:00 AM", created_at=t1
        )
        self.assertEqual(log1["status"], "SKIPPED")
        first_created_at = log1["created_at"]

        # Second submission: patient changes status to TAKEN
        t2 = "2026-09-20T06:20:00Z"
        log2 = self.dynamo_svc.record_intake(
            patient_id="usr-rep-08", medicine_id=med_id, medicine_name="Thyroxine", dosage="50mcg",
            status="TAKEN", scheduled_date="2026-09-20", scheduled_time="06:00 AM",
            taken_time="06:20 AM", created_at=t2
        )
        self.assertEqual(log2["status"], "TAKEN")
        self.assertEqual(log2["taken_time"], "06:20 AM")
        # Canonical created_at must remain stable!
        self.assertEqual(log2["created_at"], first_created_at)
        self.assertEqual(log2["log_id"], log1["log_id"])

    # -------------------------------------------------------------
    # 9. Zero duplicate dose records
    # -------------------------------------------------------------
    def test_09_no_duplicate_dose_records(self):
        med = self.db.create_medicine(patient_id="usr-nodup-09", name="Paracetamol", dosage="650mg", schedule_time="10:00 AM")
        med_id = med["medicine_id"]

        for _ in range(5):
            self.db.record_intake("usr-nodup-09", med_id, "TAKEN", scheduled_date="2026-09-20", scheduled_time="10:00 AM")

        logs_table = self.mock_tables[Config.DYNAMODB_INTAKE_LOGS_TABLE]
        # Exact matching keys in table
        matching = [k for k in logs_table.items if f"usr-nodup-09#{med_id}#2026-09-20#10:00 AM" in k]
        self.assertEqual(len(matching), 1)

    # -------------------------------------------------------------
    # 10. Appointment patient query (PatientIndex GSI)
    # -------------------------------------------------------------
    def test_10_appointment_patient_query(self):
        self.db.create_user({"user_id": "doc-apt-10", "name": "Dr. Vance", "role": "doctor"})
        self.db.create_user({"user_id": "pat-apt-10", "name": "Patient Ten", "role": "patient"})

        self.db.create_appointment({
            "patient_id": "pat-apt-10",
            "doctor_id": "doc-apt-10",
            "appointment_date": "2026-09-25",
            "appointment_time": "10:00 AM",
            "reason": "Cardiology Followup"
        })

        apt_tbl = self.mock_tables[Config.DYNAMODB_APPOINTMENTS_TABLE]
        apt_tbl.query_log.clear()

        appts = self.db.get_appointments_by_patient("pat-apt-10")
        self.assertEqual(len(appts), 1)
        self.assertEqual(appts[0]["reason"], "Cardiology Followup")
        self.assertEqual(len(apt_tbl.query_log), 1)
        self.assertEqual(apt_tbl.query_log[0]["index"], "PatientIndex")

    # -------------------------------------------------------------
    # 11. Appointment doctor query (DoctorIndex GSI)
    # -------------------------------------------------------------
    def test_11_appointment_doctor_query(self):
        self.db.create_user({"user_id": "doc-apt-11", "name": "Dr. Chen", "role": "doctor"})
        self.db.create_user({"user_id": "pat-apt-11", "name": "Patient Eleven", "role": "patient"})

        self.db.create_appointment({
            "patient_id": "pat-apt-11",
            "doctor_id": "doc-apt-11",
            "appointment_date": "2026-09-26",
            "appointment_time": "11:00 AM",
            "reason": "Routine Checkup"
        })

        apt_tbl = self.mock_tables[Config.DYNAMODB_APPOINTMENTS_TABLE]
        apt_tbl.query_log.clear()

        appts = self.db.get_appointments_by_doctor("doc-apt-11")
        self.assertEqual(len(appts), 1)
        self.assertEqual(appts[0]["doctor_id"], "doc-apt-11")
        self.assertEqual(len(apt_tbl.query_log), 1)
        self.assertEqual(apt_tbl.query_log[0]["index"], "DoctorIndex")

    # -------------------------------------------------------------
    # 12. Diagnosis patient query (PatientIndex GSI)
    # -------------------------------------------------------------
    def test_12_diagnosis_patient_query(self):
        self.db.create_user({"user_id": "doc-diag-12", "name": "Dr. Wilson", "role": "doctor"})
        self.db.create_user({"user_id": "pat-diag-12", "name": "Patient Twelve", "role": "patient"})

        self.db.create_diagnosis({
            "patient_id": "pat-diag-12",
            "doctor_id": "doc-diag-12",
            "diagnosis": "Essential Hypertension",
            "date": "2026-09-20"
        })

        diag_tbl = self.mock_tables[Config.DYNAMODB_DIAGNOSES_TABLE]
        diag_tbl.query_log.clear()

        diags = self.db.get_diagnoses_by_patient("pat-diag-12")
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0]["diagnosis"], "Essential Hypertension")
        self.assertEqual(len(diag_tbl.query_log), 1)
        self.assertEqual(diag_tbl.query_log[0]["index"], "PatientIndex")

    # -------------------------------------------------------------
    # 13. Diagnosis doctor query (DoctorIndex GSI)
    # -------------------------------------------------------------
    def test_13_diagnosis_doctor_query(self):
        self.db.create_user({"user_id": "doc-diag-13", "name": "Dr. Gregory", "role": "doctor"})
        self.db.create_user({"user_id": "pat-diag-13", "name": "Patient Thirteen", "role": "patient"})

        self.db.create_diagnosis({
            "patient_id": "pat-diag-13",
            "doctor_id": "doc-diag-13",
            "diagnosis": "Type 2 Diabetes Mellitus",
            "date": "2026-09-20"
        })

        diag_tbl = self.mock_tables[Config.DYNAMODB_DIAGNOSES_TABLE]
        diag_tbl.query_log.clear()

        diags = self.db.get_diagnoses_by_doctor("doc-diag-13")
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0]["diagnosis"], "Type 2 Diabetes Mellitus")
        self.assertEqual(len(diag_tbl.query_log), 1)
        self.assertEqual(diag_tbl.query_log[0]["index"], "DoctorIndex")

    # -------------------------------------------------------------
    # 14. Prescription creation
    # -------------------------------------------------------------
    def test_14_prescription_creation(self):
        self.db.create_user({"user_id": "doc-rx-14", "name": "Dr. House", "role": "doctor"})
        self.db.create_user({"user_id": "pat-rx-14", "name": "Patient Fourteen", "role": "patient"})

        appt = self.db.create_appointment({
            "patient_id": "pat-rx-14",
            "doctor_id": "doc-rx-14",
            "appointment_date": "2026-09-20",
            "appointment_time": "09:00 AM",
            "status": "CONFIRMED"
        })

        rx = self.db.create_prescription(
            doctor_id="doc-rx-14",
            patient_id="pat-rx-14",
            appointment_id=appt["appointment_id"],
            medicine_name="Metoprolol",
            dosage="50mg",
            schedule_times=["08:00 AM", "08:00 PM"],
            instructions="Take twice daily with meals"
        )
        rx_id = rx["prescription_id"]
        self.assertTrue(rx_id.startswith("rx-"))
        self.assertEqual(rx["status"], "ACTIVE")
        self.assertEqual(rx["medicine_name"], "Metoprolol")
        self.assertEqual(rx["dosage"], "50mg")

    # -------------------------------------------------------------
    # 15. Prescription-linked medicine creation
    # -------------------------------------------------------------
    def test_15_prescription_linked_medicine_creation(self):
        self.db.create_user({"user_id": "doc-rx-15", "name": "Dr. Wilson", "role": "doctor"})
        self.db.create_user({"user_id": "pat-rx-15", "name": "Patient Fifteen", "role": "patient"})

        appt = self.db.create_appointment({
            "patient_id": "pat-rx-15",
            "doctor_id": "doc-rx-15",
            "appointment_date": "2026-09-20",
            "appointment_time": "10:00 AM",
            "status": "CONFIRMED"
        })

        rx = self.db.create_prescription(
            doctor_id="doc-rx-15",
            patient_id="pat-rx-15",
            appointment_id=appt["appointment_id"],
            medicine_name="Carvedilol",
            dosage="25mg",
            schedule_times=["09:00 AM"]
        )
        rx_id = rx["prescription_id"]
        expected_med_id = f"med_rx_{rx_id}"
        self.assertEqual(rx["linked_medicine_id"], expected_med_id)

        linked_med = self.db.get_medicine_by_id(expected_med_id)
        self.assertIsNotNone(linked_med)
        self.assertEqual(linked_med["name"], "Carvedilol")
        self.assertEqual(linked_med["is_active"], 1)
        self.assertEqual(linked_med["prescription_id"], rx_id)

    # -------------------------------------------------------------
    # 16. Prescription lifecycle and medicine deactivation
    # -------------------------------------------------------------
    def test_16_prescription_lifecycle_synchronization(self):
        self.db.create_user({"user_id": "doc-rx-16", "name": "Dr. Strange", "role": "doctor"})
        self.db.create_user({"user_id": "pat-rx-16", "name": "Patient Sixteen", "role": "patient"})
        appt = self.db.create_appointment({
            "patient_id": "pat-rx-16",
            "doctor_id": "doc-rx-16",
            "appointment_date": "2026-09-20",
            "appointment_time": "10:00 AM",
            "status": "COMPLETED"
        })
        rx = self.db.create_prescription(
            doctor_id="doc-rx-16",
            patient_id="pat-rx-16",
            appointment_id=appt["appointment_id"],
            medicine_name="Clopidogrel",
            dosage="75mg"
        )
        rx_id = rx["prescription_id"]
        med_id = f"med_rx_{rx_id}"

        # Discontinue prescription
        success = self.db.discontinue_prescription(rx_id, doctor_id="doc-rx-16")
        self.assertTrue(success)

        # Prescription status updated to DISCONTINUED
        updated_rx = self.db.get_prescription_by_id(rx_id)
        self.assertEqual(updated_rx["status"], "DISCONTINUED")

        # Linked medicine deactivated (is_active = 0)
        updated_med = self.db.get_medicine_by_id(med_id)
        self.assertEqual(updated_med["is_active"], 0)

        # Terminal state protection: cannot reactivate DISCONTINUED prescription
        with self.assertRaises(ValueError):
            self.db.update_prescription_status(rx_id, "ACTIVE", doctor_id="doc-rx-16")

    # -------------------------------------------------------------
    # 17. Doctor prescription isolation
    # -------------------------------------------------------------
    def test_17_doctor_prescription_isolation(self):
        self.db.create_user({"user_id": "doc-A-17", "name": "Dr. Alpha", "role": "doctor"})
        self.db.create_user({"user_id": "doc-B-17", "name": "Dr. Beta", "role": "doctor"})
        self.db.create_user({"user_id": "pat-17", "name": "Patient Seventeen", "role": "patient"})

        apptA = self.db.create_appointment({"patient_id": "pat-17", "doctor_id": "doc-A-17", "appointment_date": "2026-09-20", "appointment_time": "09:00 AM", "status": "CONFIRMED"})
        apptB = self.db.create_appointment({"patient_id": "pat-17", "doctor_id": "doc-B-17", "appointment_date": "2026-09-20", "appointment_time": "10:00 AM", "status": "CONFIRMED"})

        rxA = self.db.create_prescription(doctor_id="doc-A-17", patient_id="pat-17", appointment_id=apptA["appointment_id"], medicine_name="Med-A", dosage="10mg")
        rxB = self.db.create_prescription(doctor_id="doc-B-17", patient_id="pat-17", appointment_id=apptB["appointment_id"], medicine_name="Med-B", dosage="20mg")

        # Doctor A must only see rxA
        docA_rxs = self.db.get_prescriptions_by_doctor("doc-A-17")
        docA_rx_ids = [r["prescription_id"] for r in docA_rxs]
        self.assertIn(rxA["prescription_id"], docA_rx_ids)
        self.assertNotIn(rxB["prescription_id"], docA_rx_ids)

        # Doctor B cannot discontinue Doctor A's prescription
        res = self.db.discontinue_prescription(rxA["prescription_id"], doctor_id="doc-B-17")
        self.assertFalse(res)

    # -------------------------------------------------------------
    # 18. Report metadata creation
    # -------------------------------------------------------------
    def test_18_report_metadata_creation(self):
        self.db.create_user({"user_id": "pat-rep-18", "name": "Patient Eighteen", "role": "patient"})
        rep = self.db.create_report({
            "patient_id": "pat-rep-18",
            "title": "Complete Blood Count",
            "file_name": "cbc_report.pdf",
            "file_type": "application/pdf",
            "file_size": 204800,
            "storage_path": "reports/usr-pat-18/cbc_report.pdf",
            "notes": "Normal ranges across all parameters"
        })
        self.assertTrue(rep["report_id"].startswith("rep-"))
        self.assertEqual(rep["title"], "Complete Blood Count")

        retrieved = self.db.get_report_by_id(rep["report_id"])
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["file_name"], "cbc_report.pdf")

    # -------------------------------------------------------------
    # 19. Report ownership and authorization
    # -------------------------------------------------------------
    def test_19_report_ownership_and_authorization(self):
        self.db.create_user({"user_id": "pat-rep-19", "name": "Patient Nineteen", "role": "patient"})
        self.db.create_user({"user_id": "doc-auth-19", "name": "Dr. Attending", "role": "doctor"})
        self.db.create_user({"user_id": "doc-unauth-19", "name": "Dr. Stranger", "role": "doctor"})

        # Attending doctor has appointment with patient
        self.db.create_appointment({
            "patient_id": "pat-rep-19",
            "doctor_id": "doc-auth-19",
            "appointment_date": "2026-09-20",
            "appointment_time": "11:00 AM",
            "status": "CONFIRMED"
        })

        rep = self.db.create_report({
            "patient_id": "pat-rep-19",
            "title": "Chest X-Ray",
            "file_name": "xray.pdf",
            "storage_path": "reports/xray.pdf"
        })
        rep_id = rep["report_id"]

        # Authorized doctor can view report
        auth_reports = self.db.get_reports_by_doctor("doc-auth-19", patient_id="pat-rep-19")
        self.assertEqual(len(auth_reports), 1)

        # Unauthorized doctor cannot view report
        unauth_reports = self.db.get_reports_by_doctor("doc-unauth-19", patient_id="pat-rep-19")
        self.assertEqual(len(unauth_reports), 0)

        # Unauthorized doctor cannot delete report
        del_unauth = self.db.delete_report(rep_id, actor_id="doc-unauth-19")
        self.assertFalse(del_unauth)
        self.assertIsNotNone(self.db.get_report_by_id(rep_id))

        # Patient owner CAN delete report
        del_owner = self.db.delete_report(rep_id, actor_id="pat-rep-19")
        self.assertTrue(del_owner)
        self.assertIsNone(self.db.get_report_by_id(rep_id))

    # -------------------------------------------------------------
    # 20. Notification deterministic ID
    # -------------------------------------------------------------
    def test_20_notification_deterministic_id(self):
        notif_id = "remind#pat-20#med-20#2026-09-20#08:00 AM"
        created = self.db.create_reminder_notification(
            notification_id=notif_id,
            patient_id="pat-20",
            title="Medicine Reminder",
            message="Time to take Med-20",
            scheduled_date="2026-09-20",
            scheduled_time="08:00 AM"
        )
        self.assertTrue(created)
        notif_tbl = self.mock_tables[Config.DYNAMODB_NOTIFICATIONS_TABLE]
        item = notif_tbl.get_item({"notification_id": notif_id}).get("Item")
        self.assertIsNotNone(item)
        self.assertEqual(item["notification_id"], notif_id)

    # -------------------------------------------------------------
    # 21. Duplicate notification prevention (idempotency)
    # -------------------------------------------------------------
    def test_21_duplicate_notification_prevention(self):
        notif_id = "remind#pat-21#med-21#2026-09-20#08:00 AM"
        first_created = self.db.create_reminder_notification(
            notification_id=notif_id,
            patient_id="pat-21",
            title="Medicine Reminder",
            message="Time to take Med-21",
            scheduled_date="2026-09-20",
            scheduled_time="08:00 AM"
        )
        self.assertTrue(first_created)

        # Subsequent creation with same notification_id returns False (preventing duplicates)
        second_created = self.db.create_reminder_notification(
            notification_id=notif_id,
            patient_id="pat-21",
            title="Medicine Reminder",
            message="Time to take Med-21",
            scheduled_date="2026-09-20",
            scheduled_time="08:00 AM"
        )
        self.assertFalse(second_created)

    # -------------------------------------------------------------
    # 22. Notification claim lease (120 seconds)
    # -------------------------------------------------------------
    def test_22_notification_claim_lease(self):
        notif_id = "remind#pat-22#med-22#2026-09-20#08:00 AM"
        self.db.create_reminder_notification(
            notification_id=notif_id, patient_id="pat-22", title="Reminder",
            message="Dose reminder", scheduled_date="2026-09-20", scheduled_time="08:00 AM"
        )

        now = datetime.datetime(2026, 9, 20, 8, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        claimed = self.db.claim_pending_notifications(limit=10, lease_seconds=120, now=now)
        self.assertEqual(len(claimed), 1)

        c = claimed[0]
        self.assertEqual(c["delivery_status"], "CLAIMED")
        self.assertTrue(c["delivery_claim_id"].startswith("claim-"))
        self.assertEqual(c["delivery_attempts"], 1)

        # Verify lease duration is exactly 120 seconds ahead
        lease_dt = datetime.datetime.fromisoformat(c["delivery_lease_until"])
        self.assertEqual((lease_dt - now).total_seconds(), 120)

    # -------------------------------------------------------------
    # 23. Concurrent / competing notification claims
    # -------------------------------------------------------------
    def test_23_concurrent_competing_notification_claims(self):
        notif_id = "remind#pat-23#med-23#2026-09-20#08:00 AM"
        self.db.create_reminder_notification(
            notification_id=notif_id, patient_id="pat-23", title="Reminder",
            message="Dose reminder", scheduled_date="2026-09-20", scheduled_time="08:00 AM"
        )

        now = datetime.datetime(2026, 9, 20, 8, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

        # Worker 1 claims notification
        worker1_claimed = self.db.claim_pending_notifications(limit=10, lease_seconds=120, now=now)
        self.assertEqual(len(worker1_claimed), 1)

        # Worker 2 attempts claim at t+30s (lease still active for 90 more seconds)
        t_plus_30 = now + datetime.timedelta(seconds=30)
        worker2_claimed = self.db.claim_pending_notifications(limit=10, lease_seconds=120, now=t_plus_30)
        self.assertEqual(len(worker2_claimed), 0)

        # Worker 3 attempts claim after lease expires at t+130s
        t_plus_130 = now + datetime.timedelta(seconds=130)
        worker3_claimed = self.db.claim_pending_notifications(limit=10, lease_seconds=120, now=t_plus_130)
        self.assertEqual(len(worker3_claimed), 1)
        self.assertEqual(worker3_claimed[0]["delivery_attempts"], 2)

    # -------------------------------------------------------------
    # 24. Retry handling (reverts to PENDING when attempts < 3)
    # -------------------------------------------------------------
    def test_24_retry_handling(self):
        notif_id = "remind#pat-24#med-24#2026-09-20#08:00 AM"
        self.db.create_reminder_notification(
            notification_id=notif_id, patient_id="pat-24", title="Reminder",
            message="Dose reminder", scheduled_date="2026-09-20", scheduled_time="08:00 AM"
        )
        now = datetime.datetime(2026, 9, 20, 8, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        claim = self.db.claim_pending_notifications(limit=1, lease_seconds=120, now=now)[0]

        # On delivery failure with attempt 1, must revert to PENDING
        retry_status = self.db.mark_notification_failed_or_retry(notif_id, claim["delivery_claim_id"], max_attempts=3)
        self.assertEqual(retry_status, "PENDING")

        item = self.mock_tables[Config.DYNAMODB_NOTIFICATIONS_TABLE].get_item({"notification_id": notif_id})["Item"]
        self.assertEqual(item["delivery_status"], "PENDING")
        self.assertIsNone(item.get("delivery_claim_id"))
        self.assertIsNone(item.get("delivery_lease_until"))

    # -------------------------------------------------------------
    # 25. Maximum 3 attempts transitions to FAILED
    # -------------------------------------------------------------
    def test_25_maximum_3_attempts(self):
        notif_id = "remind#pat-25#med-25#2026-09-20#08:00 AM"
        self.db.create_reminder_notification(
            notification_id=notif_id, patient_id="pat-25", title="Reminder",
            message="Dose reminder", scheduled_date="2026-09-20", scheduled_time="08:00 AM"
        )
        now = datetime.datetime(2026, 9, 20, 8, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

        # Claim 1 & fail
        c1 = self.db.claim_pending_notifications(limit=1, lease_seconds=120, now=now)[0]
        self.db.mark_notification_failed_or_retry(notif_id, c1["delivery_claim_id"], max_attempts=3)

        # Claim 2 & fail
        c2 = self.db.claim_pending_notifications(limit=1, lease_seconds=120, now=now + datetime.timedelta(seconds=1))[0]
        self.db.mark_notification_failed_or_retry(notif_id, c2["delivery_claim_id"], max_attempts=3)

        # Claim 3 & fail (reached max attempts 3 -> FAILED)
        c3 = self.db.claim_pending_notifications(limit=1, lease_seconds=120, now=now + datetime.timedelta(seconds=2))[0]
        final_status = self.db.mark_notification_failed_or_retry(notif_id, c3["delivery_claim_id"], max_attempts=3)
        self.assertEqual(final_status, "FAILED")

        item = self.mock_tables[Config.DYNAMODB_NOTIFICATIONS_TABLE].get_item({"notification_id": notif_id})["Item"]
        self.assertEqual(item["delivery_status"], "FAILED")

    # -------------------------------------------------------------
    # 26. Scheduler DynamoDB path execution (no SQLite touch)
    # -------------------------------------------------------------
    def test_26_scheduler_dynamodb_path(self):
        # Create active medicine due at 08:00 AM
        self.db.create_medicine(
            patient_id="pat-sched-26",
            name="Amlodipine",
            dosage="5mg",
            schedule_time="08:00 AM"
        )

        # Mock SNS service to simulate successful publishing
        mock_sns = MagicMock()
        mock_sns.publish_notification.return_value = True

        eval_now = datetime.datetime(2026, 9, 20, 8, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

        # Run scheduler cycle
        metrics = run_scheduler_once(db=self.db, sns=mock_sns, now=eval_now)

        self.assertEqual(metrics["eligible"], 1)
        self.assertEqual(metrics["created"], 1)
        self.assertEqual(metrics["claimed"], 1)
        self.assertEqual(metrics["sent"], 1)
        self.assertEqual(metrics["failed"], 0)

        # Notification in DynamoDB must be marked SENT
        notif_tbl = self.mock_tables[Config.DYNAMODB_NOTIFICATIONS_TABLE]
        notif_id = "remind#pat-sched-26#" + list(self.mock_tables[Config.DYNAMODB_MEDICINES_TABLE].items.keys())[0] + "#2026-09-20#08:00 AM"
        item = notif_tbl.get_item({"notification_id": notif_id})["Item"]
        self.assertEqual(item["delivery_status"], "SENT")

    # -------------------------------------------------------------
    # 27. Doctor-patient authorization check
    # -------------------------------------------------------------
    def test_27_doctor_patient_authorization(self):
        self.db.create_user({"user_id": "doc-auth-27", "name": "Dr. Watson", "role": "doctor"})
        self.db.create_user({"user_id": "pat-auth-27", "name": "Patient Sherlock", "role": "patient"})
        self.db.create_user({"user_id": "pat-unauth-27", "name": "Patient Moriarty", "role": "patient"})

        # Book appointment between Dr. Watson and Patient Sherlock
        self.db.create_appointment({
            "patient_id": "pat-auth-27",
            "doctor_id": "doc-auth-27",
            "appointment_date": "2026-09-20",
            "appointment_time": "02:00 PM",
            "status": "CONFIRMED"
        })

        self.assertTrue(self.db.is_doctor_authorized_for_patient("doc-auth-27", "pat-auth-27"))
        self.assertFalse(self.db.is_doctor_authorized_for_patient("doc-auth-27", "pat-unauth-27"))

    # -------------------------------------------------------------
    # 28. No accidental SQLite fallback when MOCK_AWS=false
    # -------------------------------------------------------------
    def test_28_no_accidental_sqlite_fallback(self):
        self.assertFalse(self.db.mock_aws)
        self.assertFalse(hasattr(self.db, "db_path"))

        # Verify that calling _get_sqlite_conn raises AttributeError because db_path was never initialized
        with self.assertRaises(AttributeError):
            with self.db._get_sqlite_conn() as conn:
                pass

    # -------------------------------------------------------------
    # 29. All intended GSI Query paths verification
    # -------------------------------------------------------------
    def test_29_all_intended_gsi_query_paths(self):
        # 1. Users EmailIndex
        user_tbl = self.mock_tables[Config.DYNAMODB_USERS_TABLE]
        user_tbl.query_log.clear()
        self.dynamo_svc.get_user_by_email("test@example.com")
        self.assertTrue(any(q["index"] == "EmailIndex" for q in user_tbl.query_log))

        # 2. Medicines PatientIndex
        med_tbl = self.mock_tables[Config.DYNAMODB_MEDICINES_TABLE]
        med_tbl.query_log.clear()
        self.dynamo_svc.get_medicines_by_patient("pat-gsi-29")
        self.assertTrue(any(q["index"] == "PatientIndex" for q in med_tbl.query_log))

        # 3. IntakeLogs PatientDateIndex
        log_tbl = self.mock_tables[Config.DYNAMODB_INTAKE_LOGS_TABLE]
        log_tbl.query_log.clear()
        self.dynamo_svc.get_intake_logs_by_patient("pat-gsi-29")
        self.assertTrue(any(q["index"] == "PatientDateIndex" for q in log_tbl.query_log))

        # 4. Appointments PatientIndex & DoctorIndex
        apt_tbl = self.mock_tables[Config.DYNAMODB_APPOINTMENTS_TABLE]
        apt_tbl.query_log.clear()
        self.dynamo_svc.get_appointments_by_patient("pat-gsi-29")
        self.assertTrue(any(q["index"] == "PatientIndex" for q in apt_tbl.query_log))
        apt_tbl.query_log.clear()
        self.dynamo_svc.get_appointments_by_doctor("doc-gsi-29")
        self.assertTrue(any(q["index"] == "DoctorIndex" for q in apt_tbl.query_log))

        # 5. Prescriptions PatientIndex & DoctorIndex
        rx_tbl = self.mock_tables[Config.DYNAMODB_PRESCRIPTIONS_TABLE]
        rx_tbl.query_log.clear()
        self.dynamo_svc.get_prescriptions_by_patient("pat-gsi-29")
        self.assertTrue(any(q["index"] == "PatientIndex" for q in rx_tbl.query_log))
        rx_tbl.query_log.clear()
        self.dynamo_svc.get_prescriptions_by_doctor("doc-gsi-29")
        self.assertTrue(any(q["index"] == "DoctorIndex" for q in rx_tbl.query_log))

        # 6. Diagnoses PatientIndex & DoctorIndex
        diag_tbl = self.mock_tables[Config.DYNAMODB_DIAGNOSES_TABLE]
        diag_tbl.query_log.clear()
        self.dynamo_svc.get_diagnoses_by_patient("pat-gsi-29")
        self.assertTrue(any(q["index"] == "PatientIndex" for q in diag_tbl.query_log))
        diag_tbl.query_log.clear()
        self.dynamo_svc.get_diagnoses_by_doctor("doc-gsi-29")
        self.assertTrue(any(q["index"] == "DoctorIndex" for q in diag_tbl.query_log))

        # 7. Reports PatientIndex
        rep_tbl = self.mock_tables[Config.DYNAMODB_REPORTS_TABLE]
        rep_tbl.query_log.clear()
        self.dynamo_svc.get_reports_by_patient("pat-gsi-29")
        self.assertTrue(any(q["index"] == "PatientIndex" for q in rep_tbl.query_log))

        # 8. Notifications PatientIndex
        notif_tbl = self.mock_tables[Config.DYNAMODB_NOTIFICATIONS_TABLE]
        notif_tbl.query_log.clear()
        self.dynamo_svc.get_notifications_by_patient("pat-gsi-29")
        self.assertTrue(any(q["index"] == "PatientIndex" for q in notif_tbl.query_log))

    # -------------------------------------------------------------
    # 30. No unnecessary Scan operations during collection queries
    # -------------------------------------------------------------
    def test_30_no_unnecessary_scan_operations(self):
        # Clear scan logs
        for tbl in self.mock_tables.values():
            tbl.scan_log.clear()

        # Perform targeted collection queries
        self.dynamo_svc.get_user_by_email("test@example.com")
        self.dynamo_svc.get_medicines_by_patient("pat-30")
        self.dynamo_svc.get_intake_logs_by_patient("pat-30")
        self.dynamo_svc.get_appointments_by_patient("pat-30")
        self.dynamo_svc.get_appointments_by_doctor("doc-30")
        self.dynamo_svc.get_prescriptions_by_patient("pat-30")
        self.dynamo_svc.get_prescriptions_by_doctor("doc-30")
        self.dynamo_svc.get_diagnoses_by_patient("pat-30")
        self.dynamo_svc.get_diagnoses_by_doctor("doc-30")
        self.dynamo_svc.get_reports_by_patient("pat-30")
        self.dynamo_svc.get_notifications_by_patient("pat-30")

        # None of these queries should execute a full-table Scan
        self.assertEqual(len(self.mock_tables[Config.DYNAMODB_USERS_TABLE].scan_log), 0)
        self.assertEqual(len(self.mock_tables[Config.DYNAMODB_MEDICINES_TABLE].scan_log), 0)
        self.assertEqual(len(self.mock_tables[Config.DYNAMODB_INTAKE_LOGS_TABLE].scan_log), 0)
        self.assertEqual(len(self.mock_tables[Config.DYNAMODB_APPOINTMENTS_TABLE].scan_log), 0)
        self.assertEqual(len(self.mock_tables[Config.DYNAMODB_PRESCRIPTIONS_TABLE].scan_log), 0)
        self.assertEqual(len(self.mock_tables[Config.DYNAMODB_DIAGNOSES_TABLE].scan_log), 0)
        self.assertEqual(len(self.mock_tables[Config.DYNAMODB_REPORTS_TABLE].scan_log), 0)
        self.assertEqual(len(self.mock_tables[Config.DYNAMODB_NOTIFICATIONS_TABLE].scan_log), 0)


if __name__ == "__main__":
    unittest.main()
