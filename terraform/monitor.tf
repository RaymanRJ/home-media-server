# --- The outside check ---
#
# A Lambda that every five minutes opens https://<domain>/health from AWS, the way a viewer outside
# the house would, and emails through SES when that stops working and when it's back
# (outside-check/check.py). It sees what the server's own monitoring can't: the house losing power
# or its connection, and the router answering 443 itself after a power cut.
#
# The same function serves the maintenance page (maintenance.tf): when the house is offline it
# points the A records at API Gateway, and API Gateway invokes it to answer browsers.

locals {
  monitor_name = "${var.project_name}-outside-check"
}

data "archive_file" "monitor" {
  type = "zip"

  source {
    content  = file("${path.module}/../outside-check/check.py")
    filename = "check.py"
  }

  source {
    content  = file("${path.module}/../outside-check/maintenance.html")
    filename = "maintenance.html"
  }

  output_path = "${path.module}/.build/outside-check.zip"
}

# The last result it emailed about. Terraform only creates it; the function rewrites it.
resource "aws_ssm_parameter" "monitor_state" {
  name        = "/${var.project_name}/outside-check/state"
  description = "The outside check's last reported result (JSON); written by the function"
  type        = "String"
  value       = jsonencode({ reported = "ok" })

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_cloudwatch_log_group" "monitor" {
  name              = "/aws/lambda/${local.monitor_name}"
  retention_in_days = 30
}

data "aws_iam_policy_document" "monitor_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "monitor" {
  name               = local.monitor_name
  assume_role_policy = data.aws_iam_policy_document.monitor_trust.json
}

# Its own log group, its own state parameter, and email as the no-reply address to alert_email only.
# Plus the dns-updater's Route 53 rights (main.tf): reading the zone, and UPSERTs of the two A
# records, which it uses to switch them to the maintenance page and back.
data "aws_iam_policy_document" "monitor" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.monitor.arn}:*"]
  }

  statement {
    actions   = ["ssm:GetParameter", "ssm:PutParameter"]
    resources = [aws_ssm_parameter.monitor_state.arn]
  }

  statement {
    actions   = ["route53:ListResourceRecordSets"]
    resources = [data.aws_route53_zone.site.arn]
  }

  statement {
    actions   = ["route53:ChangeResourceRecordSets"]
    resources = [data.aws_route53_zone.site.arn]

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "route53:ChangeResourceRecordSetsNormalizedRecordNames"
      values   = [for name in local.record_names : lower(trimsuffix(name, "."))]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "route53:ChangeResourceRecordSetsRecordTypes"
      values   = ["A"]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "route53:ChangeResourceRecordSetsActions"
      values   = ["UPSERT"]
    }
  }

  statement {
    actions   = ["ses:SendEmail"]
    resources = ["arn:aws:ses:${local.ses_region}:${data.aws_caller_identity.current.account_id}:identity/${var.hosted_zone_name}"]

    condition {
      test     = "StringEquals"
      variable = "ses:FromAddress"
      values   = [local.from_address]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "ses:Recipients"
      values   = [var.alert_email]
    }
  }
}

resource "aws_iam_role_policy" "monitor" {
  name   = "check-and-email"
  role   = aws_iam_role.monitor.id
  policy = data.aws_iam_policy_document.monitor.json
}

resource "aws_lambda_function" "monitor" {
  function_name    = local.monitor_name
  description      = "Checks https://${var.domain_name}/health from outside and emails on changes, and serves the maintenance page"
  role             = aws_iam_role.monitor.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "check.handler"
  filename         = data.archive_file.monitor.output_path
  source_code_hash = data.archive_file.monitor.output_base64sha256
  memory_size      = 128
  timeout          = 30

  environment {
    variables = {
      DOMAIN          = var.domain_name
      STATE_PARAMETER = aws_ssm_parameter.monitor_state.name
      FROM            = local.from_address
      TO              = var.alert_email
      SES_REGION      = local.ses_region
      HOSTED_ZONE_ID  = data.aws_route53_zone.site.zone_id
      # The two A records it switches to the page: the API Gateway domain each is aliased to.
      PAGE_TARGETS = jsonencode({
        for name in local.record_names : name => {
          dns  = aws_apigatewayv2_domain_name.page[name].domain_name_configuration[0].target_domain_name
          zone = aws_apigatewayv2_domain_name.page[name].domain_name_configuration[0].hosted_zone_id
        }
      })
    }
  }

  depends_on = [aws_cloudwatch_log_group.monitor, aws_iam_role_policy.monitor]

  # Terraform uploads the code only when it creates the function. After that, GitHub Actions
  # deploys it on every push to main (.github/workflows/outside-check.yaml).
  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }
}

# --- GitHub Actions deploy role ---

# GitHub's OIDC provider is one per account, often already made by another project, so it's only
# looked up here. If the account has none yet, create it once:
#   aws iam create-open-id-connect-provider --url https://token.actions.githubusercontent.com \
#     --client-id-list sts.amazonaws.com
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

# Only workflows on main of this one repository can assume it.
resource "aws_iam_role" "deploy" {
  name        = "GitHubActionsDeploy-${local.monitor_name}"
  description = "Deploys the ${local.monitor_name} Lambda's code from GitHub Actions"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repository}:ref:refs/heads/main"
        }
      }
    }]
  })
}

# Just enough to replace the code, wait for it, and run it once as a check. Not its configuration,
# role or schedule: those stay with Terraform.
resource "aws_iam_role_policy" "deploy" {
  name = "deploy-code"
  role = aws_iam_role.deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:UpdateFunctionCode", "lambda:GetFunctionConfiguration", "lambda:InvokeFunction"]
      Resource = aws_lambda_function.monitor.arn
    }]
  })
}

resource "aws_cloudwatch_event_rule" "monitor" {
  name                = local.monitor_name
  description         = "Runs ${local.monitor_name} every five minutes"
  schedule_expression = "rate(5 minutes)"
}

resource "aws_cloudwatch_event_target" "monitor" {
  rule = aws_cloudwatch_event_rule.monitor.name
  arn  = aws_lambda_function.monitor.arn
}

resource "aws_lambda_permission" "monitor" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.monitor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.monitor.arn
}
