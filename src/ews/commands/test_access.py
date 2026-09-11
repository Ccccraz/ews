from ews.config import (
    InvalidProfileError,
    PasswordNotFoundError,
    PasswordStore,
    PasswordStoreError,
    ProfileNotFoundError,
    ProfileStore,
)
from ews.contracts import Error, ErrorEnvelope, SuccessEnvelope, write_contract
from ews.exchange import EwsAuthenticationError, EwsClient, EwsServiceError
from ews.models import Profile


def test_access(user: str) -> int:
    """Test authenticated EWS access for the configured user."""
    try:
        profile = ProfileStore().load()
    except (ProfileNotFoundError, InvalidProfileError, OSError) as error:
        return _fail("configuration_error", str(error), 2)

    if not _matches_user(user, profile):
        return _fail("profile_not_found", f"Profile not found for user: {user}", 4)

    try:
        password = PasswordStore().get(profile)
    except (PasswordNotFoundError, PasswordStoreError) as error:
        return _fail("authentication_error", str(error), 3)

    try:
        result = EwsClient().test_access(profile, password)
    except EwsAuthenticationError as error:
        return _fail("authentication_error", str(error), 3)
    except EwsServiceError as error:
        return _fail("service_error", str(error), 5, retryable=True)

    write_contract(SuccessEnvelope(data=result))
    return 0


def _matches_user(user: str, profile: Profile) -> bool:
    expected = user.casefold()
    return expected in {
        profile.user.username.casefold(),
        str(profile.user.mailbox).casefold(),
    }


def _fail(code: str, message: str, exit_code: int, *, retryable: bool = False) -> int:
    write_contract(ErrorEnvelope(error=Error(code=code, message=message, retryable=retryable)))
    return exit_code
