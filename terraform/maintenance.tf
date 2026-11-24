# --- The maintenance page ---
#
# When the house is offline, the outside check (outside-check/check.py) points the Jellyfin name, www.
# and the Seerr name at an API Gateway that answers every request with a static "offline" page,
# served by the same Lambda. The dns-updater on the server (unchanged) puts the A records back as
# soon as the server is up. Caddy and the server don't know any of this exists.
#
# The Seerr name stays a CNAME to the Jellyfin name (main.tf): during an outage it follows the
# Jellyfin A record to API Gateway, which picks the custom domain by the name in the request.

locals {
  page_names = toset([var.domain_name, "www.${var.domain_name}", var.seerr_domain_name])
}

# API Gateway's regional custom domains need the certificate in the same region as the API.
resource "aws_acm_certificate" "page" {
  domain_name               = var.domain_name
  subject_alternative_names = ["www.${var.domain_name}", var.seerr_domain_name]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "page_validation" {
  for_each = {
    for o in aws_acm_certificate.page.domain_validation_options : o.domain_name => {
      name   = o.resource_record_name
      type   = o.resource_record_type
      record = o.resource_record_value
    }
  }

  zone_id         = data.aws_route53_zone.site.zone_id
  name            = each.value.name
  type            = each.value.type
  ttl             = 300
  records         = [each.value.record]
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "page" {
  certificate_arn         = aws_acm_certificate.page.arn
  validation_record_fqdns = [for r in aws_route53_record.page_validation : r.fqdn]
}

# Only the custom domains are reachable, not the default execute-api name.
resource "aws_apigatewayv2_api" "page" {
  name                         = "${var.project_name}-maintenance-page"
  protocol_type                = "HTTP"
  disable_execute_api_endpoint = true
}

resource "aws_apigatewayv2_integration" "page" {
  api_id                 = aws_apigatewayv2_api.page.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.monitor.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "page" {
  api_id    = aws_apigatewayv2_api.page.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.page.id}"
}

# The throttle keeps a flood of requests from running up Lambda invocations.
resource "aws_apigatewayv2_stage" "page" {
  api_id      = aws_apigatewayv2_api.page.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 10
    throttling_rate_limit  = 5
  }
}

resource "aws_apigatewayv2_domain_name" "page" {
  for_each    = local.page_names
  domain_name = each.value

  domain_name_configuration {
    certificate_arn = aws_acm_certificate_validation.page.certificate_arn
    endpoint_type   = "REGIONAL"
    security_policy = "TLS_1_2"
  }
}

resource "aws_apigatewayv2_api_mapping" "page" {
  for_each    = local.page_names
  api_id      = aws_apigatewayv2_api.page.id
  domain_name = aws_apigatewayv2_domain_name.page[each.key].id
  stage       = aws_apigatewayv2_stage.page.id
}

resource "aws_lambda_permission" "page" {
  statement_id  = "AllowApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.monitor.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.page.execution_arn}/*"
}
