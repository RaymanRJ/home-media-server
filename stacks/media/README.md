# The media stack

Jellyfin, and everything around it that's about watching: HTTPS for away from home, and the apps
that find and download what people ask for. Jellyfin always runs; the rest only with its profile
in `COMPOSE_PROFILES` (in the repo's `.env`).

| Service      | What it does                                                                                     | Port      | Profile     |
| ------------ | ------------------------------------------------------------------------------------------------ | --------- | ----------- |
| Jellyfin     | Plays your films and shows on TVs, phones and browsers; reads your media folder, never changes it | 8096      | always      |
| Caddy        | HTTPS on your own web address, with certificates from Let's Encrypt; sends visitors to Jellyfin and Seerr | 80, 443   | `https`     |
| duckdns      | Keeps a free `yourname.duckdns.org` address pointing at your home, every five minutes            |           | `duckdns`   |
| dns-updater  | The AWS alternative: keeps your own domain in Route 53 pointing at your home                     |           | `route53`   |
| Seerr        | The request page: people sign in with their Jellyfin account and ask for a show or film          | 5055      | `downloads` |
| Sonarr       | Follows TV shows: picks each episode's best release and files it into your TV folder             | 8989      | `downloads` |
| Radarr       | The same for films                                                                               | 7878      | `downloads` |
| Prowlarr     | Keeps the list of sites to search, for Sonarr and Radarr                                         | 9696      | `downloads` |
| FlareSolverr | Gets Prowlarr past sites protected by Cloudflare                                                 |           | `downloads` |
| qBittorrent  | Does the downloading, into `downloads` in your media folder                                      | 8081, 6881| `downloads` |
| Bazarr       | Finds subtitles for what Sonarr and Radarr have                                                  | 6767      | `downloads` |
| Recyclarr    | Keeps Sonarr's and Radarr's quality settings in step with [TRaSH Guides](https://trash-guides.info) (`recyclarr/recyclarr.yml`) |           | `downloads` |
| Cleanuparr   | Removes downloads that are stuck, dead or fake, so Sonarr and Radarr try another                 | 11011     | `downloads` |

Ports 80, 443 and 6881 are the only ones meant to be forwarded by your router; the rest are for
home only.

## Paths and addresses to use in the apps

Every app sees your media folder as `/media`, and reaches the others by name:

| App         | Setting                                   | Use                                                  |
| ----------- | ----------------------------------------- | ---------------------------------------------------- |
| Jellyfin    | Libraries                                 | `/media/Movies`, `/media/TV` (and any other folders under `/media`) |
| qBittorrent | Tools > Options > Downloads: save path    | `/media/downloads` (without this, downloads land inside the container) |
| Sonarr      | Root folder; download client              | `/media/TV`; qBittorrent at `qbittorrent`, port `8081` |
| Radarr      | Root folder; download client              | `/media/Movies`; qBittorrent at `qbittorrent`, port `8081` |
| Sonarr, Radarr | Connect > Emby / Jellyfin              | `host.docker.internal`, port `8096`                  |
| Prowlarr    | Settings > Apps; indexer proxy            | `http://sonarr:8989`, `http://radarr:7878`; FlareSolverr at `http://flaresolverr:8191` |
| Seerr       | Jellyfin; Sonarr and Radarr               | `http://host.docker.internal:8096`; `sonarr` port `8989`, `radarr` port `7878` |
| Bazarr      | Sonarr and Radarr                         | `sonarr` port `8989`, `radarr` port `7878`           |
| Cleanuparr  | qBittorrent, Sonarr, Radarr               | `http://qbittorrent:8081`, `http://sonarr:8989`, `http://radarr:7878` |

**In this folder:** `compose.yaml`; `Caddyfile` (Caddy's settings); `dns-updater/` (the Route 53
updater's image and script); `recyclarr/recyclarr.yml`; `compose.override.example.yaml` (copy to
`compose.override.yaml` to give Jellyfin your CPU's graphics for faster transcoding).

## Read more

- [Setting up a home media server with Docker, step by step](https://rrjamal.ca/articles/home-media-server-setup-guide): this stack, set up in order
- [A home media server stack, reviewed](https://rrjamal.ca/articles/home-media-server-stack-reviewed): what each of the download apps does, and how a request becomes a film on your TV
- [Putting Jellyfin behind HTTPS with Caddy and Let's Encrypt](https://rrjamal.ca/articles/jellyfin-https-caddy-lets-encrypt): the `https` profile
- [Tuning Jellyfin on an old server with no graphics card](https://rrjamal.ca/articles/tuning-jellyfin-old-server) and [What is transcoding?](https://rrjamal.ca/articles/what-is-transcoding): Jellyfin's settings, and the graphics override
- [Leaving Plex for Jellyfin to keep remote streaming free](https://rrjamal.ca/articles/leaving-plex-for-jellyfin): why Jellyfin
- [A Raspberry Pi camera on AWS with no access keys](https://rrjamal.ca/articles/raspberry-pi-aws-without-access-keys): how the `route53` updater gets into AWS without a password
