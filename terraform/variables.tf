variable "aws_region" {
  description = "AWS region to deploy into (LocalStack: us-east-1)"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment (staging | production)"
  type        = string
  default     = "staging"
}

variable "project" {
  description = "Project name used for tagging and resource naming"
  type        = string
  default     = "nimbuskart"
}

variable "owner" {
  description = "Team or individual responsible for these resources"
  type        = string
  default     = "platform-team"
}

variable "ssh_ingress_cidr" {
  description = <<-EOT
    CIDR allowed inbound on port 22.
    MUST be a specific CIDR (e.g. your office/VPN: 203.0.113.0/24).
    0.0.0.0/0 is explicitly rejected in production; for LocalStack
    testing the tfvars file sets this to 127.0.0.1/32.
  EOT
  type        = string
  default     = "127.0.0.1/32" # LocalStack/CI safe default — loopback only
}

variable "web_instance_count" {
  description = "Number of web-tier EC2 instances"
  type        = number
  default     = 2
}

variable "web_instance_type" {
  description = "EC2 instance type for web tier"
  type        = string
  default     = "t3.micro"
}

variable "ami_id" {
  description = "AMI ID for web-tier instances (any valid-looking ID works on LocalStack)"
  type        = string
  default     = "ami-0c55b159cbfafe1f0" # Amazon Linux 2 us-east-1 — LocalStack ignores AMI validity
}

variable "log_bucket_name" {
  description = "Globally unique name for the application-log S3 bucket"
  type        = string
  default     = "nimbuskart-staging-app-logs"
}

variable "orphan_ebs_size_gb" {
  description = "Size (GB) of the intentionally-orphaned EBS volume (for Part B testing)"
  type        = number
  default     = 20
}

variable "noncurrent_version_expiry_days" {
  description = "Days after which non-current S3 object versions are expired"
  type        = number
  default     = 30
}
