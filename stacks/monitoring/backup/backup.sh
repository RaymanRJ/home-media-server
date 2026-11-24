#!/bin/sh
# The nightly backup, run in the restic image by the media-backup timer.
# Everything under /src (read-only mounts) goes to the restic repository on the NAS share at
# /repo. Jellyfin's database comes from the zip its own backup makes first; the live database
# files are excluded because copying them mid-write isn't safe. The result goes to
# /status/backup-status.json for the weekly report.
set -eu

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
fail() {
    printf '{"ok": false, "time": "%s", "error": "%s"}\n' "$(now)" "$1" > /status/backup-status.json
    echo "backup failed: $1" >&2
    exit 1
}

[ -n "${RESTIC_PASSWORD:-}" ] || fail "RESTIC_PASSWORD is not set"

# Jellyfin's own backup: a consistent copy of its database, in /jellyfin-backups (which is
# data/backups inside the Jellyfin config, so restic picks it up). It takes about 20 seconds.
# If Jellyfin isn't answering, carry on with the previous zip. Keep the newest three.
wget -q -O /dev/null --header "Authorization: MediaBrowser Token=\"${JELLYFIN_API_KEY:-}\"" \
    --header "Content-Type: application/json" --post-data '{"Database":true}' \
    http://localhost:8096/Backup/Create || echo "Jellyfin's backup failed; using the previous one" >&2
ls -1t /jellyfin-backups/jellyfin-backup-*.zip 2> /dev/null | tail -n +4 | xargs -r rm -f
restic cat config > /dev/null 2>&1 || restic init || fail "could not open or create the repository"

restic backup --host media-server --exclude-file /exclude.txt --json /src > /tmp/backup.json \
    || fail "backup did not finish"
restic forget --host media-server --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune \
    || fail "removing old snapshots failed"
restic check || fail "repository check found a problem"

summary=$(grep '"message_type":"summary"' /tmp/backup.json | tail -n 1)
snapshots=$(restic snapshots --host media-server --json | grep -o '"short_id"' | wc -l)
free=$(df -Pk /repo | awk 'NR == 2 { print $4 * 1024 }')
size=$(df -Pk /repo | awk 'NR == 2 { print $2 * 1024 }')
printf '{"ok": true, "time": "%s", "summary": %s, "snapshots": %s, "free_bytes": %s, "size_bytes": %s}\n' \
    "$(now)" "${summary:-null}" "$snapshots" "$free" "$size" > /status/backup-status.json
echo "backup done: $snapshots snapshots"
