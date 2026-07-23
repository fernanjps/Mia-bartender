# ============================================================
# MIA - Configuración Central
# ============================================================
import os

# --- Conexión al S25 Ultra (Ollama remoto / Tethering) ---
S25_PORT = 8080
S25_IPS = [
    "192.168.8.72",     # IP WiFi actual del S25 Ultra (Detectada por Socket.IO al conectarse)
    "10.193.241.97",    # Red anterior
    "127.0.0.1",        # ADB Forward (USB Debugging)
    "localhost",        # ADB Forward Fallback
    "10.71.27.194",     # Fallback
    "10.71.27.1",
    "10.71.27.254",
    "100.100.192.27",
    "10.53.226.5",
    "10.53.227.223",
    "192.168.56.1",
    "192.168.43.1"
]
S25_IP = S25_IPS[0]    # IP por defecto (la primera — más reciente)
S25_URL = f"http://{S25_IP}:{S25_PORT}"

# --- Modelos ---
BRAIN_MODEL = "llama3.2:latest"
VISION_MODEL = "llava:latest"

# --- Respaldo Híbrido Cloud (Groq API Fallback si el S25 Ultra no está conectado) ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "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")
GROQ_MODEL = "llama-3.1-8b-instant"  # Modelo liviano ultra-rápido con altos límites
MODELO_GROQ = GROQ_MODEL
GROQ_ENABLED = True

# --- Ollama Híbrido (USB tethering al S25 Ultra) ---
#LOCAL_OLLAMA_URL = "http://localhost:11434"

# URLs de los modelos:
BRAIN_URL = S25_URL            # Cerebro en el S25 Ultra
VISION_URL = S25_URL           # Moondream en el S25 Ultra



# --- LLM Parameters ---
LLM_CONTEXT_SIZE = 2048       # num_ctx - limitado por VRAM del S25
LLM_TEMPERATURE = 0.7         # Creatividad en respuestas
BRAIN_TIMEOUT = 120           # Segundos máximo para esperar respuesta del S25 (la primera carga es lenta)

# --- Wake Word ---
WAKE_WORD = "mia"             # Palabra clave central
# Todas las variantes fonéticas que Google STT en español puede producir
WAKE_PHRASES = [
    "hey mia", "oye mia", "ey mia", "hola mia", "oiga mia",
    "ei mia", "ay mia", "ahi mia", "a mia", "jay mia",
    "he mia", "je mia", "y mia", "hi mia", "ale mia",
    "hey mía", "oye mía", "ey mía", "hola mía",
    "a ver mia", "o mia", "eh mia", "mi a", "mía",
]

# --- Audio / Ear ---
# ENABLE_BACKEND_MIC = False apaga por completo la escucha en la laptop
# Toda la escucha se hará nativamente desde el navegador del celular (Brave/Chrome)
ENABLE_BACKEND_MIC = False
MICROPHONE_NAME = "Microphone Array (AMD Audio"  # (Ignorado si ENABLE_BACKEND_MIC es False)
LISTEN_TIMEOUT = 5            # Segundos de silencio antes de dejar de escuchar wake word
COMMAND_TIMEOUT = 10          # Segundos esperando comando después de activarse
COMMAND_PHRASE_LIMIT = 30     # Máximo de segundos hablando un comando
AMBIENT_NOISE_DURATION = 1.5  # Segundos de calibración de ruido al inicio
STT_LANGUAGE = "es-ES"        # Idioma para Google Speech-to-Text
MIN_ENERGY_THRESHOLD = 3500   # UMBRAL MUY ALTO para rechazar música. Para ambientes ruidosos se debe usar el botón PTT.

# --- Vision / Eye ---
CAMERA_INDEX = 0  # Cámara Web integrada de la Laptop USB (evita red Wi-Fi y latencias)
CHANGE_THRESHOLD = 3000       # Umbral de pixeles cambiados para detectar movimiento
CAMERA_WARMUP = 0.3           # Reducido — la cámara USB responde rápido para capturas on-demand

# --- Voice ---
VOICE_RATE = 150              # Palabras por minuto
VOICE_VOLUME = 0.9            # Volumen (0.0 - 1.0)

# --- Assistant ---
PROACTIVE_VISION = False       # False = visión solo cuando el usuario habla (ahorra recursos)
                               # True  = hilo automático que observa y comenta
VISUAL_COMMENT_COOLDOWN = 30  # Segundos mínimos entre comentarios visuales proactivos
VISION_CHECK_INTERVAL = 10    # Segundos entre chequeos de visión
VISION_POST_COMMENT_PAUSE = 20 # Pausa extra después de comentar (evita saturar)
HEALTH_CHECK_INTERVAL = 120   # Segundos entre chequeos de salud (no saturar la consola)
DEBUG_EAR = False             # True = imprimir todo lo que el micrófono escucha

# --- Routing Inteligente (visión solo cuando se pide) ---
# Si el comando contiene alguna de estas palabras -> usar cámara + Moondream
# Si no las contiene -> responder rápido sin visión
VISION_KEYWORDS = [
    "mira", "observa", "foto", "fotografía",
    "imagen", "cámara", "camara", "muestra",
    "qué ves", "que ves", "qué hay", "que hay",
    "dime qué ves", "dime que ves", "describe",
    "analiza", "identifica", "reconoce"
]

# --- Conversation History ---
MAX_HISTORY_TURNS = 1         # Reducido a 1 para garantizar estabilidad de tokens y evitar crashear el celular

# --- Memoria a Largo Plazo (ChromaDB) ---
MEMORY_ENABLED = False                               # Desactivado a petición del usuario para PRIORIZAR VELOCIDAD
MEMORY_DIR = os.path.join(os.path.dirname(__file__), "mia_memory")  # Carpeta de persistencia
MEMORY_RESULTS_LIMIT = 1     # Reducido a 1 recuerdo para no saturar el prompt

MIA_SYSTEM_PROMPT = (
    "Eres MIA, bartender virtual ingeniosa. Responde en español, neutra y amigable. "
    "REGLAS: "
    "1. Recomienda y prepara bebidas SOLO basándote en los 'Cócteles disponibles'. "
    "2. Si piden algo que no tienes, avisa ingeniosamente y ofrece opciones de lo que SÍ hay. "
    "3. Si piden algo creativo o improvisado, inventa una mezcla usando ÚNICAMENTE los 'Ingredientes en barra'. Puedes usar [ROBOT:MEZCLAR:ingr1,ingr2...] para preparar tu invento. "
    "4. Desvía matemáticas o cosas dañinas hacia cócteles. Integra recuerdos naturalmente sin repetir etiquetas. "
    "No saludes con 'Hola' si la charla ya inició. Máximo 2 oraciones."
)

# --- Raspberry Pi (Bartender Robot) ---
ROBOT_ENABLED = True
ROBOT_CONNECTION_TYPE = "TCP" # "TCP" o "SERIAL"
ROBOT_IP = "192.168.10.2"    # IP estática de la Raspberry Pi (tethering/red)
ROBOT_PORT = 5001             # Puerto de la Raspberry Pi si es TCP
ROBOT_SERIAL_PORT = "COM3"    # Puerto Serial si es USB Serial
ROBOT_SERIAL_BAUD = 9600

# --- Configuración de Bombas e Ingredientes Físicos (Posición en ms/cm) ---
BOMBAS_CONFIG = {
    "pump_1": {"ingrediente": "Refresco de toronja (Witi)", "cm": 1250},
    "pump_2": {"ingrediente": "Jugo de limón", "cm": 2500},
    "pump_3": {"ingrediente": "Tequila", "cm": 3150},
    "pump_4": {"ingrediente": "Licor de naranja", "cm": 5000}
}

# --- Recetario de Cócteles Multibomba (Mezclas) ---
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

