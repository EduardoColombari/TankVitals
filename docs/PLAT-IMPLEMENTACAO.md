# TankVitals — implementação PLAT

Este documento acompanha a implementação de Plataforma e Mensageria sem
substituir os contratos compartilhados de [`ARQUITETURA.md`](ARQUITETURA.md).
As seções 11–17 desse contrato continuam sendo uma proposta até a revisão das
outras frentes.

## PLAT-02 — gerar segredos locais

Use Python 3.10 ou mais novo, na raiz do repositório:

```powershell
python scripts/gerar_segredos.py
```

Em um ambiente limpo, o script cria `.env` e grava
`secrets/definitions.json` para importar as contas e a topologia do RabbitMQ.
Senhas das contas de serviço ficam no `.env`; o arquivo de definições guarda os
hashes SHA-256 compatíveis com RabbitMQ. As três senhas de demonstração são
mostradas no terminal ao gerar os arquivos.

Se `.env` ou `secrets/definitions.json` já existir, a execução para sem alterar
os arquivos. Use `--force` para rotacionar as credenciais PLAT; as demais
variáveis e comentários do `.env` são preservados. Rode o gerador antes de
copiar `.env.example` para `.env`.

Para rotacionar as credenciais PLAT:

```powershell
python scripts/gerar_segredos.py --force
```

`--force` troca as senhas PLAT no `.env` e recria as definições. Atualize os
serviços que usam essas credenciais após a rotação. O script não apaga nem
recria bancos, volumes ou configurações existentes.

O token administrativo inicial do InfluxDB não deve ser usado como
`INFLUX_TOKEN` da aplicação. Depois da inicialização do InfluxDB, crie um token
com permissão apenas de leitura/escrita no bucket `tankvitals` e informe-o em
`INFLUX_TOKEN`. A criação de um token com escopo de bucket depende do InfluxDB
estar inicializado e não é feita pelo gerador local.

Os arquivos gerados são locais e ignorados pelo Git (`.env` e `secrets/`). Não
envie as senhas de demonstração para o repositório ou para mensagens públicas.

### Estado

- PLAT-01: proposta das seções 11–17 existente; aguarda revisão de contratos.
- PLAT-02: gerador e testes unitários preparados; execução depende de Python
  instalado no ambiente.
- PLAT-04: ACL criada em `infra/mosquitto/config/acl`; ativação e restrição da
  porta aguardam autorização para editar `infra/mosquitto/config/mosquitto.conf`
  e o Compose.
- PLAT-05 a PLAT-08: implementação em etapas; alterações nos arquivos
  compartilhados reservados aguardam autorização específica.
