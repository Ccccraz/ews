from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TlsCheckResult(BaseModel):
    """Observable result of verifying an endpoint against the system trust store."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    host: str
    protocol: str
    cipher: str
    certificate_subject: str
    certificate_issuer: str
    certificate_expires_at: datetime
