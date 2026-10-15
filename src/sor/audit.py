"""Audit log (FR-5). Insert-only table, sensitive payload encrypted, masked summary in the clear.

Defaults to SQLite for local dev. Point DATABASE_URL at the client's Postgres elsewhere.
Not built yet: hash chain, external anchor, retention job (see the private plan).
"""

import json
import logging
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine, insert, select

log = logging.getLogger("sor.audit")
metadata = MetaData()

audit_events = Table(
    "audit_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", String(32), nullable=False),
    Column("request_id", String(64), nullable=False, index=True),
    Column("actor_id", String(128), nullable=False),
    Column("patient_ref", String(64), nullable=False),
    Column("event_type", String(32), nullable=False),
    Column("states", String(64), nullable=False),
    Column("providers", String(256), nullable=False),
    Column("status", String(16), nullable=False),
    Column("latency_ms", Integer, nullable=False),
    Column("summary", Text, nullable=False),  # masked, safe to show
    Column("payload_enc", Text, nullable=False),  # full request + response, encrypted
)


def mask_name(first: str, last: str) -> str:
    return f"{first[:1]}*** {last[:1]}***".strip()


class AuditLog:
    def __init__(self, url: str, key: str, env: str = "dev"):
        if not key:
            if env != "dev":
                raise RuntimeError("AUDIT_ENC_KEY is required outside dev")
            key = Fernet.generate_key().decode()
            log.warning("AUDIT_ENC_KEY not set, using an ephemeral key (dev only, data unreadable after restart)")
        self._fernet = Fernet(key.encode())
        sqlite = url.startswith("sqlite")
        self.engine = create_engine(url, connect_args={"check_same_thread": False} if sqlite else {})
        metadata.create_all(self.engine)
        if sqlite:
            with self.engine.begin() as conn:
                for op in ("UPDATE", "DELETE"):
                    conn.exec_driver_sql(
                        f"CREATE TRIGGER IF NOT EXISTS audit_no_{op.lower()} BEFORE {op} ON audit_events "
                        "BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;"
                    )

    def append(
        self, *, request_id: str, actor_id: str, patient_ref: str, event_type: str,
        states: list[str], providers: list[str], status: str, latency_ms: int,
        summary: dict, payload: dict,
    ) -> None:
        row = dict(
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            request_id=request_id, actor_id=actor_id, patient_ref=patient_ref,
            event_type=event_type, states=",".join(states), providers=",".join(providers),
            status=status, latency_ms=latency_ms, summary=json.dumps(summary),
            payload_enc=self._fernet.encrypt(json.dumps(payload).encode()).decode(),
        )
        with self.engine.begin() as conn:
            conn.execute(insert(audit_events).values(**row))

    def recent(self, limit: int = 20) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(audit_events).order_by(audit_events.c.id.desc()).limit(limit)
            ).mappings()
            return [
                {**{k: r[k] for k in ("id", "ts", "request_id", "actor_id", "patient_ref",
                                      "event_type", "states", "providers", "status", "latency_ms")},
                 "summary": json.loads(r["summary"])}
                for r in rows
            ]

    def decrypt(self, token: str) -> dict:
        return json.loads(self._fernet.decrypt(token.encode()))
