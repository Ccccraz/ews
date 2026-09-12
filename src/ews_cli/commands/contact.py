from typing import Annotated

from cyclopts import Parameter

from ews_cli.commands.context import CommandContext, run_read
from ews_cli.models import (
    ContactGetResult,
    ContactListQuery,
    ContactListResult,
    DirectorySearchQuery,
    DirectorySearchResult,
)


def list_contacts(
    *,
    folder: str | None = None,
    search: str | None = None,
    offset: str = "0",
    limit: str = "50",
    context: Annotated[CommandContext, Parameter(parse=False, show=False)],
) -> int:
    """List cached contacts with an optional folder and text filter."""

    def operation(user: str) -> ContactListResult:
        query = ContactListQuery.model_validate(
            {"folder": folder, "search": search, "offset": offset, "limit": limit}
        )
        return context.service.list_contacts(user, query)

    return run_read(context, operation)


def get_contact(
    contact_id: str,
    *,
    context: Annotated[CommandContext, Parameter(parse=False, show=False)],
) -> int:
    """Get one cached contact with every common EWS field."""

    def operation(user: str) -> ContactGetResult:
        return context.service.get_contact(user, contact_id)

    return run_read(context, operation)


def search_directory(
    query: str,
    *,
    limit: str = "25",
    context: Annotated[CommandContext, Parameter(parse=False, show=False)],
) -> int:
    """Search the Exchange directory (GAL) for people and distribution lists."""

    def operation(user: str) -> DirectorySearchResult:
        validated = DirectorySearchQuery.model_validate({"query": query, "limit": limit})
        return context.service.search_directory(user, validated)

    return run_read(context, operation)
