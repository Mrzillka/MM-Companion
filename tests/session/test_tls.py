"""The TLS client context: the system CA bundle fallback for a frozen app's OpenSSL."""

from __future__ import annotations

import ssl

from mm_companion.core import tls

NO_DEFAULTS = ssl.DefaultVerifyPaths(*[None] * 6)


def _subjects(context: ssl.SSLContext) -> list:
    return [cert["subject"] for cert in context.get_ca_certs()]


def _localhost(context: ssl.SSLContext) -> bool:
    return ((("commonName", "localhost"),),) in _subjects(context)


def test_the_system_bundle_is_left_alone_when_openssl_found_its_own(tls_cert, monkeypatch):
    cert, _ = tls_cert
    found = ssl.DefaultVerifyPaths("/compiled/in/cert.pem", None, "", "", "", "")
    monkeypatch.setattr(tls.ssl, "get_default_verify_paths", lambda: found)
    monkeypatch.setattr(tls, "SYSTEM_CA_BUNDLES", (str(cert),))
    assert not _localhost(tls.client_context())


def test_a_ca_directory_alone_counts_as_trust_found(tls_cert, monkeypatch):
    cert, _ = tls_cert
    found = ssl.DefaultVerifyPaths(None, "/compiled/in/certs", "", "", "", "")
    monkeypatch.setattr(tls.ssl, "get_default_verify_paths", lambda: found)
    monkeypatch.setattr(tls, "SYSTEM_CA_BUNDLES", (str(cert),))
    assert not _localhost(tls.client_context())


def test_the_first_readable_system_bundle_is_loaded_when_openssl_found_none(
    tls_cert, tmp_path, monkeypatch
):
    cert, _ = tls_cert
    garbage = tmp_path / "garbage.pem"
    garbage.write_text("not a certificate")
    monkeypatch.setattr(tls.ssl, "get_default_verify_paths", lambda: NO_DEFAULTS)
    monkeypatch.setattr(
        tls, "SYSTEM_CA_BUNDLES", (str(tmp_path / "missing.pem"), str(garbage), str(cert))
    )
    assert _localhost(tls.client_context())


def test_no_bundle_anywhere_still_gives_a_verifying_context(tmp_path, monkeypatch):
    monkeypatch.setattr(tls.ssl, "get_default_verify_paths", lambda: NO_DEFAULTS)
    monkeypatch.setattr(tls, "SYSTEM_CA_BUNDLES", (str(tmp_path / "missing.pem"),))
    context = tls.client_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
