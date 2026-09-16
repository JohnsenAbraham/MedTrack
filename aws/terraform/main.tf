terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project     = "MedTrack"
      Environment = var.environment
      ManagedBy   = "Terraform"
      Compliance  = "HIPAA"
    }
  }
}

variable "aws_region" {
  type        = string
  default     = "us-east-1"
  description = "AWS deployment region"
}

variable "environment" {
  type        = string
  default     = "production"
  description = "Deployment environment"
}

# -------------------------------------------------------------
# 1. DynamoDB Tables
# -------------------------------------------------------------
resource "aws_dynamodb_table" "users" {
  name         = "MedTrack_Users"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"

  attribute {
    name = "user_id"
    type = "S"
  }

  attribute {
    name = "email"
    type = "S"
  }

  global_secondary_index {
    name            = "EmailIndex"
    hash_key        = "email"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }
}

resource "aws_dynamodb_table" "appointments" {
  name         = "MedTrack_Appointments"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "appointment_id"

  attribute {
    name = "appointment_id"
    type = "S"
  }

  attribute {
    name = "patient_id"
    type = "S"
  }

  global_secondary_index {
    name            = "PatientIndex"
    hash_key        = "patient_id"
    projection_type = "ALL"
  }
}

resource "aws_dynamodb_table" "diagnoses" {
  name         = "MedTrack_Diagnoses"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "diagnosis_id"

  attribute {
    name = "diagnosis_id"
    type = "S"
  }

  attribute {
    name = "patient_id"
    type = "S"
  }

  global_secondary_index {
    name            = "PatientIndex"
    hash_key        = "patient_id"
    projection_type = "ALL"
  }
}

resource "aws_dynamodb_table" "notifications" {
  name         = "MedTrack_Notifications"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "notification_id"

  attribute {
    name = "notification_id"
    type = "S"
  }

  attribute {
    name = "patient_id"
    type = "S"
  }

  global_secondary_index {
    name            = "PatientIndex"
    hash_key        = "patient_id"
    projection_type = "ALL"
  }
}

resource "aws_dynamodb_table" "audit_logs" {
  name         = "MedTrack_AuditLogs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "log_id"

  attribute {
    name = "log_id"
    type = "S"
  }
}

# -------------------------------------------------------------
# 2. Amazon SNS & SQS Dead Letter Queue
# -------------------------------------------------------------
resource "aws_sqs_queue" "sns_dlq" {
  name                      = "MedTrack-Alerts-DLQ"
  message_retention_seconds = 1209600
}

resource "aws_sns_topic" "alerts" {
  name         = "MedTrack-Alerts"
  display_name = "MedTrack Clinical Notification Alerts"
}

# -------------------------------------------------------------
# 3. Outputs
# -------------------------------------------------------------
output "sns_topic_arn" {
  value       = aws_sns_topic.alerts.arn
  description = "SNS Topic ARN for alerts"
}

output "dynamodb_tables" {
  value = [
    aws_dynamodb_table.users.name,
    aws_dynamodb_table.appointments.name,
    aws_dynamodb_table.diagnoses.name,
    aws_dynamodb_table.notifications.name,
    aws_dynamodb_table.audit_logs.name
  ]
}
