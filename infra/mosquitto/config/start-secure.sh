#!/bin/sh
set -eu
: "${MQTT_SIMULATOR_PASSWORD:?MQTT_SIMULATOR_PASSWORD is required}"
: "${MQTT_GATEWAY_PASSWORD:?MQTT_GATEWAY_PASSWORD is required}"
umask 077
file=/run/mosquitto/passwd
printf '%s\n%s\n' "$MQTT_SIMULATOR_PASSWORD" "$MQTT_SIMULATOR_PASSWORD" | mosquitto_passwd -c "$file" simulator
printf '%s\n%s\n' "$MQTT_GATEWAY_PASSWORD" "$MQTT_GATEWAY_PASSWORD" | mosquitto_passwd "$file" gateway
unset MQTT_SIMULATOR_PASSWORD MQTT_GATEWAY_PASSWORD
exec mosquitto -c /mosquitto/config/mosquitto.conf
