from pydantic import BaseModel, EmailStr, Field


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1)
    # Minimum length enforced here → a weak password is rejected with 422
    # before any token is consumed.
    new_password: str = Field(min_length=8)


class ResetTokenValidateResponse(BaseModel):
    valid: bool
    username: str | None = None


class PrincipalResponse(BaseModel):
    """What the presented credential can actually do.

    `/auth/me` answers "which account is this" — it returns the account's own
    role. For an API key that overstates things: the effective role is capped
    below the owner's. Without this endpoint a user holding a key has no way to
    see what it is really allowed to do.
    """

    user_id: int
    username: str
    account_role: str
    effective_role: str
    project_id: int | None
    via: str


class MessageResponse(BaseModel):
    message: str
