# TankVitals — Arquitetura e contratos técnicos

Este documento é a **fonte da verdade** para quem vai implementar. Toda tarefa
em [TAREFAS.md](TAREFAS.md) referencia uma seção daqui.

Regra de ouro: **ninguém inventa nome de campo, tópico ou endpoint.** Se
precisar mudar algo, muda aqui primeiro e avisa a equipe.

---

## 1. Visão geral do fluxo

```
[1] ESP32 (Wokwi)
      |  lê 4 sensores a cada 5 s, monta JSON
      v
[2] MQTT publish  ->  tankvitals/tanque-01/telemetry
      v
[3] Mosquitto local (importa do broker publico por bridge - ver §7)
      v
[4] Ingestor Python (paho-mqtt)
      |  valida o JSON (Pydantic), descarta payload malformado
      |  avalia faixas seguras -> nível de alerta
      v
[5] InfluxDB 2.x  (measurement water_reading)
      ^
      |  Flux query
[6] API FastAPI  ->  REST (/api/...) + WebSocket (/ws/live)
      v
[7] Dashboard Vue 3 + TS + Vite  ->  Chart.js
```

Responsabilidade de cada peça:

| # | Componente | Responsabilidade única |
| --- | --- | --- |
| 1 | Firmware | ler sensores e publicar telemetria; **não** decide nada de negócio (só acende o LED local) |
| 3 | Mosquitto | transporte; sem lógica |
| 4 | Ingestor | validar, normalizar e persistir; é o único que escreve no banco |
| 5 | InfluxDB | armazenar a série temporal |
| 6 | API | ler o banco e servir o front; é o único que o front conhece |
| 7 | Frontend | apresentar; **não** fala com MQTT nem com o InfluxDB direto |

---

## 2. Contrato MQTT

### 2.1 Tópicos

O prefixo de tópico do grupo é **`tankvitals-unifacef-g3`** (`<PREFIXO>` nas
tabelas abaixo).

| Tópico | Direção | QoS | Retained | Conteúdo |
| --- | --- | --- | --- | --- |
| `<PREFIXO>/<tank_id>/telemetry` | ESP32 → backend | 0 | não | JSON de leitura (§3) |
| `<PREFIXO>/<tank_id>/status` | ESP32 → backend | 1 | **sim** | `online` / `offline` (texto puro) |
| `<PREFIXO>/<tank_id>/cmd` | backend → ESP32 | 0 | não | JSON de comando (§2.3) |

- `<tank_id>` padrão: `tanque-01`.
- O backend assina com curinga: `<PREFIXO>/+/telemetry` e `<PREFIXO>/+/status`.
- `status` usa **Last Will and Testament**: se o ESP32 cair sem avisar, o broker
  publica `offline` sozinho. É assim que o dashboard sabe que o dispositivo
  sumiu.

> ⚠️ **Por que o prefixo não é só `tankvitals`.** O ESP32 publica num broker
> público (§7), que é aberto: com prefixo genérico, qualquer um publica lixo no
> nosso tópico no meio da apresentação. O valor precisa ser idêntico em três
> lugares — `sketch.ino` (`TOPIC_PREFIX`), `.env` (`MQTT_TOPIC_PREFIX`) e a
> linha `topic` da bridge no `mosquitto.conf`. Ver INFRA-04.

### 2.2 Payload de telemetria

Exemplo real publicado pelo firmware:

```json
{
  "device_id": "esp32-tank-01",
  "tank_id": "tanque-01",
  "fw": "1.0.0",
  "seq": 42,
  "uptime_s": 210,
  "rssi": -58,
  "ts": 1756108800,
  "temperature_c": 26.44,
  "ph": 7.21,
  "level_pct": 78.5,
  "distance_cm": 13.6,
  "turbidity_ntu": 12.3
}
```

### 2.3 Payload de comando (base do 2º bimestre)

```json
{ "interval_s": 10 }
```

O firmware aceita `interval_s` entre 1 e 300; qualquer outro valor é ignorado.

---

## 3. Contrato do dado (campos)

| Campo | Tipo | Unidade | Obrigatório | Faixa válida | Observação |
| --- | --- | --- | --- | --- | --- |
| `device_id` | string | — | sim | 1–64 chars | identifica a placa |
| `tank_id` | string | — | sim | 1–64 chars | vira **tag** no InfluxDB |
| `fw` | string | — | não | — | versão do firmware |
| `seq` | int | — | não | ≥ 0 | contador; detecta perda de mensagem |
| `uptime_s` | int | s | não | ≥ 0 | tempo ligado |
| `rssi` | int | dBm | não | −100 a 0 | qualidade do Wi-Fi |
| `ts` | int | epoch s | não | > 1700000000 | só enviado após sincronizar NTP |
| `temperature_c` | float | °C | não¹ | −10 a 60 | fora disso = leitura inválida |
| `ph` | float | pH | não¹ | 0 a 14 | |
| `level_pct` | float | % | não¹ | 0 a 100 | já vem normalizado pelo firmware |
| `distance_cm` | float | cm | não | 0 a 400 | leitura crua do HC-SR04 |
| `turbidity_ntu` | float | NTU | não¹ | 0 a 1000 | |

¹ Individualmente opcionais (um sensor pode falhar e o resto continua sendo
publicado), mas **pelo menos uma das quatro grandezas** precisa estar presente,
senão o ingestor descarta a mensagem.

### Regras de validação no ingestor (BE-03)

1. Payload não é JSON válido → descarta e registra `WARNING`.
2. Falta `device_id` ou `tank_id` → descarta.
3. Grandeza fora da faixa válida da tabela → **remove só aquele campo**, mantém
   o resto (sensor com defeito não derruba a leitura inteira).
4. Nenhuma grandeza sobrou → descarta.
5. `ts` ausente ou ≤ 1700000000 → usa o horário do servidor no momento do
   recebimento.
6. `tank_id` do payload deve bater com o `<tank_id>` do tópico; se divergir,
   vale o do tópico e registra `WARNING`.

---

## 4. Contrato do InfluxDB

- **Organização:** `unifacef`
- **Bucket:** `tankvitals` — retenção **30 dias**
- **Measurement:** `water_reading`
- **Precisão de escrita:** nanossegundos (`WritePrecision.NS`)

| Elemento | Nome | Tipo |
| --- | --- | --- |
| tag | `tank_id` | string |
| tag | `device_id` | string |
| tag | `fw` | string |
| field | `temperature_c` | float |
| field | `ph` | float |
| field | `level_pct` | float |
| field | `distance_cm` | float |
| field | `turbidity_ntu` | float |
| field | `rssi` | int |
| field | `seq` | int |
| timestamp | — | do `ts` do payload, ou do servidor |

> **Por que tag e não field:** tags são indexadas e usadas em `group by`.
> `tank_id`/`device_id` são dimensões (poucos valores distintos); as leituras
> são fields. Nunca coloque um valor numérico contínuo como tag — isso explode
> a cardinalidade do banco.

### Consulta de referência (histórico agregado)

```flux
from(bucket: "tankvitals")
  |> range(start: -6h)
  |> filter(fn: (r) => r._measurement == "water_reading")
  |> filter(fn: (r) => r.tank_id == "tanque-01")
  |> filter(fn: (r) => r._field == "temperature_c" or r._field == "ph")
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> yield(name: "mean")
```

### Consulta de referência (última leitura)

```flux
from(bucket: "tankvitals")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "water_reading")
  |> filter(fn: (r) => r.tank_id == "tanque-01")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
```

---

## 5. Faixas seguras e regra de alerta

Três níveis: `ok`, `atencao`, `critico`.

| Grandeza | crítico ↓ | atenção ↓ | **ok** | atenção ↑ | crítico ↑ |
| --- | --- | --- | --- | --- | --- |
| `temperature_c` | < 22,0 | 22,0–23,9 | **24,0–28,0** | 28,1–30,0 | > 30,0 |
| `ph` | < 6,0 | 6,0–6,49 | **6,5–8,0** | 8,01–8,5 | > 8,5 |
| `level_pct` | < 15 | 15–29,9 | **≥ 30** | — | — |
| `turbidity_ntu` | — | — | **< 40** | 40–60 | > 60 |

- O **nível geral da leitura** é o pior nível entre as grandezas presentes.
- O firmware usa só a faixa `ok` (binário) para acender o LED — a classificação
  em três níveis é responsabilidade do backend.
- Os limites moram em **um lugar só** no backend (`app/config.py`) e são
  expostos ao frontend pelo endpoint `GET /api/thresholds`, para que o
  dashboard não tenha número mágico duplicado.

---

## 6. Contrato da API (backend → frontend)

Base: `http://localhost:8000`. Todas as respostas em JSON, timestamps em
**ISO 8601 UTC** (`2026-08-25T13:45:00Z`).

| Método | Rota | Descrição |
| --- | --- | --- |
| GET | `/api/health` | status do serviço, do broker e do banco |
| GET | `/api/thresholds` | faixas seguras (§5) |
| GET | `/api/tanks` | tanques conhecidos + último visto + online/offline |
| GET | `/api/readings/latest` | última leitura de um tanque, já classificada |
| GET | `/api/readings/history` | série temporal agregada (alimenta o gráfico) |
| GET | `/api/stats` | mín/máx/média por grandeza em um período |
| WS | `/ws/live` | push de cada nova leitura assim que chega do MQTT |

### `GET /api/readings/latest?tank_id=tanque-01`

```json
{
  "tank_id": "tanque-01",
  "device_id": "esp32-tank-01",
  "time": "2026-08-25T13:45:00Z",
  "age_s": 3,
  "online": true,
  "level": "atencao",
  "metrics": {
    "temperature_c": { "value": 28.6, "level": "atencao", "unit": "°C" },
    "ph":            { "value": 7.21, "level": "ok",      "unit": "pH" },
    "level_pct":     { "value": 78.5, "level": "ok",      "unit": "%" },
    "turbidity_ntu": { "value": 12.3, "level": "ok",      "unit": "NTU" }
  }
}
```

### `GET /api/readings/history`

Parâmetros:

| Param | Obrigatório | Padrão | Valores |
| --- | --- | --- | --- |
| `tank_id` | não | `tanque-01` | — |
| `range` | não | `6h` | `1h`, `6h`, `24h`, `7d` |
| `window` | não | automático | `10s`, `1m`, `5m`, `1h` |
| `metrics` | não | as 4 | lista separada por vírgula |

`window` automático por `range`: `1h→10s`, `6h→1m`, `24h→5m`, `7d→1h`
(mantém a série em ~360 pontos, que é o que o Chart.js desenha bem).

```json
{
  "tank_id": "tanque-01",
  "range": "6h",
  "window": "1m",
  "series": [
    {
      "metric": "temperature_c",
      "unit": "°C",
      "points": [
        { "t": "2026-08-25T07:45:00Z", "v": 26.1 },
        { "t": "2026-08-25T07:46:00Z", "v": 26.2 }
      ]
    }
  ]
}
```

### `GET /api/stats?tank_id=tanque-01&range=24h`

```json
{
  "tank_id": "tanque-01",
  "range": "24h",
  "count": 17280,
  "metrics": {
    "temperature_c": { "min": 24.8, "max": 29.1, "avg": 26.4, "last": 26.5 }
  }
}
```

### `WS /ws/live?tank_id=tanque-01`

Mensagens enviadas pelo servidor:

```json
{ "type": "reading", "payload": { /* mesmo formato de /readings/latest */ } }
{ "type": "status",  "payload": { "tank_id": "tanque-01", "online": false } }
```

O frontend deve tratar queda de conexão com **reconexão automática** e cair
para *polling* de `/api/readings/latest` a cada 5 s se o WebSocket falhar.

### Erros

Formato único: `{ "detail": "mensagem legível" }` com status 400 (parâmetro
inválido), 404 (tanque sem dados) ou 503 (InfluxDB indisponível).

---

## 7. Conectividade Wokwi ↔ Mosquitto

O ESP32 do Wokwi roda na nuvem e não alcança o `localhost` de ninguém. Como o
projeto não tem servidor público, o caminho é o inverso: o dispositivo publica
num broker público e o Mosquitto local **importa** o tópico por *bridge*.

### Caminho adotado — broker público com bridge

```
ESP32 (Wokwi) --publish--> test.mosquitto.org:1883 --bridge--> Mosquitto local:1883 --> backend
```

O `test.mosquitto.org` é uma instância pública do **próprio Mosquitto**. O
`mosquitto.conf` local abre a bridge e importa o tópico, então o backend
continua falando só com o broker local — a exigência da disciplina segue
cumprida, e nenhuma máquina da equipe precisa de porta aberta.

O preço é depender de um serviço de terceiros e de um prefixo de tópico único:
o broker é aberto, qualquer um publica nele. O prefixo do grupo é
`tankvitals-unifacef-g3` (§2.1).

### Emergência — túnel TCP

`ngrok tcp 1883` expõe o Mosquitto local e o host/porta gerados vão para
`MQTT_HOST`/`MQTT_PORT` no `sketch.ino`. Funciona, mas o endereço muda a cada
execução do ngrok — serve para destravar um teste, não para a apresentação.

### Descartado — broker próprio em VM

A ideia original era rodar o Mosquitto numa VM gratuita da Oracle, em
`mqtt.<dominio>:1883` com senha. Foi abandonada por custo de configuração:
exige provisionar a instância, abrir a porta nos **dois** firewalls empilhados
(Security List da VCN e `iptables` da própria instância) e manter o registro DNS
fora do proxy do Cloudflare — a nuvem laranja só encaminha HTTP/HTTPS, e pelo
mesmo motivo o Cloudflare Tunnel também não resolve para MQTT.

---

## 8. Variáveis de ambiente

Arquivo `.env` na raiz (não versionado; versionar apenas `.env.example`).

```ini
# --- MQTT ---
MQTT_HOST=localhost
MQTT_PORT=1883
MQTT_USERNAME=
MQTT_PASSWORD=
MQTT_TOPIC_PREFIX=tankvitals
MQTT_CLIENT_ID=tankvitals-backend

# --- InfluxDB ---
INFLUX_URL=http://localhost:8086
INFLUX_TOKEN=coloque-o-token-gerado-no-setup
INFLUX_ORG=unifacef
INFLUX_BUCKET=tankvitals

# --- API ---
API_HOST=0.0.0.0
API_PORT=8000
CORS_ORIGINS=http://localhost:5173

# --- Regras de negócio (faixas seguras, §5) ---
TEMP_OK_MIN=24.0
TEMP_OK_MAX=28.0
PH_OK_MIN=6.5
PH_OK_MAX=8.0
LEVEL_OK_MIN=30.0
TURBIDITY_OK_MAX=40.0
```

No frontend, `frontend/.env`:

```ini
VITE_API_BASE_URL=http://localhost:8000
```

---

## 9. Decisões de arquitetura (e o porquê)

| Decisão | Motivo |
| --- | --- |
| Ingestor e API no **mesmo processo** Python | simplifica a demonstração e permite push por WebSocket sem fila intermediária; se precisar escalar depois, separa em dois processos e coloca Redis no meio |
| Frontend **nunca** fala MQTT | o navegador precisaria de MQTT over WebSocket e de credenciais do broker; centralizar na API mantém o front burro e testável |
| InfluxDB em vez de PostgreSQL | série temporal com *downsampling* nativo (`aggregateWindow`) e retenção automática — é o requisito da disciplina e também a escolha tecnicamente certa |
| ADC1 (GPIO 32–39) no ESP32 | o ADC2 é usado pelo rádio Wi-Fi; ler ADC2 com Wi-Fi ligado retorna lixo |
| Validação com Pydantic no ingestor | dado de IoT chega sujo; a fronteira de confiança é a entrada do backend |
| `tank_id` como tag | permite vários tanques no 2º bimestre sem alterar schema |
| Retenção de 30 dias | suficiente para a defesa e evita o banco crescer sem controle |

---

## 10. Como validar cada elo da corrente

Sequência de testes usada nas tarefas (executar nesta ordem):

| Elo | Comando de verificação | Esperado |
| --- | --- | --- |
| ESP32 → MQTT | monitor serial do Wokwi | linhas `[pub] {...}` a cada 5 s |
| MQTT → Mosquitto | `mosquitto_sub -h localhost -t 'tankvitals/#' -v` | mesmas mensagens chegando |
| Mosquitto → Python | log do backend | `leitura gravada tank_id=tanque-01` |
| Python → InfluxDB | Data Explorer em `http://localhost:8086` | pontos no measurement `water_reading` |
| InfluxDB → API | `curl http://localhost:8000/api/readings/latest` | JSON do §6 |
| API → Dashboard | navegador em `http://localhost:5173` | cards e gráfico atualizando |
