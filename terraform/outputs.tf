output "jellyfin_url" {
  description = "Where Jellyfin is served."
  value       = "https://${aws_route53_record.jellyfin.fqdn}"
}

output "seerr_url" {
  description = "Where Seerr is served; its name goes in the server's .env as SEERR_DOMAIN."
  value       = "https://${aws_route53_record.seerr.fqdn}"
}

# The server's .env needs these four.
output "server_env" {
  description = "Lines for the server's .env, for the dns-updater container."
  value       = <<-EOT
    HOSTED_ZONE_ID=${data.aws_route53_zone.site.zone_id}
    TRUST_ANCHOR_ARN=${aws_rolesanywhere_trust_anchor.server.arn}
    PROFILE_ARN=${aws_rolesanywhere_profile.dns_updater.arn}
    ROLE_ARN=${aws_iam_role.dns_updater.arn}
  EOT
}

# The server's certificate and key, for ~/.dns-updater on the server.
output "server_cert_pem" {
  description = "The server's Roles Anywhere certificate."
  value       = tls_locally_signed_cert.server.cert_pem
}

output "server_key_pem" {
  description = "The server's private key. Keep it off anything but the server."
  value       = tls_private_key.server.private_key_pem
  sensitive   = true
}

# Where the monitoring's SMTP credentials are.
output "ses_smtp" {
  description = "SMTP server, sender, and the SSM parameters holding the username and password."
  value = {
    server             = "email-smtp.${local.ses_region}.amazonaws.com:587"
    from               = local.from_address
    username_parameter = aws_ssm_parameter.ses_smtp_username.name
    password_parameter = aws_ssm_parameter.ses_smtp_password.name
  }
}

output "server_cert_expires" {
  description = "When the server's certificate expires; apply within 30 days of this to renew it."
  value       = tls_locally_signed_cert.server.validity_end_time
}

# The outside check: its function, log group and state parameter.
output "outside_check" {
  description = "The outside check's Lambda, its log group, and the parameter holding its last result."
  value = {
    function        = aws_lambda_function.monitor.function_name
    log_group       = aws_cloudwatch_log_group.monitor.name
    state_parameter = aws_ssm_parameter.monitor_state.name
  }
}

# The repository secrets the outside check's workflow needs.
output "github_actions_secrets" {
  description = "Secrets for .github/workflows/outside-check.yaml (null without github_repository)."
  value = local.deploy_from_github ? {
    AWS_REGION             = var.aws_region
    AWS_DEPLOY_ROLE_ARN    = aws_iam_role.deploy[0].arn
    OUTSIDE_CHECK_FUNCTION = aws_lambda_function.monitor.function_name
  } : null
}

# For testing the maintenance page without an outage.
output "maintenance_page_targets" {
  description = "Each name's API Gateway domain, which serves the maintenance page."
  value = {
    for name, d in aws_apigatewayv2_domain_name.page :
    name => d.domain_name_configuration[0].target_domain_name
  }
}
