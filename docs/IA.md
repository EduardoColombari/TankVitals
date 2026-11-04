# Entrega da frente IA

## Telas

`/laboratorio` e `/treinamento` substituem os placeholders IA-03 e IA-06.
O frontend mantém o modo de demonstração existente (`VITE_MOCK` ligado por
padrão). Entre como administrador, gere um dataset no Laboratório e depois
selecione-o no Treinamento. Cada job demonstrativo leva aproximadamente 12 s.
Os gráficos e métricas desse modo são **ilustrativos**, não evidências de treino;
os exemplos desaparecem ao recarregar a página. O aviso aparece nas duas telas.

Com `VITE_MOCK=false`, as mesmas telas usam a API real. Há formulário completo,
validação de volume, progresso, erros, listagem de datasets, histórico de versões,
comparação com a média, previsto × real e importância dos atributos. A versão
ativa é destacada. O progresso vem do store WebSocket e há atualização HTTP a
cada 5 s para recuperar eventos perdidos. Insights e 3D continuam usando o
mesmo store de previsões já existente no frontend.

## Núcleo e execução sem infraestrutura

Na raiz, instale `backend/requirements.txt` em um ambiente virtual. Dentro de
`backend`, com esse Python ativo:

```powershell
python -m app.ai_cli generate data/ai/dataset.parquet
python -m app.ai_cli train data/ai/dataset.parquet data/ai/models --version v-demo-01
python -m pytest -q
```

O primeiro comando gera **1.008.000 linhas** (100 bombas × 7 dias × 1.440
minutos). Não sobrescreve um arquivo existente. Aceita `--pumps`, `--days`,
`--seed` e `--failure-fraction`. O segundo grava os modelos aprovados, manifesto
e `result.json`, com métricas e dados dos gráficos. Esse comando é uma validação
isolada: não promove a versão no banco nem publica eventos.

Validação medida nesta máquina: 1.008.000 linhas em **2,69 s**. O tempo depende
do hardware. A semente e todos os parâmetros, inclusive o tamanho do bloco,
precisam ser iguais para reproduzir o dataset.

- `core/physics.py`: mesma física para uma bomba e para blocos; seis modos de
  falha, desgaste acelerado, ruído, diferenças entre bombas e ciclo ambiental.
- `core/datagen.py`: blocos NumPy, limite de 20 milhões de linhas e 500 mil
  por bloco, Parquet atômico, faltantes/outliers e rótulos somente no dataset.
  Uma leitura representa um minuto simulado.
- RUL usa a solução da mesma equação de desgaste da física; teto de 168 h.
- `core/features.py`: média, desvio, tendência e máximo das quatro variáveis,
  somente sobre o passado. Janelas com valores ausentes/não finitos não geram
  exemplos; a inferência também reinicia a janela após uma leitura inválida.
- `core/training.py`: Random Forest, separação por bomba, baseline pela média,
  MAE/RMSE/R², promoção condicionada a vencer MAE e RMSE da base na validação
  para **ambos** os alvos. Teste reservado para avaliação final; R² > 0,98 gera
  aviso. As features e o tamanho da janela ficam no manifesto.
- `core/inference.py`: valida o manifesto, usa a janela do treino e troca os
  modelos sem reiniciar. Janelas incompatíveis são reiniciadas.
- `core/alert_hysteresis.py`: três previsões ruins abrem; dez boas fecham.
  NaN e infinito são recusados.

## Integração com BACK, PLAT e IOT

O checkout ainda contém a infraestrutura do primeiro bimestre. Os serviços
abaixo estão implementados para a topologia de `ARQUITETURA.md` §14, mas a
integração completa depende dos serviços das outras frentes. Não basta ligar
`VITE_MOCK=false` enquanto o login e o WebSocket reais estiverem ausentes.

Exporte estas variáveis nos processos da API, worker e inference, ou passe-as
pelo Compose. Não grave credenciais no Git:

| Variável | Uso |
| --- | --- |
| `AI_DATABASE_URL` | URL SQLAlchemy `postgresql+psycopg://...`; habilita as rotas na API |
| `RABBITMQ_URL` | URL AMQP do usuário específico do serviço |
| `AI_DATA_DIR` | Caminho do volume compartilhado de datasets/modelos; padrão `data/ai` |
| `JWT_SECRET` | Mesma chave HS256 usada pelo login de BACK; claims `sub`, `role`, `exp` |
| `INFLUX_URL`, `INFLUX_TOKEN`, `INFLUX_ORG`, `INFLUX_BUCKET` | Configuração já usada pelo backend |

Com bancos, buckets e topologia prontos:

```powershell
python -m app.worker
python -m app.inference
uvicorn app.main:app
```

Execute **uma réplica de worker e uma de inference**. O worker serializa os
jobs para controlar memória; os serviços não declaram filas nem exchanges.
PLAT precisa fornecer `q.jobs.datagen`, `q.jobs.train`, `q.inference`, as
exchanges, os bindings e a DLQ documentados. A API precisa de permissão para
publicar em `tv.jobs`. O worker publica em `tv.events`; inference publica em
`tv.insights` e `tv.events`. Preserve um volume comum em `AI_DATA_DIR`.

O worker usa `asyncio.to_thread` para geração e treino. Grava progresso e
publica `job_progress` a cada bloco. A amostra de até 1.000 leituras vai para
`lab/synthetic`, nunca para o bucket ao vivo; o bucket `lab` deve existir.
Se essa exportação falhar, o job sinaliza falha, mas o Parquet permanece
registrado e pode ser usado para treino.

A API grava job e auditoria na mesma transação e retorna 202. Um dispatcher
publica os pedidos pendentes e tenta novamente se o broker estiver indisponível.
IDs de jobs concluídos não são executados novamente. A gravação de modelos é
atômica por diretório; uma reentrega após gravação recupera `result.json`.
Mensagens inválidas/falhas são rejeitadas para a DLQ. Jobs marcados `failed`
precisam de um novo pedido após corrigir a causa; não há botão de retentativa.

`ai_records` é uma tabela isolada da frente IA (SQLAlchemy JSON), criada ao
iniciar, com registros de jobs, datasets, auditoria, versões, versão ativa,
alertas e estado de inferência. SQLite é usado apenas nos testes. **BACK deve
integrar essa tabela às migrações e às consultas globais de alertas/auditoria**;
ela não substitui as tabelas de usuários e dispositivos. O histórico
`GET /api/insights/history` consulta o measurement `insight` do InfluxDB e
calcula estatísticas sobre os pontos crus.

A inferência salva janelas, sequências de histerese e eventos pendentes. Uma
reentrega não conta como outra previsão; uma falha de publicação pode ser
recuperada sem perder a transição do alerta. Mensagens fora de ordem são
descartadas. Bomba parada reinicia a janela, mas não fecha automaticamente
um alerta. O writer de PLAT precisa gravar os insights, e BACK precisa consumir
`q.ws` e repassar os envelopes ao WebSocket autenticado. Esse transporte não
foi acrescentado ao socket legado do primeiro bimestre.

Para IOT, o contrato é:

```python
from app.core.physics import create_population, advance_population, inject_fault, repair

bomba = create_population(1, seed=42)
leitura = advance_population(bomba)  # arrays de tamanho 1; somente observáveis
inject_fault(bomba, "rolamento")
repair(bomba)
```

Envie os quatro campos de `PUMP_FIELDS` e `running` no envelope de telemetria,
sem copiar `wear`, `fault_mode` ou os rótulos internos da população.

## Contratos HTTP adicionais

Todas as rotas de IA exigem JWT válido; os dois POSTs exigem admin.

| Rota | Resposta / corpo |
| --- | --- |
| `POST /api/lab/jobs` | parâmetros de `DatasetRequest`; 202 com job `{id, kind, status, progress, message, created_at, params}` |
| `GET /api/lab/jobs` | jobs de geração e treino |
| `GET /api/lab/datasets` | `{id, rows, bytes, params, created_at}`; caminho interno não exposto |
| `POST /api/models/train` | `{dataset_id, window_size?:30, sample_every?:10, seed?:42}`; 202 com job |
| `GET /api/models` | versões, `active`, `accepted`, validação, teste, gráficos e importâncias |
| `GET /api/insights/history?range=6h&device=bomba-rec-01` | formato de histórico usado pelo frontend |

O envelope de progresso segue a §12 da arquitetura (`progress` entre 0 e 1).
`model_updated` informa a versão do par de modelos; inference consulta a versão
ativa persistida, em vez de aceitar um caminho arbitrário vindo da mensagem.

## Verificação realizada e limites

Foram executados os testes do backend, incluindo permissões, volume antecipado,
reprodutibilidade, RUL versus falha física, blocos versus passo individual,
separação por bomba, treino versus baseline, worker fora do event loop,
reentregas e recuperação de alertas após reinício. O build Vue/TypeScript passou.
Não foi realizada validação com RabbitMQ/PostgreSQL/InfluxDB reais nem inspeção
visual automatizada no navegador. A entrega integrada exige as dependências
das outras frentes descritas acima.
