# The router

What to set on the home router for Jellyfin, and why. None of it lives in config anywhere, so this
is the record of it. `jellyfin.example.com` stands for your real name.

Menu paths are from a TP-Link router's web admin page (usually `http://192.168.0.1`), under the
**Advanced** tab. Other routers have the same settings under other names.

## How a request gets in

```
browser ──DNS──▶ Route 53: jellyfin.example.com  A  <home IP>   (kept current by the server's dns-updater)
browser ──TCP──▶ <home IP>:443 ──router forward──▶ <server LAN IP>:443 (Caddy) ──▶ localhost:8096 (Jellyfin)
```

The router's only job is getting the traffic to the server. Keeping the name on the home IP is the
server's job (the dns-updater), so nothing depends on the router's make or its
dynamic DNS service. A new router only needs the settings below.

## Settings

| Setting | Where | Value | Why |
| --- | --- | --- | --- |
| Dynamic DNS | Network > Dynamic DNS | Off | **Not used.** A CNAME to a TP-Link DDNS name breaks Let's Encrypt: TP-Link's nameservers fail the checks strict resolvers make, so it gets SERVFAIL. The dns-updater keeps A records current instead. |
| Server's LAN IP | Network > DHCP Server > Address Reservation | The server's MAC, a fixed `<server LAN IP>` | The forwards below target this IP. If DHCP hands the server a new one, they silently break. |
| Forward 80 | NAT Forwarding > Virtual Servers | TCP 80 → `<server LAN IP>`:80 | Caddy: Let's Encrypt's certificate check, and the `http://` → `https://` redirect. |
| Forward 443 | NAT Forwarding > Virtual Servers | TCP 443 → `<server LAN IP>`:443 | Caddy: HTTPS, which it proxies to Jellyfin. |
| Forward 6881 | NAT Forwarding > Virtual Servers | TCP and UDP 6881 → `<server LAN IP>`:6881 (protocol "All") | qBittorrent's torrent port, so peers can connect in. Only the torrent protocol answers there; its web UI stays home only. |
| Remote Management | System > Administration | Off, or on a port other than 80 and 443 | If the router's own admin page holds 80 or 443 on the WAN side, the forwards don't reach the server. |

## What's exposed

The internet can reach the server on **80** and **443**, both answered by Caddy, and on **6881**
(qBittorrent's torrent port). 80 only redirects to HTTPS and serves certificate checks. **8096** is
not forwarded: Jellyfin's own port is unencrypted. Inside the house, `http://<server LAN IP>:8096`
still works, since that doesn't go through the router's forwards.

The download apps are home only too: none of their ports is forwarded. Seerr,
the request page, is public through Caddy on its own name, on the same 80 and 443, so it needs no
forward of its own. qBittorrent's one forward is its torrent port, 6881 (an explicit rule above,
not UPnP; without it, thin public swarms barely move). Its UPnP stays off, because with the router's
UPnP on (NAT Forwarding > UPnP) any device that asks gets a port opened without a rule showing
here. Turning the router's UPnP off as well would close that for every device; game consoles are
what usually rely on it.

Adding or removing a forward changes what's reachable from the internet. Keep this table in step
with the router when that happens.

## Quirks

- **The router caches "not found" answers.** Devices use the router for DNS, and it remembers a
  lookup that failed. A name looked up before its Route 53 record existed keeps failing at home for
  up to 15 minutes after it's created, while it already works on mobile data. Wait it out, or check
  with `dig jellyfin.example.com @8.8.8.8`. A Mac caches it too:
  `sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder`.
- **NAT loopback works** on most routers. From home Wi-Fi, the public name and IP reach the server
  through the router, so apps can use `https://jellyfin.example.com` at home as well.
- **After a power cut, the forwards can stop working.** Some TP-Link firmware comes back with the 80
  and 443 rules listed and enabled, but its own web server answering both ports on the public IP (a
  `tplinkwifi.net` self-signed certificate, then the router login), even with Remote Management
  off. Switching both rules off, saving, and on again fixes it. A browser that saw the router's
  certificate meanwhile keeps saying "Not secure" until it's fully quit. The outside check
  emails a `tls` result within about ten minutes when it happens.
- **Test from outside on mobile data,** not home Wi-Fi. At home, loopback and the router's DNS
  cache can make the result differ from what everyone else sees.
