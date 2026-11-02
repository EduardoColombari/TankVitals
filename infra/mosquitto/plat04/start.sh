#!/bin/sh
set -eu

: "${MQTT_SIMULATOR_PASSWORD:?MQTT_SIMULATOR_PASSWORD is required}"
: "${MQTT_GATEWAY_PASSWORD:?MQTT_GATEWAY_PASSWORD is required}"

# Keep plaintext passwords out of command arguments and logs. The generated
# password file lives only in this container's runtime tmpfs.
umask 077
password_file=/run/mosquitto/passwd
printf '%s\n%s\n' "$MQTT_SIMULATOR_PASSWORD" "$MQTT_SIMULATOR_PASSWORD" \
  | mosquitto_passwd -c "$password_file" simulator
printf '%s\n%s\n' "$MQTT_GATEWAY_PASSWORD" "$MQTT_GATEWAY_PASSWORD" \
  | mosquitto_passwd "$password_file" gateway
chown 1883:1883 "$password_file"
unset MQTT_SIMULATOR_PASSWORD MQTT_GATEWAY_PASSWORD

exec mosquitto -c /mosquitto/config/mosquitto.conf
