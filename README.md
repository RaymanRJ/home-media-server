# home-media-server

A self-hosted media server for one Linux machine: Jellyfin for your films and shows, with optional
requests and automatic downloads, file shares, monitoring that fixes stuck apps, and nightly
backups. Everything runs in Docker, set up by one script. No cloud account is needed; a few extras
can use AWS if you have it.

## Contents

- [What's in it](#whats-in-it)
- [Choose your setup](#choose-your-setup)
- [Requirements](#requirements)
- [Getting started](#getting-started)
- [Watching away from home](#watching-away-from-home)
- [Configuration](#configuration)
- [Ports](#ports)
- [Updating](#updating)
- [Backups](#backups)
- [Advanced: AWS](#advanced-aws)
- [Repository layout](#repository-layout)
- [License](#license)

## What's in it

Three stacks, each started on its own. Everything marked *optional* is off until you turn it on.

| Stack          | Services                                                                                                                     |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| **media**      | [Jellyfin](https://jellyfin.org). Optional: [Caddy](https://caddyserver.com) for HTTPS on your own name, with a [DuckDNS](https://www.duckdns.org) (free) or Route 53 (AWS) updater to keep the name on your home IP |
|                | Optional downloads: [Seerr](https://github.com/seerr-team/seerr) for requests, Sonarr, Radarr, Prowlarr, FlareSolverr, qBittorrent, Bazarr, [Recyclarr](https://recyclarr.dev), [Cleanuparr](https://github.com/Cleanuparr/Cleanuparr) |
| **monitoring** | [Beszel](https://beszel.dev) (server stats), [Scrutiny](https://github.com/AnalogJ/scrutiny) (drive health), a watchdog that restarts stuck apps; optional email alerts, a weekly email report and a nightly [restic](https://restic.net) backup |
| **samba**      | File shares for Macs, Windows and phones                                                                                     |

```mermaid
flowchart LR
  subgraph media["media stack"]
    jellyfin[Jellyfin]
    caddy["Caddy (HTTPS, optional)"] --> jellyfin
    caddy --> seerr["Seerr (optional)"]
    seerr --> arr["Sonarr / Radarr"]
    arr --> qbit[qBittorrent]
    ddns["DuckDNS or Route 53 updater"]
  end

  subgraph monitoring["monitoring stack"]
    beszel["Beszel, Scrutiny"]
    watchdog[watchdog]
    backup["backup, weekly report"]
  end

  subgraph samba["samba stack"]
    smb[Samba]
  end

  home((At home)) --> jellyfin
  internet((Away from home)) -- "80, 443 via the router" --> caddy
  jellyfin -- read-only --> pool[("Your media")]
  qbit --> pool
  arr --> pool
  backup --> nas[("NAS share")]
```

Every image is pinned to a version, and every service has a health check.

## Choose your setup

Pick a line in each row. Only the last column needs AWS, and nothing requires it.

| You want                            | Simplest (no account)                  | Easy, free                                                                                                                | With AWS (advanced)                                 |
| ----------------------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| **Watch at home**                   | Works out of the box                   |                                                                                                                           |                                                     |
| **Watch away from home**            | Not available                          | [Tailscale](#tailscale-no-port-forwarding) (an app; no router changes) or [DuckDNS + HTTPS](#duckdns-and-https) (a free web address) | Your own domain in Route 53                        |
| **Email alerts and weekly report**  | Off                                    | Any email account: Gmail, Outlook and so on ([Configuration](#configuration))                                             | SES                                                 |
| **Know when the server is offline** | Off                                    | [UptimeRobot](https://uptimerobot.com) (free): watches your web address and emails you                                    | The outside check (a Lambda), same idea            |
| **A "we're offline" page**          | Not available                          | **Not available: there's no easy free alternative**                                                                      | The outside check's maintenance page               |

The offline page is the one feature that only works with AWS: when your house loses power or
internet, something outside it has to answer for your web address, and that's what the AWS setup
provides. Without it, visitors just see that the site can't be reached.

## Requirements

- A Linux computer to be the server (tested on Ubuntu 24.04), with
  [Docker](https://docs.docker.com/engine/install/ubuntu/) installed
- Your media in one folder, or several drives pooled with [mergerfs](https://github.com/trapexit/mergerfs)
- Optional: a router you can forward ports on (for DuckDNS), a NAS or other shared folder (for
  backups), an Intel CPU with built-in graphics (for faster transcoding)

## Getting started

1. **Download it** and run the setup script once. It creates your settings file, `.env`, with
   your user, time zone and folders already filled in, and stops:

   ```sh
   git clone https://github.com/RaymanRJ/home-media-server.git ~/home-media-server
   cd ~/home-media-server
   ./setup.sh
   ```

2. **Edit `.env`** (`nano .env`). Set `MEDIA_POOL` to your media folder, and `COMPOSE_PROFILES` to
   the parts you want (`downloads` is on to start with). Everything else can wait.

3. **Start it:**

   ```sh
   ./setup.sh --start
   ```

   It creates each app's folders, checks the settings, and installs everything to start
   automatically when the server boots. (`./setup.sh --dry-run` shows what it would do first.)

4. **Open Jellyfin** at `http://<server-address>:8096` from any device at home and follow its
   setup. Then set up the other apps you turned on, at the ports below.

5. **Optional:** [watch away from home](#watching-away-from-home), email alerts and backups (both
   in `.env`), then run `./setup.sh --start` again.

## Watching away from home

### Tailscale (no port forwarding)

The simplest: install [Tailscale](https://tailscale.com/download) on the server
(`curl -fsSL https://tailscale.com/install.sh | sh`, then `sudo tailscale up`) and on each phone,
tablet or computer that should watch. Jellyfin is then at `http://<server-name>:8096` from
anywhere, privately. Free for personal use; nothing to change on the router or in `.env`. Everyone
who watches needs the Tailscale app and an invitation to your network.

### DuckDNS and HTTPS

A free web address like `https://yourname.duckdns.org`, with a real HTTPS certificate, that
anyone can open in a browser or the Jellyfin app.

1. Sign in at [duckdns.org](https://www.duckdns.org), add a subdomain, and note it and your token.
2. In `.env`: `COMPOSE_PROFILES=downloads,https,duckdns`, then `DUCKDNS_SUBDOMAIN`,
   `DUCKDNS_TOKEN`, `JELLYFIN_DOMAIN=yourname.duckdns.org` and (for requests)
   `SEERR_DOMAIN=requests.yourname.duckdns.org`.
3. On your router, forward ports 80 and 443 to the server ([`docs/router.md`](docs/router.md)).
4. `./setup.sh --start`. Caddy gets the certificates in a minute or two (`sudo docker logs -f caddy`).

Forwarding ports makes Jellyfin and Seerr reachable from the internet: keep their passwords strong.

## Configuration

- **`.env`** (the repo's main folder) holds every setting: folders, what to run, web addresses,
  email, app keys and passwords. Each section in it explains itself.
- **Email:** set `SMTP_URL` to any email account's outgoing mail server. For Gmail, make an
  [app password](https://myaccount.google.com/apppasswords) and use
  `smtp://you%40gmail.com:APP_PASSWORD@smtp.gmail.com:587/?from=you@gmail.com&to=you@gmail.com`.
  Without it there are no emails, but the watchdog still restarts stuck apps.
- **This machine's hardware** (the GPU for faster transcoding, drives to monitor, more shares)
  goes in a `compose.override.yaml` in a stack's folder: copy the `.example` next to it.
- **Docker from the snap** works too: `setup.sh` notices and adjusts. Keep your folders under
  `/home` or `/mnt`.

## Ports

| Port                                | Service                                                    | Reachable from                    |
| ----------------------------------- | ---------------------------------------------------------- | --------------------------------- |
| 8096                                | Jellyfin                                                   | Home (and Tailscale)              |
| 80, 443                             | Caddy (Jellyfin, Seerr), only with `https`                 | The internet, if forwarded        |
| 5055                                | Seerr                                                      | Home (and via Caddy)              |
| 6881                                | qBittorrent's torrent port                                 | The internet, if forwarded        |
| 8081, 9696, 8989, 7878, 6767, 11011 | qBittorrent, Prowlarr, Sonarr, Radarr, Bazarr, Cleanuparr  | Home                              |
| 8090, 8080                          | Beszel, Scrutiny                                           | Home                              |
| 445, 139                            | Samba                                                      | Home                              |

Only what the router forwards is public. Scrutiny has no login, so never forward it.

## Updating

```sh
cd ~/home-media-server && git pull && ./setup.sh --start
```

Images are pinned, so a version only changes when this repo changes it. Read the release notes
first for Jellyfin and the download apps, which upgrade their databases.

## Backups

With `RESTIC_PASSWORD` and `BACKUP_SHARE` set in `.env`, a nightly backup sends every app's
settings, `.env` and anything added in the monitoring stack's override to an encrypted copy on a
shared folder (a NAS, or another computer). Keep `RESTIC_PASSWORD` somewhere safe as well: without
it the backup can't be restored. Your films and shows themselves aren't backed up; add a folder
that matters (music, photos) in the override. To restore:

```sh
cd stacks/monitoring
sudo docker compose run --rm -v "$HOME/restore:/restore" --entrypoint restic backup snapshots
sudo docker compose run --rm -v "$HOME/restore:/restore" --entrypoint restic backup restore latest --target /restore
```

## Advanced: AWS

**Optional, and only for people comfortable with AWS and Terraform.** Everything above works
without it. In AWS, `terraform/` sets up:

- **Your own domain** in Route 53, kept on your home IP by the `route53` profile's updater (instead
  of DuckDNS). The server gets AWS access from a certificate (IAM Roles Anywhere), never a password.
- **Email through SES**, from your own domain.
- **The outside check:** a small function in AWS that opens your site every five minutes, emails
  you when it goes down or comes back, and, the part with no easy alternative, shows a
  **"we're offline" page** while your house is unreachable.

It costs about nothing (everything stays in AWS's free tiers) but needs an AWS account, a domain
already in Route 53, and [Terraform](https://developer.hashicorp.com/terraform/install) 1.10+:

```sh
cd terraform
cp backend.hcl.example backend.hcl && cp site.auto.tfvars.example site.auto.tfvars   # fill both in
terraform init -backend-config=backend.hcl
terraform apply
terraform output -raw server_env          # paste into .env
terraform output -raw server_cert_pem     # save as ~/.dns-updater/cert.pem on the server
terraform output -raw server_key_pem      # save as ~/.dns-updater/key.pem (chmod 600)
```

Then `COMPOSE_PROFILES=downloads,https,route53`, `SMTP_URL` with SES's details
(`terraform output ses_smtp`), and `./setup.sh --start`.

## Repository layout

```
setup.sh                      sets everything up; safe to re-run
.env.example                  every setting, explained (setup.sh copies it to .env)
stacks/
  media/                      Jellyfin, Caddy, DuckDNS and Route 53 updaters, the download apps
  monitoring/                 Beszel, Scrutiny, watchdog, weekly report, backup
  samba/                      Samba
  */compose.override.example.yaml   this machine's hardware: GPU, drives, extra shares
systemd/                      the units setup.sh installs
terraform/, outside-check/    the optional AWS setup
docs/router.md                port forwarding and router settings
CLAUDE.md                     guidance for Claude Code; your own server's notes go in CLAUDE.local.md
```

## License

[MIT](LICENSE)
