provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}

# SES's region (ses.tf): its SMTP password is derived per region.
provider "aws" {
  alias  = "ses"
  region = "us-east-1"

  default_tags {
    tags = local.tags
  }
}
