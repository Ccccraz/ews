import json
from typing import Literal

from pytest import CaptureFixture

from ews.contracts import ContractModel, Error, ErrorEnvelope, SuccessEnvelope, write_contract


class ExampleData(ContractModel):
    status: Literal["ready"] = "ready"


def test_success_envelope_serialization() -> None:
    response = SuccessEnvelope(data=ExampleData())

    assert response.model_dump(mode="json") == {
        "schema_version": 1,
        "ok": True,
        "data": {"status": "ready"},
    }


def test_error_envelope_serialization() -> None:
    response = ErrorEnvelope(
        error=Error(
            code="configuration_error",
            message="Configuration is invalid.",
            details={"field": "endpoint"},
        )
    )

    assert response.model_dump(mode="json") == {
        "schema_version": 1,
        "ok": False,
        "error": {
            "code": "configuration_error",
            "message": "Configuration is invalid.",
            "details": {"field": "endpoint"},
            "retryable": False,
        },
    }


def test_write_contract_outputs_one_json_object(capsys: CaptureFixture[str]) -> None:
    write_contract(SuccessEnvelope(data=ExampleData()))

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "schema_version": 1,
        "ok": True,
        "data": {"status": "ready"},
    }
    assert captured.out.endswith("\n")
    assert captured.err == ""
