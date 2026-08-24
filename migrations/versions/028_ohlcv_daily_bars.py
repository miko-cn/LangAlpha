"""Durable daily OHLCV body. Redis keeps the live head; Postgres the rest.

One current series per ``(instrument_key, schema)``. Bars are keyed by
publisher+revision so a qfq correction or publisher switch can replace the
body without blending treatments. Daily-only (``ohlcv-1d``) — minute bars
stay Redis.

Revision ID: 028
Revises: 027
"""

from alembic import op


revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv_series (
            instrument_key TEXT NOT NULL,
            schema TEXT NOT NULL,
            publisher TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 0,
            price_treatment TEXT NOT NULL,
            watermark BIGINT NOT NULL,
            truncated BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (instrument_key, schema)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv_bars (
            instrument_key TEXT NOT NULL,
            schema TEXT NOT NULL,
            publisher TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 0,
            ts_event BIGINT NOT NULL,
            open DOUBLE PRECISION NOT NULL,
            high DOUBLE PRECISION NOT NULL,
            low DOUBLE PRECISION NOT NULL,
            close DOUBLE PRECISION NOT NULL,
            volume BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY (instrument_key, schema, publisher, revision, ts_event)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ohlcv_bars")
    op.execute("DROP TABLE IF EXISTS ohlcv_series")
