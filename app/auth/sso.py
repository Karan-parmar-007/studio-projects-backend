"""SSO, same contract as the other karanparmar apps."""

from karan_sso import SsoIdentity
from karan_sso.fastapi import AdminDep, IdentityDep, configure, get_sso, require_admin
from karan_sso.settings import SsoSettings

from app.config import auth_settings

sso_settings: SsoSettings = auth_settings.sso_settings()


def configure_sso() -> None:
    configure(sso_settings)


__all__ = ["AdminDep", "IdentityDep", "SsoIdentity", "configure_sso", "get_sso", "require_admin", "sso_settings"]
