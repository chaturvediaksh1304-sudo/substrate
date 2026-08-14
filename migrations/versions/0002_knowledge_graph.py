"""knowledge graph: concepts, concept_edges

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "concepts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("normalized", sa.String(), nullable=False),
        sa.UniqueConstraint("normalized", name="uq_concepts_normalized"),
    )

    op.create_table(
        "concept_edges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_concept_id", sa.Integer(), sa.ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_concept_id", sa.Integer(), sa.ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relation", sa.String(64), nullable=False),
        sa.Column("paper_id", sa.Integer(), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "source_concept_id", "target_concept_id", "relation", "paper_id", name="uq_concept_edges_claim"
        ),
    )
    # Traversal walks outward from a concept, so source_concept_id is the hot column.
    op.create_index("ix_concept_edges_source_concept_id", "concept_edges", ["source_concept_id"])
    op.create_index("ix_concept_edges_target_concept_id", "concept_edges", ["target_concept_id"])
    op.create_index("ix_concept_edges_paper_id", "concept_edges", ["paper_id"])


def downgrade() -> None:
    op.drop_table("concept_edges")
    op.drop_table("concepts")
