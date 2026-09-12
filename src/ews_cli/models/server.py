from pydantic import BaseModel, ConfigDict, HttpUrl, field_validator


class Server(BaseModel):
    """An Exchange Web Services endpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    endpoint: HttpUrl

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, endpoint: HttpUrl) -> HttpUrl:
        if endpoint.scheme != "https":
            raise ValueError("EWS endpoint must use HTTPS")
        if endpoint.username is not None or endpoint.password is not None:
            raise ValueError("EWS endpoint must not contain credentials")
        if endpoint.query is not None or endpoint.fragment is not None:
            raise ValueError("EWS endpoint must not contain a query or fragment")
        if endpoint.path is None or endpoint.path.rstrip("/").casefold() != "/ews/exchange.asmx":
            raise ValueError("EWS endpoint path must be /EWS/Exchange.asmx")
        return endpoint
