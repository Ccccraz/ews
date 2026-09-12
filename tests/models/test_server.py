import pytest
from pydantic import HttpUrl, ValidationError

from ews_cli.models import Server


def test_server_accepts_ews_endpoint() -> None:
    server = Server.model_validate({"endpoint": "https://mail.example.com/EWS/Exchange.asmx"})

    assert str(server.endpoint) == "https://mail.example.com/EWS/Exchange.asmx"


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://mail.example.com/EWS/Exchange.asmx",
        "https://user:password@mail.example.com/EWS/Exchange.asmx",
        "https://mail.example.com/",
        "https://mail.example.com/EWS/Exchange.asmx?debug=true",
        "https://mail.example.com/EWS/Exchange.asmx#fragment",
    ],
)
def test_server_rejects_invalid_endpoint(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        Server.model_validate({"endpoint": endpoint})


def test_server_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Server.model_validate(
            {
                "endpoint": "https://mail.example.com/EWS/Exchange.asmx",
                "verify_tls": False,
            }
        )


def test_server_is_immutable() -> None:
    server = Server.model_validate({"endpoint": "https://mail.example.com/EWS/Exchange.asmx"})

    with pytest.raises(ValidationError):
        server.endpoint = HttpUrl("https://other.example.com/EWS/Exchange.asmx")
