from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr
from webauthn.helpers.structs import AuthenticatorTransport

from controlplane_db.models import UserPasskey, UserPasskeyChallenge


class Credential(BaseModel):
    credential_id: str
    public_key: str
    sigin_count: int
    transports: list[AuthenticatorTransport] = []
    created_at: datetime | str
    last_used_at: Optional[datetime | str] = None


# ORM alias used by repositories
Challenges = UserPasskeyChallenge

__all__ = ["UserPasskey", "Challenges", "Credential"]
