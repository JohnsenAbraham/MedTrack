# MedTrack — Medicine-First Healthcare Management & Reminder System

> **Cloud-Enabled Healthcare Application**  
> *A modern medication management and reminder system featuring Python Flask, Amazon DynamoDB, Amazon S3, Amazon SNS, and AWS EC2 with IAM instance roles.*  
> **Notice:** *This project is built for educational demonstration and portfolio evaluation. It uses synthetic patient data and simulated AWS components in local mode. Not intended for production clinical use.*

---

## Table of Contents
1. [Project Title](#1-project-title)
2. [Project Purpose](#2-project-purpose)
3. [Core Medicine-First Use Case](#3-core-medicine-first-use-case)
4. [Main Features](#4-main-features)
5. [Final Architecture](#5-final-architecture)
6. [Technology Stack](#6-technology-stack)
7. [Local Development](#7-local-development)
8. [Configuration & Environment Variables](#8-configuration--environment-variables)
9. [Database Model (DynamoDB & SQLite)](#9-database-model-dynamodb--sqlite)
10. [AWS Deployment (CloudFormation & Terraform)](#10-aws-deployment-cloudformation--terraform)
11. [Autonomous Reminder Scheduler](#11-autonomous-reminder-scheduler)
12. [Clinical Notifications & Amazon SNS](#12-clinical-notifications--amazon-sns)
13. [Medical Reports & Private Amazon S3](#13-medical-reports--private-amazon-s3)
14. [Security & Application Hardening](#14-security--application-hardening)
15. [Automated Testing Suite (268 Tests)](#15-automated-testing-suite-268-tests)
16. [Project Structure](#16-project-structure)
17. [Production Deployment & Direct EC2 TLS](#17-production-deployment--direct-ec2-tls)
18. [Important Production Notes](#18-important-production-notes)
19. [Demo & Mock Data Disclaimer](#19-demo--mock-data-disclaimer)

---

## 1. Project Title
**MedTrack — Cloud-Enabled Medicine-First Healthcare Management System**

---

## 2. Project Purpose
MedTrack is a **medicine-first healthcare management and reminder application** designed to solve the critical problem of patient medication non-adherence. 

Unlike broad hospital enterprise resource planning (EHR/ERP) or administrative hospital-management software, MedTrack concentrates squarely on the patient's daily medication regimen:
- Ensuring patients take the right medicines at the right dosage and time.
- Providing autonomous, timely reminders before doses become overdue.
- Tracking intake compliance with immutable history logs.
- Connecting patients with consulting doctors for prescriptions and diagnostic report oversight.

---

## 3. Core Medicine-First Use Case
The primary interaction loop in MedTrack is medication scheduling and intake tracking:

1. **Schedule Definition**: A patient (or prescribing physician) registers a medication (e.g., *Paracetamol 500mg, 1 Tablet, Twice Daily* at `08:00 AM` and `08:00 PM`).
2. **Runtime State Calculation**: At `07:45 AM`, the application dynamically marks the 8:00 AM dose as `UPCOMING`. At `08:00 AM`, it transitions to `DUE`.
3. **Autonomous Reminder**: The background scheduler detects the due dose window and dispatches a clinical alert notification via Amazon SNS.
4. **Intake Recording**: The patient logs into MedTrack and clicks **Taken** (or **Skip** if advised).
5. **Persistent Outcome & History**: The action is atomically committed to `MedTrack_IntakeLogs` with outcome `TAKEN` (or `SKIPPED`). If the dose window passes without user action, the system records `MISSED`. Full historical compliance is retained for physician review.

---

## 4. Main Features
- **User Authentication & Portals**: Role-based access control (RBAC) separating Patient and Doctor workspaces with session protection and 60-minute inactivity timeouts.
- **Medication Management (CRUD)**: Create, view, update, and deactivate medication records with dosage instructions and custom daily schedules.
- **Multi-Dose Embedded Schedules**: Support for single and multi-dose daily regimens (e.g., `["08:00", "14:00", "20:00"]`) embedded directly in medicine definitions.
- **Dynamic State Machine**: Real-time evaluation of dose statuses (`UPCOMING`, `DUE`, `MISSED`) calculated dynamically against the local timezone.
- **Intake Logging & Adherence History**: Persistent recording of dose outcomes (`TAKEN`, `SKIPPED`, `MISSED`) with atomic deduplication.
- **Prescription Oversight**: Attending physicians issue structured prescriptions that automatically provision linked active medications with deterministic identifiers (`med_rx_<prescription_id>`).
- **Doctor Directory & Outpatient Appointments**: Search accredited physicians by specialty and book, confirm, complete, or cancel consultations.
- **Clinical Diagnoses**: Doctors author consultation notes and treatment assessments attached to patient records.
- **Private Medical Reports & S3**: Upload, validate, and download clinical laboratory reports and imaging documents secured in private Amazon S3 storage with 600-second pre-signed URLs.
- **Clinical Notification Dispatch**: Event-driven alert dispatches via Amazon SNS for bookings, cancellations, confirmations, diagnoses, and medication reminders.
- **Patient Profile & Caregiver Contacts**: Manage verified patient demographics, emergency contacts, and caregiver telephone numbers.
- **Autonomous Reminder Scheduler**: Background EC2 service triggered every 5 minutes by a systemd timer, executing atomic lease claiming and reminder fan-out.
- **Infrastructure as Code (IaC)**: Turnkey deployment via CloudFormation or Terraform with direct EC2 Nginx TLS and zero static credentials.

---

## 5. Final Architecture

```
User Browser (HTTPS / HTTP)
    │
    ▼
Nginx Reverse Proxy (Port 80 / Port 443)
    │
    ▼ (HTTP over loopback: 127.0.0.1:8000)
Gunicorn WSGI Application Server (3 Workers)
    │
    ▼
Flask Application on Amazon EC2
    ├── Amazon DynamoDB (8 Physical Tables)
    ├── Private Amazon S3 (ReportsBucket with SSE-AES256)
    └── Amazon SNS (MedTrack-Alerts Topic)

Scheduler Architecture:
systemd timer (medtrack-scheduler.timer, Every 5 minutes)
    │
    ▼
medicine_scheduler.py
    ├── Amazon DynamoDB (Queries due dose windows)
    └── Amazon SNS (Publishes patient reminders)
```

> [!IMPORTANT]
> **Gunicorn is NOT publicly exposed.** Gunicorn binds exclusively to `127.0.0.1:8000`. All inbound traffic is received and handled by Nginx on ports 80/443.

---

## 6. Technology Stack
- **Web Framework**: Python 3.14 / 3.11+, Flask 3.1, Jinja2 Templates, Werkzeug
- **WSGI Application Server**: Gunicorn
- **Web Server & Reverse Proxy**: Nginx with Certbot (Let's Encrypt TLS)
- **Local Persistence**: SQLite 3 (`medtrack_local.db` for offline development)
- **Cloud Database**: Amazon DynamoDB (8 physical NoSQL tables with GSIs)
- **Document Storage**: Amazon S3 (Private bucket with SSE-AES256 encryption)
- **Messaging & Notifications**: Amazon SNS (`MedTrack-Alerts` standard topic)
- **Cloud Hosting & OS**: Amazon EC2 running Ubuntu 22.04 LTS
- **Infrastructure as Code**: AWS CloudFormation (`aws/cloudformation.yaml`) and Terraform (`aws/terraform/main.tf`)
- **Security & IAM**: EC2 IAM Instance Profile with IMDSv2, CSRF protection, strict CSP, and session rotation

---

## 7. Local Development

### Windows (PowerShell):
```powershell
# 1. Clone repository
git clone https://github.com/your-username/MedTrack.git
cd MedTrack

# 2. Create and activate virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run full test suite (268 tests)
venv\Scripts\python -m unittest discover -s tests -p "test_*.py"

# 5. Start local development server
venv\Scripts\python app.py
```

### Linux / macOS:
```bash
# 1. Clone repository
git clone https://github.com/your-username/MedTrack.git
cd MedTrack

# 2. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run test suite
python -m unittest discover -s tests -p "test_*.py"

# 5. Start local server
python app.py
```

### Local Mode Behavior (`MOCK_AWS=true`):
When `MOCK_AWS=true` is set:
- Relational data is persisted to the local SQLite database (`medtrack_local.db`).
- Medical report uploads are validated and stored in `uploads/reports/`.
- Amazon SNS alerts are rendered as formatted clinical dispatches printed to the application console.
- **Zero AWS credentials and zero AWS connectivity are required.** Local mode does not connect to live AWS services.

---

## 8. Configuration & Environment Variables

MedTrack is configured using a `.env` file synchronized with `.env.example`:

| Variable | Default Value | Description |
|---|---|---|
| `MOCK_AWS` | `true` | When `true`, uses SQLite, local storage, and simulated SNS. Set to `false` on EC2 for live AWS. |
| `DEMO_MODE` | `false` | Enables 1-click demo role switcher for evaluators. Must remain `false` in production. |
| `APP_TIMEZONE` | `Asia/Kolkata` | Application timezone used for patient intake windows and reminder calculations. |
| `AWS_REGION` | `us-east-1` | Target AWS region for DynamoDB, S3, and SNS services. |
| `DYNAMODB_USERS_TABLE` | `MedTrack_Users` | DynamoDB table for user accounts and doctor profiles. |
| `DYNAMODB_MEDICINES_TABLE` | `MedTrack_Medicines` | DynamoDB table for medications and embedded schedules. |
| `DYNAMODB_INTAKE_LOGS_TABLE` | `MedTrack_IntakeLogs` | DynamoDB table for persistent dose intake outcomes. |
| `DYNAMODB_APPOINTMENTS_TABLE` | `MedTrack_Appointments` | DynamoDB table for outpatient consultation bookings. |
| `DYNAMODB_PRESCRIPTIONS_TABLE` | `MedTrack_Prescriptions` | DynamoDB table for physician-issued prescriptions. |
| `DYNAMODB_DIAGNOSES_TABLE` | `MedTrack_Diagnoses` | DynamoDB table for clinical consultation records. |
| `DYNAMODB_REPORTS_TABLE` | `MedTrack_Reports` | DynamoDB table for medical report metadata. |
| `DYNAMODB_NOTIFICATIONS_TABLE` | `MedTrack_Notifications` | DynamoDB table for notification delivery lifecycle. |
| `SNS_TOPIC_ARN` | `replace-with-sns-topic-arn` | ARN of the Amazon SNS topic for clinical alerts. |
| `S3_REPORTS_BUCKET` | `""` | Name of the private S3 bucket for medical report documents. |
| `SECRET_KEY` | *(Set strong key)* | Flask session signing secret key. |
| `SESSION_COOKIE_SECURE` | `false` | Set to `true` once HTTPS/TLS is actively operational. |
| `USE_PROXY_FIX` | `false` | Set to `true` when running behind Nginx reverse proxy with TLS termination. |
| `DOMAIN_NAME` | `""` | Registered DNS domain name pointing to EC2 for Certbot TLS provisioning. |
| `SSL_CERTIFICATE_PATH` | `""` | Path to active TLS certificate (configured during TLS activation). |
| `SSL_CERTIFICATE_KEY_PATH` | `""` | Path to active TLS private key. |

---

## 9. Database Model (DynamoDB & SQLite)

The production schema consists of **exactly 8 physical DynamoDB tables**:

1. **`MedTrack_Users`** (PK: `user_id`): User authentication and profiles. Includes `EmailIndex` GSI on `email`. Doctors are users with `role = "doctor"`.
2. **`MedTrack_Medicines`** (PK: `medicine_id`): Medication definitions with embedded `schedule_times` (JSON list). Includes `PatientIndex` GSI (`patient_id`, SK: `created_at`).
3. **`MedTrack_IntakeLogs`** (PK: `log_id`): Persistent dose outcomes (`TAKEN`, `SKIPPED`, `MISSED`). Composite key `<patient_id>#<medicine_id>#<date>#<time>`. Includes `PatientDateIndex` GSI (`patient_id`, SK: `log_date`).
4. **`MedTrack_Appointments`** (PK: `appointment_id`): Visit bookings. Includes `PatientIndex` and `DoctorIndex` GSIs on `appointment_date`.
5. **`MedTrack_Prescriptions`** (PK: `prescription_id`): Doctor medication orders linked to `MedTrack_Medicines`. Includes `PatientIndex` and `DoctorIndex` GSIs on `issued_date`.
6. **`MedTrack_Diagnoses`** (PK: `diagnosis_id`): Clinical consultation findings. Includes `PatientIndex` and `DoctorIndex` GSIs on `created_at`.
7. **`MedTrack_Reports`** (PK: `report_id`): Metadata for medical documents. File binaries are stored in private S3. Includes `PatientIndex` GSI on `uploaded_at`.
8. **`MedTrack_Notifications`** (PK: `notification_id`): System alerts and reminder logs with delivery states. Includes `PatientIndex` GSI (`patient_id`, SK: `created_at`).

> [!NOTE]
> **Data Model Clarifications**:
> - **No Schedule Table**: Intake times are embedded directly in `MedTrack_Medicines.schedule_times`.
> - **No Separate Doctors Table**: Doctors are records in `MedTrack_Users` where `role = "doctor"`.
> - **No ReminderLedger or AuditLogs Table**: Dose tracking and notifications use dedicated DynamoDB tables with atomic idempotency keys.

---

## 10. AWS Deployment (CloudFormation & Terraform)

MedTrack provides two equivalent, production-grade Infrastructure-as-Code (IaC) deployment paths:
- **AWS CloudFormation**: [`aws/cloudformation.yaml`](aws/cloudformation.yaml)
- **Terraform**: [`aws/terraform/main.tf`](aws/terraform/main.tf)

Both options provision identical infrastructure:
- Amazon EC2 virtual machine running Ubuntu 22.04 LTS.
- IAM Instance Role granting scoped least-privilege permissions to DynamoDB, S3, and SNS via IMDSv2.
- 8 on-demand Amazon DynamoDB tables with configured GSIs.
- Private Amazon S3 bucket (`ReportsBucket`) with complete public access block and SSE-AES256 encryption.
- Amazon SNS topic (`MedTrack-Alerts`) with decoupled notification fan-out.
- Nginx reverse proxy routing traffic to Gunicorn on `127.0.0.1:8000`.
- Systemd background service and timer for reminder scheduling.

*CloudFormation and Terraform are alternative deployment methods; choose one based on your organization's toolchain.*

---

## 11. Autonomous Reminder Scheduler

MedTrack includes an autonomous background reminder scheduler that monitors patient medication schedules:

- **Worker Script**: `workers/medicine_scheduler.py`
- **Trigger**: Systemd timer (`systemd/medtrack-scheduler.timer`) running **every 5 minutes** (`OnUnitActiveSec=5min`).
- **Claim Lease Mechanism**: Implements a 120-second atomic claim lease with a maximum of 3 retries to guarantee at-least-once reminder delivery and prevent duplicate notifications.
- **Autonomous Architecture**: Operates as a native background daemon on the EC2 instance without requiring external services like AWS EventBridge or SQS DLQ.

---

## 12. Clinical Notifications & Amazon SNS

Clinical alerts and dose reminders are published to the `MedTrack-Alerts` standard topic:
- **Topic Configuration**:
  - `TopicName: MedTrack-Alerts`
  - `DisplayName: MedTrack Clinical Notification Alerts`
- **Decoupled Architecture**: In accordance with production design, no administrative email subscription is hardcoded in IaC templates. Consumers subscribe dynamically via SMS, email, webhook, or downstream lambda processors.
- **Notification Events**:
  - Appointment Booked
  - Appointment Confirmed
  - Appointment Cancelled
  - Diagnosis Recorded
  - Medication Registered
  - Intake Reminder

---

## 13. Medical Reports & Private Amazon S3

Diagnostic laboratory reports and imaging documents are secured with private Amazon S3 storage:
- **Storage Dual-Mode**: S3 in production (`MOCK_AWS=false`); local filesystem (`uploads/reports/`) in local development (`MOCK_AWS=true`).
- **File Validation**: Server-side inspection of magic bytes for allowed types (`%PDF-`, `\x89PNG`, `\xff\xd8\xff`) and 10 MB maximum file size limit.
- **Deterministic Key Structure**: Traversal-safe storage keys: `reports/<patient_id>/<report_id>/<filename>`.
- **Pre-signed Access**: All access requires authentication and server-side authorization check before generating a 600-second (10-minute) pre-signed GET URL.
- **Complete Public Access Block**: All public ACLs and bucket policies are blocked; bucket encryption is enforced with SSE-AES256.

---

## 14. Security & Application Hardening
- **Authentication**: Werkzeug PBKDF2/SHA-256 password hashing with unique salts.
- **Session Protection**: Session fixation defense via session regeneration upon login/logout; 60-minute inactivity timeout.
- **Cookie Security**: `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE="Lax"`, and `SESSION_COOKIE_SECURE=True` when TLS is active.
- **CSRF Protection**: Synchronizer token verification enforced on all state-changing endpoints (`POST`, `PUT`, `DELETE`).
- **Content Security Policy (CSP)**: Strict headers with zero inline JavaScript (`script-src 'self'`).
- **Authorization & IDOR Protection**: Patient identities are strictly derived from server session state; doctors can only access records of patients with established clinical relationships.
- **Least Privilege IAM**: EC2 role permissions are restricted to necessary table and bucket ARNs with zero static AWS credentials.
- **Loopback WSGI**: Gunicorn binds strictly to `127.0.0.1:8000` behind Nginx.

---

## 15. Automated Testing Suite (268 Tests)

MedTrack maintains a test suite comprising **268 automated tests** across 10 test modules:

| Test Module | Test Count | Focus Area |
|---|:---:|---|
| `tests/test_app.py` | 21 | Core workflows, authentication, appointment booking, and dashboards |
| `tests/test_phase3_intake.py` | 13 | Multi-dose intake deduplication and atomic UPSERT operations |
| `tests/test_phase4_state_machine.py` | 15 | Dynamic dose state machine (`UPCOMING`, `DUE`, `TAKEN`, `SKIPPED`, `MISSED`) |
| `tests/test_phase5_scheduler.py` | 25 | Autonomous reminder scheduler, lease claiming, and retry logic |
| `tests/test_phase6_security.py` | 36 | CSRF validation, session rotation, strict CSP, and rate limiting |
| `tests/test_phase7_prescriptions.py` | 37 | Doctor prescription issuing, medicine linkage, and clinical deactivation |
| `tests/test_phase8_reports.py` | 35 | Medical report validation, private S3 storage, and pre-signed URLs |
| `tests/test_phase9_dynamodb.py` | 30 | DynamoDB NoSQL parity, conditional writes, and GSI query patterns |
| `tests/test_phase10_tls.py` | 33 | IaC synchronization, Nginx TLS activation script, and rollback safety |
| `tests/test_phase11_targeted.py` | 23 | Multi-threaded UPSERT concurrency, FK enforcement, StorageService, and session boundaries |
| **Total Test Suite** | **268** | **268 Passed, 0 Failures, 0 Errors** |

### Running the Test Suite:
```bash
# Windows
venv\Scripts\python -m unittest discover -s tests -p "test_*.py"

# Linux / macOS
python -m unittest discover -s tests -p "test_*.py"
```

> [!NOTE]
> All unit and integration tests execute against isolated temporary test databases and mocked AWS services. The canonical database (`medtrack_local.db`) is never modified by test execution.

---

## 16. Project Structure

```text
MedTrack/
├── app.py                         # Flask application entry point & route controllers
├── config.py                      # Centralized configuration & environment loader
├── requirements.txt               # Production & test dependencies
├── .env.example                   # Baseline environment variable template
├── medtrack_local.db              # Pristine canonical SQLite database (baseline data)
├── aws/
│   ├── cloudformation.yaml        # Complete AWS CloudFormation IaC template
│   └── terraform/
│       └── main.tf                # Complete AWS Terraform IaC specification
├── services/
│   ├── __init__.py
│   ├── database.py                # DatabaseService abstraction (DynamoDB & SQLite)
│   ├── dynamodb_service.py        # DynamoDB low-level operations & GSI query patterns
│   ├── storage_service.py         # StorageService (Private S3 & local filesystem)
│   ├── sns_service.py             # SNSService (Amazon SNS publishing & mock console)
│   └── rate_limiter.py            # In-memory login rate limiter
├── workers/
│   ├── __init__.py
│   └── medicine_scheduler.py      # Autonomous medicine reminder background worker
├── systemd/
│   ├── medtrack-scheduler.service # Systemd service unit for reminder scheduler
│   └── medtrack-scheduler.timer   # Systemd timer unit (fires every 5 minutes)
├── templates/                     # Server-rendered Jinja2 HTML templates
├── static/
│   ├── css/style.css              # Custom responsive CSS design system
│   └── js/script.js               # Accessible client-side JavaScript (zero inline scripts)
├── tests/                         # 10 test modules (268 automated unit & integration tests)
├── uploads/
│   ├── .gitkeep
│   └── reports/                   # Local storage directory for reports in mock mode
└── docs/
    ├── architecture.md            # Detailed cloud architecture and system design
    ├── ER-Diagram.md              # Complete entity-relationship and data model documentation
    └── PROJECT-CHECKPOINT.md      # Historical milestone checkpoint record
```

---

## 17. Production Deployment & Direct EC2 TLS

### Production Stack Overview:
In production, MedTrack runs on an Ubuntu 22.04 LTS EC2 instance managed by systemd:
- Nginx listens on port 80 (HTTP) and port 443 (HTTPS).
- Gunicorn runs behind Nginx:
  ```bash
  gunicorn --workers 3 --bind 127.0.0.1:8000 app:app
  ```
- Nginx reverse-proxies client traffic to `127.0.0.1:8000`.

### Direct EC2 Nginx TLS Activation:
1. **Initial Deployment**: HTTP on port 80 is the default operational state. `ApplicationURL` outputs the HTTP endpoint.
2. **Prerequisites for TLS**:
   - Point your registered domain name to the EC2 public IP via a DNS A record.
   - Verify port 80 and 443 are open in the EC2 security group.
3. **Run Activation Script**:
   ```bash
   sudo /usr/local/bin/medtrack-enable-tls.sh your-domain.com
   ```
4. **Verification & Safe Rollback**:
   - The script verifies DNS resolution before requesting certificates from Certbot (Let's Encrypt).
   - Nginx configuration is updated with modern TLS ciphers, TLS 1.2/1.3, and HTTP→HTTPS redirect.
   - `nginx -t` verifies configuration syntax before reloading Nginx.
   - If certificate acquisition or configuration validation fails, the script automatically rolls back to the working HTTP configuration.
   - **No self-signed certificates** are deployed in production, and no ALB, ACM, or Route53 dependencies are introduced.

---

## 18. Important Production Notes
- **Static Credentials**: Never place static AWS credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) in `.env` or application code. The application uses the EC2 IAM Instance Role automatically.
- **Port Exposure**: Never bind Gunicorn to `0.0.0.0:5000` or expose it directly to the Internet. Always use `127.0.0.1:8000` behind Nginx.
- **Scheduler Interval**: The background reminder scheduler runs **every 5 minutes** via `medtrack-scheduler.timer`. Do not configure it for sub-minute polling.
- **Database Safety**: Never run automated tests against the canonical database (`medtrack_local.db`). The test suite creates isolated temporary SQLite databases automatically.

---

## 19. Demo & Mock Data Disclaimer
This application includes synthetic sample data and an evaluator demo switcher (`DEMO_MODE=true` in local mode only) to facilitate demonstration and evaluation. All patient names, medical records, diagnostic observations, and doctor profiles are entirely fictional. MedTrack is not intended for commercial healthcare operations or emergency medical alerting.
