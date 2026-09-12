# TankVitals — Backend

Ingestor MQTT + API HTTP/WebSocket, gravando e lendo do InfluxDB.

Contratos em [../docs/ARQUITETURA.md](../docs/ARQUITETURA.md).
Backlog em [../docs/TAREFAS.md](../docs/TAREFAS.md) (BE-01..BE-09).

---

## Como rodar

**Pré-requisito:** Mosquitto e InfluxDB no ar (`cd ../infra && docker compose up -d`)
e o `.env` preenchido na raiz do projeto (copie de `../.env.example`).

```bash
cd backend

# ambiente virtual (Windows)
py -m venv .venv
.venv\Scripts\activate

# Linux/Mac
# python3 -m venv .venv && source .venv/bin/activate

pip install -r requirements.txt

uvicorn app.main:app --reload
```

- API: <http://localhost:8000>
- Documentação interativa: <http://localhost:8000/docs> — útil para demonstrar
  o backend na apresentação

Testes:

```bash
pytest -q
```

---

## Executar e validar somente o BE-05

Com o ambiente virtual ativado e o terminal em `backend/`:

```bash
python -m app.mqtt_ingestor
```

Esse comando inicia somente o ingestor. A API também inicia seu próprio ingestor
no `lifespan`: use apenas uma dessas opções por vez, para evitar conflito de
client ID MQTT. Encerre com `Ctrl+C`: MQTT e InfluxDB são fechados.
Se o broker estiver indisponível, o ingestor tenta conectar novamente, com
intervalos de 1 até 30 segundos. As assinaturas são refeitas a cada reconexão.

Para validar com os serviços reais:

1. Inicie Mosquitto e InfluxDB e confira `MQTT_*` e `INFLUX_*` no `.env`.
   A configuração do Compose em `infra/` ainda tem campos de setup pendentes;
   eles precisam estar preenchidos antes de iniciar um banco novo.
2. Execute o comando acima e inicie o Wokwi com o mesmo prefixo MQTT.
   Espere uma linha `Leitura gravada: tank_id=...` a cada aproximadamente 5 s
   e confira o measurement `water_reading` no Data Explorer do InfluxDB.
3. Publique `lixo` em `<prefixo>/tanque-01/telemetry`: deve aparecer um
   `WARNING` com o motivo, e a próxima leitura válida deve continuar chegando.
4. Publique `offline` em `<prefixo>/tanque-01/status` com QoS 1 e retained:
   o log deve mostrar `online=False`; esse valor fica em `ingestor.online`.
5. Na pasta `infra/`, execute `docker compose stop mosquitto` e depois
   `docker compose start mosquitto`. Confirme reconexão e novas leituras
   gravadas, sem reiniciar o Python.

Testes automatizados do ingestor (sem broker ou banco externos):

```bash
python -m pytest tests/test_mqtt_ingestor.py -q
```

Eles usam os validadores e classificadores reais, simulando o cliente MQTT e
o repositório. Cobrem descarte de mensagens, status por tanque, cache, callback,
falha de escrita, reassinatura na reconexão e publicação de comandos. A validação
com Wokwi, Mosquitto e InfluxDB acima continua necessária para o aceite completo.

---

## Backend completo — BE-06 a BE-09

Com Mosquitto e InfluxDB configurados e rodando, abra dois terminais na pasta
`backend/`. No primeiro, inicie API e ingestor juntos:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

No segundo, publique leituras sem precisar abrir o Wokwi:

```powershell
.\.venv\Scripts\python.exe tools/fake_device.py --interval 5
```

Para demonstrar um alerta crítico de pH, encerre o simulador com `Ctrl+C` e use:

```powershell
.\.venv\Scripts\python.exe tools/fake_device.py --interval 2 --anomalia ph
```

Também aceita `--anomalia temperature_c`, `level_pct` ou `turbidity_ntu`,
`--tank-id tanque-02` e `--count 10` para encerrar após dez envios.
O simulador publica status retained e configura Last Will; espera e reconecta
se o broker cair. **É ferramenta de desenvolvimento: na avaliação, quem publica
deve ser o ESP32 do Wokwi.**

Valide em <http://localhost:8000/docs>:

- `/api/health`: checa banco e MQTT; retorna 503 e `status: degraded` se algum falhar.
- `/api/thresholds`: limites reais usados na classificação das quatro grandezas.
- `/api/tanks`: tanques conhecidos, último visto e estado online/offline.
- `/api/readings/latest`: última leitura classificada, idade, unidades e estado.
- `/api/readings/history?range=6h`: médias em janelas automáticas de um minuto.
- `/api/stats`: mínimo, máximo, média, último valor e contagem de leituras no período.

Sem dados, latest retorna 404; histórico e estatísticas retornam estruturas
vazias. Parâmetro inválido retorna 400 e falha de consulta retorna 503. Latest
pode continuar servindo o cache durante uma falha do banco; health continua
indicando a falha. Uma leitura parcial não recebe valores antigos de sensores
que faltaram na mensagem mais recente.

Para testar o WebSocket no console do navegador (duas abas podem usar o mesmo código):

```javascript
const ws = new WebSocket('ws://localhost:8000/ws/live?tank_id=tanque-01');
ws.onmessage = event => console.log(JSON.parse(event.data));
```

Cada conexão recebe apenas seu tanque, nos eventos `reading` e `status`.
Fechar uma aba não interrompe as demais. Clientes lentos têm fila limitada e
são desconectados se não acompanharem o fluxo.

Execute toda a suíte, sem precisar de `.env`, broker ou banco reais:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

Os testes substituem as conexões externas; verificam validação e parâmetros Flux,
formatos, alertas nas bordas, publicação do simulador e MQTT → REST/WebSocket.
As consultas Flux e a latência ponta a ponta ainda precisam ser confirmadas
contra InfluxDB/Mosquitto reais. Não há testes marcados como pendentes.

---

## Estrutura

| Arquivo | Responsabilidade | Tarefa |
| --- | --- | --- |
| `app/config.py` | toda a configuração, lida do `.env` | BE-01 |
| `app/models.py` | validação do payload que chega do MQTT | BE-02 |
| `app/alerts.py` | classificação ok / atencao / critico | BE-03 |
| `app/influx_repo.py` | escrita e consultas no InfluxDB | BE-04, BE-06 |
| `app/mqtt_ingestor.py` | assinatura dos tópicos e persistência | BE-05 |
| `app/api.py` | rotas REST e WebSocket | BE-07, BE-08 |
| `app/main.py` | ponto de entrada, CORS e ciclo de vida | BE-07 |
| `tools/fake_device.py` | publicador falso para desenvolver sem o Wokwi | BE-09 |
| `tests/` | testes automatizados | BE-09 |

---

## Decisões que já estão fechadas

- **Ingestor e API no mesmo processo.** O ingestor sobe no `lifespan` do
  FastAPI. Simplifica a demonstração e permite o push por WebSocket sem fila
  intermediária.
- **O backend é sempre cliente do broker, nunca servidor.** Ele abre uma
  conexão de saída para o Mosquitto — na VM (`mqtt.<dominio>`, padrão) ou em
  `localhost` (plano B com bridge). Trocar de cenário é mexer só no `.env`.
- **Escrita síncrona no InfluxDB.** O modo em lote é mais rápido, mas na
  apresentação o ponto precisa aparecer no gráfico na hora — e erro de escrita
  em lote passa despercebido.
- **Campo ausente não vira field.** Nunca gravar `0` no lugar de uma grandeza
  que faltou: zero é valor legítimo de sensor e falsificaria o gráfico.

---

## Armadilhas conhecidas

| Sintoma | Causa provável |
| --- | --- |
| Ingestor conecta mas nunca recebe nada | assinatura não foi refeita no `on_connect` (ela se perde a cada reconexão) |
| Pontos aparecem em 1970 no gráfico | `ts` do dispositivo usado antes do NTP sincronizar (ver regra 5 da ARQUITETURA §3) |
| Navegador bloqueia as chamadas | CORS não configurado para `http://localhost:5173` |
| WebSocket nunca envia nada | `await` chamado direto da thread do paho; use `asyncio.run_coroutine_threadsafe` |
| `401` do InfluxDB | token sem permissão no bucket, ou org errada no `.env` |
