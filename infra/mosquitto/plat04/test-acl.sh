#!/bin/sh
set -eu

host=127.0.0.1
port=1883
telemetry='tankvitals/tanque-01/sensor/od-01/telemetry'
command_topic='tankvitals/tanque-01/actuator/aerador-01/cmd'
status='tankvitals/sim/status'
work=/tmp/plat04-acl-test
mkdir -p "$work"
rm -f "$work"/*

fail() {
  printf 'FAIL: %s\n' "$1"
  exit 1
}

pass() { printf 'PASS: %s\n' "$1"; }

# Wait for the listener using an authenticated request, without printing secrets.
ready=false
for _ in $(seq 1 20); do
  if mosquitto_pub -h "$host" -p "$port" -u simulator \
      -P "$MQTT_SIMULATOR_PASSWORD" -t "$status" -m '{"online":true}' -r \
      >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
[ "$ready" = true ] || fail 'broker did not become ready with authenticated credentials'

password_mode=$(stat -c '%a' /run/mosquitto/passwd)
[ "$password_mode" = 600 ] || fail 'Mosquitto password file is not mode 0600'
if grep -Fq "$MQTT_SIMULATOR_PASSWORD" /run/mosquitto/passwd \
    || grep -Fq "$MQTT_GATEWAY_PASSWORD" /run/mosquitto/passwd; then
  fail 'plaintext password was written to the Mosquitto password file'
fi
pass 'password file is hash-only and mode 0600 in runtime tmpfs'

if mosquitto_sub -h "$host" -p "$port" -t '$SYS/broker/uptime' -C 1 -W 2 \
    >"$work/anonymous.out" 2>"$work/anonymous.err"; then
  fail 'anonymous connection was accepted'
fi
pass 'anonymous connection rejected'

# Allowed simulator publish -> gateway subscription.
mosquitto_sub -h "$host" -p "$port" -u gateway -P "$MQTT_GATEWAY_PASSWORD" \
  -t "$telemetry" -C 1 -W 5 >"$work/allowed.out" 2>"$work/allowed.err" &
subscriber=$!
sleep 1
mosquitto_pub -h "$host" -p "$port" -u simulator -P "$MQTT_SIMULATOR_PASSWORD" \
  -t "$telemetry" -m 'plat04-allowed'
if ! wait "$subscriber"; then fail 'authorized telemetry publish/subscribe failed'; fi
grep -qx 'plat04-allowed' "$work/allowed.out" || fail 'authorized message body did not arrive'
pass 'authorized simulator publish and gateway subscription'

# Exercise the other permitted direction: gateway publishes a command and the
# simulator subscribes to that exact command topic.
mosquitto_sub -h "$host" -p "$port" -u simulator -P "$MQTT_SIMULATOR_PASSWORD" \
  -t "$command_topic" -C 1 -W 5 >"$work/allowed-command.out" 2>"$work/allowed-command.err" &
subscriber=$!
sleep 1
mosquitto_pub -h "$host" -p "$port" -u gateway -P "$MQTT_GATEWAY_PASSWORD" \
  -t "$command_topic" -m 'plat04-command-allowed'
if ! wait "$subscriber"; then fail 'authorized command publish/subscribe failed'; fi
grep -qx 'plat04-command-allowed' "$work/allowed-command.out" || fail 'authorized command did not arrive'
pass 'authorized gateway command publish and simulator subscription'

# MQTT can report success to an unauthorized publisher even when the broker
# discards its message. Check delivery at the authorized receiving client.
mosquitto_sub -h "$host" -p "$port" -u gateway -P "$MQTT_GATEWAY_PASSWORD" \
  -t "$command_topic" -C 1 -W 3 >"$work/denied-publish.out" 2>"$work/denied-publish.err" &
subscriber=$!
sleep 1
mosquitto_pub -h "$host" -p "$port" -u simulator -P "$MQTT_SIMULATOR_PASSWORD" \
  -t "$command_topic" -m 'plat04-must-not-arrive' >/dev/null 2>&1 || true
if wait "$subscriber"; then fail 'unauthorized publish reached an authorized subscriber'; fi
[ ! -s "$work/denied-publish.out" ] || fail 'unauthorized publish delivered a payload'
pass 'unauthorized simulator publish dropped by broker'

# The basic Mosquitto ACL file controls message delivery but does not reject a
# SUBSCRIBE packet at SUBACK. Verify that the unauthorized client receives no
# protected payload, and retain the protocol trace as evidence of this limit.
mosquitto_sub -d -h "$host" -p "$port" -u simulator -P "$MQTT_SIMULATOR_PASSWORD" \
  -t "$telemetry" -C 1 -W 3 >"$work/denied-subscribe.out" 2>"$work/denied-subscribe.err" &
subscriber=$!
sleep 1
mosquitto_pub -h "$host" -p "$port" -u simulator -P "$MQTT_SIMULATOR_PASSWORD" \
  -t "$telemetry" -m 'plat04-subscription-probe' >/dev/null 2>&1
if wait "$subscriber"; then fail 'unauthorized subscription received telemetry'; fi
if grep -qx 'plat04-subscription-probe' "$work/denied-subscribe.out"; then
  fail 'unauthorized subscription received a protected payload'
fi
if ! grep -Eqi 'Subscribed \(mid: [0-9]+\): 0' "$work/denied-subscribe.out"; then
  fail 'expected ACL-file behavior was not observed in SUBACK diagnostics'
fi
pass 'unauthorized telemetry delivery blocked; SUBSCRIBE itself is accepted by ACL-file behavior'

pass 'ACL integration checks completed'
