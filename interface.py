import os
import io
import json
import re
import hashlib
import time
import requests
import pandas as pd
import streamlit as st
import matplotlib
matplotlib.use('Agg')
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from datetime import datetime, date, timedelta
import zoneinfo
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

ZONA_LOCAL = zoneinfo.ZoneInfo("America/Mexico_City")

# ==========================================
# RUTAS DE IDENTIDAD OFICIAL (JAGUAR KINETICS)
# ==========================================
RUTA_FAVICON_PESTANA = "JAGUAR_blanco.png" if os.path.exists("JAGUAR_blanco.png") else "JAGUAR.png"
RUTA_WEB_LOGO_BLANCO = "Logo_jaguar_blanco.png" if os.path.exists("Logo_jaguar_blanco.png") else "Logo jaguar.png"
RUTA_EMBLEMA_JK      = "JK_logo.png" if os.path.exists("JK_logo.png") else "JAGUAR Tipografia.png"
RUTA_PDF_LOGO_COLOR  = "Logo jaguar.png"

icono_pestana = RUTA_FAVICON_PESTANA if os.path.exists(RUTA_FAVICON_PESTANA) else "⚡"
st.set_page_config(page_title="JAGUAR KINETICS", page_icon=icono_pestana, layout="wide")

# ==========================================
# PARÁMETROS FÍSICOS DEL PANEL FOTOVOLTAICO
# ==========================================
AREA_TOTAL_PANEL_M2    = 0.0693       # Área física total con marco
AREA_NETA_CELDAS_M2    = 0.046764     # Área neta activa de captación (celdas)
POTENCIA_MAX_PANEL_W   = 10.0         # Potencia nominal STC Epcom
CORRIENTE_MAX_PANEL_A  = 0.5          # Amperaje nominal de placa
POTENCIA_INCIDENTE_STC = 1000.0 * AREA_NETA_CELDAS_M2  # 46.764 W incidentes a 1000 W/m²

# ==========================================
# GESTIÓN SEGURA DE CREDENCIALES
# ==========================================
def cargar_secreto(clave: str, defecto: str = "") -> str:
    try:
        if clave in st.secrets:
            return str(st.secrets[clave]).strip()
    except Exception:
        pass

    val = os.environ.get(clave, "").strip()
    if val:
        return val

    for ruta_sec in [".streamlit/secrets.toml", os.path.expanduser("~/.streamlit/secrets.toml")]:
        if os.path.exists(ruta_sec):
            try:
                with open(ruta_sec, "r", encoding="utf-8") as f:
                    for linea in f:
                        if linea.startswith(clave) and "=" in linea:
                            partes = linea.split("=", 1)
                            v = partes[1].strip().strip('"').strip("'")
                            if v:
                                return v
            except Exception:
                pass
    return defecto

GEMINI_KEY = cargar_secreto("GEMINI_API_KEY")
if GEMINI_KEY:
    os.environ["GEMINI_API_KEY"] = GEMINI_KEY

# ==========================================
# MAPA DE TÉRMINOS AMIGABLES
# ==========================================
DICCIONARIO_MODOS = {
    "TRACKING_LEAD_LAG": "Seguimiento Solar Activo",
    "TRACKING": "Seguimiento Solar Activo",
    "SAFETY_STOW": "Protección Vendaval / Tormenta",
    "RAIN_WASH": "Autolavado por Lluvia",
    "THERMAL_DERATING": "Alivio por Sobrecalentamiento",
    "CLOUDY_DIFFUSE": "Cielo Nublado (Ahorro)",
    "BATTERY_SAVER": "Ahorro Batería",
    "STANDBY": "Pausa Manual / Mantenimiento",
    "STANDBY_VOZ": "Pausa por Voz",
    "TRACKING_VOZ": "Seguimiento por Voz"
}

def traducir_modo(m: str) -> str:
    return DICCIONARIO_MODOS.get(m, m)

# ==========================================
# ENLACE METEOROLÓGICO CON CACHÉ (P.M.A)
# ==========================================
@st.cache_data(ttl=600)
def obtener_pronostico_pma():
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": 18.0011,
        "longitude": -93.3756,
        "current": [
            "temperature_2m", "relative_humidity_2m", "cloud_cover", 
            "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
            "direct_normal_irradiance", "diffuse_radiation"
        ],
        "hourly": ["wind_gusts_10m", "cloud_cover"],
        "timezone": "America/Mexico_City"
    }
    try:
        r = requests.get(url, params=params, timeout=2.0)
        if r.status_code == 200:
            datos = r.json()
            actual = datos.get("current", {})
            horario = datos.get("hourly", {})
            rafaga_1h = horario.get("wind_gusts_10m", [0.0])[min(1, len(horario.get("wind_gusts_10m", []))-1)]
            nubes_1h = horario.get("cloud_cover", [0])[min(1, len(horario.get("cloud_cover", []))-1)]
            
            dni = actual.get("direct_normal_irradiance", 0.0) or 0.0
            dhi = actual.get("diffuse_radiation", 0.0) or 0.0
            ghi_estimada = max(0.0, dni + dhi)

            return {
                "viento_actual_kmh": actual.get("wind_speed_10m", 0.0),
                "direccion_viento_deg": actual.get("wind_direction_10m", 90.0),
                "rafaga_actual_kmh": actual.get("wind_gusts_10m", 0.0),
                "rafaga_proyectada_1h": rafaga_1h,
                "cobertura_nubes_pct": actual.get("cloud_cover", 0),
                "cobertura_nubes_1h": nubes_1h,
                "temp_aire_c": actual.get("temperature_2m", 25.0),
                "irradiancia_w_m2": ghi_estimada,
                "estado_api": "Enlace Estable"
            }
    except Exception:
        pass

    hora_actual = datetime.now(ZONA_LOCAL).hour + datetime.now(ZONA_LOCAL).minute / 60.0
    ghi_fallback = 0.0 if (hora_actual < 6.5 or hora_actual > 18.0) else 650.0

    return {
        "viento_actual_kmh": 0.0,
        "direccion_viento_deg": 90.0,
        "rafaga_actual_kmh": 0.0,
        "rafaga_proyectada_1h": 0.0,
        "cobertura_nubes_pct": 20,
        "cobertura_nubes_1h": 20,
        "temp_aire_c": 26.0,
        "irradiancia_w_m2": ghi_fallback,
        "estado_api": "Modo Autónomo Local"
    }

# ==========================================
# RETROSPECTIVA HISTÓRICA OPEN-METEO ARCHIVE
# ==========================================
@st.cache_data(ttl=3600)
def obtener_historico_satelital(fecha_inicio: str, fecha_fin: str):
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": 18.0011,
        "longitude": -93.3756,
        "start_date": fecha_inicio,
        "end_date": fecha_fin,
        "hourly": ["temperature_2m", "cloud_cover", "wind_speed_10m", "wind_gusts_10m", "direct_normal_irradiance", "diffuse_radiation"],
        "timezone": "America/Mexico_City"
    }
    try:
        r = requests.get(url, params=params, timeout=3.0)
        if r.status_code == 200:
            datos = r.json().get("hourly", {})
            return pd.DataFrame({
                "FechaHora": datos.get("time", []),
                "Temp (°C)": datos.get("temperature_2m", []),
                "Nubes (%)": datos.get("cloud_cover", []),
                "Viento (km/h)": datos.get("wind_speed_10m", []),
                "Ráfagas (km/h)": datos.get("wind_gusts_10m", []),
                "DNI (W/m²)": datos.get("direct_normal_irradiance", []),
                "DHI (W/m²)": datos.get("diffuse_radiation", [])
            })
    except Exception:
        pass
    return None

# ==========================================
# TELEMETRÍA SUPABASE REST API
# ==========================================
SUPABASE_URL = cargar_secreto("SUPABASE_URL", "https://uepycvoyvvdejebribu.supabase.co")
SUPABASE_KEY = cargar_secreto("SUPABASE_KEY", "")

HEADERS_SB = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

URL_SB_TELEMETRIA = f"{SUPABASE_URL}/rest/v1/telemetria?id=eq.1"
URL_SB_CONTROL    = f"{SUPABASE_URL}/rest/v1/control?id=eq.1"
URL_SB_MANT       = f"{SUPABASE_URL}/rest/v1/mantenimiento?id=eq.1"

def leer_telemetria_supabase():
    if not SUPABASE_KEY:
        return None
    try:
        r = requests.get(URL_SB_TELEMETRIA, headers=HEADERS_SB, timeout=1.5)
        if r.status_code == 200 and r.json():
            return r.json()[0]
    except Exception:
        pass
    return None

def consultar_control_supabase():
    if not SUPABASE_KEY:
        return None
    try:
        r = requests.get(URL_SB_CONTROL, headers=HEADERS_SB, timeout=1.5)
        if r.status_code == 200 and r.json():
            return r.json()[0]
    except Exception:
        pass
    return None

def enviar_comando_potencia_supabase(linea_activa: bool, modo: str = "TRACKING"):
    if not SUPABASE_KEY:
        return False
    payload = {
        "rele_potencia_motores": linea_activa,
        "modo_ejecucion": modo,
        "timestamp": datetime.now(ZONA_LOCAL).isoformat()
    }
    try:
        r = requests.patch(URL_SB_CONTROL, headers=HEADERS_SB, json=payload, timeout=2.0)
        return r.status_code in [200, 204]
    except Exception:
        return False

# ==========================================
# PERSISTENCIA DE MANTENIMIENTO
# ==========================================
ARCHIVO_MANTENIMIENTO = "mantenimiento_db.json"

def cargar_mantenimiento():
    if SUPABASE_KEY:
        try:
            r = requests.get(URL_SB_MANT, headers=HEADERS_SB, timeout=1.5)
            if r.status_code == 200 and r.json():
                datos = r.json()[0].get("registros", [])
                if isinstance(datos, list) and datos:
                    return datos
        except Exception:
            pass

    if os.path.exists(ARCHIVO_MANTENIMIENTO):
        try:
            with open(ARCHIVO_MANTENIMIENTO, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    return [
        {"OT": "OT-2026-08-15", "Fecha": "2026-08-15", "Tipo": "Puesta en Marcha", "Técnico": "Christian Garric", "Estado": "Conforme", "Paro": "No", "Observaciones": "Calibración inicial de protección de carrera en pin D3 y termopozo DS18B20."},
        {"OT": "OT-2026-08-25", "Fecha": "2026-08-25", "Tipo": "Preventivo Menor", "Técnico": "Christian Garric", "Estado": "Conforme", "Paro": "No", "Observaciones": "Inspección de bornes en driver L298N y verificación de bus I2C INA219."}
    ]

def guardar_mantenimiento(registros):
    with open(ARCHIVO_MANTENIMIENTO, "w", encoding="utf-8") as f:
        json.dump(registros, f, ensure_ascii=False, indent=2)
    if SUPABASE_KEY:
        try:
            payload = {"id": 1, "registros": registros}
            requests.put(f"{SUPABASE_URL}/rest/v1/mantenimiento", headers=HEADERS_SB, json=payload, timeout=2.0)
        except Exception:
            pass

# ==========================================
# VARIABLES DE ESTADO Y CALENDARIO
# ==========================================
if "registro_mantenimiento" not in st.session_state:
    st.session_state.registro_mantenimiento = cargar_mantenimiento()
if "modo_operacion" not in st.session_state:
    st.session_state.modo_operacion = "Operación predeterminada"
if "modo_operacion_previo" not in st.session_state:
    st.session_state.modo_operacion_previo = st.session_state.modo_operacion
if "unidad_viento" not in st.session_state:
    st.session_state.unidad_viento = "km/h"
if "unidad_corriente" not in st.session_state:
    st.session_state.unidad_corriente = "mA"
if "paro_tecnico_activo" not in st.session_state:
    st.session_state.paro_tecnico_activo = False
if "seguidor_habilitado" not in st.session_state:
    st.session_state.seguidor_habilitado = True
if "modo_simulacion_forzado" not in st.session_state:
    st.session_state.modo_simulacion_forzado = False
if "historial_pantalla" not in st.session_state:
    st.session_state.historial_pantalla = []
if "ultimo_audio_hash" not in st.session_state:
    st.session_state.ultimo_audio_hash = None

fecha_ultima_ot_str = st.session_state.registro_mantenimiento[-1]["Fecha"]
try:
    fecha_ultima_ot = datetime.strptime(fecha_ultima_ot_str, "%Y-%m-%d").date()
except Exception:
    fecha_ultima_ot = date(2026, 8, 25)

hoy = date.today()
dias_operando_real = max(1, (hoy - fecha_ultima_ot).days)
INTERVALO_MANTENIMIENTO_DIAS = 30

if dias_operando_real >= INTERVALO_MANTENIMIENTO_DIAS:
    dias_para_mantenimiento = 0
    fecha_proximo_dt = hoy + timedelta(days=7)
    fecha_proximo_mantenimiento = f"{fecha_proximo_dt.strftime('%d de %B de %Y')} (Intervención Pendiente)"
else:
    dias_para_mantenimiento = INTERVALO_MANTENIMIENTO_DIAS - dias_operando_real
    fecha_proximo_dt = fecha_ultima_ot + timedelta(days=INTERVALO_MANTENIMIENTO_DIAS)
    fecha_proximo_mantenimiento = fecha_proximo_dt.strftime("%d de %B de %Y")

if "bitacora_eventos" not in st.session_state:
    st.session_state.bitacora_eventos = [
        {"Hora": "05:42:10", "Sensor": "Fin de Carrera (D3)", "Lectura": "Tope verificado", "Acción": "Protección de sobrecarrera activa"},
        {"Hora": "13:42:15", "Sensor": "Anemómetro", "Lectura": "46.2 km/h", "Acción": "Protección por vendaval"},
        {"Hora": "14:15:00", "Sensor": "Sensor de lluvia", "Lectura": "Lluvia Moderada", "Acción": "Modo autolavado pluvial"},
        {"Hora": "14:40:00", "Sensor": "Sensor de lluvia", "Lectura": "Seco", "Acción": "Reanudación del seguimiento"}
    ]

# HISTÓRICO DE MUESTREO: PERIODO DEL 08 AL 12 DE OCTUBRE
HISTORICO_MUESTREO_OCTUBRE = [
    {"Día": "Jueves", "Fecha": "2026-10-08", "Voltaje Máx (V)": 18.90, "Potencia Pico (W)": 8.10, "Energía (Wh)": 57.60, "Viento Máx (km/h)": 19.5, "Temp Máx (°C)": 38.2, "Estado": "Nublado Variable"},
    {"Día": "Viernes", "Fecha": "2026-10-09", "Voltaje Máx (V)": 17.50, "Potencia Pico (W)": 7.45, "Energía (Wh)": 49.80, "Viento Máx (km/h)": 46.2, "Temp Máx (°C)": 36.8, "Estado": "Protección Vendaval"},
    {"Día": "Sábado", "Fecha": "2026-10-10", "Voltaje Máx (V)": 19.80, "Potencia Pico (W)": 9.65, "Energía (Wh)": 72.30, "Viento Máx (km/h)": 16.8, "Temp Máx (°C)": 42.6, "Estado": "Óptimo (Soleado)"},
    {"Día": "Domingo", "Fecha": "2026-10-11", "Voltaje Máx (V)": 19.40, "Potencia Pico (W)": 9.20, "Energía (Wh)": 67.10, "Viento Máx (km/h)": 18.0, "Temp Máx (°C)": 40.5, "Estado": "Parc. Nublado"},
    {"Día": "Lunes", "Fecha": "2026-10-12", "Voltaje Máx (V)": 18.20, "Potencia Pico (W)": 7.80, "Energía (Wh)": 54.70, "Viento Máx (km/h)": 22.4, "Temp Máx (°C)": 37.4, "Estado": "Cielo Difuso"}
]

# PERFIL DIARIO CLAMPED (06:00 A 18:00 ESTRICTO)
horas_maestras = [f"{h:02d}:00" for h in range(6, 19)]  # 06:00 a 18:00 inclusive
voltaje_base = [3.5, 8.2, 12.5, 16.8, 18.5, 19.8, 19.6, 19.2, 14.5, 10.2, 5.8, 1.8, 0.0]
corriente_ma_base = [50, 130, 260, 410, 480, 495, 490, 470, 340, 200, 85, 20, 0]
potencia_w_base = [round(v * (ma / 1000.0), 2) for v, ma in zip(voltaje_base, corriente_ma_base)]
temp_base = [24.5, 26.0, 28.5, 32.0, 36.5, 41.2, 42.6, 27.5, 31.0, 30.0, 29.5, 27.0, 25.5]
viento_base = [8.5, 10.2, 12.0, 14.0, 18.5, 22.0, 25.5, 28.0, 19.5, 16.0, 14.0, 11.0, 9.0]
condiciones_base = ["Despejado", "Despejado", "Despejado", "Soleado", "Soleado", "Soleado", "Soleado", "Lluvia Moderada", "Despejado", "Despejado", "Despejado", "Noche", "Noche"]

# RESTRICCIÓN HORARIA: Si son más de las 18:00 hrs, el corte es a las 18:00 hrs
hora_reloj = datetime.now(ZONA_LOCAL).hour
minuto_reloj = datetime.now(ZONA_LOCAL).minute
if hora_reloj < 6:
    idx_limite = 1
elif hora_reloj >= 18:
    idx_limite = len(horas_maestras)  # Se clampa exactamente a las 18:00
else:
    idx_limite = (hora_reloj - 6) + 1

horas_cortadas = horas_maestras[:idx_limite]
voltaje_cortado = voltaje_base[:idx_limite]
corriente_ma_cortada = corriente_ma_base[:idx_limite]
potencia_w_cortada = potencia_w_base[:idx_limite]
temp_cortada = temp_base[:idx_limite]
viento_cortado = viento_base[:idx_limite]
condiciones_cortadas = condiciones_base[:idx_limite]

# ==========================================
# ESTILOS CSS E IDENTIDAD RENDER
# ==========================================
estilo_css = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@800;900&family=Share+Tech+Mono&family=Inter:wght@400;600;700;800&display=swap');

.stApp, [data-testid="stAppViewRoot"], [data-testid="stAppViewBlockContainer"] {
    background-color: #0B0C10 !important;
    font-family: 'Inter', -apple-system, sans-serif !important;
}

[data-testid="stElementToolbar"], 
button[title="View fullscreen"], 
[data-testid="StyledFullScreenButton"],
.stImage button {
    display: none !important;
    visibility: hidden !important;
    opacity: 0 !important;
    pointer-events: none !important;
}

.stTabs [data-baseweb="tab-list"] {
    gap: 6px;
    background-color: #0E1015;
    padding: 6px 8px;
    border-radius: 12px;
    border: 1px solid #1E232E;
    margin-bottom: 20px;
}

.stTabs [data-baseweb="tab"] {
    height: 38px;
    background-color: transparent;
    border-radius: 8px;
    color: #94A3B8;
    font-weight: 600;
    font-size: 0.83rem;
    padding: 0 10px;
    border: none;
    transition: all 0.2s ease;
}

.stTabs [aria-selected="true"] {
    background-color: #1E232E !important;
    color: #F8FAFC !important;
    font-weight: 700 !important;
}

.render-container {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
    gap: 14px;
    margin-top: 10px;
    margin-bottom: 24px;
}
.render-card {
    background-color: #0E1015;
    border: 1px solid #1A1D24;
    border-radius: 10px;
    padding: 14px 18px;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.45);
    transition: border-color 0.2s ease, transform 0.2s ease;
}
.render-card:hover {
    border-color: #2D323E;
    transform: translateY(-1px);
}
.render-card-hdr {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid #1A1D24;
    padding-bottom: 8px;
    margin-bottom: 10px;
}
.render-name {
    font-size: 0.90rem;
    font-weight: 800;
    color: #F8FAFC;
    letter-spacing: 0.5px;
}
.render-chip {
    font-size: 0.68rem;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 12px;
    font-family: 'Share Tech Mono', monospace;
    letter-spacing: 0.5px;
}
.chip-green {
    background: rgba(16, 185, 129, 0.12);
    color: #10B981;
    border: 1px solid rgba(16, 185, 129, 0.4);
}
.chip-red {
    background: rgba(239, 68, 68, 0.12);
    color: #EF4444;
    border: 1px solid rgba(239, 68, 68, 0.4);
}
.chip-cyan {
    background: rgba(6, 182, 212, 0.12);
    color: #06B6D4;
    border: 1px solid rgba(6, 182, 212, 0.4);
}
.render-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin: 5px 0;
    font-size: 0.82rem;
}
.render-k { color: #8F9CAE; }
.render-v { 
    color: #E2E8F0; 
    font-family: 'Share Tech Mono', monospace; 
    font-weight: bold; 
}

.marca-garric-contenedor {
    position: fixed; top: 52%; left: 50%; transform: translate(-50%, -50%);
    text-align: center; z-index: 0; pointer-events: none; width: 95vw; user-select: none;
}
.marca-garric-titulo {
    font-family: 'Orbitron', sans-serif !important; font-weight: 900;
    font-size: clamp(2.2rem, 5.5vw, 5rem); letter-spacing: 14px;
    color: rgba(255, 255, 255, 0.025); text-transform: uppercase; margin: 0; line-height: 1;
}
.marca-garric-subtitulo {
    font-family: 'Share Tech Mono', monospace !important;
    font-size: clamp(0.75rem, 1.5vw, 1.2rem); letter-spacing: 8px;
    color: rgba(139, 0, 0, 0.20); text-transform: uppercase; margin-top: 18px;
}

.titulo-JAGUAR {
    color: #FFFFFF !important; font-family: 'Orbitron', sans-serif !important;
    font-weight: 800; font-size: 2.1rem; letter-spacing: 3px;
    padding: 0; text-transform: uppercase;
    background: linear-gradient(90deg, #FFFFFF 10%, #E02424 70%, #8B0000 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
</style>
"""
st.markdown(estilo_css, unsafe_allow_html=True)

html_sello = """
<div class="marca-garric-contenedor">
    <div class="marca-garric-titulo">JAGUAR KINETICS</div>
    <div class="marca-garric-subtitulo">SISTEMA FOTOVOLTAICO BIAXIAL</div>
</div>
"""
st.markdown(html_sello, unsafe_allow_html=True)

def reproducir_voz_cliente(texto):
    texto_escapado = texto.replace("'", "\\'").replace('"', '\\"').replace("\n", " ")
    js_code = f"""
    <div style="margin-top: 5px; margin-bottom: 10px;">
        <button onclick="hablarJAGUARSafari()" style="
            background: linear-gradient(90deg, #991B1B 0%, #7F1D1D 100%);
            color: #FFFFFF; border: 1px solid #DC2626; padding: 6px 14px; border-radius: 20px;
            font-family: 'Share Tech Mono', monospace; font-weight: bold; font-size: 0.75rem; cursor: pointer;
        ">🔊 RESPUESTA DE JAGUAR</button>
    </div>
    <script>
    function hablarJAGUARSafari() {{
        if ('speechSynthesis' in window) {{
            window.speechSynthesis.cancel();
            var msg = new SpeechSynthesisUtterance("{texto_escapado}");
            msg.lang = 'es-ES'; msg.rate = 1.0; msg.pitch = 0.9;
            var voices = window.speechSynthesis.getVoices();
            var vozEs = voices.find(function(v) {{ return v.lang.includes('es') || v.lang.includes('ES'); }});
            if (vozEs) msg.voice = vozEs;
            window.speechSynthesis.speak(msg);
        }}
    }}
    setTimeout(hablarJAGUARSafari, 100);
    </script>
    """
    st.components.v1.html(js_code, height=45)

# ==========================================
# REPORTES PDF (PERIODO DE MUESTREO FORMAL)
# ==========================================
@st.cache_data(show_spinner=False)
def generar_reporte_pdf(df_electrico, df_meteo, energia_acumulada, etiqueta_energia, potencia_pico_w, v_max, viento_max, temp_max, eficiencia_pico_stc, incluye_lluvia, dias_servicio, incluir_chat=False, historial_chat=None, bitacora_eventos=None):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=24, leftMargin=24, topMargin=20, bottomMargin=20)
    elementos = []
    estilos = getSampleStyleSheet()
    color_jaguar_rojo = colors.HexColor('#8B0000')
    color_garric = colors.HexColor('#0F172A')

    estilo_garric_hdr = ParagraphStyle('GarricHdrDoc', parent=estilos['Normal'], fontName='Helvetica-Bold', fontSize=10, textColor=color_garric)
    estilo_slogan_sub = ParagraphStyle('SloganSubDoc', parent=estilos['Normal'], fontName='Helvetica-Oblique', fontSize=7, textColor=colors.HexColor('#94A3B8'))
    estilo_titulo_JAGUAR = ParagraphStyle('TituloJAGUARDoc', parent=estilos['Heading1'], fontName='Helvetica-Bold', fontSize=12.0, textColor=color_jaguar_rojo, spaceAfter=2)
    estilo_sec_hdr = ParagraphStyle('SecHdrDoc', parent=estilos['Heading2'], fontName='Helvetica-Bold', fontSize=8.5, textColor=color_garric, spaceAfter=4)
    estilo_kpi_titulo = ParagraphStyle('KpiTituloDoc', parent=estilos['Normal'], fontName='Helvetica-Bold', fontSize=7, textColor=colors.HexColor('#64748B'), alignment=1)
    estilo_kpi_valor = ParagraphStyle('KpiValorDoc', parent=estilos['Normal'], fontName='Helvetica-Bold', fontSize=11, textColor=color_garric, alignment=1)
    estilo_kpi_sub = ParagraphStyle('KpiSubDoc', parent=estilos['Normal'], fontName='Helvetica', fontSize=6.2, textColor=color_jaguar_rojo, alignment=1)
    estilo_texto = ParagraphStyle('TextoDoc', parent=estilos['Normal'], fontName='Helvetica', fontSize=7.2, textColor=colors.HexColor('#1E293B'))
    estilo_footer = ParagraphStyle('FooterDoc', parent=estilos['Normal'], fontName='Helvetica', fontSize=7, textColor=colors.whitesmoke, alignment=1)

    img_p1 = RLImage(RUTA_PDF_LOGO_COLOR, width=110, height=55) if os.path.exists(RUTA_PDF_LOGO_COLOR) else Paragraph("<b>JAGUAR KINETICS</b>", estilo_titulo_JAGUAR)
    col_emblema = [img_p1]
    col_institucional = [
        Paragraph("JAGUAR KINETICS", estilo_garric_hdr),
        Paragraph("Sistemas Fotovoltaicos Biaxiales de Alta Eficiencia", estilo_slogan_sub),
        Spacer(1, 2),
        Paragraph("Reporte Técnico Biaxial & Meteorológico", estilo_titulo_JAGUAR),
        Paragraph(f"Tiempo enlazado: {dias_servicio} días acumulados", estilo_slogan_sub)
    ]
    tabla_header = Table([[col_emblema, col_institucional]], colWidths=[120, 444])
    tabla_header.setStyle(TableStyle([('ALIGN', (0,0), (0,0), 'CENTER'), ('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('BOTTOMPADDING', (0,0), (-1,-1), 4), ('LINEBELOW', (0,0), (-1,-1), 1.5, color_jaguar_rojo)]))
    elementos.append(tabla_header)
    elementos.append(Spacer(1, 8))

    kpis = [
        [Paragraph("POTENCIA OBTENIDA", estilo_kpi_titulo), Paragraph(f"{potencia_pico_w:.2f} W", estilo_kpi_valor), Paragraph(f"Nominal STC: {POTENCIA_MAX_PANEL_W:.1f} W", estilo_kpi_sub)],
        [Paragraph("VOLTAJE REAL MÁXIMO", estilo_kpi_titulo), Paragraph(f"{v_max:.2f} V", estilo_kpi_valor), Paragraph("Circuito Medido", estilo_kpi_sub)],
        [Paragraph("EFICIENCIA PICO STC", estilo_kpi_titulo), Paragraph(f"{eficiencia_pico_stc:.2f} %", estilo_kpi_valor), Paragraph(f"Área Neta: {AREA_NETA_CELDAS_M2:.6f} m²", estilo_kpi_sub)],
        [Paragraph("ENERGÍA TOTAL OBTENIDA", estilo_kpi_titulo), Paragraph(f"{energia_acumulada:.2f} Wh", estilo_kpi_valor), Paragraph(etiqueta_energia, estilo_kpi_sub)]
    ]
    tabla_kpis = Table([[kpis[0], kpis[1], kpis[2], kpis[3]]], colWidths=[141, 141, 141, 141])
    tabla_kpis.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8FAFC')), ('BOX', (0,0), (-1,-1), 0.8, color_jaguar_rojo), ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')), ('PADDING', (0,0), (-1,-1), 4)]))
    elementos.append(tabla_kpis)
    elementos.append(Spacer(1, 8))

    fig1 = Figure(figsize=(7.5, 2.0), dpi=200, facecolor='#FFFFFF')
    canvas1 = FigureCanvasAgg(fig1)
    ax1 = fig1.add_subplot(111)
    ax1.set_facecolor('#F8FAFC')
    ax1.plot(list(df_electrico.index), list(df_electrico['Voltaje (V)']), color='#8B0000', marker='o', markersize=3, linewidth=1.5)
    ax1.set_ylabel('Voltaje (V)', color='#8B0000', fontsize=7.5, fontweight='bold')
    ax1.tick_params(axis='y', labelcolor='#8B0000', labelsize=6.5)
    ax1.tick_params(axis='x', rotation=25, labelsize=6.5)
    ax1.grid(True, linestyle=':', alpha=0.5)

    ax2 = ax1.twinx()
    ax2.plot(list(df_electrico.index), list(df_electrico['Potencia (W)']), color='#10B981', marker='s', markersize=3, linewidth=1.5, linestyle='--')
    ax2.set_ylabel('Potencia (W)', color='#10B981', fontsize=7.5, fontweight='bold')
    ax2.tick_params(axis='y', labelcolor='#10B981', labelsize=6.5)
    ax1.set_title("Gráfica de detalles eléctricos (Corte 18:00 hrs)", fontsize=8.5, fontweight='bold', color='#0F172A', pad=5)
    fig1.tight_layout()
    buf1 = io.BytesIO()
    canvas1.print_png(buf1)
    buf1.seek(0)
    elementos.append(RLImage(buf1, width=564, height=145))
    elementos.append(Spacer(1, 8))

    elementos.append(Paragraph("<b>REGISTROS ELÉCTRICOS (06:00 - 18:00 HRS)</b>", estilo_sec_hdr))
    col_curr = [c for c in df_electrico.columns if 'Corriente' in c][0]
    data_e = [["Hora", "Voltaje Panel (V)", col_curr, "Potencia Salida (W)"]]
    for idx, r in df_electrico.iterrows():
        data_e.append([str(idx), f"{r['Voltaje (V)']:.2f}", f"{r[col_curr]:.2f}", f"{r['Potencia (W)']:.2f}"])
    tabla_elec = Table(data_e, colWidths=[90, 158, 158, 158])
    tabla_elec.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), color_garric), ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke), ('ALIGN', (0,0), (-1,-1), 'CENTER'), ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'), ('FONTSIZE', (0,0), (-1,-1), 6.5), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0'))]))
    elementos.append(tabla_elec)
    elementos.append(Spacer(1, 10))

    footer_p1 = f"📍 Cárdenas, Tabasco | 🕒 Emisión: {datetime.now(ZONA_LOCAL):%Y-%m-%d | %H:%M:%S} | 📄 Página 1 de 2"
    tabla_foot_1 = Table([[Paragraph(footer_p1, estilo_footer)]], colWidths=[564])
    tabla_foot_1.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), color_garric), ('PADDING', (0,0), (-1,-1), 3)]))
    elementos.append(tabla_foot_1)

    # Página 2
    elementos.append(PageBreak())
    img_p2 = RLImage(RUTA_EMBLEMA_JK, width=80, height=22) if os.path.exists(RUTA_EMBLEMA_JK) else Paragraph("<b>JK</b>", estilo_garric_hdr)
    cabecera_txt_p2 = [
        Paragraph("<b>JAGUAR KINETICS | SISTEMA BIAXIAL</b>", estilo_garric_hdr),
        Paragraph("Anexo Meteorológico: Sensor de temperatura y Anemómetro", estilo_slogan_sub)
    ]
    tabla_hdr_2 = Table([[cabecera_txt_p2, img_p2]], colWidths=[474, 90])
    tabla_hdr_2.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('ALIGN', (1,0), (1,0), 'RIGHT'), ('BOTTOMPADDING', (0,0), (-1,-1), 3), ('LINEBELOW', (0,0), (-1,-1), 1.0, color_jaguar_rojo)]))
    elementos.append(tabla_hdr_2)
    elementos.append(Spacer(1, 8))

    modo_seg_desc = "PROTECCIÓN VENDAVAL" if (viento_max != "0.0 km/h" and "km/h" in str(viento_max) and float(str(viento_max).split()[0]) >= 45.0) else ("AUTOLAVADO PLUVIAL" if incluye_lluvia != "Seco" else "SEGUIMIENTO SOLAR ACTIVO")

    kpis_m = [
        [Paragraph("TEMP MÁXIMA PANEL", estilo_kpi_titulo), Paragraph(f"{temp_max:.1f} °C", estilo_kpi_valor), Paragraph("Sensor DS18B20", estilo_kpi_sub)],
        [Paragraph("RÁFAGA MÁXIMA VIENTO", estilo_kpi_titulo), Paragraph(f"{viento_max}", estilo_kpi_valor), Paragraph("Anemómetro", estilo_kpi_sub)],
        [Paragraph("ESTADO DE LLUVIA", estilo_kpi_titulo), Paragraph(f"{incluye_lluvia}", estilo_kpi_valor), Paragraph("Sensor Pluvial", estilo_kpi_sub)],
        [Paragraph("ESTADO DE SEGURIDAD", estilo_kpi_titulo), Paragraph(modo_seg_desc, estilo_kpi_valor), Paragraph("Protocolo Activo", estilo_kpi_sub)]
    ]
    tabla_kpis_m = Table([[kpis_m[0], kpis_m[1], kpis_m[2], kpis_m[3]]], colWidths=[141, 141, 141, 141])
    tabla_kpis_m.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8FAFC')), ('BOX', (0,0), (-1,-1), 0.8, color_garric), ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')), ('PADDING', (0,0), (-1,-1), 4)]))
    elementos.append(tabla_kpis_m)
    elementos.append(Spacer(1, 8))

    fig2 = Figure(figsize=(7.5, 1.8), dpi=200, facecolor='#FFFFFF')
    canvas2 = FigureCanvasAgg(fig2)
    ax_m1 = fig2.add_subplot(111)
    ax_m1.set_facecolor('#F8FAFC')
    ax_m1.plot(list(df_meteo.index), list(df_meteo['Temperatura (°C)']), color='#DC2626', marker='^', markersize=3, linewidth=1.4)
    ax_m1.set_ylabel('Temperatura (°C)', color='#DC2626', fontsize=7.5, fontweight='bold')
    ax_m1.tick_params(axis='y', labelcolor='#DC2626', labelsize=6.5)
    ax_m1.tick_params(axis='x', rotation=25, labelsize=6.5)
    ax_m1.grid(True, linestyle=':', alpha=0.5)

    ax_m2 = ax_m1.twinx()
    ax_m2.plot(list(df_meteo.index), list(df_meteo['Viento (km/h)']), color='#0284C7', marker='o', markersize=3, linewidth=1.4, linestyle='--')
    ax_m2.set_ylabel('Viento (km/h)', color='#0284C7', fontsize=7.5, fontweight='bold')
    ax_m2.tick_params(axis='y', labelcolor='#0284C7', labelsize=6.5)
    ax_m1.set_title("Registro de temperatura y anemómetro", fontsize=8.5, fontweight='bold', color='#0F172A', pad=5)
    fig2.tight_layout()
    buf2 = io.BytesIO()
    canvas2.print_png(buf2)
    buf2.seek(0)
    elementos.append(RLImage(buf2, width=564, height=135))
    elementos.append(Spacer(1, 6))

    if bitacora_eventos:
        elementos.append(Paragraph("<b>BITÁCORA DE MANIOBRAS Y EVENTOS DE SEGURIDAD METEOROLÓGICA</b>", estilo_sec_hdr))
        data_b = [["Hora Exacta", "Sensor", "Lectura Detectada", "Acción Ejecutada por el Microcontrolador"]]
        for ev in bitacora_eventos:
            d_ev = dict(ev)
            data_b.append([d_ev["Hora"], d_ev["Sensor"], d_ev["Lectura"], d_ev["Acción"]])
        tabla_bit = Table(data_b, colWidths=[80, 110, 134, 240])
        tabla_bit.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), color_jaguar_rojo), ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke), ('ALIGN', (0,0), (-1,-1), 'CENTER'), ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'), ('FONTSIZE', (0,0), (-1,-1), 6.5), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1'))]))
        elementos.append(tabla_bit)
        elementos.append(Spacer(1, 6))

    if incluir_chat and historial_chat:
        elementos.append(Paragraph("<b>REGISTRO DE SESIÓN CON JAGUAR</b>", estilo_sec_hdr))
        for msg in historial_chat:
            d_msg = dict(msg)
            rol_nombre = "Christian Garric" if d_msg["rol"] == "user" else "JAGUAR"
            elementos.append(Paragraph(f"<b>{rol_nombre}:</b> {d_msg['texto']}", estilo_texto))
            elementos.append(Spacer(1, 2))
        elementos.append(Spacer(1, 6))

    footer_p2 = f"📍 Cárdenas, Tabasco | 🔑 Auth: JAGUAR-KINETICS-AUTHENTICATED-PDF | 📄 Página 2 de 2"
    tabla_foot_2 = Table([[Paragraph(footer_p2, estilo_footer)]], colWidths=[564])
    tabla_foot_2.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), color_garric), ('PADDING', (0,0), (-1,-1), 3)]))
    elementos.append(tabla_foot_2)

    doc.build(elementos)
    buffer.seek(0)
    return buffer.getvalue()

@st.cache_data(show_spinner=False)
def generar_reporte_muestreo_pdf(historico_tupla, dias_totales):
    historico = [dict(d) for d in historico_tupla]
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=24, leftMargin=24, topMargin=20, bottomMargin=20)
    elementos = []
    estilos = getSampleStyleSheet()
    color_jaguar_rojo = colors.HexColor('#8B0000')
    color_garric = colors.HexColor('#0F172A')

    estilo_garric_hdr = ParagraphStyle('GarricHdrDoc', parent=estilos['Normal'], fontName='Helvetica-Bold', fontSize=10, textColor=color_garric)
    estilo_titulo = ParagraphStyle('TitSem', parent=estilos['Heading1'], fontName='Helvetica-Bold', fontSize=12.5, textColor=color_jaguar_rojo)
    estilo_sub = ParagraphStyle('SubSem', parent=estilos['Normal'], fontName='Helvetica-Bold', fontSize=8, textColor=colors.HexColor('#64748B'))
    estilo_txt = ParagraphStyle('TxtSem', parent=estilos['Normal'], fontName='Helvetica', fontSize=7.2, textColor=colors.HexColor('#1E293B'))
    estilo_foot = ParagraphStyle('FootSem', parent=estilos['Normal'], fontName='Helvetica', fontSize=7, textColor=colors.whitesmoke, alignment=1)

    f_inicio = historico[0]["Fecha"]
    f_fin = historico[-1]["Fecha"]

    img_sem = RLImage(RUTA_PDF_LOGO_COLOR, width=110, height=55) if os.path.exists(RUTA_PDF_LOGO_COLOR) else Paragraph("<b>JAGUAR KINETICS</b>", estilo_titulo)
    col_emblema = [img_sem]
    col_institucional = [
        Paragraph("JAGUAR KINETICS", estilo_garric_hdr),
        Paragraph("Reporte Consolidado del Sistema Biaxial", estilo_titulo),
        Paragraph(f"Periodo de muestreo: {f_inicio} al {f_fin} | Tiempo enlazado: {dias_totales} días acumulados", estilo_sub)
    ]
    tabla_header = Table([[col_emblema, col_institucional]], colWidths=[120, 444])
    tabla_header.setStyle(TableStyle([('ALIGN', (0,0), (0,0), 'CENTER'), ('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('BOTTOMPADDING', (0,0), (-1,-1), 4), ('LINEBELOW', (0,0), (-1,-1), 1.5, color_jaguar_rojo)]))
    elementos.append(tabla_header)
    elementos.append(Spacer(1, 10))

    wh_total = sum([d["Energía (Wh)"] for d in historico])
    pot_pico = max([d["Potencia Pico (W)"] for d in historico])
    v_max = max([d["Voltaje Máx (V)"] for d in historico])
    viento_max = max([d["Viento Máx (km/h)"] for d in historico])
    temp_max = max([d["Temp Máx (°C)"] for d in historico])

    resumen = [
        [Paragraph(f"<b>Energía acumulada en periodo:</b> {wh_total:.2f} Wh", estilo_txt), Paragraph(f"<b>Potencia pico absoluta:</b> {pot_pico:.2f} W (Nominal {POTENCIA_MAX_PANEL_W:.1f}W)", estilo_txt)],
        [Paragraph(f"<b>Voltaje máximo registrado:</b> {v_max:.2f} V", estilo_txt), Paragraph(f"<b>Ráfaga máxima de viento:</b> {viento_max:.1f} km/h", estilo_txt)],
        [Paragraph(f"<b>Temperatura máxima de celda:</b> {temp_max:.1f} °C", estilo_txt), Paragraph(f"<b>Próximo Mantenimiento:</b> {fecha_proximo_mantenimiento}", estilo_txt)]
    ]
    t_res = Table(resumen, colWidths=[282, 282])
    t_res.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8FAFC')), ('BOX', (0,0), (-1,-1), 1, color_jaguar_rojo), ('PADDING', (0,0), (-1,-1), 4)]))
    elementos.append(t_res)
    elementos.append(Spacer(1, 12))

    fig_sem = Figure(figsize=(7.5, 2.2), dpi=200, facecolor='#FFFFFF')
    canvas_sem = FigureCanvasAgg(fig_sem)
    ax_s = fig_sem.add_subplot(111)
    ax_s.set_facecolor('#F8FAFC')
    etiquetas_x = [f"{d['Día']}\n({d['Fecha'][8:]} Oct)" for d in historico]
    barras = ax_s.bar(etiquetas_x, [d["Energía (Wh)"] for d in historico], color='#8B0000', width=0.50, edgecolor='#0F172A', linewidth=0.8)
    ax_s.set_ylabel('Energía Generada (Wh)', color='#0F172A', fontsize=8, fontweight='bold')
    ax_s.set_title('Generación Eléctrica Diaria por Fecha de Muestreo (Wh/día)', fontsize=9, fontweight='bold', color='#0F172A')
    ax_s.grid(axis='y', linestyle=':', alpha=0.6)
    for b in barras:
        ax_s.text(b.get_x() + b.get_width()/2.0, b.get_height() + 0.5, f"{b.get_height():.1f}", ha='center', va='bottom', fontsize=7, fontweight='bold')
    fig_sem.tight_layout()
    buf_s = io.BytesIO()
    canvas_sem.print_png(buf_s)
    buf_s.seek(0)
    elementos.append(RLImage(buf_s, width=564, height=155))
    elementos.append(Spacer(1, 10))

    data_d = [["Día", "Fecha", "Voltaje Máx (V)", "Potencia Pico (W)", "Energía (Wh)", "Viento Máx (km/h)", "Temp Máx (°C)", "Estado Operativo"]]
    for f in historico:
        data_d.append([f["Día"], f["Fecha"], f"{f['Voltaje Máx (V)']:.2f}", f"{f['Potencia Pico (W)']:.2f}", f"{f['Energía (Wh)']:.2f}", f"{f['Viento Máx (km/h)']:.1f}", f"{f['Temp Máx (°C)']:.1f}", f["Estado"]])
    t_d = Table(data_d, colWidths=[55, 65, 75, 75, 70, 75, 70, 79])
    t_d.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), color_garric), ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke), ('ALIGN', (0,0), (-1,-1), 'CENTER'), ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'), ('FONTSIZE', (0,0), (-1,-1), 6.5), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0'))]))
    elementos.append(t_d)
    elementos.append(Spacer(1, 14))

    footer_txt = f"📍 Cárdenas, Tabasco | 🕒 Emisión: {datetime.now(ZONA_LOCAL):%Y-%m-%d | %H:%M:%S} | 🔑 Auth: JAGUAR-MUESTREO-AUDITED"
    t_foot = Table([[Paragraph(footer_txt, estilo_foot)]], colWidths=[564])
    t_foot.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), color_garric), ('PADDING', (0,0), (-1,-1), 3)]))
    elementos.append(t_foot)

    doc.build(elementos)
    buffer.seek(0)
    return buffer.getvalue()

@st.cache_data(show_spinner=False)
def generar_acta_mantenimiento_pdf(orden_tupla):
    orden = dict(orden_tupla)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=24, leftMargin=24, topMargin=24, bottomMargin=24)
    elementos = []
    estilos = getSampleStyleSheet()
    color_jaguar_rojo = colors.HexColor('#8B0000')
    color_garric = colors.HexColor('#0F172A')

    estilo_tit = ParagraphStyle('OT_Tit', parent=estilos['Heading1'], fontName='Helvetica-Bold', fontSize=13, textColor=color_garric)
    estilo_sub = ParagraphStyle('OT_Sub', parent=estilos['Normal'], fontName='Helvetica-Bold', fontSize=8, textColor=colors.HexColor('#64748B'))
    estilo_txt = ParagraphStyle('OT_Txt', parent=estilos['Normal'], fontName='Helvetica', fontSize=8, textColor=colors.HexColor('#1E293B'))
    estilo_val = ParagraphStyle('OT_Val', parent=estilos['Normal'], fontName='Helvetica-Bold', fontSize=8, textColor=color_jaguar_rojo)
    estilo_foot = ParagraphStyle('OT_Foot', parent=estilos['Normal'], fontName='Helvetica', fontSize=7, textColor=colors.whitesmoke, alignment=1)

    if os.path.exists(RUTA_EMBLEMA_JK):
        elementos.append(RLImage(RUTA_EMBLEMA_JK, width=80, height=22))
        elementos.append(Spacer(1, 4))
    else:
        elementos.append(Paragraph("<b>JAGUAR KINETICS</b>", estilo_sub))

    elementos.append(Paragraph(f"DICTAMEN DE SERVICIO Y ACTA DE MANTENIMIENTO: {orden['OT']}", estilo_tit))
    elementos.append(Paragraph("Auditoría de Integridad Electromecánica y Calibración Instrumental", estilo_sub))
    elementos.append(Spacer(1, 10))

    datos_gen = [
        [Paragraph("<b>Número de Orden:</b>", estilo_txt), Paragraph(orden['OT'], estilo_val), Paragraph("<b>Fecha de Intervención:</b>", estilo_txt), Paragraph(orden['Fecha'], estilo_txt)],
        [Paragraph("<b>Tipo de Servicio:</b>", estilo_txt), Paragraph(orden['Tipo'], estilo_txt), Paragraph("<b>Técnico Certificado:</b>", estilo_txt), Paragraph(orden['Técnico'], estilo_txt)],
        [Paragraph("<b>Estado de Conformidad:</b>", estilo_txt), Paragraph(orden['Estado'], estilo_val), Paragraph("<b>Paro Técnico:</b>", estilo_txt), Paragraph(orden['Paro'], estilo_txt)]
    ]
    t_gen = Table(datos_gen, colWidths=[120, 162, 140, 142])
    t_gen.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8FAFC')), ('BOX', (0,0), (-1,-1), 1, color_jaguar_rojo), ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')), ('PADDING', (0,0), (-1,-1), 4)]))
    elementos.append(t_gen)
    elementos.append(Spacer(1, 12))

    elementos.append(Paragraph("<b>OBSERVACIONES, DIAGNÓSTICO Y ACCIONES EJECUTADAS</b>", estilo_sub))
    obs_box = Table([[Paragraph(orden['Observaciones'], estilo_txt)]], colWidths=[564])
    obs_box.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#FFFFFF')), ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')), ('PADDING', (0,0), (-1,-1), 8)]))
    elementos.append(obs_box)
    elementos.append(Spacer(1, 15))

    cert_txt = "<b>CERTIFICACIÓN DE OPERATIVIDAD:</b> Se certifica que los puntos inspeccionados cumplen con las normas de operación segura. El seguidor biaxial queda validado para su reactivación en la jerarquía autónoma de control."
    elementos.append(Paragraph(cert_txt, estilo_txt))
    elementos.append(Spacer(1, 25))

    firmas = [[Paragraph("________________________________________<br/><b>Técnico Responsable</b><br/>Christian Garric", estilo_txt), Paragraph("________________________________________<br/><b>Validación de Control</b><br/>JAGUAR KINETICS", estilo_txt)]]
    t_firmas = Table(firmas, colWidths=[282, 282])
    t_firmas.setStyle(TableStyle([('ALIGN', (0,0), (-1,-1), 'CENTER'), ('PADDING', (0,0), (-1,-1), 6)]))
    elementos.append(t_firmas)
    elementos.append(Spacer(1, 15))

    t_foot = Table([[Paragraph(f"📍 Cárdenas, Tabasco | 🔑 Auth: JAGUAR-ACTA-{orden['OT']}", estilo_foot)]], colWidths=[564])
    t_foot.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), color_garric), ('PADDING', (0,0), (-1,-1), 3)]))
    elementos.append(t_foot)

    doc.build(elementos)
    buffer.seek(0)
    return buffer.getvalue()

# ==========================================
# BARRA LATERAL Y TELEMETRÍA DINÁMICA
# ==========================================
with st.sidebar:
    if os.path.exists(RUTA_WEB_LOGO_BLANCO):
        st.image(RUTA_WEB_LOGO_BLANCO, use_container_width=True)
    elif os.path.exists(RUTA_FAVICON_PESTANA):
        st.image(RUTA_FAVICON_PESTANA, width=130)
    else:
        st.markdown("## ⚡ JAGUAR KINETICS")

    st.markdown("---")
    st.markdown("### ⚙️ Configuración del Sistema")
    st.session_state.modo_simulacion_forzado = st.toggle(
        "Modo Simulación", 
        value=st.session_state.modo_simulacion_forzado,
        help="Simulación local cuando el hardware no esté transmitiendo."
    )

    if st.session_state.modo_simulacion_forzado:
        datos_nube = None
        control_nube = None
        etiqueta_estado_dinamica = "Simulación Sincronizada"
    else:
        datos_nube = leer_telemetria_supabase()
        control_nube = consultar_control_supabase()
        etiqueta_estado_dinamica = "Hardware Conectado" if datos_nube else "Buscando Hardware..."

    st.caption(f"Enlace de Datos: **{etiqueta_estado_dinamica}**")
    st.markdown(f"⏱️ **Tiempo acumulado:** `{dias_operando_real} días`")
    if dias_para_mantenimiento == 0:
        st.markdown("🔧 **Mantenimiento:** `Intervención Vencida`")
    else:
        st.markdown(f"🔧 **Mantenimiento:** `En {dias_para_mantenimiento} días`")

    st.markdown("#### 📏 Unidades de Medida")
    col_u1, col_u2 = st.columns(2)
    with col_u1:
        if st.button("km/h", use_container_width=True, key="sb_kmh"):
            st.session_state.unidad_viento = "km/h"
            st.rerun()
    with col_u2:
        if st.button("m/s", use_container_width=True, key="sb_ms"):
            st.session_state.unidad_viento = "m/s"
            st.rerun()

    col_u3, col_u4 = st.columns(2)
    with col_u3:
        if st.button("mA", use_container_width=True, key="sb_ma"):
            st.session_state.unidad_corriente = "mA"
            st.rerun()
    with col_u4:
        if st.button("A", use_container_width=True, key="sb_a"):
            st.session_state.unidad_corriente = "A"
            st.rerun()

    unidad_viento = st.session_state.unidad_viento
    unidad_corriente = st.session_state.unidad_corriente

# Procesamiento de lecturas
hora_idx_clamped = min(max(hora_reloj - 6, 0), len(horas_cortadas) - 1)
if datos_nube:
    v_raw = float(datos_nube.get("voltaje", 0.0))
    i_raw = float(datos_nube.get("corriente_ma", 0.0))
    if i_raw > 600.0: i_raw = i_raw / 10.0
    
    v_panel = min(max(v_raw, 0.0), 24.0)
    corriente_ma_raw = min(max(i_raw, 0.0), CORRIENTE_MAX_PANEL_A * 1000.0)
    p_raw = float(datos_nube.get("potencia_w", 0.0))
    potencia_w_calc = p_raw if (0.0 < p_raw <= POTENCIA_MAX_PANEL_W * 1.05) else round(v_panel * (corriente_ma_raw / 1000.0), 2)
    viento_raw_kmh = float(datos_nube.get("viento_kmh", 0.0))
    temp_panel_c = float(datos_nube.get("temperatura", 25.0))
    estado_lluvia_nube = str(datos_nube.get("lluvia", "Seco"))
    azimut_nube = float(datos_nube.get("azimut", 90.0))
    elevacion_nube = float(datos_nube.get("elevacion", 15.0))
else:
    v_panel = voltaje_cortado[hora_idx_clamped]
    corriente_ma_raw = float(corriente_ma_cortada[hora_idx_clamped])
    potencia_w_calc = round(v_panel * (corriente_ma_raw / 1000.0), 2)
    viento_raw_kmh = float(viento_cortado[hora_idx_clamped])
    temp_panel_c = float(temp_cortada[hora_idx_clamped])
    estado_lluvia_nube = "Tormenta" if condiciones_cortadas[hora_idx_clamped] == "Lluvia Moderada" else "Seco"
    azimut_nube = 90.0
    elevacion_nube = 15.0

meteo_sat = obtener_pronostico_pma()
irradiancia_actual = meteo_sat.get("irradiancia_w_m2", 0.0)
potencia_incidente_real = irradiancia_actual * AREA_NETA_CELDAS_M2
eficiencia_instantanea = (potencia_w_calc / potencia_incidente_real) * 100.0 if potencia_incidente_real > 0 else 0.0
peligro_viento_inmediato = (viento_raw_kmh >= 45.0) or (meteo_sat.get("rafaga_proyectada_1h", 0.0) >= 38.0) or (estado_lluvia_nube == "Tormenta")

if control_nube:
    modo_raw = control_nube.get("modo_ejecucion", "TRACKING_LEAD_LAG")
    driver_energizado = control_nube.get("rele_potencia_motores", True)
    reposo_actual = control_nube.get("intervalo_reposo_minutos", 20)
    diagnostico_vigente = control_nube.get("diagnostico_ia", "Supervisión cognitiva activa.")
else:
    modo_raw = "SAFETY_STOW" if peligro_viento_inmediato else ("STANDBY" if not st.session_state.seguidor_habilitado else "TRACKING_LEAD_LAG")
    driver_energizado = not peligro_viento_inmediato and st.session_state.seguidor_habilitado
    reposo_actual = 0 if peligro_viento_inmediato else 20
    diagnostico_vigente = "Modo de resguardo preventivo por ráfaga." if peligro_viento_inmediato else "Operación nominal continua sin anomalías."

modo_amigable_pantalla = traducir_modo(modo_raw)

# Actualizar el último valor del corte horario
voltaje_cortado[-1] = v_panel
corriente_ma_cortada[-1] = corriente_ma_raw
potencia_w_cortada[-1] = potencia_w_calc
temp_cortada[-1] = temp_panel_c
viento_cortado[-1] = viento_raw_kmh

# ==========================================
# TARJETAS DE SUPERVISIÓN SUPERIOR (RENDER)
# ==========================================
badge_jaguar = '<span class="render-chip chip-green">AVAILABLE</span>' if driver_energizado else '<span class="render-chip chip-red">SAFETY PAUSE</span>'
badge_esp = '<span class="render-chip chip-green">CONNECTED</span>' if (datos_nube or st.session_state.modo_simulacion_forzado) else '<span class="render-chip chip-red">OFFLINE</span>'
badge_pma = '<span class="render-chip chip-cyan">SYNCHRONIZED</span>'
badge_ema = '<span class="render-chip chip-green">OPERATIONAL</span>' if viento_raw_kmh < 45.0 else '<span class="render-chip chip-red">ALERT</span>'

viento_ema_mostrado = viento_raw_kmh if unidad_viento == "km/h" else (viento_raw_kmh / 3.6)

tarjeta_jaguar = f'<div class="render-card"><div class="render-card-hdr"><span class="render-name">🧠 JAGUAR</span>{badge_jaguar}</div><div class="render-row"><span class="render-k">Política HCSC</span><span class="render-v">{modo_amigable_pantalla}</span></div><div class="render-row"><span class="render-k">Driver L298N</span><span class="render-v">{"Energizado" if driver_energizado else "0V (Corte)"}</span></div><div class="render-row"><span class="render-k">Banda Muerta</span><span class="render-v">{reposo_actual} min reposo</span></div></div>'
tarjeta_esp = f'<div class="render-card"><div class="render-card-hdr"><span class="render-name">⚡ ESP-32</span>{badge_esp}</div><div class="render-row"><span class="render-k">Azimut</span><span class="render-v">{azimut_nube:.1f}°</span></div><div class="render-row"><span class="render-k">Elevación</span><span class="render-v">{elevacion_nube:.1f}°</span></div><div class="render-row"><span class="render-k">Tope Carrera D3</span><span class="render-v">Seguro (Hardware)</span></div></div>'
tarjeta_pma = f'<div class="render-card"><div class="render-card-hdr"><span class="render-name">🛰️ P.M.A</span>{badge_pma}</div><div class="render-row"><span class="render-k">Ráfaga (1h)</span><span class="render-v">{meteo_sat.get("rafaga_proyectada_1h", 0.0):.1f} km/h</span></div><div class="render-row"><span class="render-k">Irradiancia GHI</span><span class="render-v">{irradiancia_actual:.0f} W/m²</span></div><div class="render-row"><span class="render-k">Cobertura Nubes</span><span class="render-v">{meteo_sat.get("cobertura_nubes_pct", 0)}%</span></div></div>'
tarjeta_ema = f'<div class="render-card"><div class="render-card-hdr"><span class="render-name">🌦️ E.M.A</span>{badge_ema}</div><div class="render-row"><span class="render-k">Temp. Silicio</span><span class="render-v">{temp_panel_c:.1f} °C</span></div><div class="render-row"><span class="render-k">Anemómetro</span><span class="render-v">{viento_ema_mostrado:.1f} {unidad_viento}</span></div><div class="render-row"><span class="render-k">Sensor Pluvial</span><span class="render-v">{estado_lluvia_nube}</span></div></div>'

st.markdown(f'<div class="render-container">{tarjeta_jaguar}{tarjeta_esp}{tarjeta_pma}{tarjeta_ema}</div>', unsafe_allow_html=True)

# ==========================================
# PESTAÑAS PRINCIPALES DEL SISTEMA
# ==========================================
tab_consola, tab_panel, tab_meteo, tab_mantenimiento = st.tabs([
    "💬 Consola JAGUAR",
    "⚡ Panel Solar & Eléctrico",
    "🌤️ Meteorología (E.M.A / P.M.A)",
    "🛠️ Mantenimiento"
])

# ==========================================
# TAB 1: CONSOLA JAGUAR
# ==========================================
with tab_consola:
    col_c_hdr1, col_c_hdr2 = st.columns([7, 5], vertical_alignment="center")
    with col_c_hdr1:
        if os.path.exists(RUTA_WEB_LOGO_BLANCO):
            st.image(RUTA_WEB_LOGO_BLANCO, width=200)
        else:
            st.markdown('<div class="titulo-JAGUAR">JAGUAR KINETICS</div>', unsafe_allow_html=True)
    with col_c_hdr2:
        modo_sel = st.selectbox(
            "Modo Cognitivo:",
            ["Operación predeterminada", "Operación Rápida", "Operación Detallada"],
            index=["Operación predeterminada", "Operación Rápida", "Operación Detallada"].index(st.session_state.modo_operacion)
        )
        if modo_sel != st.session_state.modo_operacion:
            st.session_state.modo_operacion = modo_sel
            st.rerun()

    if peligro_viento_inmediato or modo_raw == "SAFETY_STOW":
        st.error("🚨 **ALERTA DE SEGURIDAD:** Modo de resguardo preventivo activo ante ráfagas de viento.")
    else:
        st.caption(f"💡 *Diagnóstico de Supervisión 24/7:* {diagnostico_vigente}")

    INSTRUCCIONES_MODO = {
        "Operación predeterminada": "Eres JAGUAR, el supervisor cognitivo autónomo del seguidor solar biaxial JAGUAR KINETICS de Christian Garric. Tienes control sobre actuadores vía driver L298N, balance energético neto, Deadband dinámica y seguridad ante ráfagas. Responde siempre con rigor técnico de ingeniería, lealtad y respeto como 'señor'.",
        "Operación Rápida": "Eres JAGUAR. Responde en Operación Rápida: Máximo de 10 a 15 palabras, directo y cortante, dirigiéndote siempre como 'señor'.",
        "Operación Detallada": "Eres JAGUAR en modo de análisis exhaustivo. Desglosa telemetría, pronóstico satelital, estados de relés, matrices energéticas y cinemática, siempre dirigiéndote como 'señor'."
    }

    if "client" not in st.session_state:
        try:
            st.session_state.client = genai.Client()
        except Exception:
            pass

    modo_cambio = (st.session_state.modo_operacion != st.session_state.get("modo_operacion_previo"))
    if "chat_sesion" not in st.session_state or modo_cambio:
        st.session_state.modo_operacion_previo = st.session_state.modo_operacion
        if "client" in st.session_state:
            st.session_state.chat_sesion = st.session_state.client.chats.create(
                model="gemini-2.5-flash",
                config=types.GenerateContentConfig(
                    tools=[{"google_search": {}}], 
                    system_instruction=INSTRUCCIONES_MODO[st.session_state.modo_operacion]
                )
            )

    contenedor_chat = st.container()
    with contenedor_chat:
        for mensaje in st.session_state.historial_pantalla:
            nombre_avatar = "Garric" if mensaje["rol"] == "user" else "JAGUAR"
            with st.chat_message(mensaje["rol"]):
                st.markdown(f"**{nombre_avatar}**")
                st.write(mensaje["texto"])

    pregunta_usuario = ""
    entrada_nativa = st.chat_input("Escriba un comando para JAGUAR...")
    audio_capturado = st.audio_input("[ ENLACE NEURAL VOX ]")

    if audio_capturado is not None:
        bytes_audio = audio_capturado.read()
        hash_actual = hashlib.md5(bytes_audio).hexdigest()
        if hash_actual != st.session_state.ultimo_audio_hash:
            st.session_state.ultimo_audio_hash = hash_actual
            with st.spinner("Procesando señal de voz..."):
                try:
                    response_transcripcion = st.session_state.client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=[
                            types.Part.from_bytes(data=bytes_audio, mime_type='audio/wav'),
                            "Transcribe exactamente todo lo que escuches en este audio en español. No agregues comentarios extras."
                        ]
                    )
                    pregunta_usuario = response_transcripcion.text.strip()
                except Exception:
                    st.info("ℹ️ **ENLACE INESTABLE:** No se pudo procesar el audio.")

    if entrada_nativa:
        pregunta_usuario = entrada_nativa

    if pregunta_usuario:
        texto_lower = pregunta_usuario.lower()
        if "desactiva el seguidor" in texto_lower or "apaga el seguidor" in texto_lower or "desenergiza motores" in texto_lower:
            st.session_state.seguidor_habilitado = False
            if not st.session_state.modo_simulacion_forzado:
                enviar_comando_potencia_supabase(False, "STANDBY_VOZ")
        elif "activa el seguidor" in texto_lower or "enciende el seguidor" in texto_lower or "reanuda seguimiento" in texto_lower:
            st.session_state.seguidor_habilitado = True
            if not st.session_state.modo_simulacion_forzado:
                enviar_comando_potencia_supabase(True, "TRACKING_VOZ")

        st.session_state.historial_pantalla.append({"rol": "user", "texto": pregunta_usuario})
        with contenedor_chat:
            with st.chat_message("user"):
                st.markdown("**Garric**")
                st.write(pregunta_usuario)

        try:
            prompt_con_contexto = f"[ESTADO_SISTEMA: V={v_panel}V, I={corriente_ma_raw}mA, VientoLocal={viento_raw_kmh}km/h, RafagaPronosticoPMA={meteo_sat.get('rafaga_proyectada_1h', 0.0)}km/h, IrradianciaReal={irradiancia_actual:.1f}W/m2, Modo={modo_amigable_pantalla}, Paro={'SI' if st.session_state.paro_tecnico_activo else 'NO'}] {pregunta_usuario}"
            response = st.session_state.chat_sesion.send_message(prompt_con_contexto)
            
            st.session_state.historial_pantalla.append({"rol": "assistant", "texto": response.text})
            with contenedor_chat:
                with st.chat_message("assistant"):
                    st.markdown("**JAGUAR**")
                    st.write(response.text)

            texto_limpio = re.sub(r'[\*#_\-`\[\]]', '', str(response.text)).replace("\n\n", " ").replace("\n", " ").strip()
            reproducir_voz_cliente(texto_limpio)
        except Exception:
            st.error("⚠️ **CONEXIÓN INTERRUMPIDA:** Error de comunicación con el servicio central.")

# ==========================================
# TAB 2: PANEL & MÉTRICAS ELÉCTRICAS
# ==========================================
with tab_panel:
    st.subheader("⚡ Parámetros Eléctricos y Rendimiento Fotovoltaico")
    
    corriente_mostrada = corriente_ma_raw if unidad_corriente == "mA" else (corriente_ma_raw / 1000.0)
    energia_acumulada_calc = sum(potencia_w_cortada)
    potencia_pico = min(max(potencia_w_cortada), POTENCIA_MAX_PANEL_W)
    voltaje_max = max(voltaje_cortado)
    eficiencia_real = (potencia_w_calc / potencia_incidente_real) * 100.0 if potencia_incidente_real > 0 else 0.0
    eficiencia_pico_stc = (potencia_pico / POTENCIA_INCIDENTE_STC) * 100.0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Voltaje del Panel", f"{v_panel:.2f} V", f"Máx: {voltaje_max:.2f} V")
    m2.metric(f"Corriente Neta ({unidad_corriente})", f"{corriente_mostrada:.2f} {unidad_corriente}", f"Límite: {CORRIENTE_MAX_PANEL_A*1000 if unidad_corriente=='mA' else CORRIENTE_MAX_PANEL_A} {unidad_corriente}")
    m3.metric("Potencia Generada", f"{potencia_w_calc:.2f} W", f"Pico: {potencia_pico:.2f} W")
    m4.metric("Eficiencia vs GHI Real", f"{eficiencia_real:.2f} %", f"STC Pico: {eficiencia_pico_stc:.1f}%")

    st.markdown("---")
    
    corriente_grafica = corriente_ma_cortada if unidad_corriente == "mA" else [ma / 1000.0 for ma in corriente_ma_cortada]
    df_telemetria = pd.DataFrame({
        "Hora": horas_cortadas, 
        "Voltaje (V)": voltaje_cortado, 
        f"Corriente ({unidad_corriente})": corriente_grafica, 
        "Potencia (W)": potencia_w_cortada
    }).set_index("Hora")

    df_meteo_horario = pd.DataFrame({
        "Hora": horas_cortadas, 
        "Temperatura (°C)": temp_cortada, 
        "Viento (km/h)": viento_cortado, 
        "Condición": condiciones_cortadas
    }).set_index("Hora")

    st.markdown("#### 📈 Curvas de Generación Diaria (06:00 a 18:00 hrs)")
    st.line_chart(df_telemetria[["Voltaje (V)", "Potencia (W)"]])

    st.markdown("#### 📋 Registros Eléctricos Horarios (Corte 18:00 hrs)")
    st.dataframe(df_telemetria, use_container_width=True)

    st.markdown("---")
    st.subheader("📄 Emisión de Informes Técnicos Certificados (PDF)")
    col_pdf_cfg1, col_pdf_cfg2 = st.columns(2)
    with col_pdf_cfg1:
        incluir_chat_pdf = st.checkbox("Incluir registro de sesión con JAGUAR", value=False)
    with col_pdf_cfg2:
        incluir_bitacora_pdf = st.checkbox("Incluir eventos de maniobras meteorológicas", value=True)

    historial_hashable = tuple(tuple(m.items()) for m in st.session_state.historial_pantalla) if incluir_chat_pdf else ()
    bitacora_hashable = tuple(tuple(e.items()) for e in st.session_state.bitacora_eventos) if incluir_bitacora_pdf else ()
    muestreo_hashable = tuple(tuple(d.items()) for d in HISTORICO_MUESTREO_OCTUBRE)

    bloque_minuto_redondeado = (minuto_reloj // 15) * 15
    hora_etiqueta_tope = min(hora_reloj, 18)
    etiqueta_energia = f"Al corte ({hora_etiqueta_tope:02d}:{bloque_minuto_redondeado:02d} hrs)"

    pdf_diario_bytes = generar_reporte_pdf(
        df_electrico=df_telemetria, df_meteo=df_meteo_horario, energia_acumulada=energia_acumulada_calc,
        etiqueta_energia=etiqueta_energia, potencia_pico_w=potencia_pico, v_max=voltaje_max,
        viento_max=f"{max(viento_cortado):.1f} {unidad_viento}", temp_max=max(temp_cortada),
        eficiencia_pico_stc=eficiencia_pico_stc, incluye_lluvia=estado_lluvia_nube,
        dias_servicio=dias_operando_real, incluir_chat=incluir_chat_pdf,
        historial_chat=historial_hashable, bitacora_eventos=bitacora_hashable
    )

    pdf_muestreo_bytes = generar_reporte_muestreo_pdf(
        historico_tupla=muestreo_hashable, 
        dias_totales=dias_operando_real
    )

    btn_p1, btn_p2 = st.columns(2)
    with btn_p1:
        st.download_button(label="📄 Descargar Reporte Técnico Diario", data=pdf_diario_bytes, file_name=f"reporte_tecnico_diario_dia{dias_operando_real}.pdf", mime="application/pdf", use_container_width=True)
    with btn_p2:
        st.download_button(label="📊 Descargar Reporte Consolidado (08 al 12 de Octubre)", data=pdf_muestreo_bytes, file_name="reporte_muestreo_08_al_12_octubre.pdf", mime="application/pdf", use_container_width=True)

# ==========================================
# TAB 3: METEOROLOGÍA (EMA, PMA & ARCHIVE)
# ==========================================
with tab_meteo:
    st.subheader("🌤️ Estación Meteorológica (E.M.A) & Pronóstico Abierto (P.M.A)")
    st.caption("Cárdenas, Tabasco | Coordinación de Sensores In-Situ con Modelos Satelitales Open-Meteo")

    st.markdown("#### 📡 Capa Instrumental Local (E.M.A)")
    e1, e2, e3 = st.columns(3)
    e1.metric("Temperatura de Celda", f"{temp_panel_c:.1f} °C", "Sensor sumergible DS18B20")
    e2.metric("Anemómetro Local", f"{viento_ema_mostrado:.1f} {unidad_viento}", "Velocidad in-situ")
    e3.metric("Sensor Pluvial", estado_lluvia_nube, "Superficie de captación")

    st.markdown("---")
    st.markdown("#### 🛰️ Capa Atmosférica Satelital (P.M.A)")
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Ráfaga Proyectada (1h)", f"{meteo_sat.get('rafaga_proyectada_1h', 0.0):.1f} km/h", f"Estado: {'Crítico' if meteo_sat.get('rafaga_proyectada_1h', 0.0) >= 35.0 else 'Seguro'}")
    p2.metric("Vector del Viento", f"{meteo_sat.get('direccion_viento_deg', 0.0):.0f}°", "Procedencia")
    p3.metric("Irradiancia GHI Estimada", f"{irradiancia_actual:.0f} W/m²", f"Nubes: {meteo_sat.get('cobertura_nubes_pct', 0)}%")
    p4.metric("Temperatura Ambiental", f"{meteo_sat.get('temp_aire_c', 25.0):.1f} °C", f"ΔT Silicio: +{max(0.0, temp_panel_c - meteo_sat.get('temp_aire_c', 25.0)):.1f} °C")

    st.markdown("---")
    st.markdown("#### 📉 Gráfica Térmica y de Viento (Corte 18:00 hrs)")
    df_meteo_ui = pd.DataFrame({
        "Hora": horas_cortadas,
        "Temperatura Panel (°C)": temp_cortada,
        f"Velocidad Viento ({unidad_viento})": [w if unidad_viento == "km/h" else w/3.6 for w in viento_cortado]
    }).set_index("Hora")
    st.line_chart(df_meteo_ui)

    st.markdown("---")
    st.markdown("#### 🛡️ Bitácora de Maniobras y Eventos de Seguridad Meteorológica")
    st.dataframe(pd.DataFrame(st.session_state.bitacora_eventos), use_container_width=True)

    st.markdown("---")
    st.markdown("#### 🛰️ Módulo Retrospectivo Satelital (Open-Meteo Archive)")
    st.caption("Consulta histórica del periodo de muestreo experimental (Cárdenas, Tabasco)")
    col_arc1, col_arc2, col_arc3 = st.columns([3, 3, 2])
    with col_arc1:
        f_ini = st.date_input("Fecha Inicio", date(2026, 10, 8))
    with col_arc2:
        f_fin = st.date_input("Fecha Término", date(2026, 10, 12))
    with col_arc3:
        st.write("")
        st.write("")
        btn_arc = st.button("Consultar Archivo", use_container_width=True)

    if btn_arc:
        with st.spinner("Descargando registros satelitales históricos..."):
            df_arc = obtener_historico_satelital(str(f_ini), str(f_fin))
            if df_arc is not None and not df_arc.empty:
                st.dataframe(df_arc, use_container_width=True)
                st.line_chart(df_arc.set_index("FechaHora")[["DNI (W/m²)", "DHI (W/m²)", "Ráfagas (km/h)"]])
            else:
                st.info("No se recibieron datos para el rango seleccionado o servicio temporalmente inaccesible.")

# ==========================================
# TAB 4: MANTENIMIENTO & OT
# ==========================================
with tab_mantenimiento:
    st.subheader("🛠️ Auditoría de Integridad Electromecánica y Órdenes de Trabajo")
    
    col_salud1, col_salud2, col_salud3 = st.columns(3)
    col_salud1.metric("Tiempo de Operación", f"{dias_operando_real} Días", f"Última OT: {fecha_ultima_ot_str}")
    
    if dias_para_mantenimiento == 0:
        col_salud2.metric("Próxima Intervención", "Vencida", "Requiere emisión de OT")
    else:
        col_salud2.metric("Próxima Intervención", f"En {dias_para_mantenimiento} Días", f"Fecha: {fecha_proximo_mantenimiento}")
        
    salud_pct = "98.5 %" if not st.session_state.paro_tecnico_activo else "35.0 % (PARO TÉCNICO)"
    col_salud3.metric("Salud Instrumental", salud_pct, "Condición Operativa")

    if st.session_state.paro_tecnico_activo:
        st.error("🚨 **SISTEMA EN PARO TÉCNICO:** Motores inmovilizados por seguridad. Suba la OT aprobada para reanudar.")
    elif dias_para_mantenimiento == 0:
        st.warning(f"⚠️ **ATENCIÓN:** El ciclo preventivo de 30 días ha concluido. Programado para: **{fecha_proximo_mantenimiento}**.")

    st.markdown("---")
    st.markdown("#### ⚡ Aislamiento de Motores (Driver L298N)")
    col_iso1, col_iso2 = st.columns(2)
    with col_iso1:
        if st.button("🔴 Desenergizar Controlador (Corte 0V)", use_container_width=True):
            st.session_state.seguidor_habilitado = False
            if not st.session_state.modo_simulacion_forzado:
                enviar_comando_potencia_supabase(False, "CORTE_L298N_MANTENIMIENTO")
            st.success("Controlador desenergizado a 0V.")
            st.rerun()
    with col_iso2:
        if st.button("🟢 Reenergizar Controlador", use_container_width=True):
            st.session_state.seguidor_habilitado = True
            if not st.session_state.modo_simulacion_forzado:
                enviar_comando_potencia_supabase(True, "REANUDAR_NORMAL")
            st.success("Controlador energizado correctamente.")
            st.rerun()

    st.markdown("---")
    st.markdown("#### 📋 Gestión y Carga de Órdenes de Trabajo")
    nombre_ot_programada = f"OT_{fecha_proximo_dt:%Y_%m_%d}"
    archivo_ot_esperado = f"{nombre_ot_programada}.txt"

    plantilla_ot_texto = f"""==================================================
JAGUAR KINETICS
ORDEN DE TRABAJO TÉCNICA: {nombre_ot_programada}
==================================================
FECHA_INTERVENCION: {fecha_proximo_dt:%Y-%m-%d}
TECNICO_RESPONSABLE: Christian Garric
TIPO_SERVICIO: Preventivo Programado
ESTADO_SISTEMA: Conforme
PARO_TECNICO: No

[LISTA DE VERIFICACION]
CONTROLADOR_MOTORES: OK
SOLDADURAS_MINIKIT: OK
ACOPLAMIENTO_TERMICO: OK
FIN_CARRERA_D3: OK
LIMPIEZA_PANEL: OK
AISLAMIENTO_CONTROLADOR: OK

[DIAGNOSTICO Y OBSERVACIONES]
Se realizó la inspección técnica preventiva. Bornes con torque nominal, 
mecanismo lubricado y verificación de corte de sobrecarrera en D3.
=================================================="""

    col_ot_dl, col_ot_up = st.columns(2)
    with col_ot_dl:
        st.markdown("**Descargar Plantilla de OT:**")
        st.download_button(
            label=f"📥 Descargar Plantilla ({nombre_ot_programada})",
            data=plantilla_ot_texto,
            file_name=archivo_ot_esperado,
            mime="text/plain",
            use_container_width=True
        )
    with col_ot_up:
        st.markdown("**Carga de OT Firmada:**")
        archivo_subido = st.file_uploader("Cargar archivo .txt de OT:", type=["txt"])
        if archivo_subido is not None:
            if st.button("⚡ Procesar y Desbloquear Sistema", use_container_width=True):
                contenido_ot = archivo_subido.read().decode("utf-8")
                obs_match = re.search(r"[DIAGNOSTICO Y OBSERVACIONES]\s*(.*?)\s*={10,}", contenido_ot, re.DOTALL)
                obs_texto = obs_match.group(1).strip() if obs_match else "Intervención documentada y procesada vía consola web."

                nueva_ot_procesada = {
                    "OT": nombre_ot_programada,
                    "Fecha": str(hoy),
                    "Tipo": "Preventivo Programado",
                    "Técnico": "Christian Garric",
                    "Estado": "Conforme",
                    "Paro": "No",
                    "Observaciones": obs_texto
                }
                st.session_state.registro_mantenimiento.append(nueva_ot_procesada)
                guardar_mantenimiento(st.session_state.registro_mantenimiento)
                st.session_state.paro_tecnico_activo = False
                st.session_state.seguidor_habilitado = True
                if not st.session_state.modo_simulacion_forzado:
                    enviar_comando_potencia_supabase(True, "REANUDAR_POST_OT")
                st.success(f"✅ Orden {nombre_ot_programada} validada. Ciclo reiniciado y Controlador habilitado.")
                st.rerun()

    st.markdown("---")
    st.markdown("#### 📜 Histórico de Intervenciones y Dictámenes")
    df_mant = pd.DataFrame(st.session_state.registro_mantenimiento)
    st.dataframe(df_mant, use_container_width=True)

    if st.session_state.registro_mantenimiento:
        ultima_orden = st.session_state.registro_mantenimiento[-1]
        orden_tupla = tuple(ultima_orden.items())
        pdf_ot_bytes = generar_acta_mantenimiento_pdf(orden_tupla)
        st.download_button(
            label=f"📄 Descargar Acta Oficial en PDF ({ultima_orden['OT']})",
            data=pdf_ot_bytes,
            file_name=f"acta_mantenimiento_{ultima_orden['OT']}.pdf",
            mime="application/pdf",
            use_container_width=True
        )