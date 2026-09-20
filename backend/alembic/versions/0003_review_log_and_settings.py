"""review log + app settings

``review_log`` records every weekly review (changed or not) so the app can show
the engine's reasoning. ``app_settings`` is a small key/value store for
non-secret preferences (e.g. the physique reference description).

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-21
"""
from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE review_log (
          id INTEGER PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id),
          reviewed_on TEXT NOT NULL,
          assessment TEXT NOT NULL,
          rate_pct_week REAL,
          reason TEXT NOT NULL,
          proposals_json TEXT,
          rails_json TEXT,
          target_id INTEGER REFERENCES targets(id),
          triggered_by TEXT CHECK(triggered_by IN ('job','user')) NOT NULL DEFAULT 'job',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    op.execute("CREATE INDEX idx_review_log_on ON review_log(user_id, reviewed_on)")
    op.execute(
        """
        CREATE TABLE app_settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app_settings")
    op.execute("DROP TABLE IF EXISTS review_log")
