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


class MessageResponse(BaseModel):
    message: str
