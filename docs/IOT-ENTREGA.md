# Frente IOT — o que foi feito e como ligar

Tudo aqui é **código novo**: nenhum arquivo do 1º bimestre nem do trabalho das
outras frentes foi alterado. Contrato seguido: `docs/ARQUITETURA.md` §12 a §17.

## Onde está cada tarefa

| Tarefa | Onde | Como conferir |
| --- | --- | --- |
| IOT-01 Simulador | `backend/app/simulator/` | `python -m app.simulator` e `mosquitto_sub -t 'tankvitals/#' -u … -P …` mostra os 9 dispositivos |
| IOT-02 Gateway | `backend/app/gateway/` | JSON quebrado publicado no MQTT aparece na `q.dlq` com o header `x-reason` |
| IOT-03 Atuadores | `simulator/world.py`, `simulator/service.py` | comando em `…/cmd` → telemetria `{"on": true}` na hora e efeito nas leituras |
| IOT-04 Rota de comando | `backend/app/control/` | `POST /api/devices/{id}/command` |
| IOT-05 Botões | já existia: `ActuatorControl.vue`, `FaultControl.vue` | conferido contra os 4 itens da tarefa; **não foi preciso mexer** |
| IOT-06 Bomba + `SIM_SPEED` | `simulator/pump.py`, `SIM_SPEED` | ver "Pendências" |
| IOT-07 Aerador automático | `backend/app/rules/` | `python -m app.rules` |
| IOT-08 Falha injetada + teste | `simulator/pump.py`, `tests/test_iot_simulator_service.py` | `test_no_published_message_carries_the_simulation_label` |
| `core/` mínimo | `backend/app/core/` | envelope, tópicos, faixas, RabbitMQ, PostgreSQL |

Testes: `backend/tests/test_iot_*.py` (144 passando em ambiente sem rede; ver abaixo o que não rodou).

## Variáveis de ambiente por serviço

O compose (PLAT-03) mapeia os segredos do `.env` para estes nomes, um usuário por serviço.

| Serviço | Variáveis |
| --- | --- |
| simulator | `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME=simulator`, `MQTT_PASSWORD`, `SIM_SPEED` (60), `SIM_SEED` (opcional) |
| gateway | `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME=gateway`, `MQTT_PASSWORD`, `RABBITMQ_HOST`, `RABBITMQ_PORT`, `RABBITMQ_USER=gateway`, `RABBITMQ_PASSWORD` |
| rules | `RABBITMQ_*` (usuário `rules`), `POSTGRES_HOST/PORT/USER/PASSWORD/DB`, `RULES_RELOAD_S` (10), `RULES_ALERT_CLOSE_AFTER` (30), `RULES_COMMAND_GRACE_S` (20) |

Comandos do compose: `python -m app.simulator`, `python -m app.gateway`, `python -m app.rules`.
Dependências novas: `backend/requirements-iot.txt`.

## Ligar a rota de comando na API (BACK)

O login e o RabbitMQ são da BACK, então a rota recebe os dois de fora:

```python
from app.control.router import create_command_router
from app.control.service import CommandService
from app.control.store import PostgresCommandStore

service = CommandService(PostgresCommandStore(db), bus.publish)   # db: app.core.pg.Database, bus: app.core.rabbit.RabbitBus
app.include_router(create_command_router(service, require_operator=require_role("operator")), prefix="/api")
```

A dependência deve devolver o usuário (`.username`, texto ou dict com `username`/`sub`).
Devolve `{id, status: "sent"}`; 404 dispositivo inexistente, 409 desativado, 422 comando/valor
inválido, 503 barramento fora. Cada tentativa vai para a auditoria (`type=command`).

## Decisões e suposições (para revisar com o grupo)

- **Banco por reflexão.** `rules` e `control` leem as tabelas como estão no PostgreSQL, usando só os
  nomes de tabela e coluna da §16.1. Se a BACK-02 mudar um nome, o ajuste é em `rules/repo.py` e `control/store.py`.
- **Alertas de faixa** têm id `a-<id da linha>` e texto igual ao do modo simulado do front.
  Abrem na 1ª leitura fora da faixa e fecham após 30 leituras seguidas dentro (igual ao front).
- **A bomba não para sozinha** ao chegar no desgaste máximo: fica no pior estado até o `repair`
  (assim `running=false` só significa "desligada por comando").
- **Duas proteções a mais no aerador**: estado de partida vem da telemetria do atuador, e um comando
  que o atuador não seguiu por mais de 20 s é reenviado (cobre o simulador reiniciando).
- **Comando manual no aerador** não é bloqueado pela API (o front já esconde o botão: `control = rule`).
- Permissões do RabbitMQ (PLAT-05): `rules` e `gateway` pegam as exchanges sem declarar (`ensure=False`).
  Se a reconexão do aio-pika tentar redeclarar, a permissão de *configure* precisa permitir declaração passiva.

## Pendências e o que NÃO foi verificado

- **IOT-06 depende da IA-01.** `core/physics.py` ainda não existe, então a bomba usa um modelo provisório
  (`simulator/pump.py`, mesmo passo de 1 min simulado e `SIM_SPEED`, mesmos modos de falha). A troca é só
  nesse arquivo. Falta a conferência com a IA de que as leituras ao vivo parecem as do gerador.
- **Não rodei contra Mosquitto, RabbitMQ e PostgreSQL de verdade** (sem rede/Docker no ambiente de
  desenvolvimento). A lógica foi testada com portas falsas; os adaptadores finos (`simulator/mqtt.py`,
  `gateway/runner.py`, `rules/runner.py`, `core/rabbit.py`) precisam do teste de integração no compose.
- `tests/test_iot_pg_repos.py` e os 3 testes de rota (`test_route_*`) só rodam com SQLAlchemy/FastAPI
  instalados; não rodaram aqui. Os testes do gateway rodaram com um substituto do pydantic.
- Depende do PLAT: `definitions.json` com as filas/exchanges da §14, ACL do Mosquitto, compose e segredos.
