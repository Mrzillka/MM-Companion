#!/bin/sh
# Installed at /etc/letsencrypt/renewal-hooks/deploy/mm-companion.sh
#
# certbot runs every script in that directory after a certificate is *actually*
# renewed (not on every timer tick). Two things have to happen then:
#
#   1. Re-apply the ACL that lets the unprivileged `mm` user read the key. A
#      renewal writes a NEW file under /etc/letsencrypt/archive, and a new file
#      does not inherit the ACL we set on the old one, so without this the relay
#      comes back unable to read its own key.
#   2. Restart the relay, which loads the cert once at startup and would
#      otherwise keep serving the expired one until someone noticed.
set -eu

setfacl -R -m u:mm:rX /etc/letsencrypt/live /etc/letsencrypt/archive

systemctl restart mm-relay.service
