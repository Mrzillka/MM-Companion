# Playing over the internet: tunnels, the relay, and troubleshooting

This is the practical guide to getting a session reachable and fixing it when a
join fails. For how the pieces fit together, see
[`mm-session-architecture.md`](mm-session-architecture.md).

The short version: **your app tries to make itself reachable automatically, and
tells you on screen what it managed and what to do next.** Most of the time you
start hosting, hand the players the join code, and that is it. When it is not,
the advice under the join code names the specific fix — and this guide expands on
each one.

## How a player joins

1. The **GM** opens GM Mode and clicks **Start hosting**. A **join code** and a
   short block of advice appear.
2. The GM shares the join code (Copy button).
3. A **player** clicks **Join Session** on the launcher, pastes the code, picks
   a display name and (optionally) one of their saved characters, and connects.

The join code encodes the address, port, and the session's join secret, so it is
the only thing a player needs. It is per-session and ephemeral.

## Why "just forward a port" often is not enough

A join works when the player's connection can reach the GM's machine. On a home
connection that usually means getting *through* the router's NAT, and there are
three cases, worst to best for you:

- **Carrier-grade NAT (CGNAT).** Your internet provider shares one public address
  across many customers. **No port forward — automatic or manual — can ever make
  your machine reachable.** This is common on mobile broadband, fibre resellers,
  and student housing, and the dev machine this was built on is behind it. Use a
  **tunnel** or the **relay** (below).
- **Ordinary NAT, UPnP off.** Your router *could* forward a port but will not do
  it automatically. Either turn on UPnP / forward the port by hand, or use a
  tunnel.
- **Ordinary NAT, UPnP on.** The app forwards the port for you and players can
  connect directly. Nothing to do.

The app detects which case you are in as best it can and shows the matching
advice. When it cannot make you internet-reachable, it says so in red rather than
quietly handing out a LAN-only address that looks like success.

## Path 1 — a tunnel (works today, no server of your own)

A tunnel is a third-party service that gives you a public address and forwards it
to your machine. It works from behind CGNAT because *your* end dials out to the
tunnel.

Using [playit.gg](https://playit.gg) as the example (ngrok and Tailscale Funnel
work the same way):

1. Install and run the tunnel client. Create a **TCP** tunnel pointing at the
   port MM-Companion is hosting on (shown in GM Mode; `47331` by default, or set a
   fixed one in the **Port** field before hosting).
2. The tunnel gives you a public address like `something.playit.gg:12345`.
3. In GM Mode, paste that into the **"I'm using a tunnel"** field and start
   hosting. The join code now carries the tunnel's hostname and port.
4. Share the code. Players need **nothing** — they just join.

The GM window takes a typed tunnel address at its word: it never falls back to the
relay, because you told it how you are reachable.

## Path 2 — the relay (players anywhere, nothing to install)

The relay is a public box that both ends dial *out* to; it splices the two
connections and pumps bytes between them. Because both ends connect outbound, it
works from behind every NAT, CGNAT included, and **players still need nothing but
the join code**.

To use one, put its address in the **Relay address** field in GM Mode and tick
**"Use the relay if this machine cannot be reached."** The app still tries a
direct connection first — a direct connection costs the relay nothing — and only
falls back to the relay when a direct host comes back not-internet-reachable. If
the relay itself cannot be reached, the window says so and returns to direct
hosting rather than failing silently.

> **There is no default public relay bundled yet.** Point the field at one you
> run (below) or one your group runs. Deciding where a blessed public instance
> lives is a deployment question, deliberately left open.

### Running your own relay

The relay ships as a stdlib-only entrypoint — no app data, no accounts, no state:

```bash
# Plaintext, for a trusted network or a quick test:
python -m mm_companion.relay --port 47332

# With TLS (what an mmrelay:// address expects on the public internet):
python -m mm_companion.relay \
    --cert /etc/letsencrypt/live/relay.example.net/fullchain.pem \
    --key  /etc/letsencrypt/live/relay.example.net/privkey.pem
```

Then GMs set their **Relay address** to `relay.example.net` (TLS is assumed) or
`host:port`. The relay's caps are all CLI options — `--max-sessions`,
`--max-clients` (per session), `--rate` / `--burst` (per-session throughput),
`--idle-timeout`, and `--session-ttl`; run `python -m mm_companion.relay --help`
for the full list and defaults.

**What the relay is and is not.** It parses *only* its own envelope to learn which
two connections to pair, then forwards bytes verbatim; it never dials outward, so
an open relay cannot be turned into a general proxy against third parties. It
holds no session state and needs no protocol-version bumps. Because it is a dumb
pipe, one small box carries thousands of tables — the binding limit is concurrent
sockets, not bandwidth (a table-hour is a few MB). **TLS terminates at the
relay**, so its operator could in principle read the traffic (character sheets and
dice rolls); anyone who minds should run their own — it is the one command above.

## Path 3 — a headless always-on host

If you would rather the table not depend on your laptop being open, run the
session on an always-on machine (a home server, a cheap VPS):

```bash
# Create and host a new session:
python -m mm_companion.server --new "Friday Game"

# Resume the most recently used session (the default with no --session/--new):
python -m mm_companion.server

# List what is in the workspace:
python -m mm_companion.server --list

# Host through a relay, so the box needs no inbound port at all:
python -m mm_companion.server --session friday-game --relay relay.example.net
```

It runs the same session server the app hosts with, prints the join code and a
reachability banner, and persists everything to the workspace — stopping and
restarting resumes the same roster and roll history. Point it at a workspace with
`MM_COMPANION_HOME` (share the app's, or give the server its own). Stop it with
Ctrl-C (or SIGTERM), and it shuts the session down cleanly.

> **Caveat:** a GM cannot yet *drive* a headless session remotely — hidden rolls
> and GM-applied conditions need the in-process host. The box keeps the session
> alive, resolves everyone's rolls, and syncs sheets; the GM joins it as a player
> until remote-GM auth lands. See the deferred list in the architecture doc.

## Troubleshooting

The advice under the join code is written to be actionable on its own; this table
is the same guidance gathered in one place.

| What you see | What it means | What to do |
| --- | --- | --- |
| **LAN only** (red) | The app could not make you reachable from the internet — commonly CGNAT or UPnP being off. | Use a tunnel (Path 1) or the relay (Path 2). Players on your own network / VPN can still join with the address shown. |
| "carrier-grade NAT" | Your ISP shares one public address; no port forward can help. | Tunnel or relay. Or ask your ISP for a public IP, or host on a machine that has one. |
| "a second router in front of it" (double NAT) | There is another router upstream. | If it is yours, forward the port on it too. If it is the ISP's, use a tunnel or relay. |
| "your router refused to forward the port" | UPnP was found but declined. | Forward the port by hand in the router admin page, or use a tunnel. |
| "that external port is already forwarded" | The port maps to something else. | Host on a different **Port**, or remove the stale rule in your router. |
| The **first** player triggers a Windows prompt | The firewall is asking whether to allow incoming connections. | Allow MM-Companion on **both** private and public networks, or nobody can join. |
| "the GM and this client load different mods" | A mod-fingerprint mismatch (a warning, not a refusal). | The session works, but conditions/effects from the other side may not resolve. Line up your enabled mods for a clean game. |
| "this session speaks protocol vN, you speak vM" | The two apps are different enough versions to be incompatible. | Update both to the same release. |
| "that join code is not for this session" / "has a typo" | Wrong or mistyped code. | Re-copy the code from the GM; the checksum catches most typos before a doomed connection attempt. |
| The relay is unreachable / at capacity | The relay box is down or full. | The app returns to direct hosting; use a tunnel, run your own relay, or ask the GM to forward a port. |

## What is protected, and what is not

- **The listening port is guarded** by the join secret in the code, JSON-only
  decoding (never `pickle`), a message-size cap, a max-clients limit, and a
  per-connection rate limit.
- **The server is authoritative for rolls**, so no client can fake a die, and
  **hidden GM rolls never reach a player client** at all.
- **Over a relay, the transport is TLS**, so nobody on the network path can read
  it — but the relay operator terminates that TLS, so self-host if you need the
  operator not to be able to. The data is character sheets and dice rolls, and the
  join secret is per-session and ephemeral.
