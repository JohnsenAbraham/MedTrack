"""
Phase 4 Tests: Dynamic Medicine Dose State Machine
Verifies runtime state calculation, window boundaries, persistence isolation,
timezone handling (Asia/Kolkata), multi-dose evaluation, and backward compatibility.
"""

import os
import tempfile
import unittest
import datetime
import uuid
from zoneinfo import ZoneInfo
from config import Config
from services.database import DatabaseService, calculate_dose_state, parse_scheduled_time


class TestPhase4StateMachine(unittest.TestCase):
    """Test suite for Phase 4 Dynamic Medicine Dose State Machine."""

    def setUp(self):
        # Create an isolated temporary database for each test to guarantee no interference with live DB
        self.temp_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_fd)

        self.db = DatabaseService()
        self.db.db_path = self.temp_db_path
        self.db._init_sqlite()

        self.tz = ZoneInfo(getattr(Config, "APP_TIMEZONE", "Asia/Kolkata"))
        self.test_date = "2026-09-19"

        # Create test patient
        self.patient = self.db.create_user({
            "name": "Phase 4 Patient",
            "email": f"p4_{uuid.uuid4().hex[:8]}@medtrack.local",
            "password_hash": "hash123",
            "phone": "+919876543210",
            "date_of_birth": "1990-01-01",
            "gender": "Other",
            "role": "patient",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        })
        self.patient_id = self.patient["user_id"]

        # Create test medicine with multiple doses: 08:00, 14:00, 20:00
        self.med = self.db.create_medicine(
            patient_id=self.patient_id,
            name="StateTestMed",
            dosage="50mg",
            schedule_time="08:00",
            schedule_times=["08:00", "14:00", "20:00"],
            frequency="Three times daily",
            meal_timing="After meals",
            notes="Take with water"
        )

    def tearDown(self):
        if os.path.exists(self.temp_db_path):
            try:
                os.remove(self.temp_db_path)
            except Exception:
                pass

    def test_01_upcoming_dose_before_window(self):
        """TEST 1: now < scheduled - 30 min evaluates to UPCOMING."""
        # Scheduled: 08:00. Window opens: 07:30. At 07:29 -> UPCOMING
        eval_now = datetime.datetime(2026, 9, 19, 7, 29, 0, tzinfo=self.tz)
        state = calculate_dose_state(self.test_date, "08:00", intake_status=None, now=eval_now)
        self.assertEqual(state, "UPCOMING")

    def test_02_due_boundary_start(self):
        """TEST 2: now == scheduled - 30 min evaluates to DUE (boundary)."""
        # Scheduled: 08:00. At 07:30:00 -> DUE
        eval_now = datetime.datetime(2026, 9, 19, 7, 30, 0, tzinfo=self.tz)
        state = calculate_dose_state(self.test_date, "08:00", intake_status=None, now=eval_now)
        self.assertEqual(state, "DUE")

    def test_03_due_nominal_scheduled_time(self):
        """TEST 3: now == scheduled_time evaluates to DUE."""
        # Scheduled: 08:00. At 08:00:00 -> DUE
        eval_now = datetime.datetime(2026, 9, 19, 8, 0, 0, tzinfo=self.tz)
        state = calculate_dose_state(self.test_date, "08:00", intake_status=None, now=eval_now)
        self.assertEqual(state, "DUE")

    def test_04_due_boundary_end(self):
        """TEST 4: now == scheduled + 60 min evaluates to DUE (boundary)."""
        # Scheduled: 08:00. Window closes: 09:00:00. At 09:00:00 -> DUE
        eval_now = datetime.datetime(2026, 9, 19, 9, 0, 0, tzinfo=self.tz)
        state = calculate_dose_state(self.test_date, "08:00", intake_status=None, now=eval_now)
        self.assertEqual(state, "DUE")

    def test_05_missed_boundary_start(self):
        """TEST 5: now == scheduled + 61 min evaluates to MISSED (boundary)."""
        # Scheduled: 08:00. At 09:01:00 -> MISSED
        eval_now = datetime.datetime(2026, 9, 19, 9, 1, 0, tzinfo=self.tz)
        state = calculate_dose_state(self.test_date, "08:00", intake_status=None, now=eval_now)
        self.assertEqual(state, "MISSED")

    def test_06_far_past_unlogged_dose_is_missed(self):
        """TEST 6: Far past unlogged dose evaluates to MISSED."""
        # Scheduled: 08:00. At 18:00:00 -> MISSED
        eval_now = datetime.datetime(2026, 9, 19, 18, 0, 0, tzinfo=self.tz)
        state = calculate_dose_state(self.test_date, "08:00", intake_status=None, now=eval_now)
        self.assertEqual(state, "MISSED")

    def test_07_persistent_taken_overrides_overdue_window(self):
        """TEST 7: Persistent TAKEN overrides overdue time window."""
        # Log TAKEN
        self.db.record_intake(self.patient_id, self.med["medicine_id"], "TAKEN",
                              scheduled_date=self.test_date, scheduled_time="08:00")
        eval_now = datetime.datetime(2026, 9, 19, 18, 0, 0, tzinfo=self.tz)
        state = calculate_dose_state(self.test_date, "08:00", intake_status="TAKEN", now=eval_now)
        self.assertEqual(state, "TAKEN")

        # Also verify through get_patient_schedule
        schedule = self.db.get_patient_schedule(self.patient_id, target_date=self.test_date, now=eval_now)
        dose_0800 = next(d for d in schedule["doses"] if d["schedule_time"] == "08:00")
        self.assertEqual(dose_0800["status"], "TAKEN")

    def test_08_persistent_skipped_overrides_overdue_window(self):
        """TEST 8: Persistent SKIPPED overrides overdue time window."""
        # Log SKIPPED
        self.db.record_intake(self.patient_id, self.med["medicine_id"], "SKIPPED",
                              scheduled_date=self.test_date, scheduled_time="08:00")
        eval_now = datetime.datetime(2026, 9, 19, 18, 0, 0, tzinfo=self.tz)
        state = calculate_dose_state(self.test_date, "08:00", intake_status="SKIPPED", now=eval_now)
        self.assertEqual(state, "SKIPPED")

        # Also verify through get_patient_schedule
        schedule = self.db.get_patient_schedule(self.patient_id, target_date=self.test_date, now=eval_now)
        dose_0800 = next(d for d in schedule["doses"] if d["schedule_time"] == "08:00")
        self.assertEqual(dose_0800["status"], "SKIPPED")

    def test_09_persistent_taken_during_upcoming_window(self):
        """TEST 9: Persistent TAKEN taken early during UPCOMING window remains TAKEN."""
        eval_now = datetime.datetime(2026, 9, 19, 7, 0, 0, tzinfo=self.tz)
        state = calculate_dose_state(self.test_date, "08:00", intake_status="TAKEN", now=eval_now)
        self.assertEqual(state, "TAKEN")

    def test_10_preexisting_persisted_missed_compatibility(self):
        """TEST 10: Backward compatibility: pre-existing persisted MISSED recognized by state machine."""
        eval_now = datetime.datetime(2026, 9, 19, 8, 0, 0, tzinfo=self.tz)
        # Even during DUE window (08:00), a pre-existing persistent MISSED status returns MISSED
        state = calculate_dose_state(self.test_date, "08:00", intake_status="MISSED", now=eval_now)
        self.assertEqual(state, "MISSED")

    def test_11_missed_does_not_persist_rows(self):
        """TEST 11: calculate_dose_state and get_patient_schedule MUST NOT persist MISSED rows in intake_logs."""
        eval_now = datetime.datetime(2026, 9, 19, 18, 0, 0, tzinfo=self.tz)

        # Confirm 0 intake logs initially
        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM intake_logs WHERE patient_id = ?", (self.patient_id,))
            initial_count = c.fetchone()[0]
        self.assertEqual(initial_count, 0)

        # Call calculate_dose_state
        state = calculate_dose_state(self.test_date, "08:00", intake_status=None, now=eval_now)
        self.assertEqual(state, "MISSED")

        # Call get_patient_schedule
        schedule = self.db.get_patient_schedule(self.patient_id, target_date=self.test_date, now=eval_now)
        self.assertEqual(schedule["missed_count"], 2)  # 08:00 and 14:00 are MISSED at 18:00; 20:00 is UPCOMING
        self.assertEqual(schedule["upcoming_count"], 1)

        # Verify NO rows were inserted into intake_logs
        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM intake_logs WHERE patient_id = ?", (self.patient_id,))
            final_count = c.fetchone()[0]
        self.assertEqual(final_count, 0, "calculate_dose_state and get_patient_schedule must never persist MISSED rows")

    def test_12_upcoming_and_due_do_not_persist_rows(self):
        """TEST 12: UPCOMING and DUE states are runtime-calculated and NEVER persisted in intake_logs."""
        # Evaluate when 08:00 is DUE and 14:00 is UPCOMING
        eval_now = datetime.datetime(2026, 9, 19, 8, 15, 0, tzinfo=self.tz)
        schedule = self.db.get_patient_schedule(self.patient_id, target_date=self.test_date, now=eval_now)

        dose_0800 = next(d for d in schedule["doses"] if d["schedule_time"] == "08:00")
        dose_1400 = next(d for d in schedule["doses"] if d["schedule_time"] == "14:00")
        self.assertEqual(dose_0800["status"], "DUE")
        self.assertEqual(dose_1400["status"], "UPCOMING")

        # Verify NO rows in intake_logs
        with self.db._get_sqlite_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM intake_logs WHERE patient_id = ?", (self.patient_id,))
            count = c.fetchone()[0]
        self.assertEqual(count, 0, "UPCOMING and DUE states must never be persisted in intake_logs")

    def test_13_timezone_accuracy_asia_kolkata(self):
        """TEST 13: Timezone accuracy: Config.APP_TIMEZONE ('Asia/Kolkata') is strictly respected."""
        # 08:00 IST is 02:30 UTC.
        # If now is 02:30 UTC, it corresponds to 08:00 IST, which should be DUE.
        now_utc = datetime.datetime(2026, 9, 19, 2, 30, 0, tzinfo=datetime.timezone.utc)
        state = calculate_dose_state(self.test_date, "08:00", intake_status=None, now=now_utc)
        self.assertEqual(state, "DUE")

        # 07:29 IST is 01:59 UTC -> UPCOMING
        now_utc_upcoming = datetime.datetime(2026, 9, 19, 1, 59, 0, tzinfo=datetime.timezone.utc)
        state_upcoming = calculate_dose_state(self.test_date, "08:00", intake_status=None, now=now_utc_upcoming)
        self.assertEqual(state_upcoming, "UPCOMING")

        # 09:01 IST is 03:31 UTC -> MISSED
        now_utc_missed = datetime.datetime(2026, 9, 19, 3, 31, 0, tzinfo=datetime.timezone.utc)
        state_missed = calculate_dose_state(self.test_date, "08:00", intake_status=None, now=now_utc_missed)
        self.assertEqual(state_missed, "MISSED")

    def test_14_multi_dose_timetable_evaluation(self):
        """TEST 14: Multi-dose evaluation at 14:15 IST yields MISSED (08:00), DUE (14:00), UPCOMING (20:00)."""
        eval_now = datetime.datetime(2026, 9, 19, 14, 15, 0, tzinfo=self.tz)
        schedule = self.db.get_patient_schedule(self.patient_id, target_date=self.test_date, now=eval_now)

        doses_by_time = {d["schedule_time"]: d["status"] for d in schedule["doses"]}
        self.assertEqual(doses_by_time["08:00"], "MISSED", "08:00 dose evaluated at 14:15 should be MISSED")
        self.assertEqual(doses_by_time["14:00"], "DUE", "14:00 dose evaluated at 14:15 should be DUE")
        self.assertEqual(doses_by_time["20:00"], "UPCOMING", "20:00 dose evaluated at 14:15 should be UPCOMING")

    def test_15_adherence_and_counts_integrity(self):
        """TEST 15: get_patient_schedule returns accurate count breakdowns and adherence percentage."""
        # Doses: 08:00, 14:00, 20:00
        # Mark 08:00 as TAKEN
        self.db.record_intake(self.patient_id, self.med["medicine_id"], "TAKEN",
                              scheduled_date=self.test_date, scheduled_time="08:00")

        # Mark 14:00 as SKIPPED
        self.db.record_intake(self.patient_id, self.med["medicine_id"], "SKIPPED",
                              scheduled_date=self.test_date, scheduled_time="14:00")

        # Evaluate at 14:15 IST
        # 08:00 is TAKEN
        # 14:00 is SKIPPED
        # 20:00 is UPCOMING
        eval_now = datetime.datetime(2026, 9, 19, 14, 15, 0, tzinfo=self.tz)
        schedule = self.db.get_patient_schedule(self.patient_id, target_date=self.test_date, now=eval_now)

        self.assertEqual(schedule["total_doses"], 3)
        self.assertEqual(schedule["taken_count"], 1)
        self.assertEqual(schedule["skipped_count"], 1)
        self.assertEqual(schedule["due_count"], 0)
        self.assertEqual(schedule["upcoming_count"], 1)
        self.assertEqual(schedule["missed_count"], 0)
        # pending_count must equal due_count + upcoming_count + missed_count = 1
        self.assertEqual(schedule["pending_count"], 1)
        # adherence_pct = round(1 / 3 * 100) = 33%
        self.assertEqual(schedule["adherence_pct"], 33)


if __name__ == "__main__":
    unittest.main()
