"""Configuração da aplicação — lida do .env da raiz do projeto.

Tarefa: BE-01

Contrato: docs/ARQUITETURA.md §8 (variáveis de ambiente) e §5 (faixas seguras)

Regra: nenhum valor configurável pode estar espalhado pelo código.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    """Configuração única da aplicação."""

    # -------------------------
    # MQTT
    # -------------------------
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_topic_prefix: str = "tankvitals"
    mqtt_client_id: str = "tankvitals-backend"

    # -------------------------
    # InfluxDB
    # -------------------------
    influx_url: str = "http://localhost:8086"
    influx_token: str
    influx_org: str = "unifacef"
    influx_bucket: str = "tankvitals"

    # -------------------------
    # API
    # -------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173"

    # -------------------------
    # Faixas seguras
    # -------------------------
    temp_ok_min: float = 24.0
    temp_ok_max: float = 28.0

    ph_ok_min: float = 6.5
    ph_ok_max: float = 8.0

    level_ok_min: float = 30.0

    turbidity_ok_max: float = 40.0

    # Provisórios para permitir 3 níveis
    temp_crit_min: float = 22.0
    temp_crit_max: float = 30.0

    ph_crit_min: float = 6.0
    ph_crit_max: float = 8.5

    level_ok_max: float = 90.0
    level_crit_min: float = 15.0
    level_crit_max: float = 98.0

    distance_ok_min: float = 10.0
    distance_ok_max: float = 250.0
    distance_crit_min: float = 5.0
    distance_crit_max: float = 350.0

    turbidity_ok_min: float = 0.0
    turbidity_crit_min: float = 0.0
    turbidity_crit_max: float = 60.0

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# Instância única importada pelo resto da aplicação.
settings = Settings()
