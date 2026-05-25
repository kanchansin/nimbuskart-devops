"""
Pricing constants for Cost Janitor waste estimation.

All prices are USD/month for the us-east-1 region.
These are static approximations — use AWS Cost Explorer for exact billing.

Sources:
  EBS:  https://aws.amazon.com/ebs/pricing/ (accessed 2025-01)
  EC2:  https://aws.amazon.com/ec2/pricing/on-demand/ (accessed 2025-01)
  EIP:  https://aws.amazon.com/ec2/pricing/on-demand/#Elastic_IP_Addresses (accessed 2025-01)
"""

# EBS volume costs (USD per GB per month)
EBS_COST_PER_GB_MONTH = {
    "gp3": 0.08,   # General Purpose SSD v3
    "gp2": 0.10,   # General Purpose SSD v2
    "io1": 0.125,  # Provisioned IOPS SSD
    "io2": 0.125,
    "st1": 0.045,  # Throughput Optimized HDD
    "sc1": 0.015,  # Cold HDD
    "standard": 0.05,
}
EBS_DEFAULT_COST_PER_GB_MONTH = 0.08  # fallback for unknown types

# Default volume size assumption when size is unavailable
EBS_DEFAULT_SIZE_GB = 8

# EC2 instance costs (USD per month, on-demand, Linux, us-east-1)
# A stopped instance does NOT accrue compute cost — only attached EBS does.
# We flag stopped instances for human review rather than billing waste.
EC2_STOPPED_COST_NOTE = (
    "Stopped EC2 instances do not incur compute charges, but their attached "
    "EBS volumes do. This finding is for hygiene/awareness, not direct cost."
)

# Elastic IP: charged when NOT associated with a running instance
# $0.005/hour = ~$3.65/month
EIP_UNASSOCIATED_COST_PER_MONTH = 3.65

# Days threshold: EC2 instances stopped longer than this are flagged
EC2_STOPPED_THRESHOLD_DAYS_DEFAULT = 14
