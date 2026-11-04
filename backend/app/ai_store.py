"""Persistência da frente IA; PostgreSQL em produção e SQLite nos testes."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Column, MetaData, String, Table, create_engine, select, update


def now():
    return datetime.now(timezone.utc).isoformat()


class AIStore:
    def __init__(self, url):
        self.engine = create_engine(url, pool_pre_ping=True)
        metadata = MetaData()
        self.records = Table("ai_records", metadata,
                             Column("id", String(80), primary_key=True),
                             Column("kind", String(30), nullable=False, index=True),
                             Column("data", JSON, nullable=False))
        metadata.create_all(self.engine)

    def put(self, kind, data, record_id=None):
        record_id = record_id or str(uuid4())
        data = {**data, "id": record_id}
        with self.engine.begin() as conn:
            existing = conn.execute(select(self.records.c.id).where(self.records.c.id == record_id)).first()
            if existing:
                conn.execute(update(self.records).where(self.records.c.id == record_id).values(data=data))
            else:
                conn.execute(self.records.insert().values(id=record_id, kind=kind, data=data))
        return data

    def get(self, record_id, kind=None):
        with self.engine.connect() as conn:
            query = select(self.records.c.data).where(self.records.c.id == record_id)
            if kind:
                query = query.where(self.records.c.kind == kind)
            return conn.execute(query).scalar_one_or_none()

    def list(self, kind):
        with self.engine.connect() as conn:
            rows = conn.execute(select(self.records.c.data).where(self.records.c.kind == kind)).scalars().all()
        return sorted(rows, key=lambda row: row.get("created_at", ""), reverse=True)

    def create_job(self, kind, params, user):
        job = {"id": str(uuid4()), "kind": kind, "params": params, "user": user,
               "created_at": now(), "status": "queued", "progress": 0.0, "message": "Na fila"}
        # Job e auditoria são atômicos; o dispatcher recupera pedidos não publicados.
        audit = {"id": str(uuid4()), "created_at": now(), "user": user,
                 "type": "job", "action": kind, "target": job["id"], "ok": True}
        with self.engine.begin() as conn:
            conn.execute(self.records.insert(), [
                {"id": job["id"], "kind": "job", "data": job},
                {"id": audit["id"], "kind": "audit", "data": audit},
            ])
        return job

    def mark_published(self, record_id):
        with self.engine.begin() as conn:
            data = conn.execute(select(self.records.c.data).where(self.records.c.id == record_id).with_for_update()).scalar_one()
            data = {**data, "published": True}
            conn.execute(update(self.records).where(self.records.c.id == record_id).values(data=data))

    def close(self):
        self.engine.dispose()
