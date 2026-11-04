import pytest
from pydantic import ValidationError
from app.bootstrap.settings import Settings


def test_production_requires_identity():
    with pytest.raises(ValidationError):
        Settings(app_profile='production', oidc_issuer=None, oidc_audience=None, _env_file=None)


def test_key_is_masked():
    settings = Settings(openai_api_key='example-secret', _env_file=None)
    assert 'example-secret' not in repr(settings)
