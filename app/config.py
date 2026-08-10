from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str  # required: without it we would silently write nowhere
    SEMANTIC_SCHOLAR_API_KEY: str | None = None
    EMBEDDING_DIM: int = 384
    # Optional at startup, not at use: synthesis raises loudly without it, but /health
    # and /ingest stay up so a missing key can't take down ingestion.
    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_MODEL: str = "claude-sonnet-5"


settings = Settings()
