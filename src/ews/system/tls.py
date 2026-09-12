import socket
import ssl
from datetime import UTC, datetime
from typing import cast

import truststore

from ews.models import Profile, TlsCheckResult

_HTTPS_PORT = 443
_DEFAULT_TIMEOUT_SECONDS = 10.0
_DistinguishedName = tuple[tuple[tuple[str, str], ...], ...]


class TlsProbeError(Exception):
    """Raised when the EWS endpoint cannot complete a verified TLS handshake."""


class SystemTlsProbe:
    """Verify an EWS endpoint certificate against the system trust store."""

    def __init__(self, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout

    def probe(self, profile: Profile) -> TlsCheckResult:
        host = cast(str, profile.server.endpoint.host)
        port = profile.server.endpoint.port or _HTTPS_PORT
        try:
            context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
            with socket.create_connection((host, port), self._timeout) as connection:
                with context.wrap_socket(connection, server_hostname=host) as session:
                    return _check_result(host, session)
        except OSError as error:
            raise TlsProbeError(f"TLS verification failed for {host}:{port}: {error}") from error


def _check_result(host: str, session: ssl.SSLSocket) -> TlsCheckResult:
    certificate = cast("dict[str, object]", session.getpeercert())
    protocol = session.version()
    cipher = session.cipher()
    return TlsCheckResult(
        host=host,
        protocol="" if protocol is None else protocol,
        cipher="" if cipher is None else cipher[0],
        certificate_subject=_distinguished_name(certificate.get("subject")),
        certificate_issuer=_distinguished_name(certificate.get("issuer")),
        certificate_expires_at=_certificate_expiry(certificate.get("notAfter")),
    )


def _distinguished_name(value: object) -> str:
    if not isinstance(value, tuple):
        return ""
    names = cast("_DistinguishedName", value)
    return ", ".join(f"{name}={text}" for relative_name in names for name, text in relative_name)


def _certificate_expiry(value: object) -> datetime:
    if not isinstance(value, str):
        raise TlsProbeError("The EWS endpoint certificate has no expiry date")
    try:
        expires_at = ssl.cert_time_to_seconds(value)
    except ValueError as error:
        raise TlsProbeError("The EWS endpoint certificate expiry date is invalid") from error
    return datetime.fromtimestamp(expires_at, tz=UTC)
