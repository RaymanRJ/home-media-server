# Samba for the shares. Ubuntu 24.04's packages (Samba 4.19), so a password
# database from an Ubuntu 24.04 host carries over as it is. samba-vfs-modules has the Mac modules
# (fruit, streams_xattr, catia); it's only recommended, so it's named here.
FROM ubuntu:noble-20260930.1

# The Samba user, with the host user's uid and gid (from .env, through the Compose file), so files
# written over SMB belong to the same user as on the host. The image's own `ubuntu` user (uid 1000)
# is removed first so the uid is free.
ARG SAMBA_USER=smbuser
ARG SAMBA_UID=1000
ARG SAMBA_GID=1000
RUN set -e; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends samba samba-vfs-modules; \
    rm -rf /var/lib/apt/lists/*; \
    mkdir -p /run/samba; \
    userdel -r ubuntu; \
    groupadd -g "$SAMBA_GID" "$SAMBA_USER"; \
    useradd -u "$SAMBA_UID" -g "$SAMBA_GID" -M -s /usr/sbin/nologin "$SAMBA_USER"

# smbd and nmbd: Windows finds the server through NetBIOS (nmbd); Macs and phones through mDNS,
# announced by the host's Avahi (avahi/smb.service).
COPY start.sh /start.sh
ENTRYPOINT ["/bin/sh", "/start.sh"]
