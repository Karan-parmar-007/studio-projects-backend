from karan_sso import SsoSettings
from pydantic_settings import BaseSettings, SettingsConfigDict

_base_config = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    env_ignore_empty=True,
    extra="ignore",
)


class DatabaseSettings(BaseSettings):
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5433
    POSTGRES_USER: str = "karan"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = "portfolio"
    POSTGRES_SCHEMA: str = "studio"
    POSTGRES_ECHO: bool = False

    model_config = _base_config

    @property
    def postgres_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


class AuthSettings(BaseSettings):
    ENVIRONMENT: str = "development"
    SSO_BASE_URL: str = "http://127.0.0.1:8002/api"
    SSO_ISSUER: str = ""
    SSO_AUDIENCE: str = "karanparmar-apps"
    SSO_REQUIRE_AUDIENCE: bool = True
    SSO_APP_ID: str = "studio"
    SSO_JWT_SECRET: str = ""
    MODEL_TIMEOUT_SECONDS: float = 180
    FLOW_TOKEN: str = ""
    # Bearer token the release workflow sends to upload app binaries. Empty = uploads disabled.
    STUDIO_RELEASE_TOKEN: str = ""
    SPEECH_LOCAL_URL: str = "http://127.0.0.1:8003"
    LLM_LOCAL_URL: str = "http://127.0.0.1:8003"
    LLM_REMOTE_URL: str = ""

    model_config = _base_config

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.strip().lower() == "production"

    def sso_settings(self) -> SsoSettings:
        return SsoSettings(
            SSO_BASE_URL=self.SSO_BASE_URL,
            SSO_ISSUER=self.SSO_ISSUER,
            SSO_AUDIENCE=self.SSO_AUDIENCE,
            SSO_REQUIRE_AUDIENCE=self.SSO_REQUIRE_AUDIENCE,
            SSO_JWT_SECRET=self.SSO_JWT_SECRET,
            SSO_APP_ID=self.SSO_APP_ID,
        )


_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")


class CorsSettings(BaseSettings):
    ENVIRONMENT: str = "development"
    CORS_ALLOWED_ORIGINS: str = (
        "http://100.90.184.17:5174,http://localhost:5174,https://studio.karanparmar.in"
    )

    model_config = _base_config

    @property
    def allowed_origins(self) -> list[str]:
        origins = [
            origin.strip().rstrip("/")
            for origin in (self.CORS_ALLOWED_ORIGINS or "").split(",")
            if origin.strip() and origin.strip() != "*"
        ]
        if self.ENVIRONMENT.strip().lower() == "production":
            origins = [o for o in origins if not any(f"://{host}" in o for host in _LOCAL_HOSTS)]
        return origins


class GarageSettings(BaseSettings):
    """S3-compatible Garage object storage (path-style, SigV4). Holds the app binaries."""

    GARAGE_ENDPOINT_URL: str = ""
    GARAGE_ACCESS_KEY: str = ""
    GARAGE_SECRET_KEY: str = ""
    GARAGE_BUCKET_NAME: str = "studio-media"
    GARAGE_REGION_NAME: str = "garage"

    model_config = _base_config

    @property
    def configured(self) -> bool:
        return bool(self.GARAGE_ENDPOINT_URL and self.GARAGE_ACCESS_KEY and self.GARAGE_SECRET_KEY)


db_settings = DatabaseSettings()
auth_settings = AuthSettings()
cors_settings = CorsSettings()
garage_settings = GarageSettings()
