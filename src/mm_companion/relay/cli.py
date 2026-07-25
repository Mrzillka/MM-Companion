"""The command line for ``python -m mm_companion.relay``.

Kept apart from the server itself so importing :mod:`mm_companion.relay` in a
test (or in the app) costs nothing but the loop.
"""

from __future__ import annotations

import argparse
import logging
import signal
import ssl
import sys

from . import DEFAULT_LIMITS, RelayLimits, RelayServer

EPILOG = """\
Examples:
  python -m mm_companion.relay --port 47332
  python -m mm_companion.relay --cert /etc/letsencrypt/live/relay.example/fullchain.pem \\
                               --key  /etc/letsencrypt/live/relay.example/privkey.pem

Players never configure a relay: the GM enters its address in GM Mode, and the
join code they hand out carries it. Without --cert/--key the relay speaks in the
clear, which is fine on a trusted network but should not face the internet — the
app expects TLS on an mmrelay:// address.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m mm_companion.relay",
        description="Pair MM-Companion sessions between two connections that both dial out.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on (default: all)")
    parser.add_argument("--port", type=int, default=47332, help="port to listen on")
    parser.add_argument("--cert", default="", help="TLS certificate chain (PEM); enables TLS")
    parser.add_argument("--key", default="", help="TLS private key (PEM)")

    caps = parser.add_argument_group("caps")
    caps.add_argument("--max-sessions", type=int, default=DEFAULT_LIMITS.max_sessions)
    caps.add_argument(
        "--max-clients",
        type=int,
        default=DEFAULT_LIMITS.max_clients,
        help="connections per session, the GM's control link included",
    )
    caps.add_argument(
        "--rate",
        type=int,
        default=DEFAULT_LIMITS.rate_bytes // 1024,
        metavar="KB_PER_SEC",
        help="sustained throughput per session",
    )
    caps.add_argument(
        "--burst",
        type=int,
        default=DEFAULT_LIMITS.burst_bytes // 1024,
        metavar="KB",
        help="how far a session may burst above --rate",
    )
    caps.add_argument("--idle-timeout", type=float, default=DEFAULT_LIMITS.idle_timeout)
    caps.add_argument(
        "--session-ttl",
        type=float,
        default=DEFAULT_LIMITS.session_ttl / 3600.0,
        metavar="HOURS",
        help="a session is closed at this age however busy it is",
    )

    parser.add_argument("-q", "--quiet", action="store_true", help="log warnings only")
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    context: ssl.SSLContext | None = None
    if args.cert or args.key:
        if not (args.cert and args.key):
            print("--cert and --key go together", file=sys.stderr)
            return 2
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        try:
            context.load_cert_chain(args.cert, args.key)
        except (OSError, ssl.SSLError) as exc:
            print(f"cannot load the certificate: {exc}", file=sys.stderr)
            return 2

    limits = RelayLimits(
        max_sessions=args.max_sessions,
        max_clients=args.max_clients,
        rate_bytes=args.rate * 1024,
        burst_bytes=args.burst * 1024,
        idle_timeout=args.idle_timeout,
        session_ttl=args.session_ttl * 3600.0,
    )
    try:
        server = RelayServer(args.host, args.port, limits=limits, ssl_context=context)
    except OSError as exc:
        print(f"cannot listen on {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 1

    def _stop(*_signal: object) -> None:
        server.stop()

    for name in ("SIGINT", "SIGTERM"):
        handler = getattr(signal, name, None)
        if handler is not None:
            signal.signal(handler, _stop)

    server.serve_forever()
    return 0
