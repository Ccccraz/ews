from typing import Protocol

from ews.models import Profile, TlsCheckResult


class TlsProbe(Protocol):
    """Strict application boundary for system trust store verification."""

    def probe(self, profile: Profile) -> TlsCheckResult: ...
