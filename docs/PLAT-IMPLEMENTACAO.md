# TankVitals — implementação PLAT

O Compose integrado está em [`infra/docker-compose.yml`](../infra/docker-compose.yml).
Ele combina a configuração segura de Mosquitto, as definições RabbitMQ de
PLAT-05, o Writer validado em PLAT-06, a API de Mensageria e os serviços de
telemetria/IA já existentes.

## Segredos locais

Gere segredos fora de pastas sincronizadas, no PowerShell:

```powershell
$secretsRoot = Join-Path $env:TEMP 'tankvitals-secrets'
python scripts/gerar_segredos.py --output-dir $secretsRoot
```

O `.env` resultante contém senhas aleatórias, chave JWT e o caminho absoluto do
`definitions.json`. Os arquivos são locais; não os versione nem compartilhe as
senhas de demonstração impressas pelo gerador.

## Subir o ambiente integrado

```powershell
docker compose --env-file "$secretsRoot/.env" -f infra/docker-compose.yml up -d --build
docker compose --env-file "$secretsRoot/.env" -f infra/docker-compose.yml ps
```

Abra <http://localhost:8080>, entre com um dos usuários de demonstração e
consulte **Mensageria**. O polling da página é de dois segundos. A API exige
JWT, arquiva até 50 mensagens recebidas em `q.ws` em SQLite persistente e obtém
uma amostra da DLQ usando `ack_requeue_true`.

O Writer usa arquivo de token próprio, restrito à escrita no bucket `tankvitals`.
O token de leitura/escrita da API é separado e limitado ao mesmo bucket. O
ledger SQLite do Writer persiste UUIDs processados entre reinicializações.

## Portas publicadas

Somente `8080` fica acessível pela rede. Interfaces administrativas em
`1883`, `5050`, `8086` e `15672` ficam vinculadas a `127.0.0.1`; RabbitMQ AMQP
e PostgreSQL não publicam portas no host.

## Validação registrada

- Os testes unittest de segredos, ACL Mosquitto, definições RabbitMQ e
  bootstrap do token Writer passaram.
- Os 11 testes unittest do Writer passaram.
- Build do backend e build de produção Vue passaram.
- O cenário de deduplicação local confirmou UUID repetido com timestamp
  diferente, ACK do duplicado e retenção do UUID após recriar o Writer.
- A validação ponta a ponta no Docker Compose principal ainda precisa terminar
  antes de PLAT ser declarada concluída. Não use apenas os testes isolados como
  prova do fluxo integrado.
