from typing import Protocol

from ews_cli.models import Profile, TlsCheckResult


class TlsProbe(Protocol):
    """Strict application boundary for system trust store verification."""

    def probe(self, profile: Profile) -> TlsCheckResult: ...
