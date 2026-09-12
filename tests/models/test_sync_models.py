import pytest
from pydantic import ValidationError

from ews_cli.models import FolderChange, MessageChange


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "create", "folder_id": "id"},
        {
            "kind": "delete",
            "folder_id": "id",
            "folder": {
                "id": "id",
                "parent_id": None,
                "name": "Inbox",
                "well_known_name": "inbox",
                "total_count": 0,
                "unread_count": 0,
            },
        },
    ],
)
def test_folder_change_requires_payload_matching_kind(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        FolderChange.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "update", "message_id": "id"},
        {"kind": "read_flag_change", "message_id": "id"},
    ],
)
def test_message_change_requires_kind_specific_fields(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MessageChange.model_validate(payload)
