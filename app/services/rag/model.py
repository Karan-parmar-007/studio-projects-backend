"""RAG tables. One engine serves three scopes:

  chatbot  Karan's knowledge (his PDF + the portfolio), one active version
  book     the shelf: a few books anyone allowed can ask about; each live on its own
  upload   one document per user, deleted an hour after it finished processing

A new chatbot version is built beside the live one, switched to in one transaction,
and only then are the old version's rows and images deleted. A book goes live when it
is ready; the admin deletes books explicitly.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config import rag_settings
from app.db.base import Base

DIM = rag_settings.RAG_EMBED_DIM
SCHEMA = "studio"


class RagSource(Base):
    __tablename__ = "rag_sources"
    __table_args__ = (
        # Exactly one live chatbot corpus. Books are a shelf: any number can be live at once.
        Index(
            "ux_rag_sources_active_chatbot",
            "scope",
            unique=True,
            postgresql_where=text("active AND scope = 'chatbot'"),
        ),
        # One uploaded document per user.
        Index("ux_rag_sources_one_upload", "owner_id", unique=True, postgresql_where=text("scope = 'upload'")),
        Index("ix_rag_sources_expiry", "expires_at", postgresql_where=text("scope = 'upload'")),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[str | None] = mapped_column(String(64))
    # Who uploaded it (email), so the admin list is readable. Uploads only.
    owner_label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    filename: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    content_type: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    input_key: Mapped[str | None] = mapped_column(String(500))
    # queued -> processing -> ready (searchable) ; failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    stage: Mapped[str] = mapped_column(String(40), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    figure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    # Searchable from here (figures may still be getting described).
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The whole pipeline finished. Upload expiry counts from this.
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RagChunk(Base):
    __tablename__ = "rag_chunks"
    __table_args__ = (
        Index("ux_rag_chunks_source_ord", "source_id", "ord", unique=True),
        Index("ix_rag_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_rag_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "halfvec_cosine_ops"},
        ),
        Index(
            "ix_rag_chunks_graph_embedding",
            "graph_embedding",
            postgresql_using="hnsw",
            postgresql_ops={"graph_embedding": "halfvec_cosine_ops"},
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.rag_sources.id", ondelete="CASCADE"), nullable=False
    )
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="text")  # text | table | figure
    heading: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    figure_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # ords of figure chunks this passage points at ("see Figure 3")
    ref_ords: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False, default=list)
    embedding = mapped_column(HALFVEC(DIM))
    # The chunk's vector blended with its graph neighbours' (same space as queries).
    graph_embedding = mapped_column(HALFVEC(DIM))
    tsv = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(heading, '') || ' ' || content)", persisted=True),
    )


class RagFigure(Base):
    __tablename__ = "rag_figures"
    __table_args__ = (Index("ix_rag_figures_source", "source_id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.rag_sources.id", ondelete="CASCADE"), nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based, in reading order
    # The document's own name for it: "3", or "11.6.1" (shown as "Figure 11.6.1").
    ref: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    label: Mapped[str] = mapped_column(String(40), nullable=False, default="")  # classifier label
    page: Mapped[int | None] = mapped_column(Integer)
    caption: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_ord: Mapped[int | None] = mapped_column(Integer)


class RagEntity(Base):
    __tablename__ = "rag_entities"
    __table_args__ = (Index("ux_rag_entities_source_norm", "source_id", "norm", unique=True), {"schema": SCHEMA})

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.rag_sources.id", ondelete="CASCADE"), nullable=False
    )
    norm: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    df: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RagChunkEntity(Base):
    __tablename__ = "rag_chunk_entities"
    __table_args__ = (Index("ix_rag_chunk_entities_entity", "entity_id"), {"schema": SCHEMA})

    chunk_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.rag_chunks.id", ondelete="CASCADE"), primary_key=True
    )
    entity_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.rag_entities.id", ondelete="CASCADE"), primary_key=True
    )


class RagJob(Base):
    """Work for studio-worker. Claimed with FOR UPDATE SKIP LOCKED."""

    __tablename__ = "rag_jobs"
    __table_args__ = (Index("ix_rag_jobs_ready", "status", "lane", "priority", "run_after"), {"schema": SCHEMA})

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    # fast: user uploads; slow: chatbot + book (can take hours)
    lane: Mapped[str] = mapped_column(String(10), nullable=False, default="slow")
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.rag_sources.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="queued")
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=5)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class RagAnswerCache(Base):
    """Answers to repeated questions on the public corpora. Dies with its source version."""

    __tablename__ = "rag_answer_cache"
    __table_args__ = (Index("ux_rag_answer_cache_q", "source_id", "qnorm", unique=True), {"schema": SCHEMA})

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.rag_sources.id", ondelete="CASCADE"), nullable=False
    )
    qnorm: Mapped[str] = mapped_column(String(500), nullable=False)
    question_embedding = mapped_column(HALFVEC(DIM))
    answer: Mapped[dict] = mapped_column(JSONB, nullable=False)
    hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
