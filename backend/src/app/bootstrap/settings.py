from functools import lru_cache
from typing import Literal
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ASYMMETRIC_ALGORITHMS = frozenset({'RS256', 'RS384', 'RS512', 'PS256', 'PS384', 'PS512', 'ES256', 'ES384', 'ES512'})


def csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or '').split(',') if item.strip()]


class Settings(BaseSettings):
    # Root .env serves both Compose and `uv run` from backend/; a local backend/.env wins.
    model_config = SettingsConfigDict(env_file=('../.env', '.env'), extra='ignore')
    app_profile: Literal['local-demo', 'production'] = 'local-demo'
    mongo_uri: str = 'mongodb://localhost:27017/?directConnection=true'
    mongo_database: str = 'getnet_support'
    openai_api_key: SecretStr | None = None
    openai_model: str = 'gpt-6-luna'
    embedding_model: str = 'text-embedding-3-small'
    model_provider: Literal['openai', 'fake'] = 'openai'  # fake: canned model for load tests, never for production
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_uri: str | None = None  # default: discovered at {issuer}/.well-known/openid-configuration
    oidc_algorithms: str = 'RS256'  # comma-separated, asymmetric only (never HS*/none)
    oidc_workspace_claim: str = 'workspace_id'
    oidc_allowed_workspaces: str | None = None  # comma-separated; empty: any workspace the issuer signs for
    oidc_roles_claim: str = 'roles'  # dotted path allowed, e.g. realm_access.roles
    oidc_admin_role: str = 'admin'
    oidc_operator_role: str = 'operator'
    oidc_leeway_seconds: int = Field(30, ge=0, le=300)
    oidc_jwks_ttl_seconds: float = Field(600, gt=0)
    oidc_jwks_min_refresh_seconds: float = Field(30, ge=0)  # floor between refetches forced by unknown `kid`s
    history_retention_days: int | None = Field(None, ge=1)  # conversations, messages, runs, evidence; None keeps them
    otel_exporter_otlp_endpoint: str | None = None  # OTLP/HTTP base URL (e.g. http://observability:4318); unset: telemetry stays local
    demo_user_id: str = 'cliente1988'
    demo_workspace_id: str = 'demo'

    @model_validator(mode='after')
    def production_requires_identity(self):
        if self.app_profile != 'production':
            return self
        if not (self.oidc_issuer and self.oidc_audience):
            raise ValueError('O perfil production exige OIDC_ISSUER e OIDC_AUDIENCE configurados.')
        for name, url in (('OIDC_ISSUER', self.oidc_issuer), ('OIDC_JWKS_URI', self.oidc_jwks_uri)):
            if url and not url.startswith('https://'):
                raise ValueError(f'{name} deve usar https:// em produção.')
        if self.model_provider == 'fake':
            raise ValueError('MODEL_PROVIDER=fake é só para testes de carga e não pode ser usado em produção.')
        return self

    @model_validator(mode='after')
    def algorithms_are_asymmetric(self):
        wrong = set(csv(self.oidc_algorithms)) - ASYMMETRIC_ALGORITHMS
        if wrong or not csv(self.oidc_algorithms):
            raise ValueError(f'OIDC_ALGORITHMS aceita apenas {sorted(ASYMMETRIC_ALGORITHMS)}; recebido {sorted(wrong) or "vazio"}.')
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
