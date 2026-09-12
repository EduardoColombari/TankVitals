"""Dispositivo simulado para desenvolvimento; na avaliação use o ESP32/Wokwi."""
from __future__ import annotations

import argparse
import json
import logging
import math
from pathlib import Path
import sys
import time

# Permite executar python tools/fake_device.py a partir de qualquer diretório.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


def build_payload(seq: int, anomalia: str | None) -> dict:
    phase = seq / 12
    level = round(75 + 8 * math.sin(phase / 2), 2)
    payload = {
        "device_id": "fake-esp32", "tank_id": "tanque-01", "fw": "simulator-1.0",
        "seq": seq, "uptime_s": seq * 5, "rssi": -58, "ts": int(time.time()),
        "temperature_c": round(26.5 + 2.5 * math.sin(phase), 2),
        "ph": round(7.2 + 0.3 * math.sin(phase / 3), 2),
        "level_pct": level, "distance_cm": round(45 - level * 0.4, 2),
        "turbidity_ntu": round(20 + 8 * math.sin(phase / 4), 2),
    }
    anomalies = {"temperature_c": 35.0, "ph": 5.5, "level_pct": 10.0, "turbidity_ntu": 80.0}
    if anomalia is not None:
        payload[anomalia] = anomalies[anomalia]
        if anomalia == "level_pct":
            payload["distance_cm"] = 41.0
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Publicador falso do TankVitals")
    parser.add_argument("--interval", type=float, default=5.0, help="segundos entre envios")
    parser.add_argument("--tank-id", default="tanque-01")
    parser.add_argument("--count", type=int, default=0, help="número de envios; 0 = contínuo")
    parser.add_argument("--anomalia", choices=["temperature_c", "ph", "level_pct", "turbidity_ntu"])
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("--interval deve ser positivo e finito")
    if args.count < 0:
        parser.error("--count não pode ser negativo")
    if not 1 <= len(args.tank_id.strip()) <= 64 or any(c in args.tank_id for c in "/+#\x00"):
        parser.error("--tank-id inválido")
    from app.config import settings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f"tankvitals-simulator-{args.tank_id}")
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password or None)
    prefix = f"{settings.mqtt_topic_prefix}/{args.tank_id}"
    client.will_set(f"{prefix}/status", "offline", qos=1, retain=True)
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            client.publish(f"{prefix}/status", "online", qos=1, retain=True)
        else:
            logger.warning("Conexão MQTT recusada: %s", reason_code)

    client.on_connect = on_connect
    client.on_connect_fail = lambda client, userdata: logger.warning("Broker indisponível; reconectando")
    start = time.monotonic()
    seq = 0
    try:
        client.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=60)
        client.loop_start()
        while args.count == 0 or seq < args.count:
            if not client.is_connected():
                time.sleep(0.2)
                continue
            payload = build_payload(seq, args.anomalia)
            payload.update(tank_id=args.tank_id, device_id=f"fake-{args.tank_id}"[:64],
                           uptime_s=int(time.monotonic() - start))
            result = client.publish(f"{prefix}/telemetry", json.dumps(payload), qos=0, retain=False)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                try:
                    result.wait_for_publish(timeout=5)
                    if result.is_published():
                        logger.info("Publicado seq=%s tank_id=%s anomalia=%s", seq, args.tank_id, args.anomalia)
                        seq += 1
                except RuntimeError:
                    logger.warning("Publicação interrompida; aguardando reconexão")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logger.info("Encerrando simulador")
    finally:
        try:
            if client.is_connected():
                result = client.publish(f"{prefix}/status", "offline", qos=1, retain=True)
                result.wait_for_publish(timeout=5)
        except RuntimeError:
            logger.warning("Sem conexão para publicar status final; o broker usará o Last Will")
        finally:
            client.disconnect()
            client.loop_stop()


if __name__ == "__main__":
    main()
