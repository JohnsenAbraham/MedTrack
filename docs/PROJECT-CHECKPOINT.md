# MedTrack Project Checkpoint & Master Progress Record

**Timestamp:** 2026-09-19 (End of Workday Checkpoint)  
**Project:** MedTrack – AWS Cloud-Enabled Medicine & Healthcare Management System  
**Current Status:** Phase 8 LOCKED & APPROVED. Ready for Phase 9 tomorrow.  

> **Historical Record Notice:** *This document is a historical checkpoint record representing the system baseline at the conclusion of Phase 8. For current, authoritative production architecture, data models, and test suites across all locked phases (Phases 1–12), refer to [README.md](file:///c:/Users/Admin/Documents/MedTrack/README.md) and [docs/architecture.md](file:///c:/Users/Admin/Documents/MedTrack/docs/architecture.md).*

---

## 1. Project Identity & Purpose
MedTrack is a medicine-first cloud healthcare management platform running on AWS. The system provides core clinical workflows:
- Medication scheduling, dose tracking, and adherence history
- Autonomous intake reminder notifications and dispatch
- Appointment management and physician consultation records
- Doctor prescription issuing and clinical isolation
- Medical diagnostic report management with private Amazon S3 cloud storage
- Patient medical profile, caregiver, and emergency contact registry

---

## 2. Current System Architecture
```
Browser / Client (HTTPS)
        ↓
      Nginx (Port 80 / 443 reverse proxy)
        ↓
     Gunicorn (WSGI HTTP server on localhost)
        ↓
   Flask Application (EC2 Host)
     ├──→ SQLite (Local / Dev data tier: medtrack_local.db)
     ├──→ DynamoDB (Production NoSQL data tier)
     ├──→ Amazon SNS (Notification & reminder fan-out)
     └──→ Amazon S3 (Private medical report document storage)

EC2 Instance Profile → IAM Instance Role (Scoped least-privilege AWS API access)
```

### Reminder Worker Architecture:
- Autonomous background worker: `workers/medicine_scheduler.py`
- Triggered by `systemd` timer every 5 minutes (`systemd/medtrack-scheduler.timer`)
- Application Timezone: `APP_TIMEZONE = Asia/Kolkata`
- Amazon SNS acts strictly as notification/fan-out delivery. The scheduler is an autonomous EC2 worker, NOT AWS EventBridge.

---

## 3. Locked Phases (Phases 1–8)

| Phase | Description | Status | Verification Gate |
| :--- | :--- | :---: | :--- |
| **Phase 1** | Dependencies & Central Configuration | **LOCKED** | Standardized configuration & modern styling |
| **Phase 2** | Database Schema & Entity Parity (SQLite) | **LOCKED** | Canonical schema & FK constraints verified |
| **Phase 3** | Multi-Dose Intake Deduplication & Atomic UPSERT | **LOCKED** | SQLite/DynamoDB UPSERT parity; zero duplicates |
| **Phase 4** | Dynamic Medicine State Machine | **LOCKED** | UPCOMING, DUE, TAKEN, SKIPPED, MISSED lifecycle |
| **Phase 5** | Autonomous Medicine Reminder Scheduler | **LOCKED** | 120s claim lease, max 3 retries, at-least-once |
| **Phase 6** | Security Hardening & CSRF Protection | **LOCKED** | Strict CSP (zero inline JS), CSRF tokens, session rotation |
| **Phase 7** | Prescription Management & Doctor Isolation | **LOCKED** | Dedicated prescriptions table, deterministic IDs, doctor isolation |
| **Phase 8** | Medical Reports & Private S3 Storage | **LOCKED** | SSE-AES256 S3 bucket, 600s pre-signed URLs, file validation |

*Rule:* Phases 1 through 8 are strictly LOCKED. Do NOT reopen, refactor, or modify any locked phase unless a future phase proves an actual regression.

---

## 4. Phase 8 Final Verification & Implementation State

### Architecture Implemented:
- **Storage Dual-Mode**: Production routes files to private Amazon S3 bucket; Local/Test uses isolated local filesystem storage without requiring AWS credentials.
- **Server-Side File Validation**: Enforces PDF (`%PDF-`), PNG (`\x89PNG`), and JPEG (`\xff\xd8\xff`) magic bytes; rejects unsupported extensions, empty files, and uploads exceeding 10 MB.
- **Storage Key Defense**: Traversal-safe, server-generated deterministic structure: `reports/<patient_id>/<report_id>/<safe_filename>`.
- **Private S3 Configuration**:
  - `BlockPublicAcls: true`
  - `BlockPublicPolicy: true`
  - `IgnorePublicAcls: true`
  - `RestrictPublicBuckets: true`
  - Server-side encryption: `SSEAlgorithm: AES256`
  - CloudFormation bucket deletion policy: `Retain`
  - Zero public bucket policies, zero public object ACLs, zero public report URLs.
- **Access Protocol**: Access to reports requires authentication and server-side authorization check before generating a 600-second pre-signed S3 GET URL.
- **Strict Authorization & IDOR Protection**:
  - Patients can view, download, and delete only their own reports. Cross-patient access returns `404 Not Found`.
  - Doctors can access reports only for patients with an established appointment relationship.
  - Client-supplied `patient_id` parameter tampering is completely ignored; patient identity is derived strictly from the authenticated session.
  - Upload doctor association requires an established appointment relationship.
- **Atomic Compensation Ordering**:
  - Upload failure: If database insertion fails after storage upload, the uploaded storage artifact is deleted immediately.
  - Delete failure: In-memory backup is taken before storage deletion; if database deletion fails, the storage artifact is restored, a danger flash message is emitted, and successful deletion is never claimed.
- **IAM Least Privilege**: Permissions on `MedTrackEC2Role` strictly limited to `s3:GetObject`, `s3:PutObject`, and `s3:DeleteObject` scoped to `${ReportsBucket.Arn}/*`. No `s3:ListBucket` and no `s3:*`.

### Verification Test Suite:
- **Phase 8 Test Suite**: `tests/test_phase8_reports.py` (35/35 passed).
- **Full Regression Test Suite**: `tests/test_*.py` (182/182 passed, 0 failures, 0 errors).
- **Canonical Database**: Unchanged and pristine (`medtrack_local.db` unmutated).
- **Database Integrity**: `PRAGMA foreign_key_check = []`, `PRAGMA integrity_check = [('ok',)]`.

---

## 5. Current Repository & Git State
- **Git Status**: Working-tree changes remain intentionally uncommitted. Zero commits made. Zero files staged.
- **Modified files**:
  - `.env.example`, `app.py`, `aws/cloudformation.yaml`, `config.py`, `requirements.txt`, `services/__init__.py`, `services/database.py`, `static/css/style.css`, `static/js/script.js`, templates, and `tests/test_app.py`.
- **Untracked files**:
  - `services/storage_service.py`, `services/rate_limiter.py`, `systemd/`, `workers/`, new templates (`patient_reports.html`, `report_form.html`, `doctor_patient_reports.html`, etc.), and test suites (`test_phase3_intake.py` through `test_phase8_reports.py`).

---

## 6. Canonical Database Baseline (`medtrack_local.db`)
Direct verification against the canonical SQLite database confirmed:
- `users`: 163
- `medicines`: 99
- `intake_logs`: 35
- `appointments`: 200
- `diagnoses`: 41
- `prescriptions`: 0
- `reports`: 0
- Foreign key violations: 0
- Integrity check: `ok`

*Rule:* All unit and integration tests must run against isolated temporary copies. `medtrack_local.db` must never be directly targeted or mutated by test suites.

---

## 7. Cloud AWS Architecture
- **Compute Tier**: Amazon EC2 instance provisioned via CloudFormation (`MedTrackEC2Instance`).
- **Web Stack**: Nginx reverse proxy → Gunicorn WSGI → Flask Application.
- **NoSQL Data Tier**: 8 physical Amazon DynamoDB tables (billing mode: `PAY_PER_REQUEST`).
- **Messaging Tier**: Amazon SNS standard topic (`MedTrackSNSTopic`).
- **Document Storage**: Amazon S3 private bucket (`ReportsBucket`).
- **IAM Security**: Amazon EC2 IAM Instance Profile with least-privilege scoped policies. Static AWS credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) are never stored or required.

---

## 8. Important Data-Model Decisions
1. **Physical DynamoDB Tables (8 total)**:
   - `MedTrack_Users`
   - `MedTrack_Medicines`
   - `MedTrack_IntakeLogs`
   - `MedTrack_Appointments`
   - `MedTrack_Prescriptions`
   - `MedTrack_Diagnoses`
   - `MedTrack_Reports`
   - `MedTrack_Notifications`
2. **No Physical Schedule Table**:
   - Schedule times are embedded directly in `medicines` as a JSON list (`schedule_times`, e.g. `["08:00", "14:00", "20:00"]`).
3. **Dose Identity**:
   - Quadruple key: `(patient_id, medicine_id, scheduled_date, scheduled_time)`.
4. **Intake States**:
   - Runtime virtual states: `UPCOMING`, `DUE`.
   - Persistent outcomes: `TAKEN`, `SKIPPED`, `MISSED`.
   - `PENDING` is NOT a valid persistent intake state.
5. **Notification Identity & Lifecycle**:
   - Identity: `remind#<patient_id>#<medicine_id>#<scheduled_date>#<scheduled_time>`.
   - Delivery Lifecycle: `PENDING` → `CLAIMED` → `SENT` / `FAILED`.
   - Read State: Independent `is_read` (boolean) and `read_at` (ISO timestamp).
   - No separate `ReminderLedger` or `AuditLogs` table.

---

## 9. Prescription Architecture Decisions
- **Prescription Entity**: Dedicated `prescriptions` table.
- **Linked Medicine Relationship**: Exactly 1 active prescription corresponds to 1 active linked medicine.
- **Deterministic Medicine ID**: `med_rx_<prescription_id>`.
- **Prescription Lifecycle**: `ACTIVE`, `DISCONTINUED`, `EXPIRED`, `SUPERSEDED`.
- **Deactivation Cascade**: Discontinuing or expiring a prescription automatically marks the linked medicine as inactive (`is_active = 0`).
- **Clinical History Preservation**: Hard deletion of prescriptions is prohibited; clinical records are preserved through state transitions.
- **Physician Identity**: Doctors are users with `role = "doctor"`. There is no separate `doctors` table.

---

## 10. Medical Reports & S3 Architecture Decisions
- **Report Entity**: Existing `reports` table is reused.
- **Storage Tier**: Private Amazon S3 bucket in production; local filesystem under `uploads/reports/` in dev/test.
- **Pre-signed URL Expiry**: 600 seconds (10 minutes).
- **Public Access**: Completely blocked. No public bucket policies or object ACLs.

---

## 11. Planned Project Roadmap (Phases 9–12)
- **Phase 9**: DynamoDB Parity & GSI Optimization (Reconcile SQLite implementation with DynamoDB, conditional writes, GSIs).
- **Phase 10**: IaC Synchronization & Production TLS (CloudFormation / Terraform alignment, HTTPS, Route53, ALB/ACM).
- **Phase 11**: Automated Tests & End-to-End Verification.
- **Phase 12**: Documentation & Safe Cleanup.

---

## 12. Exact Phase 9 Starting Point (For Tomorrow)
**Phase 9 has NOT started.**

When resuming work tomorrow:
1. **DO NOT immediately implement code changes.**
2. Perform a **READ-ONLY AUDIT** of:
   - Current `services/database.py` DynamoDB logic and AWS mock switches (`self.mock_aws`).
   - CloudFormation DynamoDB table schemas and missing tables (`MedTrack_Prescriptions`, `MedTrack_Reports`).
   - Planned GSI definitions:
     - `Users`: `EmailIndex` (PK: `email`)
     - `Medicines`: `PatientIndex` (PK: `patient_id`, SK: `created_at`)
     - `IntakeLogs`: `PatientDateIndex` (PK: `patient_id`, SK: `log_date`)
     - `Appointments`: `PatientIndex` (PK: `patient_id`, SK: `appointment_date`), `DoctorIndex` (PK: `doctor_id`, SK: `appointment_date`)
     - `Prescriptions`: `PatientIndex` (PK: `patient_id`, SK: `issued_date`), `DoctorIndex` (PK: `doctor_id`, SK: `issued_date`)
     - `Diagnoses`: `PatientIndex` (PK: `patient_id`, SK: `created_at`), `DoctorIndex` (PK: `doctor_id`, SK: `created_at`)
     - `Reports`: `PatientIndex` (PK: `patient_id`, SK: `uploaded_at`)
     - `Notifications`: `UserIndex` (PK: `patient_id`, SK: `created_at`)
   - Query patterns replacing table scans.
   - Conditional writes (`ConditionExpression`) for atomic deduplication and deterministic IDs.
3. Compare actual state against target architecture and create `implementation_plan.md`.
4. Obtain user review/approval before executing Phase 9.

---

## 13. Audit-First Workflow & Core Constraints
- **Audit-First**: Never start implementation without an internal read-only audit.
- **Isolated Testing**: Never run tests against canonical `medtrack_local.db`. Always use isolated temporary databases.
- **Regression Verification**: Every phase must execute its dedicated test suite AND the full regression suite (182 tests baseline).
- **Integrity Validation**: PRAGMA foreign_key_check and PRAGMA integrity_check must be clean at every gate.
- **No Premature Locking**: Never declare a phase locked without explicit independent user approval.
- **No Unapproved Commits**: Do not commit or stage files in git.
