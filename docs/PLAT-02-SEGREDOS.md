# PLAT-02 — geração segura de segredos

O backend do projeto usa Python 3.13; o gerador também suporta Python 3.10 ou
mais novo. Na raiz do repositório:

```powershell
python scripts/gerar_segredos.py
```

O comando cria `.env` e `secrets/definitions.json`. O `.env` recebe senhas
aleatórias URL-safe para Mosquitto, RabbitMQ, PostgreSQL, InfluxDB, pgAdmin,
JWT e contas de demonstração. `definitions.json` contém os usuários, permissões
e filas de RabbitMQ; as senhas são armazenadas como hashes SHA-256 compatíveis
com RabbitMQ, não como texto puro. As três senhas de demonstração são exibidas
no terminal.

Se `.env` ou `secrets/definitions.json` já existir, o script para sem alterar os
arquivos. Use `--force` somente para rotacionar as credenciais PLAT. Essa opção
preserva as outras configurações e comentários do `.env`, troca os valores
PLAT e recria as definições RabbitMQ.

Para testar sem gerar credenciais na raiz do projeto, passe uma pasta
temporária:

```powershell
python scripts/gerar_segredos.py --output-dir "$env:TEMP\tankvitals-plat02-test"
```

Em Windows, o script usa `icacls` para restringir os arquivos gerados à conta
atual. Em Linux, aplica permissões `0600`. O Git ignora `.env` e `secrets/`, mas
isso não impede a sincronização por serviços de nuvem.

**Atenção ao local deste checkout:** ele está dentro de uma pasta OneDrive. O
OneDrive pode sincronizar os segredos gerados para a conta em nuvem. O script
avisa antes de gerar; execute-o apenas se essa sincronização for aceitável ou
use uma cópia do projeto fora da pasta sincronizada.

O `INFLUXDB_ADMIN_TOKEN` inicial é de administração; não o use como
`INFLUX_TOKEN` da aplicação. Após inicializar o InfluxDB, crie um token com
permissão somente de leitura/escrita no bucket `tankvitals`.

Os testes unitários isolados são executados com:

```powershell
python -m unittest scripts.tests.test_gerar_segredos -v
```
