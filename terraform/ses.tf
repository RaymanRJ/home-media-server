# --- SES SMTP: a no-reply sender for the monitoring alerts ---
#
# The zone's domain must already be a verified SES identity in us-east-1, with its DKIM, SPF and
# DMARC records (set up in the SES console, or by whatever manages the zone). It isn't managed here;
# only a user that may send through it is. In the SES sandbox, which only delivers to verified
# identities, mail to the domain's own addresses still works, since the domain is one.

data "aws_caller_identity" "current" {}

locals {
  ses_region   = "us-east-1" # where the domain is verified
  from_address = "noreply@${var.hosted_zone_name}"
}

resource "aws_iam_user" "ses_smtp" {
  name = "${var.project_name}-ses-smtp"
}

# Send only as the no-reply address, and only to addresses on the domain, so a leaked password
# can't mail anyone else even if the account ever leaves the sandbox.
resource "aws_iam_user_policy" "ses_smtp" {
  name = "send-as-noreply"
  user = aws_iam_user.ses_smtp.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ses:SendRawEmail", "ses:SendEmail"]
      Resource = "arn:aws:ses:${local.ses_region}:${data.aws_caller_identity.current.account_id}:identity/${var.hosted_zone_name}"
      Condition = {
        StringEquals = {
          "ses:FromAddress" = local.from_address
        }
        "ForAllValues:StringLike" = {
          "ses:Recipients" = ["*@${var.hosted_zone_name}"]
        }
      }
    }]
  })
}

# The SMTP password is derived from this key for the provider's region, hence the us-east-1
# provider. Like the CA's key, it's in the Terraform state. To rotate:
#   terraform apply -replace=aws_iam_access_key.ses_smtp
# then update the server; the old password stops working at once.
resource "aws_iam_access_key" "ses_smtp" {
  provider = aws.ses
  user     = aws_iam_user.ses_smtp.name
}

# Where the server's setup reads them from. Standard parameters, encrypted with the AWS-managed
# aws/ssm key: both free.
resource "aws_ssm_parameter" "ses_smtp_username" {
  provider    = aws.ses
  name        = "/${var.project_name}/ses-smtp/username"
  description = "SES SMTP username (the access key id) for the no-reply sender"
  type        = "String"
  value       = aws_iam_access_key.ses_smtp.id
}

resource "aws_ssm_parameter" "ses_smtp_password" {
  provider    = aws.ses
  name        = "/${var.project_name}/ses-smtp/password"
  description = "SES SMTP password for the no-reply sender, valid in ${local.ses_region}"
  type        = "SecureString"
  value       = aws_iam_access_key.ses_smtp.ses_smtp_password_v4
}
