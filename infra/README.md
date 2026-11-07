# Infraestrutura integrada TankVitals

O Compose principal do segundo bimestre fica em `docker-compose.yml`. Ele inicia
Mosquitto com ACL, RabbitMQ com as definições PLAT-05, InfluxDB, PostgreSQL,
API, gateway, writer, rules, inference, worker, simulador, pgAdmin e frontend.

## Gerar segredos e iniciar

No PowerShell, gere os arquivos locais fora de pastas sincronizadas e suba o
Compose sem publicar credenciais:

```powershell
$secretsRoot = Join-Path $env:TEMP 'tankvitals-secrets'
python scripts/gerar_segredos.py --output-dir $secretsRoot
docker compose --env-file "$secretsRoot/.env" -f infra/docker-compose.yml up -d --build
docker compose --env-file "$secretsRoot/.env" -f infra/docker-compose.yml ps
```

O gerador imprime as senhas dos três usuários de demonstração uma vez para
anotação local. Não as cole em logs ou no repositório. O `.env` gerado aponta
para o `definitions.json` ao lado dele. Não use `--force` sem intenção de
rotacionar as credenciais.

A aplicação fica em <http://localhost:8080>. O login usa `viewer`, `operator`
ou `admin` e as respectivas senhas `DEMO_*` do `.env` local.

## Portas e permissões

- A única porta acessível pela rede é `8080` (Nginx → API e WebSocket).
- Mosquitto (`1883`), InfluxDB (`8086`), RabbitMQ Management (`15672`) e
  pgAdmin (`5050`) ficam vinculados a `127.0.0.1`.
- AMQP (`5672`) e PostgreSQL (`5432`) não são publicados no host.
- Writer lê um token Influx com escrita somente no bucket `tankvitals`; API e
  worker recebem token separado, limitado a leitura/escrita no bucket.
- Os serviços Python e o Nginx rodam sem root. Os dados ficam em volumes
  nomeados; `docker compose down` preserva os volumes. Não use `down -v` para
  uma parada normal.

## Verificação

```powershell
docker compose --env-file "$secretsRoot/.env" -f infra/docker-compose.yml ps
docker compose --env-file "$secretsRoot/.env" -f infra/docker-compose.yml logs --tail 100 api gateway writer
```

Faça login pela interface e abra **Mensageria** para consultar filas, taxas,
consumidores, as últimas 50 mensagens de `q.ws` e uma amostra reencaminhada da
DLQ. O endpoint `GET /api/messaging` exige JWT e consulta RabbitMQ no backend.

O Compose inclui os serviços de IA baseados em RabbitMQ/PostgreSQL. Ollama não
é iniciado porque a API atual não contém uma rota que o utilize.
