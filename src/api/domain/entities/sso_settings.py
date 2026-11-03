from typing import Literal, Optional

from controlplane_db.models import SSOSettings

SSOProvider = Literal[
    "google",
    "github",
    "discord",
    "microsoft",
    "linkedin",
    "x",
    "notion",
    "gitlab",
    "bitbucket",
    "facebook",
]

__all__ = ["SSOSettings", "SSOProvider"]
