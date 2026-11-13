"""The public authenticated identity; session material never enters this model."""

from pydantic import BaseModel


class AuthenticatedUserResponse(BaseModel):
    steamid64: str
