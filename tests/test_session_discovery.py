"""Join codes, UPnP port mapping, and the relay transport seam.

Headless and offline: no packet leaves the machine. The SSDP search and the two
HTTP seams (:func:`~mm_companion.core.session.discovery._ssdp_search`,
``_http_get``, ``_soap_post``) are monkeypatched, so a router is a dict of canned
XML rather than hardware.
"""

from __future__ import annotations

import urllib.error

import pytest

from mm_companion.core.session import discovery
from mm_companion.core.session.discovery import (
    ADVICE_CGNAT,
    ADVICE_DOUBLE_NAT,
    ADVICE_NO_EXTERNAL_IP,
    ADVICE_NO_IGD,
    ADVICE_PORT_TAKEN,
    ADVICE_UPNP_REFUSED,
    METHOD_LAN,
    METHOD_MANUAL,
    METHOD_UPNP,
    RELAY_SCHEME,
    UPNP_CONFLICT,
    UPNP_ONLY_PERMANENT_LEASES,
    AddressError,
    IgdDevice,
    JoinCode,
    JoinCodeError,
    UnknownTransportError,
    UpnpError,
    add_port_mapping,
    decode_join_code,
    delete_port_mapping,
    discover_igd,
    encode_join_code,
    external_ip,
    is_cgnat_address,
    is_private_address,
    local_ip,
    parse_address,
    publish_session,
    transport_for,
    transport_scheme,
)
from mm_companion.core.session.model import new_token
from mm_companion.core.session.net import DEFAULT_PORT, TcpTransport, Transport

# --------------------------------------------------------------------------
# Join codes
# --------------------------------------------------------------------------


def test_a_join_code_round_trips_an_ipv4_address():
    token = new_token()
    code = encode_join_code("203.0.113.7", 47331, token)
    decoded = decode_join_code(code)
    assert decoded == JoinCode(host="203.0.113.7", port=47331, token=token)
    assert decoded.address == ("203.0.113.7", 47331)


def test_a_join_code_round_trips_a_hostname():
    code = encode_join_code("session.example.org", 1234, "secret")
    assert decode_join_code(code).host == "session.example.org"


def test_a_join_code_round_trips_an_ipv6_literal():
    code = encode_join_code("2001:db8::1", DEFAULT_PORT, "secret")
    decoded = decode_join_code(code)
    assert decoded.host == "2001:db8::1"
    assert decoded.port == DEFAULT_PORT


def test_a_join_code_is_grouped_uppercase_base32():
    code = encode_join_code("10.0.0.5", 47331, "secret")
    assert code == code.upper()
    groups = code.split("-")
    assert all(len(group) <= discovery.CODE_GROUP for group in groups)
    assert set(code.replace("-", "")) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")


def test_decoding_ignores_case_spaces_and_missing_dashes():
    token = new_token()
    code = encode_join_code("192.168.1.20", 47331, token)
    mangled = code.lower().replace("-", " ")
    assert decode_join_code(mangled) == decode_join_code(code)
    assert decode_join_code(code.replace("-", "")) == decode_join_code(code)


def test_decoding_forgives_the_digits_base32_does_not_have():
    code = encode_join_code("172.16.4.4", 47331, "secret")
    typed = code.replace("O", "0").replace("I", "1")
    assert decode_join_code(typed).host == "172.16.4.4"


def test_a_mistyped_character_is_caught_by_the_checksum():
    code = encode_join_code("203.0.113.7", 47331, new_token())
    body = code.replace("-", "")
    swapped = "Q" if body[3] != "Q" else "R"
    broken = body[:3] + swapped + body[4:]
    with pytest.raises(JoinCodeError):
        decode_join_code(broken)


def test_garbage_and_empties_are_refused():
    for text in ("", "   ", "!!!", "hello world"):
        with pytest.raises(JoinCodeError):
            decode_join_code(text)


def test_a_truncated_code_is_refused():
    code = encode_join_code("203.0.113.7", 47331, new_token()).replace("-", "")
    with pytest.raises(JoinCodeError):
        decode_join_code(code[:12])


def test_a_code_from_another_version_names_the_version():
    from base64 import b32encode

    payload = bytearray([discovery.JOIN_CODE_VERSION + 1, 0, 10, 0, 0, 1, 0, 80, 1, 65])
    payload.append(sum(payload) & 0xFF)
    text = b32encode(bytes(payload)).decode("ascii").rstrip("=")
    with pytest.raises(JoinCodeError, match="version"):
        decode_join_code(text)


def test_encoding_refuses_a_missing_host_token_or_impossible_port():
    with pytest.raises(JoinCodeError):
        encode_join_code("", 47331, "secret")
    with pytest.raises(JoinCodeError):
        encode_join_code("10.0.0.1", 47331, "")
    with pytest.raises(JoinCodeError):
        encode_join_code("10.0.0.1", 0, "secret")
    with pytest.raises(JoinCodeError):
        encode_join_code("10.0.0.1", 70000, "secret")


def test_encoding_refuses_oversized_fields():
    with pytest.raises(JoinCodeError):
        encode_join_code("h" * (discovery.MAX_HOST_BYTES + 1), 47331, "secret")
    with pytest.raises(JoinCodeError):
        encode_join_code("10.0.0.1", 47331, "t" * (discovery.MAX_TOKEN_BYTES + 1))


def test_the_join_code_dataclass_wraps_the_functions():
    original = JoinCode(host="198.51.100.9", port=47331, token=new_token())
    assert JoinCode.parse(original.encode()) == original
    assert not original.is_relay


def test_a_relay_url_survives_the_code_and_is_recognised():
    url = f"{RELAY_SCHEME}://relay.example:9000/abcdef123456"
    decoded = decode_join_code(encode_join_code(url, 9000, "secret"))
    assert decoded.host == url
    assert decoded.is_relay


# --------------------------------------------------------------------------
# Manual addresses
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("203.0.113.7", ("203.0.113.7", DEFAULT_PORT)),
        ("203.0.113.7:1234", ("203.0.113.7", 1234)),
        ("  session.example.org:9  ", ("session.example.org", 9)),
        ("[2001:db8::1]:1234", ("2001:db8::1", 1234)),
        ("[2001:db8::1]", ("2001:db8::1", DEFAULT_PORT)),
        ("2001:db8::1", ("2001:db8::1", DEFAULT_PORT)),
    ],
)
def test_manual_addresses_are_read_in_every_form(text, expected):
    assert parse_address(text) == expected


def test_a_manual_address_can_take_another_default_port():
    assert parse_address("10.0.0.1", default_port=5000) == ("10.0.0.1", 5000)


@pytest.mark.parametrize("text", ["", "   ", ":47331", "host:port", "host:0", "host:99999", "[::1"])
def test_an_unreadable_manual_address_is_refused(text):
    with pytest.raises(AddressError):
        parse_address(text)


# --------------------------------------------------------------------------
# Local network facts
# --------------------------------------------------------------------------


def test_local_ip_is_an_ipv4_address():
    import ipaddress

    assert ipaddress.IPv4Address(local_ip())


def test_local_ip_for_a_host_asks_the_route_that_faces_it(monkeypatch):
    asked: list[tuple[str, int]] = []

    class _Socket:
        def connect(self, address):
            asked.append(address)

        def getsockname(self):
            return ("192.168.0.50", 0)

        def close(self):
            pass

    monkeypatch.setattr(discovery.socket, "socket", lambda *_a, **_k: _Socket())
    assert discovery.local_ip_for("192.168.0.1") == "192.168.0.50"
    assert asked == [("192.168.0.1", discovery._ROUTE_PROBE[1])]


def test_local_ip_falls_back_when_there_is_no_route(monkeypatch):
    class _Broken:
        def connect(self, _address):
            raise OSError("no route")

        def getsockname(self):  # pragma: no cover - never reached
            raise AssertionError

        def close(self):
            pass

    monkeypatch.setattr(discovery.socket, "socket", lambda *_a, **_k: _Broken())
    monkeypatch.setattr(discovery.socket, "gethostname", lambda: "box")
    monkeypatch.setattr(discovery.socket, "gethostbyname", lambda _name: "192.168.7.7")
    assert local_ip() == "192.168.7.7"


@pytest.mark.parametrize(
    ("host", "private", "cgnat"),
    [
        ("192.168.1.10", True, False),
        ("10.1.2.3", True, False),
        ("172.16.0.1", True, False),
        ("127.0.0.1", True, False),
        ("169.254.4.4", True, False),
        ("fe80::1", True, False),
        ("::1", True, False),
        ("100.64.0.1", False, True),
        ("100.127.255.254", False, True),
        ("100.128.0.1", False, False),
        ("203.0.113.7", False, False),
        ("2001:db8::1", False, False),
        ("router.local", False, False),
        ("", False, False),
    ],
)
def test_addresses_are_classified(host, private, cgnat):
    assert is_private_address(host) is private
    assert is_cgnat_address(host) is cgnat


# --------------------------------------------------------------------------
# UPnP — a router made of canned XML
# --------------------------------------------------------------------------

DESCRIPTION_XML = b"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <device>
    <friendlyName>Test Router</friendlyName>
    <serviceList>
      <service>
        <serviceType>urn:schemas-upnp-org:service:Layer3Forwarding:1</serviceType>
        <controlURL>/ctl/L3F</controlURL>
      </service>
    </serviceList>
    <deviceList><device><deviceList><device>
      <serviceList>
        <service>
          <serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType>
          <controlURL>/ctl/IPConn</controlURL>
        </service>
      </serviceList>
    </device></deviceList></device></deviceList>
  </device>
</root>
"""

NO_WAN_XML = b"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <device><friendlyName>A Printer</friendlyName><serviceList>
    <service>
      <serviceType>urn:schemas-upnp-org:service:Printing:1</serviceType>
      <controlURL>/ctl/print</controlURL>
    </service>
  </serviceList></device>
</root>
"""

LOCATION = "http://192.168.1.1:5000/rootDesc.xml"

DEVICE = IgdDevice(
    location=LOCATION,
    service_type="urn:schemas-upnp-org:service:WANIPConnection:1",
    control_url="http://192.168.1.1:5000/ctl/IPConn",
    name="Test Router",
)


@pytest.fixture
def router(monkeypatch):
    """A discoverable router whose SOAP calls are recorded, not sent."""

    calls: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    answers: dict[str, object] = {"GetExternalIPAddress": {"NewExternalIPAddress": "203.0.113.7"}}

    monkeypatch.setattr(discovery, "_ssdp_search", lambda _timeout: [LOCATION])
    monkeypatch.setattr(discovery, "_http_get", lambda url, timeout=0.0: DESCRIPTION_XML)

    def fake_post(device, action, arguments, *, timeout=0.0):
        calls.append((action, tuple(arguments)))
        answer = answers.get(action, {})
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            return answer(tuple(arguments))
        return dict(answer)

    monkeypatch.setattr(discovery, "_soap_post", fake_post)
    monkeypatch.setattr(discovery, "local_ip", lambda: "192.168.1.50")
    monkeypatch.setattr(discovery, "local_ip_for", lambda _host: "192.168.1.50")

    class Router:
        def __init__(self):
            self.calls = calls
            self.answers = answers

        def action(self, name):
            return [args for called, args in self.calls if called == name]

    return Router()


def test_discovery_finds_the_wan_service_and_absolutises_the_control_url(router):
    device = discover_igd(timeout=0.01)
    assert device is not None
    assert device.service_type == "urn:schemas-upnp-org:service:WANIPConnection:1"
    assert device.control_url == "http://192.168.1.1:5000/ctl/IPConn"
    assert device.name == "Test Router"


def test_discovery_returns_none_when_nothing_answers(monkeypatch):
    monkeypatch.setattr(discovery, "_ssdp_search", lambda _timeout: [])
    assert discover_igd(timeout=0.01) is None


def test_discovery_skips_a_device_with_no_wan_service(monkeypatch):
    monkeypatch.setattr(discovery, "_ssdp_search", lambda _timeout: [LOCATION])
    monkeypatch.setattr(discovery, "_http_get", lambda url, timeout=0.0: NO_WAN_XML)
    assert discover_igd(timeout=0.01) is None


def test_discovery_survives_a_device_that_cannot_be_fetched(monkeypatch):
    def boom(url, timeout=0.0):
        raise OSError("connection refused")

    monkeypatch.setattr(discovery, "_ssdp_search", lambda _timeout: [LOCATION])
    monkeypatch.setattr(discovery, "_http_get", boom)
    assert discover_igd(timeout=0.01) is None


def test_a_description_url_off_this_network_is_refused():
    with pytest.raises(UpnpError, match="not on this network"):
        discovery._require_local_url("http://203.0.113.7/rootDesc.xml")
    with pytest.raises(UpnpError):
        discovery._require_local_url("file:///etc/passwd")
    discovery._require_local_url("http://192.168.1.1:5000/rootDesc.xml")


def test_the_external_ip_comes_from_the_router(router):
    assert external_ip(DEVICE) == "203.0.113.7"


def test_an_unconnected_wan_reports_no_external_ip(router):
    router.answers["GetExternalIPAddress"] = {"NewExternalIPAddress": "0.0.0.0"}
    assert external_ip(DEVICE) is None
    router.answers["GetExternalIPAddress"] = UpnpError("nope", code=501)
    assert external_ip(DEVICE) is None


def test_adding_a_mapping_sends_the_expected_arguments(router):
    mapping = add_port_mapping(
        DEVICE,
        external_port=47331,
        internal_port=47331,
        internal_ip="192.168.1.50",
        lease_seconds=0,
    )
    (arguments,) = router.action("AddPortMapping")
    sent = dict(arguments)
    assert sent["NewExternalPort"] == "47331"
    assert sent["NewInternalPort"] == "47331"
    assert sent["NewInternalClient"] == "192.168.1.50"
    assert sent["NewProtocol"] == "TCP"
    assert sent["NewEnabled"] == "1"
    assert sent["NewLeaseDuration"] == "0"
    assert mapping.external_port == 47331
    assert mapping.internal_ip == "192.168.1.50"


def test_a_router_that_only_takes_permanent_leases_is_retried(router):
    attempts: list[str] = []

    def answer(arguments):
        sent = dict(arguments)
        attempts.append(sent["NewLeaseDuration"])
        if sent["NewLeaseDuration"] != "0":
            raise UpnpError("OnlyPermanentLeasesSupported", code=UPNP_ONLY_PERMANENT_LEASES)
        return {}

    router.answers["AddPortMapping"] = answer
    mapping = add_port_mapping(
        DEVICE,
        external_port=47331,
        internal_port=47331,
        internal_ip="192.168.1.50",
        lease_seconds=3600,
    )
    assert attempts == ["3600", "0"]
    assert mapping.lease_seconds == 0


def test_another_fault_is_not_retried(router):
    router.answers["AddPortMapping"] = UpnpError("ConflictInMappingEntry", code=UPNP_CONFLICT)
    with pytest.raises(UpnpError) as caught:
        add_port_mapping(
            DEVICE, external_port=47331, internal_port=47331, internal_ip="192.168.1.50"
        )
    assert caught.value.code == UPNP_CONFLICT
    assert len(router.action("AddPortMapping")) == 1


def test_deleting_a_mapping_names_the_port(router):
    delete_port_mapping(DEVICE, 47331)
    (arguments,) = router.action("DeletePortMapping")
    assert dict(arguments)["NewExternalPort"] == "47331"


def test_releasing_a_mapping_reports_success_and_never_raises(router):
    mapping = add_port_mapping(
        DEVICE, external_port=47331, internal_port=47331, internal_ip="192.168.1.50"
    )
    assert mapping.release() is True
    router.answers["DeletePortMapping"] = UpnpError("no", code=501)
    assert mapping.release() is False


def test_a_soap_fault_body_becomes_an_error_code():
    fault = b"""<?xml version="1.0"?>
    <s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><s:Fault>
      <detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0">
        <errorCode>718</errorCode>
        <errorDescription>ConflictInMappingEntry</errorDescription>
      </UPnPError></detail>
    </s:Fault></s:Body></s:Envelope>"""

    class _Error(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("http://192.168.1.1/ctl", 500, "Internal", {}, None)
            self._body = fault

        def read(self, _size=-1):
            return self._body

    error = discovery._fault(_Error(), "AddPortMapping")
    assert error.code == UPNP_CONFLICT
    assert "Conflict" in str(error)


def test_response_arguments_are_read_regardless_of_namespace():
    payload = b"""<?xml version="1.0"?>
    <s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>
      <u:GetExternalIPAddressResponse xmlns:u="urn:schemas-upnp-org:service:WANIPConnection:1">
        <NewExternalIPAddress>198.51.100.4</NewExternalIPAddress>
      </u:GetExternalIPAddressResponse>
    </s:Body></s:Envelope>"""
    assert discovery._response_arguments(payload, "GetExternalIPAddress") == {
        "NewExternalIPAddress": "198.51.100.4"
    }


def test_unparseable_xml_is_simply_empty():
    assert discovery._walk(b"<not xml") == []
    assert discovery._response_arguments(b"<not xml", "Anything") == {}


def test_an_ssdp_response_header_is_read_case_insensitively():
    response = b"HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=120\r\nLocation: " + LOCATION.encode()
    assert discovery._http_header(response, "location") == LOCATION
    assert discovery._http_header(response, "st") == ""


# --------------------------------------------------------------------------
# Publishing a session
# --------------------------------------------------------------------------


def test_publishing_maps_the_port_and_publishes_the_external_address(router):
    result = publish_session(47331, timeout=0.01)
    assert result.method == METHOD_UPNP
    assert result.host == "203.0.113.7"
    assert result.port == 47331
    assert result.lan_ip == "192.168.1.50"
    assert result.mapping is not None
    assert result.internet_reachable
    assert discovery.ADVICE_FIREWALL in result.advice


def test_a_published_session_yields_a_join_code_for_that_address(router):
    token = new_token()
    result = publish_session(47331, timeout=0.01)
    assert decode_join_code(result.join_code(token)) == JoinCode("203.0.113.7", 47331, token)


def test_publishing_can_map_a_different_external_port(router):
    result = publish_session(47331, external_port=8080, timeout=0.01)
    assert result.port == 8080
    sent = dict(router.action("AddPortMapping")[0])
    assert sent["NewExternalPort"] == "8080"
    assert sent["NewInternalPort"] == "47331"


def test_no_router_falls_back_to_the_lan_address_with_advice(monkeypatch):
    monkeypatch.setattr(discovery, "_ssdp_search", lambda _timeout: [])
    monkeypatch.setattr(discovery, "local_ip", lambda: "192.168.1.50")
    result = publish_session(47331, timeout=0.01)
    assert result.method == METHOD_LAN
    assert result.host == "192.168.1.50"
    assert result.mapping is None
    assert not result.internet_reachable
    assert ADVICE_NO_IGD in result.advice


def test_a_refused_mapping_falls_back_and_says_why(router):
    router.answers["AddPortMapping"] = UpnpError("Action failed", code=501)
    result = publish_session(47331, timeout=0.01)
    assert result.method == METHOD_LAN
    assert result.host == "192.168.1.50"
    assert ADVICE_UPNP_REFUSED in result.advice
    assert result.error


def test_a_taken_port_says_so_first(router):
    router.answers["AddPortMapping"] = UpnpError("ConflictInMappingEntry", code=UPNP_CONFLICT)
    result = publish_session(47331, timeout=0.01)
    assert result.advice[0] == ADVICE_PORT_TAKEN


def test_cgnat_is_called_out_even_though_the_mapping_succeeded(router):
    router.answers["GetExternalIPAddress"] = {"NewExternalIPAddress": "100.71.4.9"}
    result = publish_session(47331, timeout=0.01)
    assert result.mapping is not None
    assert result.external_ip == "100.71.4.9"
    assert not result.internet_reachable
    assert ADVICE_CGNAT in result.advice


def test_a_private_wan_address_reports_double_nat(router):
    router.answers["GetExternalIPAddress"] = {"NewExternalIPAddress": "192.168.100.1"}
    result = publish_session(47331, timeout=0.01)
    assert ADVICE_DOUBLE_NAT in result.advice
    assert not result.internet_reachable


def test_a_silent_wan_address_still_publishes_the_lan_address(router):
    router.answers["GetExternalIPAddress"] = {}
    result = publish_session(47331, timeout=0.01)
    assert result.host == "192.168.1.50"
    assert result.method == METHOD_LAN
    assert ADVICE_NO_EXTERNAL_IP in result.advice


def test_upnp_can_be_switched_off(router):
    result = publish_session(47331, use_upnp=False, timeout=0.01)
    assert result.method == METHOD_LAN
    assert result.host == "192.168.1.50"
    assert router.calls == []


def test_a_manual_host_skips_discovery_entirely(router):
    result = publish_session(47331, manual_host="session.example.org", timeout=0.01)
    assert result.method == METHOD_MANUAL
    assert result.host == "session.example.org"
    assert router.calls == []
    assert result.release() is True


def test_a_manual_host_keeps_the_local_port_when_no_other_is_given(router):
    result = publish_session(47331, manual_host="session.example.org", timeout=0.01)
    assert result.port == 47331


def test_a_tunnel_can_publish_a_port_of_its_own(router):
    # A tunnel hands back an address *and* a port, rarely the one being listened
    # on — the join code has to carry the tunnel's, or nobody reaches the session.
    result = publish_session(
        47331, manual_host="1.tcp.eu.ngrok.io", external_port=19274, timeout=0.01
    )
    assert (result.host, result.port) == ("1.tcp.eu.ngrok.io", 19274)
    assert router.calls == []


def test_publishing_can_be_told_the_internal_address(router):
    result = publish_session(47331, internal_ip="10.0.0.9", timeout=0.01)
    assert dict(router.action("AddPortMapping")[0])["NewInternalClient"] == "10.0.0.9"
    assert result.lan_ip == "10.0.0.9"


def test_the_mapping_targets_the_interface_facing_the_router(router, monkeypatch):
    """A default route held by something else (a VPN, a virtual adapter) is not
    the address to forward to — the router's own subnet is."""
    monkeypatch.setattr(discovery, "local_ip", lambda: "10.0.0.5")
    monkeypatch.setattr(
        discovery,
        "local_ip_for",
        lambda host: "192.168.1.50" if host == "192.168.1.1" else "10.0.0.5",
    )
    result = publish_session(47331, timeout=0.01)
    assert dict(router.action("AddPortMapping")[0])["NewInternalClient"] == "192.168.1.50"
    assert result.lan_ip == "192.168.1.50"


def test_an_explicit_internal_address_is_not_second_guessed(router, monkeypatch):
    monkeypatch.setattr(discovery, "local_ip_for", lambda host: "192.168.1.50")
    publish_session(47331, internal_ip="10.0.0.9", timeout=0.01)
    assert dict(router.action("AddPortMapping")[0])["NewInternalClient"] == "10.0.0.9"


# --------------------------------------------------------------------------
# The transport seam
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_transport_registry():
    """Registrations are process-wide; hand the registry back as it was found."""
    before = discovery.transports.keys()
    yield
    for key in discovery.transports.keys():
        if key not in before:
            discovery.transports.unregister(key)


def test_a_plain_host_uses_direct_tcp():
    assert isinstance(transport_for("203.0.113.7"), TcpTransport)
    assert transport_scheme("203.0.113.7") == ""
    assert transport_scheme("session.example.org") == ""


def test_a_registered_scheme_builds_its_own_transport():
    seen: list[str] = []

    class FakeRelay(Transport):
        def listen(self, host, port):  # pragma: no cover - never dialled here
            raise AssertionError

        def connect(self, host, port, *, timeout=0.0):  # pragma: no cover
            raise AssertionError

    def factory(url: str) -> Transport:
        seen.append(url)
        return FakeRelay()

    discovery.transports.register(RELAY_SCHEME, factory)
    url = f"{RELAY_SCHEME}://relay.example:9000/abcdef"
    assert transport_scheme(url) == RELAY_SCHEME
    assert isinstance(transport_for(url), FakeRelay)
    assert seen == [url]


def test_an_unregistered_scheme_is_a_readable_refusal():
    with pytest.raises(UnknownTransportError, match=RELAY_SCHEME):
        transport_for(f"{RELAY_SCHEME}://relay.example:9000/abcdef")
