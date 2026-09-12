import pytest
from pydantic import ValidationError

from ews_cli.models import User


def test_user_accepts_mailbox_identity() -> None:
    user = User(mailbox="agent@EXAMPLE.COM", username=" DOMAIN\\agent ")

    assert user.mailbox == "agent@example.com"
    assert user.username == "DOMAIN\\agent"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mailbox", "not-an-email"),
        ("username", "  "),
    ],
)
def test_user_rejects_invalid_identity(field: str, value: str) -> None:
    values = {"mailbox": "agent@example.com", "username": "DOMAIN\\agent"}
    values[field] = value

    with pytest.raises(ValidationError):
        User.model_validate(values)


def test_user_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        User.model_validate(
            {
                "mailbox": "agent@example.com",
                "username": "DOMAIN\\agent",
                "password": "must-not-be-stored",
            }
        )


def test_user_is_immutable() -> None:
    user = User(mailbox="agent@example.com", username="DOMAIN\\agent")

    with pytest.raises(ValidationError):
        user.username = "DOMAIN\\other"
