from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, HttpUrl

from ews.models.connection_test import ConnectionTestResult
from ews.models.tls import TlsCheckResult


class DoctorCheck(StrEnum):
    """A stage verified by the doctor command, in execution order."""

    CONFIGURATION = "configuration"
    KEYRING = "keyring"
    SYSTEM_TLS = "system_tls"
    EWS_LOGIN = "ews_login"


class DoctorResult(BaseModel):
    """Evidence from a complete profile, keyring, TLS and login diagnosis."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_path: Path
    endpoint: HttpUrl
    tls: TlsCheckResult
    connection: ConnectionTestResult
