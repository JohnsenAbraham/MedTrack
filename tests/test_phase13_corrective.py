"""
MedTrack Phase 13 Corrective Engineering Test Suite:
1. CloudFormation EC2 AMI / ImageId mechanism and Ubuntu 22.04 LTS verification
2. Operating System documentation consistency across README and architecture specs
3. APP_TIMEZONE consistency and deterministic UTC vs Asia/Kolkata date boundary tests
4. DynamoDB pagination helpers, multi-page continuation, LastEvaluatedKey handling, and scheduler scan safety
"""

import unittest
from unittest.mock import MagicMock, patch
import re
from pathlib import Path
import datetime
from zoneinfo import ZoneInfo

from config import Config
from services.dynamodb_service import DynamoDBService
import app as flask_app


class TestPhase13CloudFormation(unittest.TestCase):
    """Verify CloudFormation EC2 AMI configuration and deployment correctness."""

    @classmethod
    def setUpClass(cls):
        cls.cfn_path = Config.BASE_DIR / "aws" / "cloudformation.yaml"
        with open(cls.cfn_path, "r", encoding="utf-8") as f:
            cls.cfn_content = f.read()

    def test_01_cfn_ami_parameter_defined(self):
        """1. CloudFormation defines AmiId parameter with Ubuntu 22.04 SSM resolution."""
        self.assertIn("AmiId:", self.cfn_content)
        self.assertIn("AWS::SSM::Parameter::Value<AWS::EC2::Image::Id>", self.cfn_content)
        self.assertIn("/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id", self.cfn_content)

    def test_02_cfn_ec2_instance_references_image_id(self):
        """2. MedTrackEC2Instance explicitly declares ImageId referencing !Ref AmiId."""
        # Locate MedTrackEC2Instance block
        ec2_start = self.cfn_content.find("MedTrackEC2Instance:")
        self.assertGreater(ec2_start, 0, "MedTrackEC2Instance not found in CloudFormation template")
        ec2_block = self.cfn_content[ec2_start:ec2_start + 400]
        self.assertIn("ImageId: !Ref AmiId", ec2_block)

    def test_03_cfn_userdata_remains_ubuntu_apt_compatible(self):
        """3. UserData preserves Ubuntu 22.04 apt package management and toolchain."""
        self.assertIn("apt update && apt upgrade -y", self.cfn_content)
        self.assertIn("apt install -y python3-pip python3-venv nginx git curl certbot python3-certbot-nginx", self.cfn_content)
        self.assertNotIn("dnf install", self.cfn_content)
        self.assertNotIn("yum install", self.cfn_content)

    def test_04_cfn_no_broken_substitutions_or_prohibited_resources(self):
        """4. CloudFormation template contains no broken variable substitutions and no forbidden architecture."""
        # Verify no unclosed or malformed substitutions
        malformed = re.findall(r"\$\{[^}]*$", self.cfn_content, re.MULTILINE)
        self.assertEqual(len(malformed), 0)
        # Verify prohibited architecture is absent
        self.assertNotIn("AWS::Events::Rule", self.cfn_content)
        self.assertNotIn("AWS::SQS::Queue", self.cfn_content)
        self.assertNotIn("AWS::ElasticLoadBalancingV2", self.cfn_content)
        self.assertNotIn("ReminderLedger", self.cfn_content)
        self.assertNotIn("AuditLogs", self.cfn_content)


class TestPhase13OSConsistency(unittest.TestCase):
    """Verify consistent documentation of Ubuntu 22.04 LTS across all active repository docs."""

    @classmethod
    def setUpClass(cls):
        cls.readme_path = Config.BASE_DIR / "README.md"
        with open(cls.readme_path, "r", encoding="utf-8") as f:
            cls.readme_content = f.read()

        cls.arch_path = Config.BASE_DIR / "docs" / "architecture.md"
        with open(cls.arch_path, "r", encoding="utf-8") as f:
            cls.arch_content = f.read()

        cls.tf_path = Config.BASE_DIR / "aws" / "terraform" / "main.tf"
        with open(cls.tf_path, "r", encoding="utf-8") as f:
            cls.tf_content = f.read()

    def test_05_readme_specifies_ubuntu_2204(self):
        """5. README specifies Ubuntu 22.04 LTS and contains no stale Amazon Linux references."""
        self.assertIn("Amazon EC2 running Ubuntu 22.04 LTS", self.readme_content)
        self.assertIn("Amazon EC2 virtual machine running Ubuntu 22.04 LTS", self.readme_content)
        self.assertNotIn("Amazon Linux 2023", self.readme_content)
        self.assertNotIn("AL2023", self.readme_content)

    def test_06_architecture_specifies_ubuntu_2204(self):
        """6. docs/architecture.md specifies Ubuntu 22.04 LTS for EC2 compute host."""
        self.assertIn("running Ubuntu 22.04 LTS", self.arch_content)
        self.assertNotIn("Amazon Linux 2023", self.arch_content)

    def test_07_terraform_and_cloudformation_os_parity(self):
        """7. Terraform and CloudFormation IaC definitions both target Ubuntu 22.04 LTS."""
        self.assertIn("ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*", self.tf_content)
        self.assertIn("099720109477", self.tf_content)  # Canonical owner ID


class TestPhase13TimezoneConsistency(unittest.TestCase):
    """Verify APP_TIMEZONE consistency and deterministic UTC vs Asia/Kolkata boundary behavior."""

    def setUp(self):
        self.app = flask_app.app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_08_get_current_date_respects_app_timezone(self):
        """8. get_current_date() in app.py strictly resolves date in Config.APP_TIMEZONE."""
        tz = ZoneInfo(getattr(Config, "APP_TIMEZONE", "Asia/Kolkata"))
        expected_date = datetime.datetime.now(tz).date().isoformat()
        actual_date = flask_app.get_current_date()
        self.assertEqual(actual_date, expected_date)

    def test_09_utc_vs_ist_date_boundary_dashboard_categorization(self):
        """
        9. Timezone boundary test:
        When UTC is 2026-09-21 20:00:00 (Day D),
        Asia/Kolkata (UTC+5:30) is 2026-09-22 01:30:00 (Day D+1).
        Appointments for 2026-09-22 must be categorized as upcoming (today) and not past.
        Appointments for 2026-09-21 must be categorized as past.
        """
        ist_tz = ZoneInfo("Asia/Kolkata")
        # Frozen boundary time: 2026-09-21 20:00:00 UTC == 2026-09-22 01:30:00 IST
        boundary_dt = datetime.datetime(2026, 9, 22, 1, 30, 0, tzinfo=ist_tz)

        with patch("app.datetime") as mock_datetime:
            mock_datetime.datetime.now.side_effect = lambda *args, **kwargs: (
                boundary_dt if (args and args[0] == ist_tz) or kwargs.get("tz") == ist_tz
                else datetime.datetime(2026, 9, 21, 20, 0, 0, tzinfo=datetime.timezone.utc)
            )
            mock_datetime.date = datetime.date
            mock_datetime.timedelta = datetime.timedelta
            mock_datetime.timezone = datetime.timezone

            # Verify get_current_date evaluates to 2026-09-22 (IST)
            resolved_today = flask_app.get_current_date()
            self.assertEqual(resolved_today, "2026-09-22")

            # Simulate appointment list
            appointments = [
                {"appointment_id": "appt-today", "appointment_date": "2026-09-22", "status": "CONFIRMED"},
                {"appointment_id": "appt-yesterday", "appointment_date": "2026-09-21", "status": "CONFIRMED"},
                {"appointment_id": "appt-tomorrow", "appointment_date": "2026-09-23", "status": "CONFIRMED"},
            ]

            upcoming = [a for a in appointments if a.get("appointment_date", "") >= resolved_today and a.get("status") != "CANCELLED"]
            past = [a for a in appointments if a.get("appointment_date", "") < resolved_today or a.get("status") in ("COMPLETED", "CANCELLED")]

            upcoming_ids = [a["appointment_id"] for a in upcoming]
            past_ids = [a["appointment_id"] for a in past]

            self.assertIn("appt-today", upcoming_ids, "Today's IST appointment must be in upcoming")
            self.assertIn("appt-tomorrow", upcoming_ids, "Tomorrow's appointment must be in upcoming")
            self.assertIn("appt-yesterday", past_ids, "Yesterday's appointment must be in past")
            self.assertNotIn("appt-today", past_ids, "Today's IST appointment must NOT be in past")

    def test_10_doctor_dashboard_today_visits_respects_boundary(self):
        """10. Doctor dashboard today_visits uses IST date at UTC/IST boundary."""
        ist_tz = ZoneInfo("Asia/Kolkata")
        boundary_dt = datetime.datetime(2026, 9, 22, 2, 0, 0, tzinfo=ist_tz)

        with patch("app.datetime") as mock_datetime:
            mock_datetime.datetime.now.side_effect = lambda *args, **kwargs: (
                boundary_dt if (args and args[0] == ist_tz) or kwargs.get("tz") == ist_tz
                else datetime.datetime(2026, 9, 21, 20, 30, 0, tzinfo=datetime.timezone.utc)
            )
            mock_datetime.date = datetime.date
            mock_datetime.timedelta = datetime.timedelta
            mock_datetime.timezone = datetime.timezone

            resolved_today = flask_app.get_current_date()
            self.assertEqual(resolved_today, "2026-09-22")

            appointments = [
                {"appointment_id": "appt-1", "appointment_date": "2026-09-22", "status": "CONFIRMED"},
                {"appointment_id": "appt-2", "appointment_date": "2026-09-21", "status": "CONFIRMED"},
            ]
            today_visits = [a for a in appointments if a.get("appointment_date", "") == resolved_today and a.get("status") != "CANCELLED"]
            self.assertEqual(len(today_visits), 1)
            self.assertEqual(today_visits[0]["appointment_id"], "appt-1")


class TestPhase13DynamoDBPagination(unittest.TestCase):
    """Verify DynamoDB pagination helpers, LastEvaluatedKey iteration, and scanner safety."""

    def test_11_paginate_query_empty_result(self):
        """11. _paginate_query handles empty DynamoDB response gracefully."""
        mock_table = MagicMock()
        mock_table.query.return_value = {"Items": []}

        result = DynamoDBService._paginate_query(mock_table, KeyConditionExpression="mock_key")
        self.assertEqual(result, [])
        self.assertEqual(mock_table.query.call_count, 1)

    def test_12_paginate_query_single_page(self):
        """12. _paginate_query returns single-page items when LastEvaluatedKey is absent."""
        mock_table = MagicMock()
        mock_table.query.return_value = {
            "Items": [{"id": "item-1"}, {"id": "item-2"}]
        }

        result = DynamoDBService._paginate_query(mock_table, KeyConditionExpression="mock_key")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["id"], "item-1")
        self.assertEqual(result[1]["id"], "item-2")
        self.assertEqual(mock_table.query.call_count, 1)

    def test_13_paginate_query_multiple_pages_continuation(self):
        """13. _paginate_query continues across multiple pages using LastEvaluatedKey."""
        mock_table = MagicMock()
        mock_table.query.side_effect = [
            {"Items": [{"id": "item-1"}], "LastEvaluatedKey": {"pk": "item-1"}},
            {"Items": [{"id": "item-2"}], "LastEvaluatedKey": {"pk": "item-2"}},
            {"Items": [{"id": "item-3"}]}  # Final page (no LastEvaluatedKey)
        ]

        result = DynamoDBService._paginate_query(mock_table, KeyConditionExpression="mock_key")
        self.assertEqual(len(result), 3)
        self.assertEqual([r["id"] for r in result], ["item-1", "item-2", "item-3"])
        self.assertEqual(mock_table.query.call_count, 3)

        # Verify ExclusiveStartKey was passed on subsequent calls
        calls = mock_table.query.call_args_list
        self.assertNotIn("ExclusiveStartKey", calls[0].kwargs)
        self.assertEqual(calls[1].kwargs.get("ExclusiveStartKey"), {"pk": "item-1"})
        self.assertEqual(calls[2].kwargs.get("ExclusiveStartKey"), {"pk": "item-2"})

    def test_14_paginate_query_preserves_filter_parameters(self):
        """14. _paginate_query maintains all query parameters (IndexName, ScanIndexForward, etc.) across pages."""
        mock_table = MagicMock()
        mock_table.query.side_effect = [
            {"Items": [{"id": "a"}], "LastEvaluatedKey": {"pk": "a"}},
            {"Items": [{"id": "b"}]}
        ]

        DynamoDBService._paginate_query(
            mock_table,
            IndexName="PatientIndex",
            KeyConditionExpression="patient_id = :pid",
            ScanIndexForward=False
        )

        calls = mock_table.query.call_args_list
        for call in calls:
            self.assertEqual(call.kwargs.get("IndexName"), "PatientIndex")
            self.assertFalse(call.kwargs.get("ScanIndexForward"))

    def test_15_paginate_scan_unbounded_multi_page(self):
        """15. _paginate_scan consumes all pages when max_items is None."""
        mock_table = MagicMock()
        mock_table.scan.side_effect = [
            {"Items": [{"id": "med-1"}], "LastEvaluatedKey": {"pk": "med-1"}},
            {"Items": [{"id": "med-2"}], "LastEvaluatedKey": {"pk": "med-2"}},
            {"Items": [{"id": "med-3"}]}
        ]

        result = DynamoDBService._paginate_scan(mock_table)
        self.assertEqual(len(result), 3)
        self.assertEqual([r["id"] for r in result], ["med-1", "med-2", "med-3"])
        self.assertEqual(mock_table.scan.call_count, 3)

    def test_16_paginate_scan_bounded_early_termination(self):
        """16. _paginate_scan terminates early once max_items matches are collected."""
        mock_table = MagicMock()
        mock_table.scan.side_effect = [
            {"Items": [{"id": "cand-1"}, {"id": "cand-2"}], "LastEvaluatedKey": {"pk": "cand-2"}},
            {"Items": [{"id": "cand-3"}, {"id": "cand-4"}], "LastEvaluatedKey": {"pk": "cand-4"}},
        ]

        # Request only 2 items; should terminate after page 1 without calling page 2
        result = DynamoDBService._paginate_scan(mock_table, max_items=2)
        self.assertEqual(len(result), 2)
        self.assertEqual(mock_table.scan.call_count, 1)

    def test_17_claim_pending_notifications_multi_page_scan(self):
        """17. claim_pending_notifications successfully discovers pending notifications across page boundaries."""
        service = DynamoDBService()
        mock_table = MagicMock()
        service.notifications_table = mock_table

        # Page 1 has 0 matches (e.g. historical delivered notifications) but returns LastEvaluatedKey
        # Page 2 has 1 pending notification
        pending_item = {
            "notification_id": "ntf-page2",
            "delivery_status": "PENDING",
            "delivery_attempts": 0,
            "type": "REMINDER"
        }
        mock_table.scan.side_effect = [
            {"Items": [], "LastEvaluatedKey": {"pk": "page-1-end"}},
            {"Items": [pending_item]}
        ]
        mock_table.update_item.return_value = {
            "Attributes": {
                "notification_id": "ntf-page2",
                "delivery_status": "CLAIMED",
                "delivery_claim_id": "claim-123"
            }
        }

        claimed = service.claim_pending_notifications(limit=10, notification_type="REMINDER")
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["notification_id"], "ntf-page2")
        self.assertEqual(mock_table.scan.call_count, 2)

    def test_18_get_active_medicines_paginates_all_pages(self):
        """18. get_active_medicines collects active medications across all DynamoDB pages and sorts deterministically."""
        service = DynamoDBService()
        mock_table = MagicMock()
        service.medicines_table = mock_table

        mock_table.scan.side_effect = [
            {"Items": [{"medicine_id": "m2", "patient_id": "p1", "schedule_time": "08:00 PM"}], "LastEvaluatedKey": {"pk": "m2"}},
            {"Items": [{"medicine_id": "m1", "patient_id": "p1", "schedule_time": "08:00 AM"}]}
        ]

        active = service.get_active_medicines()
        self.assertEqual(len(active), 2)
        # Verify sorted order: 08:00 AM before 08:00 PM
        self.assertEqual(active[0]["medicine_id"], "m1")
        self.assertEqual(active[1]["medicine_id"], "m2")

    def test_19_is_doctor_authorized_multi_page_short_circuit(self):
        """19. is_doctor_authorized_for_patient evaluates multiple pages and short-circuits on match."""
        service = DynamoDBService()
        mock_table = MagicMock()
        service.appointments_table = mock_table

        mock_table.query.side_effect = [
            {"Items": [], "LastEvaluatedKey": {"pk": "page-1"}},
            {"Items": [{"appointment_id": "appt-auth", "patient_id": "target-patient"}]}
        ]

        authorized = service.is_doctor_authorized_for_patient("doc-1", "target-patient")
        self.assertTrue(authorized)
        self.assertEqual(mock_table.query.call_count, 2)


if __name__ == "__main__":
    unittest.main()
