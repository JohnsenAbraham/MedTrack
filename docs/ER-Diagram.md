# MedTrack Database Model & Entity-Relationship (ER) Diagram

## 1. Overview
MedTrack employs a dual-tier persistence layer that provides parity between local development (`MOCK_AWS=true` using SQLite in `medtrack_local.db`) and AWS cloud production (`MOCK_AWS=false` using Amazon DynamoDB).

The data model is centered around patient healthcare management, with **Medication Adherence & Intake Tracking** as the core use case, complemented by outpatient appointments, physician consultations, clinical prescriptions, diagnostic reports, and notification alerts.

There are **exactly 8 physical DynamoDB tables** in the production schema. Schedule times are embedded directly inside the `MEDICINE` entity (`schedule_times` JSON list), eliminating unnecessary join complexity. Doctors are represented as `USER` records with `role = "doctor"`. Medical report metadata is stored in the database, while report file binaries are stored in private, server-side encrypted Amazon S3 buckets.

---

## 2. Mermaid Entity-Relationship Diagram

```mermaid
erDiagram
    USER ||--o{ MEDICINE : "manages (as Patient)"
    USER ||--o{ APPOINTMENT : "books (as Patient)"
    USER ||--o{ APPOINTMENT : "attends (as Doctor)"
    USER ||--o{ PRESCRIPTION : "receives (as Patient)"
    USER ||--o{ PRESCRIPTION : "issues (as Doctor)"
    USER ||--o{ DIAGNOSIS : "receives (as Patient)"
    USER ||--o{ DIAGNOSIS : "authors (as Doctor)"
    USER ||--o{ REPORT : "owns (as Patient)"
    USER ||--o{ REPORT : "reviews (as Doctor)"
    USER ||--o{ NOTIFICATION : "receives (as Patient)"
    MEDICINE ||--o{ INTAKE_LOG : "generates intake history"
    PRESCRIPTION ||--o| MEDICINE : "provisions linked medicine"
    APPOINTMENT |o--o{ PRESCRIPTION : "may lead to"
    APPOINTMENT |o--o{ DIAGNOSIS : "results in"
    APPOINTMENT |o--o{ REPORT : "may attach"

    USER {
        string user_id PK "UUID (Partition Key)"
        string name "Full Name"
        string email UK "Unique Email (GSI: EmailIndex)"
        string password_hash "Werkzeug PBKDF2/SHA-256 Hash"
        string phone "Contact Phone Number"
        string date_of_birth "YYYY-MM-DD"
        string gender "Gender (Male, Female, Other)"
        string role "patient | doctor"
        string created_at "ISO 8601 Timestamp"
    }

    MEDICINE {
        string medicine_id PK "UUID (Partition Key)"
        string patient_id FK "References USER.user_id (GSI: PatientIndex)"
        string name "Medication Name"
        string dosage "Dosage (e.g. 500mg, 1 Tablet)"
        string frequency "e.g. Once Daily, Twice Daily"
        string schedule_times "Embedded JSON list (e.g. ['08:00', '20:00'])"
        string instructions "e.g. After Food, Before Sleep"
        string start_date "YYYY-MM-DD"
        string end_date "YYYY-MM-DD (Optional)"
        int is_active "1 = Active, 0 = Inactive"
        string created_at "ISO 8601 Timestamp"
    }

    INTAKE_LOG {
        string log_id PK "Composite: patient_id#medicine_id#date#time"
        string patient_id FK "References USER.user_id (GSI: PatientDateIndex)"
        string medicine_id FK "References MEDICINE.medicine_id"
        string scheduled_date "Dose Date (YYYY-MM-DD)"
        string scheduled_time "Dose Time (HH:MM)"
        string log_date "Log Date (YYYY-MM-DD)"
        string status "Persistent Outcome: TAKEN | SKIPPED | MISSED"
        string logged_at "ISO 8601 Timestamp"
    }

    APPOINTMENT {
        string appointment_id PK "UUID (Partition Key)"
        string patient_id FK "References USER.user_id (GSI: PatientIndex)"
        string doctor_id FK "References USER.user_id (GSI: DoctorIndex)"
        string appointment_date "Visit Date (YYYY-MM-DD)"
        string appointment_time "Visit Time Slot (e.g. 10:00 AM)"
        string reason "Chief Clinical Complaint"
        string status "PENDING | CONFIRMED | COMPLETED | CANCELLED"
        string created_at "ISO 8601 Timestamp"
    }

    PRESCRIPTION {
        string prescription_id PK "Deterministic ID: rx-<uuid>"
        string patient_id FK "References USER.user_id (GSI: PatientIndex)"
        string doctor_id FK "References USER.user_id (GSI: DoctorIndex)"
        string appointment_id FK "References APPOINTMENT.appointment_id (Optional)"
        string medicine_name "Prescribed Drug Name"
        string dosage "Dosage specification"
        string frequency "e.g. Twice Daily"
        string schedule_times "Embedded JSON list (e.g. ['09:00', '21:00'])"
        string instructions "Special instructions"
        int duration_days "Prescription duration in days"
        string status "ACTIVE | DISCONTINUED | EXPIRED | SUPERSEDED"
        string linked_medicine_id FK "Deterministic: med_rx_<prescription_id>"
        string issued_date "YYYY-MM-DD"
        string created_at "ISO 8601 Timestamp"
    }

    DIAGNOSIS {
        string diagnosis_id PK "UUID (Partition Key)"
        string patient_id FK "References USER.user_id (GSI: PatientIndex)"
        string doctor_id FK "References USER.user_id (GSI: DoctorIndex)"
        string appointment_id FK "References APPOINTMENT.appointment_id (Optional)"
        string diagnosis "Clinical Assessment & Findings"
        string date "Consultation Date (YYYY-MM-DD)"
        string created_at "ISO 8601 Timestamp"
    }

    REPORT {
        string report_id PK "UUID (Partition Key)"
        string patient_id FK "References USER.user_id (GSI: PatientIndex)"
        string doctor_id FK "References USER.user_id (Optional)"
        string appointment_id FK "References APPOINTMENT.appointment_id (Optional)"
        string title "Report Title"
        string report_type "Lab Report | Imaging | Prescription | Summary"
        string file_name "Sanitized Original Filename"
        string file_path "Storage Key: reports/<patient_id>/<report_id>/<file>"
        int file_size "Size in Bytes"
        string mime_type "application/pdf | image/png | image/jpeg"
        string uploaded_at "ISO 8601 Timestamp"
    }

    NOTIFICATION {
        string notification_id PK "UUID / Composite Key (Partition Key)"
        string patient_id FK "References USER.user_id (GSI: PatientIndex)"
        string message "Notification Text"
        string delivery_status "PENDING | CLAIMED | SENT | FAILED"
        int is_read "Read Flag: 0 = Unread, 1 = Read"
        string read_at "ISO 8601 Timestamp (Optional)"
        string created_at "ISO 8601 Timestamp"
    }
```

---

## 3. Physical DynamoDB Tables & Global Secondary Indexes (GSIs)

In AWS production, MedTrack provisions **exactly 8 physical DynamoDB tables** with on-demand capacity (`PAY_PER_REQUEST`).

| Table Name | Partition Key (PK) | Sort Key (SK) | Global Secondary Indexes (GSIs) | Purpose |
|---|---|---|---|---|
| **MedTrack_Users** | `user_id` (S) | *None* | `EmailIndex` (`email` S) | Stores patient and physician credentials, contact info, and roles. |
| **MedTrack_Medicines** | `medicine_id` (S) | *None* | `PatientIndex` (`patient_id` S, SK: `created_at` S) | Medication definitions with embedded `schedule_times`. |
| **MedTrack_IntakeLogs** | `log_id` (S) | *None* | `PatientDateIndex` (`patient_id` S, SK: `log_date` S) | Persistent dose outcomes (`TAKEN`, `SKIPPED`, `MISSED`). |
| **MedTrack_Appointments** | `appointment_id` (S) | *None* | `PatientIndex` (`patient_id` S, SK: `appointment_date` S)<br>`DoctorIndex` (`doctor_id` S, SK: `appointment_date` S) | Consultation scheduling and booking lifecycle. |
| **MedTrack_Prescriptions** | `prescription_id` (S) | *None* | `PatientIndex` (`patient_id` S, SK: `issued_date` S)<br>`DoctorIndex` (`doctor_id` S, SK: `issued_date` S) | Physician-issued medication orders linked to active medicines. |
| **MedTrack_Diagnoses** | `diagnosis_id` (S) | *None* | `PatientIndex` (`patient_id` S, SK: `created_at` S)<br>`DoctorIndex` (`doctor_id` S, SK: `created_at` S) | Post-consultation clinical diagnoses and treatment records. |
| **MedTrack_Reports** | `report_id` (S) | *None* | `PatientIndex` (`patient_id` S, SK: `uploaded_at` S) | Diagnostic report metadata (binary file stored in private S3). |
| **MedTrack_Notifications** | `notification_id` (S) | *None* | `PatientIndex` (`patient_id` S, SK: `created_at` S) | System & reminder notifications with delivery lifecycle tracking. |

---

## 4. Key Architectural Data-Model Principles

### 4.1 Embedded Schedules (No Separate Schedule Table)
- Daily intake schedules are stored directly within each `MEDICINE` record as an embedded JSON list in `schedule_times` (e.g., `["08:00", "14:00", "20:00"]`).
- This eliminates join overhead, allows atomic updates, and simplifies time-of-day calculations.
- There is **no physical `Schedule` table** or `ReminderLedger` table.

### 4.2 Runtime States vs. Persistent Outcomes
- **Runtime Virtual States**: `UPCOMING` and `DUE` are dynamically calculated at request time based on the current local time (`APP_TIMEZONE = Asia/Kolkata`), the medication's embedded `schedule_times`, and existing intake logs for that day.
- **Persistent Outcomes**: When a patient acts on a dose or the scheduler records a missed dose, an `INTAKE_LOG` record is written with an immutable outcome:
  - `TAKEN`: Patient confirmed taking the dose.
  - `SKIPPED`: Patient chose to skip the dose.
  - `MISSED`: Scheduled dose window elapsed without patient confirmation.
- `PENDING` is strictly a delivery state for notifications, never a persistent intake state.

### 4.3 Physician Representation (Users with Role = doctor)
- Physicians are stored in `MedTrack_Users` with `role = "doctor"`.
- There is no separate `Doctors` table.
- Doctor isolation is enforced through application authorization: doctors can only view patient records, diagnoses, and reports where an established consultation or appointment exists.

### 4.4 Separation of Medical Report Metadata and Binaries
- The `MedTrack_Reports` table stores report metadata: `title`, `report_type`, `file_name`, `file_path`, `file_size`, and `mime_type`.
- The actual document binary is stored in a private Amazon S3 bucket (`ReportsBucket`) encrypted with AWS SSE-AES256.
- In local development mode (`MOCK_AWS=true`), files are stored locally in `uploads/reports/`.
- Access is gated through authenticated endpoints that generate short-lived (600-second) pre-signed S3 download URLs.
