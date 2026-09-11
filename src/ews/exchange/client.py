# pyright: reportMissingTypeStubs=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from typing import Any, cast

from exchangelib import DELEGATE, NTLM, Account, Configuration, Credentials
from exchangelib.errors import (
    ErrorAccessDenied,
    ErrorNonExistentMailbox,
    EWSError,
    UnauthorizedError,
)
from pydantic import SecretStr

from ews.models import ConnectionTestResult, Profile


class EwsAuthenticationError(Exception):
    """Raised when EWS rejects the configured identity or credentials."""


class EwsServiceError(Exception):
    """Raised when EWS cannot complete the connection test."""


class EwsClient:
    """Perform synchronous operations against one EWS profile."""

    def test_access(self, profile: Profile, password: SecretStr) -> ConnectionTestResult:
        """Authenticate and fetch Inbox metadata through EWS."""
        try:
            credentials = Credentials(
                username=profile.user.username,
                password=password.get_secret_value(),
            )
            configuration = Configuration(
                service_endpoint=str(profile.server.endpoint),
                credentials=credentials,
                auth_type=NTLM,
            )
            account = Account(
                primary_smtp_address=str(profile.user.mailbox),
                config=configuration,
                autodiscover=False,
                access_type=DELEGATE,
            )
            inbox = cast(Any, account.inbox)
            inbox.refresh()
            server_version = str(account.version)
        except (UnauthorizedError, ErrorAccessDenied, ErrorNonExistentMailbox) as error:
            raise EwsAuthenticationError(
                "EWS rejected the configured credentials or mailbox"
            ) from error
        except EWSError as error:
            raise EwsServiceError("Unable to access the EWS service") from error

        return ConnectionTestResult(
            user=profile.user.username,
            mailbox=profile.user.mailbox,
            server_version=server_version,
            inbox_total_count=inbox.total_count,
            inbox_unread_count=inbox.unread_count,
        )
