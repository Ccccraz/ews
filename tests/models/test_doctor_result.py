from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from ews.models import DoctorCheck, DoctorResult


def test_doctor_check_names_are_stable() -> None:
    assert [check.value for check in DoctorCheck] == [
        "configuration",
        "keychain",
        "system_tls",
        "ews_login",
    ]


def test_doctor_result_carries_diagnosis_evidence(tmp_path: Path) -> None:
    result = DoctorResult.model_validate(
        {
            "profile_path": str(tmp_path / "profile.toml"),
            "endpoint": "https://mail.example.com/EWS/Exchange.asmx",
            "tls": _tls_check(),
            "connection": {
                "user": "DOMAIN\\agent",
                "mailbox": "agent@example.com",
                "server_version": "Build=15.2.1.2, API=Exchange2016",
                "inbox_total_count": 12,
                "inbox_unread_count": 3,
            },
        }
    )

    assert result.profile_path == tmp_path / "profile.toml"
    assert str(result.endpoint) == "https://mail.example.com/EWS/Exchange.asmx"
    assert result.tls.certificate_expires_at == datetime(2027, 1, 1, tzinfo=UTC)
    assert result.connection.server_version == "Build=15.2.1.2, API=Exchange2016"


def test_doctor_result_rejects_unknown_evidence() -> None:
    with pytest.raises(ValidationError):
        DoctorResult.model_validate(
            {
                "profile_path": "/tmp/profile.toml",
                "endpoint": "https://mail.example.com/EWS/Exchange.asmx",
                "tls": _tls_check(),
                "connection": {
                    "user": "DOMAIN\\agent",
                    "mailbox": "agent@example.com",
                    "server_version": "Exchange2016",
                    "inbox_total_count": 12,
                    "inbox_unread_count": 3,
                },
                "password": "top-secret",
            }
        )


def _tls_check() -> dict[str, str]:
    return {
        "host": "mail.example.com",
        "protocol": "TLSv1.3",
        "cipher": "TLS_AES_256_GCM_SHA384",
        "certificate_subject": "commonName=mail.example.com",
        "certificate_issuer": "commonName=Example Issuing CA",
        "certificate_expires_at": "2027-01-01T00:00:00Z",
    }
