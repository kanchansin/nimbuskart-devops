# NimbusKart Cost Hygiene & Automation

**A multi-cloud FinOps foundation for NimbusKart:** Terraform IaC on LocalStack, a Python
"Cost Janitor" that detects orphaned resources, and a GitHub Actions pipeline that enforces
cost hygiene on every PR.

---

## Overview

This repository implements a cost-hygiene automation stack for NimbusKart, a fictional
e-commerce startup whose AWS bill ballooned from ~$400 to ~$2,100/month due to orphaned
resources. It provisions NimbusKart's staging infrastructure as code (Terraform, targeting
LocalStack), then runs a Python script ("Cost Janitor") that scans for wasteful resources —
unattached EBS volumes, long-stopped EC2 instances, unassociated Elastic IPs, and untagged
resources — and outputs a structured `report.json` plus a human-readable Markdown summary.
A GitHub Actions workflow ties it all together: on every PR, it spins up LocalStack, applies
the Terraform stack, runs the Janitor in `--dry-run` mode, uploads the report as a build
artifact, and posts a comment to the PR if orphans are found.

---

## How to Run Locally

Prerequisites: **Docker**, **Python 3.10+**, **Terraform 1.5+**.

```bash
# 1. Clone
git clone https://github.com/<your-username>/nimbuskart-devops.git
cd nimbuskart-devops

# 2. Start LocalStack
docker run --rm -d \
  -p 4566:4566 \
  -e SERVICES=ec2,s3,iam,sts \
  --name localstack \
  localstack/localstack:latest

# Wait ~10 seconds for LocalStack to be healthy, then verify:
curl -s http://localhost:4566/_localstack/health | python3 -m json.tool

# 3. Install tflocal (LocalStack's Terraform wrapper)
pip install terraform-local

# 4. Provision the NimbusKart staging stack
cd terraform
tflocal init
tflocal apply -auto-approve
# Expected output: vpc_id, subnet IDs, bucket name, orphan EBS volume ID

# 5. Go back to root and install Janitor dependencies
cd ..
pip install -r janitor/requirements.txt

# 6. Run the Janitor in dry-run mode (default)
python janitor/janitor.py \
  --endpoint-url http://localhost:4566 \
  --region us-east-1 \
  --output-dir ./output

# Janitor exits with code 1 and writes:
#   ./output/report.json
#   ./output/report.md

# 7. (Optional) Run in delete mode — destroys safe orphans
python janitor/janitor.py \
  --delete \
  --endpoint-url http://localhost:4566 \
  --region us-east-1 \
  --output-dir ./output

# 8. Run unit tests (uses Moto — no LocalStack needed)
pip install pytest moto[ec2,s3,sts]
pytest janitor/tests/ -v

# 9. Tear down LocalStack when done
docker stop localstack
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        GitHub Actions PR                            │
│                                                                     │
│  ┌───────────────┐    ┌─────────────────┐    ┌──────────────────┐  │
│  │ LocalStack    │    │  Terraform       │    │  Cost Janitor    │  │
│  │ (service      │───▶│  (tflocal apply) │───▶│  (janitor.py     │  │
│  │  container)   │    │                 │    │   --dry-run)     │  │
│  │               │    │  Creates:       │    │                  │  │
│  │  Mocks AWS    │    │  • VPC+Subnets  │    │  Scans:          │  │
│  │  EC2, S3,     │    │  • EC2 x2       │    │  • Unattached    │  │
│  │  IAM, STS     │    │  • S3 bucket    │    │    EBS volumes   │  │
│  └───────────────┘    │  • Orphan EBS   │    │  • Stopped EC2   │  │
│                       └─────────────────┘    │  • Unassoc. EIPs │  │
│                                              │  • Missing tags  │  │
│                                              └────────┬─────────┘  │
│                                                       │             │
│                    ┌──────────────────────────────────┘             │
│                    ▼                                                 │
│          ┌──────────────────┐    ┌──────────────────────────────┐  │
│          │  report.json     │    │  PR Comment (if orphans)     │  │
│          │  report.md       │───▶│  + Artifacts upload          │  │
│          │  (artifacts)     │    │  + Workflow fails (exit 1)   │  │
│          └──────────────────┘    └──────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘

Terraform Module Structure:
  terraform/
  ├── main.tf              ← calls network module, defines EC2/S3/EBS
  └── modules/
      └── network/         ← reusable VPC + subnets + security group
          ├── main.tf
          ├── variables.tf
          └── outputs.tf

Janitor Scanner Pipeline:
  scan_unattached_ebs()  ──┐
  scan_stopped_ec2()     ──┼──▶ deduplicate ──▶ build_report() ──▶ build_markdown()
  scan_unassociated_eips()─┤                         │
  scan_untagged_resources()┘                         ▼
                                              report.json + report.md
```

---

## Decisions & Deviations

- **SSH CIDR defaulted to `127.0.0.1/32`, not `0.0.0.0/0`:** The spec says to "flag this" — opening SSH to the entire internet is a critical security risk. The variable has no permissive default; callers must be explicit. LocalStack CI uses `127.0.0.1/32` (loopback only).
- **S3 public access block added:** The spec doesn't mention it, but a log bucket should never be public. Added `aws_s3_bucket_public_access_block` with all four options set to `true`.
- **EC2 instances never auto-deleted:** `safe_to_auto_delete = false` for all EC2 findings regardless of tags. Terminating a running or recently-stopped instance is too high blast-radius for automation; it always requires human sign-off.
- **EIP age unknown:** The AWS API doesn't expose EIP allocation time, so `age_days` is `null` for EIP findings. The report documents this clearly rather than fabricating a number.
- **Missing tag = `safe_to_auto_delete = false`:** An untagged resource has unknown ownership. Deleting it without knowing who owns it risks an outage. Safety over cost savings.
- **`--dry-run` is the default flag:** The spec implies this but doesn't explicitly state the default. Making dry-run default means a careless `python janitor.py` never deletes anything.
- **LocalStack provider block in `main.tf`:** Using an explicit provider block instead of relying entirely on `tflocal` injection means the intent is self-documenting and the config works with both approaches.

---

## Trade-offs

Given one more week, I would:

1. **Add RDS snapshot cleanup** — old automated snapshots are often the single biggest line item on a startup's AWS bill ($50–300/month) but require lineage checks (don't delete the last snapshot) that would take a full day to implement safely.
2. **Add multi-account orchestration** — deploy a `JanitorRole` IAM role via AWS Organizations/StackSets so the same script scans every account in the org, not just one.
3. **Add GCP provider** — the module boundary in `DESIGN.md` section 1 is already designed for this; implementing it is mostly mechanical SDK work.
4. **Publish CloudWatch metrics** from the Janitor — currently just JSON/Markdown; a real FinOps team wants a Grafana dashboard, not a file download.
5. **Add a `--since` flag for stopped EC2** — currently we rely on parsing `StateTransitionReason` which is fragile. AWS CloudTrail `StopInstances` events give a definitive timestamp.

---

## AI Usage Disclosure

**Tools used:**
- Claude (Anthropic) for initial scaffolding of Terraform module structure and the GitHub Actions YAML boilerplate (service container health-check syntax is tedious to get right from memory).
- GitHub Copilot for autocomplete on the boto3 paginator loops and the `moto` test fixtures.

**One thing AI got wrong:**
The initial GitHub Actions workflow generated by Claude used `actions/upload-artifact@v3`, which was deprecated. It also omitted the `continue-on-error: true` on the Janitor step — meaning the step would fail before uploading the artifact or posting the PR comment. I caught this by reading the workflow logic end-to-end: if the Janitor exits 1 (orphans found), you need the artifact and comment steps to *still run*, which requires `continue-on-error` on the scan step and an explicit `Fail if orphans found` step at the end.

**One section written without AI:**
The deduplication logic in `janitor.py` (`main()` — the part that merges findings from multiple scanners and coalesces duplicate `resource_id` entries by appending reasons). I wrote this manually because it required thinking through a subtle correctness issue: a volume can appear in both `scan_unattached_ebs` (for being unattached) *and* `scan_untagged_resources` (for missing tags). The right behaviour is one finding with a combined reason string, not two separate findings for the same resource ID. AI-generated deduplication either dropped the second finding entirely or created duplicates; I needed to reason through the merge semantics myself.
