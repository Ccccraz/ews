from pydantic import Field

from ews_cli.contracts import ContractModel
from ews_cli.models.contact import ContactAddress, ContactEmail, ContactPhone


class DirectoryContact(ContractModel):
    """One contact returned by an Exchange directory (GAL) search."""

    display_name: str
    email_address: str | None = None
    mailbox_type: str
    given_name: str | None = None
    surname: str | None = None
    company_name: str | None = None
    department: str | None = None
    job_title: str | None = None
    email_alias: str | None = None
    directory_id: str | None = None
    emails: list[ContactEmail] = []
    phones: list[ContactPhone] = []
    addresses: list[ContactAddress] = []


class DirectorySearchQuery(ContractModel):
    """Validated query and client-side limit for one directory search."""

    query: str = Field(min_length=1)
    limit: int = Field(default=25, ge=1, le=100)


class DirectorySearchResult(ContractModel):
    """One live directory search result, never read from the local cache."""

    user: str
    query: str
    contacts: list[DirectoryContact]
    truncated: bool = False
