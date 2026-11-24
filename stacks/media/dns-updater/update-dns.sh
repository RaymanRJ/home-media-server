#!/bin/sh
# Keeps the A records for $JELLYFIN_DOMAIN and www.$JELLYFIN_DOMAIN on the home connection's public
# IP, in place of the router's dynamic DNS. Runs in the dns-updater container, checking every five
# minutes, and only writes to Route 53 when the IP has changed.
#
# AWS credentials come from IAM Roles Anywhere: the AWS CLI runs the signing helper, which signs a
# request with the server's certificate (mounted at /certs) and gets one-hour credentials back.
set -u

cat > /tmp/aws-config <<EOF
[default]
credential_process = /usr/local/bin/aws_signing_helper credential-process --certificate /certs/cert.pem --private-key /certs/key.pem --trust-anchor-arn $TRUST_ANCHOR_ARN --profile-arn $PROFILE_ARN --role-arn $ROLE_ARN
EOF
export AWS_CONFIG_FILE=/tmp/aws-config

names="$JELLYFIN_DOMAIN www.$JELLYFIN_DOMAIN"

change() {
  printf '{"Action":"UPSERT","ResourceRecordSet":{"Name":"%s","Type":"A","TTL":300,"ResourceRecords":[{"Value":"%s"}]}}' "$1" "$2"
}

while true; do
  ip=$(curl -fsS --max-time 10 https://checkip.amazonaws.com | tr -d '[:space:]')
  case "$ip" in
    '' | *[!0-9.]*)
      echo "couldn't get the public IP (got '$ip'); trying again later"
      ;;
    *)
      # How many of the records already hold this IP. Names come back lowercase with a final dot.
      query="length(ResourceRecordSets[?Type=='A' && ResourceRecords[0].Value=='$ip' && (Name=='$JELLYFIN_DOMAIN.' || Name=='www.$JELLYFIN_DOMAIN.')])"
      current=$(aws route53 list-resource-record-sets --hosted-zone-id "$HOSTED_ZONE_ID" \
        --query "$query" --output text)
      if [ "$current" = "2" ]; then
        touch /tmp/healthy  # for the container's health check
      elif [ -z "$current" ]; then
        echo "couldn't read the records from Route 53; trying again later"
      else
        batch='{"Changes":['
        sep=''
        for name in $names; do
          batch="$batch$sep$(change "$name" "$ip")"
          sep=','
        done
        batch="$batch]}"
        if aws route53 change-resource-record-sets --hosted-zone-id "$HOSTED_ZONE_ID" \
          --change-batch "$batch" --output text --query 'ChangeInfo.Status' >/dev/null; then
          echo "set $names to $ip"
          touch /tmp/healthy
        else
          echo "couldn't update the records; trying again later"
        fi
      fi
      ;;
  esac
  sleep 300
done
