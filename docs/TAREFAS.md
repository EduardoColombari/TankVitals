# TankVitals — Backlog de implementação

Tudo que falta para o projeto rodar ponta a ponta, quebrado em tarefas com
passo a passo e critério de aceite.

**Antes de pegar qualquer tarefa, leia [ARQUITETURA.md](ARQUITETURA.md).** Os
nomes de tópico, campo, endpoint e variável de ambiente já estão fechados lá —
não invente nomes novos, senão as peças não encaixam na hora de integrar.

---

## Índice

- [Como usar este backlog](#como-usar-este-backlog)
- [Ordem de execução (caminho crítico)](#ordem-de-execução-caminho-crítico)
- [Frente INFRA — Mosquitto e InfluxDB](#frente-infra--mosquitto-e-influxdb) (4 tarefas + 1 descartada)
- [Frente FW — Firmware ESP32 / Wokwi](#frente-fw--firmware-esp32--wokwi) (5 tarefas)
- [Frente BE — Backend Python](#frente-be--backend-python) (9 tarefas)
- [Frente FE — Frontend Vue 3](#frente-fe--frontend-vue-3) (8 tarefas)
- [Frente ENT — Entrega e apresentação](#frente-ent--entrega-e-apresentação) (4 tarefas)
- [Checklist final × rubrica de avaliação](#checklist-final--rubrica-de-avaliação)

---

## Como usar este backlog

**Formato de cada tarefa**

| Campo | Significado |
| --- | --- |
| Objetivo | o resultado esperado, em uma frase |
| Depende de | não comece antes disso estar pronto |
| Entrega | arquivos que devem existir no repositório ao final |
| Passo a passo | o caminho sugerido (não é obrigatório seguir ao pé da letra) |
| Critério de aceite | como saber que acabou |
| Ref. | seção da ARQUITETURA.md que define o contrato |
| Peso | quanto vale na rubrica da disciplina |

O **critério de aceite** é a parte que mais importa: a rubrica da disciplina diz
que incluir a tecnologia no código sem demonstrar funcionamento não pontua.
Então vale conferir cada item rodando, e não só lendo o código.

Como criar branch, escrever commit e abrir PR está no
[Guia de desenvolvimento](PADROES-DESENVOLVIMENTO.md).

**Estimativas** são para uma pessoa que já viu a tecnologia antes. Se for o
primeiro contato, dobre — e sem drama, a estimativa é só pra ajudar a dividir o
trabalho.

---

## O que já está no repositório

Para ninguém começar do zero absoluto, o esqueleto dos três projetos já está
criado e as dependências instaladas:

| Já pronto | Onde | O que ainda falta |
| --- | --- | --- |
| Projeto Vue 3 + TS + Vite, com Chart.js e vue-chartjs instalados | `frontend/` | proxy, `strict`, e todo o dashboard (FE-01..08) |
| Projeto Python com `requirements.txt` instalado e `pytest` rodando | `backend/` | implementar os módulos (BE-01..09) |
| Módulos do backend com a assinatura das funções e os `TODO(BE-xx)` | `backend/app/` | o corpo das funções |
| Testes com os casos já nomeados, marcados como pendentes | `backend/tests/` | tirar o `skip` e implementar (BE-09) |
| `docker-compose.yml` e `mosquitto.conf` prontos e validados | `infra/` | nada (INFRA-01, 02 e 04 concluídas) |
| `.env.example` com todas as chaves | raiz e `frontend/` | copiar para `.env` e preencher (INFRA-02) |

Ou seja: as tarefas BE-01, FE-01 e INFRA-01 já começam parcialmente andadas —
leia o passo a passo delas e pule o que já estiver feito.

---

## Ordem de execução (caminho crítico)

O gargalo é o dado chegar no banco. Sem isso, nem backend nem front têm o que
mostrar. Por isso INFRA e FW vêm primeiro.

```
Semana 1   INFRA-01 -> INFRA-02 -> INFRA-04 ─┐
           FW-01 -> FW-02 -> FW-03 ──────────┤
                                             ├─> INFRA-05 (dado chegando no broker)
Semana 2   BE-01 -> BE-02 -> BE-04 -> BE-05 ─┘   (dado chegando no InfluxDB)
           FW-04, FW-05
Semana 3   BE-06 -> BE-07 -> BE-08
           FE-01 -> FE-02 -> FE-03
Semana 4   FE-04 -> FE-05 -> FE-06 -> FE-07
           BE-09, FE-08, ENT-01..04
```

**Dá para paralelizar desde o dia 1:**

| Frente | Pode começar imediatamente porque... |
| --- | --- |
| INFRA | não depende de ninguém |
| FW | o contrato do payload já está definido (§2.2 e §3) |
| BE | `BE-01`/`BE-02`/`BE-03` são código puro; use o simulador da `BE-09` para testar sem ESP32 |
| FE | `FE-01`/`FE-02` só precisam do contrato da API (§6); use dados falsos (*mock*) até a API existir |

**Regra anti-travamento:** ninguém fica parado esperando outra frente. Se o
backend ainda não existe, o front trabalha com JSON de exemplo copiado da
ARQUITETURA §6. Se o ESP32 ainda não publica, o backend usa o publicador falso
da BE-09.

---

## Frente INFRA — Mosquitto e InfluxDB

> Meta da frente: um comando sobe o ambiente e o dado do ESP32 chega no broker
> local. **Peso indireto: 3,5 pontos** (MQTT/Mosquitto 2,0 + InfluxDB 1,5) —
> nenhuma outra frente pontua sem esta.

### INFRA-01 — Subir Mosquitto e InfluxDB com Docker Compose

**Objetivo:** ter os dois serviços rodando localmente com um comando.
**Depende de:** nada.
**Entrega:** `infra/docker-compose.yml`, `infra/mosquitto/config/mosquitto.conf`, `infra/README.md`.
**Estimativa:** 2 h.

**Passo a passo**

1. Crie `infra/docker-compose.yml` com dois serviços:

   | Serviço | Imagem | Portas | Volumes |
   | --- | --- | --- | --- |
   | `mosquitto` | `eclipse-mosquitto:2` | `1883:1883`, `9001:9001` | `./mosquitto/config`, `./mosquitto/data`, `./mosquitto/log` |
   | `influxdb` | `influxdb:2.7` | `8086:8086` | volume nomeado para `/var/lib/influxdb2` e `/etc/influxdb2` |

2. No serviço `influxdb`, use as variáveis de *setup* automático para não
   precisar clicar na interface toda vez que recriar o container:
   `DOCKER_INFLUXDB_INIT_MODE=setup`, `_USERNAME`, `_PASSWORD` (mínimo 8
   caracteres), `_ORG=unifacef`, `_BUCKET=tankvitals`, `_RETENTION=30d`,
   `_ADMIN_TOKEN` (invente um token fixo para o ambiente de desenvolvimento).
3. Ponha `restart: unless-stopped` nos dois.
4. Crie `mosquitto.conf` mínimo (a bridge entra na INFRA-04):
   ```
   listener 1883
   allow_anonymous true
   persistence true
   persistence_location /mosquitto/data/
   log_dest stdout
   ```
   > O Mosquitto 2.x **não aceita conexão de fora do container sem
   > `listener` + `allow_anonymous` explícitos.** Se esquecer, o sintoma é
   > "Connection refused" vindo de qualquer cliente.
5. Adicione `infra/data/` e `infra/mosquitto/{data,log}/` ao `.gitignore` (já
   está lá — confira).
6. Escreva `infra/README.md` com: como subir (`docker compose up -d`), como ver
   log (`docker compose logs -f mosquitto`), como derrubar e como zerar os dados.

**Critério de aceite**

- [x] `docker compose up -d` sobe os dois containers sem erro.
- [x] `docker compose ps` mostra ambos como `running`/`healthy`.
- [x] `http://localhost:8086` abre a interface do InfluxDB e aceita o login criado.
- [x] `docker compose down && docker compose up -d` mantém os dados do InfluxDB.

**Ref.:** ARQUITETURA §4, §8. **Peso:** habilita 3,5 pts.

---

### INFRA-02 — Configurar bucket, token e `.env`

**Objetivo:** o backend ter credencial válida para escrever no InfluxDB.
**Depende de:** INFRA-01.
**Entrega:** `.env.example` na raiz, seção de credenciais no `infra/README.md`.
**Estimativa:** 1 h.

**Passo a passo**

1. Abra `http://localhost:8086` e confirme que existem a organização `unifacef`
   e o bucket `tankvitals` com retenção de 30 dias (criados pelo setup da INFRA-01).
2. Em **Load Data → API Tokens**, gere um token do tipo *Custom* com permissão
   de **read + write apenas no bucket `tankvitals`**.
   > Não use o token de admin no backend. Se vazar no GitHub, vaza o banco inteiro.
3. Crie o `.env` na raiz do projeto com todas as chaves listadas na
   ARQUITETURA §8, preenchendo `INFLUX_TOKEN` com o token gerado.
4. Crie o `.env.example` com **as mesmas chaves e valores vazios ou de exemplo**
   — este é o arquivo que vai para o Git.
5. Confirme que `.env` está ignorado: `git check-ignore -v .env` tem que
   responder apontando para o `.gitignore`.

**Critério de aceite**

- [x] Bucket `tankvitals` existe na org `unifacef` com retenção de 30 dias.
- [x] Token com escopo restrito ao bucket criado e salvo no `.env`.
- [x] `.env.example` versionado, `.env` **não** versionado.
- [x] Teste de escrita manual funciona:
      `curl -i -XPOST "http://localhost:8086/api/v2/write?org=unifacef&bucket=tankvitals&precision=s" -H "Authorization: Token $INFLUX_TOKEN" --data-raw "water_reading,tank_id=teste temperature_c=25.5"`
      retorna **HTTP 204**.

**Ref.:** ARQUITETURA §4, §8. **Peso:** parte de 1,5 pts (persistência).

---

### INFRA-03 — Publicar o Mosquitto na VM Oracle ❌ DESCARTADA

A equipe decidiu não usar VM. O broker fica local e o ESP32 chega nele pela
bridge da INFRA-04, que deixou de ser plano B e passou a ser o caminho único.

O motivo do descarte está registrado na
[ARQUITETURA §7](ARQUITETURA.md#7-conectividade-wokwi--mosquitto): provisionar a
instância, abrir a porta nos dois firewalls empilhados da Oracle e manter o DNS
fora do proxy do Cloudflare custava mais tempo do que o projeto tinha.

**Peso:** os 2,0 pts de MQTT/Mosquitto passam a depender da INFRA-04.

---

### INFRA-04 — Broker público com bridge ✅

**Objetivo:** o ESP32 do Wokwi alcançar o Mosquitto local.
**Depende de:** INFRA-01.
**Entrega:** bloco de bridge comentado no `mosquitto.conf` local, seção "Plano B"
no `infra/README.md`.
**Estimativa:** 1 h.

**Contexto:** o ESP32 do Wokwi roda na nuvem e não enxerga o `localhost` de
ninguém. O `test.mosquitto.org` é uma instância **pública do próprio
Mosquitto**: o ESP32 publica lá e o Mosquitto local importa as mensagens por
*bridge* — então o backend continua falando só com um Mosquitto, e a exigência
da disciplina segue cumprida.

**Feito em 01/09/2026.** A bridge sobe junto com o container (não está mais
comentada) e foi testada: mensagem publicada no broker público chegou no local,
sem laço de reconexão. Prefixo do grupo: `tankvitals-unifacef-g3`.

```
ESP32 (Wokwi) --publish--> test.mosquitto.org:1883 --bridge--> Mosquitto local --> backend
```

**Passo a passo**

1. **Escolha um prefixo de tópico único** — ex.: `tankvitals-unifacef-g3`. O
   broker público é aberto: com o prefixo genérico `tankvitals`, outro grupo (ou
   um curioso qualquer) publica lixo no seu tópico no meio da apresentação.
2. Acrescente ao `mosquitto.conf` **local** o bloco de bridge:
   ```
   connection wokwi-bridge
   address test.mosquitto.org:1883
   topic <SEU_PREFIXO>/# in 0
   bridge_protocol_version mqttv311
   cleansession true
   try_private false
   notifications false
   ```
   - `in` = só importa mensagens, não reexporta as suas para o mundo;
   - `try_private false` é necessário porque o broker público não é seu.
3. `docker compose restart mosquitto` e acompanhe
   `docker compose logs -f mosquitto` — a bridge deve conectar e ficar estável,
   sem laço de reconexão.
4. Teste com dois terminais: `mosquitto_sub` no broker **local** e
   `mosquitto_pub` no **público**, no mesmo tópico. A mensagem tem que
   atravessar.
5. Documente quais linhas do `sketch.ino` mudam entre um cenário e outro
   (`MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASS`) — no dia, é só trocar e
   recompilar no Wokwi.

**Critério de aceite**

- [x] Prefixo único escolhido e registrado na ARQUITETURA §2.1.
- [x] Bridge conectada e estável no log do Mosquitto.
- [x] Mensagem publicada no broker público chega no local.
- [x] Os três lugares onde o prefixo precisa bater estão documentados.

**Ref.:** ARQUITETURA §7. **Peso:** seguro contra perder 2,0 pts.

---

### INFRA-05 — Validação ponta a ponta da infraestrutura

**Objetivo:** provar que os elos 1 a 3 da corrente funcionam antes do backend existir.
**Depende de:** INFRA-04, FW-03.
**Entrega:** seção "Como validar" no `infra/README.md` com a saída esperada.
**Estimativa:** 30 min.

**Passo a passo**

1. Suba a infra e rode o projeto no Wokwi.
2. Com `mosquitto_sub -h localhost -t '<SEU_PREFIXO>/#' -v`, confirme que chega
   uma mensagem a cada 5 segundos.
3. Confirme o tópico de status: pare a simulação do Wokwi e veja o broker
   publicar `offline` sozinho (é o Last Will agindo).
4. Cole no `infra/README.md` um trecho real da saída — isso vira prova de
   funcionamento na apresentação.

**Critério de aceite**

- [ ] Telemetria chegando no broker local a cada ~5 s.
- [ ] `status` = `online` ao iniciar e `offline` ao parar a simulação.
- [ ] Saída real colada na documentação.

**Ref.:** ARQUITETURA §10. **Peso:** parte de 2,0 pts.

---

## Frente FW — Firmware ESP32 / Wokwi

> Meta da frente: o ESP32 simulado lê 4 sensores e publica JSON válido no MQTT.
> **Peso: 1,5 pts (dispositivo) + parte de 2,0 pts (MQTT).**

### FW-01 — Montar o circuito no Wokwi ✅

**Objetivo:** ter o projeto no Wokwi com todas as peças ligadas corretamente.
**Depende de:** nada.
**Entrega:** `firmware/wokwi/diagram.json`, link do projeto no `README.md`.
**Estimativa:** 2 h.

**Passo a passo**

1. Crie um projeto novo em [wokwi.com](https://wokwi.com) → **ESP32 → Arduino**.
   Confira que é Arduino mesmo: se o projeto for criado como ESP-IDF, o
   `diagram.json` fica com `"builder": "esp-idf"` na placa e as bibliotecas do
   `libraries.txt` não funcionam.
   A placa usada é a `board-esp32-devkit-c-v4`, em que os pinos são nomeados
   pelo número do GPIO (`esp:4`, `esp:19`, `esp:34`...).
2. Adicione as peças pelo botão **+** e faça as ligações desta tabela:

   | Peça (nome no Wokwi) | Pino da peça | Pino do ESP32 | Observação |
   | --- | --- | --- | --- |
   | `DS18B20` (temperatura) | VCC | 3V3 | |
   | | GND | GND | |
   | | DQ | **D4** | |
   | `Resistor` **4,7 kΩ** | um lado no DQ do DS18B20 | outro lado no 3V3 | *pull-up* do barramento 1-Wire |
   | `HC-SR04` (ultrassônico) | VCC | 3V3 | |
   | | TRIG | **D5** | |
   | | ECHO | **D18** | |
   | | GND | GND | |
   | `Potentiometer` (faz o papel da sonda de pH) | VCC | 3V3 | |
   | | GND | GND | |
   | | SIG | **D34** | |
   | `Photoresistor (LDR)` (faz o papel do sensor de turbidez) | VCC | 3V3 | |
   | | GND | GND | |
   | | AO | **D35** | a saída digital DO não é usada |
   | `Resistor` **220 Ω** | um lado no **GPIO 19** | outro lado no ânodo (A) do LED | limita a corrente do LED |
   | `LED` vermelho (alerta local) | ânodo (A) | resistor 220 Ω → **GPIO 19** | |
   | | cátodo (C) | GND | |

   > São **dois** resistores diferentes, com funções diferentes: o de 4,7 kΩ é o
   > pull-up do DS18B20 e o de 220 Ω é do LED. Usar um só, ligando o LED direto
   > no 3V3, deixa o LED aceso o tempo todo e o GPIO 19 sem controlar nada — e
   > ainda deixa o sensor de temperatura sem pull-up.

   > **Por que D34 e D35 e não outro pino qualquer:** o ESP32 tem dois
   > conversores analógicos e o **ADC2 é usado pelo rádio Wi-Fi**. Ler ADC2 com
   > Wi-Fi ligado devolve lixo. Só GPIO 32–39 (ADC1) servem para sensor
   > analógico neste projeto. Esse é um erro clássico de prova.

3. Copie o conteúdo da aba `diagram.json` do Wokwi para
   `firmware/wokwi/diagram.json` no repositório.
4. Confira a fiação antes de seguir: cole `firmware/wokwi/sketch.ino` no
   projeto e rode. Ele lê os 4 sensores e imprime no monitor serial. Mexendo no
   potenciômetro, na luz do LDR e na distância do HC-SR04, os valores impressos
   têm que acompanhar.
5. Coloque o link público do projeto Wokwi no `README.md`.

**Critério de aceite**

- [x] As 7 peças (mais a placa) estão no circuito e ligadas conforme a tabela.
- [x] O LED só acende quando o firmware manda, não fica aceso sozinho.
- [x] `diagram.json` versionado e idêntico ao do Wokwi.
- [x] A simulação inicia sem aviso de fiação no console do Wokwi.

**Peso:** 1,5 pts (dispositivo IoT).

---

### FW-02 — Leitura dos sensores ✅

**Objetivo:** as 4 grandezas sendo lidas e impressas no monitor serial.
**Depende de:** FW-01.
**Entrega:** `firmware/wokwi/sketch.ino`, `firmware/wokwi/libraries.txt`.
**Estimativa:** 4 h.

**Passo a passo**

1. No Wokwi, adicione as bibliotecas (aba **Library Manager**): `OneWire` e
   `DallasTemperature` agora; `PubSubClient` e `ArduinoJson` entram na FW-03 e
   na FW-04. Isso gera o `libraries.txt` — versione esse arquivo também.
2. No `setup()`: `Serial.begin(115200)`, `pinMode` do TRIG (saída), ECHO
   (entrada) e LED (saída), `analogReadResolution(12)`,
   `analogSetAttenuation(ADC_11db)` e `dallas.begin()`.
3. Implemente uma função por sensor, cada uma devolvendo `float` (e `NAN` quando
   a leitura falhar):

   | Função | Como calcular |
   | --- | --- |
   | temperatura | `DallasTemperature::requestTemperatures()` + `getTempCByIndex(0)`; valor ≤ −100 significa sensor ausente → devolva `NAN` |
   | pH | `analogRead(34)` devolve 0–4095 → converta linearmente para 0–14 |
   | turbidez | `analogRead(35)` 0–4095 → **invertido** para 0–100 NTU (mais luz = água mais limpa = menos NTU) |
   | nível | pulso de 10 µs no TRIG, `pulseIn(ECHO, HIGH, 30000)`, distância = `duração × 0,0343 / 2`; converta distância em percentual usando cheio = 5 cm e vazio = 45 cm, limitando entre 0 e 100; timeout (`pulseIn` = 0) → `NAN` |

4. No `loop()`, imprima as quatro leituras no serial a cada 5 s.
5. Valide movendo o potenciômetro e o slider do LDR na simulação: os valores
   impressos têm que acompanhar.

**Critério de aceite**

- [x] Monitor serial mostra as 4 grandezas (hoje a cada 1 s; a FW-04 troca o
      `delay()` por `millis()` e fixa o intervalo em 5 s).
- [x] Mexer no potenciômetro muda o pH; mexer na luz do LDR muda a turbidez.
- [x] Mudar a distância do HC-SR04 muda o nível, sempre entre 0 e 100 %.
- [x] Sensor removido do circuito devolve `NAN` em vez de travar o firmware.

**Ref.:** ARQUITETURA §3. **Peso:** parte de 1,5 pts.

---

### FW-03 — Wi-Fi, NTP e conexão MQTT ⚠️ escrito, falta validar no Wokwi

**Objetivo:** o ESP32 conectar no broker e se manter conectado.
**Depende de:** FW-02, INFRA-04 (para saber o endereço e o prefixo de tópico).
**Entrega:** `sketch.ino` atualizado.
**Estimativa:** 3 h.

**Passo a passo**

1. Conecte no Wi-Fi virtual do Wokwi: SSID `Wokwi-GUEST`, senha vazia, **canal
   6** (`WiFi.begin(ssid, pass, 6)` — informar o canal acelera muito a conexão
   dentro do simulador).
2. Sincronize a hora com `configTime(0, 0, "pool.ntp.org", "time.nist.gov")`.
   Sem isso o dispositivo não tem data válida e o backend usará o horário do
   servidor (o que também funciona, mas perde precisão).
3. Configure o cliente MQTT com `PubSubClient`:
   - `setServer(MQTT_HOST, 1883)`, `setBufferSize(512)` (o padrão de 256 bytes
     **corta o JSON** e a publicação falha silenciosamente), `setKeepAlive(30)`;
   - **clientId único** — concatene algo do MAC (`ESP.getEfuseMac()`). Dois
     clientes com o mesmo id derrubam um ao outro em laço infinito de
     reconexão;
   - **usuário e senha** — o broker público aceita conexão anônima, então os
     dois ficam vazios; o `mqtt.connect()` recebe `NULL` nesse caso. Se um dia
     o broker pedir credencial e o rc vier `5` (`not authorised`), é credencial
     errada, não problema de rede;
   - **Last Will**: tópico `<PREFIXO>/<tank_id>/status`, QoS 1, retained,
     payload `offline`.
4. Assim que conectar, publique `online` (retained) no tópico de status.
5. No `loop()`, reconecte Wi-Fi e MQTT se caírem, e chame `mqtt.loop()` sempre.

**Critério de aceite**

- [ ] Serial mostra o IP obtido e "conectado" no MQTT em menos de 15 s.
- [ ] `mosquitto_sub` no broker recebe `online` no tópico de status.
- [ ] Parar a simulação faz o broker publicar `offline` (Last Will).
- [ ] Reiniciar o broker não deixa o ESP32 travado — ele reconecta sozinho.

**Ref.:** ARQUITETURA §2.1, §7. **Peso:** parte de 2,0 pts (MQTT).

---

### FW-04 — Montar e publicar o payload JSON ⚠️ escrito, falta validar no Wokwi

**Objetivo:** publicar telemetria no formato exato do contrato.
**Depende de:** FW-03.
**Entrega:** `sketch.ino` atualizado.
**Estimativa:** 2 h.

**Passo a passo**

1. Com `ArduinoJson` (`StaticJsonDocument<320>`), monte o objeto com os campos
   da ARQUITETURA §2.2: `device_id`, `tank_id`, `fw`, `seq` (contador que
   incrementa a cada envio), `uptime_s` (`millis()/1000`), `rssi`
   (`WiFi.RSSI()`), `ts` (epoch em segundos) e as grandezas.
2. **Só inclua a grandeza no JSON se ela não for `NAN`** — campo ausente é o
   contrato para "sensor falhou"; enviar `NaN` gera JSON inválido e o backend
   descarta a mensagem inteira.
3. **Só inclua `ts` se `time(nullptr) > 1700000000`**, isto é, se o NTP já
   sincronizou. Antes disso o relógio marca 1970 e os pontos iriam parar em
   1970 no gráfico.
4. Arredonde: 2 casas para temperatura e pH, 1 casa para nível, distância e
   turbidez.
5. Publique em `<PREFIXO>/<tank_id>/telemetry` a cada 5 s usando `millis()`
   (**não use `delay()`** no loop — ele trava o `mqtt.loop()` e derruba a conexão).
6. Imprima no serial o JSON publicado, para conferência e para a apresentação.

**Critério de aceite**

- [ ] JSON recebido no `mosquitto_sub` bate campo a campo com a ARQUITETURA §2.2.
- [ ] Nenhum campo com `NaN`, `null` ou `inf`.
- [ ] `seq` incrementa de 1 em 1 sem pular.
- [ ] Intervalo de publicação estável em ~5 s, sem `delay()` no loop.
- [ ] O JSON passa em um validador (ex.: `jq .` no terminal).

**Ref.:** ARQUITETURA §2.2, §3. **Peso:** parte de 1,5 + 2,0 pts.

---

### FW-05 — Alerta local e assinatura de comandos ⚠️ escrito, falta validar no Wokwi

**Objetivo:** LED indicando anomalia e o gancho de atuação do 2º bimestre pronto.
**Depende de:** FW-04.
**Entrega:** `sketch.ino` finalizado (v1.0.0).
**Estimativa:** 2 h.

**Passo a passo**

1. Implemente a checagem binária das faixas seguras da ARQUITETURA §5 (só a
   coluna "ok"): temperatura 24–28 °C, pH 6,5–8,0, nível ≥ 30 %, turbidez < 40.
2. Qualquer grandeza fora da faixa acende o LED do D19; todas dentro, apaga.
   Grandeza ausente (`NAN`) **não** conta como alerta.
3. Assine `<PREFIXO>/<tank_id>/cmd` e trate o comando `{"interval_s": N}`,
   aceitando N entre 1 e 300 e alterando o intervalo de publicação em tempo de
   execução. Ignore qualquer outro conteúdo.
4. Teste o comando:
   `mosquitto_pub -h localhost -t '<PREFIXO>/tanque-01/cmd' -m '{"interval_s":2}'`
   → a publicação passa a sair a cada 2 s.

**Critério de aceite**

- [ ] Girar o potenciômetro para pH 3 acende o LED; voltar para 7 apaga.
- [ ] Comando de intervalo funciona e é rejeitado fora da faixa 1–300.
- [ ] JSON malformado no tópico `/cmd` não derruba o firmware.

**Ref.:** ARQUITETURA §2.3, §5. **Peso:** parte de 1,5 pts + base do 2º bimestre.

---

## Frente BE — Backend Python

> Meta da frente: consumir MQTT, validar, gravar no InfluxDB e servir a API.
> **Peso: 1,0 pt (backend) + 1,5 pts (persistência) + habilita 1,0 pt (dashboard).**

### BE-01 — Esqueleto do projeto e configuração

**Objetivo:** projeto Python instalável, lendo configuração do `.env`.
**Depende de:** INFRA-02.
**Entrega:** `backend/requirements.txt`, `backend/app/config.py`, `backend/app/__init__.py`, `backend/README.md`.
**Estimativa:** 2 h.

**Passo a passo**

1. `python -m venv .venv` dentro de `backend/` e ative
   (`.venv\Scripts\activate` no Windows).
2. `requirements.txt` com: `fastapi`, `uvicorn[standard]`, `paho-mqtt`,
   `influxdb-client`, `pydantic`, `pydantic-settings`, `python-dotenv`,
   `pytest`, `httpx`. **Fixe as versões** (`==`) para o ambiente não quebrar
   entre os computadores da equipe.
3. Estrutura de pastas:
   ```
   backend/
   ├─ app/
   │  ├─ __init__.py
   │  ├─ config.py       <- BE-01
   │  ├─ models.py       <- BE-02
   │  ├─ alerts.py       <- BE-03
   │  ├─ influx_repo.py  <- BE-04 e BE-06
   │  ├─ mqtt_ingestor.py<- BE-05
   │  ├─ api.py          <- BE-07 e BE-08
   │  └─ main.py         <- BE-07
   ├─ tools/             <- BE-09
   └─ tests/             <- BE-09
   ```
4. Em `config.py`, use `pydantic-settings` (`BaseSettings`) lendo o `.env` da
   raiz do projeto. Exponha **todas** as chaves da ARQUITETURA §8, inclusive os
   limites das faixas seguras.
5. Nada de valor fixo no meio do código: se é configurável, mora no `config.py`.

**Critério de aceite**

- [ ] `pip install -r requirements.txt` funciona do zero em outra máquina.
- [ ] `python -c "from app.config import settings; print(settings.influx_bucket)"` imprime `tankvitals`.
- [ ] Faltando `INFLUX_TOKEN` no `.env`, a aplicação falha na inicialização com
      mensagem clara (não com erro genérico lá na frente).

**Ref.:** ARQUITETURA §8. **Peso:** parte de 1,0 pt.

---

### BE-02 — Modelo e validação do payload

**Objetivo:** transformar JSON cru do MQTT em objeto validado, ou rejeitá-lo.
**Depende de:** BE-01.
**Entrega:** `backend/app/models.py`.
**Estimativa:** 3 h.

**Passo a passo**

1. Crie um modelo Pydantic `SensorReading` com todos os campos da ARQUITETURA §3,
   respeitando obrigatoriedade e tipos.
2. Aplique os limites de faixa **válida** (não confundir com faixa **segura**):
   temperatura −10..60, pH 0..14, nível 0..100, distância 0..400,
   turbidez 0..1000.
3. Implemente as 6 regras de validação da ARQUITETURA §3:
   - JSON inválido → rejeita;
   - sem `device_id`/`tank_id` → rejeita;
   - grandeza fora da faixa válida → **descarta só aquele campo**, mantém o resto;
   - nenhuma grandeza sobrando → rejeita;
   - `ts` ausente ou ≤ 1700000000 → usa o horário do servidor;
   - `tank_id` divergente do tópico → vale o do tópico, registra `WARNING`.
4. Exponha uma função tipo `parse_reading(topic: str, payload: bytes) -> SensorReading | None`.

**Critério de aceite**

- [ ] Payload de exemplo da ARQUITETURA §2.2 é aceito e todos os campos batem.
- [ ] `b"nao e json"` devolve `None` sem lançar exceção.
- [ ] Payload com `"ph": 99` mantém as outras grandezas e descarta só o pH.
- [ ] Payload só com `device_id` e `tank_id` é rejeitado.
- [ ] Testes automatizados cobrindo os 4 casos acima (entregues na BE-09).

**Ref.:** ARQUITETURA §3. **Peso:** parte de 1,0 pt.

---

### BE-03 — Regra de alerta em três níveis

**Objetivo:** classificar cada leitura em `ok` / `atencao` / `critico`.
**Depende de:** BE-02.
**Entrega:** `backend/app/alerts.py`.
**Estimativa:** 2 h.

**Passo a passo**

1. Implemente a tabela de faixas da ARQUITETURA §5, lendo os limites do
   `config.py` (nada de número solto no código).
2. Classifique **cada grandeza** individualmente e depois calcule o **nível
   geral da leitura como o pior** entre as grandezas presentes.
3. Grandeza ausente não entra no cálculo (não é `critico`, é ausente).
4. Cuidado com as bordas: exatamente 24,0 °C é `ok`; 23,9 é `atencao`.

**Critério de aceite**

- [ ] Leitura toda dentro da faixa → geral `ok`.
- [ ] Temperatura 29 °C com o resto normal → geral `atencao`.
- [ ] pH 5,5 → geral `critico`, mesmo com o resto `ok`.
- [ ] Valores exatamente nos limites classificados conforme a tabela §5.
- [ ] Testes automatizados dos casos de borda (BE-09).

**Ref.:** ARQUITETURA §5. **Peso:** parte de 1,0 pt.

---

### BE-04 — Escrita no InfluxDB

**Objetivo:** persistir cada leitura válida como ponto na série temporal.
**Depende de:** BE-02, INFRA-02.
**Entrega:** `backend/app/influx_repo.py` (parte de escrita).
**Estimativa:** 3 h.

**Passo a passo**

1. Crie o cliente com `InfluxDBClient(url, token, org)` do pacote `influxdb-client`.
2. Monte o `Point` com measurement `water_reading`, as **tags** `tank_id`,
   `device_id`, `fw` e os **fields** numéricos — exatamente como na
   ARQUITETURA §4.
3. Escreva com `WritePrecision.NS` e o timestamp da leitura.
4. Use `write_api(write_options=SYNCHRONOUS)` — o modo em lote (*batching*) é
   mais rápido, mas na apresentação o dado precisa aparecer no gráfico na hora,
   e um erro de escrita em lote passa despercebido.
5. Trate a exceção de escrita: **logar e seguir**, nunca derrubar o ingestor
   porque o banco piscou.
6. Campos ausentes na leitura simplesmente não viram field (não grave `0` — zero
   é um valor legítimo de sensor e falsificaria o gráfico).

**Critério de aceite**

- [ ] Uma leitura publicada no MQTT aparece no Data Explorer do InfluxDB.
- [ ] Tags e fields exatamente com os nomes da ARQUITETURA §4.
- [ ] Parar o InfluxDB não derruba o backend; ele volta a gravar quando o banco volta.
- [ ] Leitura sem `ph` grava o ponto sem o field `ph` (e não com `ph=0`).

**Ref.:** ARQUITETURA §4. **Peso:** 1,5 pts (persistência).

---

### BE-05 — Ingestor MQTT

**Objetivo:** o serviço que fecha o elo MQTT → InfluxDB.
**Depende de:** BE-02, BE-04, INFRA-04.
**Entrega:** `backend/app/mqtt_ingestor.py`.
**Estimativa:** 4 h.

**Passo a passo**

1. Cliente `paho-mqtt` conectando no broker **local** com os dados do `config.py`.
2. Assine com curinga: `<PREFIXO>/+/telemetry` e `<PREFIXO>/+/status`.
3. No callback de mensagem:
   - se o tópico termina em `/telemetry` → `parse_reading()` → classifica
     (BE-03) → grava (BE-04) → registra em log resumido;
   - se termina em `/status` → atualiza em memória o estado online/offline do tanque.
4. Reconexão automática: use `reconnect_delay_set()` e `loop_start()` (o cliente
   roda em *thread* própria).
5. Log informativo, não verborrágico: uma linha por leitura gravada, com
   `tank_id` e as grandezas. `WARNING` para payload descartado, dizendo o motivo.
6. Guarde a última leitura de cada tanque em memória — a API usa isso para
   responder rápido e para o WebSocket (BE-08).

**Critério de aceite**

- [ ] Com o Wokwi rodando, o log mostra uma leitura gravada a cada ~5 s.
- [ ] Payload lixo publicado à mão gera `WARNING` e **não** derruba o serviço.
- [ ] Derrubar e subir o Mosquitto: o ingestor reconecta sozinho e volta a gravar.
- [ ] Mensagem `offline` no tópico de status é refletida no estado em memória.

**Ref.:** ARQUITETURA §2.1, §3. **Peso:** 1,0 pt (backend) + reforça 2,0 pts (MQTT).

---

### BE-06 — Consultas ao InfluxDB

**Objetivo:** ler do banco o que o dashboard precisa mostrar.
**Depende de:** BE-04.
**Entrega:** `backend/app/influx_repo.py` (parte de leitura).
**Estimativa:** 4 h.

**Passo a passo**

1. Implemente quatro consultas Flux (use as de referência da ARQUITETURA §4):

   | Função | O que devolve |
   | --- | --- |
   | `get_latest(tank_id)` | última leitura, com `pivot` para virar uma linha só |
   | `get_history(tank_id, range, window, metrics)` | série agregada com `aggregateWindow(fn: mean)` |
   | `get_stats(tank_id, range)` | mín, máx, média e último valor por grandeza |
   | `list_tanks()` | valores distintos da tag `tank_id` e quando cada um foi visto |

2. **Valide `range` e `window` contra uma lista fixa** (`1h`, `6h`, `24h`, `7d`
   / `10s`, `1m`, `5m`, `1h`). Nunca interpole string vinda do usuário direto na
   query Flux — é injeção de consulta.
3. Aplique a janela automática por período (ARQUITETURA §6): `1h→10s`,
   `6h→1m`, `24h→5m`, `7d→1h`.
4. Converta os timestamps para ISO 8601 UTC com `Z` no fim.

**Critério de aceite**

- [ ] `get_latest` devolve a leitura mais recente com todas as grandezas presentes.
- [ ] `get_history` com `range=6h` devolve no máximo ~360 pontos por grandeza.
- [ ] `range=abc` é rejeitado com erro claro, sem chegar no banco.
- [ ] Tanque sem dado nenhum devolve estrutura vazia, não exceção.

**Ref.:** ARQUITETURA §4, §6. **Peso:** parte de 1,5 pts + habilita o dashboard.

---

### BE-07 — API REST

**Objetivo:** expor os endpoints que o frontend consome.
**Depende de:** BE-03, BE-06.
**Entrega:** `backend/app/api.py`, `backend/app/main.py`.
**Estimativa:** 4 h.

**Passo a passo**

1. Aplicação FastAPI com os 6 endpoints REST da ARQUITETURA §6, respondendo
   **exatamente** naqueles formatos de JSON.
2. Habilite CORS para `http://localhost:5173` (porta padrão do Vite), lendo a
   lista do `config.py`. Sem isso o navegador bloqueia toda chamada do front.
3. Use o `lifespan` do FastAPI para iniciar o ingestor MQTT (BE-05) junto com a
   API e encerrá-lo no *shutdown*.
4. `/api/health` deve checar de verdade: ping no InfluxDB e estado da conexão
   MQTT. Um health que sempre responde "ok" não serve para nada.
5. Padronize os erros como `{"detail": "..."}` com 400 / 404 / 503.
6. Confira a documentação automática em `http://localhost:8000/docs` — ela é
   ótima para demonstrar o backend na apresentação.

**Critério de aceite**

- [ ] `uvicorn app.main:app --reload` sobe API e ingestor no mesmo processo.
- [ ] Os 6 endpoints respondem no formato da ARQUITETURA §6.
- [ ] `/api/health` acusa `degraded`/503 quando o InfluxDB está parado.
- [ ] `/docs` abre e permite testar cada rota.
- [ ] Chamada a partir do front (`localhost:5173`) não é bloqueada por CORS.

**Ref.:** ARQUITETURA §6. **Peso:** 1,0 pt (backend).

---

### BE-08 — WebSocket ao vivo

**Objetivo:** o dashboard atualizar sozinho, sem o usuário recarregar a página.
**Depende de:** BE-05, BE-07.
**Entrega:** rota `/ws/live` em `backend/app/api.py`.
**Estimativa:** 3 h.

**Passo a passo**

1. Crie um gerenciador de conexões WebSocket (lista de clientes conectados, com
   remoção no *disconnect*).
2. O ingestor roda em *thread* do paho, e o FastAPI em `asyncio`: para cruzar
   essa fronteira use `asyncio.run_coroutine_threadsafe(...)` com o *event loop*
   capturado no `lifespan`. Chamar `await` direto da thread do paho **não
   funciona** e é o erro mais comum aqui.
3. Envie as mensagens nos dois formatos da ARQUITETURA §6 (`reading` e `status`).
4. Cliente que caiu deve ser removido da lista sem quebrar o envio para os outros.

**Critério de aceite**

- [ ] Duas abas do navegador abertas recebem a mesma leitura ao mesmo tempo.
- [ ] Fechar uma aba não afeta a outra nem gera erro no log do servidor.
- [ ] Mensagem chega em menos de 1 s depois da publicação MQTT.

**Ref.:** ARQUITETURA §6. **Peso:** parte de 1,0 pt (dashboard).

---

### BE-09 — Testes automatizados e simulador de dispositivo

**Objetivo:** poder desenvolver e demonstrar sem depender do Wokwi aberto.
**Depende de:** BE-02, BE-03.
**Entrega:** `backend/tests/`, `backend/tools/fake_device.py`.
**Estimativa:** 3 h.

**Passo a passo**

1. `tools/fake_device.py`: publica no mesmo tópico do ESP32 um JSON idêntico ao
   contrato, com valores oscilando de forma realista (ex.: temperatura passeando
   entre 24 e 29 °C). Aceite argumentos de linha de comando para intervalo e
   para forçar uma anomalia — isso serve para **demonstrar o alerta na
   apresentação sem precisar mexer no potenciômetro**.
2. `tests/test_models.py`: os 4 casos de aceite da BE-02.
3. `tests/test_alerts.py`: os casos de borda da BE-03.
4. `tests/test_api.py`: usando `TestClient` do FastAPI, verifique o formato de
   resposta de `/api/health` e `/api/readings/latest`.

> Deixe claro no `backend/README.md` que o simulador é ferramenta de
> desenvolvimento — **na avaliação quem publica é o ESP32 do Wokwi**. A rubrica
> exige o dispositivo funcionando.

**Critério de aceite**

- [ ] `pytest` passa 100 % verde.
- [ ] `python tools/fake_device.py` alimenta o dashboard com o Wokwi desligado.
- [ ] O simulador consegue forçar um cenário de alerta sob demanda.

**Peso:** rede de segurança para 1,0 + 1,5 pts.

---

## Frente FE — Frontend Vue 3

> Meta da frente: dashboard mostrando estado atual, histórico em gráfico e alertas.
> **Peso: 2,0 pts (Vue+TS+Vite) + 1,0 pt (gráfico/indicador).**

### FE-01 — Scaffold Vue 3 + TypeScript + Vite

**Objetivo:** projeto criado, rodando e com TypeScript de verdade.
**Depende de:** nada.
**Entrega:** `frontend/` completo com `package.json`, `vite.config.ts`, `tsconfig.json`.
**Estimativa:** 2 h.

**Passo a passo**

1. `npm create vite@latest frontend -- --template vue-ts`
2. `npm install` e depois `npm install chart.js vue-chartjs`.
3. No `tsconfig.json`, mantenha `"strict": true`. Não é frescura: o critério da
   disciplina é *usar* TypeScript, e um projeto cheio de `any` não demonstra uso.
4. Em `vite.config.ts`, configure o *proxy* de desenvolvimento para o backend,
   evitando dor de cabeça com CORS:
   ```ts
   server: {
     proxy: {
       '/api': 'http://localhost:8000',
       '/ws':  { target: 'ws://localhost:8000', ws: true }
     }
   }
   ```
5. Crie `frontend/.env` com `VITE_API_BASE_URL=` (vazio usa o proxy) e o
   `.env.example` correspondente.

**Critério de aceite**

- [ ] `npm run dev` abre em `http://localhost:5173`.
- [ ] `npm run build` conclui **sem nenhum erro de tipo**.
- [ ] `strict: true` ativo e nenhum `any` explícito no código entregue.

**Peso:** parte de 2,0 pts.

---

### FE-02 — Tipos e cliente da API

**Objetivo:** todo dado vindo do backend chega tipado no front.
**Depende de:** FE-01.
**Entrega:** `frontend/src/types.ts`, `frontend/src/api/client.ts`.
**Estimativa:** 3 h.

**Passo a passo**

1. Em `types.ts`, declare as interfaces espelhando a ARQUITETURA §6:
   `AlertLevel` (`'ok' | 'atencao' | 'critico'`), `MetricValue`, `LatestReading`,
   `HistorySeries`, `Stats`, `Thresholds`, `TankInfo`.
2. Em `api/client.ts`, uma função por endpoint, tipada no retorno
   (`getLatest(tankId): Promise<LatestReading>`).
3. Trate erro de rede num lugar só: função que verifica `response.ok`, extrai o
   campo `detail` e lança um `Error` com mensagem legível para a interface.
4. Enquanto a API da BE-07 não existir, use os JSONs de exemplo da ARQUITETURA §6
   como *mock* — assim a frente não fica parada.

**Critério de aceite**

- [ ] Nenhum `any` nas assinaturas.
- [ ] Erro 503 do backend vira mensagem amigável, não tela branca.
- [ ] Trocar `VITE_API_BASE_URL` muda o destino das chamadas sem editar código.

**Ref.:** ARQUITETURA §6. **Peso:** parte de 2,0 pts.

---

### FE-03 — Dados ao vivo (WebSocket com plano B)

**Objetivo:** a tela se atualizar sozinha e nunca ficar congelada sem avisar.
**Depende de:** FE-02.
**Entrega:** `frontend/src/composables/useLiveReadings.ts`.
**Estimativa:** 4 h.

**Passo a passo**

1. *Composable* que conecta em `/ws/live`, guarda a última leitura em um `ref`
   e expõe também o estado da conexão.
2. Reconexão automática com espera crescente (1 s, 2 s, 4 s… até 30 s). Não
   tente reconectar em laço apertado: trava a aba do navegador.
3. **Fallback:** se o WebSocket falhar duas vezes seguidas, passe a consultar
   `/api/readings/latest` a cada 5 s e mostre isso na interface.
4. Limpe tudo no `onUnmounted` (fechar socket, cancelar `setInterval`) —
   *listener* vazando é a causa clássica de a página ficar lenta com o tempo.
5. Marque o tanque como "sem sinal" se a última leitura tiver mais de 30 s.

**Critério de aceite**

- [ ] Card atualiza sozinho a cada nova publicação do ESP32.
- [ ] Derrubar o backend mostra "desconectado"; subir de novo reconecta sem F5.
- [ ] Trocar de página e voltar não deixa conexão órfã (confira no DevTools).

**Ref.:** ARQUITETURA §6. **Peso:** parte de 2,0 pts.

---

### FE-04 — Layout e barra de status

**Objetivo:** o esqueleto visual do dashboard.
**Depende de:** FE-03.
**Entrega:** `frontend/src/App.vue`, `components/StatusBar.vue`, `src/assets/main.css`.
**Estimativa:** 3 h.

**Passo a passo**

1. Layout em uma página: cabeçalho → linha de indicadores → gráfico → histórico.
2. A barra de status mostra: nome do tanque, `online`/`offline`, "última leitura
   há X s" e o estado da conexão ao vivo (WebSocket ou *polling*).
3. Defina as cores dos três níveis **como variáveis CSS em um lugar só**:
   `--nivel-ok`, `--nivel-atencao`, `--nivel-critico`. Todos os componentes usam
   essas variáveis.
4. Não use **só cor** para indicar estado — acrescente ícone ou texto
   ("Atenção", "Crítico"). Cor sozinha exclui quem tem daltonismo, e isso costuma
   contar como qualidade de interface.
5. Responsivo: os indicadores empilham em tela estreita.

**Critério de aceite**

- [ ] Dashboard legível em 1366×768 e em tela de celular.
- [ ] Estado offline visualmente distinto (não some da tela, fica marcado).
- [ ] Nenhum valor de cor escrito solto dentro dos componentes.

**Peso:** parte de 2,0 pts.

---

### FE-05 — Indicadores (cards) das 4 grandezas

**Objetivo:** bater o olho e saber se o tanque está bem.
**Depende de:** FE-04.
**Entrega:** `frontend/src/components/MetricCard.vue`.
**Estimativa:** 3 h.

**Passo a passo**

1. Componente reutilizável com `props` tipadas: rótulo, valor, unidade, nível e
   faixa segura.
2. Mostre valor grande, unidade, faixa segura em texto pequeno ("ideal: 24–28 °C")
   e a marcação do nível.
3. Formate número em pt-BR (vírgula decimal) e com casas decimais fixas — valor
   pulando de 26,4 para 26,44444 dá impressão de instabilidade.
4. Grandeza ausente mostra "—", nunca `NaN`, `null` ou `undefined`.
5. Os quatro cards vêm da mesma resposta de `/api/readings/latest`.

**Critério de aceite**

- [ ] Os 4 cards renderizados a partir de dados reais.
- [ ] Card muda de cor **e** de rótulo quando a grandeza sai da faixa.
- [ ] Sem dado nenhum, mostra "—" e não quebra o layout.

**Ref.:** ARQUITETURA §5, §6. **Peso:** parte de 1,0 pt (indicador).

---

### FE-06 — Gráfico do histórico (Chart.js)

**Objetivo:** o item mais cobrado da rubrica — visualizar a série temporal.
**Depende de:** FE-05, BE-06.
**Entrega:** `components/HistoryChart.vue`, `components/RangeSelector.vue`, `composables/useHistory.ts`.
**Estimativa:** 5 h.

**Passo a passo**

1. Gráfico de linha com `vue-chartjs`, consumindo `/api/readings/history`.
2. Seletor de período: 1 h, 6 h, 24 h, 7 d. Trocar o período refaz a consulta
   (a janela de agregação o backend escolhe sozinho).
3. Seletor de grandeza: mostrar uma por vez **ou** permitir comparar duas com
   dois eixos Y. Uma por vez é mais legível — só não misture escalas diferentes
   (°C e NTU) no mesmo eixo, o gráfico fica sem sentido.
4. Eixo X como escala de tempo, com rótulo em hora local e formato que muda
   conforme o período (`HH:mm` em 1 h; `dd/MM HH:mm` em 7 d).
5. Marque visualmente a faixa segura — uma banda de fundo ou duas linhas
   tracejadas nos limites. É isso que transforma o gráfico em informação: o
   avaliador vê na hora se o valor está onde deveria.
6. Estados explícitos: carregando, vazio ("sem dados no período") e erro.
7. Cuidado clássico: **atualize os dados do gráfico em vez de recriar a
   instância do Chart** a cada leitura nova, senão a memória cresce sem parar e
   a animação fica engasgada.

**Critério de aceite**

- [ ] Gráfico desenha a série real vinda do InfluxDB.
- [ ] Trocar de período recarrega os dados corretamente.
- [ ] Faixa segura visível no gráfico.
- [ ] Período sem dados mostra mensagem, não gráfico quebrado.
- [ ] Deixar a página aberta por 10 min não degrada o desempenho.

**Ref.:** ARQUITETURA §6. **Peso:** 1,0 pt (gráfico) — **item cheio da rubrica**.

---

### FE-07 — Histórico em tabela e painel de alertas

**Objetivo:** ver os números crus e o que saiu da faixa.
**Depende de:** FE-06.
**Entrega:** `components/HistoryTable.vue`, `components/AlertsPanel.vue`.
**Estimativa:** 3 h.

**Passo a passo**

1. Tabela com as últimas ~50 leituras: horário e as 4 grandezas, mais recente
   no topo, com a linha marcada quando o nível não for `ok`.
2. Painel de alertas listando as ocorrências fora da faixa no período
   selecionado (grandeza, valor, horário, nível).
3. Data e hora em formato pt-BR (`dd/MM/yyyy HH:mm:ss`).
4. Tabela com rolagem própria, sem esticar a página inteira.

**Critério de aceite**

- [ ] Tabela reflete os mesmos dados do gráfico no mesmo período.
- [ ] Linhas fora da faixa visualmente distintas.
- [ ] Sem alerta no período, mostra "nenhuma ocorrência" (não fica em branco).

**Peso:** parte de 1,0 pt.

---

### FE-08 — Build de produção e revisão final

**Objetivo:** entregar o front redondo.
**Depende de:** FE-07.
**Entrega:** `frontend/dist/` gerando sem erro, `frontend/README.md`.
**Estimativa:** 2 h.

**Passo a passo**

1. `npm run build` limpo — zero erro de tipo, zero aviso relevante.
2. `npm run preview` e teste o dashboard servido do build.
3. Varra o console do navegador: nenhum erro em vermelho durante 5 min de uso.
4. Teste os caminhos ruins: backend fora do ar, banco vazio, sem dado no período.
5. `frontend/README.md` com como rodar em desenvolvimento e como gerar o build.

**Critério de aceite**

- [ ] Build de produção funcionando.
- [ ] Console limpo.
- [ ] Nenhum cenário de erro resulta em tela branca.

**Peso:** fecha os 2,0 pts do frontend.

---

## Frente ENT — Entrega e apresentação

### ENT-01 — Repositório no GitHub organizado

**Objetivo:** o repositório demonstrar trabalho de equipe, não um `.zip` com um commit.
**Depende de:** nada (comece já).
**Estimativa:** 1 h.

**Passo a passo**

1. Crie o repositório no GitHub e adicione todos os integrantes como
   colaboradores. Cada um comita com a **própria conta** — histórico com um
   único autor pesa contra na hora da avaliação.
2. Trabalhe com uma branch por tarefa e merge por Pull Request.
3. Confira antes de cada push: `.env` fora do Git, `node_modules/` e `.venv/`
   fora do Git.
4. Se algum token vazar em commit, **gere um token novo** — apagar o arquivo no
   commit seguinte não remove o segredo do histórico.

**Critério de aceite**

- [ ] Repositório com commits de todos os integrantes.
- [ ] Nenhum segredo versionado.
- [ ] `main` sempre em estado que roda.

**Peso:** requisito obrigatório da disciplina.

---

### ENT-02 — README final com evidências

**Objetivo:** quem abre o repositório entende e consegue rodar o projeto.
**Depende de:** ENT-01 e as frentes concluídas.
**Estimativa:** 2 h.

**Passo a passo**

1. Atualize a seção "Estado atual" do `README.md` conforme as frentes fecham.
2. Acrescente **capturas de tela** do dashboard (estado normal e estado de alerta).
3. Acrescente o **link público do projeto no Wokwi**.
4. Escreva o passo a passo de execução do zero, na ordem: `docker compose up -d`
   → backend → frontend → Wokwi.
5. Peça para alguém de outra frente seguir o passo a passo em uma máquina limpa.
   Se travar, o passo a passo está incompleto.

**Critério de aceite**

- [ ] Uma pessoa que nunca viu o projeto sobe tudo seguindo só o README.
- [ ] Prints e link do Wokwi presentes.

**Peso:** parte de 1,0 pt (definição do problema e proposta).

---

### ENT-03 — Ensaio geral ponta a ponta

**Objetivo:** não descobrir problema na frente do professor.
**Depende de:** todas as frentes.
**Estimativa:** 2 h.

**Passo a passo**

1. Em uma máquina **desligada e reiniciada**, suba tudo do zero cronometrando.
2. Percorra a tabela de validação da ARQUITETURA §10, elo por elo, e confirme
   os seis.
3. Ensaie a demonstração do alerta: mexer no potenciômetro do Wokwi até o pH sair
   da faixa e mostrar o LED acendendo, o card virando vermelho e o ponto saindo
   da faixa no gráfico. **Esse é o momento que amarra a apresentação inteira.**
4. Ensaie o que fazer se cair a internet ou se o `test.mosquitto.org` estiver
   fora — nesse caso o simulador da BE-09 publica direto no broker local e
   pula o trecho público inteiro. Avise que é ferramenta de desenvolvimento.

**Critério de aceite**

- [ ] Ambiente sobe do zero em menos de 5 minutos.
- [ ] Os 6 elos da ARQUITETURA §10 validados na sequência.
- [ ] Demonstração de alerta ensaiada e funcionando.
- [ ] Caminho de emergência (simulador da BE-09) testado.

**Peso:** protege os 10,0 pts.

---

### ENT-04 — Roteiro da apresentação

**Objetivo:** contar a história na ordem em que a rubrica é avaliada.
**Depende de:** ENT-03.
**Entrega:** `docs/ROTEIRO-APRESENTACAO.md`.
**Estimativa:** 1 h.

**Roteiro sugerido (8 a 10 min)**

| Tempo | O que mostrar | Ponto da rubrica |
| --- | --- | --- |
| 0–1 min | O problema: mortandade por variação de temperatura/pH, controle manual e sem histórico | Definição do problema (1,0) |
| 1–2 min | Diagrama da arquitetura e as tecnologias em cada caixa | Contextualiza tudo |
| 2–4 min | Wokwi rodando: circuito, monitor serial publicando JSON | Dispositivo IoT (1,5) |
| 4–5 min | `mosquitto_sub` recebendo no broker local + explicação da bridge | MQTT/Mosquitto (2,0) |
| 5–6 min | Log do backend gravando + Data Explorer do InfluxDB com os pontos | Backend (1,0) + Persistência (1,5) |
| 6–8 min | Dashboard: cards, gráfico com faixa segura, troca de período | Frontend (2,0) + Gráfico (1,0) |
| 8–9 min | **Provocar o alerta ao vivo** no Wokwi e mostrar a reação em toda a cadeia | Amarra tudo |
| 9–10 min | O que vem no 2º bimestre (tópico `/cmd`, atuação remota, múltiplos tanques) | Continuidade |

**Critério de aceite**

- [ ] Roteiro escrito com quem fala cada parte.
- [ ] Ensaiado dentro do tempo.
- [ ] Cada integrante sabe explicar a frente do colega no básico (o professor
      costuma perguntar para quem não implementou).

---

## Checklist final × rubrica de avaliação

Confira item a item **antes** da apresentação. A rubrica é explícita: incluir a
tecnologia no código sem demonstrar funcionamento **não pontua**.

| Pts | Critério | O que precisa estar demonstrável | Tarefas |
| --- | --- | --- | --- |
| 1,0 | Definição do problema e proposta IoT | README explicando o problema real e como o sistema resolve | ENT-02 |
| 2,0 | Frontend Vue 3 + TS + Vite | dashboard rodando, `strict` ligado, build limpo | FE-01..FE-08 |
| 1,5 | Dispositivo IoT no Wokwi | circuito com 4 sensores publicando de verdade | FW-01..FW-05 |
| 2,0 | MQTT + Mosquitto | broker local recebendo do ESP32; mostrar `mosquitto_sub` ao vivo | INFRA-01, 04, 05 / FW-03, FW-04 / BE-05 |
| 1,0 | Backend Python | log processando e validando; `/docs` da API | BE-01..BE-03, BE-05, BE-07 |
| 1,5 | Persistência no InfluxDB | pontos visíveis no Data Explorer, com tags e fields certos | INFRA-02 / BE-04, BE-06 |
| 1,0 | Dashboard com gráfico/indicador | gráfico histórico + 4 cards com dado real | FE-05, FE-06 |

**Requisitos obrigatórios que não têm pontuação própria** (mas reprovam o item
correspondente se faltarem):

- [ ] Consulta ao histórico funcionando (`/api/readings/history` alimentando o gráfico)
- [ ] Pelo menos 1 sensor IoT — o projeto tem 4
- [ ] Código versionado no GitHub com histórico da equipe
- [ ] Fluxo mínimo completo: Sensor → MQTT → Mosquitto → Python → InfluxDB → Web
