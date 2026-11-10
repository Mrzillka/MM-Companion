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
#   3. Restart the hub. The relay restart drops every session's registration;
#      a hub from before 0.9.5 gives up re-registering after five minutes and
#      leaves every table unjoinable (it happened on 2026-09-26). Newer hubs
#      retry for as long as they run, so this is belt and braces there. It costs
#      nothing: the relay restart has already dropped every connection. The pause
#      lets the relay start listening first, since an older hub skips any session
#      it cannot register at boot.
set -eu

setfacl -R -m u:mm:rX /etc/letsencrypt/live /etc/letsencrypt/archive

systemctl restart mm-relay.service

sleep 5
systemctl restart mm-sessions.service
