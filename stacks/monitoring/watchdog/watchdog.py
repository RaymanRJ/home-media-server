"""The watchdog: every minute it looks at each container's health check and the
nightly backup's result, restarts a container that has turned unhealthy, and emails when something
breaks and again when it's fixed.

  - A container whose health check fails (Docker marks it unhealthy after three failures in a row)
    is restarted, at most MAX_RESTARTS times in a row, at least RESTART_GAP apart; then it's left
    alone and one email says so. Docker itself only restarts a container whose process exits, so a
    hung app (Scrutiny's start after a reboot) stays "Up" without this.
  - A container that stops (exited, not restarted by Docker) is only reported: it may have been
    stopped by hand. Only after it has been down for STOPPED_CHECKS checks, so the order Compose
    stops things in at shutdown sends nothing.
  - The backup is reported when its last run failed or none has succeeded for BACKUP_MAX_AGE.

Emails go only when a state changes. One that can't be sent (no internet) is kept and tried again
each minute. Nothing is stored on disk: after a restart of the watchdog itself, a backup that is
still failing is reported again.

Talks to Docker through its socket, mounted read-write (restarting needs it), with the standard
library only. SMTP_URL is the Shoutrrr-style URL Scrutiny and the weekly report use.
"""
import http.client
import json
import os
import signal
import smtplib
import socket
import ssl
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

INTERVAL = 60  # seconds between checks
MAX_RESTARTS = 3
RESTART_GAP = 10 * 60  # long enough for the health check's start period and three failures
STOPPED_CHECKS = 3
BACKUP_STATUS = "/status/backup-status.json"
BACKUP_MAX_AGE = 36 * 3600  # as the weekly report: one missed night plus a slow one
HEARTBEAT = "/tmp/heartbeat"  # the watchdog's own health check reads its age

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # stop at once on `docker stop`


def log(text):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}", flush=True)


# --- Docker ---------------------------------------------------------------------------------------

class DockerConnection(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost", timeout=120)  # a restart waits for the container to stop

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/var/run/docker.sock")


def docker(method, path):
    c = DockerConnection()
    try:
        c.request(method, path)
        r = c.getresponse()
        body = r.read()
        if r.status >= 300:
            raise RuntimeError(f"Docker {method} {path}: {r.status} {body[:200]!r}")
        return json.loads(body) if body else None
    finally:
        c.close()


def containers():
    """The Compose file's long-running services (not one-off `run`s like the backup), by name."""
    filters = urllib.parse.quote(json.dumps({"label": ["com.docker.compose.oneoff=False"]}))
    out = {}
    for c in docker("GET", f"/containers/json?all=1&filters={filters}"):
        status = c.get("Status", "")
        health = ("unhealthy" if "(unhealthy)" in status else "healthy" if "(healthy)" in status
                  else "starting" if "health: starting" in status else None)
        out[c["Names"][0].lstrip("/")] = {"id": c["Id"], "running": c["State"] == "running",
                                          "health": health, "status": status}
    return out


def last_check(cid):
    """The failing health check's own output, for the email."""
    entries = ((docker("GET", f"/containers/{cid}/json").get("State") or {}).get("Health") or {}).get("Log") or []
    return (entries[-1].get("Output") or "").strip()[:500] if entries else ""


# --- Email ----------------------------------------------------------------------------------------

outbox = []  # (subject, body) not sent yet


def send_pending():
    u = urllib.parse.urlsplit(os.environ["SMTP_URL"])
    q = urllib.parse.parse_qs(u.query)
    while outbox:
        subject, body = outbox[0]
        msg = EmailMessage()
        msg["From"] = f"Media Server <{q['from'][0]}>"
        msg["To"] = q["to"][0]
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=q["from"][0].split("@")[1])
        msg.set_content(body)
        try:
            with smtplib.SMTP(u.hostname, u.port or 587, timeout=60) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(urllib.parse.unquote(u.username), urllib.parse.unquote(u.password))
                s.send_message(msg)
        except (OSError, smtplib.SMTPException) as e:
            log(f"couldn't send '{subject}', will retry: {e}")
            return
        log(f"sent: {subject}")
        outbox.pop(0)


def email(subject, body):
    log(subject)
    outbox.append((subject, f"{body}\n\nSent {datetime.now():%a %b %-d, %H:%M} by the watchdog."))


# --- Checks ---------------------------------------------------------------------------------------

state = {}  # container name -> {"restarts", "last_restart", "gave_up", "stopped_checks", "reported_stopped"}


def check_containers():
    for name, c in containers().items():
        s = state.setdefault(name, {"restarts": 0, "last_restart": 0, "gave_up": False,
                                    "stopped_checks": 0, "reported_stopped": False})
        if not c["running"]:
            s["stopped_checks"] += 1
            if s["stopped_checks"] == STOPPED_CHECKS:
                s["reported_stopped"] = True
                email(f"{name} has stopped",
                      f"The {name} container isn't running ({c['status']}). Docker restarts a container that "
                      "crashes, so it was stopped, or failed to start again.\n\n"
                      f"See why: sudo docker logs --tail 50 {name}\n"
                      f"Start it: sudo docker start {name}")
            continue
        if s["reported_stopped"]:
            email(f"{name} is running again", f"The {name} container is running again.")
        s["stopped_checks"], s["reported_stopped"] = 0, False

        if c["health"] == "healthy" and (s["restarts"] or s["gave_up"]):
            email(f"{name} is healthy again",
                  f"{name} passes its health check again, after {s['restarts']} restart{'s' * (s['restarts'] != 1)}.")
            s.update(restarts=0, gave_up=False)
        elif c["health"] == "unhealthy" and time.time() - s["last_restart"] >= RESTART_GAP:
            output = last_check(c["id"])
            if s["restarts"] < MAX_RESTARTS:
                docker("POST", f"/containers/{c['id']}/restart?t=30")
                s["restarts"] += 1
                s["last_restart"] = time.time()
                email(f"{name} restarted: it failed its health check",
                      f"{name} failed its health check three times in a row, so it was restarted "
                      f"(restart {s['restarts']} of at most {MAX_RESTARTS}). Another email follows when it's "
                      f"healthy again.\n\nThe check's last output:\n{output or '(none)'}\n\n"
                      f"Its log: sudo docker logs --tail 50 {name}")
            elif not s["gave_up"]:
                s["gave_up"] = True
                email(f"{name} is still unhealthy: left alone",
                      f"{name} is still failing its health check after {MAX_RESTARTS} restarts, so the watchdog has "
                      "stopped restarting it. It needs looking at.\n\n"
                      f"The check's last output:\n{output or '(none)'}\n\nIts log: sudo docker logs --tail 50 {name}")


backup_kind = None  # "ok", "failed", "stale" or "missing", as last reported


def check_backup():
    global backup_kind
    try:
        b = json.load(open(BACKUP_STATUS))
        when = datetime.fromisoformat(b["time"].replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError):
        b, when = None, None
    age = (datetime.now(timezone.utc) - when).total_seconds() if when else None
    kind = ("missing" if b is None else "failed" if not b.get("ok")
            else "stale" if age > BACKUP_MAX_AGE else "ok")
    if kind == backup_kind or (backup_kind is None and kind == "ok"):
        backup_kind = kind
        return
    local = when.astimezone().strftime("%a %b %-d, %H:%M") if when else "never"
    see = "See why: journalctl -u media-backup.service -n 50"
    if kind == "ok":
        email("The backup works again", f"The backup at {local} succeeded.")
    elif kind == "failed":
        email("The backup failed", f"The backup at {local} failed: {b.get('error', '(no reason given)')}\n\n{see}")
    elif kind == "stale":
        email("No backup for over a day and a half",
              f"The last backup that ran was at {local}, and it succeeded, but none has run since. The timer "
              f"(media-backup.timer) may be off, or a run may be stuck.\n\n{see}\n"
              "Timer: systemctl list-timers media-backup.timer")
    else:
        email("No backup status", f"{BACKUP_STATUS} is missing or unreadable, so there's no record of a backup.\n\n{see}")
    backup_kind = kind


if __name__ == "__main__":
    log("started")
    while True:
        for check in (check_containers, check_backup):
            try:
                check()
            except Exception as e:  # Docker busy at boot, say: try again next minute
                log(f"{check.__name__} failed: {e}")
        send_pending()
        open(HEARTBEAT, "w").close()
        time.sleep(INTERVAL)
