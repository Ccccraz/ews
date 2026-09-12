from datetime import date

from pydantic import Field

from ews_cli.contracts import ContractModel
from ews_cli.models.mailbox import Pagination


class ContactEmail(ContractModel):
    """One labeled contact email address."""

    label: str | None = None
    address: str = Field(min_length=1)


class ContactPhone(ContractModel):
    """One labeled contact phone number."""

    label: str | None = None
    number: str = Field(min_length=1)


class ContactAddress(ContractModel):
    """One labeled physical address."""

    label: str | None = None
    street: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None


class ContactIm(ContractModel):
    """One labeled instant-messaging address."""

    label: str | None = None
    address: str = Field(min_length=1)


class Contact(ContractModel):
    """A cached personal contact with its common EWS fields."""

    id: str
    change_key: str
    parent_folder_id: str
    folder_name: str | None = None
    display_name: str
    file_as: str | None = None
    given_name: str | None = None
    middle_name: str | None = None
    surname: str | None = None
    nickname: str | None = None
    initials: str | None = None
    generation: str | None = None
    company_name: str | None = None
    department: str | None = None
    job_title: str | None = None
    office: str | None = None
    manager: str | None = None
    profession: str | None = None
    business_homepage: str | None = None
    emails: list[ContactEmail] = []
    phones: list[ContactPhone] = []
    addresses: list[ContactAddress] = []
    im_addresses: list[ContactIm] = []
    categories: list[str] = []
    notes: str | None = None
    birthday: date | None = None
    has_picture: bool = False


class ContactListQuery(ContractModel):
    """Validated filters and pagination for one contact list request."""

    folder: str | None = None
    search: str | None = Field(default=None, min_length=1)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)


class ContactListResult(ContractModel):
    """One page of cached contacts."""

    user: str
    contacts: list[Contact]
    pagination: Pagination


class ContactGetResult(ContractModel):
    """One cached contact returned for the selected profile."""

    user: str
    contact: Contact
