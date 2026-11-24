# The samba stack

Shared folders on your home network, for Macs, Windows PCs and phones. Start it with
`./setup.sh --start samba`, then give its user a password once (the main README's "File shares").

| Service | What it does                                                                                     | Ports         |
| ------- | ------------------------------------------------------------------------------------------------ | ------------- |
| Samba   | Shares `SAMBA_SHARE_DIR` (from `.env`); one user, no guests. Windows finds it by name through NetBIOS | 445, 139 (TCP); 137, 138 (UDP) |

Macs and phones find the server by name through the host's Avahi (mDNS), from `avahi/smb.service`,
which `setup.sh` installs: Docker won't let Samba announce itself. Home only: never forward these
ports.

**In this folder:** `compose.yaml`; `Dockerfile` and `start.sh` (Ubuntu 24.04's Samba);
`smb.conf` (the shares and Mac-friendly settings); `avahi/smb.service`;
`compose.override.example.yaml` (copy to `compose.override.yaml` to share more folders, with your
own copy of `smb.conf` in `~/.samba`).

## Read more

- [Samba in Docker: moving a home server's file shares without breaking Windows, Mac or phone](https://rrjamal.ca/articles/samba-docker-home-server): how this stack works, and the AppArmor rule that hid it from Macs
