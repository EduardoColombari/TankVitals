# BACK-01 a BACK-06

Todos os processos usam a imagem `tankvitals-app:local` e os módulos
`app.api`, `app.gateway`, `app.writer`, `app.rules`, `app.inference`,
`app.worker` e `app.simulator`. Execute a partir de `backend/`:

```sh
python -m app.api
```

O Compose em `infra/docker-compose.yml` constrói a imagem, aguarda PostgreSQL,
executa `python -m app.migrate` (Alembic e seed na mesma execução) e só então
inicia os serviços. Use as credenciais geradas pelo script já existente:

```sh
python scripts/gerar_segredos.py
docker compose --env-file .env -f infra/docker-compose.yml up --build -d
```

O seed cria os nove dispositivos, faixas, regra do aerador e usuários
`viewer`, `operator` e `admin`, usando `DEMO_*_PASSWORD`. Reexecutar preserva
cadastros e senhas existentes. A revisão inicial também adiciona as colunas
ausentes em instalações que usavam `infra/postgres/init.sql`.

O login aceita formulário ou JSON. JWT HS256 expira em 30 minutos; cinco
falhas consecutivas bloqueiam a conta por cinco minutos. A sexta tentativa
retorna 429. Logins e bloqueios ficam em `audit`, inclusive nomes desconhecidos.
As rotas de leitura exigem JWT, comandos exigem operador e cadastro, regras,
jobs e auditoria exigem administrador.

`POST /api/ws/ticket` entrega um ticket aleatório de 30 segundos, consumido
uma única vez por `/ws/live?ticket=...`. A API distribui os envelopes de `q.ws`
a todos os clientes, com filas limitadas por conexão. Execute uma única
instância da API: tickets ficam em memória e consumidores da mesma fila
RabbitMQ dividiriam os eventos entre instâncias.

As rotas seguem os formatos da seção 17 de `docs/ARQUITETURA.md`:
`/api/devices`, `/api/rules`, `/api/history`, `/api/alerts` e `/api/audit`.
O histórico consulta `telemetry`, agrega aproximadamente 360 pontos por
série e calcula count/min/max/mean/last no servidor sobre os dados crus.
Há também `/api/devices/{id}/history?variable=...&period=24h`.
Faixas editadas ficam disponíveis imediatamente em `/api/devices`;
o serviço rules já recarrega o cadastro periodicamente.

Além do PUT completo de dispositivo, existem PATCH e DELETE (desativação),
e PUT/DELETE de `/api/devices/{id}/fields/{key}`. Alterações e sua auditoria
são gravadas na mesma transação. `jobs` e `model_versions` espelham os
documentos usados pelo pipeline IA, preservando a compatibilidade existente.

Validação:

```sh
python -m pytest tests -q
```

`tests/test_backend_contract.py` cobre seed repetido, migrations repetidas,
bloqueio de login, papéis, cadastro/faixas, auditoria e tickets de uso único.
Os limites de um segundo para histórico real e entrega via RabbitMQ precisam
ser medidos com a stack Docker e dados representativos.

## Validação nesta implementação

Em 09/11/2026: 350 testes passaram na execução conjunta de `backend/tests`
e `scripts/tests`; seis subtestes também passaram. Os seis testes novos de
aceite passaram. Restaram duas falhas em arquivos de infraestrutura que
não foram alterados: a comparação textual da ACL do Mosquitto e a verificação
do scope probe no harness do writer. A configuração do Compose passou em
`docker compose -f infra/docker-compose.yml config --no-interpolate --quiet`.

O Docker Engine estava indisponível, portanto não foi possível executar
PostgreSQL/RabbitMQ/InfluxDB reais ou medir o histórico de 24 horas. Os testes
relacionais usam SQLite; a entrega em duas conexões testa o distribuidor da
API, sem medir o percurso completo pelo broker. O ambiente local utilizou
Python 3.14 e dependências disponíveis para essa versão; a imagem define
Python 3.13 e instala as versões fixadas nos requirements.
