import pytest

from model_gateway import ModelGateway, ModelGatewaySettings, ModelNotConfiguredError, ModelRole


def test_resolve_configured_model():
    gw = ModelGateway(ModelGatewaySettings(MODEL_FAST="some-fast-model"))
    assert gw.resolve(ModelRole.FAST) == "some-fast-model"


def test_unconfigured_model_fails_explicitly():
    gw = ModelGateway(ModelGatewaySettings(MODEL_DEEP=None))
    with pytest.raises(ModelNotConfiguredError):
        gw.resolve(ModelRole.DEEP)


def test_is_configured_requires_default_and_key():
    assert not ModelGateway(ModelGatewaySettings()).is_configured
    gw = ModelGateway(
        ModelGatewaySettings(MODEL_DEFAULT="some-model", LLM_API_KEY="sk-test")
    )
    assert gw.is_configured
