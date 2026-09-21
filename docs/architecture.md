# MedTrack Cloud Architecture & System Design

## 1. Executive Summary
MedTrack is an enterprise-grade, medicine-first healthcare management and medication adherence web application. The platform provides reliable medication scheduling, dynamic dose state tracking, autonomous reminder dispatches, outpatient appointment management, physician consultations, doctor-issued prescriptions, and private diagnostic report management.

The architecture is built for dual-mode execution:
- **AWS Production Mode (`MOCK_AWS=false`)**: Fully integrated into AWS services including **Amazon EC2**, **Amazon DynamoDB**, **Amazon S3**, and **Amazon SNS**, operating under a least-privilege **AWS IAM Instance Role** with zero hardcoded credentials.
- **Local Development Mode (`MOCK_AWS=true`)**: Self-contained local execution utilizing **SQLite** (`medtrack_local.db`), local filesystem report storage (`uploads/reports/`), and simulated console SNS dispatch, enabling rapid offline development and automated testing with zero AWS cost.

---

## 2. High-Level Architecture Diagram

```mermaid
flowchart TD
    subgraph Client Tier
        UserBrowser["Web Browser (Patient / Doctor)"]
    end

    subgraph AWS Cloud Environment (EC2 Instance)
        subgraph Ingress & Web Server Tier
            Nginx["Nginx Reverse Proxy\n(Ports 80 HTTP / 443 HTTPS)"]
            Gunicorn["Gunicorn WSGI Application Server\n(Strictly Loopback: 127.0.0.1:8000)"]
            Nginx -->|Proxy Pass 127.0.0.1:8000| Gunicorn
        end

        subgraph Application Tier
            Flask["Flask Web Application (app.py)"]
            ServiceLayer["Service Layer\n(DatabaseService, StorageService, SNSService, RateLimiter)"]
            Gunicorn --> Flask
            Flask --> ServiceLayer
        end

        subgraph Background Worker Tier
            SystemdTimer["systemd timer (medtrack-scheduler.timer)\n(Fires every 5 minutes)"]
            SchedulerWorker["Medicine Reminder Worker\n(workers/medicine_scheduler.py)"]
            SystemdTimer -->|Triggers| SchedulerWorker
        end

        IAMRole["IAM Instance Role\n(Temporary STS Credentials via IMDSv2)"]
        IAMRole -. Authorizes .-> ServiceLayer
        IAMRole -. Authorizes .-> SchedulerWorker
    end

    subgraph AWS Managed Cloud Services
        subgraph Storage Tier [Amazon DynamoDB - 8 Physical Tables]
            TableUsers[("MedTrack_Users")]
            TableMeds[("MedTrack_Medicines")]
            TableLogs[("MedTrack_IntakeLogs")]
            TableAppts[("MedTrack_Appointments")]
            TableRx[("MedTrack_Prescriptions")]
            TableDiags[("MedTrack_Diagnoses")]
            TableReports[("MedTrack_Reports")]
            TableNotifs[("MedTrack_Notifications")]
        end

        subgraph Document Tier [Amazon S3]
            ReportsBucket[("Private S3 Bucket (ReportsBucket)\nSSE-AES256 Encrypted")]
        end

        subgraph Messaging Tier [Amazon SNS]
            SNSTopic[["MedTrack-Alerts Topic\n(Standard Topic Fan-Out)"]]
        end
    end

    UserBrowser -->|HTTP :80 / HTTPS :443| Nginx
    ServiceLayer -->|boto3 NoSQL Calls| StorageTier
    ServiceLayer -->|boto3 PutObject / GetObject / DeleteObject| ReportsBucket
    ServiceLayer -->|boto3 Publish| SNSTopic
    SchedulerWorker -->|boto3 Query Due Doses| StorageTier
    SchedulerWorker -->|boto3 Publish Reminders| SNSTopic
```

> [!IMPORTANT]
> **Network Isolation**: Gunicorn is bound strictly to `127.0.0.1:8000` (loopback only) and is **never** publicly exposed. All client traffic enters through Nginx on ports 80/443.

---

## 3. End-to-End Request Flow

1. **Client Request**: The user's browser sends an HTTP (port 80) or HTTPS (port 443) request to the EC2 public IP or mapped domain.
2. **Nginx Reverse Proxy**:
   - Nginx handles TLS termination (when enabled) and forwards client headers (`X-Forwarded-For`, `X-Forwarded-Proto`, `Host`).
   - Requests are reverse-proxied to Gunicorn at `http://127.0.0.1:8000`.
   - Nginx directly terminates invalid requests and prevents external access to backend internals.
3. **WSGI & Application Routing**:
   - Gunicorn passes the request to the Flask application WSGI entrypoint (`app:app`).
   - If `USE_PROXY_FIX=true` is set, `ProxyFix` middleware accurately restores client IPs and request protocols.
4. **Middleware & Security Verification**:
   - Inactivity timeout checked (60-minute limit).
   - CSRF token validated for all state-changing HTTP methods (`POST`, `PUT`, `DELETE`).
   - Role-based access control decorators (`@login_required`, `@patient_required`, `@doctor_required`) enforce authorization.
5. **Service Layer Execution**:
   - `DatabaseService` coordinates data operations against DynamoDB (or SQLite in mock mode).
   - `StorageService` manages document validation, S3 upload, and pre-signed URL generation (or local filesystem in mock mode).
   - `SNSService` dispatches notifications to the `MedTrack-Alerts` topic.
6. **Response Delivery**:
   - Flask renders server-side Jinja2 templates or returns JSON responses with hardened headers (`Content-Security-Policy`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`).

---

## 4. Application & Service Layer Design

MedTrack adheres to a clean service-oriented separation of concerns:

- **`services/database.py` (`DatabaseService`)**:
  Provides a unified database interface. When `MOCK_AWS=false`, queries are executed against Amazon DynamoDB using `boto3` with Global Secondary Indexes (GSIs), conditional writes (`ConditionExpression`), and projection expressions. When `MOCK_AWS=true`, operations run against `medtrack_local.db` via SQLite3.
- **`services/dynamodb_service.py` (`DynamoDBService`)**:
  Implements low-level DynamoDB operations including atomic deduplication, composite key queries, and GSI query optimizations across all 8 tables.
- **`services/storage_service.py` (`StorageService`)**:
  Handles document validation (magic-byte validation for PDF, PNG, JPEG), file size checks (10 MB limit), path-traversal sanitization, and dual-mode storage (private S3 with SSE-AES256 in production, `uploads/reports/` in local mode). Generates 600-second pre-signed URLs for authenticated downloads.
- **`services/sns_service.py` (`SNSService`)**:
  Encapsulates event dispatching for clinical events (appointment booked, confirmed, cancelled, diagnosis added, medicine registered, intake reminder). In production, publishes to `SNS_TOPIC_ARN`. In mock mode, formats payloads and logs to the application console.
- **`services/rate_limiter.py` (`RateLimiter`)**:
  In-memory rate limiter tracking login attempts and sensitive action frequencies to protect against brute-force attacks.

---

## 5. Amazon DynamoDB Layer (8 Physical Tables)

In AWS production, MedTrack utilizes **exactly 8 physical DynamoDB tables** with on-demand capacity (`PAY_PER_REQUEST`):

1. **`MedTrack_Users`**:
   - **PK**: `user_id` (String)
   - **GSI**: `EmailIndex` (`email` String)
   - Stores patient and doctor credentials, hashed passwords, contact info, and role.
2. **`MedTrack_Medicines`**:
   - **PK**: `medicine_id` (String)
   - **GSI**: `PatientIndex` (`patient_id` String, SK: `created_at` String)
   - Stores medication definitions, dosages, instructions, and embedded `schedule_times` (JSON list).
3. **`MedTrack_IntakeLogs`**:
   - **PK**: `log_id` (String: `<patient_id>#<medicine_id>#<date>#<time>`)
   - **GSI**: `PatientDateIndex` (`patient_id` String, SK: `log_date` String)
   - Stores persistent intake outcomes (`TAKEN`, `SKIPPED`, `MISSED`).
4. **`MedTrack_Appointments`**:
   - **PK**: `appointment_id` (String)
   - **GSIs**: `PatientIndex` (`patient_id` String, SK: `appointment_date` String), `DoctorIndex` (`doctor_id` String, SK: `appointment_date` String)
   - Manages outpatient consultation scheduling and status lifecycle.
5. **`MedTrack_Prescriptions`**:
   - **PK**: `prescription_id` (String: `rx-<uuid>`)
   - **GSIs**: `PatientIndex` (`patient_id` String, SK: `issued_date` String), `DoctorIndex` (`doctor_id` String, SK: `issued_date` String)
   - Doctor-issued medication orders linked deterministically to `MedTrack_Medicines`.
6. **`MedTrack_Diagnoses`**:
   - **PK**: `diagnosis_id` (String)
   - **GSIs**: `PatientIndex` (`patient_id` String, SK: `created_at` String), `DoctorIndex` (`doctor_id` String, SK: `created_at` String)
   - Clinical consultation notes, assessments, and treatment plans.
7. **`MedTrack_Reports`**:
   - **PK**: `report_id` (String)
   - **GSI**: `PatientIndex` (`patient_id` String, SK: `uploaded_at` String)
   - Metadata for patient medical reports; document binary stored in S3.
8. **`MedTrack_Notifications`**:
   - **PK**: `notification_id` (String)
   - **GSI**: `PatientIndex` (`patient_id` String, SK: `created_at` String)
   - System alerts and reminder notification tracking with `delivery_status` and `is_read`.
   - *Architectural Note on Lifecycle*: In current scope, historical notifications are preserved as legitimate clinical audit history. For high-volume multi-year deployments, a DynamoDB Time-To-Live (TTL) specification or archival policy may be enabled to manage long-term table growth without altering query semantics.

---

## 6. Private Amazon S3 Storage Architecture

Medical diagnostic reports and laboratory documents are secured in a dedicated Amazon S3 bucket (`ReportsBucket`):

- **Private Bucket Configuration**:
  - `BlockPublicAcls: true`
  - `BlockPublicPolicy: true`
  - `IgnorePublicAcls: true`
  - `RestrictPublicBuckets: true`
  - Server-Side Encryption: `SSEAlgorithm: AES256`
  - Deletion Policy: `Retain`
- **Key Hierarchy**: Traversal-safe deterministic structure: `reports/<patient_id>/<report_id>/<safe_filename>`.
- **Pre-signed Access**:
  - Documents are never publicly accessible.
  - Upon authenticated and authorized request, the server generates a short-lived (600-second / 10-minute) pre-signed S3 GET URL.
  - Patients can only access their own reports; doctors can only access reports for patients with an established appointment relationship.
- **Atomic Compensation**: If a database record creation fails after S3 upload, the uploaded file is purged immediately.

---

## 7. Amazon SNS Notification Architecture

Amazon SNS provides standard topic fan-out messaging for clinical alerts:

- **Topic Name**: `MedTrack-Alerts`
- **Display Name**: `MedTrack Clinical Notification Alerts`
- **Decoupled Architecture**: The SNS topic functions purely as an application notification sink. In accordance with production design, no administrative email subscription is hardcoded into IaC templates (CloudFormation / Terraform). Consumers or subscribers (SMS, email, webhooks, microservices) subscribe dynamically to the topic.
- **Dispatched Clinical Events**:
  1. Appointment Booked (patient & doctor)
  2. Appointment Confirmed (patient)
  3. Appointment Cancelled (patient)
  4. Diagnosis Recorded (patient)
  5. Medication Registered (patient)
  6. Intake Reminder (patient)

---

## 8. Autonomous Scheduler Architecture

MedTrack includes an autonomous background reminder scheduler that monitors patient medication schedules:

```
systemd timer: medtrack-scheduler.timer (Every 5 minutes)
    ↓
systemd service: medtrack-scheduler.service
    ↓
workers/medicine_scheduler.py
    ├── Evaluates current local time (APP_TIMEZONE = Asia/Kolkata)
    ├── Scans active medicines for scheduled dose windows
    ├── Verifies existing intake logs and claim leases
    ├── Emits reminder notifications via Amazon SNS (MedTrack-Alerts)
    └── Records intake outcomes or marks overdue doses as MISSED
```

- **Execution Cadence**: Triggered via `systemd` timer (`medtrack-scheduler.timer`) **every 5 minutes** (`OnUnitActiveSec=5min`).
- **Claim Lease Mechanism**: Implements a 120-second claim lease with maximum 3 retry attempts to guarantee at-least-once reminder delivery while preventing duplicate dispatches across concurrent worker runs.
- **Independent EC2 Daemon**: The scheduler runs as an autonomous EC2 background worker. It does **not** rely on external cron services, AWS EventBridge, or SQS DLQ queues.

---

## 9. Local / Mock Architecture (`MOCK_AWS=true`)

For development, testing, and offline evaluation:
- **Relational Persistence**: SQLite (`medtrack_local.db`) maintains complete entity parity with DynamoDB tables.
- **Local File Storage**: Uploaded reports are stored in `uploads/reports/<patient_id>/<report_id>/`.
- **Simulated SNS**: Notifications are rendered into structured log entries printed directly to the application console.
- **Zero AWS Footprint**: Requires zero AWS credentials, zero internet access, and incurs zero cloud costs.

---

## 10. Production AWS Architecture (`MOCK_AWS=false`)

When deployed to AWS:
- **Compute**: Single Amazon EC2 instance (e.g. `t3.micro` / `t3.small`) running Ubuntu 22.04 LTS.
- **Web Stack**: Nginx (ports 80/443) reverse-proxying to Gunicorn (3 workers, bound to `127.0.0.1:8000`).
- **Security Group**: Inbound rules restricted to port 80 (HTTP), port 443 (HTTPS), and configurable SSH CIDR (`SSHLocation`, e.g. operator IP).
- **IAM Instance Profile**: Attached to EC2 to grant temporary STS credentials automatically via IMDSv2.

---

## 11. Security Boundaries & Application Hardening

MedTrack enforces defense-in-depth across the application and infrastructure:

1. **Authentication & Password Security**:
   - Werkzeug PBKDF2/SHA-256 password hashing with unique salts.
   - Session fixation protection: sessions are cleared and regenerated upon login and logout.
   - Inactivity timeout: sessions expire after 60 minutes of inactivity.
2. **Session & Cookie Security**:
   - `SESSION_COOKIE_HTTPONLY=True` (blocks JavaScript access).
   - `SESSION_COOKIE_SAMESITE="Lax"` (mitigates cross-site request forgery).
   - `SESSION_COOKIE_SECURE=True` enabled when TLS is active.
3. **Cross-Site Request Forgery (CSRF)**:
   - Synchronizer token pattern enforced on all state-changing endpoints (`POST`, `PUT`, `DELETE`).
4. **Content Security Policy (CSP)**:
   - Strict CSP header: zero inline scripts (`script-src 'self'`), zero unsafe eval, strict frame restrictions (`frame-ancestors 'none'`).
5. **IDOR & Authorization Isolation**:
   - Horizontal privilege escalation blocked by strictly deriving patient identities from authenticated session state (`session['user_id']`), ignoring client-supplied URL or form parameters.
   - Doctor endpoints verify established patient-physician relationships before granting access to patient records, diagnoses, or reports.
6. **File Upload Hardening**:
   - Server-side validation of magic bytes for allowed types (`%PDF-`, `\x89PNG`, `\xff\xd8\xff`).
   - File size ceiling: 10 MB maximum.
   - Deterministic, sanitized file keys preventing directory traversal attacks.
7. **Rate Limiting**:
   - In-memory rate limiting on authentication routes (login, registration) to defend against brute-force attempts.

---

## 12. Direct EC2 Nginx TLS Architecture

MedTrack implements a robust, direct EC2 Nginx TLS model:

- **Initial State**: HTTP (port 80) is the default operational state upon deployment.
- **Prerequisites for TLS**:
  1. A registered domain name must point to the EC2 public IP via a DNS A record.
  2. Inbound port 80 and 443 must be reachable for ACME validation.
- **Activation Process (`medtrack-enable-tls.sh`)**:
  - The script validates domain reachability and DNS resolution.
  - Certbot obtains valid Let's Encrypt certificates.
  - Nginx configuration is updated with certificate paths, TLS 1.2/1.3 protocols, modern ciphers, and HTTP→HTTPS redirection.
  - `nginx -t` verifies configuration syntax before activating.
  - Automatic rollback restores the working HTTP configuration if certificate issuance or configuration verification fails.
- **No Falsely Claimed HTTPS**: `ApplicationURL` in CloudFormation/Terraform outputs HTTP until TLS is actively configured.
- **No Self-Signed Production Certificates**: Production requires valid CA certificates; no self-signed certificates are generated in production.
- **No ALB / ACM / Route53**: TLS is terminated directly on the EC2 Nginx instance, avoiding unnecessary AWS managed service costs and complexity.

---

## 13. AWS IAM Least-Privilege Model

Access to AWS services is governed exclusively by an **IAM Instance Role** attached to the EC2 instance:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DynamoDBTableAccess",
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:Query",
        "dynamodb:Scan"
      ],
      "Resource": [
        "arn:aws:dynamodb:*:*:table/MedTrack_*",
        "arn:aws:dynamodb:*:*:table/MedTrack_*/index/*"
      ]
    },
    {
      "Sid": "S3ReportsBucketAccess",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::medtrack-reports-*/*"
    },
    {
      "Sid": "SNSTopicPublishAccess",
      "Effect": "Allow",
      "Action": [
        "sns:Publish"
      ],
      "Resource": "arn:aws:sns:*:*:MedTrack-Alerts"
    }
  ]
}
```

- **Zero Static Credentials**: No `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` exist in the codebase, `.env`, or configuration files.
- **Strictly Scoped S3 Permissions**: Access is limited to `s3:GetObject`, `s3:PutObject`, and `s3:DeleteObject` on bucket objects. No bucket listing (`s3:ListBucket`) or destructive bucket actions (`s3:DeleteBucket`).
