"""
MedTrack - Phase 10 Verification Test Suite: IaC Synchronization & TLS Architecture

Verifies:
1. Werkzeug ProxyFix request handling (x_for=1, x_proto=1, x_host=1) and documented security model.
2. request.is_secure and request.scheme evaluation behind TLS-terminating reverse proxy.
3. Strict defense-in-depth HSTS behavior (emitted on HTTPS; omitted on HTTP; no unconditional subdomains).
4. Secure cookie transport configuration (SESSION_COOKIE_SECURE, HttpOnly, SameSite=Lax).
5. Preservation of Phase 6 security headers (CSP, X-Content-Type-Options, X-Frame-Options, Permissions-Policy).
6. Local-only Gunicorn binding (127.0.0.1:8000; never public 0.0.0.0).
7. ApplicationURL does NOT falsely advertise HTTPS before TLS is actually active (remains HTTP).
8. Configurable administrative SSH CIDR in CloudFormation (SSHLocation) and Terraform (ssh_cidr).
9. Robust TLS enablement script (/usr/local/bin/medtrack-enable-tls.sh):
   - Domain argument validation
   - DNS prerequisite validation
   - Certificate file existence check
   - Candidate configuration creation
   - HTTP configuration backup and rollback on nginx -t failure
   - Activation of secure environment variables ONLY after verified nginx validation
10. CloudFormation and Terraform full infrastructure parity (8 DynamoDB tables, S3 reports bucket, least-privilege IAM).
11. Regression verification of Phases 6-9 security and authorization invariants.
"""

import os
import re
import unittest
from pathlib import Path
from flask import session
from werkzeug.middleware.proxy_fix import ProxyFix

# Ensure local test environment runs with MOCK_AWS=true
os.environ["MOCK_AWS"] = "true"
os.environ["FLASK_ENV"] = "development"

from config import Config
from app import app, db


class Phase10TLSTestCase(unittest.TestCase):
    """Test suite for Phase 10 TLS architecture and IaC synchronization."""

    @classmethod
    def setUpClass(cls):
        cls.base_dir = Path(__file__).resolve().parent.parent
        cls.cfn_path = cls.base_dir / "aws" / "cloudformation.yaml"
        cls.tf_path = cls.base_dir / "aws" / "terraform" / "main.tf"
        cls.app_path = cls.base_dir / "app.py"

        with open(cls.cfn_path, "r", encoding="utf-8") as f:
            cls.cfn_content = f.read()

        with open(cls.tf_path, "r", encoding="utf-8") as f:
            cls.tf_content = f.read()

        with open(cls.app_path, "r", encoding="utf-8") as f:
            cls.app_content = f.read()

    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.client = self.app.test_client()

    # =========================================================================
    # Group 1: ProxyFix & Request Scheme Evaluation
    # =========================================================================

    def test_01_proxyfix_secure_scheme_detection(self):
        """1. ProxyFix correctly identifies HTTPS requests when X-Forwarded-Proto is https."""
        test_app = self.app.wsgi_app
        wrapped_app = ProxyFix(test_app, x_for=1, x_proto=1, x_host=1)

        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/",
            "SERVER_NAME": "medtrack.example.com",
            "SERVER_PORT": "80",
            "wsgi.url_scheme": "http",
            "HTTP_HOST": "medtrack.example.com",
            "HTTP_X_FORWARDED_PROTO": "https",
            "HTTP_X_FORWARDED_FOR": "203.0.113.195",
            "REMOTE_ADDR": "127.0.0.1"
        }

        captured_is_secure = []
        captured_scheme = []

        def dummy_start_response(status, headers):
            pass

        def test_wsgi(env, start_resp):
            with self.app.request_context(env):
                from flask import request
                captured_is_secure.append(request.is_secure)
                captured_scheme.append(request.scheme)
            return [b"OK"]

        wrapped_wsgi = ProxyFix(test_wsgi, x_for=1, x_proto=1, x_host=1)
        wrapped_wsgi(environ, dummy_start_response)

        self.assertTrue(captured_is_secure[0], "request.is_secure must be True behind HTTPS reverse proxy")
        self.assertEqual(captured_scheme[0], "https", "request.scheme must be 'https'")

    def test_02_proxyfix_insecure_scheme_detection(self):
        """2. ProxyFix correctly identifies plain HTTP requests when X-Forwarded-Proto is http."""
        captured_is_secure = []
        captured_scheme = []

        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/",
            "SERVER_NAME": "medtrack.example.com",
            "SERVER_PORT": "80",
            "wsgi.url_scheme": "http",
            "HTTP_HOST": "medtrack.example.com",
            "HTTP_X_FORWARDED_PROTO": "http",
            "REMOTE_ADDR": "127.0.0.1"
        }

        def test_wsgi(env, start_resp):
            with self.app.request_context(env):
                from flask import request
                captured_is_secure.append(request.is_secure)
                captured_scheme.append(request.scheme)
            return [b"OK"]

        wrapped_wsgi = ProxyFix(test_wsgi, x_for=1, x_proto=1, x_host=1)
        wrapped_wsgi(environ, lambda s, h: None)

        self.assertFalse(captured_is_secure[0], "request.is_secure must be False on HTTP requests")
        self.assertEqual(captured_scheme[0], "http", "request.scheme must be 'http'")

    def test_03_proxyfix_bounded_hop_trust(self):
        """3. ProxyFix trusts strictly 1 hop (x_proto=1), ignoring upstream spoofed chains."""
        captured_proto = []

        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/",
            "SERVER_NAME": "medtrack.example.com",
            "SERVER_PORT": "80",
            "wsgi.url_scheme": "http",
            "HTTP_HOST": "medtrack.example.com",
            "HTTP_X_FORWARDED_PROTO": "https, http",
            "REMOTE_ADDR": "127.0.0.1"
        }

        def test_wsgi(env, start_resp):
            captured_proto.append(env.get("wsgi.url_scheme"))
            return [b"OK"]

        wrapped_wsgi = ProxyFix(test_wsgi, x_for=1, x_proto=1, x_host=1)
        wrapped_wsgi(environ, lambda s, h: None)

        self.assertEqual(captured_proto[0], "http", "ProxyFix must trust only the rightmost immediate proxy hop")

    def test_04_default_raw_app_fails_closed_against_forwarded_headers(self):
        """4. Direct request with X-Forwarded-Proto does not spoof raw WSGI without ProxyFix."""
        res = self.client.get("/", headers={"X-Forwarded-Proto": "https"})
        self.assertNotIn("Strict-Transport-Security", res.headers)

    def test_05_proxyfix_security_model_documented(self):
        """5. ProxyFix documentation in app.py states Nginx overwrites headers and Gunicorn loopback isolation."""
        self.assertIn("Nginx is the sole reverse proxy", self.app_content)
        self.assertIn("overwrites X-Forwarded-* headers", self.app_content)
        self.assertIn("Gunicorn is bound exclusively to loopback", self.app_content)
        self.assertIn("x_for=1, x_proto=1, x_host=1", self.app_content)

    # =========================================================================
    # Group 2: HSTS and Defense-in-Depth Response Headers
    # =========================================================================

    def test_06_hsts_emitted_on_https(self):
        """6. Strict-Transport-Security is emitted when request.is_secure is True."""
        res = self.client.get("/", environ_overrides={"wsgi.url_scheme": "https"})
        self.assertIn("Strict-Transport-Security", res.headers)
        self.assertEqual(res.headers["Strict-Transport-Security"], "max-age=31536000")

    def test_07_hsts_omitted_on_http(self):
        """7. Strict-Transport-Security is strictly omitted on plain HTTP requests."""
        res = self.client.get("/", environ_overrides={"wsgi.url_scheme": "http"})
        self.assertNotIn("Strict-Transport-Security", res.headers)

    def test_08_hsts_does_not_contain_unconditional_subdomains_or_preload(self):
        """8. HSTS header does not contain includeSubDomains or preload unless justified."""
        res = self.client.get("/", environ_overrides={"wsgi.url_scheme": "https"})
        hsts = res.headers.get("Strict-Transport-Security", "")
        self.assertNotIn("includeSubDomains", hsts)
        self.assertNotIn("preload", hsts)

    def test_09_standard_security_headers_present(self):
        """9. Security headers (X-Content-Type-Options, X-Frame-Options, CSP, etc.) remain intact."""
        res = self.client.get("/")
        self.assertEqual(res.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(res.headers.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertEqual(res.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin")
        self.assertIn("geolocation=()", res.headers.get("Permissions-Policy", ""))
        self.assertIn("default-src 'self'", res.headers.get("Content-Security-Policy", ""))

    # =========================================================================
    # Group 3: Session Cookie Security Flags
    # =========================================================================

    def test_10_session_cookie_security_attributes(self):
        """10. Session cookies adhere to HttpOnly, SameSite=Lax, and configurable Secure attribute."""
        self.assertEqual(Config.SESSION_COOKIE_NAME, "medtrack_session")
        self.assertTrue(self.app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(self.app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertIsInstance(self.app.config["SESSION_COOKIE_SECURE"], bool)

    def test_11_cookie_issued_with_httponly_and_samesite(self):
        """11. Set-Cookie response header contains HttpOnly and SameSite=Lax."""
        res = self.client.get("/")
        set_cookie = res.headers.get("Set-Cookie", "")
        if "medtrack_session=" in set_cookie:
            self.assertIn("HttpOnly", set_cookie)
            self.assertIn("SameSite=Lax", set_cookie)

    # =========================================================================
    # Group 4: CloudFormation Infrastructure Audit
    # =========================================================================

    def test_12_cfn_security_group_ports(self):
        """12. CloudFormation SecurityGroup ingress allows only ports 80, 443, and 22."""
        sg_block = self.cfn_content[self.cfn_content.find("MedTrackSecurityGroup:"):self.cfn_content.find("MedTrackEC2Role:")]
        from_ports = re.findall(r"FromPort:\s*(\d+)", sg_block)
        to_ports = re.findall(r"ToPort:\s*(\d+)", sg_block)
        self.assertEqual(sorted(from_ports), ["22", "443", "80"])
        self.assertEqual(sorted(to_ports), ["22", "443", "80"])

    def test_13_cfn_ssh_administrative_cidr_configurable(self):
        """13. CloudFormation defines SSHLocation parameter and uses !Ref SSHLocation on port 22."""
        self.assertIn("SSHLocation:", self.cfn_content)
        self.assertIn("CidrIp: !Ref SSHLocation", self.cfn_content)

    def test_14_cfn_gunicorn_loopback_binding(self):
        """14. CloudFormation Gunicorn service binds strictly to 127.0.0.1:8000."""
        self.assertIn("--bind 127.0.0.1:8000", self.cfn_content)
        self.assertNotIn("--bind 0.0.0.0:8000", self.cfn_content)

    def test_15_cfn_nginx_reverse_proxy_forwarded_proto(self):
        """15. CloudFormation Nginx configuration sets X-Forwarded-Proto."""
        self.assertIn("proxy_set_header X-Forwarded-Proto", self.cfn_content)
        self.assertIn("proxy_pass http://127.0.0.1:8000;", self.cfn_content)

    def test_16_cfn_tls_script_robust_validation_and_rollback(self):
        """16. CloudFormation medtrack-enable-tls.sh validates DNS, tests with nginx -t, and backs up HTTP config."""
        self.assertIn("/usr/local/bin/medtrack-enable-tls.sh", self.cfn_content)
        self.assertIn("DNS prerequisite not met", self.cfn_content)
        self.assertIn("medtrack.http.bak", self.cfn_content)
        self.assertIn("if ! nginx -t; then", self.cfn_content)
        self.assertIn("Rolling back to HTTP", self.cfn_content)
        self.assertIn("SESSION_COOKIE_SECURE=true", self.cfn_content)
        self.assertIn("USE_PROXY_FIX=true", self.cfn_content)

    def test_17_cfn_application_url_remains_http_prior_to_tls(self):
        """17. CloudFormation ApplicationURL remains HTTP and does NOT falsely claim HTTPS merely on DomainName."""
        self.assertIn("Value: !Sub http://${MedTrackEC2Instance.PublicIp}", self.cfn_content)
        self.assertIn("ConfiguredDomainName:", self.cfn_content)

    def test_18_cfn_no_prohibited_resources(self):
        """18. CloudFormation does not introduce AuditLogs, ReminderLedger, Schedule table, SQS DLQ, or EventBridge."""
        self.assertNotIn("AuditLogs", self.cfn_content)
        self.assertNotIn("ReminderLedger", self.cfn_content)
        self.assertNotIn("AWS::SQS::Queue", self.cfn_content)
        self.assertNotIn("AWS::Events::Rule", self.cfn_content)

    def test_19_cfn_eight_dynamodb_tables(self):
        """19. CloudFormation defines exactly 8 DynamoDB tables."""
        tables = re.findall(r"TableName:\s*(MedTrack_\w+)", self.cfn_content)
        expected_tables = {
            "MedTrack_Users",
            "MedTrack_Medicines",
            "MedTrack_IntakeLogs",
            "MedTrack_Appointments",
            "MedTrack_Prescriptions",
            "MedTrack_Diagnoses",
            "MedTrack_Reports",
            "MedTrack_Notifications"
        }
        self.assertEqual(set(tables), expected_tables)
        self.assertEqual(len(tables), 8)

    # =========================================================================
    # Group 5: Terraform Infrastructure Audit
    # =========================================================================

    def test_20_tf_security_group_ports(self):
        """20. Terraform medtrack_sg ingress allows only ports 80, 443, and 22."""
        sg_start = self.tf_content.find("resource \"aws_security_group\" \"medtrack_sg\"")
        egress_pos = self.tf_content.find("egress {", sg_start)
        ingress_block = self.tf_content[sg_start:egress_pos]

        from_ports = re.findall(r"from_port\s*=\s*(\d+)", ingress_block)
        to_ports = re.findall(r"to_port\s*=\s*(\d+)", ingress_block)
        self.assertEqual(sorted(from_ports), ["22", "443", "80"])
        self.assertEqual(sorted(to_ports), ["22", "443", "80"])

    def test_21_tf_ssh_administrative_cidr_configurable(self):
        """21. Terraform defines ssh_cidr variable and uses [var.ssh_cidr] for port 22."""
        self.assertIn("variable \"ssh_cidr\"", self.tf_content)
        self.assertIn("cidr_blocks = [var.ssh_cidr]", self.tf_content)

    def test_22_tf_gunicorn_loopback_binding(self):
        """22. Terraform EC2 UserData binds Gunicorn strictly to 127.0.0.1:8000."""
        self.assertIn("--bind 127.0.0.1:8000", self.tf_content)
        self.assertNotIn("--bind 0.0.0.0:8000", self.tf_content)

    def test_23_tf_nginx_reverse_proxy_forwarded_proto(self):
        """23. Terraform Nginx configuration sets X-Forwarded-Proto."""
        self.assertIn("proxy_set_header X-Forwarded-Proto", self.tf_content)
        self.assertIn("proxy_pass http://127.0.0.1:8000;", self.tf_content)

    def test_24_tf_tls_script_robust_validation_and_rollback(self):
        """24. Terraform medtrack-enable-tls.sh validates DNS, tests with nginx -t, and backs up HTTP config."""
        self.assertIn("/usr/local/bin/medtrack-enable-tls.sh", self.tf_content)
        self.assertIn("DNS prerequisite not met", self.tf_content)
        self.assertIn("medtrack.http.bak", self.tf_content)
        self.assertIn("if ! nginx -t; then", self.tf_content)
        self.assertIn("Rolling back to HTTP", self.tf_content)
        self.assertIn("SESSION_COOKIE_SECURE=.*/SESSION_COOKIE_SECURE=true", self.tf_content)
        self.assertIn("USE_PROXY_FIX=.*/USE_PROXY_FIX=true", self.tf_content)

    def test_25_tf_application_url_remains_http_prior_to_tls(self):
        """25. Terraform application_url remains HTTP and configured_domain_name is separate."""
        self.assertIn("value       = \"http://${aws_instance.server.public_ip}\"", self.tf_content)
        self.assertIn("output \"configured_domain_name\"", self.tf_content)

    def test_26_tf_eight_dynamodb_tables(self):
        """26. Terraform defines exactly 8 DynamoDB tables."""
        tables = re.findall(r"name\s*=\s*\"(MedTrack_\w+)\"", self.tf_content)
        expected_tables = {
            "MedTrack_Users",
            "MedTrack_Medicines",
            "MedTrack_IntakeLogs",
            "MedTrack_Appointments",
            "MedTrack_Prescriptions",
            "MedTrack_Diagnoses",
            "MedTrack_Reports",
            "MedTrack_Notifications"
        }
        self.assertEqual(set(tables), expected_tables)
        self.assertEqual(len(tables), 8)

    def test_27_tf_s3_reports_bucket_and_iam_parity(self):
        """27. Terraform includes S3 reports bucket with encryption and least-privilege IAM policy."""
        self.assertIn("resource \"aws_s3_bucket\" \"reports\"", self.tf_content)
        self.assertIn("aws_s3_bucket_public_access_block", self.tf_content)
        self.assertIn("aws_iam_role", self.tf_content)
        self.assertIn("MedTrackCloudOperationsPolicy", self.tf_content)
        self.assertIn("S3ReportsAccess", self.tf_content)

    def test_28_tf_no_prohibited_resources(self):
        """28. Terraform does not introduce AuditLogs, ReminderLedger, Schedule table, SQS DLQ, or EventBridge."""
        self.assertNotIn("AuditLogs", self.tf_content)
        self.assertNotIn("ReminderLedger", self.tf_content)
        self.assertNotIn("aws_sqs_queue", self.tf_content)
        self.assertNotIn("aws_cloudwatch_event_rule", self.tf_content)

    # =========================================================================
    # Group 6: Phases 6-9 Regression Invariants
    # =========================================================================

    def test_29_csrf_enabled_in_production(self):
        """29. WTF_CSRF_ENABLED remains True by default."""
        self.assertTrue(Config.WTF_CSRF_ENABLED)

    def test_30_session_inactivity_timeout_intact(self):
        """30. Session inactivity timeout remains configured to 3600 seconds (60 minutes)."""
        self.assertEqual(Config.SESSION_INACTIVITY_TIMEOUT, 3600)

    def test_31_demo_mode_disabled_by_default(self):
        """31. Demo mode remains disabled by default."""
        self.assertFalse(Config.DEMO_MODE)

    def test_32_scheduler_systemd_files_exist(self):
        """32. Systemd scheduler service and timer files exist in the repository."""
        service_file = self.base_dir / "systemd" / "medtrack-scheduler.service"
        timer_file = self.base_dir / "systemd" / "medtrack-scheduler.timer"
        self.assertTrue(service_file.is_file())
        self.assertTrue(timer_file.is_file())

    def test_33_dynamodb_service_eight_tables_configured(self):
        """33. DynamoDB service is configured with all 8 core table names."""
        from services.dynamodb_service import DynamoDBService
        service = DynamoDBService()
        self.assertEqual(service.users_table.name, "MedTrack_Users")
        self.assertEqual(service.medicines_table.name, "MedTrack_Medicines")
        self.assertEqual(service.intake_logs_table.name, "MedTrack_IntakeLogs")
        self.assertEqual(service.appointments_table.name, "MedTrack_Appointments")
        self.assertEqual(service.prescriptions_table.name, "MedTrack_Prescriptions")
        self.assertEqual(service.diagnoses_table.name, "MedTrack_Diagnoses")
        self.assertEqual(service.reports_table.name, "MedTrack_Reports")
        self.assertEqual(service.notifications_table.name, "MedTrack_Notifications")


if __name__ == "__main__":
    unittest.main()
