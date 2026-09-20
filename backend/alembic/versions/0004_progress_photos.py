"""progress photos with Gemini physique analysis

Display-only estimates: nothing in this table feeds targets, rollups or
guardrails (CLAUDE.md rule 1). One photo per day; the resized JPEG lives on
the data volume, the original is never stored.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-21
"""
from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE progress_photos (
          id INTEGER PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id),
          taken_on TEXT NOT NULL,
          path TEXT NOT NULL,
          analysis_json TEXT,
          body_fat_pct REAL,
          muscularity REAL,
          progress_pct REAL,
          actor_match TEXT,
          confidence REAL,
          reference_name TEXT,
          llm_call_id INTEGER REFERENCES llm_calls(id),
          error TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE(user_id, taken_on)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS progress_photos")
