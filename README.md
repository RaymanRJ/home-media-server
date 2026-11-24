# home-media-server

A self-hosted media server for one Linux machine: Jellyfin for your films and shows, with optional
requests and automatic downloads, file shares, monitoring that fixes stuck apps, and nightly
backups. Everything runs in Docker, set up by one script. No cloud account is needed; a few extras
can use AWS if you have it.

**New to this?** [Setting up a home media server with Docker, step by step](https://rrjamal.ca/articles/home-media-server-setup-guide)
walks through this repo from a spare computer to a film playing on your TV. More background is in
[Further reading](#further-reading).

## Contents

- [What's in it](#whats-in-it)
- [Choose your setup](#choose-your-setup)
- [Requirements](#requirements)
- [Getting started](#getting-started)
- [File shares](#file-shares)
- [Watching away from home](#watching-away-from-home)
- [Configuration](#configuration)
- [Ports](#ports)
- [Updating](#updating)
- [Backups](#backups)
- [Advanced: AWS](#advanced-aws)
- [Repository layout](#repository-layout)
- [Further reading](#further-reading)
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

Every image is pinned to a version, and every long-running service has a health check.

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
  backups), an Intel CPU with built-in graphics (for faster transcoding), and Avahi
  (`sudo apt install avahi-daemon`) so Macs and phones find the file shares by name

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

   This starts the media stack: Jellyfin, and the parts you chose in `COMPOSE_PROFILES`. It
   creates each app's folders (and `downloads`, `Movies` and `TV` in your media folder, for the
   download apps), checks the settings, and installs everything to start automatically when the
   server boots. (`./setup.sh --dry-run` shows what it would do first.)

4. **Open Jellyfin** at `http://<server-address>:8096` from any device at home and follow its
   setup. Then set up the other apps you turned on, at the ports below, with the folder paths and
   addresses in [`stacks/media/README.md`](stacks/media/README.md#paths-and-addresses-to-use-in-the-apps)
   (qBittorrent's save path in particular).

5. **Add the other stacks when you want them**, each by name. Once added, a plain
   `./setup.sh --start` includes them from then on:

   ```sh
   ./setup.sh --start monitoring     # server stats, drive health, the watchdog; backups and email from .env
   ./setup.sh --start samba          # file shares: then set its password (File shares, below)
   ```

6. **Optional:** [watch away from home](#watching-away-from-home), email alerts and backups (all
   in `.env`), then run `./setup.sh --start` again.

## File shares

The samba stack shares one folder, `SAMBA_SHARE_DIR` in `.env` (`~/share` unless you change it),
with Macs, Windows and phones at home. After its first start, give its user (`SAMBA_USER`, your
own user name unless you change it) a password, once:

```sh
sudo docker exec -it samba smbpasswd -a "$(id -un)"     # or the SAMBA_USER from .env
```

Then connect: on a Mac, Finder's sidebar under Network (or `smb://<server-address>`); on Windows,
`\\<server-address>` in File Explorer; on a phone, a file app's "connect to server". To share
more folders, see `stacks/samba/compose.override.example.yaml`.

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
| 8090, 8080, 45876                   | Beszel, Scrutiny, the Beszel agent                         | Home                              |
| 445, 139 (TCP); 137, 138 (UDP)      | Samba                                                      | Home                              |

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
shared folder (a NAS, or another computer). The folder can allow guests (the default) or need a
login: `BACKUP_SHARE_OPTS=username=...,password=...` in `.env`. Keep `RESTIC_PASSWORD` somewhere safe as well: without
it the backup can't be restored. Your films and shows themselves aren't backed up; add a folder
that matters (music, photos) in the override. To restore:

```sh
cd stacks/monitoring
sudo docker compose run --rm -v "$HOME/restore:/restore" --entrypoint restic backup snapshots
sudo docker compose run --rm -v "$HOME/restore:/restore" --entrypoint restic backup restore latest --target /restore
```

With Docker from the snap, write `sudo docker-compose` instead of `sudo docker compose`.

## Advanced: AWS

**Optional, and only for people comfortable with AWS and Terraform.** Everything above works
without it. In AWS, `terraform/` sets up:

- **Your own domain** in Route 53, kept on your home IP by the `route53` profile's updater (instead
  of DuckDNS). The server gets AWS access from a certificate (IAM Roles Anywhere), never a password:
  the same pattern as [a Raspberry Pi camera on AWS with no access keys](https://rrjamal.ca/articles/raspberry-pi-aws-without-access-keys).
- **Email through SES**, from your own domain.
- **The outside check:** a small function in AWS that opens your site every five minutes, emails
  you when it goes down or comes back, and, the part with no easy alternative, shows a
  **"we're offline" page** while your house is unreachable.

It costs about $0.50 a month (Route 53's charge for the hosted zone; the rest stays in AWS's free
tiers) and needs, before you start:

- an AWS account, and [Terraform](https://developer.hashicorp.com/terraform/install) 1.10+;
- your domain as a hosted zone in Route 53 (Terraform only adds records to it);
- an S3 bucket for Terraform's state (any name; `backend.hcl` says which);
- **your domain verified in SES**, in the **us-east-1** region (SES console > Identities > Create
  identity > Domain, then add the DKIM records it shows to Route 53; it can add them for you).
  Terraform doesn't do this, and email fails until it's done. Alerts go only to an address on
  that domain (`alert_email`, and `SMTP_URL`'s `to`): that works in SES's *sandbox*, where new
  accounts start, and the sending user Terraform makes is limited to it on purpose. Want alerts at
  another address? Use any email account for `SMTP_URL` instead ([Configuration](#configuration)).

Then:

```sh
cd terraform
cp backend.hcl.example backend.hcl && cp site.auto.tfvars.example site.auto.tfvars   # fill both in
terraform init -backend-config=backend.hcl
terraform apply
terraform output -raw server_env          # paste into .env
terraform output -raw server_cert_pem     # save as ~/.dns-updater/cert.pem on the server
terraform output -raw server_key_pem      # save as ~/.dns-updater/key.pem
```

On the server, make the folder for the certificate first (`mkdir -m 700 ~/.dns-updater`), and
`chmod 600 ~/.dns-updater/key.pem` after. Then in `.env`:

- `COMPOSE_PROFILES=downloads,https,route53`
- `JELLYFIN_DOMAIN` and `SEERR_DOMAIN`: the same names as `domain_name` and `seerr_domain_name` in
  `site.auto.tfvars`
- `SMTP_URL` for SES: its username and password are in AWS's parameter store, named in
  `terraform output ses_smtp`, along with the server and the `from` address to use:

  ```sh
  aws ssm get-parameter --region us-east-1 --name <username_parameter> --query Parameter.Value --output text
  aws ssm get-parameter --region us-east-1 --name <password_parameter> --with-decryption --query Parameter.Value --output text
  ```

  Then `SMTP_URL=smtp://USERNAME:PASSWORD@email-smtp.us-east-1.amazonaws.com:587/?from=<from>&to=<you@your-domain>`,
  with any `/` in the password written as `%2F` (and `+` as `%2B`).

and `./setup.sh --start`.

**The outside check's code** (`outside-check/`) goes into AWS with that first `terraform apply`,
and runs from then on. Terraform doesn't upload it again on its own; after changing it, run
`terraform apply -replace=aws_lambda_function.monitor`.

**Automatic deploys (optional):** to have GitHub upload the code on every push instead, as this
repo's [workflow](.github/workflows/outside-check.yaml) does (how that works without stored keys:
[Deploying from GitHub Actions to AWS without access keys](https://rrjamal.ca/articles/github-actions-aws-without-access-keys)):

1. Fork this repo (or push your copy to GitHub), and enable Actions on it.
2. Set `github_repository = "you/home-media-server"` in `site.auto.tfvars` and `terraform apply`.
   That creates a role only your repo's `main` branch can use, which can only update and run this
   one function. It needs GitHub's sign-in provider in your AWS account; if `apply` says there is
   none, create it once with the command in `terraform/monitor.tf`.
3. Add the secrets and switch the deploy on:

   ```sh
   terraform output -json github_actions_secrets \
     | python3 -c 'import json,sys; [print(k, v) for k, v in json.load(sys.stdin).items()]' \
     | while read -r name value; do gh secret set "$name" --body "$value"; done
   gh variable set OUTSIDE_CHECK_DEPLOY --body true
   ```

## Repository layout

```
setup.sh                      sets everything up; safe to re-run
.env.example                  every setting, explained (setup.sh copies it to .env)
stacks/                       one folder per stack, each with a README on what's in it
  media/                      Jellyfin, Caddy, DuckDNS and Route 53 updaters, the download apps
  monitoring/                 Beszel, Scrutiny, watchdog, weekly report, backup
  samba/                      Samba
  */compose.override.example.yaml   this machine's hardware: GPU, drives, extra shares
systemd/                      the units setup.sh installs
terraform/, outside-check/    the optional AWS setup
docs/router.md                port forwarding and router settings
CLAUDE.md                     guidance for Claude Code; your own server's notes go in CLAUDE.local.md
```

## Further reading

The why and the details behind this repo, in plain language:

- [Setting up a home media server with Docker, step by step](https://rrjamal.ca/articles/home-media-server-setup-guide): this repo, from start to finish
- [A home media server stack, reviewed](https://rrjamal.ca/articles/home-media-server-stack-reviewed): what Jellyfin, Sonarr, Radarr, Seerr and the rest each do
- [Monitoring a home media server](https://rrjamal.ca/articles/home-server-monitoring): the monitoring stack, the watchdog and the backup
- [Putting Jellyfin behind HTTPS with Caddy and Let's Encrypt](https://rrjamal.ca/articles/jellyfin-https-caddy-lets-encrypt): the `https` profile, and why a router's dynamic DNS name broke it
- [Samba in Docker](https://rrjamal.ca/articles/samba-docker-home-server): the samba stack
- [Tuning Jellyfin on an old server with no graphics card](https://rrjamal.ca/articles/tuning-jellyfin-old-server) and [What is transcoding?](https://rrjamal.ca/articles/what-is-transcoding): Jellyfin's settings, and the GPU override

## License

[MIT](LICENSE)
