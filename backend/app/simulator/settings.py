"""Configuração do simulador, lida do ambiente (e do .env, se existir)."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SimulatorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # O Mosquitto fica na borda: quem conecta é o simulador, com usuário e senha (PLAT-04).
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    sim_client_id: str = "tankvitals-simulator"

    # 1 s real = 1 min simulado (padrão): um dia dura 24 min e uma falha leva uns 6 min.
    sim_speed: float = Field(default=60.0, gt=0, le=3600)
    # Semente fixa para a simulação sair igual; vazia = aleatória.
    sim_seed: int | None = None
