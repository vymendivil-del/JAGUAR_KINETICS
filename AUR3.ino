#include <Wire.h>
#include <math.h>
#include "RTClib.h"
#include <OneWire.h>
#include <DallasTemperature.h>
#include <Adafruit_INA219.h>

// CONSTANTES GEOESPACIALES Y FÍSICAS
const float LATITUDE        = 18.0011;   
const float LONGITUDE       = -93.3756;  
const int   TIMEZONE        = -6;        

const float PANEL_AREA_M2   = 0.035475;  // Área total real 
const float PANEL_P_PEAK    = 4.94;      // Potencia nominal STC 

#define DEG_TO_RAD 0.017453292519943295
#define RAD_TO_DEG 57.29577951308232

const float ANGULAR_DEADBAND = 2.5;     // Histéresis angular mínima (grados)

// Calibración cinemática (ms por grado recorrido)
const unsigned long MS_POR_GRADO_ELEV = 250; 
const unsigned long MS_POR_GRADO_AZIM = 180; 

// --- LÍMITES DE POTENCIA PWM (Jumpers ENA y ENB colocados en L298N) ---
const int PWM_MAX_AZIMUT = 255; // Motor de 12V (Azimut)
const int PWM_MAX_ELEV   = 105; // Motor amarillo TT 3.3V-6V (Elevación)

// Constantes del Anemómetro
const float KvMotor          = 600.0;
const float perimetroMetros  = 0.423;
const float factorCorreccion = 3.0;

// ASIGNACIÓN DE PINES
const int pinTempDS18B20     = 2;   // Bus OneWire 
const int pinTopeElevacion   = 3;   // Microswitch fin de carrera (INPUT_PULLUP)
const int pinMotorAzimut_IN1 = 5;   // L298N IN1 -> OUT1/OUT2
const int pinMotorAzimut_IN2 = 6;   // L298N IN2 -> OUT1/OUT2
const int pinMotorElev_IN3   = 9;   // L298N IN3 -> OUT3/OUT4
const int pinMotorElev_IN4   = 10;  // L298N IN4 -> OUT3/OUT4

const int pinLluvia           = A2; // Sensor resistivo lluvia
const int pinAnemometroAnalog = A3; // Entrada analógica anemómetro

// MÁQUINA DE ESTADOS FINITOS (FSM) Y VARIABLES GLOBALES
enum SystemState {
  NIGHT_STOW,   
  TRACKING,     
  SAFETY_STOW,
  STANDBY_REMOTO
};

SystemState currentState = TRACKING;
bool corteLogicoL298N = false;

RTC_DS3231 rtc;
Adafruit_INA219 ina219;
OneWire oneWire(pinTempDS18B20);
DallasTemperature sensorTemp(&oneWire);

unsigned long tiempoAnteriorSensores = 0;
float targetAzimuth   = 90.0;
float targetElevation = 15.0;
float currentAzimuth  = 90.0;
float currentElev     = 15.0;

// Variables de sensores
float velocidadKMH        = 0.0;
float busVoltage_V        = 0.0;
float current_mA          = 0.0;
float power_mW            = 0.0;
float temperaturaPanel    = 25.0;
float rendimientoPanel    = 0.0;
const char* estadoLluvia  = "Seco";
bool topeMecanicoActivo   = false;
bool rtcDisponible        = false;
bool inaDisponible        = false;

// CONTROL ELECTROMECÁNICO CON FRENO DINÁMICO
void moverMotorSeguroPWM(int pinA, int pinB, int sentido, int pwmLimite) {
  if (corteLogicoL298N) {
    analogWrite(pinA, 0);
    analogWrite(pinB, 0);
    return;
  }

  if (sentido == 1) {
    analogWrite(pinA, pwmLimite);
    analogWrite(pinB, 0);
  } else if (sentido == -1) {
    analogWrite(pinA, 0);
    analogWrite(pinB, pwmLimite);
  } else {
    // Freno regenerativo activo
    analogWrite(pinA, 255);
    analogWrite(pinB, 255);
    delay(40);
    analogWrite(pinA, 0);
    analogWrite(pinB, 0);
  }
}

void ejecutarMovimientoSeguro(int inA, int inB, int sentido, unsigned long duracionTotalMs, bool esBajada, int pwmLimite) {
  if (corteLogicoL298N) return;
  
  unsigned long tiempoInicio = millis();
  moverMotorSeguroPWM(inA, inB, sentido, pwmLimite);

  while (millis() - tiempoInicio < duracionTotalMs) {
    if (esBajada) {
      topeMecanicoActivo = (digitalRead(pinTopeElevacion) == LOW);
      if (topeMecanicoActivo) {
        moverMotorSeguroPWM(inA, inB, 0, pwmLimite); 
        currentElev = 0.0;        
        return;
      }
    }
    delay(20);
  }
  moverMotorSeguroPWM(inA, inB, 0, pwmLimite);
}

void ajustarActuadores() {
  if (corteLogicoL298N || currentState == STANDBY_REMOTO) {
    moverMotorSeguroPWM(pinMotorAzimut_IN1, pinMotorAzimut_IN2, 0, 0);
    moverMotorSeguroPWM(pinMotorElev_IN3, pinMotorElev_IN4, 0, 0);
    return;
  }

  // --- EJE DE ELEVACIÓN ---
  float deltaElev = targetElevation - currentElev;
  if (abs(deltaElev) > ANGULAR_DEADBAND) {
    unsigned long tiempoElevMs = (unsigned long)(abs(deltaElev) * MS_POR_GRADO_ELEV);
    if (deltaElev > 0) {
      ejecutarMovimientoSeguro(pinMotorElev_IN3, pinMotorElev_IN4, 1, tiempoElevMs, false, PWM_MAX_ELEV);
      currentElev = targetElevation;
    } else {
      ejecutarMovimientoSeguro(pinMotorElev_IN3, pinMotorElev_IN4, -1, tiempoElevMs, true, PWM_MAX_ELEV);
      if (!topeMecanicoActivo) {
        currentElev = targetElevation;
      }
    }
  }

  // --- EJE DE AZIMUT ---
  float deltaAz = targetAzimuth - currentAzimuth;
  if (abs(deltaAz) > ANGULAR_DEADBAND) {
    unsigned long tiempoAzimMs = (unsigned long)(abs(deltaAz) * MS_POR_GRADO_AZIM);
    if (deltaAz > 0) {
      ejecutarMovimientoSeguro(pinMotorAzimut_IN1, pinMotorAzimut_IN2, 1, tiempoAzimMs, false, PWM_MAX_AZIMUT);
    } else {
      ejecutarMovimientoSeguro(pinMotorAzimut_IN1, pinMotorAzimut_IN2, -1, tiempoAzimMs, false, PWM_MAX_AZIMUT);
    }
    currentAzimuth = targetAzimuth;
  }
}

// ALGORITMO ASTRONÓMICO RIGUROSO
int calcularDiaDelAno(int y, int m, int d) {
  int diasMes[] = {0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
  if ((y % 4 == 0 && y % 100 != 0) || (y % 400 == 0)) diasMes[2] = 29;
  int diaAno = d;
  for (int i = 1; i < m; i++) diaAno += diasMes[i];
  return diaAno;
}

void calcularPosicionSolar(DateTime dt) {
  int N = calcularDiaDelAno(dt.year(), dt.month(), dt.day());
  float horaDecimal = dt.hour() + (dt.minute() / 60.0) + (dt.second() / 3600.0);

  float gamma = 2.0 * M_PI * (N - 1) / 365.0;
  float declinacion = 23.45 * sin((284 + N) * (360.0 / 365.0) * DEG_TO_RAD) * DEG_TO_RAD;

  float eqtime = 229.18 * (0.000075 + 0.001868 * cos(gamma) - 0.032077 * sin(gamma)
                 - 0.014615 * cos(2 * gamma) - 0.040849 * sin(2 * gamma));

  float timeOffset = eqtime + 4.0 * (LONGITUDE - (15.0 * TIMEZONE));
  float trueSolarTime = horaDecimal * 60.0 + timeOffset;
  float hrAngle = (trueSolarTime / 4.0 - 180.0) * DEG_TO_RAD;
  float latRad = LATITUDE * DEG_TO_RAD;

  float cosZenith = sin(latRad) * sin(declinacion) + cos(latRad) * cos(declinacion) * cos(hrAngle);
  cosZenith = constrain(cosZenith, -1.0, 1.0);
  float zenith = acos(cosZenith);
  targetElevation = 90.0 - (zenith * RAD_TO_DEG);

  float cosAzimuth = (sin(latRad) * cosZenith - sin(declinacion)) / (cos(latRad) * sin(zenith));
  cosAzimuth = constrain(cosAzimuth, -1.0, 1.0);
  float az = acos(cosAzimuth) * RAD_TO_DEG;

  if (hrAngle > 0) {
    targetAzimuth = 360.0 - az;
  } else {
    targetAzimuth = az;
  }
}

float calcularRendimientoReal(float pGeneradaWatts, float elevacionSolar) {
  if (elevacionSolar <= 5.0) return 0.0;
  float gTeorica = 1000.0 * sin(elevacionSolar * DEG_TO_RAD);
  if (gTeorica <= 50.0) return 0.0;
  float pIncidente = gTeorica * PANEL_AREA_M2;
  return (pGeneradaWatts / pIncidente) * 100.0;
}

void leerSensores() {
  // FILTRO TEMPORAL PARA ENTRADAS DESCONECTADAS / RUIDO FLOTANTE:
  // Si A3 está desconectado físicamente, fijamos 0.0 km/h
  int rawAnem = analogRead(pinAnemometroAnalog);
  if (rawAnem < 15 || rawAnem > 1010) { 
    velocidadKMH = 0.0; 
  } else {
    float voltAnem = (rawAnem * 5.0) / 1023.0;
    float rpm = voltAnem * KvMotor;
    velocidadKMH = (((rpm * perimetroMetros) / 60.0) * factorCorreccion) * 3.6;
  }

  // Si A2 está con cables pelados o desconectado, protegemos contra falsa tormenta
  int rawLluvia = analogRead(pinLluvia);
  if (rawLluvia < 150) estadoLluvia = "Seco"; // Desconectado / Ruido a GND
  else if (rawLluvia < 450) estadoLluvia = "Tormenta";
  else if (rawLluvia < 750) estadoLluvia = "Lluvia_Mod";
  else estadoLluvia = "Seco";

  topeMecanicoActivo = (digitalRead(pinTopeElevacion) == LOW);

  sensorTemp.requestTemperatures();
  float tLeida = sensorTemp.getTempCByIndex(0);
  if (tLeida > -55.0 && tLeida < 125.0) {
    temperaturaPanel = tLeida;
  }

  if (inaDisponible) {
    busVoltage_V = ina219.getBusVoltage_V();
    current_mA   = ina219.getCurrent_mA();
    power_mW     = ina219.getPower_mW();
  } else {
    busVoltage_V = 0.0;
    current_mA   = 0.0;
    power_mW     = 0.0;
  }

  rendimientoPanel = calcularRendimientoReal(power_mW / 1000.0, targetElevation);
}

void escucharComandosESP32() {
  if (Serial.available()) {
    String comando = Serial.readStringUntil('\n');
    comando.trim();
    if (comando == "CMD:PARO" || comando == "CMD:DESENERGIZAR") {
      corteLogicoL298N = true;
      currentState = STANDBY_REMOTO;
      moverMotorSeguroPWM(pinMotorAzimut_IN1, pinMotorAzimut_IN2, 0, 0);
      moverMotorSeguroPWM(pinMotorElev_IN3, pinMotorElev_IN4, 0, 0);
    } else if (comando == "CMD:REANUDAR" || comando == "CMD:TRACKING") {
      corteLogicoL298N = false;
      currentState = TRACKING;
    }
  }
}

void setup() {
  Serial.begin(9600);

  pinMode(pinMotorAzimut_IN1, OUTPUT);
  pinMode(pinMotorAzimut_IN2, OUTPUT);
  pinMode(pinMotorElev_IN3, OUTPUT);
  pinMode(pinMotorElev_IN4, OUTPUT);
  pinMode(pinTopeElevacion, INPUT_PULLUP);

  moverMotorSeguroPWM(pinMotorAzimut_IN1, pinMotorAzimut_IN2, 0, 0);
  moverMotorSeguroPWM(pinMotorElev_IN3, pinMotorElev_IN4, 0, 0);

  sensorTemp.begin();
  sensorTemp.setWaitForConversion(true);

  Wire.begin();
  Wire.setClock(100000); 

  rtcDisponible = rtc.begin();
  inaDisponible = ina219.begin();
}

void loop() {
  escucharComandosESP32();

  DateTime ahora = rtcDisponible ? rtc.now() : DateTime(2026, 9, 13, 12, 0, 0);
  unsigned long tiempoActual = millis();

  if (tiempoActual - tiempoAnteriorSensores >= 1000) {
    tiempoAnteriorSensores = tiempoActual;

    leerSensores();
    calcularPosicionSolar(ahora);

    if (currentState != STANDBY_REMOTO) {
      if (velocidadKMH >= 50.0 || strcmp(estadoLluvia, "Tormenta") == 0) {
        currentState = SAFETY_STOW;
      } else if (targetElevation < 0.0) {
        currentState = NIGHT_STOW;
      } else {
        currentState = TRACKING;
      }
    }

    const char* strEstado = "TRACKING";
    switch (currentState) {
      case STANDBY_REMOTO:
        strEstado = "STANDBY";
        ajustarActuadores();
        break;
      case SAFETY_STOW:
        strEstado = "SEGURIDAD";
        targetAzimuth   = 180.0; 
        targetElevation = 15.0;  
        ajustarActuadores();
        break;
      case NIGHT_STOW:
        strEstado = "REPOSO_NOCHE";
        targetAzimuth   = 90.0;  
        targetElevation = 15.0;  
        ajustarActuadores();
        break;
      case TRACKING:
        strEstado = "SEGUIMIENTO";
        ajustarActuadores();
        break;
    }

    // Trama serial estricta de 11 variables separadas por coma
    Serial.print(F("DATA:"));
    Serial.print(busVoltage_V, 2);      Serial.print(F(","));
    Serial.print(current_mA, 1);        Serial.print(F(","));
    Serial.print(power_mW, 1);          Serial.print(F(","));
    Serial.print(temperaturaPanel, 1);  Serial.print(F(","));
    Serial.print(velocidadKMH, 1);      Serial.print(F(","));
    Serial.print(targetAzimuth, 1);     Serial.print(F(","));
    Serial.print(targetElevation, 1);   Serial.print(F(","));
    Serial.print(rendimientoPanel, 1);  Serial.print(F(","));
    Serial.print(strEstado);            Serial.print(F(","));
    Serial.print(estadoLluvia);         Serial.print(F(","));
    Serial.print(topeMecanicoActivo ? "1" : "0");
    Serial.println();
  }
}