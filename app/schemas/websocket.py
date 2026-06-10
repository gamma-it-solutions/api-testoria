from pydantic import BaseModel


class ConnectionTokenResponse(BaseModel):
    token: str


class SubscriptionTokensRequest(BaseModel):
    channels: list[str]


class SubscriptionTokensResponse(BaseModel):
    tokens: dict[str, str]
