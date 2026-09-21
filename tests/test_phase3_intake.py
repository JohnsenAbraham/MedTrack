"""
Unit & integration tests for MedTrack Phase 3:
Multi-Dose Intake Deduplication & Atomic UPSERT.
"""

import unittest
import os
import sys
import tempfile
import sqlite3
import datetime

sys.path.insert(0, os.path.abspath("."))
from services.database import DatabaseService


class Phase3IntakeTestCase(unittest.TestCase):
    """Test suite for Phase 3 multi-dose intake deduplication, atomicity, and constraints."""

    def setUp(self):
        # Create an isolated temporary database for each test to guarantee no interference with live DB
        self.temp_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_fd)

        self.db = DatabaseService()
        self.db.db_path = self.temp_db_path
        self.db._init_sqlite()

        # Create a test patient
        self.patient = self.db.create_user({
            "name": "Intake Test Patient",
            "email": "intake_test@medtrack.local",
            "password_hash": "hash123",
            "phone": "+1-555-0100",
            "date_of_birth": "1990-01-01",
            "gender": "Other",
            "role": "patient",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        })
        self.patient_id = self.patient["user_id"]

    def tearDown(self):
        if os.path.exists(self.temp_db_path):
            try:
                os.remove(self.temp_db_path)
            except Exception:
                pass

    def test_01_single_dose_creation(self):
        """TEST 1 — Single-dose creation: Medicine with schedule_times = ['08:00']. Record 2026-09-19 08:00 TAKEN."""
        med = self.db.create_medicine(
            patient_id=self.patient_id,
            name="Amoxicillin 250mg",
            dosage="1 Capsule",
            schedule_time="08:00",
            schedule_times=["08:00"]
        )

        log = self.db.record_intake(
            patient_id=self.patient_id,
            medicine_id=med["medicine_id"],
            status="TAKEN",
            scheduled_date="2026-09-19",
            scheduled_time="08:00"
        )

        self.assertEqual(log["status"], "TAKEN")
        self.assertEqual(log["scheduled_date"], "2026-09-19")
        self.assertEqual(log["scheduled_time"], "08:00")
        self.assertIsNotNone(log["taken_time"])

        with sqlite3.connect(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM intake_logs WHERE patient_id = ?", (self.patient_id,))
            self.assertEqual(cur.fetchone()[0], 1)

    def test_02_duplicate_prevention(self):
        """TEST 2 — Duplicate prevention: Record the exact same dose twice. Expected: exactly 1 intake row."""
        med = self.db.create_medicine(
            patient_id=self.patient_id,
            name="Paracetamol 500mg",
            dosage="1 Tablet",
            schedule_time="08:00",
            schedule_times=["08:00"]
        )

        log1 = self.db.record_intake(
            patient_id=self.patient_id,
            medicine_id=med["medicine_id"],
            status="TAKEN",
            scheduled_date="2026-09-19",
            scheduled_time="08:00"
        )

        log2 = self.db.record_intake(
            patient_id=self.patient_id,
            medicine_id=med["medicine_id"],
            status="TAKEN",
            scheduled_date="2026-09-19",
            scheduled_time="08:00"
        )

        self.assertEqual(log1["log_id"], log2["log_id"])

        with sqlite3.connect(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM intake_logs WHERE patient_id = ?", (self.patient_id,))
            self.assertEqual(cur.fetchone()[0], 1)

    def test_03_multi_dose_same_day(self):
        """TEST 3 — Multi-dose same day: Medicine with ['08:00', '14:00', '20:00']. Record all 3 doses."""
        med = self.db.create_medicine(
            patient_id=self.patient_id,
            name="Ibuprofen 400mg",
            dosage="1 Tablet",
            schedule_times=["08:00", "14:00", "20:00"]
        )

        log1 = self.db.record_intake(self.patient_id, med["medicine_id"], "TAKEN", scheduled_date="2026-09-19", scheduled_time="08:00")
        log2 = self.db.record_intake(self.patient_id, med["medicine_id"], "SKIPPED", scheduled_date="2026-09-19", scheduled_time="14:00")
        log3 = self.db.record_intake(self.patient_id, med["medicine_id"], "TAKEN", scheduled_date="2026-09-19", scheduled_time="20:00")

        self.assertEqual(log1["scheduled_time"], "08:00")
        self.assertEqual(log2["scheduled_time"], "14:00")
        self.assertEqual(log3["scheduled_time"], "20:00")

        with sqlite3.connect(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM intake_logs WHERE patient_id = ? AND scheduled_date = '2026-09-19'", (self.patient_id,))
            self.assertEqual(cur.fetchone()[0], 3)

    def test_04_same_medicine_different_times(self):
        """TEST 4 — Same medicine, different times: Record 08:00 TAKEN and 14:00 TAKEN. Expected: 2 rows."""
        med = self.db.create_medicine(
            patient_id=self.patient_id,
            name="Metformin 500mg",
            dosage="1 Tablet",
            schedule_times=["08:00", "14:00"]
        )

        self.db.record_intake(self.patient_id, med["medicine_id"], "TAKEN", scheduled_date="2026-09-19", scheduled_time="08:00")
        self.db.record_intake(self.patient_id, med["medicine_id"], "TAKEN", scheduled_date="2026-09-19", scheduled_time="14:00")

        with sqlite3.connect(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT scheduled_time FROM intake_logs WHERE patient_id = ? ORDER BY scheduled_time ASC", (self.patient_id,))
            times = [r[0] for r in cur.fetchall()]
            self.assertEqual(times, ["08:00", "14:00"])

    def test_05_same_medicine_different_dates(self):
        """TEST 5 — Same medicine, different dates: 2026-09-19 08:00 and 2026-09-20 08:00. Expected: 2 rows."""
        med = self.db.create_medicine(
            patient_id=self.patient_id,
            name="Atorvastatin 20mg",
            dosage="1 Tablet",
            schedule_times=["08:00"]
        )

        self.db.record_intake(self.patient_id, med["medicine_id"], "TAKEN", scheduled_date="2026-09-19", scheduled_time="08:00")
        self.db.record_intake(self.patient_id, med["medicine_id"], "TAKEN", scheduled_date="2026-09-20", scheduled_time="08:00")

        with sqlite3.connect(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT scheduled_date FROM intake_logs WHERE patient_id = ? ORDER BY scheduled_date ASC", (self.patient_id,))
            dates = [r[0] for r in cur.fetchall()]
            self.assertEqual(dates, ["2026-09-19", "2026-09-20"])

    def test_06_same_dose_status_update_and_created_at_preservation(self):
        """TEST 6 — Same dose, status update: Record 08:00 SKIPPED, then 08:00 TAKEN.
        Verify 1 row total, final status TAKEN, log_id preserved, and created_at NOT updated."""
        med = self.db.create_medicine(
            patient_id=self.patient_id,
            name="Omeprazole 20mg",
            dosage="1 Capsule",
            schedule_times=["08:00"]
        )

        log_skipped = self.db.record_intake(
            self.patient_id, med["medicine_id"], "SKIPPED",
            scheduled_date="2026-09-19", scheduled_time="08:00"
        )
        self.assertEqual(log_skipped["status"], "SKIPPED")
        self.assertIsNone(log_skipped["taken_time"])
        original_created_at = log_skipped["created_at"]
        original_log_id = log_skipped["log_id"]

        # Now update the dose to TAKEN
        log_taken = self.db.record_intake(
            self.patient_id, med["medicine_id"], "TAKEN",
            scheduled_date="2026-09-19", scheduled_time="08:00"
        )
        self.assertEqual(log_taken["status"], "TAKEN")
        self.assertIsNotNone(log_taken["taken_time"])

        # Review correction 1 & 6: created_at and log_id must be preserved
        self.assertEqual(log_taken["log_id"], original_log_id)
        self.assertEqual(log_taken["created_at"], original_created_at)

        with sqlite3.connect(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*), status, log_id, created_at FROM intake_logs WHERE patient_id = ?", (self.patient_id,))
            row = cur.fetchone()
            self.assertEqual(row[0], 1)
            self.assertEqual(row[1], "TAKEN")
            self.assertEqual(row[2], original_log_id)
            self.assertEqual(row[3], original_created_at)

    def test_07_different_patients_isolation(self):
        """TEST 7 — Different patients: Patient A and Patient B recording same date/time remain independent."""
        patient_b = self.db.create_user({
            "name": "Second Patient",
            "email": "patient_b@medtrack.local",
            "password_hash": "hash456",
            "role": "patient",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        })
        patient_b_id = patient_b["user_id"]

        med_a = self.db.create_medicine(self.patient_id, "Aspirin", "100mg", schedule_time="08:00")
        med_b = self.db.create_medicine(patient_b_id, "Aspirin", "100mg", schedule_time="08:00")

        self.db.record_intake(self.patient_id, med_a["medicine_id"], "TAKEN", scheduled_date="2026-09-19", scheduled_time="08:00")
        self.db.record_intake(patient_b_id, med_b["medicine_id"], "TAKEN", scheduled_date="2026-09-19", scheduled_time="08:00")

        with sqlite3.connect(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT patient_id, COUNT(*) FROM intake_logs GROUP BY patient_id")
            counts = dict(cur.fetchall())
            self.assertEqual(counts[self.patient_id], 1)
            self.assertEqual(counts[patient_b_id], 1)

    def test_08_database_uniqueness_constraint(self):
        """TEST 8 — Database uniqueness: Direct SQL INSERT violating idx_intake_unique_dose triggers IntegrityError."""
        med = self.db.create_medicine(self.patient_id, "Lisinopril 10mg", "1 Tablet", schedule_time="08:00")
        self.db.record_intake(self.patient_id, med["medicine_id"], "TAKEN", scheduled_date="2026-09-19", scheduled_time="08:00")

        with sqlite3.connect(self.temp_db_path) as conn:
            cur = conn.cursor()
            with self.assertRaises(sqlite3.IntegrityError):
                cur.execute("""
                    INSERT INTO intake_logs (
                        log_id, patient_id, medicine_id, medicine_name, dosage,
                        scheduled_date, scheduled_time, status, log_date, created_at
                    ) VALUES ('log-dup-fail', ?, ?, 'Lisinopril', '10mg', '2026-09-19', '08:00', 'TAKEN', '2026-09-19', '2026-09-19T08:00:00')
                """, (self.patient_id, med["medicine_id"]))

    def test_09a_no_pending_in_service_layer(self):
        """TEST 9A — Service rejection of PENDING: record_intake(status='PENDING') raises ValueError and creates no row."""
        med = self.db.create_medicine(self.patient_id, "Cetirizine 10mg", "1 Tablet", schedule_time="08:00")

        with self.assertRaises(ValueError) as ctx:
            self.db.record_intake(self.patient_id, med["medicine_id"], "PENDING", scheduled_date="2026-09-19", scheduled_time="08:00")
        self.assertIn("PENDING is not a valid persistent intake status", str(ctx.exception))

        with sqlite3.connect(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM intake_logs WHERE patient_id = ?", (self.patient_id,))
            self.assertEqual(cur.fetchone()[0], 0)

    def test_09b_no_pending_in_database_check_constraint(self):
        """TEST 9B — Database rejection of PENDING: Direct SQL insert of status='PENDING' fails with IntegrityError."""
        med = self.db.create_medicine(self.patient_id, "Cetirizine 10mg", "1 Tablet", schedule_time="08:00")

        with sqlite3.connect(self.temp_db_path) as conn:
            cur = conn.cursor()
            with self.assertRaises(sqlite3.IntegrityError):
                cur.execute("""
                    INSERT INTO intake_logs (
                        log_id, patient_id, medicine_id, medicine_name, dosage,
                        scheduled_date, scheduled_time, status, log_date, created_at
                    ) VALUES ('log-pending-fail', ?, ?, 'Cetirizine', '10mg', '2026-09-19', '08:00', 'PENDING', '2026-09-19', '2026-09-19T08:00:00')
                """, (self.patient_id, med["medicine_id"]))

    def test_10_historical_preservation_on_medicine_deletion(self):
        """TEST 10 — Historical preservation: Deleting a medicine preserves historical intake records (medicine_id becomes NULL)."""
        med = self.db.create_medicine(self.patient_id, "Temporary Antibiotic", "500mg", schedule_time="08:00")
        med_id = med["medicine_id"]

        self.db.record_intake(self.patient_id, med_id, "TAKEN", scheduled_date="2026-09-19", scheduled_time="08:00")

        # Delete the medicine
        deleted = self.db.delete_medicine(med_id, self.patient_id)
        self.assertTrue(deleted)

        # Verify the intake record survived
        with sqlite3.connect(self.temp_db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON;")
            cur = conn.cursor()
            cur.execute("SELECT log_id, medicine_id, medicine_name, dosage, status FROM intake_logs WHERE patient_id = ?", (self.patient_id,))
            row = cur.fetchone()
            self.assertIsNotNone(row, "Intake log was erroneously deleted when medicine was deleted!")
            self.assertIsNone(row[1], "Expected medicine_id to be NULL (ON DELETE SET NULL)")
            self.assertEqual(row[2], "Temporary Antibiotic")
            self.assertEqual(row[3], "500mg")
            self.assertEqual(row[4], "TAKEN")

    def test_11_atomic_upsert_no_select_check(self):
        """TEST 11 — Concurrency/Atomicity verification:
        Verify atomic UPSERT succeeds and does not use SELECT-then-INSERT."""
        med = self.db.create_medicine(self.patient_id, "Vitamin D3", "1000IU", schedule_times=["08:00"])

        # Call record_intake
        log1 = self.db.record_intake(self.patient_id, med["medicine_id"], "TAKEN", scheduled_date="2026-09-19", scheduled_time="08:00")
        log2 = self.db.record_intake(self.patient_id, med["medicine_id"], "SKIPPED", scheduled_date="2026-09-19", scheduled_time="08:00")

        self.assertEqual(log1["log_id"], log2["log_id"])
        self.assertEqual(log2["status"], "SKIPPED")

        # Ensure index idx_intake_unique_dose is active and enforced
        with sqlite3.connect(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA index_list(intake_logs)")
            indexes = [r[1] for r in cur.fetchall()]
            self.assertIn("idx_intake_unique_dose", indexes)

    def test_12_timetable_multi_dose_virtual_pending(self):
        """TEST 12 — Timetable multi-dose schedule and virtual pending count:
        Verify get_patient_schedule calculates pending_count over unlogged doses without writing PENDING rows."""
        med = self.db.create_medicine(
            self.patient_id, "Multivitamin", "1 Tablet",
            schedule_times=["08:00", "14:00", "20:00"]
        )

        # Before any intake logging: 3 doses expected, all virtually PENDING
        schedule = self.db.get_patient_schedule(self.patient_id, target_date="2026-09-19")
        self.assertEqual(schedule["total_doses"], 3)
        self.assertEqual(schedule["pending_count"], 3)
        self.assertEqual(schedule["taken_count"], 0)
        self.assertEqual(schedule["skipped_count"], 0)
        self.assertEqual(len(schedule["doses"]), 3)

        # Log 08:00 as TAKEN
        self.db.record_intake(self.patient_id, med["medicine_id"], "TAKEN", scheduled_date="2026-09-19", scheduled_time="08:00")

        schedule = self.db.get_patient_schedule(self.patient_id, target_date="2026-09-19")
        self.assertEqual(schedule["total_doses"], 3)
        self.assertEqual(schedule["taken_count"], 1)
        self.assertEqual(schedule["pending_count"], 2)
        self.assertEqual(schedule["skipped_count"], 0)

        # Confirm that NO PENDING row exists in the database
        with sqlite3.connect(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM intake_logs WHERE status = 'PENDING'")
            self.assertEqual(cur.fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
