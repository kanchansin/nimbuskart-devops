# 🧹 Cost Janitor Report

**Scan time:** `2026-05-21T23:55:01Z`  
**Account:** `000000000000`  
**Region:** `us-east-1`

## Summary

| Metric | Value |
|--------|-------|
| Total orphans found | **3** |
| Estimated monthly waste | **$15.25** |

## Findings

| Resource ID | Type | Reason | Age (days) | Est. Cost/mo | Safe to Auto-Delete? | Action |
|-------------|------|--------|------------|--------------|----------------------|--------|
| `vol-51ca07efccb16afa9` | ebs_volume | unattached | 0 | $1.60 | Yes | delete |
| `vol-c7f6bd589fafccefd` | ebs_volume | unattached; missing_tags:Environment,Project,Owner | 0 | $10.00 | No | delete |
| `eipalloc-a577bed5910cb7574` | elastic_ip | unassociated; missing_tags:Environment,Project,Owner | unknown | $3.65 | No | release |

---

> **Note:** Resources tagged `Protected=true` are never auto-deleted even in `--delete` mode.
> Always verify findings before running `--delete`.