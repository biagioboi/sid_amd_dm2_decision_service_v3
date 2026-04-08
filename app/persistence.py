from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import json

from sqlalchemy import DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.types import TypeDecorator


class JsonText(TypeDecorator):
    """Portable JSON storage.

    Uses TEXT with JSON (de)serialization so SQLite and Postgres behave consistently
    without requiring native JSON support.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):  # type: ignore[override]
        if value is None:
            return "{}"
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def process_result_value(self, value, dialect):  # type: ignore[override]
        if value is None or value == "":
            return {}
        try:
            return json.loads(value)
        except Exception:
            return {}


def _env(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v is not None and v != "" else default


# Default to SQLite for local runs and test simplicity.
# In docker-compose we set DATABASE_URL to a PostgreSQL DSN.
DATABASE_URL = _env("DATABASE_URL", "sqlite:///./data/decision_service.db")


class Base(DeclarativeBase):
    pass


class Evaluation(Base):
    __tablename__ = "evaluations"

    evaluation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    patient_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    guideline_version: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    request_json: Mapped[dict] = mapped_column(JsonText(), nullable=False)
    result_json: Mapped[dict] = mapped_column(JsonText(), nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evaluation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail_json: Mapped[dict] = mapped_column(JsonText(), nullable=False)


engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def init_db() -> None:
    # Ensure local SQLite directory exists.
    if DATABASE_URL.startswith("sqlite"):
        # sqlite:///./data/file.db OR sqlite:////abs/path
        try:
            path_part = DATABASE_URL.split("sqlite:///", 1)[1]
            if path_part.startswith("./"):
                db_file = (os.getcwd() + "/" + path_part[2:]).replace("//", "/")
            else:
                db_file = path_part
            os.makedirs(os.path.dirname(db_file), exist_ok=True)
        except Exception:
            pass
    # Create tables if missing (reference implementation). Use Alembic in production.
    Base.metadata.create_all(engine)


def save_evaluation(*, result: Dict[str, Any], request_payload: Dict[str, Any]) -> None:
    audit = result.get("audit", {})
    evaluation_id = result["evaluationId"]
    patient_id = request_payload.get("patient", {}).get("patientId", "unknown")
    created_at = audit.get("generatedAt")
    if isinstance(created_at, str) and created_at:
        try:
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            created_dt = datetime.now(timezone.utc)
    else:
        created_dt = datetime.now(timezone.utc)

    with Session(engine) as session:
        ev = Evaluation(
            evaluation_id=evaluation_id,
            patient_id=patient_id,
            status=result.get("status", "unknown"),
            guideline_version=audit.get("guidelineVersionUsed", "unknown"),
            decision_model_version=audit.get("decisionModelVersion", "unknown"),
            trace_id=audit.get("traceId", ""),
            created_at=created_dt,
            request_json=request_payload,
            result_json=result,
        )
        session.merge(ev)
        session.add(
            AuditEvent(
                evaluation_id=evaluation_id,
                event_type="decision_evaluated",
                created_at=created_dt,
                detail_json={
                    "status": result.get("status"),
                    "recommendationCount": len(result.get("recommendations", [])),
                    "appliedRules": [r.get("ruleId") for r in result.get("appliedRules", [])],
                },
            )
        )
        session.commit()


def get_evaluation(evaluation_id: str) -> Optional[Dict[str, Any]]:
    with Session(engine) as session:
        row = session.get(Evaluation, evaluation_id)
        return None if row is None else row.result_json


def list_audit_events(evaluation_id: str) -> List[Dict[str, Any]]:
    with Session(engine) as session:
        rows = session.execute(
            select(AuditEvent).where(AuditEvent.evaluation_id == evaluation_id).order_by(AuditEvent.id.asc())
        ).scalars().all()
        return [
            {
                "eventType": r.event_type,
                "createdAt": r.created_at.isoformat(),
                "detail": r.detail_json,
            }
            for r in rows
        ]
