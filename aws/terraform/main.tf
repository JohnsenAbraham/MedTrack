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

variable "instance_type" {
  type        = string
  default     = "t3.small"
  description = "EC2 instance type for application hosting"
}

variable "key_name" {
  type        = string
  default     = "medtrack-key"
  description = "Existing EC2 KeyPair for SSH access"
}

variable "domain_name" {
  type        = string
  default     = ""
  description = "Optional fully qualified domain name (FQDN). Prerequisite for automated TLS via Certbot."
}

variable "ssh_cidr" {
  type        = string
  default     = "0.0.0.0/0"
  description = "Allowed CIDR block for SSH administrative access. Default is 0.0.0.0/0; restrict to authorized administrative CIDR in production for least privilege."
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

resource "aws_dynamodb_table" "medicines" {
  name         = "MedTrack_Medicines"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "medicine_id"

  attribute {
    name = "medicine_id"
    type = "S"
  }

  attribute {
    name = "patient_id"
    type = "S"
  }

  attribute {
    name = "created_at"
    type = "S"
  }

  global_secondary_index {
    name            = "PatientIndex"
    hash_key        = "patient_id"
    range_key       = "created_at"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }
}

resource "aws_dynamodb_table" "intake_logs" {
  name         = "MedTrack_IntakeLogs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "log_id"

  attribute {
    name = "log_id"
    type = "S"
  }

  attribute {
    name = "patient_id"
    type = "S"
  }

  attribute {
    name = "log_date"
    type = "S"
  }

  global_secondary_index {
    name            = "PatientDateIndex"
    hash_key        = "patient_id"
    range_key       = "log_date"
    projection_type = "ALL"
  }

  point_in_time_recovery {
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

  attribute {
    name = "doctor_id"
    type = "S"
  }

  attribute {
    name = "appointment_date"
    type = "S"
  }

  global_secondary_index {
    name            = "PatientIndex"
    hash_key        = "patient_id"
    range_key       = "appointment_date"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "DoctorIndex"
    hash_key        = "doctor_id"
    range_key       = "appointment_date"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }
}

resource "aws_dynamodb_table" "prescriptions" {
  name         = "MedTrack_Prescriptions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "prescription_id"

  attribute {
    name = "prescription_id"
    type = "S"
  }

  attribute {
    name = "patient_id"
    type = "S"
  }

  attribute {
    name = "doctor_id"
    type = "S"
  }

  attribute {
    name = "issued_date"
    type = "S"
  }

  global_secondary_index {
    name            = "PatientIndex"
    hash_key        = "patient_id"
    range_key       = "issued_date"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "DoctorIndex"
    hash_key        = "doctor_id"
    range_key       = "issued_date"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
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

  attribute {
    name = "doctor_id"
    type = "S"
  }

  attribute {
    name = "created_at"
    type = "S"
  }

  global_secondary_index {
    name            = "PatientIndex"
    hash_key        = "patient_id"
    range_key       = "created_at"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "DoctorIndex"
    hash_key        = "doctor_id"
    range_key       = "created_at"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }
}

resource "aws_dynamodb_table" "reports" {
  name         = "MedTrack_Reports"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "report_id"

  attribute {
    name = "report_id"
    type = "S"
  }

  attribute {
    name = "patient_id"
    type = "S"
  }

  attribute {
    name = "uploaded_at"
    type = "S"
  }

  global_secondary_index {
    name            = "PatientIndex"
    hash_key        = "patient_id"
    range_key       = "uploaded_at"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
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

  attribute {
    name = "created_at"
    type = "S"
  }

  global_secondary_index {
    name            = "PatientIndex"
    hash_key        = "patient_id"
    range_key       = "created_at"
    projection_type = "ALL"
  }
}

# -------------------------------------------------------------
# 2. Amazon SNS (Fan-Out Only)
# -------------------------------------------------------------
resource "aws_sns_topic" "alerts" {
  name         = "MedTrack-Alerts"
  display_name = "MedTrack Clinical Notification Alerts"
}

# -------------------------------------------------------------
# 3. Virtual Private Cloud (VPC) & Networking
# -------------------------------------------------------------
resource "aws_vpc" "medtrack_vpc" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = "MedTrack-${var.environment}-VPC"
  }
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.medtrack_vpc.id

  tags = {
    Name = "MedTrack-${var.environment}-IGW"
  }
}

resource "aws_subnet" "public_1" {
  vpc_id                  = aws_vpc.medtrack_vpc.id
  cidr_block              = "10.0.1.0/24"
  map_public_ip_on_launch = true

  tags = {
    Name = "MedTrack-${var.environment}-Public-1"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.medtrack_vpc.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }

  tags = {
    Name = "MedTrack-${var.environment}-Public-RT"
  }
}

resource "aws_route_table_association" "public_1" {
  subnet_id      = aws_subnet.public_1.id
  route_table_id = aws_route_table.public.id
}

# -------------------------------------------------------------
# 4. Security Group (Ports 80, 443, 22)
# -------------------------------------------------------------
resource "aws_security_group" "medtrack_sg" {
  name        = "MedTrack-${var.environment}-SG"
  description = "Security group for MedTrack Flask/Gunicorn application server"
  vpc_id      = aws_vpc.medtrack_vpc.id

  ingress {
    description = "HTTP public traffic"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS SSL termination"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "SSH Admin access (restricted by administrative CIDR)"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_cidr]
  }

  egress {
    description = "Outbound traffic to AWS APIs and Internet"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "MedTrack-${var.environment}-SG"
  }
}

# -------------------------------------------------------------
# 5. Amazon S3 Private Medical Reports Bucket (Phase 8 Parity)
# -------------------------------------------------------------
resource "aws_s3_bucket" "reports" {
  bucket_prefix = "medtrack-${lower(var.environment)}-reports-"

  tags = {
    Name = "MedTrack-${var.environment}-Reports"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "reports" {
  bucket = aws_s3_bucket.reports.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# -------------------------------------------------------------
# 6. IAM Least-Privilege Role & Instance Profile
# -------------------------------------------------------------
data "aws_caller_identity" "current" {}

resource "aws_iam_role" "app_role" {
  name = "MedTrack-${var.environment}-AppRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "cloud_operations" {
  name = "MedTrackCloudOperationsPolicy"
  role = aws_iam_role.app_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:DescribeTable",
          "dynamodb:TransactWriteItems"
        ]
        Resource = [
          "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/MedTrack_*",
          "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/MedTrack_*/index/*"
        ]
      },
      {
        Sid    = "SNSPublishAccess"
        Effect = "Allow"
        Action = [
          "sns:Publish",
          "sns:GetTopicAttributes"
        ]
        Resource = aws_sns_topic.alerts.arn
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/ec2/medtrack-*"
      },
      {
        Sid    = "S3ReportsAccess"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ]
        Resource = "${aws_s3_bucket.reports.arn}/*"
      }
    ]
  })
}

resource "aws_iam_instance_profile" "app_profile" {
  name = "MedTrack-${var.environment}-InstanceProfile"
  role = aws_iam_role.app_role.name
}

# -------------------------------------------------------------
# 7. EC2 Production Compute Host
# -------------------------------------------------------------
data "aws_ami" "ubuntu" {
  most_recent = true
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
  owners = ["099720109477"] # Canonical
}

resource "aws_instance" "server" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  key_name               = var.key_name
  subnet_id              = aws_subnet.public_1.id
  vpc_security_group_ids = [aws_security_group.medtrack_sg.id]
  iam_instance_profile   = aws_iam_instance_profile.app_profile.name

  user_data = <<-EOF
    #!/bin/bash
    set -e
    apt update && apt upgrade -y
    apt install -y python3-pip python3-venv nginx git curl certbot python3-certbot-nginx

    # Deploy Application
    mkdir -p /var/www/medtrack
    git clone https://github.com/JohnsenAbraham/MedTrack.git /var/www/medtrack
    cd /var/www/medtrack
    python3 -m venv venv
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install -r requirements.txt gunicorn

    # Set up Environment
    cat <<EOT > /var/www/medtrack/.env
    MOCK_AWS=false
    AWS_REGION=${var.aws_region}
    DYNAMODB_USERS_TABLE=MedTrack_Users
    DYNAMODB_MEDICINES_TABLE=MedTrack_Medicines
    DYNAMODB_INTAKE_LOGS_TABLE=MedTrack_IntakeLogs
    DYNAMODB_APPOINTMENTS_TABLE=MedTrack_Appointments
    DYNAMODB_PRESCRIPTIONS_TABLE=MedTrack_Prescriptions
    DYNAMODB_DIAGNOSES_TABLE=MedTrack_Diagnoses
    DYNAMODB_REPORTS_TABLE=MedTrack_Reports
    DYNAMODB_NOTIFICATIONS_TABLE=MedTrack_Notifications
    SNS_TOPIC_ARN=${aws_sns_topic.alerts.arn}
    S3_REPORTS_BUCKET=${aws_s3_bucket.reports.bucket}
    SECRET_KEY=$$(openssl rand -hex 32)
    SESSION_COOKIE_SECURE=false
    USE_PROXY_FIX=false
    DOMAIN_NAME=${var.domain_name}
    APP_TIMEZONE=Asia/Kolkata
    EOT

    # Systemd Service Daemon (Gunicorn bound strictly to localhost:8000)
    cat <<EOT > /etc/systemd/system/medtrack.service
    [Unit]
    Description=MedTrack Healthcare Gunicorn Service
    After=network.target

    [Service]
    User=www-data
    Group=www-data
    WorkingDirectory=/var/www/medtrack
    Environment="PATH=/var/www/medtrack/venv/bin"
    ExecStart=/var/www/medtrack/venv/bin/gunicorn --workers 3 --bind 127.0.0.1:8000 app:app --access-logfile - --error-logfile -

    [Install]
    WantedBy=multi-user.target
    EOT

    chown -R www-data:www-data /var/www/medtrack
    systemctl daemon-reload
    systemctl enable --now medtrack

    # Configure Autonomous Medicine Reminder Scheduler systemd units
    if [ -f /var/www/medtrack/systemd/medtrack-scheduler.service ]; then
        cp /var/www/medtrack/systemd/medtrack-scheduler.* /etc/systemd/system/
        systemctl daemon-reload
        systemctl enable --now medtrack-scheduler.timer
    fi

    # Nginx Reverse Proxy Config (Initial HTTP mode proxying to Gunicorn localhost)
    cat <<EOT > /etc/nginx/sites-available/medtrack
    server {
        listen 80;
        server_name _;

        location / {
            proxy_pass http://127.0.0.1:8000;
            proxy_set_header Host \$host;
            proxy_set_header X-Real-IP \$remote_addr;
            proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
    }
    EOT

    ln -sf /etc/nginx/sites-available/medtrack /etc/nginx/sites-enabled/
    rm -f /etc/nginx/sites-enabled/default
    systemctl restart nginx

    # Provision TLS Activation Script (activates HTTPS & 301 redirect once valid domain/cert prerequisites are met)
    cat <<'EOF' > /usr/local/bin/medtrack-enable-tls.sh
    #!/bin/bash
    set -e

    TARGET_DOMAIN="$${1}"
    if [ -z "$$TARGET_DOMAIN" ]; then
        TARGET_DOMAIN=$$(grep '^DOMAIN_NAME=' /var/www/medtrack/.env 2>/dev/null | cut -d'=' -f2- || true)
    fi

    # (a) Domain argument validation
    if [ -z "$$TARGET_DOMAIN" ]; then
        echo "ERROR: No domain supplied. Usage: $$0 <domain-name> (or configure DOMAIN_NAME in /var/www/medtrack/.env)" >&2
        exit 1
    fi

    echo "Beginning TLS enablement for domain: $$TARGET_DOMAIN"

    # (b) DNS prerequisite validation
    EC2_IP=$$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4 || true)
    DOMAIN_IP=$$(getent ahosts "$$TARGET_DOMAIN" | awk '{print $$1}' | head -n 1 || true)

    if [ -n "$$EC2_IP" ] && [ -n "$$DOMAIN_IP" ] && [ "$$EC2_IP" != "$$DOMAIN_IP" ]; then
        echo "ERROR: DNS prerequisite not met. $$TARGET_DOMAIN resolves to $$DOMAIN_IP, but this instance is $$EC2_IP." >&2
        echo "Ensure DNS A-record propagates to $$EC2_IP before running TLS activation." >&2
        exit 1
    fi

    # (c) Certificate acquisition via Certbot (non-interactive)
    echo "Attempting Let's Encrypt certificate acquisition for $$TARGET_DOMAIN..."
    if ! certbot certonly --nginx -d "$$TARGET_DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email; then
        echo "ERROR: Certbot certificate issuance failed. Preserving existing working HTTP configuration." >&2
        exit 1
    fi

    CERT_FILE="/etc/letsencrypt/live/$$TARGET_DOMAIN/fullchain.pem"
    KEY_FILE="/etc/letsencrypt/live/$$TARGET_DOMAIN/privkey.pem"

    # (d) Certificate-file existence validation
    if [ ! -f "$$CERT_FILE" ] || [ ! -f "$$KEY_FILE" ]; then
        echo "ERROR: Certificate files not found at $$CERT_FILE and $$KEY_FILE. Preserving HTTP configuration." >&2
        exit 1
    fi

    # (e) Generate candidate HTTPS Nginx configuration in a temporary file
    TMP_CONF="/etc/nginx/sites-available/medtrack.tls.tmp"
    cat <<NGINX_EOF > "$$TMP_CONF"
    server {
        listen 80;
        server_name $$TARGET_DOMAIN _;
        return 301 https://\$$host\$$request_uri;
    }

    server {
        listen 443 ssl;
        server_name $$TARGET_DOMAIN;

        ssl_certificate $$CERT_FILE;
        ssl_certificate_key $$KEY_FILE;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384';
        ssl_prefer_server_ciphers on;
        ssl_session_cache shared:SSL:10m;
        ssl_session_timeout 1d;
        ssl_session_tickets off;

        location / {
            proxy_pass http://127.0.0.1:8000;
            proxy_set_header Host \$$host;
            proxy_set_header X-Real-IP \$$remote_addr;
            proxy_set_header X-Forwarded-For \$$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;
        }
    }
    NGINX_EOF

    # (f) nginx -t validation BEFORE replacing active configuration
    cp /etc/nginx/sites-available/medtrack /etc/nginx/sites-available/medtrack.http.bak
    cp "$$TMP_CONF" /etc/nginx/sites-available/medtrack
    rm -f "$$TMP_CONF"

    if ! nginx -t; then
        echo "ERROR: Nginx syntax validation failed for HTTPS configuration. Rolling back to HTTP." >&2
        cp /etc/nginx/sites-available/medtrack.http.bak /etc/nginx/sites-available/medtrack
        nginx -t && systemctl reload nginx || true
        exit 1
    fi

    # (g, h, i, j, k) Activate secure settings upon verified valid configuration
    sed -i 's/^SESSION_COOKIE_SECURE=.*/SESSION_COOKIE_SECURE=true/' /var/www/medtrack/.env
    sed -i 's/^USE_PROXY_FIX=.*/USE_PROXY_FIX=true/' /var/www/medtrack/.env

    systemctl restart medtrack
    systemctl reload nginx
    echo "SUCCESS: MedTrack TLS successfully activated for $$TARGET_DOMAIN."
    EOF
    chmod +x /usr/local/bin/medtrack-enable-tls.sh

    # Conditional TLS activation at stack launch if DomainName is configured and resolves to host
    if [ -n "${var.domain_name}" ]; then
        EC2_IP=$$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4 || true)
        DOMAIN_IP=$$(getent ahosts "${var.domain_name}" | awk '{print $$1}' | head -n 1 || true)
        if [ "$$EC2_IP" = "$$DOMAIN_IP" ] && [ -n "$$DOMAIN_IP" ]; then
            /usr/local/bin/medtrack-enable-tls.sh "${var.domain_name}" || true
        else
            echo "DNS prerequisite not yet met for ${var.domain_name}. Running in HTTP mode."
        fi
    fi
  EOF

  tags = {
    Name = "MedTrack-${var.environment}-Server"
  }
}

# -------------------------------------------------------------
# 8. Outputs
# -------------------------------------------------------------
output "instance_public_ip" {
  value       = aws_instance.server.public_ip
  description = "Public IP address of the deployed MedTrack server"
}

output "application_url" {
  value       = "http://${aws_instance.server.public_ip}"
  description = "HTTP URL to access MedTrack server (HTTP until TLS is activated via domain and certificate)"
}

output "configured_domain_name" {
  value       = var.domain_name
  description = "Configured domain name for future HTTPS activation"
}

output "sns_topic_arn" {
  value       = aws_sns_topic.alerts.arn
  description = "SNS Topic ARN for alerts"
}

output "reports_bucket_name" {
  value       = aws_s3_bucket.reports.bucket
  description = "Private Amazon S3 Bucket for Medical Diagnostic Reports"
}

output "dynamodb_tables" {
  value = [
    aws_dynamodb_table.users.name,
    aws_dynamodb_table.medicines.name,
    aws_dynamodb_table.intake_logs.name,
    aws_dynamodb_table.appointments.name,
    aws_dynamodb_table.prescriptions.name,
    aws_dynamodb_table.diagnoses.name,
    aws_dynamodb_table.reports.name,
    aws_dynamodb_table.notifications.name
  ]
  description = "Names of the 8 core DynamoDB tables"
}
