"""The outside check: a Lambda that every five minutes opens
https://$DOMAIN/health from AWS, the way a viewer outside the house would, and emails when that
stops working and again when it's back.

Each run sorts the result into one kind:

  ok        a valid certificate for the name, and Jellyfin answers Healthy
  expiring  ok, but the certificate expires within EXPIRY_WARN_DAYS: Caddy isn't renewing it
  jellyfin  Caddy answers with a valid certificate, but Jellyfin doesn't (502/503 or unhealthy)
  tls       something other than Caddy answers 443: after a power cut, the router's own page
  offline   nothing answers: the power, the internet connection or the server is down
  dns       the name doesn't resolve

It emails only when the kind changes and the new one has been seen twice in a row (one check for
a return to ok), so the minute of 502s after a reboot doesn't send anything. The last reported
kind is kept in the SSM parameter $STATE_PARAMETER as JSON.

It also serves the maintenance page. When an offline
or tls result is about to be emailed, it points the A records for the Jellyfin name and www. at an
API Gateway custom domain (an alias), and API Gateway invokes this same function, which answers
any request with maintenance.html and a 503, with a timer from when the outage was first seen. While the names are on the page it tests the house
directly, at the IP it saved, and puts the home IP back when the house answers ok. If the
dns-updater on the server gets there first (it rewrites any record that doesn't hold the home IP),
the names are already home and this only notices.

Standard library plus boto3, which the Lambda runtime includes.
"""

import datetime as dt
import http.client
import json
import os
import socket
import ssl

import boto3

DOMAIN = os.environ["DOMAIN"]
STATE_PARAMETER = os.environ["STATE_PARAMETER"]
EXPIRY_WARN_DAYS = 10
TIMEOUT = 10  # seconds, for connecting and for the answer
HOSTED_ZONE_ID = os.environ["HOSTED_ZONE_ID"]
# Record name -> {"dns": the API Gateway domain's target, "zone": its hosted zone}, for the alias.
PAGE_TARGETS = json.loads(os.environ["PAGE_TARGETS"])

with open(os.path.join(os.path.dirname(__file__), "maintenance.html"), encoding="utf-8") as f:
    PAGE = f.read()

ssm = boto3.client("ssm")
route53 = boto3.client("route53")
ses = boto3.client("ses", region_name=os.environ["SES_REGION"])

# For each kind: the email's subject, and what it means and what to do.
MESSAGES = {
    "ok": ("Jellyfin is back", "Jellyfin answers from outside the house again."),
    "expiring": (
        "Jellyfin's certificate is about to expire",
        "Caddy should renew the certificate about a month before it expires, so it's failing to. "
        "Check `sudo docker logs caddy` on the server for ACME errors; the usual cause is port 80 "
        "not reaching Caddy (docs/router.md).",
    ),
    "jellyfin": (
        "Jellyfin is down (Caddy is up)",
        "Caddy answers with a valid certificate, so the router and DNS are fine, but Jellyfin "
        "behind it doesn't. On the server: `sudo docker ps` and `sudo docker logs jellyfin`.",
    ),
    "tls": (
        "Jellyfin isn't reachable: something else is answering on 443",
        "The HTTPS connection failed, so whatever answers isn't Caddy. After a power cut that's "
        "the router's own login page, still open to the internet. Fix: in the router, NAT "
        "Forwarding > Port Forwarding, switch the 80 and 443 rules off, save, and on again "
        "(docs/router.md, \"After a power cut\").",
    ),
    "offline": (
        "Jellyfin isn't reachable: home is offline",
        "Nothing answers at the home IP: the power, the internet connection, the router or the "
        "server is down. If the server is up, check that the router's 80 and 443 forwards still "
        "point at its LAN IP.",
    ),
    "dns": (
        "Jellyfin isn't reachable: the name doesn't resolve",
        "The name has no address. The A records are in Route 53, kept current by the dns-updater "
        "on the server; check `sudo docker logs dns-updater` and the records.",
    ),
}


def check(ip=None):
    """Return (kind, detail, ip) for one attempt. Given an ip, skips the lookup and connects there
    (the name points at the maintenance page then), still verifying the certificate for DOMAIN."""
    if ip is None:
        try:
            ip = socket.gethostbyname(DOMAIN)
        except socket.gaierror as e:
            return "dns", f"lookup failed: {e}", None

    conn = http.client.HTTPSConnection(DOMAIN, timeout=TIMEOUT)
    try:
        sock = socket.create_connection((ip, 443), timeout=TIMEOUT)
        conn.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=DOMAIN)
        conn.request("GET", "/health", headers={"User-Agent": "media-outside-check"})
        cert = conn.sock.getpeercert()
        resp = conn.getresponse()
        body = resp.read(200).decode(errors="replace").strip()
    except ssl.SSLError as e:
        return "tls", f"{ip}: {getattr(e, 'verify_message', None) or e}", ip
    except (TimeoutError, OSError) as e:
        return "offline", f"{ip}: {e}", ip
    finally:
        conn.close()

    if resp.status != 200 or body != "Healthy":
        return "jellyfin", f"{ip}: HTTP {resp.status} {body[:80]!r}", ip

    expires = dt.datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), dt.timezone.utc)
    days = (expires - dt.datetime.now(dt.timezone.utc)).days
    if days < EXPIRY_WARN_DAYS:
        return "expiring", f"{ip}: the certificate expires {expires:%Y-%m-%d} ({days} days)", ip
    return "ok", f"{ip}: certificate valid until {expires:%Y-%m-%d}", ip


def records_state():
    """Where the A records point: "page" (an alias to API Gateway), "home" (plain A records), or
    None if they can't be read."""
    try:
        sets = route53.list_resource_record_sets(
            HostedZoneId=HOSTED_ZONE_ID, StartRecordName=DOMAIN, StartRecordType="A", MaxItems="1"
        )["ResourceRecordSets"]
    except Exception as e:
        print(f"couldn't read the records: {e}")
        return None
    if not sets or sets[0]["Name"] != DOMAIN.lower() + "." or sets[0]["Type"] != "A":
        return None
    return "page" if "AliasTarget" in sets[0] else "home"


def change_records(make):
    """UPSERT every record in PAGE_TARGETS; make(name, target) gives its ResourceRecordSet."""
    route53.change_resource_record_sets(
        HostedZoneId=HOSTED_ZONE_ID,
        ChangeBatch={
            "Changes": [
                {"Action": "UPSERT", "ResourceRecordSet": make(name, target)}
                for name, target in PAGE_TARGETS.items()
            ]
        },
    )


def to_page():
    change_records(
        lambda name, t: {
            "Name": name,
            "Type": "A",
            "AliasTarget": {"HostedZoneId": t["zone"], "DNSName": t["dns"], "EvaluateTargetHealth": False},
        }
    )


def to_home(ip):
    change_records(
        lambda name, t: {"Name": name, "Type": "A", "TTL": 300, "ResourceRecords": [{"Value": ip}]}
    )


def send(old, new, detail, since, now, extra=None):
    subject, explanation = MESSAGES[new]
    lines = [explanation, "", f"Checked https://{DOMAIN}/health from AWS at {now:%Y-%m-%d %H:%M} UTC."]
    lines.append(f"Result: {new} ({detail})")
    if extra:
        lines.append(extra)
    if since:
        took = now - dt.datetime.fromisoformat(since)
        hours, rest = divmod(int(took.total_seconds()) // 60, 60)
        lines.append(f"Before that: {old}, since {since[:16].replace('T', ' ')} UTC ({hours} h {rest} min).")
    ses.send_email(
        Source=os.environ["FROM"],
        Destination={"ToAddresses": [os.environ["TO"]]},
        Message={
            "Subject": {"Data": subject},
            "Body": {"Text": {"Data": "\n".join(lines) + "\n"}},
        },
    )


def handler(event, context):
    if "requestContext" in event:  # API Gateway: a browser on one of the names, not the schedule
        # The page counts up from when the outage was first seen; without it, it shows no timer.
        try:
            state = json.loads(ssm.get_parameter(Name=STATE_PARAMETER)["Parameter"]["Value"])
            down_since = state.get("down_since") or ""
        except Exception as e:
            print(f"couldn't read the state: {e}")
            down_since = ""
        return {
            "statusCode": 503,
            "headers": {
                "Content-Type": "text/html; charset=utf-8",
                "Retry-After": "600",
                "Cache-Control": "no-store",
            },
            "body": PAGE.replace("{{DOWN_SINCE}}", down_since),
        }

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    raw = ssm.get_parameter(Name=STATE_PARAMETER)["Parameter"]["Value"]
    state = json.loads(raw)
    reported = state.get("reported", "ok")
    # armed: whether an outage may switch the names to the page. After a power cut the router can
    # answer 443 itself (tls) while the server is up; the dns-updater points the names home, the
    # check would switch them away again, and so on. So it switches at most once per outage, and
    # is armed again only after seeing ok.
    armed = state.get("armed", True)
    page_ip = state.get("page_ip")  # the home IP saved when the names went to the page
    down_since = state.get("down_since")  # when that outage was first seen, for the page's timer
    records = records_state()
    was_on_page = records == "page" or page_ip is not None

    ip = None
    if records == "page":
        if not page_ip:
            print("the names are on the page, but not by this function; leaving them")
            return {"kind": "page", "detail": "switched by hand"}
        # The name points at the page now, so test the house directly.
        kind, detail, _ = check(page_ip)
        if kind in ("ok", "expiring"):
            to_home(page_ip)
            records, page_ip, down_since = "home", None, None
    else:
        if records == "home":
            page_ip, down_since = None, None  # the dns-updater pointed the names home
        kind, detail, ip = check()
    print(json.dumps({"kind": kind, "detail": detail, "records": records}))

    if kind == reported:
        state.update(pending=None, count=0, pending_since=None)
    else:
        count = state.get("count", 0) + 1 if state.get("pending") == kind else 1
        state.update(pending=kind, count=count)
        if count == 1:
            state["pending_since"] = now.isoformat()
        if count >= (1 if kind == "ok" else 2):
            extra = None
            if kind in ("offline", "tls") and records == "home" and armed and ip:
                try:
                    to_page()
                    page_ip, armed = ip, False
                    down_since = state.get("pending_since") or now.isoformat()
                    extra = (
                        "Browsers now get the maintenance page; the names point home again when the "
                        "server is back (dns-updater)."
                    )
                except Exception as e:
                    extra = f"Couldn't switch to the maintenance page: {e}"
            elif kind == "ok" and was_on_page:
                extra = "The names were on the maintenance page during the outage; they point home again."
            send(reported, kind, detail, state.get("since"), now, extra)
            state = {"reported": kind, "since": now.isoformat(), "pending": None, "count": 0}

    if kind in ("ok", "expiring") and records == "home":
        armed = True
    state.update(armed=armed, page_ip=page_ip, down_since=down_since)

    new_raw = json.dumps(state, sort_keys=True)
    if new_raw != raw:
        ssm.put_parameter(Name=STATE_PARAMETER, Value=new_raw, Type="String", Overwrite=True)
    return {"kind": kind, "detail": detail}
