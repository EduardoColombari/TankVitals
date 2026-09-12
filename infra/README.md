# TankVitals — Infraestrutura

Mosquitto (broker MQTT) e InfluxDB (série temporal) via Docker Compose.

Tarefas: INFRA-01..INFRA-05 em [../docs/TAREFAS.md](../docs/TAREFAS.md).
Contratos: [../docs/ARQUITETURA.md](../docs/ARQUITETURA.md) §4, §7, §8.

---

## Subir e derrubar

```bash
cd infra

docker compose up -d          # sobe os dois serviços
docker compose ps             # confere se estão running/healthy
docker compose logs -f        # acompanha os logs (Ctrl+C sai do log, não para nada)
docker compose logs -f mosquitto

docker compose down           # para os containers, mantém os dados
docker compose down -v        # para e APAGA os dados (recomeça do zero)
```

| Serviço | Endereço | Para quê |
| --- | --- | --- |
| Mosquitto | `localhost:1883` | MQTT |
| Mosquitto (WebSocket) | `localhost:9001` | reserva do 2º bimestre |
| InfluxDB | <http://localhost:8086> | interface e API do banco |

---

## Credenciais

Criadas pelo bloco de setup automático do `docker-compose.yml` (INFRA-01):
organização `unifacef`, bucket `tankvitals`, retenção de 30 dias.

O token usado pelo backend é gerado na interface do InfluxDB
(**Load Data → API Tokens**), com permissão de leitura e escrita **apenas no
bucket `tankvitals`**, e vai para o `.env` da raiz (INFRA-02).

Pela API, sem abrir a interface (`$ADMIN` é o `DOCKER_INFLUXDB_INIT_ADMIN_TOKEN`):

```bash
BUCKET_ID=$(curl -s "http://localhost:8086/api/v2/buckets?name=tankvitals" \
  -H "Authorization: Token $ADMIN" | jq -r '.buckets[0].id')
ORG_ID=$(curl -s "http://localhost:8086/api/v2/orgs?org=unifacef" \
  -H "Authorization: Token $ADMIN" | jq -r '.orgs[0].id')

curl -s -XPOST "http://localhost:8086/api/v2/authorizations" \
  -H "Authorization: Token $ADMIN" -H "Content-Type: application/json" -d "{
    \"orgID\": \"$ORG_ID\",
    \"description\": \"tankvitals-backend\",
    \"permissions\": [
      {\"action\":\"read\", \"resource\":{\"type\":\"buckets\",\"id\":\"$BUCKET_ID\",\"orgID\":\"$ORG_ID\"}},
      {\"action\":\"write\",\"resource\":{\"type\":\"buckets\",\"id\":\"$BUCKET_ID\",\"orgID\":\"$ORG_ID\"}}
    ]
  }" | jq -r '.token'
```

Conferindo que o escopo é mesmo restrito — o token do backend precisa **falhar**
nas duas últimas:

| Ação com o token do backend | Esperado | Obtido |
| --- | --- | --- |
| escrever no bucket `tankvitals` | 204 | ✅ 204 |
| consultar com Flux | 200 | ✅ 200 |
| criar outro bucket | negado | ✅ 401 |
| listar tokens da organização | nada visível | ✅ 200 com lista vazia (o admin vê 2) |

> Não use o token de admin no backend. Se ele vazar no GitHub, vaza o banco
> inteiro. E se qualquer token vazar em commit, gere um novo — apagar o arquivo
> depois não tira o segredo do histórico do Git.

---

## Como validar (INFRA-05)

Testar o broker sem depender do ESP32, com dois terminais:

```bash
# terminal A — escuta o broker LOCAL
mosquitto_sub -h localhost -t 'tankvitals-unifacef-g3/#' -v

# terminal B — publica imitando o dispositivo
mosquitto_pub -h localhost -t 'tankvitals-unifacef-g3/tanque-01/telemetry' \
  -m '{"device_id":"teste","tank_id":"tanque-01","temperature_c":25.5}'
```

Para validar a **bridge** (INFRA-04), publique no broker **público** e confirme
que a mensagem aparece no terminal A:

```bash
mosquitto_pub -h test.mosquitto.org -t 'tankvitals-unifacef-g3/tanque-01/telemetry' \
  -m '{"device_id":"teste","tank_id":"tanque-01","temperature_c":25.5}'
```

Teste de escrita direta no InfluxDB (INFRA-02) — deve responder **HTTP 204**:

```bash
curl -i -XPOST "http://localhost:8086/api/v2/write?org=unifacef&bucket=tankvitals&precision=s" \
  -H "Authorization: Token $INFLUX_TOKEN" \
  --data-raw "water_reading,tank_id=teste temperature_c=25.5"
```

### Saída real (01/09/2026)

`docker compose ps`:

```
NAME                   STATUS
tankvitals-influxdb    Up 16 minutes (healthy)
tankvitals-mosquitto   Up 16 minutes
```

Escrita no InfluxDB:

```
HTTP/1.1 204 No Content
X-Influxdb-Build: OSS
X-Influxdb-Version: v2.7.12
```

Pub/sub no Mosquitto — o `mosquitto_sub` recebeu o que o `mosquitto_pub` enviou:

```
tankvitals-unifacef-g3/tanque-01/telemetry {"device_id":"esp32-tank-01","tank_id":"tanque-01","temperature_c":26.4}
```

Bucket conferido: `tankvitals`, org `unifacef`, `retentionRules[0].everySeconds
= 2592000` (30 dias).

> Falta ainda a parte que depende do dispositivo: telemetria a cada ~5 s vinda
> do Wokwi e o `offline` do Last Will ao parar a simulação (precisa da FW-03).

---

## Como o ESP32 chega no broker (INFRA-04)

O ESP32 do Wokwi roda na nuvem e não enxerga o `localhost` de ninguém. Ele
publica no `test.mosquitto.org` (que também é Mosquitto) e o Mosquitto local
importa o tópico por *bridge* — o backend continua falando só com um Mosquitto,
e nenhuma máquina da equipe precisa de porta aberta.

A bridge **sobe junto com o container**, já configurada com o prefixo do grupo.

**Testado em 01/09/2026** — a bridge conectou e a mensagem atravessou:

```
# log do mosquitto local
Bridge support available.
Connecting bridge wokwi-bridge (test.mosquitto.org:1883)

# publicado no broker PUBLICO
mosquitto_pub -h test.mosquitto.org -t 'tankvitals-unifacef-g3/tanque-01/telemetry' \
  -m '{"device_id":"teste-bridge","tank_id":"tanque-01","temperature_c":25.5}'

# recebido no broker LOCAL
tankvitals-unifacef-g3/tanque-01/telemetry {"device_id":"teste-bridge","tank_id":"tanque-01","temperature_c":25.5}
```

Sem laço de reconexão no log — o `try_private false` é o que evita isso.

### O prefixo precisa bater em três lugares

| Onde | Chave |
| --- | --- |
| `firmware/wokwi/sketch.ino` | `TOPIC_PREFIX` |
| `.env` da raiz | `MQTT_TOPIC_PREFIX` |
| `infra/mosquitto/config/mosquitto.conf` | linha `topic` da bridge |

Se um dos três divergir, a mensagem sai do ESP32 e some sem erro nenhum — é o
sintoma mais chato de diagnosticar aqui.

**Último recurso:** `ngrok tcp 1883` expõe o Mosquitto local, mas o endereço
muda a cada execução — serve para destravar um teste, não para a apresentação.

---

## Problemas comuns

| Sintoma | Causa | Solução |
| --- | --- | --- |
| `Connection refused` no 1883 | Mosquitto 2.x sem `listener`/`allow_anonymous` | conferir o `mosquitto.conf` (INFRA-01) |
| Bridge reconectando em laço | falta `try_private false` | o broker público não é seu |
| Mensagem de outro grupo no tópico | prefixo genérico no broker público | conferir o prefixo nos três lugares (INFRA-04) |
| ESP32 publica mas nada chega no backend | prefixo diferente entre sketch, bridge e `.env` | alinhar os três |
| `401 unauthorized` do InfluxDB | token sem escopo no bucket, ou org errada | refazer o token (INFRA-02) |
| Dados sumiram depois do restart | subiu com `down -v` | `-v` apaga os volumes |
| Porta 1883 ocupada | Mosquitto instalado direto no Windows | parar o serviço local ou trocar a porta publicada |
