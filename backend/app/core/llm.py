from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.core.config import settings


@lru_cache(maxsize=8)
def get_chat_model(
    model_name: str | None = None,
    temperature: float = 0.0,
) -> ChatOpenAI:
    """Return a shared, configured LangChain OpenAI chat model.

    The cache is keyed by model name and temperature so chains can share the
    underlying HTTP client without preventing a future workflow from choosing
    a different deterministic configuration.
    """
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    return ChatOpenAI(
        model=model_name or settings.llm_model,
        api_key=settings.openai_api_key,
        temperature=temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


def clear_chat_model_cache() -> None:
    """Clear cached clients, primarily for settings overrides in tests."""
    get_chat_model.cache_clear()
