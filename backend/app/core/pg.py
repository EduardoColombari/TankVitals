"""Acesso ao PostgreSQL pelas tabelas do contrato (docs/ARQUITETURA.md §16.1).

A BACK-02 é quem cria o schema (SQLAlchemy + Alembic). Os serviços da IOT só
precisam ler cadastro e regras e gravar comandos, alertas e auditoria, então
usam **reflexão**: pegam as tabelas como elas estão no banco, sem importar os
modelos da BACK. Assim os dois lados não ficam presos um ao outro: vale o
contrato de nomes de tabela e coluna da §16.1, e os tipos (texto, JSON, uuid)
ficam por conta de quem criou.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from sqlalchemy import JSON, Engine, MetaData, Table, create_engine
from sqlalchemy.engine import URL
from sqlalchemy.exc import NoSuchTableError, OperationalError

logger = logging.getLogger(__name__)


def build_url(*, host: str, port: int, user: str, password: str, database: str) -> URL:
    """URL do SQLAlchemy com o driver psycopg 3. O `URL.create` cuida dos caracteres especiais da senha."""
    return URL.create("postgresql+psycopg", username=user, password=password, host=host, port=port, database=database)


class Database:
    """Engine síncrona (chame pelos serviços com `asyncio.to_thread`) e tabelas refletidas."""

    def __init__(self, url: str | URL | None = None, *, engine: Engine | None = None):
        if engine is None:
            if url is None:
                raise ValueError("passe a url ou a engine")
            engine = create_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=2)
        self.engine = engine
        self._metadata = MetaData()
        self._tables: dict[str, Table] = {}

    def table(self, name: str, *, wait_s: float = 0.0) -> Table:
        """A tabela refletida (guardada depois da primeira vez).

        `wait_s` espera o `migrate` terminar, se o serviço subiu antes dele.
        """
        if name in self._tables:
            return self._tables[name]
        deadline = time.monotonic() + wait_s
        while True:
            try:
                self._tables[name] = Table(name, self._metadata, autoload_with=self.engine)
                return self._tables[name]
            except (NoSuchTableError, OperationalError) as err:
                if time.monotonic() >= deadline:
                    raise
                logger.warning("tabela %s ainda não existe (%s); esperando a migração", name, type(err).__name__)
                time.sleep(2)

    def dispose(self) -> None:
        self.engine.dispose()


def coerce(table: Table, column: str, value: Any) -> Any:
    """Ajusta um valor Python ao tipo real da coluna.

    A §16.1 não fixa se `commands.value` e `audit.detail` são texto ou JSON.
    Em coluna JSON vai o objeto; em coluna de texto vai o JSON serializado.
    """
    if value is None:
        return None
    if isinstance(table.c[column].type, JSON):
        return value
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
