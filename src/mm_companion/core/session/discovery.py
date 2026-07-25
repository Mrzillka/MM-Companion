"""Getting a session reachable: join codes, UPnP port mapping, and the relay seam.

Pure Python and Qt-free, stdlib only. Three separable jobs live here:

**Join codes.** A session's address plus its host token, packed into one
typo-tolerant string the GM can paste into chat. :func:`encode_join_code` and
:func:`decode_join_code` are inverse; the code carries a version byte and a
checksum, so a mistyped code fails cleanly instead of dialling a wrong address.

**UPnP/IGD.** :func:`discover_igd` finds the router over SSDP,
:func:`add_port_mapping` asks it to forward the session port, and
:func:`external_ip` reads the WAN address to put in the code.
:func:`publish_session` is the one call the UI wants: it does all of that, and
whatever happens it returns a :class:`Reachability` carrying the address to
publish *and* the plain-language ``advice`` to show. That last part is the point
— **UPnP fails often** (disabled on the router, double NAT, and CGNAT, where no
port mapping can ever work), and the fallback has to reach the GM, not just a
docs page. Every failure mode here produces an ``ADVICE_*`` line for the UI.

**The relay seam.** :data:`transports` is the registry a relay transport
registers itself into, and :func:`transport_for` is what turns the host half of a
join code into the :class:`~.net.Transport` to use. A host of ``"1.2.3.4"``
yields plain TCP; a host of ``"mmrelay://relay.example:9000/<session-id>"``
yields whatever is registered for the ``mmrelay`` scheme, so a relay can be added
later without touching :mod:`.server`, :mod:`.client`, or the join-code format.

That transport is :mod:`.relay`, and the relay itself is
:mod:`mm_companion.relay`; both ends of the protocol are documented there. In
outline: the same newline-delimited JSON framing as :mod:`.net`, one small
envelope frame per connection (``relay_host`` / ``relay_join`` / ``relay_accept``
→ ``relay_ok`` or ``relay_error``), and a verbatim byte pipe after that, so the
relay holds no session state and needs no upgrade when
:data:`~.protocol.PROTOCOL_VERSION` changes.
"""

from __future__ import annotations

import binascii
import ipaddress
import re
import socket
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from base64 import b32decode, b32encode
from collections.abc import Callable
from dataclasses import dataclass
from xml.sax.saxutils import escape as xml_escape

from mm_companion.core.registry import Registry

from .net import DEFAULT_PORT, TcpTransport, Transport

# --------------------------------------------------------------------------
# Join codes
# --------------------------------------------------------------------------

#: Bumped only if the packed layout below changes; a code from another version
#: is refused by name rather than misread.
JOIN_CODE_VERSION = 1

#: Characters per dash-separated group in a rendered code. Purely cosmetic —
#: decoding ignores every separator.
CODE_GROUP = 5

#: Hard caps on the two variable-length fields, so a code stays a code.
MAX_HOST_BYTES = 128
MAX_TOKEN_BYTES = 128

_HOST_IPV4 = 0
_HOST_TEXT = 1

#: The URL scheme that marks a join code as pointing at a relay rather than
#: straight at the GM's machine. See the module docstring for the protocol.
RELAY_SCHEME = "mmrelay"

_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://")

# Digits that are not in the base32 alphabet at all, mapped to the letter they
# are almost always a mistyping of. Anything else stays as typed and the
# checksum sorts it out.
_CONFUSABLES = str.maketrans({"0": "O", "1": "I"})


class JoinCodeError(ValueError):
    """A join code was malformed, corrupt, or from an incompatible version."""


class AddressError(ValueError):
    """A manually typed ``host``/``host:port`` could not be read."""


@dataclass(frozen=True)
class JoinCode:
    """What a join code decodes to: where the session is, and the secret to enter.

    ``host`` is an address literal, a hostname, or a relay URL (see
    :attr:`is_relay`); ``token`` is the session's ``host_token``, which the
    server checks in the handshake.
    """

    host: str
    port: int
    token: str

    @property
    def address(self) -> tuple[str, int]:
        return (self.host, self.port)

    @property
    def is_relay(self) -> bool:
        """True when the host half names a transport scheme instead of a machine."""
        return bool(_SCHEME_RE.match(self.host))

    def encode(self) -> str:
        return encode_join_code(self.host, self.port, self.token)

    @classmethod
    def parse(cls, text: str) -> JoinCode:
        return decode_join_code(text)


def encode_join_code(host: str, port: int, token: str) -> str:
    """Pack ``host``/``port``/``token`` into one grouped, uppercase code.

    An IPv4 literal is packed as four bytes; anything else (a hostname, an IPv6
    literal, a relay URL) is carried as UTF-8 text.
    """
    host = (host or "").strip()
    if not host:
        raise JoinCodeError("a join code needs a host")
    if not 1 <= int(port) <= 65535:
        raise JoinCodeError(f"port {port} is out of range")
    token_bytes = (token or "").encode("utf-8")
    if not token_bytes:
        raise JoinCodeError("a join code needs a token")
    if len(token_bytes) > MAX_TOKEN_BYTES:
        raise JoinCodeError(f"token is longer than {MAX_TOKEN_BYTES} bytes")

    payload = bytearray([JOIN_CODE_VERSION])
    try:
        packed_ip = ipaddress.IPv4Address(host).packed
    except ValueError:
        host_bytes = host.encode("utf-8")
        if len(host_bytes) > MAX_HOST_BYTES:
            raise JoinCodeError(f"host is longer than {MAX_HOST_BYTES} bytes") from None
        payload.append(_HOST_TEXT)
        payload.append(len(host_bytes))
        payload.extend(host_bytes)
    else:
        payload.append(_HOST_IPV4)
        payload.extend(packed_ip)

    payload.extend(struct.pack(">H", int(port)))
    payload.append(len(token_bytes))
    payload.extend(token_bytes)
    payload.append(_checksum(payload))

    raw = b32encode(bytes(payload)).decode("ascii").rstrip("=")
    return "-".join(raw[index : index + CODE_GROUP] for index in range(0, len(raw), CODE_GROUP))


def decode_join_code(text: str) -> JoinCode:
    """Read a code back, tolerating case, spaces, dashes, and ``0``/``1`` typos."""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", text or "").upper().translate(_CONFUSABLES)
    if not cleaned:
        raise JoinCodeError("that is not a join code")
    padded = cleaned + "=" * (-len(cleaned) % 8)
    try:
        payload = b32decode(padded.encode("ascii"))
    except (binascii.Error, ValueError) as exc:
        raise JoinCodeError("that join code is not readable") from exc

    if len(payload) < 6:
        raise JoinCodeError("that join code is too short to be complete")
    if payload[0] != JOIN_CODE_VERSION:
        raise JoinCodeError(
            f"that join code is version {payload[0]}, and this app speaks "
            f"version {JOIN_CODE_VERSION}"
        )
    if _checksum(payload[:-1]) != payload[-1]:
        raise JoinCodeError("that join code has a typo in it")

    body = payload[1:-1]
    try:
        host, rest = _unpack_host(body)
        port = struct.unpack(">H", rest[:2])[0]
        token_length = rest[2]
        token_bytes = rest[3 : 3 + token_length]
        if len(token_bytes) != token_length or len(rest) != 3 + token_length:
            raise ValueError("trailing or missing bytes")
        token = token_bytes.decode("utf-8")
    except (IndexError, struct.error, UnicodeDecodeError, ValueError) as exc:
        raise JoinCodeError("that join code is not readable") from exc
    if not 1 <= port <= 65535:
        raise JoinCodeError("that join code carries an impossible port")
    if not token:
        raise JoinCodeError("that join code carries no token")
    return JoinCode(host=host, port=port, token=token)


def _unpack_host(body: bytes) -> tuple[str, bytes]:
    """Split the host off the front of a decoded body; returns ``(host, rest)``."""
    kind = body[0]
    if kind == _HOST_IPV4:
        return str(ipaddress.IPv4Address(bytes(body[1:5]))), body[5:]
    if kind == _HOST_TEXT:
        length = body[1]
        host = body[2 : 2 + length]
        if len(host) != length:
            raise ValueError("host is shorter than its length byte")
        return host.decode("utf-8"), body[2 + length :]
    raise ValueError(f"unknown host kind {kind}")


def _checksum(data: bytes | bytearray) -> int:
    """A one-byte sum over the payload — enough to catch a mistyped character."""
    return sum(data) & 0xFF


def parse_address(text: str, default_port: int = DEFAULT_PORT) -> tuple[str, int]:
    """Read a manually typed ``host``, ``host:port``, or ``[v6]:port``.

    The manual path for the times UPnP cannot help: the GM forwards a port by
    hand (or runs a tunnel) and tells players the address; the join dialog pairs
    this with a separately pasted token.
    """
    raw = (text or "").strip()
    if not raw:
        raise AddressError("enter an address")
    if raw.startswith("["):
        closing = raw.find("]")
        if closing < 0:
            raise AddressError("that address is missing its closing bracket")
        host = raw[1:closing]
        tail = raw[closing + 1 :]
        port_text = tail[1:] if tail.startswith(":") else ""
        if tail and not tail.startswith(":"):
            raise AddressError("that address is not readable")
    elif raw.count(":") == 1:
        host, _, port_text = raw.partition(":")
    elif ":" in raw:
        # A bare IPv6 literal — several colons and no brackets to disambiguate.
        host, port_text = raw, ""
    else:
        host, port_text = raw, ""

    host = host.strip()
    if not host:
        raise AddressError("that address has no host in it")
    if not port_text:
        return (host, int(default_port))
    if not port_text.isdigit():
        raise AddressError(f"{port_text!r} is not a port number")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise AddressError(f"port {port} is out of range")
    return (host, port)


# --------------------------------------------------------------------------
# Local network facts
# --------------------------------------------------------------------------

#: The address used to work out which local interface faces the internet.
#: TEST-NET-3 (RFC 5737) is guaranteed to belong to nobody, and a UDP "connect"
#: sends no packets — it only makes the OS pick a route and bind a source
#: address, which is the one thing we want to know.
_ROUTE_PROBE = ("203.0.113.1", 9)

#: RFC 6598 carrier-grade NAT space. An external address in here means the ISP
#: is NATing the whole neighbourhood: no port mapping can make the GM reachable.
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

#: What counts as "on a local network". Spelled out rather than deferring to
#: :attr:`ipaddress.IPv4Address.is_private`, whose membership genuinely changed
#: across the Python versions this app supports (carrier-grade NAT space left it
#: in 3.12). This classification decides both what address a session publishes
#: and which URLs UPnP will fetch, so it must not depend on the interpreter.
_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)


def local_ip() -> str:
    """This machine's address on whichever interface holds the default route."""
    return local_ip_for(_ROUTE_PROBE[0])


def local_ip_for(host: str) -> str:
    """The local address that faces ``host`` — the mapping target to hand a router.

    Not the same thing as :func:`local_ip` on a machine with more than one
    interface, and the difference is what makes a port mapping work: a VPN or a
    virtual adapter can hold the default route while the router lives on a
    directly-connected LAN, and a router told to forward a port to an address
    outside its own subnet either refuses or quietly does nothing. So the address
    handed to ``AddPortMapping`` is always the one the OS would use to reach
    *that router*.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((host, _ROUTE_PROBE[1]))
        return str(sock.getsockname()[0])
    except OSError:
        pass
    finally:
        sock.close()
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def is_private_address(host: str) -> bool:
    """True for a LAN / loopback / link-local literal (a hostname is never private)."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(
        address in network for network in _PRIVATE_NETWORKS if network.version == address.version
    )


def is_cgnat_address(host: str) -> bool:
    """True for an address inside RFC 6598 carrier-grade NAT space."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.version == 4 and address in _CGNAT_NETWORK


# --------------------------------------------------------------------------
# UPnP / IGD
# --------------------------------------------------------------------------

SSDP_ADDRESS = ("239.255.255.250", 1900)
SSDP_TIMEOUT = 3.0
SSDP_MX = 2

#: Search targets, most specific first. Routers answer some and ignore others,
#: so all four go out and the replies are pooled.
SSDP_TARGETS = (
    "urn:schemas-upnp-org:service:WANIPConnection:1",
    "urn:schemas-upnp-org:service:WANPPPConnection:1",
    "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
    "upnp:rootdevice",
)

#: The two services that can actually forward a port; either will do.
WAN_SERVICE_TYPES = (
    "urn:schemas-upnp-org:service:WANIPConnection:1",
    "urn:schemas-upnp-org:service:WANPPPConnection:1",
)

HTTP_TIMEOUT = 5.0

#: A device description is a small XML file. Anything larger is not one, and
#: refusing early is what keeps a hostile answer from being worth parsing.
MAX_DESCRIPTION_BYTES = 256 * 1024

#: What the mapping is labelled with in the router's admin page.
PORT_MAPPING_DESCRIPTION = "MM-Companion session"

#: Requested lease. ``0`` means "until deleted", which is what most consumer
#: routers implement; a router that insists on a finite lease answers 725 and
#: :func:`add_port_mapping` retries.
DEFAULT_LEASE_SECONDS = 0

#: The two IGD faults worth reacting to rather than merely reporting.
UPNP_ONLY_PERMANENT_LEASES = 725
UPNP_CONFLICT = 718


class UpnpError(Exception):
    """The router answered a SOAP action with a fault.

    ``code`` is the IGD ``errorCode`` (0 when the answer was unreadable), which
    is how callers tell "this port is already mapped" (718) from "leases must be
    permanent" (725) without matching on prose.
    """

    def __init__(self, message: str, code: int = 0, description: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.description = description


@dataclass(frozen=True)
class IgdDevice:
    """One router's WAN connection service, ready to be asked for a mapping."""

    location: str
    service_type: str
    control_url: str
    name: str = ""


def discover_igd(*, timeout: float = SSDP_TIMEOUT) -> IgdDevice | None:
    """Find an internet gateway over SSDP, or ``None`` if none answers.

    ``None`` is the ordinary outcome on a network with UPnP switched off; it is
    not an error, it just means the GM has to forward the port by hand.
    """
    for location in _ssdp_search(timeout):
        device = _describe_device(location)
        if device is not None:
            return device
    return None


def external_ip(device: IgdDevice, *, timeout: float = HTTP_TIMEOUT) -> str | None:
    """The router's WAN address, or ``None`` when it will not say."""
    try:
        answer = _soap_post(device, "GetExternalIPAddress", (), timeout=timeout)
    except (UpnpError, OSError):
        return None
    value = answer.get("NewExternalIPAddress", "").strip()
    # A router whose WAN side has not come up answers with the unspecified
    # address; that is "I do not know yet", not an address to publish.
    if not value or value == "0.0.0.0":
        return None
    return value


def add_port_mapping(
    device: IgdDevice,
    *,
    external_port: int,
    internal_port: int,
    internal_ip: str,
    protocol: str = "TCP",
    description: str = PORT_MAPPING_DESCRIPTION,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    timeout: float = HTTP_TIMEOUT,
) -> PortMapping:
    """Ask the router to forward ``external_port`` to us. Raises :class:`UpnpError`."""
    arguments = (
        ("NewRemoteHost", ""),
        ("NewExternalPort", str(int(external_port))),
        ("NewProtocol", protocol),
        ("NewInternalPort", str(int(internal_port))),
        ("NewInternalClient", internal_ip),
        ("NewEnabled", "1"),
        ("NewPortMappingDescription", description),
        ("NewLeaseDuration", str(int(lease_seconds))),
    )
    try:
        _soap_post(device, "AddPortMapping", arguments, timeout=timeout)
    except UpnpError as exc:
        if exc.code != UPNP_ONLY_PERMANENT_LEASES or lease_seconds == 0:
            raise
        # The router refuses timed leases; the same request with a permanent one
        # is what it wanted all along.
        lease_seconds = 0
        _soap_post(
            device,
            "AddPortMapping",
            arguments[:-1] + (("NewLeaseDuration", "0"),),
            timeout=timeout,
        )
    return PortMapping(
        device=device,
        external_port=int(external_port),
        internal_port=int(internal_port),
        internal_ip=internal_ip,
        protocol=protocol,
        lease_seconds=int(lease_seconds),
    )


def delete_port_mapping(
    device: IgdDevice,
    external_port: int,
    *,
    protocol: str = "TCP",
    timeout: float = HTTP_TIMEOUT,
) -> None:
    """Remove a mapping. Raises :class:`UpnpError` if the router refuses."""
    _soap_post(
        device,
        "DeletePortMapping",
        (
            ("NewRemoteHost", ""),
            ("NewExternalPort", str(int(external_port))),
            ("NewProtocol", protocol),
        ),
        timeout=timeout,
    )


@dataclass(frozen=True)
class PortMapping:
    """A forwarding rule this app asked for, and can take back."""

    device: IgdDevice
    external_port: int
    internal_port: int
    internal_ip: str
    protocol: str = "TCP"
    lease_seconds: int = DEFAULT_LEASE_SECONDS

    def release(self) -> bool:
        """Delete the mapping, reporting success. Never raises — this runs at shutdown."""
        try:
            delete_port_mapping(self.device, self.external_port, protocol=self.protocol)
        except (UpnpError, OSError):
            return False
        return True


# --------------------------------------------------------------------------
# Publishing a session, and what to tell the GM when it does not work
# --------------------------------------------------------------------------

METHOD_UPNP = "upnp"
METHOD_LAN = "lan"
METHOD_MANUAL = "manual"
METHOD_RELAY = "relay"

ADVICE_FIREWALL = (
    "The first time you host, Windows asks whether to let MM-Companion accept "
    "connections. Allow it on both private and public networks, or nobody can join."
)
ADVICE_NO_IGD = (
    "No router answered the automatic port-forward request (UPnP is often off by "
    "default). Players on your own network can join with the address below; for "
    "players over the internet, forward the port on your router by hand or use a "
    "tunnel such as Tailscale or playit.gg."
)
ADVICE_UPNP_REFUSED = (
    "Your router was found but refused to forward the port. Forward it by hand in "
    "the router's admin page, or use a tunnel such as Tailscale or playit.gg."
)
ADVICE_PORT_TAKEN = (
    "That external port is already forwarded to something else. Host on a "
    "different port, or remove the old rule in your router's admin page."
)
ADVICE_CGNAT = (
    "Your internet provider puts you behind carrier-grade NAT, so no port forward "
    "— automatic or manual — can make this machine reachable from the internet. "
    "Use a tunnel such as Tailscale or playit.gg, ask your provider for a public "
    "IP address, or host the session on a machine that has one."
)
ADVICE_DOUBLE_NAT = (
    "Your router reports a private address on its internet side, so there is a "
    "second router in front of it. If that box is yours, repeat the port forward "
    "on it too. If it belongs to your internet provider — which is common — you "
    "cannot forward anything through it: use a tunnel such as Tailscale or "
    "playit.gg, or ask the provider for a public IP address."
)
ADVICE_NO_EXTERNAL_IP = (
    "The port was forwarded, but the router would not say what your public "
    "address is. Look it up (search the web for 'what is my IP') and share that "
    "address with your players."
)
ADVICE_MANUAL_ADDRESS = (
    "This code points at the address you entered; make sure that address really "
    "reaches this machine before sharing it."
)
ADVICE_LAN_ONLY = "Anyone on this network can join with the address below."
ADVICE_RELAY = (
    "This session is going through a relay, so players anywhere can join and "
    "nobody has to forward a port. The relay only passes the table's traffic "
    "along; it is slower than a direct connection and its operator could in "
    "principle see what goes through it."
)
ADVICE_RELAY_UNREACHABLE = (
    "The relay could not be reached, so the session is back on a direct "
    "connection. Check the relay address, or forward the port on your router by "
    "hand and use a tunnel such as Tailscale or playit.gg instead."
)


@dataclass(frozen=True)
class Reachability:
    """The outcome of trying to make a session reachable — and what to say about it.

    ``host``/``port`` are what belongs in the join code. ``advice`` is finished
    prose for the UI: the plan's one hard requirement for this layer is that a
    UPnP failure, and CGNAT above all, reaches the GM on screen rather than
    living only in the docs.
    """

    host: str
    port: int
    method: str = METHOD_LAN
    lan_ip: str = ""
    external_ip: str = ""
    mapping: PortMapping | None = None
    advice: tuple[str, ...] = ()
    error: str = ""

    @property
    def internet_reachable(self) -> bool:
        """A best-effort yes: a mapping exists and the WAN address is a public one."""
        if self.method == METHOD_RELAY:
            # Both ends dial out to the relay, so there is nothing left for a NAT
            # to refuse: if the relay answered at all, players can reach us.
            return True
        if self.mapping is None:
            return False
        return bool(self.external_ip) and not (
            is_private_address(self.external_ip) or is_cgnat_address(self.external_ip)
        )

    def join_code(self, token: str) -> str:
        """The code to share for this address."""
        return encode_join_code(self.host, self.port, token)

    def release(self) -> bool:
        """Undo any port mapping this took out."""
        return self.mapping.release() if self.mapping is not None else True


def publish_session(
    port: int,
    *,
    internal_ip: str = "",
    external_port: int = 0,
    use_upnp: bool = True,
    manual_host: str = "",
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    timeout: float = SSDP_TIMEOUT,
) -> Reachability:
    """Make a listening session as reachable as this network allows.

    Never raises: every failure becomes a :class:`Reachability` with the LAN
    address (still useful — most tables are one household or a VPN) and the
    ``advice`` explaining what to do instead. ``manual_host`` skips discovery
    entirely and takes the GM at their word, which is the tunnel / hand-forwarded
    path.
    """
    lan = internal_ip or local_ip()
    if manual_host:
        # A tunnel hands back an address *and* a port of its own, which is rarely
        # the port being listened on locally — so the code carries
        # ``external_port`` when one was given, and the local port otherwise (a
        # hand-forwarded router rule, where the two usually match).
        return Reachability(
            host=manual_host,
            port=int(external_port or port),
            method=METHOD_MANUAL,
            lan_ip=lan,
            advice=(ADVICE_MANUAL_ADDRESS, ADVICE_FIREWALL),
        )
    if not use_upnp:
        return Reachability(
            host=lan,
            port=int(port),
            method=METHOD_LAN,
            lan_ip=lan,
            advice=(ADVICE_LAN_ONLY, ADVICE_FIREWALL),
        )

    device = discover_igd(timeout=timeout)
    if device is None:
        return Reachability(
            host=lan,
            port=int(port),
            method=METHOD_LAN,
            lan_ip=lan,
            advice=(ADVICE_NO_IGD, ADVICE_FIREWALL),
        )

    # Re-aim at the interface that faces this router. The default route can
    # belong to something else entirely (a VPN, a virtual adapter), and a
    # mapping pointed off the router's own subnet does not work.
    if not internal_ip:
        gateway = urllib.parse.urlsplit(device.control_url).hostname or ""
        lan = local_ip_for(gateway) if gateway else lan

    wan = external_ip(device) or ""
    wanted_external = int(external_port or port)
    try:
        mapping = add_port_mapping(
            device,
            external_port=wanted_external,
            internal_port=int(port),
            internal_ip=lan,
            lease_seconds=lease_seconds,
        )
    except (UpnpError, OSError) as exc:
        advice = [ADVICE_UPNP_REFUSED]
        if isinstance(exc, UpnpError) and exc.code == UPNP_CONFLICT:
            advice.insert(0, ADVICE_PORT_TAKEN)
        advice.extend(_wan_advice(wan))
        advice.append(ADVICE_FIREWALL)
        return Reachability(
            host=lan,
            port=int(port),
            method=METHOD_LAN,
            lan_ip=lan,
            external_ip=wan,
            advice=tuple(advice),
            error=str(exc),
        )

    advice = list(_wan_advice(wan))
    advice.append(ADVICE_FIREWALL)
    return Reachability(
        host=wan or lan,
        port=wanted_external if wan else int(port),
        method=METHOD_UPNP if wan else METHOD_LAN,
        lan_ip=lan,
        external_ip=wan,
        mapping=mapping,
        advice=tuple(advice),
    )


def relay_reachability(url: str, port: int) -> Reachability:
    """The published address of a session already hosted through a relay.

    There is nothing to probe — the relay accepted the registration or hosting
    would have failed — so this only dresses the relay URL up as the
    :class:`Reachability` the UI renders, with the join code pointing at the
    relay. ``url`` is the full ``mmrelay://…/<session-id>`` address and ``port``
    the relay's own port, kept in the code for readability; the transport reads
    both from the URL.
    """
    return Reachability(
        host=url,
        port=int(port),
        method=METHOD_RELAY,
        advice=(ADVICE_RELAY,),
    )


def _wan_advice(wan: str) -> tuple[str, ...]:
    """What the router's WAN address implies for a GM who wants outside players."""
    if not wan:
        return (ADVICE_NO_EXTERNAL_IP,)
    if is_cgnat_address(wan):
        return (ADVICE_CGNAT,)
    if is_private_address(wan):
        return (ADVICE_DOUBLE_NAT,)
    return ()


# --------------------------------------------------------------------------
# The transport seam
# --------------------------------------------------------------------------

#: Builds a :class:`~.net.Transport` from the host half of a join code (the whole
#: URL, scheme included).
TransportFactory = Callable[[str], Transport]

#: ``scheme -> factory``. Empty by default: nothing but direct TCP ships yet. A
#: relay registers itself here (from a mod's Python module, or from a future
#: ``core.session.relay``) and every join code carrying that scheme starts
#: working, with no change to the server, the client, or the code format.
transports: Registry[TransportFactory] = Registry("session transports")


class UnknownTransportError(LookupError):
    """A join code names a transport scheme nothing has registered."""


def transport_scheme(host: str) -> str:
    """The scheme in a relay-style host, or ``""`` for a plain address."""
    match = _SCHEME_RE.match(host or "")
    return match.group(1).lower() if match else ""


def transport_for(host: str) -> Transport:
    """The transport to reach ``host`` — plain TCP, or a registered relay."""
    scheme = transport_scheme(host)
    if not scheme:
        return TcpTransport()
    factory = transports.get(scheme)
    if factory is None:
        raise UnknownTransportError(
            f"this app has no {scheme!r} transport, so it cannot use that join code"
        )
    return factory(host)


# --------------------------------------------------------------------------
# SSDP / SOAP plumbing — the seams the tests replace
# --------------------------------------------------------------------------


def _ssdp_search(timeout: float) -> list[str]:
    """M-SEARCH the local segment and return the ``LOCATION`` URLs that answered."""
    locations: list[str] = []
    seen: set[str] = set()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.5)
        sock.bind(("", 0))
        for target in SSDP_TARGETS:
            request = (
                "M-SEARCH * HTTP/1.1\r\n"
                f"HOST: {SSDP_ADDRESS[0]}:{SSDP_ADDRESS[1]}\r\n"
                'MAN: "ssdp:discover"\r\n'
                f"MX: {SSDP_MX}\r\n"
                f"ST: {target}\r\n"
                "\r\n"
            ).encode("ascii")
            try:
                sock.sendto(request, SSDP_ADDRESS)
            except OSError:
                continue
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, _sender = sock.recvfrom(8192)
            except TimeoutError:
                continue
            except OSError:
                break
            location = _http_header(data, "location")
            if location and location not in seen:
                seen.add(location)
                locations.append(location)
    except OSError:
        pass
    finally:
        sock.close()
    return locations


def _http_header(data: bytes, name: str) -> str:
    """Read one header out of a raw SSDP response."""
    wanted = name.lower() + ":"
    for line in data.decode("utf-8", "replace").splitlines():
        if line.lower().startswith(wanted):
            return line[len(wanted) :].strip()
    return ""


def _http_get(url: str, timeout: float = HTTP_TIMEOUT) -> bytes:
    """Fetch a device description from a *local* URL, size-capped.

    The URL comes from an unauthenticated multicast reply, so it is checked
    against the private address ranges before and after the request: a device
    that claims to live on the internet, or redirects there, is not a router of
    ours and is not worth fetching.
    """
    _require_local_url(url)
    request = urllib.request.Request(url, headers={"Accept": "text/xml"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        _require_local_url(response.geturl())
        body = response.read(MAX_DESCRIPTION_BYTES + 1)
    if len(body) > MAX_DESCRIPTION_BYTES:
        raise UpnpError(f"device description is larger than {MAX_DESCRIPTION_BYTES} bytes")
    return body


def _require_local_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UpnpError(f"{parts.scheme or 'that'} is not a device description URL")
    host = parts.hostname or ""
    if not is_private_address(host):
        raise UpnpError(f"{host or 'that device'} is not on this network")


def _soap_post(
    device: IgdDevice,
    action: str,
    arguments: tuple[tuple[str, str], ...],
    *,
    timeout: float = HTTP_TIMEOUT,
) -> dict[str, str]:
    """Invoke one SOAP action and return the response's parameters as a dict."""
    _require_local_url(device.control_url)
    body = "".join(f"<{name}>{xml_escape(value)}</{name}>" for name, value in arguments)
    envelope = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><u:{action} xmlns:u="{device.service_type}">{body}</u:{action}></s:Body>'
        "</s:Envelope>"
    ).encode()
    request = urllib.request.Request(
        device.control_url,
        data=envelope,
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"{device.service_type}#{action}"',
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(MAX_DESCRIPTION_BYTES)
    except urllib.error.HTTPError as exc:
        raise _fault(exc, action) from exc
    return _response_arguments(payload, action)


def _fault(exc: urllib.error.HTTPError, action: str) -> UpnpError:
    """Turn a SOAP fault body into a :class:`UpnpError` carrying the IGD error code."""
    try:
        body = exc.read(MAX_DESCRIPTION_BYTES)
    except OSError:
        body = b""
    code = 0
    description = ""
    for element in _walk(body):
        local = _local_name(element.tag)
        if local == "errorCode" and (element.text or "").strip().isdigit():
            code = int((element.text or "").strip())
        elif local == "errorDescription":
            description = (element.text or "").strip()
    message = description or f"{action} failed with HTTP {exc.code}"
    return UpnpError(message, code=code, description=description)


def _response_arguments(payload: bytes, action: str) -> dict[str, str]:
    """The ``<Name>value</Name>`` children of an action's response element."""
    answer: dict[str, str] = {}
    wanted = f"{action}Response"
    for element in _walk(payload):
        if _local_name(element.tag) != wanted:
            continue
        for child in element:
            answer[_local_name(child.tag)] = (child.text or "").strip()
    return answer


def _describe_device(location: str) -> IgdDevice | None:
    """Fetch a description and pick out a WAN connection service, if it has one."""
    try:
        payload = _http_get(location)
    except (UpnpError, OSError, ValueError):
        return None
    name = ""
    services: list[tuple[str, str]] = []
    for element in _walk(payload):
        local = _local_name(element.tag)
        if local == "friendlyName" and not name:
            name = (element.text or "").strip()
        elif local == "service":
            service_type = ""
            control_url = ""
            for child in element:
                child_name = _local_name(child.tag)
                if child_name == "serviceType":
                    service_type = (child.text or "").strip()
                elif child_name == "controlURL":
                    control_url = (child.text or "").strip()
            if service_type and control_url:
                services.append((service_type, control_url))

    for wanted in WAN_SERVICE_TYPES:
        for service_type, control_url in services:
            if service_type == wanted:
                return IgdDevice(
                    location=location,
                    service_type=service_type,
                    control_url=urllib.parse.urljoin(location, control_url),
                    name=name,
                )
    return None


def _walk(payload: bytes) -> list[ElementTree.Element]:
    """Every element in an XML document, root included; ``[]`` if it will not parse."""
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return []
    return list(root.iter())


def _local_name(tag: object) -> str:
    """An element's tag without its ``{namespace}`` prefix."""
    text = str(tag)
    return text.rsplit("}", 1)[-1]


#: Re-exported so callers do not have to reach into :mod:`.net` for the port a
#: session listens on by default.
__all__ = [
    "ADVICE_CGNAT",
    "ADVICE_DOUBLE_NAT",
    "ADVICE_FIREWALL",
    "ADVICE_LAN_ONLY",
    "ADVICE_MANUAL_ADDRESS",
    "ADVICE_NO_EXTERNAL_IP",
    "ADVICE_NO_IGD",
    "ADVICE_PORT_TAKEN",
    "ADVICE_RELAY",
    "ADVICE_RELAY_UNREACHABLE",
    "ADVICE_UPNP_REFUSED",
    "DEFAULT_PORT",
    "METHOD_LAN",
    "METHOD_MANUAL",
    "METHOD_RELAY",
    "METHOD_UPNP",
    "AddressError",
    "IgdDevice",
    "JoinCode",
    "JoinCodeError",
    "PortMapping",
    "Reachability",
    "TransportFactory",
    "UnknownTransportError",
    "UpnpError",
    "add_port_mapping",
    "decode_join_code",
    "delete_port_mapping",
    "discover_igd",
    "encode_join_code",
    "external_ip",
    "is_cgnat_address",
    "is_private_address",
    "local_ip",
    "parse_address",
    "publish_session",
    "relay_reachability",
    "transport_for",
    "transport_scheme",
    "transports",
]
