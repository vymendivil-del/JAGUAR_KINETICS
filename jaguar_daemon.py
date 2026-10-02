import os
import json
import time
import requests
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# ==========================================
# 1. SERVIDOR HTTP LIGERO (RENDER FREE KEEPALIVE)
# ==========================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ONLINE", "daemon": "JAGUAR KINETICS"}).encode('utf-8'))

    def log_message(self, format, *args):
        return  # Silenciar registros HTTP en la consola

def arrancar_servidor_web():
    puerto = int(os.environ.get("PORT", 8080))
    servidor = HTTPServer(('0.0.0.0', puerto), HealthHandler)
    servidor.serve_forever()

# Iniciar el servidor web en un hilo secundario
threading.Thread(target=arrancar_servidor_web, daemon=True).start()

# ==========================================
# 2. ENDPOINTS Y CONFIGURACIÓN
# ==========================================
FIREBASE_BASE = "https://jaguar-telemetry-default-rtdb.firebaseio.com"
FIREBASE_SECRET = os.environ.get("FIREBASE_DATABASE_SECRET", "")
AUTH_PARAM = f"?auth={FIREBASE_SECRET}" if FIREBASE_SECRET else ""

URL_TELEMETRIA = f"{FIREBASE_BASE}/telemetria.json{AUTH_PARAM}"
URL_CONTROL    = f"{FIREBASE_BASE}/control.json{AUTH_PARAM}"

# Inicialización del cliente de Gemini
client = genai.Client()

# ==========================================
# 3. ESQUEMA DE DECISIÓN COGNITIVA
# ==========================================
class JaguarDecision(BaseModel):
    modo_operativo: str = Field(
        description="Modo: TRACKING_LEAD_LAG, CLOUDY_DIFFUSE, SAFETY_STOW, RAIN_WASH, THERMAL_DERATING, BATTERY_SAVER o STANDBY"
    )
    rele_potencia: bool = Field(
        description="True para energizar L298N, False para corte lógico a 0V"
    )
    intervalo_reposo_minutos: int = Field(
        description="Minutos de reposo antes del próximo pulso cinemático"
    )
    angulo_azimut_offset: float = Field(
        description="Offset azimutal en grados (-15.0 a 15.0)"
    )
    angulo_elev_offset: float = Field(
        description="Offset cenital en grados (-15.0 a 35.0)"
    )
    justificacion_tecnica: str = Field(
        description="Diagnóstico técnico de ingeniería"
    )
    alerta_critica: bool = Field(
        description="True si se detecta peligro estructural o ráfagas extremas"
    )

# ==========================================
# 4. ENLACE METEOROLÓGICO SATELITAL (P.M.A)
# ==========================================
def obtener_pronostico_satelital():
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": 18.0011,
        "longitude": -93.3756,
        "current": [
            "temperature_2m", "cloud_cover", "wind_speed_10m",
            "wind_direction_10m", "wind_gusts_10m",
            "direct_normal_irradiance", "diffuse_radiation"
        ],
        "hourly": ["wind_gusts_10m"],
        "timezone": "America/Mexico_City"
    }
    try:
        r = requests.get(url, params=params, timeout=3.0)
        if r.status_code == 200:
            d = r.json()
            act = d.get("current", {})
            horario = d.get("hourly", {})
            raf_1h = horario.get("wind_gusts_10m", [0.0])[1] if len(horario.get("wind_gusts_10m", [])) > 1 else 0.0
            dni = act.get("direct_normal_irradiance", 0.0) or 0.0
            dhi = act.get("diffuse_radiation", 0.0) or 0.0
            return {
                "viento_kmh": act.get("wind_speed_10m", 0.0),
                "direccion_viento_deg": act.get("wind_direction_10m", 90.0),
                "rafaga_actual_kmh": act.get("wind_gusts_10m", 0.0),
                "rafaga_proyectada_1h": raf_1h,
                "nubes_pct": act.get("cloud_cover", 0),
                "ghi_estimada": max(0.0, dni + dhi)
            }
    except Exception:
        pass
    return {
        "viento_kmh": 0.0,
        "direccion_viento_deg": 90.0,
        "rafaga_actual_kmh": 0.0,
        "rafaga_proyectada_1h": 0.0,
        "nubes_pct": 20,
        "ghi_estimada": 650.0
    }

# ==========================================
# 5. INFERENCIA COGNITIVA JERÁRQUICA (HCSC)
# ==========================================
def ejecutar_inferencia_jaguar(telemetria, pronostico):
    prompt_sistema = """
    Eres JAGUAR, el supervisor cognitivo autónomo del seguidor biaxial JAGUAR KINETICS (panel 10W, área 0.046764 m2).
    Aplica las políticas HCSC:
    1. SAFETY_STOW: Si anemómetro >= 45.0 km/h o ráfaga P.M.A 1h >= 35.0 km/h. Rele=False, reposo=0, az_off=viento_dir, el_off=15.0.
    2. RAIN_WASH: Si lluvia activa y viento < 28.0 km/h. Rele=True, el_off=35.0, reposo=30.
    3. THERMAL_DERATING: Si temp silicio >= 65.0°C y viento < 6.0 km/h. Rele=True, az_off=+6.0, reposo=15.
    4. CLOUDY_DIFFUSE: Si nubes >= 80% o potencia < 1.5W con luz. Rele=False, el_off=0.0, reposo=60.
    5. BATTERY_SAVER: Si potencia < 0.8W o tensión < 11.0V. Rele=False, reposo=30.
    6. TRACKING_LEAD_LAG: Nominal despejado. Rele=True, az_off=+2.5, reposo=20.
    Responde estrictamente con un objeto JSON válido según el esquema indicado.
    """
    try:
        resp = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=f"Telemetría viva: {json.dumps(telemetria)} | Pronóstico: {json.dumps(pronostico)}",
            config=types.GenerateContentConfig(
                system_instruction=prompt_sistema,
                response_mime_type="application/json",
                response_schema=JaguarDecision,
                temperature=0.1
            )
        )
        return JaguarDecision.model_validate_json(resp.text)
    except Exception as e:
        print(f"[ALERTA GEMINI] Failsafe activado por error: {e}")
        es_peligro = telemetria.get("viento_kmh", 0.0) >= 45.0 or pronostico.get("rafaga_proyectada_1h", 0.0) >= 38.0
        return JaguarDecision(
            modo_operativo="SAFETY_STOW" if es_peligro else "TRACKING_LEAD_LAG",
            rele_potencia=not es_peligro,
            intervalo_reposo_minutos=0 if es_peligro else 20,
            angulo_azimut_offset=0.0,
            angulo_elev_offset=0.0,
            justificacion_tecnica="Failsafe ejecutado automáticamente ante latencia o desconexión.",
            alerta_critica=es_peligro
        )

# ==========================================
# 6. BUCLE CONTINUO DE SUPERVISIÓN 24/7
# ==========================================
def ciclo_principal():
    print("[INICIO] Daemon de supervisión JAGUAR KINETICS en ejecución continua...")
    while True:
        try:
            # 1. Leer telemetría viva de Firebase RTDB
            r_tel = requests.get(URL_TELEMETRIA, timeout=3.0)
            telemetria = r_tel.json() if r_tel.status_code == 200 and r_tel.json() else {}

            # 2. Consultar Open-Meteo
            pronostico = obtener_pronostico_satelital()

            # 3. Inferencia con Gemini 2.5-Flash
            decision = ejecutar_inferencia_jaguar(telemetria, pronostico)

            # 4. Despachar a /control.json
            payload = {
                "modo_ejecucion": decision.modo_operativo,
                "rele_potencia_motores": decision.rele_potencia,
                "offset_azimut": decision.angulo_azimut_offset,
                "offset_elevacion": decision.angulo_elev_offset,
                "intervalo_reposo_minutos": decision.intervalo_reposo_minutos,
                "alerta_critica": decision.alerta_critica,
                "diagnostico_ia": decision.justificacion_tecnica,
                "timestamp": datetime.now().isoformat()
            }
            r_patch = requests.patch(URL_CONTROL, json=payload, timeout=3.0)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Despacho: {decision.modo_operativo} | Relé: {decision.rele_potencia} | AzOff: {decision.angulo_azimut_offset}° | ElOff: {decision.angulo_elev_offset}°")
        except Exception as err:
            print(f"[ERROR BUCLE] {err}")

        # Pausa de 60 segundos entre cada evaluación cognitiva
        time.sleep(60)

if __name__ == "__main__":
    ciclo_principal()