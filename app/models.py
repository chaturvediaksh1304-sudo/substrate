from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.db import Base


class Paper(Base):
    __tablename__ = "papers"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_papers_source_external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32))  # semantic_scholar | arxiv
    external_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String)
    abstract: Mapped[str] = mapped_column(Text)
    # JSON: authors are a list; JSON keeps them a list, so neither side parses strings.
    authors: Mapped[list[str]] = mapped_column(JSON)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    # ponytail: no ivfflat/hnsw index — nothing to tune against yet. Add one
    # (and pick lists/m from real row counts) when sequential scan gets slow.
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.EMBEDDING_DIM))
