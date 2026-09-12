import pytest
from pydantic import ValidationError

from ews.models import (
    ContactAddress,
    ContactEmail,
    ContactPhone,
    DirectoryContact,
    DirectorySearchQuery,
    DirectorySearchResult,
)


def test_directory_search_query_defaults_and_bounds() -> None:
    assert DirectorySearchQuery(query="abel").limit == 25
    with pytest.raises(ValidationError):
        DirectorySearchQuery(query="")
    with pytest.raises(ValidationError):
        DirectorySearchQuery(query="abel", limit=0)
    with pytest.raises(ValidationError):
        DirectorySearchQuery(query="abel", limit=101)


def test_directory_contact_and_result_round_trip() -> None:
    contact = DirectoryContact(
        display_name="Abel, Jacqueline",
        email_address="jabel@dpz.eu",
        mailbox_type="Mailbox",
        given_name="Jacqueline",
        surname="Abel",
        company_name="Deutsches Primatenzentrum GmbH",
        department="Tierhaltung",
        job_title="Mitarbeiterin",
        email_alias="jabel",
        directory_id="<GUID=82035e82-399d-4bad-9b8d-b8b210ba8aa4>",
        emails=[ContactEmail(label="EmailAddress1", address="jabel@dpz.eu")],
        phones=[ContactPhone(label="BusinessPhone", number="+49 551 3851-0")],
        addresses=[ContactAddress(label="Business", city="Göttingen", country="Deutschland")],
    )
    result = DirectorySearchResult(
        user="DOMAIN\\agent", query="abel", contacts=[contact], truncated=False
    )

    assert result.contacts[0].emails[0].address == "jabel@dpz.eu"
    assert result.contacts[0].phones[0].number == "+49 551 3851-0"
    assert result.contacts[0].addresses[0].city == "Göttingen"
    assert result.truncated is False
