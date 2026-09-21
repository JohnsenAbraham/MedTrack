"""
MedTrack Autonomous Medicine Reminder Scheduler Worker
Operates as an isolated process triggered by a 5-minute systemd timer.

Responsibilities:
1. Resolve current time in Config.APP_TIMEZONE (Asia/Kolkata).
2. Evaluate active medicine schedule times within the reminder window:
   (scheduled_datetime - 30 minutes <= now <= scheduled_datetime + 60 minutes).
3. Check persistent intake outcomes: ignore doses already TAKEN or SKIPPED.
4. Atomically persist reminder notifications using deterministic identity:
   remind#<patient_id>#<medicine_id>#<scheduled_date>#<scheduled_time>
   via SQLite INSERT ... ON CONFLICT(notification_id) DO NOTHING.
5. Atomically claim eligible PENDING notifications with a 120-second lease.
6. Dispatch claimed notifications via Amazon SNS (or mock).
7. Update delivery lifecycle:
   - SENT upon publish success
   - Revert to PENDING with cleared lease for retry if attempts < 3
   - FAILED if attempts >= 3
8. Terminate cleanly after one execution (no infinite loop).
"""

import sys
import os
import datetime
import logging
from zoneinfo import ZoneInfo

# Ensure root workspace directory is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import Config
from services.database import DatabaseService, parse_scheduled_time
from services.sns_service import SNSService

logger = logging.getLogger("workers.medicine_scheduler")


def run_scheduler_once(db=None, sns=None, now: datetime.datetime = None, lease_seconds: int = 120) -> dict:
    """
    Execute one complete autonomous reminder cycle and return telemetry metrics.
    Safe to execute repeatedly; guaranteed to be idempotent.
    """
    if db is None:
        db = DatabaseService()
    if sns is None:
        sns = SNSService()

    # 1. Resolve evaluation time in authoritative timezone
    tz_name = getattr(Config, "APP_TIMEZONE", "Asia/Kolkata")
    tz = ZoneInfo(tz_name)

    if now is None:
        eval_now = datetime.datetime.now(tz)
    elif isinstance(now, datetime.datetime):
        if now.tzinfo is None:
            eval_now = now.replace(tzinfo=tz)
        else:
            eval_now = now.astimezone(tz)
    else:
        eval_now = datetime.datetime.now(tz)

    # Target evaluation dates: previous local calendar date and current local date
    # This correctly covers doses whose reminder window crosses midnight (e.g., 23:30 dose
    # with window 23:00 to 00:30 evaluated at 00:15 the next calendar day).
    target_dates = [
        eval_now.date() - datetime.timedelta(days=1),
        eval_now.date()
    ]

    metrics = {
        "evaluated": 0,
        "eligible": 0,
        "created": 0,
        "claimed": 0,
        "sent": 0,
        "retried": 0,
        "failed": 0
    }

    # 2. Load all active patient medicines
    active_medicines = db.get_active_medicines()

    # 3. Evaluate each scheduled dose across target dates
    for med in active_medicines:
        patient_id = med["patient_id"]
        medicine_id = med["medicine_id"]
        med_name = med["name"]
        dosage = med["dosage"]

        schedule_times = db.normalize_schedule_times(med.get("schedule_times"), med.get("schedule_time"))
        for target_date in target_dates:
            target_date_str = target_date.strftime("%Y-%m-%d")
            for st in schedule_times:
                metrics["evaluated"] += 1

                # Check persistent intake outcome: TAKEN / SKIPPED doses are excluded
                intake = db.get_dose_intake_log(patient_id, medicine_id, target_date_str, st)
                if intake and intake.get("status") in ("TAKEN", "SKIPPED"):
                    continue

                # Reminder window evaluation: [scheduled - 30 min, scheduled + 60 min]
                st_time = parse_scheduled_time(st)
                sched_dt = datetime.datetime.combine(target_date, st_time, tzinfo=tz)
                due_start = sched_dt - datetime.timedelta(minutes=30)
                due_end = sched_dt + datetime.timedelta(minutes=60)

                if not (due_start <= eval_now <= due_end):
                    # Outside reminder window: either UPCOMING or MISSED (runtime state; no row persisted)
                    continue

                # Dose is eligible for reminder notification
                metrics["eligible"] += 1
                notification_id = f"remind#{patient_id}#{medicine_id}#{target_date_str}#{st}"
                title = "Medicine Reminder"
                message = (
                    f"Reminder: It is time to take your scheduled dose of {med_name} ({dosage}) "
                    f"scheduled for {st}. Please take your medication and log your intake."
                )

                # Atomic creation: at most one persisted notification per dose identity
                was_created = db.create_reminder_notification(
                    notification_id=notification_id,
                    patient_id=patient_id,
                    title=title,
                    message=message,
                    scheduled_date=target_date_str,
                    scheduled_time=st
                )
                if was_created:
                    metrics["created"] += 1

    # 4. Atomically claim eligible PENDING notifications with lease (explicitly medicine_reminder only)
    claimed_records = db.claim_pending_notifications(
        limit=50,
        lease_seconds=lease_seconds,
        now=eval_now,
        notification_type="medicine_reminder"
    )
    metrics["claimed"] = len(claimed_records)

    # 5. Dispatch claimed notifications via SNS
    for notif in claimed_records:
        notif_id = notif["notification_id"]
        claim_id = notif["delivery_claim_id"]
        target_patient_id = notif["patient_id"]
        subject = notif.get("title") or "Medicine Reminder"
        body = notif["message"]

        publish_success = sns.publish_notification(
            patient_id=target_patient_id,
            message=body,
            subject=subject
        )

        if publish_success:
            db.mark_notification_sent(
                notification_id=notif_id,
                claim_id=claim_id,
                sent_at=eval_now.isoformat()
            )
            metrics["sent"] += 1
        else:
            result_status = db.mark_notification_failed_or_retry(
                notification_id=notif_id,
                claim_id=claim_id,
                max_attempts=3
            )
            if result_status == "PENDING":
                metrics["retried"] += 1
            else:
                metrics["failed"] += 1

    logger.info(
        "Scheduler cycle complete: evaluated=%d, eligible=%d, created=%d, claimed=%d, sent=%d, retried=%d, failed=%d",
        metrics["evaluated"], metrics["eligible"], metrics["created"],
        metrics["claimed"], metrics["sent"], metrics["retried"], metrics["failed"]
    )
    return metrics


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    print("Starting MedTrack autonomous reminder scheduler worker...")
    results = run_scheduler_once()
    print(f"Cycle completed successfully: {results}")
    sys.exit(0)
