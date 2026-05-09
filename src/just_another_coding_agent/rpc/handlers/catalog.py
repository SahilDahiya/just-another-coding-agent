from __future__ import annotations

from collections.abc import AsyncIterator

from just_another_coding_agent.contracts.model_catalog import (
    CANONICAL_PROVIDER_ORDER,
    default_model_for_provider,
    shipped_models_for_provider,
)
from just_another_coding_agent.contracts.rpc import (
    ModelCatalogModel,
    ModelCatalogProvider,
    ModelCatalogRequest,
    ModelCatalogResponse,
    RpcResponseEnvelope,
)
from just_another_coding_agent.rpc.context import _RpcContext


async def _handle_model_catalog(
    request: ModelCatalogRequest,
    _ctx: _RpcContext,
) -> AsyncIterator[str]:
    yield RpcResponseEnvelope(
        id=request.id,
        response=ModelCatalogResponse(
            providers=[
                ModelCatalogProvider(
                    provider=provider,
                    default_model_id=default_model_for_provider(provider),
                    models=[
                        ModelCatalogModel(
                            model_id=model.model_id,
                            description=model.description,
                        )
                        for model in shipped_models_for_provider(provider)
                    ],
                )
                for provider in CANONICAL_PROVIDER_ORDER
            ]
        ),
    ).model_dump_json()
