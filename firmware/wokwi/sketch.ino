/*
 * ============================================================================
 *  TankVitals - Firmware ESP32 (Wokwi)  -  v1.0.0
 *  Monitoramento de tanque de aquicultura / aquario
 *  UniFACEF 2026 - Web II - 1o Bimestre
 * ============================================================================
 *
 *  Le as 4 grandezas, publica telemetria JSON no MQTT a cada 5 s, acende o LED
 *  quando alguma sai da faixa segura e aceita comando de intervalo.
 *  Etapas FW-02 a FW-05 do backlog (docs/TAREFAS.md).
 *
 *  Sensores (pinos conforme diagram.json):
 *    - DS18B20        -> temperatura da agua (GPIO 4, 1-Wire + pull-up 4.7k)
 *    - Potenciometro  -> sonda de pH (GPIO 34, ADC1)
 *    - HC-SR04        -> nivel da agua (TRIG 5 / ECHO 18)
 *    - LDR (modulo)   -> turbidez (GPIO 35, ADC1)
 *    - LED vermelho   -> alerta local (GPIO 19 + resistor 220 ohm)
 *
 *  O projeto no Wokwi precisa ser do tipo ESP32 -> Arduino, com as
 *  bibliotecas listadas no libraries.txt.
 * ============================================================================
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <time.h>

// ---------------------------------------------------------------------------
// Conexao
// ---------------------------------------------------------------------------
// O ESP32 do Wokwi roda na nuvem e nao enxerga o localhost da equipe. Ele
// publica no test.mosquitto.org e o Mosquitto local importa por bridge
// (INFRA-04) - o backend continua falando so com o broker local.
//
// O broker publico e aberto a qualquer um, por isso o prefixo unico. Ele
// precisa ser IGUAL em tres lugares: aqui, no MQTT_TOPIC_PREFIX do .env e na
// linha "topic" da bridge no mosquitto.conf.

static const char *WIFI_SSID = "Wokwi-GUEST";
static const char *WIFI_PASS = "";
static const int   WIFI_CANAL = 6;   // informar o canal acelera muito a conexao no simulador

static const char *MQTT_HOST = "test.mosquitto.org";
static const int   MQTT_PORT = 1883;
static const char *MQTT_USER = "";   // broker publico nao pede credencial
static const char *MQTT_PASS = "";

static const char *TOPIC_PREFIX = "tankvitals-unifacef-g3";
static const char *DEVICE_ID    = "esp32-tank-01";
static const char *TANK_ID      = "tanque-01";
static const char *FW_VERSION   = "1.0.0";

// Faixas seguras (ARQUITETURA §5). O firmware usa so a coluna "ok", em binario:
// a classificacao em tres niveis e responsabilidade do backend.
static const float TEMP_OK_MIN  = 24.0f;
static const float TEMP_OK_MAX  = 28.0f;
static const float PH_OK_MIN    = 6.5f;
static const float PH_OK_MAX    = 8.0f;
static const float LEVEL_OK_MIN = 30.0f;
static const float TURB_OK_MAX  = 40.0f;

// Pinos - iguais aos do diagram.json
static const int PIN_ONEWIRE   = 4;
static const int PIN_TRIG      = 5;
static const int PIN_ECHO      = 18;
static const int PIN_LED       = 19;
static const int PIN_PH        = 34;   // ADC1
static const int PIN_TURBIDITY = 35;   // ADC1

// Geometria do tanque, em cm (ARQUITETURA §3)
static const float DIST_TANQUE_CHEIO = 5.0f;
static const float DIST_TANQUE_VAZIO = 45.0f;

// Abaixo disso o relogio ainda nao sincronizou com o NTP (ARQUITETURA §3).
static const long TS_MINIMO_VALIDO = 1700000000L;

OneWire oneWire(PIN_ONEWIRE);
DallasTemperature dallas(&oneWire);

WiFiClient rede;
PubSubClient mqtt(rede);

char clientId[40];
char topicTelemetry[128];
char topicStatus[128];
char topicCmd[128];

unsigned long seq = 0;
unsigned long intervaloPublicacao = 5000;   // alteravel em runtime pelo topico /cmd
unsigned long ultimaPublicacao = 0;
unsigned long ultimaTentativaMqtt = 0;

// ---------------------------------------------------------------------------
// Leitura dos sensores
// ---------------------------------------------------------------------------

float lerTemperatura() {
  dallas.requestTemperatures();
  float t = dallas.getTempCByIndex(0);
  if (t <= -100.0f) return NAN;   // -127 = sensor nao respondeu
  return t;
}

float lerPh() {
  return (analogRead(PIN_PH) * 14.0f) / 4095.0f;
}

float lerTurbidez() {
  float ntu = ((4095.0f - analogRead(PIN_TURBIDITY)) * 100.0f) / 4095.0f;
  return ntu < 0.0f ? 0.0f : ntu;
}

float lerDistancia() {
  digitalWrite(PIN_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);

  unsigned long dur = pulseIn(PIN_ECHO, HIGH, 30000UL);
  if (dur == 0) return NAN;
  return (dur * 0.0343f) / 2.0f;
}

float distanciaParaNivel(float dist) {
  if (isnan(dist)) return NAN;
  float pct = ((DIST_TANQUE_VAZIO - dist) * 100.0f) /
              (DIST_TANQUE_VAZIO - DIST_TANQUE_CHEIO);
  if (pct < 0.0f)   pct = 0.0f;
  if (pct > 100.0f) pct = 100.0f;
  return pct;
}

float arredondar(float valor, int casas) {
  float fator = powf(10.0f, casas);
  return roundf(valor * fator) / fator;
}

// ---------------------------------------------------------------------------
// Alerta local (FW-05)
// ---------------------------------------------------------------------------

// Grandeza ausente (NAN) nao conta como alerta: ausente e ausente.
bool foraDaFaixa(float temp, float ph, float nivel, float turbidez) {
  if (!isnan(temp)     && (temp < TEMP_OK_MIN || temp > TEMP_OK_MAX)) return true;
  if (!isnan(ph)       && (ph < PH_OK_MIN || ph > PH_OK_MAX))         return true;
  if (!isnan(nivel)    && nivel < LEVEL_OK_MIN)                       return true;
  if (!isnan(turbidez) && turbidez >= TURB_OK_MAX)                    return true;
  return false;
}

// ---------------------------------------------------------------------------
// Wi-Fi, NTP e MQTT (FW-03)
// ---------------------------------------------------------------------------

void conectarWifi() {
  Serial.print("[wifi] conectando em ");
  Serial.print(WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS, WIFI_CANAL);

  unsigned long inicio = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - inicio < 20000) {
    delay(250);
    Serial.print(".");
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print(" ok, IP ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println(" FALHOU (segue tentando no loop)");
  }
}

void onComando(char *topic, byte *payload, unsigned int length);

bool conectarMqtt() {
  Serial.print("[mqtt] conectando em ");
  Serial.print(MQTT_HOST);
  Serial.print(" como ");
  Serial.print(clientId);

  const char *usuario = strlen(MQTT_USER) > 0 ? MQTT_USER : NULL;
  const char *senha   = strlen(MQTT_PASS) > 0 ? MQTT_PASS : NULL;

  // Last Will: se a placa cair sem avisar, o broker publica "offline" sozinho.
  // E assim que o dashboard descobre que o dispositivo sumiu.
  bool ok = mqtt.connect(clientId, usuario, senha, topicStatus, 1, true, "offline");

  if (ok) {
    // A PubSubClient so publica em QoS 0; o QoS 1 do contrato vale para o Last
    // Will (definido acima) e para a assinatura do /cmd.
    mqtt.publish(topicStatus, "online", true);
    mqtt.subscribe(topicCmd, 1);
    Serial.println(" ok");
  } else {
    Serial.print(" falhou, rc=");
    Serial.print(mqtt.state());
    // rc=5 e credencial recusada, nao problema de rede.
    Serial.println(mqtt.state() == 5 ? " (nao autorizado)" : "");
  }
  return ok;
}

// ---------------------------------------------------------------------------
// Comandos recebidos (FW-05)
// ---------------------------------------------------------------------------

void onComando(char *topic, byte *payload, unsigned int length) {
  JsonDocument doc;
  if (deserializeJson(doc, payload, length)) {
    Serial.println("[cmd] JSON invalido, ignorado");
    return;
  }

  if (!doc["interval_s"].is<int>()) {
    Serial.println("[cmd] sem interval_s, ignorado");
    return;
  }

  int novo = doc["interval_s"];
  if (novo < 1 || novo > 300) {
    Serial.print("[cmd] interval_s fora da faixa 1-300, ignorado: ");
    Serial.println(novo);
    return;
  }

  intervaloPublicacao = (unsigned long)novo * 1000UL;
  Serial.print("[cmd] intervalo de publicacao agora e ");
  Serial.print(novo);
  Serial.println(" s");
}

// ---------------------------------------------------------------------------
// Telemetria (FW-04)
// ---------------------------------------------------------------------------

void publicarTelemetria(float temp, float ph, float nivel, float dist, float turbidez) {
  JsonDocument doc;

  doc["device_id"] = DEVICE_ID;
  doc["tank_id"]   = TANK_ID;
  doc["fw"]        = FW_VERSION;
  doc["seq"]       = seq;
  doc["uptime_s"]  = millis() / 1000;
  doc["rssi"]      = WiFi.RSSI();

  // Só envia ts depois que o NTP sincronizou; antes disso o relogio marca 1970
  // e os pontos iriam parar la no grafico.
  time_t agora = time(NULL);
  if (agora > TS_MINIMO_VALIDO) doc["ts"] = (long)agora;

  // Campo ausente e o contrato para "sensor falhou". Enviar NaN geraria JSON
  // invalido e o backend descartaria a mensagem inteira.
  if (!isnan(temp))     doc["temperature_c"] = arredondar(temp, 2);
  if (!isnan(ph))       doc["ph"]            = arredondar(ph, 2);
  if (!isnan(nivel))    doc["level_pct"]     = arredondar(nivel, 1);
  if (!isnan(dist))     doc["distance_cm"]   = arredondar(dist, 1);
  if (!isnan(turbidez)) doc["turbidity_ntu"] = arredondar(turbidez, 1);

  char buffer[384];
  size_t tamanho = serializeJson(doc, buffer, sizeof(buffer));
  if (tamanho == 0 || tamanho >= sizeof(buffer) - 1) {
    Serial.println("[pub] JSON nao coube no buffer, descartado");
    return;
  }

  // publish(topic, buffer) e nao publish(topic, buffer, tamanho): a sobrecarga
  // de 3 argumentos que aceita char* recebe "retained" como terceiro parametro,
  // entao o tamanho seria lido como flag. O serializeJson ja termina em '\0'.
  if (mqtt.publish(topicTelemetry, buffer)) {
    Serial.print("[pub] ");
    Serial.println(buffer);
    seq++;
  } else {
    Serial.println("[pub] falhou (buffer da PubSubClient ou conexao)");
  }
}

// ---------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n=== TankVitals v1.0.0 ===");

  pinMode(PIN_TRIG, OUTPUT);
  pinMode(PIN_ECHO, INPUT);
  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, LOW);

  analogReadResolution(12);          // 0..4095
  analogSetAttenuation(ADC_11db);    // faixa util ~0..3.3 V

  dallas.begin();
  Serial.print("DS18B20 encontrados no barramento: ");
  Serial.println(dallas.getDeviceCount());
  if (dallas.getDeviceCount() == 0) {
    Serial.println("  -> confira o pino DQ no GPIO 4 e o pull-up de 4.7k ate o 3V3");
  }

  snprintf(topicTelemetry, sizeof(topicTelemetry), "%s/%s/telemetry", TOPIC_PREFIX, TANK_ID);
  snprintf(topicStatus,    sizeof(topicStatus),    "%s/%s/status",    TOPIC_PREFIX, TANK_ID);
  snprintf(topicCmd,       sizeof(topicCmd),       "%s/%s/cmd",       TOPIC_PREFIX, TANK_ID);

  // clientId unico a partir do MAC: dois clientes com o mesmo id se derrubam
  // em laco infinito de reconexao.
  uint64_t mac = ESP.getEfuseMac();
  snprintf(clientId, sizeof(clientId), "tankvitals-%04X%08X",
           (uint16_t)(mac >> 32), (uint32_t)mac);

  conectarWifi();
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");

  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  // O padrao de 256 bytes CORTA o JSON e a publicacao falha silenciosamente.
  mqtt.setBufferSize(512);
  mqtt.setKeepAlive(30);
  mqtt.setCallback(onComando);
  conectarMqtt();

  Serial.println();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    conectarWifi();
  }

  // Reconexao sem delay(): delay no loop trava o mqtt.loop() e derruba a conexao.
  if (!mqtt.connected() && millis() - ultimaTentativaMqtt >= 5000) {
    ultimaTentativaMqtt = millis();
    conectarMqtt();
  }

  mqtt.loop();

  if (millis() - ultimaPublicacao < intervaloPublicacao) return;
  ultimaPublicacao = millis();

  float temp     = lerTemperatura();
  float ph       = lerPh();
  float turbidez = lerTurbidez();
  float dist     = lerDistancia();
  float nivel    = distanciaParaNivel(dist);

  digitalWrite(PIN_LED, foraDaFaixa(temp, ph, nivel, turbidez) ? HIGH : LOW);

  if (mqtt.connected()) {
    publicarTelemetria(temp, ph, nivel, dist, turbidez);
  }
}
