"""Configuração do serviço `rules`, lida do ambiente (e do .env, se existir)."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RulesSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    rabbitmq_host: str = "localhost"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "rules"
    rabbitmq_password: str = ""
    rabbitmq_vhost: str = "/"

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "tankvitals"
    postgres_password: str = ""
    postgres_db: str = "tankvitals"

    # De quanto em quanto tempo relê regras e faixas do PostgreSQL.
    rules_reload_s: float = Field(default=10.0, gt=0)
    # Leituras seguidas dentro da faixa para fechar um alerta (evita abre-fecha no ruído).
    rules_alert_close_after: int = Field(default=30, ge=1)
    # Tempo que o atuador pode discordar do último comando antes de a regra reavaliar.
    rules_command_grace_s: float = Field(default=20.0, ge=0)
