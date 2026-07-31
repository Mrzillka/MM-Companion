#!/usr/bin/env bash
# Push the current code to the box and restart what runs there.
#
#   sudo ./deploy.sh            # pull the checked-out branch and restart
#   sudo ./deploy.sh develop    # switch to a branch first
#
# Idempotent: safe to run twice, and safe to run when nothing changed. Run it as
# root on the server (it installs unit files and touches /etc); the checkout
# itself is done as `mm` so file ownership stays right.
set -euo pipefail

APP_DIR=/home/mm/MM_Companion
WORKSPACE=/var/lib/mm-companion
CONFIG_DIR=/etc/mm-companion
BRANCH="${1:-}"

if [ "$(id -u)" -ne 0 ]; then
    echo "run this as root (it installs systemd units)" >&2
    exit 1
fi

echo "==> updating the checkout"
if [ -n "$BRANCH" ]; then
    sudo -u mm git -C "$APP_DIR" fetch --all --prune
    sudo -u mm git -C "$APP_DIR" checkout "$BRANCH"
fi
sudo -u mm git -C "$APP_DIR" pull --ff-only

echo "==> installing the package"
sudo -u mm "$APP_DIR/.venv/bin/pip" install --quiet --editable "$APP_DIR"

echo "==> ensuring directories"
install -d -o mm -g mm -m 750 "$WORKSPACE"
install -d -o root -g mm -m 750 "$CONFIG_DIR"

# The one credential that may create or delete a session. Minted once, then left
# alone -- rotating it invalidates the address book in every GM's app.
if [ ! -f "$CONFIG_DIR/admin.secret" ]; then
    echo "==> minting an admin secret (first run)"
    "$APP_DIR/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))' \
        > "$CONFIG_DIR/admin.secret"
    chown root:mm "$CONFIG_DIR/admin.secret"
    chmod 640 "$CONFIG_DIR/admin.secret"
    echo
    echo "    Admin secret (put this in the app, GM Mode -> connect to server):"
    echo "        $(cat "$CONFIG_DIR/admin.secret")"
    echo
fi

echo "==> letting mm read the TLS certificate"
setfacl -R -m u:mm:rX /etc/letsencrypt/live /etc/letsencrypt/archive

echo "==> installing units and the renewal hook"
install -m 644 "$APP_DIR/deploy/mm-relay.service"    /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/mm-sessions.service" /etc/systemd/system/
install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
install -m 755 "$APP_DIR/deploy/certbot-deploy-hook.sh" \
    /etc/letsencrypt/renewal-hooks/deploy/mm-companion.sh

systemctl daemon-reload
systemctl enable --now mm-relay.service
systemctl enable --now mm-sessions.service
systemctl restart mm-relay.service mm-sessions.service

echo "==> status"
systemctl --no-pager --lines=5 status mm-relay.service mm-sessions.service || true
