# PLAT-04 — Mosquitto e segurança MQTT

## Escopo e estado

O `infra/mosquitto/config/mosquitto.conf` existente pertence ao fluxo do 1º
bimestre: aceita conexão anônima no broker local e mantém uma bridge para
`test.mosquitto.org`, usada pelo ESP32 no Wokwi. O código atual do backend e do
firmware continua usando esse contrato legado.

A ACL preliminar em `infra/mosquitto/config/acl` já define usuários `simulator`
e `gateway` para os tópicos do 2º bimestre documentados em
`docs/ARQUITETURA.md` §13. A ACL ainda não é carregada pela configuração
compartilhada.

Esta implementação adiciona uma configuração e um Compose **isolados** para
testar a proposta PLAT-04. Ela não substitui nem ativa a configuração legada.
Ativar o modo estrito no Compose principal depende de aprovação, pois quebra a
bridge pública e a telemetria legada do firmware/backend.

## Segurança aplicada no perfil isolado

- `allow_anonymous false` e autenticação no arquivo `password_file`.
- As senhas vêm de `MQTT_SIMULATOR_PASSWORD` e `MQTT_GATEWAY_PASSWORD`, geradas
  pelo script PLAT-02. O script local `start.sh` alimenta `mosquitto_passwd`
  pela entrada padrão; as senhas não são argumentos do processo nem são
  impressas nos logs. Como esta é uma configuração de teste local, elas são
  passadas ao container por ambiente e podem ser vistas por quem tem acesso
  administrativo ao Docker; não use esta configuração como política de segredo
  em produção.
- O arquivo de senhas com hash fica em `/run/mosquitto`, um `tmpfs` restrito ao
  container. Ele é recriado a cada início, não é persistido nem versionado.
- A ACL dá ao simulador publicação somente em `.../telemetry` e status, e
  assinatura somente em `.../cmd`. O gateway recebe as permissões inversas.
- Com a ACL clássica, `read` impede que mensagens de tópicos não autorizados
  sejam entregues, mas não rejeita o pacote `SUBSCRIBE`: o broker pode enviar
  SUBACK de sucesso e depois não encaminhar mensagens. O teste demonstra essa
  diferença. A exigência mais estrita de rejeitar o próprio pedido de assinatura
  requer outro mecanismo de autorização, como o Dynamic Security plugin, e fica
  pendente de decisão/aprovação.
- A porta de teste é vinculada a `127.0.0.1:1884` por padrão.
- Logs de conexão e autorização não incluem credenciais.

O `.gitignore` já ignora `.env`, `secrets/` e
`infra/mosquitto/config/passwd`. O perfil de teste não cria arquivo de senha
na árvore do repositório.

## Teste automatizado de integração

Com Docker Desktop, Docker Compose, Python e o plugin `docker compose`
disponíveis, na raiz do projeto execute em PowerShell:

```powershell
.\scripts\tests\test_mosquitto_integration.ps1
```

O script cria credenciais em uma pasta temporária fora do checkout, inicia um
projeto Compose isolado, testa autenticação anônima, publicação e assinatura
permitidas, descarte de publicação não autorizada, bloqueio de entrega ao
assinante sem permissão e retenção após reinício. O teste também registra que a
ACL clássica aceita o SUBSCRIBE no protocolo. Em seguida remove somente o
container e a rede do projeto de teste e apaga sua pasta temporária; não remove
volumes Docker.

## Inicialização para teste manual

Gere credenciais em uma pasta temporária, fora da pasta OneDrive do checkout:

```powershell
$testRoot = Join-Path $env:TEMP 'tankvitals-plat04-manual'
python scripts\gerar_segredos.py --output-dir $testRoot
$compose = 'infra/mosquitto/plat04/compose.yaml'
docker compose --project-name tankvitals-plat04-manual --env-file "$testRoot/.env" --file $compose up -d
```

O broker de teste escuta somente em `127.0.0.1:1884`. Os exemplos abaixo
executam os clientes dentro do container, que já recebeu as duas credenciais
pela configuração de teste. Não imprima nem compartilhe os valores do `.env`.

### Testes manuais

```powershell
# Anônimo: deve falhar com Not authorized.
docker compose --project-name tankvitals-plat04-manual --env-file "$testRoot/.env" --file $compose exec -T mosquitto mosquitto_sub -h localhost -p 1883 -t '$SYS/broker/uptime' -C 1 -W 2

# Terminal 1: gateway autorizado a assinar telemetria.
docker compose --project-name tankvitals-plat04-manual --env-file "$testRoot/.env" --file $compose exec -T mosquitto sh -c 'mosquitto_sub -h localhost -p 1883 -u gateway -P "$MQTT_GATEWAY_PASSWORD" -t tankvitals/tanque-01/sensor/od-01/telemetry -C 1 -W 30'

# Terminal 2: simulador autorizado a publicar telemetria.
docker compose --project-name tankvitals-plat04-manual --env-file "$testRoot/.env" --file $compose exec -T mosquitto sh -c 'mosquitto_pub -h localhost -p 1883 -u simulator -P "$MQTT_SIMULATOR_PASSWORD" -t tankvitals/tanque-01/sensor/od-01/telemetry -m plat04-manual-ok'

# Gateway autorizado a assinar comandos.
docker compose --project-name tankvitals-plat04-manual --env-file "$testRoot/.env" --file $compose exec -T mosquitto sh -c 'mosquitto_sub -h localhost -p 1883 -u gateway -P "$MQTT_GATEWAY_PASSWORD" -t tankvitals/tanque-01/actuator/aerador-01/cmd -C 1 -W 30'

# Em outro terminal, simulador sem permissão de publicar comando.
# O cliente pode encerrar sem erro; o gateway não deve receber esta mensagem.
docker compose --project-name tankvitals-plat04-manual --env-file "$testRoot/.env" --file $compose exec -T mosquitto sh -c 'mosquitto_pub -h localhost -p 1883 -u simulator -P "$MQTT_SIMULATOR_PASSWORD" -t tankvitals/tanque-01/actuator/aerador-01/cmd -m plat04-nao-deve-chegar'

# Simulador sem permissão para receber telemetria. A ACL clássica pode aceitar
# o SUBSCRIBE (SUBACK 0), mas nenhuma mensagem desse tópico deve ser entregue.
docker compose --project-name tankvitals-plat04-manual --env-file "$testRoot/.env" --file $compose exec -T mosquitto sh -c 'mosquitto_sub -d -h localhost -p 1883 -u simulator -P "$MQTT_SIMULATOR_PASSWORD" -t tankvitals/+/+/+/telemetry -C 1 -W 3'
```

Para confirmar a publicação negada, mantenha o `mosquitto_sub` autorizado do
gateway aberto no tópico `/cmd` durante o teste e verifique que ele não recebe
`plat04-nao-deve-chegar`. Não use apenas o código de saída do publicador como
prova de bloqueio: MQTT pode descartar a mensagem sem informar o publicador.
O ACL clássico do Mosquitto também não rejeita necessariamente o pedido de
assinatura no SUBACK; confirme que nenhum payload protegido é entregue. A
rejeição explícita do SUBSCRIBE continua pendente de um mecanismo de autorização
que ofereça esse controle.

Encerre somente o projeto isolado quando terminar:

```powershell
docker compose --project-name tankvitals-plat04-manual --env-file "$testRoot/.env" --file $compose down
Remove-Item -LiteralPath $testRoot -Recurse -Force
```

## Integração pendente

Para ativar esta configuração no ambiente principal será preciso autorizar e
revisar uma alteração em `infra/mosquitto/config/mosquitto.conf` e no Compose
compartilhado. Essa migração deve decidir como manter ou encerrar o contrato
legado `tankvitals-unifacef-g3/<tanque>/...` da bridge e como o firmware e o
backend passarão para `tankvitals/<zona>/<tipo>/<device>/...`. Esta tarefa não
altera essas frentes nem ativa a migração.
