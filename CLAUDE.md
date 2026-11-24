# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Overview

Config for a self-hosted media server on one Linux machine: three Docker Compose stacks, systemd
units that run them, a setup script, and an optional Terraform setup for AWS. There's no
application code to build and no test suite; the Python and shell scripts are small and use only
their standard libraries.

**It's meant for people who aren't very technical, and nothing may require AWS.** Every
AWS-backed part is optional (the `route53` profile, `terraform/`, `outside-check/`), and each has a
free, simpler alternative documented in the README's "Choose your setup", or the README says
plainly that there isn't one (the maintenance page). Keep it that way: a new feature works without
an AWS account, and the README explains it in plain words.

The repo is cloned on the server and run from there (anywhere; `setup.sh` points the units at it). Changes
usually happen on another machine and reach the server by `git pull`, followed by restarting the
affected stack. Terraform runs from any machine with AWS credentials. Unless you're on the server
itself, you can't run the stacks: say what the user needs to run on the server, and in what order.

Notes about the user's own server (hardware, drives, what's set up, decisions) belong in
`CLAUDE.local.md` (gitignored), not here.

The README stays short: what's here and how to get it running. The explanations and deep dives are
articles on rrjamal.ca, linked from the README's top and its "Further reading"
(`https://rrjamal.ca/articles/<slug>`; the setup guide is `home-media-server-setup-guide`). Link to
them rather than repeating them, and when a change makes an article's description of this repo
wrong, say so: the articles live in another repo.

## Layout

- `stacks/media/`: Jellyfin, always; the rest by profile, enabled by `COMPOSE_PROFILES` in `.env`:
  `https` (Caddy, `Caddyfile`, HTTPS for Jellyfin and Seerr), `duckdns` (a free DuckDNS name kept on
  the home IP), `route53` (the AWS alternative, `dns-updater/`), and `downloads` (qBittorrent,
  Prowlarr, FlareSolverr, Sonarr, Radarr, Bazarr, Seerr, Cleanuparr, Recyclarr).
- `stacks/monitoring/`: Beszel and its agent, Scrutiny (`scrutiny/collector.yaml`), the watchdog
  (`watchdog/`), and two run-only services started by timers: `weekly-report` (profile `report`)
  and `backup` (profile `backup`, restic to an SMB share).
- `stacks/samba/`: the Samba image (Ubuntu 24.04's Samba), `smb.conf`, and the Avahi service file
  for mDNS (`avahi/smb.service`, copied to `/etc/avahi/services/` on the host).
- `.env` (gitignored, from `.env.example`) at the repo root: every path, ID, domain and secret.
  `SMTP_URL` is the one email setting (any provider; Scrutiny, the watchdog and the weekly report
  all use it); empty means no email, and the watchdog then only logs.
  Each stack reads it through its committed `.env` symlink (`stacks/<name>/.env -> ../../.env`).
- `stacks/<name>/compose.override.yaml` (gitignored, from the `.example` beside it): this machine's
  hardware (GPU device, disks by serial, drive mounts, extra shares). Compose merges it in on its own.
- `systemd/`: `home-media@.service`, a template run once per stack (`home-media@media` etc.), and
  the backup and weekly-report units and timers. They're written for a normal Docker install at
  `/opt/home-media-server`; `setup.sh` installs them with drop-ins (`*.service.d/local.conf`) for
  the repo's real path and the machine's Docker (the snap or not), and `pool.conf` from
  `MEDIA_POOL`. Units use `Wants=docker.service`, never `Requires=`: a drop-in can't undo a
  `Requires`, and snap Docker has no `docker.service`.
- `setup.sh`: creates `.env` with this machine's user, time zone and folders (then stops), each
  enabled part's data folder, checks `.env` and that every stack renders,
  installs the units and Avahi file, enables them, and the backup and report timers only once their
  settings exist (`--start` starts them, `--dry-run` only prints).
  Without stack names it does media plus any stack already enabled, so monitoring and samba start
  only once named (samba needs `smbpasswd` after its first start). It creates `downloads`,
  `Movies` and `TV` in `MEDIA_POOL` (with `downloads`) and `SAMBA_SHARE_DIR`, owned by
  `PUID:PGID`, since a folder Docker creates is root's.
  Keep it idempotent and POSIX `sh`; it must never overwrite `.env` or a `compose.override.yaml`.
- `terraform/` (optional, AWS): Route 53 records, IAM Roles Anywhere for the dns-updater, SES for alert email, and
  the outside check (`monitor.tf`, `maintenance.tf`). Real values only in gitignored `backend.hcl`
  and `site.auto.tfvars`. `.terraform.lock.hcl` is committed.
- `outside-check/` (optional, AWS): the Lambda (`check.py`) that checks the site from AWS every
  five minutes, emails on change, and serves `maintenance.html` while the house is offline.
  Terraform uploads its code when it creates the function and ignores it after
  (`terraform apply -replace=aws_lambda_function.monitor` uploads a change). With
  `github_repository` set, Terraform also makes a deploy role (and needs the account's GitHub OIDC
  provider), and `.github/workflows/outside-check.yaml` deploys pushes to main when the repository
  variable `OUTSIDE_CHECK_DEPLOY` is `true`. Empty `github_repository` (the default): neither.
- `docs/router.md`: the router's settings (forwards for `https` and `downloads`), which live in no
  config file.

## Conventions

- **Pin every image** to a version (and the dns-updater's signing helper by version and SHA-256).
  Never use `latest`.
- **Every long-running service has a `healthcheck`**, with the shared timing in each file's
  `x-health` anchor; the watchdog restarts unhealthy containers and emails. A new service needs a
  check that works with what's in its image (`curl`, `wget` or the app's own command); test it with
  `docker exec` before relying on it.
- **Machine-specific values go in `.env`; hardware goes in `compose.override.yaml`.** Never
  hardcode a home path, user, UUID, serial, domain or IP in a tracked file. A new setting gets a
  placeholder and a one-line comment in `.env.example`.
- **Secrets only in `.env`** (or Terraform state), never in a tracked file or a separate dotfile:
  `.env` is backed up nightly and is the one file to copy to a new server.
- **Jellyfin mounts the media read-only** (`:ro`), and its libraries save metadata in its config,
  not next to the media. The download apps get the pool whole and writable, at the same `/media`
  path as Jellyfin, so hardlinks work and paths match.
- **App data lives in `${CONFIG_ROOT}/.<app>`** on the host, never in the repo. With Docker from the
  snap, keep every mounted path under `/home` or `/mnt` (confinement blocks others) and write
  `/snap/bin/docker-compose`, not `docker compose`.
- **Ports:** web UIs are published for the LAN only; only Caddy (80, 443) and qBittorrent's torrent
  port (6881) are meant to be forwarded by the router. Scrutiny has no login: never put it behind
  Caddy without authentication.
- Comments explain *why*, in plain sentences; keep them in step with the code.

## Checking a change

There are no tests; check what you can locally (Docker and Terraform needn't be on the server):

```sh
cp -n .env.example .env                                   # placeholders are enough to render
for s in media monitoring samba; do (cd stacks/$s && docker compose config -q && echo "$s ok"); done
(cd stacks/media && docker compose --profile downloads config --services)
python3 -m py_compile outside-check/check.py stacks/monitoring/*/*.py
shellcheck setup.sh stacks/*/*.sh stacks/*/*/*.sh
(cd terraform && terraform fmt -check && terraform init -backend=false && terraform validate)
```

On the server, after `git pull`: `cd stacks/<name> && sudo docker compose up -d --build`, then
`sudo docker ps` until the changed services show `(healthy)`. A unit change needs `./setup.sh`
again (it reinstalls the units and reloads systemd), then a restart of the affected unit.

## Boundaries

- Never commit `.env`, `compose.override.yaml`, `site.auto.tfvars`, `backend.hcl` or Terraform
  state, and never print a secret from them. Scrutiny logs its SMTP URL, password included, when
  it sends: don't show `docker logs scrutiny` unfiltered.
- The Route 53 hosted zone is only looked up (a data source); never create, import or change the
  zone here. Same for the account's GitHub OIDC provider.
- Changing `project_name` in Terraform renames, and so recreates, every AWS resource.
- Changing ports, the Caddyfile, DNS or the router's forwards changes what's reachable from the
  internet: say plainly what becomes exposed or closed, and update `docs/router.md`.
- Ask before anything that restarts Jellyfin while people may be watching, deletes media, or runs
  `terraform apply`.
