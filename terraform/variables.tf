variable "project_name" {
  description = "Short name used to tag resources, e.g. \"media\"."
  type        = string
}

variable "domain_name" {
  description = "Name Jellyfin is served from, e.g. \"jellyfin.example.com\"."
  type        = string
}

variable "hosted_zone_name" {
  description = "Existing public Route 53 hosted zone that contains domain_name, e.g. \"example.com\"."
  type        = string
}

variable "seerr_domain_name" {
  description = "Name Seerr (the request page) is served from, e.g. \"requests.example.com\". A CNAME to domain_name."
  type        = string
}

variable "home_ip" {
  description = "The home connection's public IP, used only when the records are first created. The server keeps them current after that."
  type        = string
}

variable "alert_email" {
  description = "Where the outside check emails, e.g. \"you@example.com\". While SES is in the sandbox it has to be on hosted_zone_name's domain."
  type        = string

  validation {
    condition     = endswith(lower(var.alert_email), "@${lower(var.hosted_zone_name)}")
    error_message = "alert_email has to be an address on hosted_zone_name's domain: SES is in the sandbox."
  }
}

variable "github_repository" {
  description = "Optional: the GitHub repository whose main branch may deploy the outside check's code, as \"owner/name\". Empty: no deploy role, and no GitHub OIDC provider needed in the account."
  type        = string
  default     = ""
}

variable "aws_region" {
  description = "Region for the AWS provider. Route 53 itself is global."
  type        = string
}

variable "tags" {
  description = "Extra tags for every resource."
  type        = map(string)
  default     = {}
}
