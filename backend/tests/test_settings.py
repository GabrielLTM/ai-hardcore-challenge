import pytest
from pydantic import ValidationError
from app.bootstrap.settings import Settings


def test_production_requires_identity():
    with pytest.raises(ValidationError):
        Settings(app_profile='production', oidc_issuer=None, oidc_audience=None, _env_file=None)


def test_key_is_masked():
    settings = Settings(openai_api_key='example-secret', _env_file=None)
    assert 'example-secret' not in repr(settings)


def test_attendant_settings_have_demo_defaults_and_a_distinct_oidc_role():
    from app.bootstrap.settings import Settings
    settings = Settings(_env_file=None)
    assert settings.operator_demo_password.get_secret_value() == 'atendente-demo' and settings.operator_session_secret is None
    assert settings.oidc_attendant_role == 'attendant' != settings.oidc_operator_role
