from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str  # required: without it we would silently write nowhere
    SEMANTIC_SCHOLAR_API_KEY: str | None = None
    EMBEDDING_DIM: int = 384
    # Optional at startup, not at use: synthesis raises loudly without it, but /health
    # and /ingest stay up so a missing key can't take down ingestion.
    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_MODEL: str = "claude-sonnet-5"
    # Which LLM answers. Anything else is malformed config, so pydantic fails at startup.
    LLM_PROVIDER: Literal["anthropic", "ollama"] = "anthropic"
    # Right when the API runs on the host. From inside the api container localhost is the
    # container, so docker-compose.yml overrides this with host.docker.internal.
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:7b-instruct"


settings = Settings()
