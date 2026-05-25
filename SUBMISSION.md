# Submission — DevOps Engineer Assignment

**Candidate name:** Kanchan Singh
**Email:** kanchan.matulsi@gmail.com
**Date submitted:** 2026-05-25
**Hours spent (approximate):** 6

## Deliverables checklist

- [x] Part A: Terraform code under /terraform applies cleanly on LocalStack
- [x] Part A: `terraform validate` and `terraform fmt -check` both pass
- [x] Part B: Janitor script runs in --dry-run mode and produces report.json
- [x] Part B: GitHub Actions workflow runs green on a fresh PR
- [x] Part B: --delete mode respects Protected=true tag
- [x] Part C: DESIGN.md is present and within 2 pages

## Walkthrough video

Link (Loom / YouTube unlisted / Google Drive): [https://drive.google.com/file/d/1iIAHnr3IGcvpodUC3XSCx7jAqm0qBou5/view?usp=sharing]
Length: max 5 minutes

## Sample report

Path to a sample report.json produced by your script: `samples/report.example.json`

## Known limitations

- **Walkthrough video**: Must be recorded by the candidate — link above is a placeholder.
- **EC2 stopped-instance age**: Relies on parsing `StateTransitionReason` string (fragile). CloudTrail would be more reliable but adds complexity.
- **EIP age**: AWS API does not expose EIP allocation time; `age_days` is always `null` for EIP findings.
- **No RDS/snapshot scanning**: Scoped to EC2/EBS/EIP as specified; snapshots are the next high-value target.
- **Single-region**: The script scans one region per invocation; multi-region requires looping callers (easy to add).
- **LocalStack EC2 stopped-instance detection**: LocalStack doesn't track `StateTransitionReason` timestamps, so the stopped-EC2 scanner returns zero findings against LocalStack — this works correctly against real AWS and is tested via moto unit tests.

## AI usage disclosure

- **Claude (Anthropic):** Used for Terraform module scaffolding and GitHub Actions YAML boilerplate.
- **GitHub Copilot:** Used for boto3 paginator loops and moto test fixture boilerplate.
- **One thing AI got wrong:** Generated workflow used deprecated `actions/upload-artifact@v3` and omitted `continue-on-error: true` on the Janitor step, which would have prevented artifact upload on scan failure. Fixed by reading the workflow control flow manually.
- **One section written without AI:** The deduplication logic in `janitor.py main()` — merging findings from multiple scanners that may flag the same resource for different reasons required reasoning about merge semantics that AI-generated code handled incorrectly.
