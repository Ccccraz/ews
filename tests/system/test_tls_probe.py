import socket
import ssl
from datetime import UTC, datetime

import pytest
import truststore
from pytest import MonkeyPatch

from ews.models import Profile, TlsCheckResult
from ews.system import SystemTlsProbe, TlsProbeError

CERTIFICATE: dict[str, object] = {
    "subject": (
        (("commonName", "mail.example.com"),),
        (("organizationName", "Example Corporation"),),
    ),
    "issuer": ((("commonName", "Example Issuing CA"),),),
    "notAfter": "Jan  1 00:00:00 2027 GMT",
}


class FakeTlsSession:
    def __init__(
        self,
        certificate: dict[str, object],
        *,
        version: str | None = "TLSv1.3",
        cipher: tuple[str, str, int] | None = ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256),
    ) -> None:
        self._certificate = certificate
        self._version = version
        self._cipher = cipher

    def getpeercert(self) -> dict[str, object]:
        return self._certificate

    def version(self) -> str | None:
        return self._version

    def cipher(self) -> tuple[str, str, int] | None:
        return self._cipher

    def __enter__(self) -> FakeTlsSession:
        return self

    def __exit__(self, *args: object) -> None:
        del args


class FakeConnection:
    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *args: object) -> None:
        del args


class FakeContext:
    """Stand-in for truststore.SSLContext that records verification settings."""

    def __init__(
        self,
        protocol: int,
        *,
        session: FakeTlsSession,
        handshake_error: Exception | None,
    ) -> None:
        self.protocol = protocol
        self.check_hostname = False
        self.verify_mode = ssl.CERT_NONE
        self.server_hostnames: list[str | None] = []
        self._session = session
        self._handshake_error = handshake_error

    def wrap_socket(self, connection: object, server_hostname: str | None) -> FakeTlsSession:
        del connection
        self.server_hostnames.append(server_hostname)
        if self._handshake_error is not None:
            raise self._handshake_error
        return self._session


class ProbeHarness:
    """Fake TLS dependencies that record how SystemTlsProbe used them."""

    def __init__(
        self,
        monkeypatch: MonkeyPatch,
        *,
        session: FakeTlsSession | None = None,
        handshake_error: Exception | None = None,
        connect_error: Exception | None = None,
    ) -> None:
        self.contexts: list[FakeContext] = []
        self.connections: list[tuple[tuple[str, int], float]] = []
        self._session = FakeTlsSession(CERTIFICATE) if session is None else session
        self._handshake_error = handshake_error
        self._connect_error = connect_error
        monkeypatch.setattr(truststore, "SSLContext", self._build_context)
        monkeypatch.setattr(socket, "create_connection", self._connect)

    @property
    def context(self) -> FakeContext:
        return self.contexts[0]

    def _build_context(self, protocol: int) -> FakeContext:
        context = FakeContext(
            protocol,
            session=self._session,
            handshake_error=self._handshake_error,
        )
        self.contexts.append(context)
        return context

    def _connect(self, address: tuple[str, int], timeout: float) -> FakeConnection:
        self.connections.append((address, timeout))
        if self._connect_error is not None:
            raise self._connect_error
        return FakeConnection()


def test_probe_verifies_the_endpoint_against_the_system_trust_store(
    monkeypatch: MonkeyPatch,
) -> None:
    harness = ProbeHarness(monkeypatch)

    result = SystemTlsProbe().probe(_profile())

    assert harness.connections == [(("mail.example.com", 443), 10.0)]
    assert harness.context.protocol == ssl.PROTOCOL_TLS_CLIENT
    assert harness.context.check_hostname is True
    assert harness.context.verify_mode == ssl.CERT_REQUIRED
    assert harness.context.server_hostnames == ["mail.example.com"]
    assert result == TlsCheckResult(
        host="mail.example.com",
        protocol="TLSv1.3",
        cipher="TLS_AES_256_GCM_SHA384",
        certificate_subject="commonName=mail.example.com, organizationName=Example Corporation",
        certificate_issuer="commonName=Example Issuing CA",
        certificate_expires_at=datetime(2027, 1, 1, tzinfo=UTC),
    )


def test_probe_uses_the_configured_port_and_timeout(monkeypatch: MonkeyPatch) -> None:
    harness = ProbeHarness(monkeypatch)

    result = SystemTlsProbe(timeout=2.5).probe(
        _profile("https://mail.example.com:8443/EWS/Exchange.asmx")
    )

    assert harness.connections == [(("mail.example.com", 8443), 2.5)]
    assert result.host == "mail.example.com"


def test_probe_reports_an_incomplete_tls_session(monkeypatch: MonkeyPatch) -> None:
    ProbeHarness(
        monkeypatch,
        session=FakeTlsSession(CERTIFICATE, version=None, cipher=None),
    )

    result = SystemTlsProbe().probe(_profile())

    assert result.protocol == ""
    assert result.cipher == ""


def test_probe_reports_a_certificate_without_a_subject_or_issuer(monkeypatch: MonkeyPatch) -> None:
    harness = ProbeHarness(
        monkeypatch,
        session=FakeTlsSession({"notAfter": CERTIFICATE["notAfter"]}),
    )

    result = SystemTlsProbe().probe(_profile())

    assert harness.context.server_hostnames == ["mail.example.com"]
    assert result.certificate_subject == ""
    assert result.certificate_issuer == ""


def test_probe_maps_certificate_verification_failures(monkeypatch: MonkeyPatch) -> None:
    ProbeHarness(
        monkeypatch,
        handshake_error=ssl.SSLCertVerificationError("certificate verify failed"),
    )

    with pytest.raises(TlsProbeError, match="TLS verification failed for mail.example.com:443"):
        SystemTlsProbe().probe(_profile())


def test_probe_maps_connection_failures(monkeypatch: MonkeyPatch) -> None:
    ProbeHarness(monkeypatch, connect_error=TimeoutError("connection timed out"))

    with pytest.raises(TlsProbeError, match="connection timed out"):
        SystemTlsProbe().probe(_profile())


def test_probe_rejects_a_certificate_without_an_expiry_date(monkeypatch: MonkeyPatch) -> None:
    ProbeHarness(monkeypatch, session=FakeTlsSession({}))

    with pytest.raises(TlsProbeError, match="no expiry date"):
        SystemTlsProbe().probe(_profile())


def test_probe_rejects_an_invalid_expiry_date(monkeypatch: MonkeyPatch) -> None:
    ProbeHarness(monkeypatch, session=FakeTlsSession({"notAfter": "not-a-date"}))

    with pytest.raises(TlsProbeError, match="expiry date is invalid"):
        SystemTlsProbe().probe(_profile())


def _profile(endpoint: str = "https://mail.example.com/EWS/Exchange.asmx") -> Profile:
    return Profile.model_validate(
        {
            "server": {"endpoint": endpoint},
            "user": {"mailbox": "agent@example.com", "username": "DOMAIN\\agent"},
        }
    )
