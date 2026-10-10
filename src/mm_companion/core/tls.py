"""A TLS client context that can find the trust store on any Linux.

:func:`ssl.create_default_context` trusts whatever OpenSSL was *compiled* to look
for, and in the frozen app that is the build machine's choice, not the player's.
The Linux release is built on Ubuntu, whose OpenSSL looks under ``/usr/lib/ssl``;
Arch, SteamOS and Fedora keep their certificates elsewhere, so there the bundled
OpenSSL starts with an empty trust store and refuses every certificate as
"unable to get local issuer certificate" — the relay's included.

:func:`client_context` is the default context plus one fallback: when neither the
compiled-in CA file nor CA directory exists, it loads the first system bundle it
finds from the same list Go's ``crypto/x509`` searches. Where the defaults do
exist — a source install, Debian and Ubuntu, or a player who set
``SSL_CERT_FILE`` — nothing changes. Windows is untouched too: Python reads the
Windows certificate store there, and none of these paths exist.
"""

from __future__ import annotations

import os
import ssl

#: Where Linux distributions keep their CA bundle, most common first. The same
#: list Go's ``crypto/x509`` searches.
SYSTEM_CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu, Arch, SteamOS, Gentoo
    "/etc/pki/tls/certs/ca-bundle.crt",  # Fedora, RHEL 6
    "/etc/ssl/ca-bundle.pem",  # openSUSE
    "/etc/pki/tls/cacert.pem",  # OpenELEC
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # CentOS, RHEL 7
    "/etc/ssl/cert.pem",  # Alpine
)


def client_context() -> ssl.SSLContext:
    """A verifying client context, with a system CA bundle if OpenSSL found none."""
    context = ssl.create_default_context()
    if not _has_default_trust():
        _load_system_bundle(context)
    return context


def _has_default_trust() -> bool:
    # Each path is None when it does not exist, and both honour SSL_CERT_FILE and
    # SSL_CERT_DIR, so a player's override counts as trust found.
    paths = ssl.get_default_verify_paths()
    return paths.cafile is not None or paths.capath is not None


def _load_system_bundle(context: ssl.SSLContext) -> None:
    for bundle in SYSTEM_CA_BUNDLES:
        if not os.path.isfile(bundle):
            continue
        try:
            context.load_verify_locations(cafile=bundle)
        except (OSError, ssl.SSLError):
            continue
        return
