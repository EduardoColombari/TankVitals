"""Ingestor MQTT — fecha o elo Mosquitto -> InfluxDB.

Tarefa: BE-05

Contrato: docs/ARQUITETURA.md §2.1 (tópicos) e §3 (validação)

Roda em thread própria do paho (loop_start).
"""

from __future__ import annotations

import json
import logging
from threading import Event
from typing import Callable

import paho.mqtt.client as mqtt

from app.alerts import classify_reading
from app.config import settings
from app.influx_repo import InfluxRepository
from app.models import parse_reading, tank_id_from_topic


logger = logging.getLogger(__name__)


class MqttIngestor:
    """Assina os tópicos de telemetria/status e persiste o que chega."""

    def __init__(
        self,
        on_reading: Callable[[dict], None] | None = None,
        on_status: Callable[[dict], None] | None = None,
        repository=None,
    ) -> None:
        self.on_reading = on_reading
        self.on_status = on_status
        self.repository = repository if repository is not None else InfluxRepository()

        self.last_reading: dict[str, dict] = {}
        self.online: dict[str, bool] = {}

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=settings.mqtt_client_id,
        )

        if settings.mqtt_username:
            self.client.username_pw_set(
                settings.mqtt_username,
                settings.mqtt_password or None,
            )

        self.client.reconnect_delay_set(
            min_delay=1,
            max_delay=30,
        )

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        self.client.on_connect_fail = self._on_connect_fail

    def start(self) -> None:
        """Conecta no broker local e começa a consumir."""

        logger.info(
            "Conectando ao MQTT em %s:%s",
            settings.mqtt_host,
            settings.mqtt_port,
        )

        self.client.connect_async(
            settings.mqtt_host,
            settings.mqtt_port,
            keepalive=60,
        )

        self.client.loop_start()

    def stop(self) -> None:
        """Para o loop do Paho e desconecta."""

        try:
            self.client.disconnect()
        except Exception as exc:
            logger.warning(
                "Erro ao desconectar MQTT: %s",
                exc,
            )
        finally:
            self.client.loop_stop()

    # ----------------------------------------------------------------- callbacks

    def _on_connect(
        self,
        client,
        userdata,
        flags,
        reason_code,
        properties=None,
    ) -> None:
        """Reassina os tópicos após cada conexão/reconexão."""

        if reason_code != 0:
            logger.error(
                "Falha ao conectar no MQTT: reason_code=%s",
                reason_code,
            )
            return

        telemetry_topic = (
            f"{settings.mqtt_topic_prefix}/+/telemetry"
        )

        status_topic = (
            f"{settings.mqtt_topic_prefix}/+/status"
        )

        client.subscribe(telemetry_topic, qos=0)
        client.subscribe(status_topic, qos=1)

        logger.info(
            "MQTT conectado. Assinando %s e %s",
            telemetry_topic,
            status_topic,
        )

    def _on_connect_fail(self, client, userdata) -> None:
        logger.warning("Broker MQTT indisponível; tentando reconectar.")

    def _on_disconnect(
        self, client, userdata, disconnect_flags, reason_code, properties=None,
    ) -> None:
        if reason_code != 0:
            logger.warning("Conexão MQTT perdida; tentando reconectar: %s", reason_code)

    def _on_message(
        self,
        client,
        userdata,
        msg,
    ) -> None:
        # Uma mensagem inesperada não pode encerrar a thread de consumo.
        try:
            self._handle_message(msg)
        except Exception:
            logger.exception("Falha ao processar mensagem MQTT: topic=%s", msg.topic)

    def _handle_message(self, msg) -> None:
        topic = msg.topic
        prefix = f"{settings.mqtt_topic_prefix}/"
        remainder = topic.removeprefix(prefix)
        parts = remainder.split("/")
        if (
            not topic.startswith(prefix)
            or len(parts) != 2
            or not 1 <= len(parts[0]) <= 64
            or any(char in parts[0] for char in "+#\x00")
            or parts[1] not in ("telemetry", "status")
        ):
            logger.warning("Tópico MQTT inválido: %s", topic)
            return

        # -------------------------------------------------- telemetria

        if topic.endswith("/telemetry"):
            reading = parse_reading(
                topic,
                msg.payload,
            )

            if reading is None:
                logger.warning(
                    "Payload descartado: topic=%s",
                    topic,
                )
                return

            general_level, metric_levels = classify_reading(
                reading
            )

            saved = self.repository.write_reading(
                reading
            )

            if not saved:
                logger.warning(
                    "Leitura válida, mas não gravada: tank_id=%s",
                    reading.tank_id,
                )
                return

            reading_dict = reading.model_dump(
                mode="json"
            )

            reading_dict["alert"] = general_level.value

            reading_dict["alerts"] = {
                metric: level.value
                for metric, level in metric_levels.items()
            }

            self.last_reading[
                reading.tank_id
            ] = reading_dict

            logger.info(
                "Leitura gravada: tank_id=%s "
                "temp=%s ph=%s nivel=%s distancia=%s turbidez=%s alerta=%s",
                reading.tank_id,
                reading.temperature_c,
                reading.ph,
                reading.level_pct,
                reading.distance_cm,
                reading.turbidity_ntu,
                general_level.value,
            )

            if self.on_reading is not None:
                try:
                    self.on_reading(
                        reading_dict
                    )
                except Exception as exc:
                    logger.warning(
                        "Erro no callback on_reading: %s",
                        exc,
                    )

            return

        # ------------------------------------------------------ status

        if topic.endswith("/status"):
            tank_id = tank_id_from_topic(
                topic
            )

            if tank_id is None:
                logger.warning(
                    "Tópico de status inválido: %s",
                    topic,
                )
                return

            try:
                raw = msg.payload.decode(
                    "utf-8"
                ).strip()
            except UnicodeDecodeError:
                logger.warning(
                    "Status inválido para tank_id=%s",
                    tank_id,
                )
                return

            # Suporta tanto:
            # online
            # offline
            #
            # quanto:
            # {"online": true}

            is_online: bool | None = None

            lowered = raw.lower()

            if lowered == "online":
                is_online = True

            elif lowered == "offline":
                is_online = False

            else:
                try:
                    data = json.loads(raw)

                    if (
                        isinstance(data, dict)
                        and isinstance(data.get("online"), bool)
                    ):
                        is_online = data["online"]

                except json.JSONDecodeError:
                    pass

            if is_online is None:
                logger.warning(
                    "Payload de status inválido: tank_id=%s payload=%r",
                    tank_id,
                    raw,
                )
                return

            self.online[tank_id] = is_online
            if self.on_status is not None:
                try:
                    self.on_status({"tank_id": tank_id, "online": is_online})
                except Exception:
                    logger.exception("Erro no callback on_status")

            logger.info(
                "Status atualizado: tank_id=%s online=%s",
                tank_id,
                is_online,
            )

            return

        logger.warning(
            "Tópico MQTT ignorado: %s",
            topic,
        )

    def publish_command(
        self,
        tank_id: str,
        command: dict,
    ) -> None:
        """Publica em <prefixo>/<tank_id>/cmd."""

        topic = (
            f"{settings.mqtt_topic_prefix}/"
            f"{tank_id}/cmd"
        )

        payload = json.dumps(
            command,
            ensure_ascii=False,
        )

        result = self.client.publish(
            topic,
            payload,
        )

        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.warning(
                "Falha ao publicar comando MQTT: tank_id=%s rc=%s",
                tank_id,
                result.rc,
            )


def main() -> None:
    """Executa a BE-05 sozinha enquanto a integração da API não está pronta."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ingestor = MqttIngestor()
    try:
        ingestor.start()
        Event().wait()
    except KeyboardInterrupt:
        logger.info("Encerrando ingestor MQTT.")
    finally:
        ingestor.stop()
        ingestor.repository.close()


if __name__ == "__main__":
    main()
