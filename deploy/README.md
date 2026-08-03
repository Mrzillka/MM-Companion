# Deploying the MM-Companion server

Everything in this directory is **secret-free and tracked in git**. The machine's
address, the SSH key, and the admin secret are not here — they live in the
gitignored `SERVER.md` at the repo root and in `/etc/mm-companion/` on the box.

## What runs where

The server does two jobs, as two systemd units on one machine:

| Unit | What it is | Port |
| --- | --- | --- |
| `mm-relay.service` | The TLS byte-pump (`python -m mm_companion.relay`). Pairs two connections that both dialled *out* and forwards bytes between them. Holds no state and parses no game data. | 47332 (public) |
| `mm-sessions.service` | The session hub (`python -m mm_companion.server --hub`). Stores every session and hosts each one, reaching players by dialling out to the relay above. | none — outbound only |

The hub needs **no inbound port of its own**: it registers each session with the
relay, exactly as a GM's laptop does. So 47332 is the only door in the wall, and
it is TLS.

```
player's app ──TLS──┐                    ┌── session "friday-game"
                    ├─► mm-relay :47332 ─┤
GM's app     ──TLS──┘                    └── session "the-vault"
                                              (both inside mm-sessions)
```

## First-time setup on a fresh box

Assumes Ubuntu, a public IPv4, and a DNS name already pointing at it.

```bash
apt update && apt install -y python3-venv git certbot acl unattended-upgrades

# Firewall: 22 for you, 80 for certificate renewal, 47332 for the relay.
ufw default deny incoming && ufw default allow outgoing
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 47332/tcp
ufw enable

# An unprivileged user to run both services as.
adduser --disabled-password --gecos "" mm
sudo -u mm git clone https://github.com/Mrzillka/MM_Companion.git /home/mm/MM_Companion
sudo -u mm python3 -m venv /home/mm/MM_Companion/.venv

# The certificate. --standalone binds port 80 for the challenge, so nothing else
# may be listening on it.
certbot certonly --standalone -d YOUR.DOMAIN --agree-tos -m you@example.com

cd /home/mm/MM_Companion && ./deploy/deploy.sh
```

`deploy.sh` prints the **admin secret** on its first run. That is the credential a
GM enters once in the app to create sessions; copy it then, or read it back later
with `cat /etc/mm-companion/admin.secret`.

## Updating

```bash
cd /home/mm/MM_Companion && ./deploy/deploy.sh              # current branch
cd /home/mm/MM_Companion && ./deploy/deploy.sh develop      # switch branches
```

### Ordering, for the release that restores the stock idle timeout

The relay never parses a session message — it is a byte pump with a six-tag
envelope vocabulary — so upgrading it is completely decoupled from the app's
protocol version. That cuts both ways, and one combination is dangerous:

| | old app (pre-v7) | new app (v7) |
|---|---|---|
| **relay with the 4 h override** | works (today) | works |
| **relay on the stock 120 s** | **breaks after 2 min** | works |

An old GM and old players talk v6 to each other perfectly well and merely pass
*through* this relay, so the protocol bump does not protect them here. Restoring
the stock timeout on the box before the table has updated re-introduces the exact
bug it was working around.

So:

1. Ship the app release carrying the keepalive (protocol v7).
2. Confirm the table is on it. This is self-enforcing for *joining* — a v6 client
   cannot join a v7 session at all — so once a game is actually being played,
   everyone is v7.
3. Only then `./deploy/deploy.sh` and `sudo systemctl restart mm-relay`.

Rolling back is the reverse and is always safe: an old relay with the 4 h
override serves a new app fine, since the keepalive is then just belt-and-braces
against a limit that never fires.

## Reading the logs

```bash
systemctl status mm-relay mm-sessions
journalctl -u mm-sessions -f              # follow
journalctl -u mm-relay --since "1 hour ago"
journalctl -u mm-sessions -p warning      # warnings and worse only
```

## Checking it from outside

```bash
# Does the port answer, and is the certificate good?
openssl s_client -connect YOUR.DOMAIN:47332 -servername YOUR.DOMAIN </dev/null \
    | grep -E "subject=|Verify return code"
```

`Verify return code: 0 (ok)` is the answer you want. From Windows,
`Test-NetConnection YOUR.DOMAIN -Port 47332` checks reachability alone, without
saying anything about TLS.

## Certificate renewal

`certbot.timer` renews automatically. Renewal *alone* is not enough — the relay
loads its certificate once at startup — so
`/etc/letsencrypt/renewal-hooks/deploy/mm-companion.sh` (installed from
`certbot-deploy-hook.sh` by `deploy.sh`) re-applies the ACL that lets `mm` read
the new key and restarts the relay. Verify the whole path without waiting for
expiry:

```bash
certbot renew --dry-run
```

Port 80 must stay open in `ufw` for renewal to work. Closing it is a failure that
only shows up 60 days later.

## Backups

Everything worth keeping is `/var/lib/mm-companion/sessions/` — one directory per
session, each a `session.json` plus an append-only `rolls.jsonl`. Copy that
directory and you have copied every table.

```bash
tar czf ~/mm-sessions-$(date +%F).tar.gz -C /var/lib/mm-companion sessions
```

## Rolling back

The units run whatever is checked out at `/home/mm/MM_Companion`, so a rollback
is a checkout plus a restart:

```bash
sudo -u mm git -C /home/mm/MM_Companion checkout <last-good-tag>
/home/mm/MM_Companion/deploy/deploy.sh
```

Session data on disk is independent of the code, so it survives a rollback.
