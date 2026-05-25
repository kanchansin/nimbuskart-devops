
"""
Cost Janitor — NimbusKart cloud waste detector.

Scans an AWS account (or LocalStack environment) for orphaned / wasteful
resources and produces a machine-readable report.json plus a human-readable
Markdown summary.

Usage:
    python janitor.py [--dry-run | --delete] [--region REGION]
                      [--endpoint-url URL] [--stopped-days N]
                      [--output-dir DIR]

Flags:
    --dry-run       (default) Detect and report orphans; do NOT delete anything.
                    Exits with code 1 if any orphans are found (CI-friendly).
    --delete        Detect AND delete safe orphans. Resources tagged
                    Protected=true are always skipped.
    --region        AWS region to scan (default: us-east-1).
    --endpoint-url  Override endpoint — used to point at LocalStack.
    --stopped-days  Flag EC2 instances stopped longer than N days (default: 14).
    --output-dir    Directory to write report.json and report.md (default: .).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from constants import (
    EBS_COST_PER_GB_MONTH,
    EBS_DEFAULT_COST_PER_GB_MONTH,
    EBS_DEFAULT_SIZE_GB,
    EIP_UNASSOCIATED_COST_PER_MONTH,
    EC2_STOPPED_THRESHOLD_DAYS_DEFAULT,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("janitor")

REQUIRED_TAGS = {"Project", "Environment", "Owner"}
PROTECTED_TAG_KEY = "Protected"
PROTECTED_TAG_VALUE = "true"






def tags_to_dict(tag_list: list[dict] | None) -> dict[str, str]:
    """Convert AWS tag list [{'Key': k, 'Value': v}] -> plain dict."""
    if not tag_list:
        return {}
    return {t["Key"]: t["Value"] for t in tag_list}


def is_protected(tags: dict[str, str]) -> bool:
    return tags.get(PROTECTED_TAG_KEY, "").lower() == PROTECTED_TAG_VALUE


def missing_tags(tags: dict[str, str]) -> list[str]:
    return [t for t in REQUIRED_TAGS if not tags.get(t)]


def days_since(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (now - dt).days)


def build_finding(
    resource_id: str,
    resource_type: str,
    reason: str,
    age_days: int | None,
    estimated_monthly_cost_usd: float,
    tags: dict[str, str],
    suggested_action: str,
    safe_to_auto_delete: bool,
) -> dict[str, Any]:
    """Construct a finding dict matching the required report schema."""
    tag_snapshot = {k: tags.get(k) for k in REQUIRED_TAGS}
    return {
        "resource_id": resource_id,
        "resource_type": resource_type,
        "reason": reason,
        "age_days": age_days,
        "estimated_monthly_cost_usd": round(estimated_monthly_cost_usd, 2),
        "tags": tag_snapshot,
        "suggested_action": suggested_action,
        "safe_to_auto_delete": safe_to_auto_delete,
    }






def scan_unattached_ebs(ec2_client) -> list[dict[str, Any]]:
    """Detect EBS volumes in 'available' state (not attached to any instance)."""
    findings = []
    log.info("Scanning EBS volumes for unattached volumes...")

    paginator = ec2_client.get_paginator("describe_volumes")
    for page in paginator.paginate(Filters=[{"Name": "status", "Values": ["available"]}]):
        for vol in page["Volumes"]:
            vol_id = vol["VolumeId"]
            tags = tags_to_dict(vol.get("Tags"))
            size_gb = vol.get("Size", EBS_DEFAULT_SIZE_GB)
            vol_type = vol.get("VolumeType", "gp3")
            cost_per_gb = EBS_COST_PER_GB_MONTH.get(vol_type, EBS_DEFAULT_COST_PER_GB_MONTH)
            monthly_cost = size_gb * cost_per_gb
            age = days_since(vol.get("CreateTime"))

            reasons = ["unattached"]
            missing = missing_tags(tags)
            if missing:
                reasons.append(f"missing_tags:{','.join(missing)}")

            findings.append(
                build_finding(
                    resource_id=vol_id,
                    resource_type="ebs_volume",
                    reason="; ".join(reasons),
                    age_days=age,
                    estimated_monthly_cost_usd=monthly_cost,
                    tags=tags,
                    suggested_action="delete",
                    
                    
                    safe_to_auto_delete=not is_protected(tags) and not missing,
                )
            )
            log.info("  Found unattached EBS volume: %s (%d GB, ~$%.2f/mo)", vol_id, size_gb, monthly_cost)

    return findings


def scan_stopped_ec2(ec2_client, stopped_days_threshold: int) -> list[dict[str, Any]]:
    """Detect EC2 instances that have been stopped for longer than the threshold."""
    findings = []
    log.info("Scanning EC2 instances stopped for > %d days...", stopped_days_threshold)

    paginator = ec2_client.get_paginator("describe_instances")
    for page in paginator.paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["stopped"]}]
    ):
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                inst_id = inst["InstanceId"]
                tags = tags_to_dict(inst.get("Tags"))

                
                
                state_reason = inst.get("StateTransitionReason", "")
                stopped_at = _parse_state_transition_time(state_reason)
                age = days_since(stopped_at)

                if age is None or age < stopped_days_threshold:
                    continue

                reasons = [f"stopped_for_{age}_days"]
                missing = missing_tags(tags)
                if missing:
                    reasons.append(f"missing_tags:{','.join(missing)}")

                findings.append(
                    build_finding(
                        resource_id=inst_id,
                        resource_type="ec2_instance",
                        reason="; ".join(reasons),
                        age_days=age,
                        estimated_monthly_cost_usd=0.0,  
                        tags=tags,
                        suggested_action="review_then_terminate",
                        
                        safe_to_auto_delete=False,
                    )
                )
                log.info("  Found long-stopped EC2: %s (stopped ~%d days ago)", inst_id, age)

    return findings


def _parse_state_transition_time(reason: str) -> datetime | None:
    """Extract datetime from StateTransitionReason string."""
    import re
    match = re.search(r"\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) GMT\)", reason)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    return None


def scan_unassociated_eips(ec2_client) -> list[dict[str, Any]]:
    """Detect Elastic IPs not associated with any instance or network interface."""
    findings = []
    log.info("Scanning Elastic IPs for unassociated addresses...")

    try:
        response = ec2_client.describe_addresses()
    except ClientError as e:
        log.warning("Could not describe Elastic IPs: %s", e)
        return findings

    for addr in response.get("Addresses", []):
        
        if addr.get("AssociationId"):
            continue

        allocation_id = addr.get("AllocationId", addr.get("PublicIp", "unknown"))
        public_ip = addr.get("PublicIp", "unknown")
        tags = tags_to_dict(addr.get("Tags"))

        reasons = ["unassociated"]
        missing = missing_tags(tags)
        if missing:
            reasons.append(f"missing_tags:{','.join(missing)}")

        findings.append(
            build_finding(
                resource_id=allocation_id,
                resource_type="elastic_ip",
                reason="; ".join(reasons),
                age_days=None,  
                estimated_monthly_cost_usd=EIP_UNASSOCIATED_COST_PER_MONTH,
                tags=tags,
                suggested_action="release",
                safe_to_auto_delete=not is_protected(tags) and not missing,
            )
        )
        log.info("  Found unassociated EIP: %s (%s)", allocation_id, public_ip)

    return findings


def scan_untagged_resources(ec2_client) -> list[dict[str, Any]]:
    """
    Detect resources missing required tags that weren't already caught
    by the other scanners (running instances, attached volumes, etc.).

    This scanner focuses on running EC2 instances and attached EBS volumes
    -- the orphan scanners above already include tag checks for their resources.
    """
    findings = []
    log.info("Scanning running EC2 instances and attached volumes for missing tags...")

    
    paginator = ec2_client.get_paginator("describe_instances")
    for page in paginator.paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["running", "pending"]}]
    ):
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                inst_id = inst["InstanceId"]
                tags = tags_to_dict(inst.get("Tags"))
                missing = missing_tags(tags)
                if not missing:
                    continue

                findings.append(
                    build_finding(
                        resource_id=inst_id,
                        resource_type="ec2_instance",
                        reason=f"missing_tags:{','.join(missing)}",
                        age_days=days_since(inst.get("LaunchTime")),
                        estimated_monthly_cost_usd=0.0,  
                        tags=tags,
                        suggested_action="add_missing_tags",
                        safe_to_auto_delete=False,
                    )
                )
                log.info("  Running instance missing tags: %s (%s)", inst_id, missing)

    
    vol_paginator = ec2_client.get_paginator("describe_volumes")
    for page in vol_paginator.paginate(Filters=[{"Name": "status", "Values": ["in-use"]}]):
        for vol in page["Volumes"]:
            vol_id = vol["VolumeId"]
            tags = tags_to_dict(vol.get("Tags"))
            missing = missing_tags(tags)
            if not missing:
                continue

            size_gb = vol.get("Size", EBS_DEFAULT_SIZE_GB)
            vol_type = vol.get("VolumeType", "gp3")
            cost_per_gb = EBS_COST_PER_GB_MONTH.get(vol_type, EBS_DEFAULT_COST_PER_GB_MONTH)

            findings.append(
                build_finding(
                    resource_id=vol_id,
                    resource_type="ebs_volume",
                    reason=f"missing_tags:{','.join(missing)}",
                    age_days=days_since(vol.get("CreateTime")),
                    estimated_monthly_cost_usd=size_gb * cost_per_gb,
                    tags=tags,
                    suggested_action="add_missing_tags",
                    safe_to_auto_delete=False,
                )
            )
            log.info("  In-use volume missing tags: %s (%s)", vol_id, missing)

    return findings






def delete_findings(ec2_client, findings: list[dict], dry_run: bool) -> None:
    """Attempt to delete/release resources marked safe_to_auto_delete."""
    for f in findings:
        if not f["safe_to_auto_delete"]:
            log.info("SKIP (not safe): %s %s", f["resource_type"], f["resource_id"])
            continue

        if dry_run:
            log.info("DRY-RUN would delete: %s %s", f["resource_type"], f["resource_id"])
            continue

        try:
            if f["resource_type"] == "ebs_volume":
                ec2_client.delete_volume(VolumeId=f["resource_id"])
                log.info("DELETED EBS volume: %s", f["resource_id"])
            elif f["resource_type"] == "elastic_ip":
                ec2_client.release_address(AllocationId=f["resource_id"])
                log.info("RELEASED EIP: %s", f["resource_id"])
            
        except ClientError as e:
            log.error("Failed to delete %s %s: %s", f["resource_type"], f["resource_id"], e)






def build_report(
    findings: list[dict],
    account_id: str,
    region: str,
) -> dict[str, Any]:
    total_waste = sum(f["estimated_monthly_cost_usd"] for f in findings)
    return {
        "scan_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "account_id": account_id,
        "region": region,
        "summary": {
            "total_orphans": len(findings),
            "estimated_monthly_waste_usd": round(total_waste, 2),
        },
        "findings": findings,
    }


def build_markdown(report: dict) -> str:
    lines = [
        "Cost Janitor Report",
        "",
        f"**Scan time:** `{report['scan_timestamp']}`  ",
        f"**Account:** `{report['account_id']}`  ",
        f"**Region:** `{report['region']}`",
        "",
        "Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total orphans found | **{report['summary']['total_orphans']}** |",
        f"| Estimated monthly waste | **${report['summary']['estimated_monthly_waste_usd']:.2f}** |",
        "",
    ]

    if not report["findings"]:
        lines.append("No orphaned resources found. Account looks clean!")
        return "\n".join(lines)

    lines += [
        "Findings",
        "",
        "| Resource ID | Type | Reason | Age (days) | Est. Cost/mo | Safe to Auto-Delete? | Action |",
        "|-------------|------|--------|------------|--------------|----------------------|--------|",
    ]

    for f in report["findings"]:
        age = str(f["age_days"]) if f["age_days"] is not None else "unknown"
        safe = "Yes" if f["safe_to_auto_delete"] else "No"
        cost = f"${f['estimated_monthly_cost_usd']:.2f}"
        lines.append(
            f"| `{f['resource_id']}` | {f['resource_type']} | {f['reason']} "
            f"| {age} | {cost} | {safe} | {f['suggested_action']} |"
        )

    lines += [
        "",
        "---",
        "",
        "> **Note:** Resources tagged `Protected=true` are never auto-deleted even in `--delete` mode.",
        "> Always verify findings before running `--delete`.",
    ]

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cost Janitor -- detect orphaned AWS resources"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Report orphans without deleting (default)",
    )
    mode.add_argument(
        "--delete",
        action="store_true",
        default=False,
        help="Delete safe orphans (respects Protected=true tag)",
    )
    parser.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    parser.add_argument(
        "--endpoint-url",
        default=os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566"),
        help="AWS endpoint override (for LocalStack)",
    )
    parser.add_argument(
        "--stopped-days",
        type=int,
        default=EC2_STOPPED_THRESHOLD_DAYS_DEFAULT,
        help=f"Flag EC2 instances stopped > N days (default: {EC2_STOPPED_THRESHOLD_DAYS_DEFAULT})",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory to write report.json and report.md",
    )
    return parser.parse_args()


def get_account_id(sts_client) -> str:
    try:
        return sts_client.get_caller_identity()["Account"]
    except Exception:
        return "000000000000"  


def main() -> int:
    args = parse_args()
    delete_mode = args.delete
    dry_run = not delete_mode

    log.info("=" * 60)
    log.info("Cost Janitor starting")
    log.info("Mode: %s", "DELETE" if delete_mode else "DRY-RUN")
    log.info("Region: %s", args.region)
    log.info("Endpoint: %s", args.endpoint_url)
    log.info("=" * 60)

    boto_kwargs = dict(
        region_name=args.region,
        endpoint_url=args.endpoint_url,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
    )

    ec2 = boto3.client("ec2", **boto_kwargs)
    sts = boto3.client("sts", **boto_kwargs)

    account_id = get_account_id(sts)
    log.info("Account ID: %s", account_id)

    
    findings: list[dict] = []
    findings.extend(scan_unattached_ebs(ec2))
    findings.extend(scan_stopped_ec2(ec2, args.stopped_days))
    findings.extend(scan_unassociated_eips(ec2))
    findings.extend(scan_untagged_resources(ec2))

    
    seen: set[str] = set()
    unique_findings = []
    for f in findings:
        key = f["resource_id"]
        if key not in seen:
            seen.add(key)
            unique_findings.append(f)
        else:
            
            for existing in unique_findings:
                if existing["resource_id"] == key:
                    if f["reason"] not in existing["reason"]:
                        existing["reason"] += "; " + f["reason"]
                    break

    report = build_report(unique_findings, account_id, args.region)
    markdown = build_markdown(report)

    
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report_json_path = output_dir / "report.json"
    report_md_path = output_dir / "report.md"

    report_json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report_md_path.write_text(markdown, encoding="utf-8")

    log.info("Report written to %s", report_json_path)
    log.info("Markdown summary written to %s", report_md_path)

    
    sys.stdout.buffer.write(markdown.encode("utf-8"))
    sys.stdout.buffer.write(b"\n")

    orphan_count = report["summary"]["total_orphans"]
    waste = report["summary"]["estimated_monthly_waste_usd"]
    log.info("Scan complete: %d orphans found, ~$%.2f/month waste", orphan_count, waste)

    if delete_mode and unique_findings:
        log.info("DELETE mode: processing safe deletions...")
        delete_findings(ec2, unique_findings, dry_run=False)

    
    if dry_run and orphan_count > 0:
        log.warning("Orphans found in dry-run mode -- exiting with code 1 (CI will fail)")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())