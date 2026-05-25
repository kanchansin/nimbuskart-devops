# Walkthrough

## Video

[Link to Loom / YouTube unlisted recording — to be added before submission]

**Timestamps:**
- `0:00` — Start LocalStack, run `tflocal apply`, walk through outputs
- `1:30` — Run `python janitor.py --dry-run`, walk through one finding in `report.json`
- `3:00` — Point to the deduplication logic as a design decision I'm proud of
- `4:00` — One thing I would change: replace StateTransitionReason parsing with CloudTrail

## Transcript

[Auto-generated transcript to be added after recording]

---

## Key Design Decisions Covered in Video

### Decision I'm proud of: Deduplication with reason merging

A single EBS volume can trigger multiple scanners — `scan_unattached_ebs` (it's unattached)
and `scan_untagged_resources` (it's missing tags). Naive code produces two findings for
the same `resource_id`, which inflates `total_orphans` and confuses downstream consumers.

The dedup loop in `main()` merges these into one finding with a combined reason string:
`"unattached; missing_tags:Owner,Environment"`. This makes the report accurate and gives
the reviewer the full picture in one row.

### Thing I would change: EC2 stopped-instance detection

Currently we parse `StateTransitionReason` (e.g. `"User initiated (2024-03-15 10:00:00 GMT)"`)
to get the stop timestamp. This is fragile — the format is undocumented and can be
`"User initiated"` without a timestamp if the instance was stopped before a certain API
version. The right approach is to query CloudTrail for `StopInstances` events, which gives
a precise, reliable timestamp. I'd add this in the next sprint with a `--use-cloudtrail`
flag so it's opt-in (CloudTrail queries are slower and cost money in high-volume accounts).
