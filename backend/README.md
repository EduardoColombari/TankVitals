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
