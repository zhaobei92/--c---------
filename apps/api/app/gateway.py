from functools import lru_cache

from model_gateway import ModelGateway


@lru_cache
def get_model_gateway() -> ModelGateway:
    return ModelGateway()
