from pathlib import Path

from pydantic import Field

from ews_cli.contracts import ContractModel


class AttachmentSaveResult(ContractModel):
    """Result of streaming one attachment to an explicit local path."""

    user: str
    message_id: str
    attachment_id: str
    name: str
    content_type: str | None
    path: Path
    bytes_written: int = Field(ge=0)
