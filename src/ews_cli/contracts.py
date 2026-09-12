import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ContractModel(BaseModel):
    """Base model for the public JSON contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Error(ContractModel):
    """A machine-readable CLI error."""

    code: str
    message: str
    details: dict[str, JsonValue] = Field(default_factory=dict)
    retryable: bool = False


class SuccessEnvelope[DataT](ContractModel):
    """A successful versioned CLI response."""

    schema_version: Literal[1] = 1
    ok: Literal[True] = True
    data: DataT


class ErrorEnvelope(ContractModel):
    """A failed versioned CLI response."""

    schema_version: Literal[1] = 1
    ok: Literal[False] = False
    error: Error


def write_contract(contract: ContractModel) -> None:
    """Write one JSON contract to stdout."""
    sys.stdout.write(f"{contract.model_dump_json()}\n")
