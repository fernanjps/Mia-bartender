# ============================================================
# MIA - Configuración Central (Raspberry Pi 5 - Arquitectura Monolítica)
# ============================================================
import os

def _load_dotenv():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key:
                    os.environ.setdefault(key, val)
    except Exception as e:
        print(f"⚠️ [CONFIG] Error leyendo .env: {e}")

_load_dotenv()

# --- Motor Primario Local (Ollama en Raspberry Pi 5 - 8GB RAM) ---
OLLAMA_LOCAL_URL = os.getenv("OLLAMA_LOCAL_URL", "http://localhost:11434")
LOCAL_MODEL = os.getenv("LOCAL_MODEL", "qwen2.5:3b")
BRAIN_MODEL = LOCAL_MODEL
VISION_MODEL = os.getenv("VISION_MODEL", "moondream")
OLLAMA_NUM_THREADS = int(os.getenv("OLLAMA_NUM_THREADS", "4"))  # 4 núcleos Cortex-A76 en RPi 5

# --- Motor Secundario Cloud / Fallback (Consultas complejas >15 palabras o generales) ---
CLOUD_ENABLED = False # Activar fallback a motor en la nube (Groq / OpenAI)
CLOUD_TIMEOUT = 3.5  # Timeout estricto de 3.5 segundos para fallback silencioso
CLOUD_API_URL = os.getenv("CLOUD_API_URL", "https://api.groq.com/openai/v1/chat/completions")
CLOUD_API_KEY = os.getenv("CLOUD_API_KEY") or os.getenv("GROQ_API_KEY") or ""
CLOUD_MODEL = os.getenv("CLOUD_MODEL", "llama-3.1-8b-instant")

# --- LLM Parameters ---
LLM_CONTEXT_SIZE = 2048       # num_ctx en Ollama local
LLM_TEMPERATURE = 0.5         # Precisión en respuestas y comandos
LLM_MAX_TOKENS = 120          # Respuestas cortas = menos latencia en la Pi
OLLAMA_KEEP_ALIVE = "60m"     # Mantener el modelo cargado en RAM (evita recarga de ~5-10s)
BRAIN_TIMEOUT = 30            # Segundos máximo de espera para Ollama local

# --- Wake Word ---
# Motor: "openwakeword" (recomendado, usa tu hey_mia.onnx), "vosk" o "stt" (Google, requiere internet)
WAKEWORD_ENGINE = os.getenv("WAKEWORD_ENGINE", "openwakeword")
WAKEWORD_MODEL_PATH = os.getenv(
    "WAKEWORD_MODEL_PATH", os.path.join(os.path.dirname(__file__), "models", "hey_mia.onnx")
)
WAKEWORD_THRESHOLD = float(os.getenv("WAKEWORD_THRESHOLD", "0.42"))  # Súbelo si se activa sola; bájalo si no escucha
WAKEWORD_COOLDOWN = 2.0       # Segundos mínimos entre dos activaciones
WAKE_WORD = "mia"
VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", os.path.join(os.path.dirname(__file__), "model"))
VOSK_GRAMMAR = [
    "hey mia", "oye mia", "hola mia", "mia",
    "prepara", "preparame", "sirve", "sirveme", "dame", "quiero", "ponme", "hazme",
    "el", "la", "un", "una", "coctel", "trago", "bebida", "opcion", "numero",
    "uno", "dos", "tres", "cuatro", "primero", "segundo", "tercero", "cuarto",
    "paloma", "paloma dulce", "margarita", "margarita con toronja", "tequila citrus",
    "menu", "carta", "que tienes", "que hay", "otro igual", "lo mismo",
    "como te llamas", "quien eres", "como va mi trago", "que ves", "mira",
    "[unk]"
]
WAKE_PHRASES = ["hey mia", "oye mia", "ey mia", "hola mia", "mia", "hey mía", "oye mía", "mía"]

# --- Hardware GPIO (Raspberry Pi 5 con gpiozero) ---
# Driver L298N (Riel motor DC)
GPIO_MOTOR_IN1 = 16
GPIO_MOTOR_IN2 = 20
GPIO_MOTOR_ENA = 21  # PWM
MOTOR_VELOCIDAD = 1.0 # 0.0 a 1.0 (100% torque)
SEGUNDOS_POR_CM = 0.08

# Bombas Peristálticas (Módulo de Relés activos en bajo)
PUMPS_PINS = {
    "pump_1": 19,
    "pump_2": 6,
    "pump_3": 13,
    "pump_4": 5
}

# Botón PTT Físico (Hardware Activation) y Sensores Opcionales (0 = desactivado)
GPIO_PIN_BUTTON = 26
GPIO_LIMIT_SWITCH = int(os.getenv("GPIO_LIMIT_SWITCH", "0"))
GPIO_CUP_SENSOR = int(os.getenv("GPIO_CUP_SENSOR", "0"))

# Mapeo Físico de Bombas, Ingredientes y Posición en Riel (cm) - Calibración banco de pruebas real
BOMBAS_CONFIG = {
    "pump_1": {"ingrediente": "Refresco de toronja (Witi)", "cm": 9},
    "pump_2": {"ingrediente": "Jugo de limón", "cm": 13},
    "pump_3": {"ingrediente": "Tequila", "cm": 20},
    "pump_4": {"ingrediente": "Licor de naranja", "cm": 27}
}

# Recetario de Cócteles Multibomba
RECETAS_COCTELES = {
    "Paloma": {
        "Tequila": 15,
        "Refresco de toronja (Witi)": 15,
        "Jugo de limón": 15
    },
    "Margarita con toronja": {
        "Tequila": 15,
        "Licor de naranja": 15,
        "Jugo de limón": 15,
        "Refresco de toronja (Witi)": 20
    },
    "Tequila Citrus": {
        "Tequila": 15,
        "Jugo de limón": 15,
        "Licor de naranja": 15
    },
    "Paloma Dulce": {
        "Tequila": 15,
        "Refresco de toronja (Witi)": 15,
        "Licor de naranja": 20,
        "Jugo de limón": 10
    }
}

# --- Audio / Ear ---
ENABLE_BACKEND_MIC = True
MICROPHONE_NAME = "USB"       # Detecta micrófono USB/Jack en Raspberry Pi
LISTEN_TIMEOUT = 5            # Segundos de silencio
COMMAND_TIMEOUT = 8           # Segundos esperando comando
COMMAND_PHRASE_LIMIT = 20     # Máximo de segundos por frase
AMBIENT_NOISE_DURATION = 1.0  # Calibración inicial
STT_LANGUAGE = "es-ES"
MIN_ENERGY_THRESHOLD = 3000

# --- Vision / Eye ---
CAMERA_INDEX = 0              # Cámara USB local / CSI en Raspberry Pi 5
CHANGE_THRESHOLD = 3000
CAMERA_WARMUP = 0.3

# --- Voice (Edge-TTS + Fallback Offline) ---
VOICE_RATE = 150
VOICE_VOLUME = 0.9
EDGE_TTS_TIMEOUT = float(os.getenv("EDGE_TTS_TIMEOUT", "4.0"))

# --- Assistant ---
PROACTIVE_VISION = False
PROACTIVE_POUR_COMMENT = os.getenv("PROACTIVE_POUR_COMMENT", "true").lower() in ("1", "true", "yes")
VISUAL_COMMENT_COOLDOWN = 30
VISION_CHECK_INTERVAL = 10
VISION_POST_COMMENT_PAUSE = 20
HEALTH_CHECK_INTERVAL = 120
DEBUG_EAR = False

VISION_KEYWORDS = [
    "mira", "observa", "foto", "fotografía",
    "imagen", "cámara", "camara", "muestra",
    "qué ves", "que ves", "qué hay", "que hay",
    "dime qué ves", "dime que ves", "describe",
    "analiza", "identifica", "reconoce"
]

MAX_HISTORY_TURNS = 2
MEMORY_ENABLED = True
MEMORY_DIR = os.path.join(os.path.dirname(__file__), "mia_memory")
MEMORY_RESULTS_LIMIT = 1
SESSION_TIMEOUT_SECONDS = int(os.getenv("SESSION_TIMEOUT_SECONDS", "90"))

# --- Seguridad HTTP / Socket.IO / IoT (Tailscale, ESP32, Arduino, App) ---
API_TOKEN = os.getenv("MIA_API_TOKEN", "").strip()
ALLOW_TAILSCALE_WITHOUT_TOKEN = os.getenv("MIA_ALLOW_TAILSCALE", "true").lower() in ("1", "true", "yes")
REQUIRE_CUP_SENSOR = os.getenv("MIA_REQUIRE_CUP_SENSOR", "false").lower() in ("1", "true", "yes")
ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("MIA_ALLOWED_ORIGINS", "").split(",") if o.strip()
]

MIA_SYSTEM_PROMPT = (
    "Tu nombre es MIA. Eres una bartender carismática, perspicaz y muy profesional detrás de una barra robótica automática. "
    "Si te preguntan tu nombre responde estrictamente 'Me llamo MIA'. "
    "Detect the language of the user's input: if the user speaks in English, answer strictly in English and pivot cleverly to cocktails. "
    "If the user speaks in Spanish, answer strictly in Spanish. "
    "PIVOTE COCTELERO CONSTANTE: Sin importar lo que el usuario pregunte (filosofía, ciencia, matemáticas, política o absurdos), "
    "responde con ingenio y enlaza siempre el tema hacia la coctelería, el ambiente de bar o una bebida de la carta. "
    "MANEJO DE TEMAS OSCUROS: Ante preguntas sobre sustancias peligrosas, daños o cosas indebidas, desvía con ingenio y humor de cantina "
    "sin romper el personaje ni dar instrucciones nocivas (ejemplo: 'De venenos no sé nada, pero si buscas algo que te noquee el día, te sirvo un Tequila Citrus bien cargado'). "
    "Si el usuario se presenta o simplemente platica, sé amable y no prepares nada sin pedido explícito. "
    "Gramática: si te refieres al usuario por su nombre di 'Te llamas [Nombre]'. Nunca digas 'Me llama [Nombre]'. "
    "CONCISIÓN ESTRICTA: Responde SIEMPRE en máximo 2 oraciones cortas para minimizar la latencia. "
    "No uses emojis, listas, asteriscos, corchetes ni formato markdown. "
    "Solo prepara bebidas del MENÚ disponible; nunca inventes otras bebidas."
)


