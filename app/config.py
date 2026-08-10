from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str  # required: without it we would silently write nowhere
    SEMANTIC_SCHOLAR_API_KEY: str | None = None
    EMBEDDING_DIM: int = 384


settings = Settings()
