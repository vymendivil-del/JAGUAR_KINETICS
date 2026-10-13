#include <WiFi.h>
#include <WiFiMulti.h>
#include <HTTPClient.h>
#include <WebServer.h>
#include <ArduinoJson.h>

// ==========================================
// 1. GESTIÓN MULTIRED Y CREDENCIALES
// ==========================================
WiFiMulti wifiMulti;

const char* CASA_SSID     = "FAM.RODRIGUEZ";
const char* CASA_PASS     = "H1955C2007";

const char* MOVIL_SSID    = "Iphone";
const char* MOVIL_PASS    = "Gamemote";

// Credenciales oficiales de Supabase
const char* SUPABASE_URL_TEL = "https://uepycvoyvvdejebribu.supabase.co/rest/v1/telemetria?id=eq.1";
const char* SUPABASE_URL_CMD = "https://uepycvoyvvdejebribu.supabase.co/rest/v1/control?id=eq.1&select=*";

// Pega aquí la clave anon copiada de la consola de Supabase
const char* SUPABASE_KEY     = "PEGA_AQUI_TU_CLAVE_ANON_PUBLIC";

const char* AP_SSID       = "JAGUAR_AP";
const char* AP_PASS       = "garricindustries";

// ==========================================
// 2. OBJETOS Y VARIABLES DE CONTROL
// ==========================================
HardwareSerial SerialArduino(2); // UART2: GPIO 16 (RX2) y GPIO 17 (TX2)
WebServer server(80);

bool modoNubeActivo = false;
String ultimoJson = "{}";
unsigned long ultimoCheckControl = 0;
unsigned long ultimoIntentoWiFi = 0;

void handleRoot() {
  server.send(200, "application/json", ultimoJson);
}

// ==========================================
// 3. CONSULTA DE DIRECTIVAS EN SUPABASE (GET)
// ==========================================
void consultarDirectivasNube() {
  if (!modoNubeActivo || WiFi.status() != WL_CONNECTED) return;

  HTTPClient http;
  http.begin(SUPABASE_URL_CMD);
  http.setTimeout(1500);
  http.addHeader("apikey", SUPABASE_KEY);
  http.addHeader("Authorization", String("Bearer ") + SUPABASE_KEY);

  int httpCode = http.GET();

  if (httpCode == 200) {
    String payload = http.getString();
    StaticJsonDocument<512> docArray;
    DeserializationError error = deserializeJson(docArray, payload);

    if (!error && docArray.is<JsonArray>() && docArray.size() > 0) {
      JsonObject docCmd = docArray[0];

      bool relePotencia = docCmd["rele_potencia_motores"] | true;
      const char* modoEjecucion = docCmd["modo_ejecucion"] | "TRACKING";
      float offsetAz = docCmd["offset_azimut"] | 0.0;
      float offsetEl = docCmd["offset_elevacion"] | 0.0;
      int reposoMin  = docCmd["intervalo_reposo_minutos"] | 20;

      // 1. Trama estructurada multivariable hacia Arduino (CMD:PARAM)
      SerialArduino.print("CMD:PARAM,");
      SerialArduino.print(relePotencia ? "1" : "0");
      SerialArduino.print(",");
      SerialArduino.print(modoEjecucion);
      SerialArduino.print(",");
      SerialArduino.print(offsetAz, 2);
      SerialArduino.print(",");
      SerialArduino.print(offsetEl, 2);
      SerialArduino.print(",");
      SerialArduino.println(reposoMin);

      // 2. Compatibilidad con comandos rápidos
      if (!relePotencia || strcmp(modoEjecucion, "STANDBY_VOZ") == 0 || strcmp(modoEjecucion, "SAFETY_STOW") == 0) {
        SerialArduino.println("CMD:PARO");
      } else {
        SerialArduino.println("CMD:REANUDAR");
      }
    }
  }
  http.end();
}

// ==========================================
// 4. PARSER Y DESPACHO A SUPABASE (PATCH)
// ==========================================
void procesarYDespachar(String trama) {
  if (!trama.startsWith("DATA:")) return;
  trama.remove(0, 5);

  char buf[220];
  trama.toCharArray(buf, sizeof(buf));

  // Extracción secuencial de los 11 tokens transmitidos por el Arduino
  char* token = strtok(buf, ",");
  float v = token ? atof(token) : 0.0;                        // [0] Voltaje (V)

  token = strtok(NULL, ",");
  float i = token ? atof(token) : 0.0;                        // [1] Corriente (mA)
  if (i > 600.0) i = i / 10.0;                                // Filtro de escala shunt

  token = strtok(NULL, ",");
  float p_mW = token ? atof(token) : 0.0;                     // [2] Potencia (mW)
  float p_w = (p_mW > 0.0 && p_mW < 15000.0) ? (p_mW / 1000.0) : (v * (i / 1000.0));

  token = strtok(NULL, ",");
  float t = token ? atof(token) : 25.0;                       // [3] Temperatura (°C)

  token = strtok(NULL, ",");
  float w = token ? atof(token) : 0.0;                        // [4] Viento (km/h)

  token = strtok(NULL, ",");
  float azimut = token ? atof(token) : 90.0;                  // [5] Azimut (°)

  token = strtok(NULL, ",");
  float elevacion = token ? atof(token) : 15.0;               // [6] Elevación (°)

  token = strtok(NULL, ",");
  float rendimiento = token ? atof(token) : 0.0;              // [7] Rendimiento (%)

  token = strtok(NULL, ",");
  String modo = token ? String(token) : "SEGUIMIENTO";         // [8] Modo FSM

  token = strtok(NULL, ",");
  String lluvia = token ? String(token) : "Seco";             // [9] Estado Pluvial

  token = strtok(NULL, ",");
  bool fin_carrera = token ? (atoi(token) == 1) : false;      // [10] Fin de Carrera D3

  StaticJsonDocument<512> doc;
  doc["voltaje"] = v;
  doc["corriente_ma"] = i;
  doc["potencia_w"] = (p_w > 0.0) ? p_w : round(v * (i / 1000.0) * 100.0) / 100.0;
  doc["temperatura"] = t;
  doc["viento_kmh"] = w;
  doc["azimut"] = azimut;
  doc["elevacion"] = elevacion;
  doc["rendimiento_pct"] = rendimiento;
  doc["modo"] = modo;
  doc["lluvia"] = lluvia;
  doc["fin_carrera_activo"] = fin_carrera;

  String payload;
  serializeJson(doc, payload);
  ultimoJson = payload;

  if (modoNubeActivo && (WiFi.status() == WL_CONNECTED)) {
    HTTPClient http;
    http.begin(SUPABASE_URL_TEL);
    http.setTimeout(1500);
    http.addHeader("Content-Type", "application/json");
    http.addHeader("apikey", SUPABASE_KEY);
    http.addHeader("Authorization", String("Bearer ") + SUPABASE_KEY);
    http.addHeader("Prefer", "return=minimal");

    int httpCode = http.PATCH(payload);
    if (httpCode > 0) {
      Serial.printf("[SUPABASE] Telemetría enviada -> Código: %d | Potencia: %.2f W | Viento: %.1f km/h\n", 
                    httpCode, doc["potencia_w"].as<float>(), w);
    }
    http.end();
  } else if (!modoNubeActivo) {
    Serial.println("[AP LOCAL] Datos disponibles en 192.168.4.1/telemetria");
  }
}

// ==========================================
// 5. CONFIGURACIÓN Y SETUP
// ==========================================
void setup() {
  Serial.begin(115200);

  // Sincronización estricta con el Arduino a 115200 baudios
  SerialArduino.begin(115200, SERIAL_8N1, 16, 17); // GPIO 16 (RX2), GPIO 17 (TX2)
  SerialArduino.setTimeout(20);

  Serial.println("\n[JAGUAR KINETICS] Pasarela Ciberfísica en línea (Supabase Edition)...");

  wifiMulti.addAP(CASA_SSID, CASA_PASS);
  wifiMulti.addAP(MOVIL_SSID, MOVIL_PASS);

  WiFi.mode(WIFI_STA);
  Serial.print("[INFO] Conectando a infraestructura WiFi...");

  unsigned long tInicio = millis();
  while (wifiMulti.run() != WL_CONNECTED && (millis() - tInicio < 8000)) {
    delay(500);
    Serial.print(".");
  }

  if (WiFi.status() == WL_CONNECTED) {
    modoNubeActivo = true;
    Serial.println("\n[EXITO] Conexión establecida: " + WiFi.SSID());
    Serial.print("[INFO] IP asignada: ");
    Serial.println(WiFi.localIP());
  } else {
    modoNubeActivo = false;
    Serial.println("\n[ALERTA] Red no disponible. Levantando Punto de Acceso de contingencia...");
    WiFi.mode(WIFI_AP_STA);
    WiFi.softAP(AP_SSID, AP_PASS);

    server.on("/telemetria", handleRoot);
    server.begin();
    Serial.println("[EXITO] AP Activo: JAGUAR_AP (192.168.4.1)");
  }
}

// ==========================================
// 6. BUCLE PRINCIPAL (NON-BLOCKING)
// ==========================================
void loop() {
  if (!modoNubeActivo || WiFi.status() != WL_CONNECTED) {
    server.handleClient();
  }

  // 1. Escucha no bloqueante de la telemetría del Arduino
  if (SerialArduino.available()) {
    String linea = SerialArduino.readStringUntil('\n');
    linea.trim();
    if (linea.length() > 0) {
      procesarYDespachar(linea);
    }
  }

  // 2. Consulta periódica de órdenes a Supabase cada 2 segundos
  if (millis() - ultimoCheckControl >= 2000) {
    ultimoCheckControl = millis();
    consultarDirectivasNube();
  }

  // 3. Intento de reconexión WiFi si cayó la señal
  if (!modoNubeActivo && (millis() - ultimoIntentoWiFi >= 15000)) {
    ultimoIntentoWiFi = millis();
    if (wifiMulti.run() == WL_CONNECTED) {
      modoNubeActivo = true;
      Serial.println("[RECONEXIÓN] WiFi reestablecido. Conectado a: " + WiFi.SSID());
    }
  }
}