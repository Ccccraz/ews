from pathlib import Path

import pytest
from pydantic import ValidationError

from ews.models import AttachmentSaveResult


def test_attachment_save_result_serializes_the_destination_path() -> None:
    result = AttachmentSaveResult(
        user="DOMAIN\\agent",
        message_id="message-id",
        attachment_id="attachment-id",
        name="report.pdf",
        content_type="application/pdf",
        path=Path("/tmp/report.pdf"),
        bytes_written=7,
    )

    assert result.model_dump() == {
        "user": "DOMAIN\\agent",
        "message_id": "message-id",
        "attachment_id": "attachment-id",
        "name": "report.pdf",
        "content_type": "application/pdf",
        "path": Path("/tmp/report.pdf"),
        "bytes_written": 7,
    }
    assert '"path":"/tmp/report.pdf"' in result.model_dump_json()


def test_attachment_save_result_accepts_missing_server_metadata() -> None:
    result = AttachmentSaveResult(
        user="DOMAIN\\agent",
        message_id="message-id",
        attachment_id="attachment-id",
        name="",
        content_type=None,
        path=Path("/tmp/file.bin"),
        bytes_written=0,
    )

    assert result.name == ""
    assert result.content_type is None
    assert result.bytes_written == 0


def test_attachment_save_result_rejects_impossible_byte_counts() -> None:
    with pytest.raises(ValidationError):
        AttachmentSaveResult(
            user="DOMAIN\\agent",
            message_id="message-id",
            attachment_id="attachment-id",
            name="report.pdf",
            content_type=None,
            path=Path("/tmp/report.pdf"),
            bytes_written=-1,
        )
