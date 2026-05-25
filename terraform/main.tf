terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# ---------------------------------------------------------------------------
# Provider — LocalStack endpoint override
# When tflocal is used, it injects these automatically. The explicit block
# here makes the intent clear and allows plain `terraform` to work too
# (set TF_VAR_localstack_endpoint or use tflocal wrapper).
# ---------------------------------------------------------------------------
provider "aws" {
  region = var.aws_region

  # LocalStack connection — ignored when using real AWS credentials
  access_key = "test"
  secret_key = "test"

  endpoints {
    ec2 = "http://localhost:4566"
    s3  = "http://localhost:4566"
    iam = "http://localhost:4566"
  }

  # LocalStack doesn't validate these
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true

  s3_use_path_style = true # Required for LocalStack S3
}

# ---------------------------------------------------------------------------
# Local values — single source of truth for mandatory tags
# ---------------------------------------------------------------------------
locals {
  project_tags = {
    Project     = var.project
    Environment = var.environment
    Owner       = var.owner
  }

  common_tags = merge(local.project_tags, {
    ManagedBy = "terraform"
  })
}

# ---------------------------------------------------------------------------
# Module: Network (VPC, subnets, SG)
# ---------------------------------------------------------------------------
module "network" {
  source = "./modules/network"

  vpc_cidr            = "10.20.0.0/16"
  public_subnet_cidrs = ["10.20.1.0/24", "10.20.2.0/24"]
  availability_zones  = ["${var.aws_region}a", "${var.aws_region}b"]
  ssh_ingress_cidr    = var.ssh_ingress_cidr
  project_tags        = local.project_tags
}

# ---------------------------------------------------------------------------
# EC2 — Web tier (two t3.micro instances)
# ---------------------------------------------------------------------------
resource "aws_instance" "web" {
  count = var.web_instance_count

  ami                    = var.ami_id
  instance_type          = var.web_instance_type
  subnet_id              = module.network.public_subnet_ids[count.index % length(module.network.public_subnet_ids)]
  vpc_security_group_ids = [module.network.web_security_group_id]

  tags = merge(local.common_tags, {
    Name = "${var.project}-${var.environment}-web-${count.index + 1}"
    Tier = "web"
  })
}

# ---------------------------------------------------------------------------
# S3 — Application log bucket
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "app_logs" {
  bucket = var.log_bucket_name

  tags = merge(local.common_tags, {
    Name    = var.log_bucket_name
    Purpose = "application-logs"
  })
}

resource "aws_s3_bucket_versioning" "app_logs" {
  bucket = aws_s3_bucket.app_logs.id
  versioning_configuration {
    status = "Enabled"
  }
}


# Block all public access — log buckets should never be public
resource "aws_s3_bucket_public_access_block" "app_logs" {
  bucket = aws_s3_bucket.app_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------------------
# EBS — Intentional orphan (unattached volume for Part B testing)
# This resource is deliberately NOT attached to any aws_instance.
# In a real account this is exactly the kind of waste the Janitor catches.
# ---------------------------------------------------------------------------
resource "aws_ebs_volume" "orphan" {
  availability_zone = "${var.aws_region}a"
  size              = var.orphan_ebs_size_gb
  type              = "gp3"

  # Intentionally missing the Protected tag so the Janitor flags it
  tags = merge(local.common_tags, {
    Name   = "${var.project}-${var.environment}-orphan-vol"
    Note   = "intentional-orphan-for-janitor-testing"
  })
}
