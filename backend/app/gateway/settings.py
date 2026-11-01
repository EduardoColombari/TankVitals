"""Configuração do gateway, lida do ambiente (e do .env, se existir)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Mosquitto (usuário `gateway`, PLAT-04)
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    gateway_client_id: str = "tankvitals-gateway"

    # RabbitMQ (usuário `gateway`, PLAT-05)
    rabbitmq_host: str = "localhost"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "gateway"
    rabbitmq_password: str = ""
    rabbitmq_vhost: str = "/"
