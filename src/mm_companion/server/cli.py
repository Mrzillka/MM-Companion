"""The command line for ``python -m mm_companion.server``.

Kept apart from the package so importing :mod:`mm_companion.server` in a test
costs nothing but this module. The orchestration is split into small pure-ish
helpers (:func:`resolve_session`, :func:`build_transport`, :func:`describe`) that
tests drive directly, with :func:`run` wiring them together and blocking on a
stop signal.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

from mm_companion.core import mods, storage
from mm_companion.core.session import discovery, store
from mm_companion.core.session.model import SessionState, new_session
from mm_companion.core.session.net import DEFAULT_PORT, Transport
from mm_companion.core.session.relay import RelayTransport, relay_url
from mm_companion.core.session.server import SessionServer

from . import hub as hub_mod

log = logging.getLogger("mm_companion.server")

EPILOG = """\
Examples:
  python -m mm_companion.server --hub --relay relay.example.net \\
                                --admin-secret-file /etc/mm-companion/admin.secret
  python -m mm_companion.server --new "Friday Game"
  python -m mm_companion.server --session friday-game --relay relay.example.net
  python -m mm_companion.server --list

--hub is what an always-on server runs: it hosts every session in the workspace
at once, each reaching players by dialling out to the relay, and opens a control
channel GMs use to create, rename and delete sessions.

Creating a session is open to anyone running the app. What a session belongs to
is its gm token, handed back by the create and held by nobody else: renaming it,
deleting it and taking the GM's seat in it all need that token, and there is no
way to list other people's sessions at all. --admin-secret-file is the one
exception, for whoever runs the box: it grants the full list and the right to
delete anything, so abandoned or abusive sessions can be cleaned up.

Without --hub a single session is hosted: the one named by --session, a new one
from --new, or otherwise the most recently updated in the workspace. Either way
the session persists across restarts, resuming the same roster and roll history.
Set MM_COMPANION_HOME to share a workspace with the desktop app (or to keep the
server's own).

A GM drives a session hosted here by connecting with its gm token, which the
control channel hands out alongside the join code. They get their own seat, with
hidden rolls and conditions; nobody has to be at the table for players to play.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m mm_companion.server",
        description="Host an MM-Companion session headless, on an always-on box.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--session", metavar="ID", help="host an existing session by id")
    which.add_argument(
        "--new", metavar="NAME", help="create a new session with this name and host it"
    )
    which.add_argument(
        "--list", action="store_true", help="list the sessions in the workspace and exit"
    )
    which.add_argument(
        "--hub",
        action="store_true",
        help="host EVERY session in the workspace at once, and open a control channel a GM "
        "can create and delete sessions over - what an always-on server runs",
    )

    hub = parser.add_argument_group("hub mode")
    hub.add_argument(
        "--admin-secret-file",
        metavar="PATH",
        default="",
        help="file holding the OPERATOR secret - not needed to create a session, which is "
        "open to anyone; it grants whoever runs the box the full session list and the "
        "right to delete any of them. A file rather than an argument so it stays out of "
        "the process list. Omit it and the server simply has no caretaker",
    )
    hub.add_argument(
        "--retention-days",
        type=int,
        default=hub_mod.DEFAULT_RETENTION_DAYS,
        metavar="DAYS",
        help="delete a session nobody has touched in this long (0 disables the sweep); "
        "a join, a roll or a rename all count as touching it",
    )
    hub.add_argument(
        "--control-id",
        default=hub_mod.DEFAULT_CONTROL_ID,
        help="the relay session id the control channel registers under",
    )
    hub.add_argument(
        "--max-sessions", type=int, default=hub_mod.DEFAULT_MAX_SESSIONS, help="ceiling on sessions"
    )
    hub.add_argument(
        "--idle-unload",
        type=float,
        default=hub_mod.DEFAULT_IDLE_UNLOAD,
        metavar="SECONDS",
        help="drop an empty session's roll history from memory after this long; it stays "
        "joinable throughout and reloads on the next arrival",
    )

    parser.add_argument("--bind", default="0.0.0.0", help="address to listen on (default: all)")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"port to listen on (default: {DEFAULT_PORT}; 0 picks a free one)",
    )
    parser.add_argument("--gm-name", default="GM", help="the host's name in the roster")

    reach = parser.add_argument_group("reachability")
    reach.add_argument(
        "--relay",
        metavar="URL",
        default="",
        help="host through a relay (host, host:port, or mmrelay://host:port) instead of a "
        "listening socket - the only thing that works from behind carrier-grade NAT",
    )
    reach.add_argument(
        "--manual-host",
        metavar="HOST[:PORT]",
        default="",
        help="put this address in the join code instead of discovering one - the tunnel or "
        "hand-forwarded path",
    )
    reach.add_argument(
        "--no-upnp",
        action="store_true",
        help="skip the UPnP port-mapping attempt; publish only the LAN address",
    )

    parser.add_argument("-q", "--quiet", action="store_true", help="log warnings only")
    return parser


def resolve_session(args: argparse.Namespace) -> SessionState:
    """Load, create, or find the session the arguments name.

    Raises :class:`~mm_companion.core.session.store.SessionStoreError` when a
    named session is missing or when nothing is asked for and none exist - the
    caller turns that into a clean exit rather than a traceback.
    """
    if args.new:
        state = new_session(args.new)
        store.save_session(state, write_rolls=True)
        log.info("created session %r (id: %s)", state.name, state.id)
        return state
    if args.session:
        return store.load_session(args.session)
    summaries = store.list_sessions()
    if not summaries:
        raise store.SessionStoreError(
            "no sessions in the workspace yet - create one with --new NAME"
        )
    return store.load_session(summaries[0].id)


def build_transport(relay: str, state: SessionState) -> Transport | None:
    """A relay transport for *state* when a relay is configured, else ``None``.

    ``None`` means a plain listening socket - the direct rung of the ladder. A
    relay makes the GM's box dial *out*, which is what reaches it from behind a
    NAT no port forward can open.
    """
    if not relay:
        return None
    return RelayTransport(relay_url(relay, state.id))


def publish(
    server: SessionServer, transport: Transport | None, args: argparse.Namespace
) -> discovery.Reachability:
    """Work out the join code's address for a *running* server.

    Mirrors the GM window's ladder: a relay needs no probe (it accepted the
    session or the start would have failed), a manual host is taken at its word,
    and otherwise UPnP is tried unless ``--no-upnp`` said not to.
    """
    if isinstance(transport, RelayTransport):
        return discovery.relay_reachability(transport.url, transport.relay.port)
    port = server.address[1]
    if args.manual_host:
        host, manual_port = discovery.parse_address(args.manual_host, default_port=port)
        return discovery.publish_session(port, manual_host=host, external_port=manual_port)
    return discovery.publish_session(port, use_upnp=not args.no_upnp)


def describe(state: SessionState, server: SessionServer, reach: discovery.Reachability) -> str:
    """The banner printed once the session is up: the join code and how to reach it."""
    host, port = server.address
    code = reach.join_code(state.host_token)
    if reach.method == discovery.METHOD_RELAY:
        status = "relay (players anywhere can join, nothing to forward)"
    elif reach.internet_reachable:
        status = "internet (players anywhere can join)"
    elif reach.method == discovery.METHOD_MANUAL:
        status = f"manual - {reach.host}:{reach.port} (as you typed it)"
    else:
        status = "LAN only (players off your network need a relay or a tunnel - see --help)"

    lines = [
        "",
        f'Session "{state.name}" (id: {state.id})',
        f"  Listening on : {host}:{port}",
        f"  Reachability : {status}",
        "",
        f"  Join code    : {code}",
        "",
    ]
    if reach.advice:
        lines.append("  What to tell players / how to reach this box:")
        lines.extend(f"    - {line}" for line in reach.advice)
        lines.append("")
    lines.append("Press Ctrl+C to stop.")
    return "\n".join(lines)


def _install_signals(stop: threading.Event) -> None:
    def _stop(*_signal: object) -> None:
        stop.set()

    for name in ("SIGINT", "SIGTERM"):
        handler = getattr(signal, name, None)
        if handler is not None:
            try:
                signal.signal(handler, _stop)
            except (ValueError, OSError):
                # Not on the main thread (a test harness): the caller drives the
                # stop event itself, so a missing handler is fine.
                pass


def run(argv: list[str] | None = None, *, stop: threading.Event | None = None) -> int:
    """Parse *argv*, host the session, and block until interrupted.

    ``stop`` lets a test pre-set the stop event so :func:`run` returns instead of
    blocking; production leaves it ``None`` and waits on SIGINT/SIGTERM.
    """
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    storage.ensure_workspace()
    # Load trusted mods' Python so a session's condition/effect ids resolve the
    # same way here as in the GM's app; the fingerprint the handshake compares
    # comes from the same stack.
    mods.initialize_mods()

    if args.list:
        _print_sessions()
        return 0

    if args.hub:
        return _run_hub(args, stop)

    try:
        state = resolve_session(args)
    except store.SessionStoreError as exc:
        print(exc, file=sys.stderr)
        return 1

    transport = build_transport(args.relay, state)
    server = SessionServer(
        state,
        host=args.bind,
        port=int(args.port),
        transport=transport,
        mod_fingerprint=mods.stack_fingerprint(),
        gm_name=args.gm_name,
    )

    try:
        server.start()
    except OSError as exc:
        where = args.relay or f"{args.bind}:{args.port}"
        print(f"cannot host on {where}: {exc}", file=sys.stderr)
        return 1

    try:
        reach = publish(server, transport, args)
    except discovery.AddressError as exc:
        print(f"--manual-host {args.manual_host!r} is not a valid address: {exc}", file=sys.stderr)
        server.stop()
        return 2

    storage.update_settings(session_last_id=state.id)
    print(describe(state, server, reach))

    stop = stop if stop is not None else threading.Event()
    _install_signals(stop)
    try:
        stop.wait()
    except KeyboardInterrupt:
        pass  # Ctrl+C on a platform whose signal did not land in _install_signals
    finally:
        log.info("stopping session %s", state.id)
        server.stop()
        reach.release()
    return 0


def read_admin_secret(path: str) -> str:
    """The operator's secret, read from a file rather than an argument.

    A command line is world-readable in ``ps``; a 0640 file is not. Optional: no
    path means no operator, which costs the box its caretaker but stops nobody
    from hosting a game on it.
    """
    if not path:
        return ""
    try:
        secret = Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise hub_mod.HubError(f"cannot read {path}: {exc}") from exc
    if not secret:
        raise hub_mod.HubError(f"{path} is empty; delete the file or put a secret in it")
    return secret


def describe_hub(hub: hub_mod.SessionHub) -> str:
    """The banner: every session it is holding, and the code for each."""
    catalog = hub.catalog()
    lines = ["", f"Hosting {len(catalog)} session(s) through {hub.relay_base}", ""]
    for entry in catalog:
        lines.append(f'  {entry["name"]!r}  (id: {entry["id"]})')
        lines.append(f'    Join code : {entry["join_code"]}')
        lines.append(f'    Players   : {entry["player_count"]}, {entry["roll_count"]} roll(s)')
        lines.append("")
    if not catalog:
        lines.append("  No sessions yet. Any GM can make one from the app's GM Mode.")
        lines.append("")
    lines.append(f"Control channel : {hub.control_id} (open — anyone may create a session)")
    lines.append(
        "Operator        : "
        + ("yes, a secret is configured" if hub.admin_secret else "none — nobody can clean up")
    )
    if hub.retention_days > 0:
        lines.append(f"Sweep           : sessions untouched for {hub.retention_days} days")
    lines.append("Press Ctrl+C to stop.")
    return "\n".join(lines)


def _run_hub(args: argparse.Namespace, stop: threading.Event | None) -> int:
    """Host every session at once until interrupted."""
    try:
        secret = read_admin_secret(args.admin_secret_file)
        if not args.relay:
            raise hub_mod.HubError(
                "--hub needs --relay: sessions reach players by dialling out to one"
            )
        hub = hub_mod.SessionHub(
            args.relay,
            secret,
            mod_fingerprint=mods.stack_fingerprint(),
            max_sessions=args.max_sessions,
            idle_unload=args.idle_unload,
            retention_days=args.retention_days,
            control_id=args.control_id,
        )
    except hub_mod.HubError as exc:
        print(exc, file=sys.stderr)
        return 2

    hub.start()
    print(describe_hub(hub))

    stop = stop if stop is not None else threading.Event()
    _install_signals(stop)
    try:
        stop.wait()
    except KeyboardInterrupt:
        pass
    finally:
        log.info("stopping the hub")
        hub.stop()
    return 0


def _print_sessions() -> None:
    summaries = store.list_sessions()
    if not summaries:
        print("No sessions in the workspace yet. Create one with --new NAME.")
        return
    print(f"{len(summaries)} session(s) in the workspace, newest first:\n")
    for summary in summaries:
        print(
            f"  {summary.id}  -  {summary.name!r}  "
            f"({summary.player_count} player(s), {summary.roll_count} roll(s), "
            f"updated {summary.updated_at})"
        )
