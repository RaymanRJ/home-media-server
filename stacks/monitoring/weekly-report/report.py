"""The weekly email report: last week's viewing per user (totals only, never titles), the server's
load, disk space and alerts, and the drives' health. Reads only local data and sends one email.

Sources, all on this server:
  - Jellyfin's Playback Reporting plugin, through Jellyfin's API (JELLYFIN_API_KEY).
  - Beszel's database, copied to /tmp first so the running hub is never touched.
  - Scrutiny's API.
  - The nightly backup's status file (/status/backup-status.json).
  - Sonarr's and Radarr's APIs (SONARR_API_KEY, RADARR_API_KEY), for downloads removed that week,
    with the reason from a copy of Cleanuparr's event database.
  - Seerr's API (SEERR_API_KEY), for seasons missing episodes, checked against Jellyfin and Sonarr.
It also writes, to /out (all three are backed up nightly; only the first is emailed):
  - media-inventory.csv: every film, show season, audiobook, book and album, with the drive(s)
    holding it.
  - all-files.csv: every file on every data drive, minus OS clutter and metadata sidecars.
  - <PRIVATE_DIR>-files.csv: if PRIVATE_DIR is set, the files in that top-level folder of the
    drives (e.g. a private share), kept apart from everything else.
SMTP_URL is the same Shoutrrr-style URL Scrutiny uses: smtp://user:pass@host:port/?from=..&to=..
DRY_RUN=1 prints the email's HTML instead of sending it. INCLUDE_TODAY=1 ends the period now instead
of at midnight, for a test run.
"""
import csv
import html
import io
import json
import os
import shutil
import smtplib
import sqlite3
import ssl
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

JELLYFIN = os.environ.get("JELLYFIN_URL", "http://localhost:8096")
SCRUTINY = os.environ.get("SCRUTINY_URL", "http://localhost:8080")
BESZEL_DB = os.environ.get("BESZEL_DB", "/beszel/data.db")
SONARR = os.environ.get("SONARR_URL", "http://localhost:8989")
RADARR = os.environ.get("RADARR_URL", "http://localhost:7878")
SEERR = os.environ.get("SEERR_URL", "http://localhost:5055")
CLEANUPARR_DIR = os.environ.get("CLEANUPARR_DIR", "/cleanuparr")
DAYS = int(os.environ.get("DAYS", "7"))

# The data drives, each mounted whole and read-only at /drives/<label> (compose.override.yaml). The
# pool's drives keep the library in a `media` folder; a drive without one is listed but not pooled.
DRIVES = sorted(os.listdir("/drives")) if os.path.isdir("/drives") else []
BRANCHES = [d for d in DRIVES if os.path.isdir(f"/drives/{d}/media")]
PRIVATE = os.environ.get("PRIVATE_DIR", "")  # a top-level folder listed in its own file, if set

# Left out of the file lists: OS clutter, Jellyfin/Kodi metadata, subtitle sidecars and standard
# artwork. Anything else stays in, so forgotten documents or photos still show up.
SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".directory", "@eaDir"}
SKIP_EXT = {".nfo", ".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup"}
ARTWORK = {"poster", "folder", "fanart", "banner", "cover", "backdrop", "thumb", "logo", "clearart",
           "clearlogo", "landscape", "disc", "discart", "season-all-poster"}
SKIP_DIRS = {"lost+found", "Pending Delete", ".Trash-1000", ".Trashes", ".Spotlight-V100", ".fseventsd",
             ".AppleDouble", ".TemporaryItems"}
# Downloads in progress and Sonarr's/Radarr's recycle bin: passing through.
SKIP_PATHS = {os.path.join("media", "downloads"), os.path.join("media", "Recycle Bin")}

# Scrutiny's container sees each drive under a fixed name (compose.override.yaml maps them by
# serial); SCRUTINY_DRIVE_LABELS names them, e.g. "sda=system,sdb=disk1".
SCRUTINY_DRIVES = dict(pair.split("=", 1) for pair in os.environ.get("SCRUTINY_DRIVE_LABELS", "").split(",")
                       if "=" in pair)

now = datetime.now().astimezone()
midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
end = now if os.environ.get("INCLUDE_TODAY") else midnight  # normally up to midnight this morning
start = midnight - timedelta(days=DAYS - 1 if os.environ.get("INCLUDE_TODAY") else DAYS)
days = [(start + timedelta(days=i)).date() for i in range(DAYS)]


def get_json(url, body=None, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def local(utc_text):
    """Beszel's UTC timestamps ('2026-10-25 03:23:01.360Z') as local time."""
    return datetime.fromisoformat(utc_text.replace("Z", "+00:00")).astimezone(now.tzinfo).strftime("%a %b %-d, %H:%M")


def hours(seconds):
    return f"{seconds / 3600:.1f} h"


# --- Viewing --------------------------------------------------------------------------------------

def viewing():
    key = os.environ["JELLYFIN_API_KEY"]
    # The plugin stores local times as text, so compare as text in the same format.
    sql = ("SELECT UserId, PlaybackMethod, PlayDuration, DateCreated, ClientName, DeviceName FROM PlaybackActivity "
           f"WHERE DateCreated >= '{start:%Y-%m-%d %H:%M:%S}' AND DateCreated < '{end:%Y-%m-%d %H:%M:%S}'")
    r = get_json(f"{JELLYFIN}/user_usage_stats/submit_custom_query",
                 {"CustomQueryString": sql, "ReplaceUserId": True},
                 {"Authorization": f'MediaBrowser Token="{key}"'})
    first = get_json(f"{JELLYFIN}/user_usage_stats/submit_custom_query",
                     {"CustomQueryString": "SELECT MIN(DateCreated) FROM PlaybackActivity", "ReplaceUserId": False},
                     {"Authorization": f'MediaBrowser Token="{key}"'}).get("results") or [[None]]
    recording_since = datetime.strptime(first[0][0][:10], "%Y-%m-%d").date() if first[0][0] else None
    new = lambda: {"plays": 0, "secs": 0, **{k: 0 for k in KINDS}}
    users, devices = defaultdict(new), defaultdict(new)
    per_day = defaultdict(int)
    for user, method, secs, created, client, device in r.get("results") or []:
        name = client if not device or device == client else f"{client} · {device}"
        for u in (users[user], devices[(user, name)]):
            u["plays"] += 1
            u["secs"] += int(secs or 0)
            u[kind(method)] += 1
        per_day[datetime.strptime(created[:10], "%Y-%m-%d").date()] += int(secs or 0)
    return dict(users), dict(devices), per_day, recording_since


# How a play reached the device, from the plugin's PlaybackMethod: "DirectPlay", "DirectStream", or
# "Transcode (v:direct a:aac)" when only the audio or container was converted, "(v:h264 ...)" when
# the video was re-encoded.
KINDS = {"direct": "Direct", "copied": "Video copied", "video": "Transcoded"}


def kind(method):
    method = method or ""
    return "direct" if method.startswith("Direct") else "copied" if "v:direct" in method else "video"


# --- Added ----------------------------------------------------------------------------------------

def added():
    """What arrived in Jellyfin this week, by its date added: from Sonarr and Radarr or copied in by
    hand. A library that was reorganized that week counts as added too."""
    key = os.environ["JELLYFIN_API_KEY"]
    q = urllib.parse.urlencode({"Recursive": "true", "IncludeItemTypes": "Movie,Episode,MusicAlbum,Book,AudioBook",
                                "Fields": "DateCreated,MediaSources,SeriesName", "SortBy": "DateCreated",
                                "SortOrder": "Descending", "Limit": "5000", "CollapseBoxSetItems": "false"})
    films, shows, counts, total = [], defaultdict(lambda: [0, 0]), defaultdict(int), 0
    for it in get_json(f"{JELLYFIN}/Items?{q}", headers={"Authorization": f'MediaBrowser Token="{key}"'})["Items"]:
        # Jellyfin's UTC times have seven decimal places, more than fromisoformat takes.
        when = datetime.fromisoformat(it["DateCreated"][:19]).replace(tzinfo=timezone.utc)
        if when >= end:
            continue
        if when < start:
            break
        size = sum(m.get("Size") or 0 for m in it.get("MediaSources") or [])
        total += size
        counts[it["Type"]] += 1
        if it["Type"] == "Movie":
            films.append((f'{it["Name"]} ({it["ProductionYear"]})' if it.get("ProductionYear") else it["Name"], size))
        elif it["Type"] == "Episode":
            s = shows[it.get("SeriesName") or "?"]
            s[0] += 1
            s[1] += size
    return films, dict(shows), counts, total


# --- Downloads (Sonarr, Radarr, Cleanuparr) -------------------------------------------------------

# Cleanuparr's reasons for removing a download, in words.
REASONS = {"stalled": "stalled", "downloadingmetadata": "never got its file list", "slowtime": "too slow",
           "slowspeed": "too slow", "failedimport": "failed to import", "allfilesblocked": "only junk or malware",
           "atleastonefileblocked": "junk or malware", "allfilesskipped": "every file skipped",
           "allfilesskippedbyqbit": "every file skipped", "errored": "errored in qBittorrent"}


def cleanuparr():
    """Cleanuparr's removals this week, as torrent hash -> reason, and whether it's in Dry Run (it
    only logs then). Its databases are copied to /tmp first, as Beszel's are."""
    for name in ("events.db", "cleanuparr.db"):
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(f"{CLEANUPARR_DIR}/{name}{suffix}"):
                shutil.copy(f"{CLEANUPARR_DIR}/{name}{suffix}", f"/tmp/{name}{suffix}")
    since = start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    until = end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    reasons = {h.lower(): (r or "").lower() for h, r in sqlite3.connect("/tmp/events.db").execute(
        "SELECT item_hash, delete_reason FROM events WHERE event_type = 'queueitemdeleted' AND is_dry_run = 0 "
        "AND timestamp >= ? AND timestamp < ?", (since, until)) if h}
    dry_run = sqlite3.connect("/tmp/cleanuparr.db").execute("SELECT dry_run FROM general_configs").fetchone()
    return reasons, bool(dry_run and dry_run[0])


def downloads():
    """Every download that failed or was removed this week, by Cleanuparr or by hand (Sonarr and Radarr
    then search again), with what's become of it: replaced, downloading again, or still missing."""
    reasons, dry_run = cleanuparr() if os.path.isdir(CLEANUPARR_DIR) else ({}, None)
    since = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    found = {}  # what -> [reasons, status, dates]: one row however many times it was removed
    for app, url, kind in (("Sonarr", SONARR, "episode"), ("Radarr", RADARR, "movie")):
        auth = {"X-Api-Key": os.environ[f"{app.upper()}_API_KEY"]}
        include = "includeSeries=true" if kind == "episode" else "includeMovie=true"
        by_download = defaultdict(list)
        for h in get_json(f"{url}/api/v3/history/since?date={since}&eventType=downloadFailed&{include}", headers=auth):
            if datetime.fromisoformat(h["date"].replace("Z", "+00:00")) < end:
                by_download[h["downloadId"]].append(h)
        queued = {r.get(f"{kind}Id") for r in get_json(f"{url}/api/v3/queue?pageSize=1000", headers=auth)["records"]}
        for download_id, hs in by_download.items():
            ids = sorted({h[f"{kind}Id"] for h in hs})
            if kind == "episode":
                eps = get_json(f"{url}/api/v3/episode?" + urllib.parse.urlencode([("episodeIds", i) for i in ids]), headers=auth)
                seasons = sorted({e["seasonNumber"] for e in eps})
                what = hs[0]["series"]["title"] + (f' S{eps[0]["seasonNumber"]:02}E{eps[0]["episodeNumber"]:02}' if len(eps) == 1
                                                   else f' S{seasons[0]:02}, {len(eps)} episodes' if len(seasons) == 1
                                                   else f", {len(eps)} episodes")
                have = sum(1 for e in eps if e["hasFile"])
            else:
                m = get_json(f"{url}/api/v3/movie/{ids[0]}", headers=auth)
                what, have = f'{m["title"]} ({m["year"]})', int(bool(m.get("hasFile")))
            status = ("replaced" if have == len(ids) else "downloading again" if queued & set(ids)
                      else f"{have} of {len(ids)} replaced" if have else "still missing")
            reason = reasons.get(download_id.lower())
            when = datetime.fromisoformat(hs[0]["date"].replace("Z", "+00:00")).astimezone(now.tzinfo)
            f = found.setdefault(what, [[], status, []])
            f[0].append(REASONS.get(reason, reason) if reason is not None else "removed by hand or failed")
            f[2].append(when)
    rows = [(what, ", ".join(dict.fromkeys(reasons)), status, ", ".join(f"{d:%a %-d}" for d in sorted(dates)))
            for what, (reasons, status, dates) in found.items()]
    rows.sort(key=lambda r: (r[2] == "replaced", r[2] == "downloading again", r[0].lower()))
    return rows, dry_run


# --- Incomplete shows (Seerr, Jellyfin, Sonarr) ---------------------------------------------------

# Seasons TMDB numbers differently from the files, so they always look incomplete. (TMDB id, season.)
GAPS_IGNORE = {(105009, 1)}  # Tokyo Revengers: TMDB's season 1 is every season


def ranges(ns):
    """[1, 2, 3, 5] as 'E1–3, E5'."""
    out, ns = [], sorted(ns)
    for n in ns:
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return ", ".join(f"E{a}" if a == b else f"E{a}–{b}" for a, b in out)


def incomplete():
    """Every season in Jellyfin missing episodes that have aired, whether Sonarr follows the show or it
    was copied in by hand. Seerr marks those seasons partly available (whole missing seasons, often on
    purpose, are left out); its TMDB episode list is compared with the files Jellyfin has. A season
    Sonarr follows and counts complete is left out: TVDB, which Sonarr uses, numbers it differently."""
    seerr = {"X-Api-Key": os.environ["SEERR_API_KEY"]}
    jf = {"Authorization": f'MediaBrowser Token="{os.environ["JELLYFIN_API_KEY"]}"'}
    items = lambda types, fields: get_json(f"{JELLYFIN}/Items?" + urllib.parse.urlencode(
        {"Recursive": "true", "IncludeItemTypes": types, "Fields": fields}), headers=jf)["Items"]
    tmdb = {s["Id"]: (int(s["ProviderIds"]["Tmdb"]), s["Name"]) for s in items("Series", "ProviderIds")
            if (s.get("ProviderIds") or {}).get("Tmdb")}
    names = dict(tmdb.values())
    have = defaultdict(set)  # (TMDB id, season) -> episode numbers on disk; a double episode counts both
    for e in items("Episode", "ProviderIds"):
        if e.get("SeriesId") in tmdb and e.get("IndexNumber") is not None:
            for n in range(e["IndexNumber"], (e.get("IndexNumberEnd") or e["IndexNumber"]) + 1):
                have[(tmdb[e["SeriesId"]][0], e.get("ParentIndexNumber"))].add(n)
    sonarr = {"X-Api-Key": os.environ["SONARR_API_KEY"]}
    followed = {s["tmdbId"]: s["id"] for s in get_json(f"{SONARR}/api/v3/series", headers=sonarr) if s.get("tmdbId")}
    today, rows = now.date().isoformat(), []
    for m in get_json(f"{SEERR}/api/v1/media?filter=partial&take=1000", headers=seerr)["results"]:
        t = m["tmdbId"]
        if m["mediaType"] != "tv" or t not in names:
            continue
        for s in m.get("seasons") or []:
            n = s["seasonNumber"]
            if s["status"] != 4 or n == 0 or (t, n) in GAPS_IGNORE:  # 4: partly available
                continue
            eps = get_json(f"{SEERR}/api/v1/tv/{t}/season/{n}", headers=seerr)["episodes"]
            aired = {e["episodeNumber"] for e in eps if e.get("airDate") and e["airDate"] <= today}
            missing = aired - have[(t, n)]
            if not missing:  # only episodes still to air
                continue
            if t not in followed:
                sonarr_status = "not in Sonarr"
            else:
                wanted = [e for e in get_json(f"{SONARR}/api/v3/episode?seriesId={followed[t]}&seasonNumber={n}", headers=sonarr)
                          if not e["hasFile"] and e.get("airDateUtc") and e["airDateUtc"][:10] <= today]
                if not wanted:  # complete by Sonarr's (TVDB's) numbering: TMDB splits the seasons differently
                    continue
                sonarr_status = ("searching" if wanted and all(e["monitored"] for e in wanted)
                                 else "partly monitored" if any(e["monitored"] for e in wanted) else "not monitored")
            rows.append((f"{names[t]} S{n:02}", f"{len(aired) - len(missing)} of {len(aired)}", ranges(missing), sonarr_status))
    rows.sort(key=lambda r: r[0].lower())
    return rows


# --- Server (Beszel) ------------------------------------------------------------------------------

def beszel():
    # Copy the database and its write-ahead log so the read sees everything without locking the hub.
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(BESZEL_DB + suffix):
            shutil.copy(BESZEL_DB + suffix, "/tmp/beszel.db" + suffix)
    db = sqlite3.connect("/tmp/beszel.db")
    since = start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    until = end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    load = defaultdict(lambda: {"cpu": [], "mem": [], "temp": []})
    for created, stats in db.execute("SELECT created, stats FROM system_stats WHERE type = '120m' "
                                     "AND created >= ? AND created < ? ORDER BY created", (since, until)):
        s = json.loads(stats)
        day = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(now.tzinfo).date()
        load[day]["cpu"].append(s.get("cpu", 0))
        load[day]["mem"].append(s.get("mp", 0))
        temps = s.get("t") or {}
        if isinstance(temps, dict) and temps:
            load[day]["temp"].append(max(temps.values()))
    # Disk space from the newest sample: the root filesystem plus each extra one, by its label.
    latest = json.loads(db.execute("SELECT stats FROM system_stats WHERE type = '1m' ORDER BY created DESC LIMIT 1").fetchone()[0])
    disks = [("system", latest["du"], latest["d"])]
    for dev, fs in sorted((latest.get("efs") or {}).items(), key=lambda kv: kv[1].get("n", kv[0])):
        disks.append((fs.get("n", dev), fs.get("du", 0), fs.get("d", 0)))
    alerts = db.execute("SELECT name, value, created, resolved FROM alerts_history WHERE created >= ? AND created < ? "
                        "ORDER BY created", (since, until)).fetchall()
    info = json.loads(db.execute("SELECT info FROM systems LIMIT 1").fetchone()[0])
    return load, disks, alerts, info.get("u", 0)


# --- Drives (Scrutiny) ----------------------------------------------------------------------------

def drives():
    out = []
    for d in get_json(f"{SCRUTINY}/api/summary")["data"]["summary"].values():
        dev, smart = d["device"], d.get("smart") or {}
        status = {0: "passed", 1: "FAILED (SMART)", 2: "FAILED (Scrutiny)", 3: "FAILED"}.get(dev.get("device_status"), "?")
        out.append((SCRUTINY_DRIVES.get(dev["device_name"], dev["device_name"]), dev.get("model_name", ""),
                    status, smart.get("temp"), smart.get("power_on_hours")))
    return sorted(out)


# --- Media inventory ------------------------------------------------------------------------------

def inventory():
    """Every film, show season, audiobook, book and album, with its size and the drive(s) it's on.
    mergerfs hides which drive holds a file, so each path is looked up on every drive's own folder."""
    key = os.environ["JELLYFIN_API_KEY"]
    auth = {"Authorization": f'MediaBrowser Token="{key}"'}
    items = lambda types, fields="Path,MediaSources,ProductionYear": get_json(
        f"{JELLYFIN}/Items?" + urllib.parse.urlencode({"Recursive": "true", "IncludeItemTypes": types, "Fields": fields,
                                                       "CollapseBoxSetItems": "false"}), headers=auth)["Items"]
    libraries = sorted(((loc.replace("/media/", "", 1), lib["Name"]) for lib in get_json(f"{JELLYFIN}/Library/VirtualFolders", headers=auth)
                        for loc in lib["Locations"]), key=lambda x: -len(x[0]))
    library = lambda rel: next((n for loc, n in libraries if rel == loc or rel.startswith(loc + "/")), "")
    drives = lambda rel: [b for b in BRANCHES if os.path.exists(f"/drives/{b}/media/{rel}")]
    paths = lambda it: [m["Path"] for m in it.get("MediaSources") or []] or [it.get("Path", "")]
    rows = []
    for m in items("Movie"):
        rels = [p.replace("/media/", "", 1) for p in paths(m)]
        rows.append({"library": library(rels[0]), "kind": "film", "title": m["Name"], "year": m.get("ProductionYear", ""),
                     "season": "", "items": len(rels), "gb": sum(x.get("Size", 0) for x in m.get("MediaSources") or []) / 1e9,
                     "drives": sorted({d for r in rels for d in drives(r)})})
    seasons = defaultdict(lambda: {"items": 0, "gb": 0.0, "drives": set(), "library": "", "year": ""})
    for e in items("Episode", "Path,MediaSources,ParentIndexNumber,ProductionYear"):
        rel = e.get("Path", "").replace("/media/", "", 1)
        s = seasons[(e.get("SeriesName", "?"), e.get("ParentIndexNumber", ""))]
        s["items"] += 1
        s["gb"] += sum(x.get("Size", 0) for x in e.get("MediaSources") or []) / 1e9
        s["drives"].update(d for p in paths(e) for d in drives(p.replace("/media/", "", 1)))
        s["library"] = s["library"] or library(rel)
    for (series, season), s in seasons.items():
        rows.append({"library": s["library"], "kind": "season", "title": series, "year": "", "season": season,
                     "items": s["items"], "gb": s["gb"], "drives": sorted(s["drives"])})
    for kind, types in (("audiobook", "AudioBook"), ("book", "Book")):
        for b in items(types):
            rel = b.get("Path", "").replace("/media/", "", 1)
            rows.append({"library": library(rel), "kind": kind, "title": b["Name"], "year": b.get("ProductionYear", ""),
                         "season": "", "items": 1, "gb": sum(x.get("Size", 0) for x in b.get("MediaSources") or []) / 1e9,
                         "drives": drives(rel)})
    for a in items("MusicAlbum", "Path,ProductionYear"):
        rel = a.get("Path", "").replace("/media/", "", 1)
        rows.append({"library": library(rel), "kind": "album", "title": f'{a.get("AlbumArtist", "")} - {a["Name"]}',
                     "year": a.get("ProductionYear", ""), "season": "", "items": "", "gb": "", "drives": drives(rel)})
    rows.sort(key=lambda r: (r["library"], r["kind"], str(r["title"]).lower(), str(r["season"])))
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["library", "kind", "title", "year", "season", "files", "size_gb", "drives"])
    for r in rows:
        w.writerow([r["library"], r["kind"], r["title"], r["year"], r["season"], r["items"],
                    f'{r["gb"]:.2f}' if r["gb"] != "" else "", " ".join(r["drives"]) or "?"])
    text = out.getvalue()
    if os.path.isdir("/out"):
        with open("/out/media-inventory.csv", "w") as f:
            f.write(text)
    per_drive = defaultdict(lambda: {"films": 0, "seasons": 0, "gb": 0.0})
    for r in rows:
        for d in r["drives"]:
            k = {"film": "films", "season": "seasons"}.get(r["kind"])
            if k:
                per_drive[d][k] += 1
            if r["gb"] != "":
                per_drive[d]["gb"] += r["gb"] / len(r["drives"])
    return text, per_drive, len(rows)


# --- File lists -----------------------------------------------------------------------------------

def skipped(name):
    if name.startswith("._") or name in SKIP_NAMES:
        return True
    stem, ext = os.path.splitext(name.lower())
    return ext in SKIP_EXT or (ext in (".jpg", ".jpeg", ".png", ".tbn") and
                               (stem in ARTWORK or stem.endswith(("-poster", "-fanart", "-thumb", "-banner"))))


def file_lists():
    """Every file on every data drive, as drive, path, size and date; PRIVATE in its own file."""
    files, private = [], []
    for drive in DRIVES:
        root = f"/drives/{drive}"
        for dirpath, dirnames, names in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root)
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("._")
                                 and os.path.normpath(os.path.join(rel_dir, d)) not in SKIP_PATHS)
            target = private if PRIVATE and rel_dir.split(os.sep)[0] == PRIVATE else files
            for name in sorted(names):
                if skipped(name):
                    continue
                try:
                    st = os.lstat(os.path.join(dirpath, name))
                except OSError:
                    continue
                rel = name if rel_dir == "." else os.path.join(rel_dir, name)
                target.append((drive, rel, st.st_size, datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")))
    for name, rows in (("all-files.csv", files), (f"{PRIVATE}-files.csv", private)):
        if os.path.isdir("/out") and (rows is files or PRIVATE):
            with open(f"/out/{name}", "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["drive", "path", "size_bytes", "modified"])
                w.writerows(rows)
    return len(files), sum(r[2] for r in files), len(private), sum(r[2] for r in private)


# --- Backup ---------------------------------------------------------------------------------------

def backup_status():
    try:
        return json.load(open("/status/backup-status.json"))
    except (OSError, ValueError):
        return None


# --- Email ----------------------------------------------------------------------------------------

CELL = 'style="padding:4px 10px;border-bottom:1px solid #e5e5e5;text-align:left"'
NUM = 'style="padding:4px 10px;border-bottom:1px solid #e5e5e5;text-align:right"'


def table(head, rows, numeric_from=1):
    h = "".join(f"<th {CELL if i < numeric_from else NUM}>{html.escape(c)}</th>" for i, c in enumerate(head))
    body = "".join("<tr>" + "".join(f"<td {CELL if i < numeric_from else NUM}>{html.escape(str(c))}</td>"
                                    for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<table style="border-collapse:collapse;font-size:14px;margin:6px 0 18px">{"<tr>" + h + "</tr>"}{body}</table>'


def bars(rows, scale, unit, color="#3b82f6"):
    """A bar chart as a table: one row per day, the bar a coloured cell. Shows in every email client."""
    out = []
    for label, value, text in rows:
        width = max(1, round(200 * value / scale)) if scale and value else 0
        bar = (f'<table cellpadding="0" cellspacing="0"><tr><td width="{width}" height="12" bgcolor="{color}" '
               f'style="background:{color};font-size:0;line-height:0">&nbsp;</td></tr></table>') if width else ""
        out.append(f'<tr><td style="padding:2px 10px 2px 0;font-size:13px;white-space:nowrap">{label}</td>'
                   f'<td style="padding:2px 0;width:210px">{bar}</td>'
                   f'<td style="padding:2px 0 2px 8px;font-size:13px;white-space:nowrap">{text}</td></tr>')
    return f'<table style="border-collapse:collapse;margin:6px 0 18px">{"".join(out)}</table>'


def build():
    users, devices, per_day, recording_since = viewing()
    films, shows, counts, added_bytes = added()
    try:
        removed, dry_run = downloads()
    except Exception as e:  # Sonarr or Radarr being down shouldn't stop the rest of the report
        removed, dry_run = e, None
    try:
        gaps = incomplete()
    except Exception as e:  # likewise Seerr
        gaps = e
    load, disks, alerts, uptime = beszel()
    drive_rows = drives()
    inv_csv, per_drive, inv_count = inventory()
    n_files, files_bytes, n_private, private_bytes = file_lists()
    backup = backup_status()
    period = f"{days[0]:%b %-d} – {days[-1]:%b %-d, %Y}"
    parts = [f'<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#111;max-width:640px">'
             f'<h2 style="margin:0 0 4px">Media server, week of {period}</h2>'
             f'<p style="margin:0 0 18px;color:#666;font-size:13px">Up {uptime / 86400:.1f} days. Sent {now:%a %b %-d, %H:%M}.</p>']

    parts.append("<h3>Viewing</h3>")
    if users:
        share = lambda v: [f'{100 * v[k] / v["plays"]:.0f}%' for k in KINDS]
        rows = [(u, v["plays"], hours(v["secs"]), *share(v))
                for u, v in sorted(users.items(), key=lambda kv: -kv[1]["secs"])]
        total = {k: sum(v[k] for v in users.values()) for k in ("plays", "secs", *KINDS)}
        rows.append(("Total", total["plays"], hours(total["secs"]), *share(total)))
        parts.append(table(["User", "Plays", "Watched", *KINDS.values()], rows))
        top = max(per_day.values(), default=0)
        parts.append(bars([(f"{d:%a %-d}", per_day.get(d, 0), hours(per_day.get(d, 0)) if recording_since and d >= recording_since else "no data")
                           for d in days], top, "h"))
        parts.append("<p style='margin:0;font-size:13px'>Devices</p>")
        parts.append(table(["User", "Device", "Plays", "Watched", *KINDS.values()],
                           [(u, d, v["plays"], hours(v["secs"]), *(v[k] or "" for k in KINDS))
                            for (u, d), v in sorted(devices.items(), key=lambda kv: (kv[0][0], -kv[1]["secs"]))],
                           numeric_from=2))
        parts.append('<p style="font-size:12px;color:#666;margin-top:-10px">Direct: sent as is. Video copied: '
                     "only the audio or container converted, which is cheap. Transcoded: the video re-encoded "
                     "by the server, the costly case.</p>")
    else:
        parts.append("<p>Nothing was watched.</p>")

    more = lambda rows, n: [*rows[:n], (f"and {len(rows) - n} more", *[""] * (len(rows[0]) - 1))] if len(rows) > n else rows
    parts.append("<h3>Added</h3>")
    if counts:
        gb = lambda b: f"{b / 1e9:.1f} GB"
        what = [f'{counts["Movie"]} film{"s" * (counts["Movie"] != 1)}' if counts["Movie"] else "",
                f'{counts["Episode"]} episode{"s" * (counts["Episode"] != 1)} of {len(shows)} show{"s" * (len(shows) != 1)}'
                if counts["Episode"] else "",
                f'{counts["MusicAlbum"]} album{"s" * (counts["MusicAlbum"] != 1)}' if counts["MusicAlbum"] else "",
                f'{counts["Book"] + counts["AudioBook"]} book{"s" * (counts["Book"] + counts["AudioBook"] != 1)}'
                if counts["Book"] + counts["AudioBook"] else ""]
        parts.append(f"<p style='font-size:14px'>{', '.join(w for w in what if w)}: {gb(added_bytes)}.</p>")
        if films:
            parts.append(table(["Film", "Size"], more([(t, gb(s)) for t, s in sorted(films)], 20)))
        if shows:
            parts.append(table(["Show", "Episodes", "Size"],
                               more([(n, e, gb(s)) for n, (e, s) in sorted(shows.items(), key=lambda kv: -kv[1][1])], 20)))
    else:
        parts.append("<p>Nothing new.</p>")

    parts.append("<h3>Removed downloads</h3>")
    if isinstance(removed, Exception):
        parts.append(f'<p style="color:#b91c1c">Couldn\'t read Sonarr or Radarr: {html.escape(str(removed))}</p>')
    elif removed:
        tally = defaultdict(int)
        for r in removed:
            tally["replaced" if r[2] == "replaced" else "downloading again" if r[2] == "downloading again" else "missing"] += 1
        missing = f'<b style="color:#b91c1c">{tally["missing"]} still missing</b>' if tally["missing"] else "none still missing"
        parts.append(f"<p style='font-size:14px'>{len(removed)} download{'s' * (len(removed) != 1)} removed (dead, fake or "
                     f"failed to import), each searched for again: {tally['replaced']} replaced, "
                     f"{tally['downloading again']} downloading again, {missing}.</p>")
        parts.append(table(["What", "Why", "Now", "Removed"], more(removed, 25), numeric_from=4))
        parts.append('<p style="font-size:12px;color:#666;margin-top:-10px">Still missing means no other release was '
                     "found yet; Sonarr and Radarr keep watching for one. A request in Seerr waits until then.</p>")
    else:
        parts.append("<p>None.</p>")
    if dry_run:
        parts.append('<p style="font-size:13px">Cleanuparr is in <b>Dry Run</b>: it only logs what it would remove.</p>')

    parts.append("<h3>Incomplete shows</h3>")
    if isinstance(gaps, Exception):
        parts.append(f'<p style="color:#b91c1c">Couldn\'t read Seerr, Jellyfin or Sonarr: {html.escape(str(gaps))}</p>')
    elif gaps:
        lost = sum(1 for r in gaps if r[3] != "searching")
        parts.append(f"<p style='font-size:14px'>{len(gaps)} season{'s' * (len(gaps) != 1)} missing episodes that have aired"
                     f"{f', {lost} of them with nothing looking for the rest' if lost else ''}.</p>")
        parts.append(table(["Season", "Have", "Missing", "Sonarr"], more(gaps, 80), numeric_from=4))
        parts.append('<p style="font-size:12px;color:#666;margin-top:-10px">Episode numbers are TMDB\'s, as in Seerr. '
                     "A season that looks half there may be matched to the wrong show (a remake with the same name: "
                     "check the year in Jellyfin's Identify), or numbered differently from the files. To fill a "
                     "season not in Sonarr: add the show in Sonarr with Monitor: Missing Episodes and its existing "
                     "folder (existing files, 2160p ones included, are left alone); Seerr can't request part of a "
                     "season. Searching: Sonarr is watching for a release.</p>")
    else:
        parts.append("<p>None: every season has every episode that has aired.</p>")

    parts.append("<h3>Server load</h3>")
    if load:
        avg = lambda xs: sum(xs) / len(xs) if xs else 0
        parts.append("<p style='margin:0;font-size:13px'>CPU, daily average</p>")
        parts.append(bars([(f"{d:%a %-d}", avg(load[d]["cpu"]), f'{avg(load[d]["cpu"]):.0f}% (peak {max(load[d]["cpu"]):.0f}%)'
                            if load[d]["cpu"] else "no data") for d in days], 100, "%"))
        parts.append("<p style='margin:0;font-size:13px'>Memory, daily average</p>")
        parts.append(bars([(f"{d:%a %-d}", avg(load[d]["mem"]), f'{avg(load[d]["mem"]):.0f}%' if load[d]["mem"] else "no data")
                           for d in days], 100, "%", "#8b5cf6"))
        temps = [t for d in days for t in load[d]["temp"]]
        if temps:
            parts.append(f"<p style='font-size:13px'>CPU temperature peaked at {max(temps):.0f} °C.</p>")
    else:
        parts.append("<p>No load data for this week.</p>")

    parts.append("<h3>Disk space</h3>")
    parts.append(table(["Disk", "Used", "Free", "Size"],
                       [(n, f"{100 * u / s:.0f}%" if s else "?", f"{(s - u) / 1024:.2f} TB" if s - u >= 1024 else f"{s - u:.0f} GB",
                         f"{s / 1024:.1f} TB") for n, u, s in disks]))

    parts.append("<h3>Drive health</h3>")
    parts.append(table(["Drive", "Model", "Health", "Temp", "Powered on"],
                       [(n, m, st, f"{t} °C" if t is not None else "?", f"{h / 8766:.1f} years" if h else "?")
                        for n, m, st, t, h in drive_rows], numeric_from=3))

    parts.append("<h3>Backup</h3>")
    if not backup:
        parts.append("<p><b>No backup has run yet.</b></p>")
    else:
        when = datetime.fromisoformat(backup["time"].replace("Z", "+00:00")).astimezone(now.tzinfo)
        age = (now - when).total_seconds() / 3600
        if not backup.get("ok"):
            parts.append(f'<p style="color:#b91c1c"><b>The last backup failed</b> ({when:%a %b %-d, %H:%M}): '
                         f'{html.escape(backup.get("error", ""))}</p>')
        else:
            sm = backup.get("summary") or {}
            note = f' <b style="color:#b91c1c">That is {age / 24:.1f} days ago.</b>' if age > 36 else ""
            parts.append(f'<p style="font-size:14px">Last backup {when:%a %b %-d, %H:%M}: '
                         f'{sm.get("files_new", 0)} new and {sm.get("files_changed", 0)} changed files, '
                         f'{sm.get("data_added", 0) / 1e9:.2f} GB added. {backup.get("snapshots", 0)} versions kept. '
                         f'Backup share: {backup.get("free_bytes", 0) / 1e12:.2f} TB free of {backup.get("size_bytes", 0) / 1e12:.1f} TB.{note}</p>')

    parts.append("<h3>What's on each drive</h3>")
    parts.append(table(["Drive", "Films", "Show seasons", "Size"],
                       [(d, per_drive[d]["films"], per_drive[d]["seasons"], f'{per_drive[d]["gb"] / 1000:.2f} TB')
                        for d in sorted(per_drive)]))
    parts.append(f'<p style="font-size:12px;color:#666;margin-top:-10px">The attached media-inventory.csv lists all '
                 f"{inv_count} films, show seasons, audiobooks, books and albums, with the drive(s) each is on: "
                 "the list of what to find again if a drive dies. "
                 f"Also saved on the server and in the backup (not emailed): a list of all {n_files:,} files "
                 f"on the data drives ({files_bytes / 1e12:.1f} TB, all-files.csv in the inventory folder)"
                 + (f", and the {n_private:,} files in {PRIVATE} on their own ({PRIVATE}-files.csv)" if PRIVATE else "")
                 + ".</p>")

    parts.append("<h3>Alerts</h3>")
    if alerts:
        parts.append(table(["Alert", "Threshold", "Fired", "Resolved"],
                           [(a, v, local(c), local(r) if r else "still active") for a, v, c, r in alerts], numeric_from=1))
    else:
        parts.append("<p>None this week.</p>")
    parts.append("</div>")
    return f"Media server weekly report, {period}", "".join(parts), inv_csv


def send(subject, body, attachment):
    u = urllib.parse.urlsplit(os.environ["SMTP_URL"])
    q = urllib.parse.parse_qs(u.query)
    msg = EmailMessage()
    msg["From"] = f"Media Server <{q['from'][0]}>"
    msg["To"] = q["to"][0]
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=q["from"][0].split("@")[1])
    msg.set_content("This report is HTML; open it in an email app that shows HTML.")
    msg.add_alternative(body, subtype="html")
    msg.add_attachment(attachment.encode(), maintype="text", subtype="csv", filename=f"media-inventory-{now:%Y-%m-%d}.csv")
    with smtplib.SMTP(u.hostname, u.port or 587, timeout=60) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(urllib.parse.unquote(u.username), urllib.parse.unquote(u.password))
        s.send_message(msg)


if __name__ == "__main__":
    if not os.environ.get("SMTP_URL") and not os.environ.get("DRY_RUN"):
        raise SystemExit("SMTP_URL isn't set in .env, so there's nowhere to send the report.")
    subject, body, inv = build()
    if os.environ.get("DRY_RUN"):
        print(body)
    else:
        send(subject, body, inv)
        print(f"sent: {subject}")
