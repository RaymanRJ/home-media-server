#!/bin/sh
# Sets up this repo on a server: .env, each app's data folder, and the systemd units that run the
# stacks at boot. Safe to run again after changing .env or moving the repo; it never overwrites
# .env or a compose.override.yaml, and only starts anything with --start.
#
#   ./setup.sh [--dry-run] [--start] [stack ...]     stacks: media monitoring samba (default: all)
#
# Run it from anywhere as the user who owns the media; it uses sudo for the systemd and Avahi files.
set -eu

repo=$(cd "$(dirname "$0")" && pwd)
dry=false
start=false
stacks=""
for arg in "$@"; do
    case "$arg" in
        --dry-run) dry=true ;;
        --start) start=true ;;
        media | monitoring | samba) stacks="$stacks $arg" ;;
        *) echo "unknown argument: $arg (see the top of $0)" >&2; exit 2 ;;
    esac
done
[ -n "$stacks" ] || stacks="media monitoring samba"

say() { printf '%s\n' "$*"; }
run() { if $dry; then say "would run: $*"; else "$@"; fi; }
# Writes stdin to a root-owned file, unless it already has exactly that content.
put() {
    content=$(cat)
    if [ -f "$1" ] && [ "$(sudo cat "$1")" = "$content" ]; then return; fi
    if $dry; then say "would write $1:"; printf '%s\n' "$content" | sed 's/^/    /'; return; fi
    sudo mkdir -p "$(dirname "$1")"
    printf '%s\n' "$content" | sudo tee "$1" > /dev/null
    say "wrote $1"
}

# --- .env ---------------------------------------------------------------------------------------
if [ ! -f "$repo/.env" ]; then
    # This machine's values, so the first section of .env is already right.
    tz=$(timedatectl show -p Timezone --value 2> /dev/null || true)
    sed -e "s#^CONFIG_ROOT=.*#CONFIG_ROOT=$HOME#" -e "s#^PUID=.*#PUID=$(id -u)#" -e "s#^PGID=.*#PGID=$(id -g)#" \
        -e "s#^TZ=.*#TZ=${tz:-Etc/UTC}#" -e "s#^STATUS_DIR=.*#STATUS_DIR=$HOME/media-server/status#" \
        -e "s#^INVENTORY_DIR=.*#INVENTORY_DIR=$HOME/media-server/inventory#" \
        -e "s#^SAMBA_USER=.*#SAMBA_USER=$(id -un)#" "$repo/.env.example" > "$repo/.env"
    chmod 600 "$repo/.env"
    say "Created .env, with this machine's user, time zone and folders filled in."
    say "Open it (nano .env), set MEDIA_POOL to your media folder and choose what to run, then run $0 again."
    exit 0
fi
# Values are read, not sourced: .env holds secrets with characters a shell would interpret.
env_get() { sed -n "s/^$1=//p" "$repo/.env" | tail -n 1; }
config_root=$(env_get CONFIG_ROOT)
media_pool=$(env_get MEDIA_POOL)
profiles=",$(env_get COMPOSE_PROFILES | tr -d ' '),"
has() { case "$profiles" in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
[ -n "$config_root" ] || { echo "CONFIG_ROOT is empty in .env" >&2; exit 1; }
[ -d "$media_pool" ] || { echo "MEDIA_POOL ($media_pool) isn't a folder: set it in .env" >&2; exit 1; }
if has https && [ -z "$(env_get JELLYFIN_DOMAIN)" ]; then
    echo "https is in COMPOSE_PROFILES but JELLYFIN_DOMAIN is empty in .env" >&2; exit 1
fi
if has duckdns && { [ -z "$(env_get DUCKDNS_SUBDOMAIN)" ] || [ -z "$(env_get DUCKDNS_TOKEN)" ]; }; then
    echo "duckdns is in COMPOSE_PROFILES but DUCKDNS_SUBDOMAIN or DUCKDNS_TOKEN is empty in .env" >&2; exit 1
fi

# --- Docker: the snap or a normal install -------------------------------------------------------
if [ -x /snap/bin/docker-compose ]; then
    compose=/snap/bin/docker-compose
    docker=/snap/bin/docker
    docker_unit=snap.docker.dockerd.service
else
    command -v docker > /dev/null || { echo "Docker isn't installed" >&2; exit 1; }
    compose="$(command -v docker) compose"
    docker=$(command -v docker)
    docker_unit=docker.service
fi
say "Docker: $docker_unit, Compose: $compose"

# --- Data folders, so Docker doesn't create them owned by root ------------------------------------
folders=""
for s in $stacks; do
    case "$s" in
        media)
            folders="$folders .jellyfin-config/config .jellyfin-config/cache"
            has https && folders="$folders .caddy/data .caddy/config"
            has duckdns && folders="$folders .duckdns"
            has route53 && folders="$folders .dns-updater"
            has downloads && folders="$folders .qbittorrent .prowlarr .sonarr .radarr .bazarr .seerr .recyclarr .cleanuparr"
            ;;
        monitoring) folders="$folders .beszel/data .scrutiny/config .scrutiny/influxdb .restic-cache" ;;
        samba) folders="$folders .samba/private" ;;
    esac
done
for f in $folders; do
    [ -d "$config_root/$f" ] && continue
    run mkdir -p "$config_root/$f"
    # The AWS certificate's key and Samba's password database: only their owner may read them.
    case "$f" in .dns-updater | .samba/private) run chmod 700 "$config_root/$f" ;; esac
done
case "$stacks" in *monitoring*)
    for v in STATUS_DIR INVENTORY_DIR; do
        d=$(env_get $v); [ -z "$d" ] || [ -d "$d" ] || run mkdir -p "$d"
    done ;;
esac

# --- Check each stack's configuration before installing anything ----------------------------------
for s in $stacks; do
    (cd "$repo/stacks/$s" && $compose config -q) || { echo "stacks/$s doesn't render; check .env and its compose.override.yaml" >&2; exit 1; }
    [ -f "$repo/stacks/$s/compose.override.yaml" ] || say "stacks/$s: no compose.override.yaml (see its .example if this machine needs one)"
done

# --- systemd units, with a drop-in for this machine's paths and Docker ----------------------------
units=/etc/systemd/system
for u in "home-media@.service" media-backup.service media-backup.timer media-weekly-report.service media-weekly-report.timer; do
    put "$units/$u" < "$repo/systemd/$u"
done
put "$units/home-media@.service.d/local.conf" <<EOF
# Written by setup.sh: this repo's path and this machine's Docker.
[Unit]
Requires=$docker_unit
After=$docker_unit

[Service]
WorkingDirectory=$repo/stacks/%i
ExecStartPre=
ExecStartPre=/bin/sh -c 'until $docker info >/dev/null 2>&1; do sleep 2; done'
ExecStart=
ExecStart=$compose up -d
ExecStop=
ExecStop=$compose down
EOF
for job in "media-backup:run --rm backup" "media-weekly-report:run --rm --build weekly-report"; do
    put "$units/${job%%:*}.service.d/local.conf" <<EOF
# Written by setup.sh: this repo's path and this machine's Docker.
[Unit]
Requires=$docker_unit
After=$docker_unit

[Service]
WorkingDirectory=$repo/stacks/monitoring
ExecStart=
ExecStart=$compose ${job#*:}
EOF
done
# Don't start Jellyfin against an empty folder if the pool didn't mount.
if [ -n "$media_pool" ]; then
    put "$units/home-media@media.service.d/pool.conf" <<EOF
[Unit]
RequiresMountsFor=$media_pool
EOF
fi

# --- Samba's mDNS announcement, through the host's Avahi -------------------------------------------
case "$stacks" in *samba*)
    if [ -d /etc/avahi/services ]; then
        put /etc/avahi/services/smb.service < "$repo/stacks/samba/avahi/smb.service"
    else
        say "Avahi isn't installed (apt install avahi-daemon): Macs and phones won't find Samba by name"
    fi ;;
esac

# --- Enable (and maybe start) ---------------------------------------------------------------------
run sudo systemctl daemon-reload
for s in $stacks; do run sudo systemctl enable "home-media@$s.service"; done
# The timers only once what they need is in .env.
timers=""
case "$stacks" in *monitoring*)
    if [ -n "$(env_get RESTIC_PASSWORD)" ]; then timers="$timers media-backup.timer"
    else say "No RESTIC_PASSWORD in .env: the nightly backup stays off"; fi
    if [ -n "$(env_get SMTP_URL)" ]; then timers="$timers media-weekly-report.timer"
    else say "No SMTP_URL in .env: no email alerts, and the weekly report stays off"; fi ;;
esac
# shellcheck disable=SC2086 # $timers is a list of unit names
[ -z "$timers" ] || run sudo systemctl enable $timers
if $start; then
    for s in $stacks; do run sudo systemctl restart "home-media@$s.service"; done
    # shellcheck disable=SC2086
    [ -z "$timers" ] || run sudo systemctl restart $timers
    say "Started. Check with: sudo docker ps (every service should reach (healthy))"
else
    say "Enabled for the next boot. Start now with: sudo systemctl start$(for s in $stacks; do printf ' home-media@%s' "$s"; done)"
fi
