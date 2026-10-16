import os
import json
import time
import requests
import threading
from datetime import datetime
import zoneinfo
from http.server import HTTPServer, BaseHTTPRequestHandler
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

ZONA_LOCAL = zoneinfo.ZoneInfo("America/Mexico_City")

# ==========================================
# 1. GESTOR SEGURO DE CREDENCIALES
# ==========================================
def cargar_secreto(clave: str, defecto: str = "") -> str:
    val = os.environ.get(clave, "").strip()
    if val:
        return val
    rutas = [".streamlit/secrets.toml", os.path.expanduser("~/.streamlit/secrets.toml")]
    for ruta in rutas:
        if os.path.exists(ruta):
            try:
                with open(ruta, "r", encoding="utf-8") as f:
                    for linea in f:
                        if linea.startswith(clave) and "=" in linea:
                            _, v = linea.split("=", 1)
                            v_limpio = v.strip().strip('"').strip("'")
                            if v_limpio:
                                return v_limpio
            except Exception:
                pass
    return defecto

GEMINI_KEY = cargar_secreto("GEMINI_API_KEY")
if GEMINI_KEY:
    os.environ["GEMINI_API_KEY"] = GEMINI_KEY

TELEGRAM_BOT_TOKEN = cargar_secreto("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = cargar_secreto("TELEGRAM_CHAT_ID")

# URL homologada oficial de Supabase
SUPABASE_URL = cargar_secreto("SUPABASE_URL", "https://uepycvoyvvdejebrlbuv.supabase.co")
SUPABASE_KEY = cargar_secreto("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVlcHljdm95dnZkZWplYnJsYnV2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTE1MTI4MTgsImV4cCI6MjEwNzA4ODgxOH0.pkeFwkGrXQ9WylCIForlYcD4386hn46jMBH988byAcI")

HEADERS_SB = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

URL_SB_TEL = f"{SUPABASE_URL}/rest/v1/telemetria?id=eq.1"
URL_SB_CMD = f"{SUPABASE_URL}/rest/v1/control?id=eq.1"

# ==========================================
# 2. MAPA DE TÉRMINOS OPERATIVOS
# ==========================================
DICCIONARIO_MODOS = {
    "TRACKING_LEAD_LAG": "Seguimiento Solar Activo",
    "TRACKING": "Seguimiento Solar Activo",
    "SAFETY_STOW": "Protección por Vendaval / Tormenta",
    "RAIN_WASH": "Autolavado por Lluvia",
    "THERMAL_DERATING": "Alivio por Sobrecalentamiento",
    "CLOUDY_DIFFUSE": "Cielo Nublado (Ahorro)",
    "BATTERY_SAVER": "Ahorro de Energía",
    "STANDBY": "Pausa Manual / Mantenimiento"
}

def formatear_modo_legible(modo_raw: str) -> str:
    return DICCIONARIO_MODOS.get(modo_raw, modo_raw)

def enviar_notificacion_telegram(mensaje: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=4.0)
    except Exception as e:
        print(f"[ALERTA TELEGRAM] Error enviando mensaje: {e}")

# ==========================================
# 3. GENERADOR DE CONSOLA WEB HTML (RENDER)
# ==========================================
def construir_dashboard_html():
    # Consulta de telemetría y directivas vigentes
    tel = {}
    ctrl = {}
    try:
        r_t = requests.get(URL_SB_TEL, headers=HEADERS_SB, timeout=2.0)
        if r_t.status_code == 200 and r_t.json():
            tel = r_t.json()[0]
    except Exception:
        pass

    try:
        r_c = requests.get(URL_SB_CMD, headers=HEADERS_SB, timeout=2.0)
        if r_c.status_code == 200 and r_c.json():
            ctrl = r_c.json()[0]
    except Exception:
        pass

    v = tel.get("voltaje", 0.0)
    i = tel.get("corriente_ma", 0.0)
    p = tel.get("potencia_w", 0.0)
    temp = tel.get("temperatura", 25.0)
    viento = tel.get("viento_kmh", 0.0)
    lluvia = tel.get("lluvia", "Seco")
    azimut = tel.get("azimut", 90.0)
    elevacion = tel.get("elevacion", 15.0)
    timestamp_tel = tel.get("actualizado_en", tel.get("timestamp", "Sin registro reciente"))

    modo = formatear_modo_legible(ctrl.get("modo_ejecucion", "TRACKING_LEAD_LAG"))
    motores_on = ctrl.get("rele_potencia_motores", True)
    reposo = ctrl.get("intervalo_reposo_minutos", 20)
    diagnostico = ctrl.get("diagnostico_ia", "Supervisión cognitiva activa 24/7.")

    estado_badge = '<span class="badge badge-green">DISPONIBLE</span>' if motores_on else '<span class="badge badge-red">CORTE 0V</span>'
    estado_esp = '<span class="badge badge-green">CONECTADO</span>' if v > 0 or p > 0 else '<span class="badge badge-cyan">EN ESPERA</span>'

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JAGUAR KINETICS | Consola Operativa 24/7</title>
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@800;900&family=Share+Tech+Mono&family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
    <meta http-equiv="refresh" content="15">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background-color: #0B0C10;
            color: #F8FAFC;
            font-family: 'Inter', sans-serif;
            padding: 20px;
            min-height: 100vh;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 2px solid #1E232E;
            padding-bottom: 15px;
            margin-bottom: 25px;
            flex-wrap: wrap;
            gap: 10px;
        }}
        .title {{
            font-family: 'Orbitron', sans-serif;
            font-size: 1.8rem;
            font-weight: 900;
            letter-spacing: 2px;
            background: linear-gradient(90deg, #FFFFFF 10%, #E02424 70%, #8B0000 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .subtitle {{
            font-family: 'Share Tech Mono', monospace;
            font-size: 0.85rem;
            color: #94A3B8;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 16px;
            margin-bottom: 25px;
        }}
        .card {{
            background-color: #0E1015;
            border: 1px solid #1A1D24;
            border-radius: 12px;
            padding: 16px 20px;
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.4);
        }}
        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #1A1D24;
            padding-bottom: 8px;
            margin-bottom: 12px;
            font-weight: 700;
            font-size: 0.95rem;
        }}
        .badge {{
            font-family: 'Share Tech Mono', monospace;
            font-size: 0.7rem;
            font-weight: 700;
            padding: 3px 8px;
            border-radius: 12px;
        }}
        .badge-green {{ background: rgba(16, 185, 129, 0.15); color: #10B981; border: 1px solid rgba(16, 185, 129, 0.4); }}
        .badge-red {{ background: rgba(239, 68, 68, 0.15); color: #EF4444; border: 1px solid rgba(239, 68, 68, 0.4); }}
        .badge-cyan {{ background: rgba(6, 182, 212, 0.15); color: #06B6D4; border: 1px solid rgba(6, 182, 212, 0.4); }}
        .row {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin: 8px 0;
            font-size: 0.85rem;
        }}
        .label {{ color: #8F9CAE; }}
        .val {{ font-family: 'Share Tech Mono', monospace; font-weight: bold; color: #FFFFFF; font-size: 1rem; }}
        .val-highlight {{ color: #10B981; font-size: 1.15rem; }}
        .diag-box {{
            background-color: #0E1015;
            border: 1px solid #8B0000;
            border-radius: 10px;
            padding: 15px 20px;
            margin-bottom: 25px;
        }}
        .diag-title {{
            font-family: 'Orbitron', sans-serif;
            font-size: 0.85rem;
            color: #E02424;
            margin-bottom: 6px;
        }}
        .diag-text {{
            font-size: 0.9rem;
            color: #CBD5E1;
            line-height: 1.4;
        }}
        .footer-links {{
            display: flex;
            justify-content: center;
            gap: 15px;
            flex-wrap: wrap;
        }}
        .btn {{
            display: inline-block;
            background: #1E232E;
            color: #FFFFFF;
            text-decoration: none;
            padding: 10px 18px;
            border-radius: 8px;
            font-size: 0.82rem;
            font-weight: 600;
            border: 1px solid #334155;
            transition: all 0.2s ease;
        }}
        .btn:hover {{
            background: #8B0000;
            border-color: #DC2626;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div>
            <div class="title">JAGUAR KINETICS</div>
            <div class="subtitle">Consola de Supervisión Ciberfísica 24/7 (Cloud HCSC)</div>
        </div>
        <div class="subtitle">
            🕒 Hora Local: {datetime.now(ZONA_LOCAL).strftime('%H:%M:%S')} | Cárdenas, Tabasco
        </div>
    </div>

    <div class="grid">
        <!-- Tarjeta JAGUAR IA -->
        <div class="card">
            <div class="card-header">
                <span>🧠 JAGUAR (Supervisor)</span>
                {estado_badge}
            </div>
            <div class="row"><span class="label">Política Activa:</span><span class="val">{modo}</span></div>
            <div class="row"><span class="label">Driver L298N:</span><span class="val">{'Energizado' if motores_on else 'Corte 0V'}</span></div>
            <div class="row"><span class="label">Banda Muerta:</span><span class="val">{reposo} min reposo</span></div>
        </div>

        <!-- Tarjeta Generación Eléctrica -->
        <div class="card">
            <div class="card-header">
                <span>⚡ Módulo Epcom 10W</span>
                <span class="badge badge-green">MÉTRICAS</span>
            </div>
            <div class="row"><span class="label">Potencia Generada:</span><span class="val val-highlight">{p:.2f} W</span></div>
            <div class="row"><span class="label">Voltaje del Panel:</span><span class="val">{v:.2f} V</span></div>
            <div class="row"><span class="label">Corriente Neta:</span><span class="val">{i:.1f} mA</span></div>
        </div>

        <!-- Tarjeta Hardware / Cinemática -->
        <div class="card">
            <div class="card-header">
                <span>🛰️ Enlace Hardware</span>
                {estado_esp}
            </div>
            <div class="row"><span class="label">Coordenada Azimut:</span><span class="val">{azimut:.1f}°</span></div>
            <div class="row"><span class="label">Coordenada Elevación:</span><span class="val">{elevacion:.1f}°</span></div>
            <div class="row"><span class="label">Tope Carrera D3:</span><span class="val">Protegido (&lt;4µs)</span></div>
        </div>

        <!-- Tarjeta Meteorología -->
        <div class="card">
            <div class="card-header">
                <span>🌦️ E.M.A / P.M.A</span>
                <span class="badge badge-cyan">ESTABLE</span>
            </div>
            <div class="row"><span class="label">Velocidad Viento:</span><span class="val">{viento:.1f} km/h</span></div>
            <div class="row"><span class="label">Temperatura Celda:</span><span class="val">{temp:.1f} °C</span></div>
            <div class="row"><span class="label">Condición Pluvial:</span><span class="val">{lluvia}</span></div>
        </div>
    </div>

    <div class="diag-box">
        <div class="diag-title">⚡ DIAGNÓSTICO COGNITIVO EN TIEMPO REAL (GEMINI 2.5 FLASH)</div>
        <div class="diag-text">{diagnostico}</div>
    </div>

    <div class="footer-links">
        <a href="https://github.com/vymendivil-del/JAGUAR_KINETICS" target="_blank" class="btn">📂 Repositorio Oficial GitHub</a>
        <a href="https://t.me/" target="_blank" class="btn">🤖 Bot de Telegram JAGUAR</a>
        <a href="/api/telemetria" class="btn">🔌 API JSON Cruda</a>
    </div>
</body>
</html>"""
    return html

# ==========================================
# 4. SERVIDOR HTTP DUAL (DASHBOARD + PUENTE)
# ==========================================
class BridgeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ['/', '/index.html', '/dashboard']:
            contenido_html = construir_dashboard_html()
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(contenido_html.encode('utf-8'))
        elif self.path in ['/api/telemetria', '/status']:
            try:
                r_sb = requests.get(URL_SB_TEL, headers=HEADERS_SB, timeout=2.0)
                datos = r_sb.json() if r_sb.status_code == 200 else {"status": "error"}
            except Exception:
                datos = {"status": "error_conexion"}
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(datos).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == '/telemetria_esp32':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                datos = json.loads(post_data.decode('utf-8'))
                # Reenviar a Supabase desde la conexión de Render
                r_sb = requests.patch(URL_SB_TEL, headers=HEADERS_SB, json=datos, timeout=3.5)
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "OK", "supabase_code": r_sb.status_code}).encode('utf-8'))
                print(f"[PUENTE ESP32] Telemetría recibida y despachada a Supabase -> Código: {r_sb.status_code}")
            except Exception as err:
                self.send_response(500)
                self.end_headers()
                print(f"[PUENTE ERROR] Error procesando telemetría del ESP32: {err}")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return  # Silenciar pings periódicos de monitoreo

def arrancar_servidor_web():
    puerto = int(os.environ.get("PORT", 8080))
    servidor = HTTPServer(('0.0.0.0', puerto), BridgeHandler)
    servidor.serve_forever()

threading.Thread(target=arrancar_servidor_web, daemon=True).start()

# ==========================================
# 5. CONFIGURACIÓN GEMINI Y SUPERVISIÓN
# ==========================================
client = genai.Client()

class JaguarDecision(BaseModel):
    modo_operativo: str = Field(description="Modo: TRACKING_LEAD_LAG, CLOUDY_DIFFUSE, SAFETY_STOW, RAIN_WASH, THERMAL_DERATING, BATTERY_SAVER o STANDBY")
    rele_potencia: bool = Field(description="True para energizar L298N, False para corte lógico a 0V")
    intervalo_reposo_minutos: int = Field(description="Minutos de reposo antes del próximo pulso")
    angulo_azimut_offset: float = Field(description="Offset azimutal en grados")
    angulo_elev_offset: float = Field(description="Offset cenital en grados")
    justificacion_tecnica: str = Field(description="Diagnóstico técnico comprensible")
    alerta_critica: bool = Field(description="True si se detecta peligro estructural")

def obtener_pronostico_satelital():
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": 18.0011, "longitude": -93.3756,
        "current": ["temperature_2m", "cloud_cover", "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "direct_normal_irradiance", "diffuse_radiation"],
        "hourly": ["wind_gusts_10m"], "timezone": "America/Mexico_City"
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
    return {"viento_kmh": 0.0, "direccion_viento_deg": 90.0, "rafaga_actual_kmh": 0.0, "rafaga_proyectada_1h": 0.0, "nubes_pct": 20, "ghi_estimada": 650.0}

def ejecutar_inferencia_jaguar(telemetria, pronostico):
    prompt_sistema = """
    Eres JAGUAR, el supervisor cognitivo autónomo del seguidor biaxial JAGUAR KINETICS (panel 10W, área 0.046764 m2).
    Aplica las políticas HCSC de seguridad y optimización de forma comprensible:
    1. SAFETY_STOW: Anemómetro >= 45.0 km/h o ráfaga 1h >= 35.0 km/h. Rele=False, reposo=0, az_off=viento_dir, el_off=15.0.
    2. RAIN_WASH: Lluvia activa y viento < 28.0 km/h. Rele=True, el_off=35.0, reposo=30.
    3. THERMAL_DERATING: Temp silicio >= 65.0°C y viento < 6.0 km/h. Rele=True, az_off=+6.0, reposo=15.
    4. CLOUDY_DIFFUSE: Nubes >= 80% o potencia < 1.5W con luz. Rele=False, el_off=0.0, reposo=60.
    5. BATTERY_SAVER: Potencia < 0.8W o tensión < 11.0V. Rele=False, reposo=30.
    6. TRACKING_LEAD_LAG: Nominal despejado. Rele=True, az_off=+2.5, reposo=20.
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
    except Exception:
        es_peligro = telemetria.get("viento_kmh", 0.0) >= 45.0 or pronostico.get("rafaga_proyectada_1h", 0.0) >= 38.0
        return JaguarDecision(
            modo_operativo="SAFETY_STOW" if es_peligro else "TRACKING_LEAD_LAG",
            rele_potencia=not es_peligro,
            intervalo_reposo_minutos=0 if es_peligro else 20,
            angulo_azimut_offset=0.0,
            angulo_elev_offset=0.0,
            justificacion_tecnica="Resguardo preventivo por ráfagas de viento detectadas." if es_peligro else "Operación nominal continua sin anomalías.",
            alerta_critica=es_peligro
        )

# ==========================================
# 6. ATENCIÓN DE PREGUNTAS (TELEGRAM Q&A)
# ==========================================
def responder_pregunta_usuario(pregunta: str) -> str:
    try:
        r_tel = requests.get(URL_SB_TEL, headers=HEADERS_SB, timeout=2.5)
        telemetria = r_tel.json()[0] if r_tel.status_code == 200 and r_tel.json() else {}
        
        r_ctrl = requests.get(URL_SB_CMD, headers=HEADERS_SB, timeout=2.5)
        control = r_ctrl.json()[0] if r_ctrl.status_code == 200 and r_ctrl.json() else {}

        modo_actual = formatear_modo_legible(control.get("modo_ejecucion", "TRACKING"))

        texto_cmd = pregunta.strip().lower()
        if texto_cmd == "/estado":
            return f"🛡️ *Estado:* {modo_actual}\n⚡ *Potencia:* {telemetria.get('potencia_w', 0.0)} W\n🔋 *Tensión:* {telemetria.get('voltaje', 0.0)} V\n💨 *Viento:* {telemetria.get('viento_kmh', 0.0)} km/h\n🌡️ *Temperatura:* {telemetria.get('temperatura', 25.0)} °C"
        elif texto_cmd == "/potencia":
            return f"⚡ *Generación Eléctrica:*\n- Potencia: {telemetria.get('potencia_w', 0.0)} W\n- Tensión: {telemetria.get('voltaje', 0.0)} V\n- Corriente: {telemetria.get('corriente_ma', 0.0)} mA"
        elif texto_cmd == "/clima":
            return f"🌤️ *Condiciones:*\n- Viento: {telemetria.get('viento_kmh', 0.0)} km/h\n- Lluvia: {telemetria.get('lluvia', 'Seco')}\n- Temp. silicio: {telemetria.get('temperatura', 25.0)} °C"
        elif texto_cmd == "/modo":
            rele_txt = "Energizados (En movimiento)" if control.get("rele_potencia_motores", True) else "Corte a 0V (Protección/Reposo)"
            return f"⚙️ *Modo Operativo:* {modo_actual}\n🔌 *Motores:* {rele_txt}\n⏱️ *Reposo:* {control.get('intervalo_reposo_minutos', 20)} min"

        prompt_chat = f"""
        Eres JAGUAR, el supervisor cognitivo del seguidor solar fotovoltaico JAGUAR KINETICS.
        Responde al operador por Telegram de forma clara, directa, educada y profesional.
        DATOS EN VIVO:
        - Modo: {modo_actual} | Potencia: {telemetria.get('potencia_w', 0.0)} W | Tensión: {telemetria.get('voltaje', 0.0)} V
        - Corriente: {telemetria.get('corriente_ma', 0.0)} mA | Temp: {telemetria.get('temperatura', 25.0)} °C | Viento: {telemetria.get('viento_kmh', 0.0)} km/h
        - Lluvia: {telemetria.get('lluvia', 'Seco')} | Motores: {'Energizados' if control.get('rele_potencia_motores', True) else 'Corte a 0V'}
        - Diagnóstico: {control.get('diagnostico_ia', 'Nominal')}
        Consulta del usuario: "{pregunta}"
        """
        resp = client.models.generate_content(model='gemini-2.5-flash', contents=prompt_chat)
        return resp.text.strip()
    except Exception as e:
        return f"Demora momentánea consultando los sensores: {e}"

def hilo_escucha_telegram():
    if not TELEGRAM_BOT_TOKEN:
        return
    offset = 0
    url_updates = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    while True:
        try:
            params = {"offset": offset, "timeout": 20}
            r = requests.get(url_updates, params=params, timeout=25)
            if r.status_code == 200:
                data = r.json()
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    texto = msg.get("text", "").strip()
                    if chat_id == str(TELEGRAM_CHAT_ID) and texto:
                        respuesta = responder_pregunta_usuario(texto)
                        enviar_notificacion_telegram(respuesta)
        except Exception:
            time.sleep(3)

threading.Thread(target=hilo_escucha_telegram, daemon=True).start()

# ==========================================
# 7. BUCLE PRINCIPAL DE SUPERVISIÓN 24/7
# ==========================================
def ciclo_principal():
    print("[INICIO] JAGUAR DAEMON 24/7 activo con Dashboard Web y Puente HTTP...")
    ultimo_minuto_horario = -1
    alerta_critica_enviada = False

    while True:
        try:
            ahora_local = datetime.now(ZONA_LOCAL)

            # Leer telemetría viva desde Supabase
            r_tel = requests.get(URL_SB_TEL, headers=HEADERS_SB, timeout=3.0)
            telemetria = r_tel.json()[0] if r_tel.status_code == 200 and r_tel.json() else {}

            pronostico = obtener_pronostico_satelital()
            decision = ejecutar_inferencia_jaguar(telemetria, pronostico)

            payload = {
                "modo_ejecucion": decision.modo_operativo,
                "rele_potencia_motores": decision.rele_potencia,
                "offset_azimut": decision.angulo_azimut_offset,
                "offset_elevacion": decision.angulo_elev_offset,
                "intervalo_reposo_minutos": decision.intervalo_reposo_minutos,
                "alerta_critica": decision.alerta_critica,
                "diagnostico_ia": decision.justificacion_tecnica,
                "timestamp": ahora_local.isoformat()
            }
            requests.patch(URL_SB_CMD, headers=HEADERS_SB, json=payload, timeout=3.0)

            minuto_actual = ahora_local.minute
            if minuto_actual == 0 and ultimo_minuto_horario != 0:
                ultimo_minuto_horario = 0
                modo_amigable = formatear_modo_legible(decision.modo_operativo)
                msg_reporte = f"""⚡ *REPORTE HORARIO JAGUAR KINETICS*
🕒 *Hora Local:* {ahora_local.strftime('%H:%M')} hrs
🛡️ *Estado:* {modo_amigable}
📊 *Potencia:* {telemetria.get('potencia_w', 0.0)} W
🔋 *Tensión:* {telemetria.get('voltaje', 0.0)} V
💨 *Viento:* {telemetria.get('viento_kmh', 0.0)} km/h
🌡️ *Temp. Silicio:* {telemetria.get('temperatura', 0.0)} °C
🤖 *Diagnóstico:* {decision.justificacion_tecnica}"""
                enviar_notificacion_telegram(msg_reporte)

            if minuto_actual != 0:
                ultimo_minuto_horario = -1

            if decision.alerta_critica:
                if not alerta_critica_enviada:
                    alerta_critica_enviada = True
                    modo_amigable = formatear_modo_legible(decision.modo_operativo)
                    msg_alerta = f"""🚨 *ALERTA DE SEGURIDAD JAGUAR*
⚠️ *Acción:* {modo_amigable}
💨 *Viento en el Seguidor:* {telemetria.get('viento_kmh', 0.0)} km/h
🛰️ *Ráfaga Pronosticada:* {pronostico.get('rafaga_proyectada_1h', 0.0)} km/h
🛑 *Medida:* Motores desconectados a 0V y panel en resguardo."""
                    enviar_notificacion_telegram(msg_alerta)
            else:
                alerta_critica_enviada = False

        except Exception as err:
            print(f"[ERROR BUCLE] {err}")

        time.sleep(60)

if __name__ == "__main__":
    ciclo_principal()
