# TankVitals — Arquitetura e contratos técnicos

Este documento é a **fonte da verdade** para quem vai implementar. Toda tarefa
em [TAREFAS.md](TAREFAS.md) referencia uma seção daqui.

Regra de ouro: **ninguém inventa nome de campo, tópico ou endpoint.** Se
precisar mudar algo, muda aqui primeiro e avisa a equipe.

> **2º bimestre:** as seções 1 a 10 descrevem o sistema do 1º bimestre (ESP32
> no Wokwi, ingestor e API num processo só). Elas continuam valendo para o
> código atual do `backend/` até a BACK-01. Os contratos do 2º bimestre começam
> na **§11** e vão até a §17.

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
5. `ts` ausente, ≤ 1700000000 ou a mais de 60 s do relógio do servidor → usa
   o horário do servidor no momento do recebimento. O desvio acontece de
   verdade no Wokwi: o relógio do ESP32 anda no ritmo da simulação, 3 a 7
   vezes mais lento, e o `ts` vai ficando no passado.
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

### Formatos auxiliares

`GET /api/health` devolve `{"status":"ok","mqtt":true,"influxdb":true}`.
Se qualquer dependência estiver indisponível, devolve HTTP 503, `status` igual
a `degraded`, os booleanos correspondentes e uma mensagem em `detail`.

`GET /api/thresholds` devolve um objeto por grandeza, contendo `ok_min`,
`ok_max`, `crit_min` e `crit_max` da §5. Para nível, limites superiores são
`null` (não há alerta por nível alto). Em turbidez, `ok_max` é exclusivo:
40 NTU já é atenção.

`GET /api/tanks` devolve uma lista de objetos com `tank_id`, `last_seen`
(ISO 8601 UTC, ou `null` se só houve status) e `online` (booleano).
`count` em `/api/stats` conta leituras distintas por dispositivo e timestamp,
não o número de fields. Campos ausentes não contam como zero.

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

---

## 11. Segundo bimestre: visão e estado deste contrato

> **Estado:** §§11–17 são a proposta de arquitetura do 2º bimestre, escrita a
> partir do modo simulado do frontend (`frontend/src/domain/`,
> `frontend/src/live/envelope.ts` e `frontend/src/mock/`). As seções 1–10
> continuam descrevendo o backend atualmente implementado. O frontend confirma
> os formatos usados pelo mock; o gerador de segredos, a topologia RabbitMQ e o
> Writer têm implementações PLAT isoladas, mas isso ainda não integra o fluxo
> completo do 2º bimestre. Os contratos entre frentes aguardam revisão e
> aprovação de IOT, BACK, WEB, IA e PLAT. Até essa aprovação, esta seção é uma
> proposta, não uma especificação consolidada.

O desenho e o papel de cada peça estão em
[TAREFAS-2BIM.md › Arquitetura](TAREFAS-2BIM.md#arquitetura). Em resumo, uma
leitura sai do simulador, passa pelo Mosquitto e pelo gateway e entra no
RabbitMQ. De lá o writer grava no InfluxDB e a API empurra para o navegador
pelo WebSocket. O comando faz o caminho inverso, e a IA lê a telemetria no
RabbitMQ e devolve as previsões pelo mesmo barramento.

O que muda em relação às seções 1 a 10:

| 1º bimestre | 2º bimestre |
| --- | --- |
| o ESP32 publica um JSON por tanque | o simulador publica uma mensagem por dispositivo (§13) |
| o ingestor grava direto no InfluxDB | o gateway passa para o RabbitMQ e o writer grava (§14) |
| faixas fixas no `.env` (§5) | faixas no cadastro do PostgreSQL, editáveis pela tela (§15 e §16) |
| API sem login | JWT com três papéis e WebSocket com ticket (§17) |
| `/api/readings/*` por tanque | rotas por dispositivo e por variável (§17) |

---

## 12. Envelope único das mensagens

Toda mensagem ao vivo usa o mesmo envelope no MQTT, no RabbitMQ e no
WebSocket. Assim nenhum serviço traduz formato: cada um só lê o que precisa e
repassa.

```json
{
  "id": "0f6c1d0e-5b7a-4a43-9a52-7c3e2f1b9d10",
  "ts": "2026-11-16T13:45:00Z",
  "type": "telemetry",
  "device": "od-01",
  "payload": { "oxygen_mg_l": 6.82 }
}
```

| Campo | Regra |
| --- | --- |
| `id` | UUID que identifica a mensagem. O UUID, por si só, não garante deduplicação; o mecanismo para detectar e descartar repetições ainda precisa ser definido e implementado |
| `ts` | quando a leitura ou o evento aconteceu, em ISO 8601 UTC, e não quando chegou |
| `type` | `telemetry`, `command`, `insight`, `alert`, `job_progress`, `model_updated` ou `status` |
| `device` | id do cadastro (§15). Quando a mensagem não é de um dispositivo, vai o nome da origem: `sim` no status do simulador e `worker` no progresso de job |
| `payload` | objeto, com o formato que depende do `type` (exemplos abaixo) |

O frontend confere essa forma antes de aceitar a mensagem (`isEnvelope`, em
`frontend/src/live/envelope.ts`) e ignora o que não bater.

O validador atual do frontend verifica a forma do envelope; ele não deduplica.
O Writer também não descarta mensagens repetidas por `id`. Portanto, entrega
duplicada pode ser processada mais de uma vez até que as frentes definam e
implementem um mecanismo de idempotência.

### 12.1 Payload de cada tipo

**`telemetry` de um sensor:** um campo por variável, com a chave do cadastro.

```json
{ "oxygen_mg_l": 6.82 }
```

**`telemetry` da bomba de recirculação:** as quatro variáveis e o estado.

```json
{ "vibration_mm_s": 1.12, "current_a": 2.61, "motor_temp_c": 47.3, "flow_l_min": 48.9, "running": true }
```

**`telemetry` de um atuador:** só o estado. É a confirmação de que o comando
chegou (IOT-03); a tela só sai do "aguardando confirmação" quando ela chega.

```json
{ "on": true }
```

**`command`:** o que a API, ou o serviço `rules`, manda para o dispositivo. Os
modos de `inject_fault` são `rolamento`, `entupimento`, `superaquecimento` e
`desbalanceamento` (IOT-08).

```json
{ "command": "on", "value": true, "origin": "web", "user": "operador" }
{ "command": "inject_fault", "value": "rolamento", "origin": "web", "user": "operador" }
{ "command": "repair", "origin": "web", "user": "operador" }
{ "command": "on", "value": false, "origin": "regra", "user": null }
```

**`insight`:** a previsão da IA para um equipamento (IA-07). A vida útil
(`rul_h`) tem teto de 168 h, e no teto ela quer dizer "nenhuma falha à vista".

```json
{ "health": 72.5, "rul_h": 41.0, "model_version": "v3" }
```

**`alert`:** o alerta inteiro, igual a um item de `GET /api/alerts`, mandado
quando ele abre, quando piora e quando fecha. O `level` é `atencao` ou
`critico`. Nos alertas da IA, que vêm da previsão e não de uma faixa, `field` e
`value` são `null`.

```json
{
  "id": "a-118", "device": "bomba-rec-01", "field": "vibration_mm_s", "level": "critico",
  "message": "Vibração em nível crítico", "value": 4.71,
  "opened_at": "2026-11-16T13:40:00Z", "closed_at": null
}
```

**`job_progress`:** o andamento de um job do worker (IA-03 e IA-05). O
`status` é `queued`, `running`, `done` ou `failed`, e o `progress` vai de 0
a 1. *A IA confirma este formato.*

```json
{ "job_id": "j-42", "kind": "datagen", "status": "running", "progress": 0.35, "message": "dia 12 de 30" }
```

**`model_updated`:** um modelo novo entrou em uso (IA-05). A inferência
recarrega sem reiniciar, e a tela Treinamento destaca a versão nova.

```json
{ "model": "health", "version": "v4" }
```

**`status`:** o simulador entrou ou caiu (o Last Will da §13).

```json
{ "online": false }
```

### 12.2 O que nunca vai na telemetria

O rótulo da simulação (horas até a falha, saúde de verdade e modo de falha) só
existe nos dados do gerador da IA. Se ele aparecer na telemetria, o modelo
"cola" na prova ao vivo. A IOT-08 tem um teste automatizado conferindo isso.

---

## 13. MQTT: simulador e gateway

O Mosquitto fica na borda, dentro do compose. Só o simulador e o gateway falam
com ele, cada um com usuário e senha (PLAT-04). O prefixo de grupo do 1º
bimestre (§2) sai, porque o broker agora é nosso e não o público.

| Tópico | Publica | Assina | QoS | Retained | Conteúdo |
| --- | --- | --- | --- | --- | --- |
| `tankvitals/<zona>/<tipo>/<device>/telemetry` | simulador | gateway | 0 | não | envelope `telemetry` (§12) |
| `tankvitals/<zona>/<tipo>/<device>/cmd` | gateway | simulador | 1 | não | envelope `command` |
| `tankvitals/sim/status` | simulador (e o Last Will) | gateway | 1 | sim | envelope `status` |

- `<zona>` é a zona do cadastro (`tanque-01`), `<tipo>` é o `kind` do
  dispositivo (`sensor`, `equipment` ou `actuator`) e `<device>` é o id (§15).
  Exemplo: `tankvitals/tanque-01/sensor/od-01/telemetry`.
- Ids e zonas só têm letras minúsculas, números e hífen (é o que a tela
  Dispositivos aceita). Nunca ponto nem barra, porque os dois viram separador no
  MQTT e no RabbitMQ.
- O gateway assina com curinga: `tankvitals/+/+/+/telemetry` e
  `tankvitals/sim/status`.
- A telemetria vai com QoS 0 porque chega uma por segundo e a próxima cobre a
  perdida. O comando e o status vão com QoS 1, porque não podem se perder.
- O simulador publica uma mensagem por dispositivo a cada passo. Com
  `SIM_SPEED=60` (o padrão da IOT-06), 1 s real é 1 min simulado e um dia dura
  24 min.
- **Last Will:** o simulador registra `{"online": false}` em
  `tankvitals/sim/status` ao conectar, e logo depois publica `{"online": true}`
  retained. Se ele cair sem se despedir, o Mosquitto publica o Last Will
  sozinho. Como o broker publica o envelope pré-configurado, o `ts` do Last Will
  é o horário da conexão, não o instante exato da queda. O consumidor usa o
  horário de recebimento como momento em que observou o estado offline; esse
  horário observado não substitui o `ts` original do envelope.
- **ACL:** `simulator` publica em `tankvitals/+/+/+/telemetry` e em
  `tankvitals/sim/status` e só lê `tankvitals/+/+/+/cmd`; `gateway` faz o
  contrário. A porta 1883 fica aberta só em `127.0.0.1`.

---

## 14. RabbitMQ: o barramento interno

Toda a estrutura é declarada uma vez, no `definitions.json` carregado quando o
broker sobe (PLAT-05). Nenhum serviço declara exchange ou fila. Todas as
mensagens são o envelope da §12 em JSON (`content_type: application/json`), e
comandos e jobs vão como persistentes.

### 14.1 Exchanges e routing keys

| Exchange | Tipo | Quem publica | Routing key | Exemplo |
| --- | --- | --- | --- | --- |
| `tv.telemetry` | topic | gateway | `telemetry.<zona>.<tipo>.<device>` | `telemetry.tanque-01.sensor.od-01` |
| `tv.telemetry` | topic | gateway | `status.sim` | status do simulador |
| `tv.commands` | topic | API e `rules` | `command.<zona>.<tipo>.<device>` | `command.tanque-01.actuator.aerador-01` |
| `tv.insights` | topic | inference | `insight.<device>` | `insight.bomba-rec-01` |
| `tv.events` | topic | `rules` e inference | `alert.<device>` | alerta que abriu, piorou ou fechou |
| `tv.events` | topic | worker | `job.<kind>` | `job.datagen`, `job.train` |
| `tv.events` | topic | worker | `model.updated` | modelo novo em uso |
| `tv.jobs` | direct | API | `datagen` ou `train` | pedido de job |
| `tv.dlx` | fanout | o RabbitMQ e o gateway | (qualquer) | mensagem morta |

O gateway monta a routing key com as partes do tópico:
`tankvitals/<zona>/<tipo>/<device>/telemetry` vira
`telemetry.<zona>.<tipo>.<device>`. Não é a troca direta de `/` por `.` que a
IOT-02 sugeria, porque aí a key começaria por `tankvitals.` e terminaria em
`.telemetry`, e os bindings abaixo não bateriam.

### 14.2 Filas

| Fila | Bindings | Consumidor | Para quê |
| --- | --- | --- | --- |
| `q.writer` | `tv.telemetry` `telemetry.#` · `tv.insights` `#` | writer | grava leituras e previsões no InfluxDB (PLAT-06) |
| `q.ws` | `tv.telemetry` `#` · `tv.insights` `#` · `tv.events` `#` | API | repassa tudo ao WebSocket (BACK-04) |
| `q.rules` | `tv.telemetry` `telemetry.#` | rules | aerador automático e alertas de faixa (IOT-07) |
| `q.inference` | `tv.telemetry` `telemetry.*.equipment.*` · `tv.events` `model.updated` | inference | previsão ao vivo e troca de modelo (IA-07) |
| `q.commands` | `tv.commands` `#` | gateway | publica no `/cmd` do dispositivo (IOT-02) |
| `q.jobs.datagen` | `tv.jobs` `datagen` | worker | gera os dados sintéticos (IA-02 e IA-03) |
| `q.jobs.train` | `tv.jobs` `train` | worker | treina os modelos (IA-05) |
| `q.dlq` | `tv.dlx` | ninguém consome | guarda o que falhou; a página Mensageria lê sem tirar da fila (PLAT-07) |

- Toda fila, menos a `q.dlq`, tem `x-dead-letter-exchange: tv.dlx`. Uma
  mensagem rejeitada sem devolver (`nack` com `requeue=false`) cai na DLQ.
- O gateway publica direto em `tv.dlx` a mensagem MQTT inválida, com o corpo
  original intacto e o motivo no header `x-reason` (IOT-02).
- O ack sempre vem depois do trabalho feito: o writer confirma depois de gravar
  e o gateway depois de publicar no MQTT.

### 14.3 Usuários

Um usuário por serviço, sem `guest`. As senhas saem do `scripts/gerar_segredos.py`
(PLAT-02).

| Usuário | Lê | Escreve |
| --- | --- | --- |
| `gateway` | `q.commands` | `tv.telemetry`, `tv.dlx` |
| `writer` | `q.writer` | (nada) |
| `rules` | `q.rules` | `tv.commands`, `tv.events` |
| `inference` | `q.inference` | `tv.insights`, `tv.events` |
| `worker` | `q.jobs.datagen`, `q.jobs.train` | `tv.events` |
| `api` | `q.ws` | `tv.commands`, `tv.jobs` |

O usuário `api` tem também a tag `monitoring`, para consultar a API de
gerenciamento do RabbitMQ (`/api/queues`, `/api/overview`, `/api/consumers`) e
montar a página Mensageria. A tag `monitoring` permite consultar estatísticas,
mas não concede leitura do corpo das mensagens. A permissão `read` em `q.dlq`
é necessária somente se a API buscar a amostra prevista para a tela; ela deve
permanecer restrita a essa fila. Se a amostra for removida do contrato, retire
também essa permissão. A leitura pela API de gerenciamento com
`ack_requeue_true` recoloca a mensagem na fila; não equivale a uma consulta
passiva de métricas.

---

## 15. Cadastro inicial: ids, campos e faixas

É o seed do PostgreSQL (BACK-02) e o que o simulador publica (IOT-01). Os ids
estão no código do frontend (`SEED_DEVICES`, em
`frontend/src/mock/registry.ts`): **o seed do backend tem que bater com esta
tabela**, senão o dashboard e o 3D não reconhecem o dispositivo.

| Id | Nome | Tipo | Campo | Unidade | Casas | Faixa |
| --- | --- | --- | --- | --- | --- | --- |
| `temp-agua-01` | Termômetro da água | sensor | `temperature_c` | °C | 1 | normal de 24 a 28; crítico abaixo de 22 ou acima de 30 |
| `ph-01` | Sonda de pH | sensor | `ph` | pH | 2 | normal de 6,5 a 8; crítico abaixo de 6 ou acima de 8,5 |
| `nivel-01` | Sensor de nível | sensor | `level_pct` | % | 1 | normal a partir de 30; crítico abaixo de 15 |
| `turbidez-01` | Turbidímetro | sensor | `turbidity_ntu` | NTU | 1 | limite normal em 40 pendente de decisão; crítico acima de 60 |
| `od-01` | Sonda de oxigênio | sensor | `oxygen_mg_l` | mg/L | 2 | normal a partir de 5; crítico abaixo de 3 |
| `luz-01` | Sensor de luz | sensor | `light_lux` | lux | 0 | sem faixa: não gera alerta |
| `bomba-rec-01` | Bomba de recirculação | equipment | `vibration_mm_s` | mm/s | 2 | normal até 2,8; crítico acima de 4,5 |
| | | | `current_a` | A | 2 | normal até 3,2; crítico acima de 3,8 |
| | | | `motor_temp_c` | °C | 1 | normal até 65; crítico acima de 80 |
| | | | `flow_l_min` | L/min | 1 | normal a partir de 40; crítico abaixo de 30 |
| `aerador-01` | Aerador | actuator | (só o estado `on`) | | | controle pela regra |
| `bomba-repo-01` | Reposição de água | actuator | (só o estado `on`) | | | controle manual |

Todos estão na zona `tanque-01`. Os campos têm também um nome completo
(`label`, "Oxigênio dissolvido") e um curto (`short`, "Oxigênio"), que estão no
seed do front.

- **Estado e comandos:** a bomba de recirculação tem o estado `running` e
  aceita `on`, `inject_fault` e `repair`, com controle manual. Os atuadores têm
  o estado `on` e aceitam só `on`.
- **Como a faixa classifica**, igual no backend e no front (`classify`, em
  `frontend/src/alerts.ts`): abaixo de `crit_min` ou acima de `crit_max` é
  crítico; fora de `ok_min`–`ok_max` é atenção; o resto é normal. **Os limites
  contam como dentro**, salvo a pendência de turbidez descrita abaixo. Limite
  `null` quer dizer "sem limite daquele lado".
- **Turbidez em exatamente 40 NTU — decisão pendente da frente IoT:** o contrato
  do 1º bimestre (§5) e o classificador atual do backend tratam 40 NTU como
  atenção (`ok` abaixo de 40); o cadastro e o classificador do frontend no
  modo simulado tratam 40 NTU como normal (`ok` até 40, inclusive). As duas
  alternativas permanecem registradas; a frente IoT deve aprovar qual regra
  será usada antes de consolidar esse limite. Nenhuma alteração de código é
  feita nesta tarefa.
- **Regra do aerador** (`aerador-od`): vigia `od-01.oxygen_mg_l`, liga o
  `aerador-01` abaixo de 5 mg/L e desliga a partir de 6,5.
- **Cadastro novo pela tela:** id e zona só com letras minúsculas, números e
  hífen. O id segue a convenção prefixo + número (`ph-02`), e uma zona nova,
  como `tanque-02`, vira outro tanque no dashboard e no 3D.

---

## 16. Bancos: PostgreSQL e InfluxDB

### 16.1 PostgreSQL

O PostgreSQL guarda o que é cadastro e registro (BACK-02). As tabelas abaixo
são **proposta para a BACK**: tipos e índices ficam com quem implementa, mas
os nomes que viram JSON nas rotas (§17) têm que bater.

| Tabela | Colunas principais |
| --- | --- |
| `users` | `id`, `username` (único), `password_hash` (bcrypt), `role` (`viewer`, `operator` ou `admin`), `failed_attempts`, `locked_until` |
| `devices` | `id` (texto, chave), `name`, `kind`, `zone`, `active`, `state_field`, `commands` (lista), `control` (`manual`, `rule` ou nulo), `created_at`, `updated_at` |
| `device_fields` | `device_id`, `key` (a dupla é a chave), `label`, `short`, `unit`, `decimals`, `ok_min`, `ok_max`, `crit_min`, `crit_max` |
| `rules` | `id`, `name`, `device_id`, `field_key`, `actuator_id`, `on_below`, `off_at`, `updated_at`, `updated_by` |
| `alerts` | `id`, `device_id`, `field_key` (nulo nos da IA), `level`, `message`, `value` (nulo nos da IA), `opened_at`, `closed_at` |
| `commands` | `id`, `device_id`, `command`, `value`, `origin` (`web` ou `regra`), `username`, `created_at` |
| `audit` | `id`, `ts`, `username`, `type`, `action`, `target`, `detail`, `ok` |
| `jobs` | `id`, `kind` (`datagen` ou `train`), `params`, `status`, `progress`, `message`, `created_by`, `created_at`, `finished_at`, `result` |
| `model_versions` | `id`, `model` (`rul` ou `health`), `version`, `metrics`, `baseline`, `active`, `created_at`, `job_id` |

- **Desativar não apaga:** o dispositivo continua no banco com
  `active = false`, para o histórico e a auditoria continuarem fazendo sentido.
- **Tipos e ações da auditoria**, os mesmos que a tela mostra
  (`frontend/src/domain/audit.ts`):
  - `login`: `login`, `login_failed` e `login_blocked`;
  - `command`: `command`;
  - `registry`: `device_created`, `device_updated`, `device_deactivated` e `rule_updated`;
  - `job`: `datagen_started` e `train_started`;
  - `assistant`: `question`.

### 16.2 InfluxDB

| Bucket | Measurement | Tags | Fields |
| --- | --- | --- | --- |
| `tankvitals` | `telemetry` | `device`, `zone`, `kind` | as chaves do payload; estados (`on`, `running`) gravados como 0 e 1 |
| `tankvitals` | `insight` | `device`, `model_version` | `health`, `rul_h` |
| `lab` | `synthetic` | `pump`, `dataset` | uma amostra dos dados gerados (IA-02), separada do ao vivo |

O estado vai como 0 e 1 para dar para somar: é assim que o histórico calcula o
tempo ligado (`on_s`) e quantas vezes ligou (`turn_ons`).

---

## 17. API: rotas, login e WebSocket

O navegador só fala com a API, sempre por caminho relativo (`/api` e `/ws`): o
nginx do frontend, na porta 8080, repassa para a API, que não publica porta
(PLAT-08). Tudo é JSON, com datas em ISO 8601 UTC.

### 17.1 Login, papéis e erros

- **Login:** `POST /api/auth/login` recebe um formulário
  (`x-www-form-urlencoded`, o mesmo do "Authorize" do `/api/docs`) com
  `username` e `password` e devolve `{access_token, token_type: "bearer"}`.
- **Token:** é um JWT de 30 min (BACK-03). O front lê do token as claims
  `sub` (o usuário), `role` e `exp`, e manda o token em
  `Authorization: Bearer <token>`.
- **Papéis:** o visualizador (`viewer`) só vê. O operador (`operator`) também
  comanda os atuadores e o simulador. O admin (`admin`) também edita o
  cadastro e as regras, gera dados, treina e vê a auditoria. Quem barra é a
  API; a tela só esconde o botão.
- **Erros** vêm como `{"detail": "mensagem em português"}`, e a tela mostra o
  `detail` como está. O 422 de validação do próprio FastAPI, que vem em lista,
  também é aceito.

| Código | Quando |
| --- | --- |
| 401 | sem token ou token vencido; o front volta para o login |
| 403 | o papel não pode fazer aquilo |
| 404 | dispositivo, regra ou job que não existe |
| 409 | id repetido no cadastro |
| 422 | corpo inválido, como faixa trocada ou `off_at` menor que `on_below` |
| 429 | 6ª senha errada seguida (bloqueia 5 min) ou mais de 5 perguntas por minuto ao assistente |

### 17.2 WebSocket

1. Com o JWT, o front pede `POST /api/ws/ticket` e recebe `{ticket}`. O ticket
   vale 30 s e uma vez só (BACK-04).
2. Abre `WS /ws/live?ticket=...`. O JWT nunca vai na URL, porque URL acaba em
   log de proxy e no histórico do navegador.
3. Recebe um envelope da §12 por mensagem: telemetria, previsões, alertas,
   progresso de job, modelo novo e status do simulador.
4. Se a conexão cair, pede outro ticket e reconecta, esperando de 1 s até
   30 s entre as tentativas.

### 17.3 Rotas que o front já usa

O modo simulado (`frontend/src/mock/`) responde exatamente nestes formatos, e
os tipos estão em `frontend/src/domain/devices.ts` e `frontend/src/domain/audit.ts`.

| Rota | Papel | O que o front espera | Tarefa |
| --- | --- | --- | --- |
| `POST /api/auth/login` | (sem login) | formulário `username` e `password`; devolve `{access_token, token_type}` | BACK-03 |
| `POST /api/ws/ticket` | todos | `{ticket}`, de uso único | BACK-04 |
| `GET /api/devices` | todos | `[{id, name, kind, zone, active, fields, state_field, commands, control}]`, cada campo com `key, label, short, unit, decimals, range`, e `range` com `ok_min, ok_max, crit_min, crit_max`. Vêm também os desativados | BACK-05 |
| `POST /api/devices` | admin | corpo `{id, name, kind, zone, active, fields}`; a API completa `state_field`, `commands` e `control` pelo tipo e devolve o dispositivo | BACK-06 |
| `PUT /api/devices/{id}` | admin | corpo `{name, zone, active, fields}` (o id e o tipo não mudam); devolve o dispositivo | BACK-06 |
| `GET /api/rules` | todos | `[{id, name, device, field, actuator, on_below, off_at, updated_at, updated_by}]` | BACK-06 |
| `PUT /api/rules/{id}` | admin | corpo `{on_below, off_at}`, com `off_at > on_below`; devolve a regra | BACK-06 |
| `GET /api/history?range=6h` | todos | `{range, from, to, step_s, series}`; cada série com `device, field, points` (uns 360 `{t, v}`) e `stats` (`count, min, max, avg, last, out_of_range_s`; nos campos de estado, `on_s` e `turn_ons`). `&device=od-01` filtra e pode repetir | BACK-05 |
| `GET /api/insights/history?range=6h` | todos | o mesmo formato do `/api/history`, com as séries `health` e `rul_h` de cada equipamento, tiradas do measurement `insight`. `&device=bomba-rec-01` filtra | IA-09 |
| `GET /api/alerts?status=all` | todos | `[{id, device, field, level, message, value, opened_at, closed_at}]`, os mais novos primeiro. `status=open` traz só os abertos | BACK-05 |
| `GET /api/audit?page=1&page_size=25` | admin | `{items, total, page, page_size}`, os mais novos primeiro; cada item `{id, ts, user, type, action, target, detail, ok}`. `&type=login` e `&user=admin` filtram | BACK-06 |
| `POST /api/devices/{id}/command` | operator | corpo `{command: "on", value: true}` (ou `inject_fault` com o modo, ou `repair`); devolve `{id, status: "sent"}`. O estado novo chega pela telemetria | IOT-04 |

- **Períodos do histórico:** `range` aceita `1h`, `6h`, `24h` e `7d`.
- **Estatísticas sobre as leituras cruas:** elas são calculadas sobre as
  leituras de verdade, não sobre os pontos agregados do gráfico. É delas que
  saem o "tempo fora da faixa" e o "quantas vezes o aerador ligou" do dashboard.

### 17.4 Rotas das telas que ainda vão nascer

As rotas abaixo ainda não existem necessariamente no backend. Os formatos
marcados como provisórios precisam ser aprovados pelas frentes responsáveis
antes de orientar uma implementação.

| Rota | Papel | Para quê | Tarefa |
| --- | --- | --- | --- |
| `GET /api/health` | (sem login) | estado da API e das dependências, para o healthcheck do compose | PLAT-03 |
| `GET /api/messaging` | todos | métricas das filas, mensagens recentes encaminhadas pela `q.ws` e contagem/amostra da DLQ; formato provisório abaixo | PLAT-07 |
| `POST /api/lab/jobs` | admin | pede a geração de dados com os parâmetros; responde na hora com o id do job | IA-03 |
| `GET /api/lab/jobs` | todos | jobs de geração, com estado e progresso | IA-03 |
| `GET /api/lab/datasets` | todos | datasets gerados, com tamanho e parâmetros | IA-03 |
| `POST /api/models/train` | admin | dispara o treino pela fila `q.jobs.train` | IA-05 |
| `GET /api/models` | todos | versões dos modelos, com as métricas, a base de comparação e qual está em uso | IA-06 |
| `POST /api/assistant` | todos | pergunta ao assistente; a resposta vem em streaming | BACK-09 |
| `GET /api/assistant/status` | todos | se o modelo do Ollama já está carregado | BACK-09 |

#### Contrato provisório de `GET /api/messaging`

Esta rota ainda não está implementada. O exemplo abaixo propõe nomes e tipos
para revisão de BACK, WEB e PLAT; não define onde nem por quanto tempo guardar
mensagens recentes. A tarefa PLAT-07 solicita exibir as últimas 50 mensagens,
mas um buffer em memória de 50 itens não foi aprovado como solução. A origem,
a capacidade e a retenção dessas mensagens continuam pendentes.

```json
{
  "queues": [
    {
      "name": "q.writer",
      "messages_ready": 0,
      "messages_unacknowledged": 0,
      "publish_rate": 0.0,
      "deliver_rate": 0.0,
      "consumers": 1
    }
  ],
  "recent_messages": [
    {
      "routing_key": "telemetry.tanque-01.sensor.od-01",
      "message": {
        "id": "0f6c1d0e-5b7a-4a43-9a52-7c3e2f1b9d10",
        "ts": "2026-11-16T13:45:00Z",
        "type": "telemetry",
        "device": "od-01",
        "payload": { "oxygen_mg_l": 6.82 }
      }
    }
  ],
  "dlq": {
    "messages": 0,
    "sample": []
  }
}
```

Os campos de `queues` representam contagens e taxas em mensagens por segundo.
`recent_messages` é uma proposta de conteúdo, sem compromisso com um buffer
volátil ou persistente. `dlq.messages` informa a quantidade na fila e `sample`
é opcional enquanto a estratégia e a permissão de leitura não forem aprovadas.
Se o RabbitMQ estiver indisponível, a resposta `503` com
`{"detail":"RabbitMQ indisponível"}` também é uma proposta a aprovar.
