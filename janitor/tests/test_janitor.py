"""
Unit tests for Cost Janitor scanners using Moto (AWS mock library).

Run with:  pytest janitor/tests/ -v
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import pytest

# Make janitor importable from tests/
sys.path.insert(0, str(Path(__file__).parent.parent))

# Moto must be imported before boto3 clients are created
from moto import mock_aws

import janitor as jan
from constants import EBS_COST_PER_GB_MONTH, EIP_UNASSOCIATED_COST_PER_MONTH

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def aws_credentials(monkeypatch):
    """Prevent any real AWS calls during tests."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


def make_ec2_client():
    return boto3.client("ec2", region_name="us-east-1")


# ---------------------------------------------------------------------------
# EBS unattached volume tests
# ---------------------------------------------------------------------------

@mock_aws
def test_scan_unattached_ebs_finds_orphan():
    ec2 = make_ec2_client()
    # Create an unattached volume
    vol = ec2.create_volume(
        AvailabilityZone="us-east-1a",
        Size=20,
        VolumeType="gp3",
        TagSpecifications=[{
            "ResourceType": "volume",
            "Tags": [
                {"Key": "Project", "Value": "test"},
                {"Key": "Environment", "Value": "staging"},
                {"Key": "Owner", "Value": "team"},
            ],
        }],
    )
    findings = jan.scan_unattached_ebs(ec2)
    assert len(findings) == 1
    assert findings[0]["resource_id"] == vol["VolumeId"]
    assert findings[0]["resource_type"] == "ebs_volume"
    assert "unattached" in findings[0]["reason"]


@mock_aws
def test_scan_unattached_ebs_cost_calculation():
    ec2 = make_ec2_client()
    ec2.create_volume(AvailabilityZone="us-east-1a", Size=100, VolumeType="gp3")
    findings = jan.scan_unattached_ebs(ec2)
    expected_cost = 100 * EBS_COST_PER_GB_MONTH["gp3"]
    assert findings[0]["estimated_monthly_cost_usd"] == pytest.approx(expected_cost, rel=0.01)


@mock_aws
def test_scan_attached_ebs_not_flagged():
    """Volumes attached to an instance should not appear as orphans."""
    ec2 = make_ec2_client()
    # Need a running instance to attach to
    # Moto requires a valid AMI — use a known moto AMI
    images = ec2.describe_images(Owners=["amazon"])
    ami_id = images["Images"][0]["ImageId"] if images["Images"] else "ami-12345678"

    reservation = ec2.run_instances(
        ImageId=ami_id,
        MinCount=1,
        MaxCount=1,
        InstanceType="t3.micro",
    )
    inst_id = reservation["Instances"][0]["InstanceId"]

    vol = ec2.create_volume(AvailabilityZone="us-east-1a", Size=10, VolumeType="gp3")
    ec2.attach_volume(VolumeId=vol["VolumeId"], InstanceId=inst_id, Device="/dev/sdf")

    findings = jan.scan_unattached_ebs(ec2)
    flagged_ids = [f["resource_id"] for f in findings]
    assert vol["VolumeId"] not in flagged_ids


# ---------------------------------------------------------------------------
# Unassociated EIP tests
# ---------------------------------------------------------------------------

@mock_aws
def test_scan_unassociated_eip_finds_orphan():
    ec2 = make_ec2_client()
    alloc = ec2.allocate_address(Domain="vpc")
    findings = jan.scan_unassociated_eips(ec2)
    assert len(findings) == 1
    assert findings[0]["resource_id"] == alloc["AllocationId"]
    assert findings[0]["resource_type"] == "elastic_ip"
    assert findings[0]["estimated_monthly_cost_usd"] == pytest.approx(
        EIP_UNASSOCIATED_COST_PER_MONTH, rel=0.01
    )


@mock_aws
def test_scan_associated_eip_not_flagged():
    ec2 = make_ec2_client()
    images = ec2.describe_images(Owners=["amazon"])
    ami_id = images["Images"][0]["ImageId"] if images["Images"] else "ami-12345678"

    reservation = ec2.run_instances(
        ImageId=ami_id, MinCount=1, MaxCount=1, InstanceType="t3.micro"
    )
    inst_id = reservation["Instances"][0]["InstanceId"]

    alloc = ec2.allocate_address(Domain="vpc")
    ec2.associate_address(InstanceId=inst_id, AllocationId=alloc["AllocationId"])

    findings = jan.scan_unassociated_eips(ec2)
    flagged_ids = [f["resource_id"] for f in findings]
    assert alloc["AllocationId"] not in flagged_ids


# ---------------------------------------------------------------------------
# Missing tag tests
# ---------------------------------------------------------------------------

@mock_aws
def test_untagged_resource_flagged():
    ec2 = make_ec2_client()
    # Create a volume with no required tags
    ec2.create_volume(AvailabilityZone="us-east-1a", Size=10, VolumeType="gp3")
    findings = jan.scan_unattached_ebs(ec2)
    assert findings[0]["safe_to_auto_delete"] is False
    assert any("missing_tags" in f["reason"] for f in findings)


# ---------------------------------------------------------------------------
# Protected tag tests
# ---------------------------------------------------------------------------

@mock_aws
def test_protected_volume_not_safe_to_auto_delete():
    ec2 = make_ec2_client()
    ec2.create_volume(
        AvailabilityZone="us-east-1a",
        Size=10,
        VolumeType="gp3",
        TagSpecifications=[{
            "ResourceType": "volume",
            "Tags": [
                {"Key": "Project", "Value": "p"},
                {"Key": "Environment", "Value": "e"},
                {"Key": "Owner", "Value": "o"},
                {"Key": "Protected", "Value": "true"},
            ],
        }],
    )
    findings = jan.scan_unattached_ebs(ec2)
    assert findings[0]["safe_to_auto_delete"] is False


# ---------------------------------------------------------------------------
# Report schema tests
# ---------------------------------------------------------------------------

@mock_aws
def test_report_schema_fields():
    ec2 = make_ec2_client()
    ec2.create_volume(AvailabilityZone="us-east-1a", Size=5, VolumeType="gp3")
    findings = jan.scan_unattached_ebs(ec2)
    report = jan.build_report(findings, "000000000000", "us-east-1")

    assert "scan_timestamp" in report
    assert "account_id" in report
    assert "region" in report
    assert "summary" in report
    assert "total_orphans" in report["summary"]
    assert "estimated_monthly_waste_usd" in report["summary"]
    assert "findings" in report

    f = report["findings"][0]
    for field in ["resource_id", "resource_type", "reason", "age_days",
                  "estimated_monthly_cost_usd", "tags", "suggested_action",
                  "safe_to_auto_delete"]:
        assert field in f, f"Missing required field: {field}"


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------

@mock_aws
def test_markdown_generated_with_findings():
    ec2 = make_ec2_client()
    ec2.create_volume(AvailabilityZone="us-east-1a", Size=10)
    findings = jan.scan_unattached_ebs(ec2)
    report = jan.build_report(findings, "000000000000", "us-east-1")
    md = jan.build_markdown(report)
    assert "Cost Janitor Report" in md
    assert "Findings" in md


@mock_aws
def test_markdown_clean_when_no_findings():
    ec2 = make_ec2_client()
    report = jan.build_report([], "000000000000", "us-east-1")
    md = jan.build_markdown(report)
    assert "No orphaned resources" in md


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------

def test_tags_to_dict():
    raw = [{"Key": "Project", "Value": "nimbuskart"}, {"Key": "Env", "Value": "staging"}]
    result = jan.tags_to_dict(raw)
    assert result == {"Project": "nimbuskart", "Env": "staging"}


def test_tags_to_dict_empty():
    assert jan.tags_to_dict(None) == {}
    assert jan.tags_to_dict([]) == {}


def test_missing_tags_detection():
    # Use set comparison — order of returned list is not guaranteed
    assert set(jan.missing_tags({})) == {"Project", "Environment", "Owner"}
    assert jan.missing_tags({"Project": "p", "Environment": "e", "Owner": "o"}) == []
    missing = jan.missing_tags({"Project": "p"})
    assert "Environment" in missing
    assert "Owner" in missing


def test_is_protected():
    assert jan.is_protected({"Protected": "true"}) is True
    assert jan.is_protected({"Protected": "True"}) is True
    assert jan.is_protected({"Protected": "false"}) is False
    assert jan.is_protected({}) is False
