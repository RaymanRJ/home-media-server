# The monitoring stack

Keeps an eye on the server so you don't have to: its health, its drives, and every app in all
three stacks, plus the nightly backup and a weekly email. Start it with
`./setup.sh --start monitoring`.

| Service       | What it does                                                                                              | Port | Runs                    |
| ------------- | --------------------------------------------------------------------------------------------------------- | ---- | ----------------------- |
| Beszel        | Charts of CPU, memory, temperatures, disk space and each app's use, with alerts                           | 8090 | always                  |
| Beszel agent  | Collects those numbers for Beszel (its key and token go in `.env`)                                        |      | always                  |
| Scrutiny      | Reads each drive's health (SMART) daily and warns before one fails                                        | 8080 | always                  |
| watchdog      | Restarts an app that stops answering (up to three times) and emails you; also emails if the backup fails  |      | always                  |
| weekly-report | A Monday email: who watched how much, what was added, disk space, drive health, the last backup           |      | weekly, with `SMTP_URL` and `JELLYFIN_API_KEY` |
| backup        | Copies every app's settings and `.env`, encrypted, to a shared folder on a NAS ([restic](https://restic.net)) |      | nightly, with `RESTIC_PASSWORD` |

Emails go through `SMTP_URL` in `.env` (any email account). Without it the watchdog still restarts
stuck apps; it just can't tell you. The weekly report also needs Jellyfin's **Playback Reporting**
plugin (Dashboard > Plugins > Catalog) and `JELLYFIN_API_KEY`. The backup needs a shared folder
that allows guests, or a login in `BACKUP_SHARE_OPTS`. Scrutiny has no login, so keep it home only.

**In this folder:** `compose.yaml`; `watchdog/`, `weekly-report/` (small Python scripts, standard
library only); `backup/` (the backup script and what it skips); `scrutiny/collector.yaml` (which
drives Scrutiny reads); `compose.override.example.yaml` (copy to `compose.override.yaml` to list
your drives and anything more to back up).

## Read more

- [Monitoring a home media server: how I know it's working](https://rrjamal.ca/articles/home-server-monitoring): each check, what it watches and can't see, and the backup behind them
- [Setting up a home media server with Docker, step by step](https://rrjamal.ca/articles/home-media-server-setup-guide): setting this stack up
