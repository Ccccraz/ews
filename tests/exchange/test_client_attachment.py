# pyright: reportMissingTypeStubs=false

import io
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

import pytest
from exchangelib.errors import (
    ErrorAttachmentSizeLimitExceeded,
    ErrorInvalidAttachmentId,
    TransportError,
)
from pydantic import SecretStr
from pytest import MonkeyPatch

from ews.application import (
    AttachmentNotFoundError,
    DestinationExistsError,
    InvalidDestinationError,
    UnsupportedAttachmentError,
)
from ews.exchange import EwsClient, EwsNotFoundError, EwsRejectedError, EwsServiceError
from ews.models import Profile

PAYLOAD = b"attachment payload" * 100


class FakeCredentials:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FakeConfiguration:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FakeItemId:
    def __init__(self, id: str) -> None:
        self.id = id


class FakeAttachmentId:
    def __init__(self, id: str) -> None:
        self.id = id


class FakeStream:
    """Stands in for exchangelib's FileAttachmentIO context manager."""

    def __init__(self, content: bytes | None = None, error: Exception | None = None) -> None:
        self._content = content if content is not None else PAYLOAD
        self._error = error
        self.entered = 0

    def __enter__(self) -> io.BytesIO:
        self.entered += 1
        if self._error is not None:
            raise self._error
        return io.BytesIO(self._content)

    def __exit__(self, *args: object) -> None:
        del args


class FailingStream(io.BytesIO):
    """A stream that fails in the middle of a chunked read."""

    def read(self, size: int | None = -1) -> bytes:
        del size
        raise OSError("connection reset")


class FakeFileAttachment:
    def __init__(
        self,
        *,
        attachment_id: str = "attachment-id",
        name: str | None = "report.pdf",
        content_type: str | None = "application/pdf",
        content: bytes | None = None,
        error: Exception | None = None,
        failing: bool = False,
    ) -> None:
        self.attachment_id = FakeAttachmentId(attachment_id)
        self.name = name
        self.content_type = content_type
        self.stream = FakeStream(content, error)
        self.failing = failing
        self.fp_reads = 0

    @property
    def fp(self) -> io.BytesIO | FakeStream:
        self.fp_reads += 1
        return FailingStream(PAYLOAD) if self.failing else self.stream


class FakeItemAttachment:
    def __init__(self, *, attachment_id: str = "item-attachment-id") -> None:
        self.attachment_id = FakeAttachmentId(attachment_id)
        self.name = "attached message"
        self.content_type = None


class FakeMessage:
    def __init__(self, attachments: Sequence[object]) -> None:
        self.attachments = list(attachments)


class FakeAccount:
    last_instance: ClassVar[FakeAccount | None] = None
    items: ClassVar[list[object]] = []
    fetch_error: ClassVar[Exception | None] = None
    fetch_calls: ClassVar[list[tuple[list[FakeItemId], list[str]]]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        FakeAccount.last_instance = self

    def fetch(self, *, ids: Sequence[FakeItemId], only_fields: Sequence[str]) -> list[object]:
        FakeAccount.fetch_calls.append((list(ids), list(only_fields)))
        if FakeAccount.fetch_error is not None:
            raise FakeAccount.fetch_error
        return list(FakeAccount.items)


def test_save_attachment_streams_content_to_the_explicit_path(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    attachment = FakeFileAttachment()
    _install(monkeypatch, attachments=[attachment])
    destination = tmp_path / "report.pdf"

    result = EwsClient().save_attachment(
        _profile(), SecretStr("secret"), "message-id", "attachment-id", destination
    )

    assert destination.read_bytes() == PAYLOAD
    assert attachment.fp_reads == 1
    ids, only_fields = FakeAccount.fetch_calls[0]
    assert [item.id for item in ids] == ["message-id"]
    assert only_fields == ["attachments"]
    assert result.model_dump() == {
        "user": "DOMAIN\\agent",
        "message_id": "message-id",
        "attachment_id": "attachment-id",
        "name": "report.pdf",
        "content_type": "application/pdf",
        "path": destination,
        "bytes_written": len(PAYLOAD),
    }


def test_save_attachment_reports_missing_server_metadata(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    attachment = FakeFileAttachment(name=None, content_type=None, content=b"x")
    _install(monkeypatch, attachments=[attachment])

    result = EwsClient().save_attachment(
        _profile(), SecretStr("secret"), "message-id", "attachment-id", tmp_path / "file.bin"
    )

    assert result.name == ""
    assert result.content_type is None
    assert result.bytes_written == 1


def test_save_attachment_rejects_an_unknown_attachment(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _install(monkeypatch, attachments=[FakeFileAttachment(attachment_id="other")])

    with pytest.raises(AttachmentNotFoundError, match="Attachment not found: attachment-id"):
        EwsClient().save_attachment(
            _profile(), SecretStr("secret"), "message-id", "attachment-id", tmp_path / "file.bin"
        )


def test_save_attachment_rejects_an_item_attachment(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _install(monkeypatch, attachments=[FakeItemAttachment(attachment_id="attachment-id")])

    with pytest.raises(UnsupportedAttachmentError, match="Only file attachments"):
        EwsClient().save_attachment(
            _profile(), SecretStr("secret"), "message-id", "attachment-id", tmp_path / "file.bin"
        )


def test_save_attachment_rejects_an_unusable_destination(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    attachment = FakeFileAttachment()
    _install(monkeypatch, attachments=[attachment])

    with pytest.raises(InvalidDestinationError, match="Unable to create destination"):
        EwsClient().save_attachment(
            _profile(),
            SecretStr("secret"),
            "message-id",
            "attachment-id",
            tmp_path / ("x" * 300),
        )

    assert attachment.fp_reads == 0


def test_save_attachment_refuses_an_existing_destination(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    attachment = FakeFileAttachment()
    _install(monkeypatch, attachments=[attachment])
    destination = tmp_path / "report.pdf"
    destination.write_bytes(b"existing")

    with pytest.raises(DestinationExistsError, match="Destination already exists"):
        EwsClient().save_attachment(
            _profile(), SecretStr("secret"), "message-id", "attachment-id", destination
        )

    assert destination.read_bytes() == b"existing"
    assert attachment.fp_reads == 0


def test_save_attachment_rejects_a_missing_destination_directory(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _install(monkeypatch, attachments=[FakeFileAttachment()])

    with pytest.raises(InvalidDestinationError, match="Destination directory does not exist"):
        EwsClient().save_attachment(
            _profile(),
            SecretStr("secret"),
            "message-id",
            "attachment-id",
            tmp_path / "missing" / "report.pdf",
        )

    assert FakeAccount.fetch_calls == []


def test_save_attachment_rejects_a_destination_directory(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _install(monkeypatch, attachments=[FakeFileAttachment()])
    destination = tmp_path / "folder"
    destination.mkdir()

    with pytest.raises(InvalidDestinationError, match="Destination is a directory"):
        EwsClient().save_attachment(
            _profile(), SecretStr("secret"), "message-id", "attachment-id", destination
        )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ErrorInvalidAttachmentId("gone"), EwsNotFoundError),
        (ErrorAttachmentSizeLimitExceeded("too big"), EwsRejectedError),
        (TransportError("network"), EwsServiceError),
    ],
)
def test_save_attachment_translates_ews_stream_failures(
    tmp_path: Path, monkeypatch: MonkeyPatch, error: Exception, expected: type[Exception]
) -> None:
    _install(monkeypatch, attachments=[FakeFileAttachment(error=error)])
    destination = tmp_path / "report.pdf"

    with pytest.raises(expected):
        EwsClient().save_attachment(
            _profile(), SecretStr("secret"), "message-id", "attachment-id", destination
        )

    assert not destination.exists()


def test_save_attachment_removes_a_partial_file_after_a_transport_failure(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _install(monkeypatch, attachments=[FakeFileAttachment(failing=True)])
    destination = tmp_path / "report.pdf"

    with pytest.raises(EwsServiceError, match="Unable to download the attachment"):
        EwsClient().save_attachment(
            _profile(), SecretStr("secret"), "message-id", "attachment-id", destination
        )

    assert not destination.exists()


def test_save_attachment_translates_fetch_failures(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _install(monkeypatch, attachments=[])
    FakeAccount.fetch_error = ErrorInvalidAttachmentId("gone")

    with pytest.raises(EwsNotFoundError):
        EwsClient().save_attachment(
            _profile(), SecretStr("secret"), "message-id", "attachment-id", tmp_path / "file.bin"
        )


def _install(monkeypatch: MonkeyPatch, *, attachments: Sequence[object] = ()) -> None:
    FakeAccount.items = [FakeMessage(attachments)]
    FakeAccount.fetch_error = None
    FakeAccount.fetch_calls = []
    FakeAccount.last_instance = None
    monkeypatch.setattr("ews.exchange.client.Credentials", FakeCredentials)
    monkeypatch.setattr("ews.exchange.client.Configuration", FakeConfiguration)
    monkeypatch.setattr("ews.exchange.client.Account", FakeAccount)
    monkeypatch.setattr("ews.exchange.client.ItemId", FakeItemId)
    monkeypatch.setattr("ews.exchange.client.FileAttachment", FakeFileAttachment)


def _profile() -> Profile:
    return Profile.model_validate(
        {
            "server": {"endpoint": "https://mail.example.com/EWS/Exchange.asmx"},
            "user": {"mailbox": "agent@example.com", "username": "DOMAIN\\agent"},
        }
    )
