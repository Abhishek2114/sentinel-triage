"""Shared FastAPI dependencies (overridden in tests)."""

import asyncio

from app.core.config import get_settings
from app.rag.embeddings import Embedder, get_embedder
from app.rag.summarizer import LLMClient, llm_from_settings
from app.realtime import RedisFactory, redis_factory


async def get_embedder_dep() -> Embedder:
    # The first call loads the model (seconds); keep it off the event loop
    return await asyncio.to_thread(get_embedder, get_settings().embedding_model)


def get_llm_dep() -> LLMClient | None:
    return llm_from_settings(get_settings())


def get_redis_factory() -> RedisFactory:
    return redis_factory(get_settings().redis_url)
