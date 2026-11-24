locals {
  tags = merge(var.tags, {
    Project   = var.project_name
    ManagedBy = "Terraform"
  })

  record_names = [var.domain_name, "www.${var.domain_name}"]

  # The common name on the server's certificate. The role only trusts this one.
  server_cn = "${var.project_name}-server"
}

# The hosted zone already exists and is managed elsewhere; this only adds records to it.
data "aws_route53_zone" "site" {
  name         = var.hosted_zone_name
  private_zone = false
}

# A records on the home connection's IP. Terraform only sets the first value: the dns-updater
# container on the server keeps them current, so changes to the IP are ignored here.
#
# These used to be CNAMEs to the router's TP-Link dynamic DNS name, but TP-Link's nameservers
# break the DNS rules strict resolvers check (they change the case of the name in answers, and
# answer NXDOMAIN for other record types), so Let's Encrypt got SERVFAIL and couldn't issue a
# certificate. Being A records also means nothing here depends on the router.
resource "aws_route53_record" "jellyfin" {
  zone_id = data.aws_route53_zone.site.zone_id
  name    = var.domain_name
  type    = "A"
  ttl     = 300
  records = [var.home_ip]

  lifecycle {
    ignore_changes = [records]
  }
}

# www. in front of the domain, for anyone who types it. Caddy redirects it to domain_name.
resource "aws_route53_record" "jellyfin_www" {
  zone_id = data.aws_route53_zone.site.zone_id
  name    = "www.${var.domain_name}"
  type    = "A"
  ttl     = 300
  records = [var.home_ip]

  lifecycle {
    ignore_changes = [records]
  }
}

# Seerr, behind the same Caddy. A CNAME to the Jellyfin name, so it follows the home IP without the
# dns-updater (the CNAME problem above was TP-Link's nameservers; this one stays in Route 53).
resource "aws_route53_record" "seerr" {
  zone_id = data.aws_route53_zone.site.zone_id
  name    = var.seerr_domain_name
  type    = "CNAME"
  ttl     = 300
  records = [aws_route53_record.jellyfin.fqdn]
}

# --- IAM Roles Anywhere: AWS credentials for the server without an access key ---
#
# A private CA and the server's certificate, both generated here. Their private keys are in the
# Terraform state, which is why the state bucket is encrypted and private. The server presents its
# certificate to Roles Anywhere and gets one-hour credentials for the role below.

resource "tls_private_key" "ca" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}

resource "tls_self_signed_cert" "ca" {
  private_key_pem       = tls_private_key.ca.private_key_pem
  is_ca_certificate     = true
  validity_period_hours = 87600 # 10 years
  allowed_uses          = ["cert_signing", "crl_signing", "digital_signature"]

  subject {
    common_name = "${var.project_name} CA"
  }
}

resource "tls_private_key" "server" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}

resource "tls_cert_request" "server" {
  private_key_pem = tls_private_key.server.private_key_pem

  subject {
    common_name = local.server_cn
  }
}

# Two years. Within 30 days of expiry, `terraform apply` replaces it; copy the new one to the
# server.
resource "tls_locally_signed_cert" "server" {
  cert_request_pem      = tls_cert_request.server.cert_request_pem
  ca_private_key_pem    = tls_private_key.ca.private_key_pem
  ca_cert_pem           = tls_self_signed_cert.ca.cert_pem
  validity_period_hours = 17520
  early_renewal_hours   = 720
  allowed_uses          = ["digital_signature", "client_auth"]
}

resource "aws_rolesanywhere_trust_anchor" "server" {
  name    = "${var.project_name}-server"
  enabled = true

  source {
    source_type = "CERTIFICATE_BUNDLE"
    source_data {
      x509_certificate_data = tls_self_signed_cert.ca.cert_pem
    }
  }
}

# Only certificates from this trust anchor, with the server's common name, can assume the role.
data "aws_iam_policy_document" "dns_updater_trust" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession", "sts:SetSourceIdentity"]

    principals {
      type        = "Service"
      identifiers = ["rolesanywhere.amazonaws.com"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_rolesanywhere_trust_anchor.server.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalTag/x509Subject/CN"
      values   = [local.server_cn]
    }
  }
}

resource "aws_iam_role" "dns_updater" {
  name               = "${var.project_name}-dns-updater"
  assume_role_policy = data.aws_iam_policy_document.dns_updater_trust.json
}

# Only UPSERTs of the two A records above, and reading the zone to compare against.
data "aws_iam_policy_document" "dns_updater" {
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
    actions   = ["route53:ListResourceRecordSets"]
    resources = [data.aws_route53_zone.site.arn]
  }
}

resource "aws_iam_role_policy" "dns_updater" {
  name   = "update-jellyfin-records"
  role   = aws_iam_role.dns_updater.id
  policy = data.aws_iam_policy_document.dns_updater.json
}

resource "aws_rolesanywhere_profile" "dns_updater" {
  name             = "${var.project_name}-dns-updater"
  enabled          = true
  role_arns        = [aws_iam_role.dns_updater.arn]
  duration_seconds = 3600
}
