import pytest
from fastapi import HTTPException
from app.adapters.inbound.http.identity import demo_identity
from app.bootstrap.settings import Settings


def test_local_identity_is_explicit_and_rejects_other_user():
    settings = Settings(app_profile='local-demo', demo_user_id='cliente1988', _env_file=None)
    assert demo_identity(settings, 'cliente1988').is_demo
    with pytest.raises(HTTPException) as exc:
        demo_identity(settings, 'other')
    assert exc.value.status_code == 403


def test_production_does_not_fall_back_to_demo_identity():
    settings = Settings(app_profile='production', oidc_issuer='https://issuer.test', oidc_audience='app', _env_file=None)
    with pytest.raises(HTTPException) as exc:
        demo_identity(settings)
    assert exc.value.status_code == 503
