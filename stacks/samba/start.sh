#!/bin/sh
# nmbd answers NetBIOS name lookups, which Windows uses to find \\SERVER; smbd serves the shares.
# Both log to stdout, so to `docker logs samba`. If smbd exits, the container does and Docker
# restarts it.
nmbd --foreground --no-process-group --debug-stdout &
exec smbd --foreground --no-process-group --debug-stdout
