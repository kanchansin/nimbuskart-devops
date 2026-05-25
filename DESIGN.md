# DESIGN.md — Cost Janitor: Hardening, Scale & Production

> Maximum 2 pages. Specific and opinionated throughout.

---

## 1. Multi-Cloud Reality: Adding GCP (and Later Azure)

The current Janitor is AWS-only because it talks directly to `boto3`. To add GCP without
rewriting the core, the right abstraction is a **provider plugin pattern**:

```
janitor/
├── core/
│   ├── models.py        # Finding, Report dataclasses — cloud-agnostic
│   ├── report.py        # JSON/Markdown rendering — cloud-agnostic
│   └── runner.py        # Orchestrates scanners, deduplication, deletion
├── providers/
│   ├── base.py          # Abstract BaseProvider with scan() → list[Finding]
│   ├── aws/
│   │   ├── provider.py  # Implements BaseProvider using boto3
│   │   └── scanners/    # ebs.py, ec2.py, eip.py, tags.py
│   ├── gcp/
│   │   ├── provider.py  # Implements BaseProvider using google-cloud SDK
│   │   └── scanners/    # disks.py, instances.py, addresses.py, labels.py
│   └── azure/
│       ├── provider.py  # Implements BaseProvider using azure-mgmt SDK
│       └── scanners/    # disks.py, vms.py, ips.py, tags.py
└── janitor.py           # CLI: loads providers by config, calls runner
```

`BaseProvider` exposes one method: `scan(config) → list[Finding]`. The `runner.py` calls
every registered provider, merges findings into a single `Report`, and handles deletions
via a `delete(finding)` method on each provider. Adding GCP means writing `gcp/provider.py`
— the core report, deduplication, and CI logic stays untouched.

**Config-driven provider registration** (e.g. `janitor.yaml`):
```yaml
providers:
  - type: aws
    regions: [us-east-1, eu-west-1]
    assume_role_arn: arn:aws:iam::123456789012:role/JanitorReadOnly
  - type: gcp
    projects: [nimbuskart-prod]
    service_account: janitor@nimbuskart-prod.iam.gserviceaccount.com
```

---

## 2. Permissions: Minimal IAM Policy

**Dry-run mode** needs read-only access. The minimal policy (JSON):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "JanitorReadOnly",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeVolumes",
        "ec2:DescribeInstances",
        "ec2:DescribeAddresses",
        "ec2:DescribeTags",
        "sts:GetCallerIdentity"
      ],
      "Resource": "*"
    }
  ]
}
```

**Delete mode** adds only the destructive actions needed, with a condition guard:

```json
{
  "Sid": "JanitorDeleteSafe",
  "Effect": "Allow",
  "Action": [
    "ec2:DeleteVolume",
    "ec2:ReleaseAddress"
  ],
  "Resource": "*",
  "Condition": {
    "StringNotEquals": { "aws:ResourceTag/Protected": "true" }
  }
}
```

`ec2:TerminateInstances` is intentionally **absent** — the Janitor flags stopped instances
for human review but never terminates. Multi-account: attach these policies to a
`JanitorRole` in each member account; the Janitor assumes it via
`sts:AssumeRole` from a central automation account.

---

## 3. Safety Net: Two Failure Modes & Guardrails

**Failure mode 1 — Unattached volume that's actually a detached-for-maintenance disk.**  
A DBA detaches an RDS/EC2 volume to run `fsck` or resize it. The Janitor sees it as
`available` and marks it `safe_to_auto_delete`. Auto-deletion destroys production data.

*Guardrail:* Add a `DetachTime` check — skip any volume detached fewer than **48 hours**
ago (query `describe_volumes` `Attachments[].DetachTime`). Require explicit `Reason` tag
on recently-detached volumes. In delete mode, write a pre-deletion snapshot and tag it
`CreatedBy=janitor` before deleting the volume. Keep snapshots for 7 days.

**Failure mode 2 — EIP released while DNS hasn't propagated.**  
NimbusKart uses an EIP as a static endpoint. They stop the instance for a hotfix window
(instance shows stopped, EIP shows unassociated). Janitor releases the EIP. DNS still
points to the old IP. When they restart and get a new EIP, their domain is broken for
the TTL duration (potentially hours).

*Guardrail:* Before releasing any EIP, query Route 53 (and optionally a public DNS
resolver) to check if the IP appears in any A/ALIAS record. If it does, skip deletion
and flag as `"reason": "eip_in_dns_record — manual review required"`. Additionally,
respect a `"LastUsed"` tag convention: only auto-release EIPs unassociated for **>7 days**
(not just currently unassociated).

---

## 4. Observability: 5 Metrics for the FinOps Team

| # | Metric | Source | Destination | Alert threshold |
|---|--------|--------|-------------|-----------------|
| 1 | `janitor.orphans_found` (count, by type) | `report.json summary.total_orphans` | CloudWatch custom metrics / Datadog | > 10 orphans on any single scan |
| 2 | `janitor.estimated_waste_usd` (gauge, monthly) | `report.json summary.estimated_monthly_waste_usd` | Same | > $200/month (configurable per account) |
| 3 | `janitor.scan_duration_seconds` | Script wall-clock time | Same | > 300s (indicates API throttling or hung scan) |
| 4 | `janitor.resources_deleted` (count, by type) | Emitted during `--delete` run | Same + CloudTrail | Any deletion in prod without a matching Jira ticket tag |
| 5 | `janitor.scan_errors` (count) | Exception count in runner | PagerDuty | > 0 (any scan error should be investigated immediately) |

Metrics are emitted via `boto3 cloudwatch.put_metric_data` (AWS) or
`google.cloud.monitoring` (GCP). A Grafana dashboard joins them across clouds.
The FinOps team gets a weekly Slack digest from a scheduled Lambda that queries
the last 7 days of `janitor.estimated_waste_usd` and diffs it against the prior week.

---

## 5. What I Consciously Left Out

**Snapshot cleanup** (old EC2/RDS snapshots are often the biggest waste category — easily
$50–200/month for a startup — but requires careful age + lineage checks to avoid deleting
the only backup). **S3 storage class optimisation** (Intelligent-Tiering, Glacier moves)
requires access pattern analysis, not just a tag check. **Multi-account orchestration**
(AWS Organizations + StackSets to deploy the IAM role into every member account) is
essential for production but would double the repo size. **Slack/JIRA ticketing integration**
for non-auto-deletable findings: in production you'd open a ticket, not just log a line.
All of these are the obvious next sprint — I scoped to what can be verified end-to-end
in a local environment within the assignment time budget.
