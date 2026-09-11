import pytest
from pydantic import ValidationError

from ews.models import Profile, Server, User

PROFILE_DATA = {
    "server": {"endpoint": "https://mail.example.com/EWS/Exchange.asmx"},
    "user": {"mailbox": "agent@example.com", "username": "DOMAIN\\agent"},
}


def test_profile_combines_server_and_user() -> None:
    profile = Profile.model_validate(PROFILE_DATA)

    assert isinstance(profile.server, Server)
    assert isinstance(profile.user, User)
    assert str(profile.server.endpoint) == "https://mail.example.com/EWS/Exchange.asmx"
    assert profile.user.mailbox == "agent@example.com"
    assert profile.user.username == "DOMAIN\\agent"


def test_profile_rejects_password() -> None:
    with pytest.raises(ValidationError):
        Profile.model_validate({**PROFILE_DATA, "password": "must-not-be-stored"})


def test_profile_rejects_invalid_nested_model() -> None:
    with pytest.raises(ValidationError):
        Profile.model_validate(
            {
                **PROFILE_DATA,
                "server": {"endpoint": "http://mail.example.com/EWS/Exchange.asmx"},
            }
        )


def test_profile_is_immutable() -> None:
    profile = Profile.model_validate(PROFILE_DATA)
    other_user = User(mailbox="other@example.com", username="DOMAIN\\other")

    with pytest.raises(ValidationError):
        profile.user = other_user
