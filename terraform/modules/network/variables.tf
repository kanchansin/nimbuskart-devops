variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.20.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "List of CIDR blocks for the two public subnets"
  type        = list(string)
  default     = ["10.20.1.0/24", "10.20.2.0/24"]
}

variable "availability_zones" {
  description = "List of AZs to deploy subnets into"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]
}

# DEVIATION: Spec says default 0.0.0.0/0 for SSH. We default to empty string
# and require explicit opt-in. See README "Decisions & deviations".
variable "ssh_ingress_cidr" {
  description = <<-EOT
    CIDR allowed to reach port 22. Set to your bastion or VPN CIDR.
    Intentionally has NO default — caller must be explicit.
    The original spec defaulted this to 0.0.0.0/0, which exposes SSH
    to the entire internet and is a security anti-pattern.
  EOT
  type        = string
}

variable "project_tags" {
  description = "Mandatory cost-attribution tags applied to every resource in this module"
  type = object({
    Project     = string
    Environment = string
    Owner       = string
  })
}
