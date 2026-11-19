"""
MIA - Cerebro Híbrido Embebido (Raspberry Pi 5)

Diseño:
- SYSTEM PROMPT: Personalidad bartender carismática, pivote coctelero constante y manejo ingenioso de temas oscuros.
- CONTROL DINÁMICO DE INVENTARIO Y FALTANTES:
  * Validación en tiempo real de bombas (disponible / ml_restantes).
  * Si falta un ingrediente, NO se genera [DISPENSE] y se avisa con alternativas disponibles.
  * Menú hablado filtra dinámicamente cócteles 100% disponibles.
- FILTRO DE ALERGIAS E INGREDIENTES:
  * Detección de advertencias médicas y dietéticas (alergia, embarazo, sin alcohol).
  * Consultas de ingredientes ("¿qué tragos tienen limón?").
- BLINDAJE CONTRA PEDIDOS FALSOS: Presentaciones y saludos nunca disparan pedidos.
- DETECCIÓN DETERMINISTA DE NOMBRE: Persiste de inmediato en user_profile.json.
- IDENTIDAD GRAMATICAL: "Te llamas [Nombre]" al recordar al usuario.
- MEMORIA LIGERA JSON: Persistencia instantánea sin dependencias pesadas.
- SOPORTE BILINGÜE: Detección automática español/inglés en respuestas deterministas y LLM.
- MOTOR CONVERSACIONAL: Ollama local (qwen2.5:1.5b) con streaming y fallback cloud opcional.
"""

import os
import sys
import re
import json
import time
import threading
import unicodedata
import requests
import ollama

try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass

from config import (
    OLLAMA_LOCAL_URL, LOCAL_MODEL, OLLAMA_NUM_THREADS,
    CLOUD_ENABLED, CLOUD_TIMEOUT, CLOUD_API_URL, CLOUD_API_KEY, CLOUD_MODEL,
    LLM_CONTEXT_SIZE, LLM_TEMPERATURE, LLM_MAX_TOKENS, OLLAMA_KEEP_ALIVE,
    MIA_SYSTEM_PROMPT, BOMBAS_CONFIG, RECETAS_COCTELES,
    MAX_HISTORY_TURNS, MEMORY_ENABLED, MEMORY_RESULTS_LIMIT
)
from memory import Memory
from voice import is_english_text


def _clean(s):
    """minúsculas, sin acentos, sin signos."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    s = re.sub(r"[^a-z0-9ñ ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# Verbos explícitos de preparación en Español
ORDER_WORDS_ES = [
    "prepara", "preparame", "preparas", "prepares", "sirve", "sirveme", "sirvas",
    "hazme", "haz me", "dame", "deme", "quiero", "quisiera", "ponme", "regalame",
    "me das", "me da", "me puedes preparar", "me podrias preparar", "me haces",
    "me sirves", "traeme", "otra", "otro", "pideme", "me antoja", "preparate"
]

# Verbos explícitos de preparación en Inglés
ORDER_WORDS_EN = [
    "prepare", "make", "serve", "give me", "i want", "id like", "i'd like",
    "can i have", "could i have", "pour me", "get me", "order"
]

# Preguntas informativas (NO son pedidos)
INFO_WORDS = [
    "que lleva", "que tiene", "ingrediente", "como se hace", "de que es",
    "cuanto", "que es", "what is in", "ingredients", "how do you make"
]

# Palabras de menú en Español (explícitas vs. ambiguas que chocan con visión)
EXPLICIT_MENU_WORDS_ES = [
    "menu", "carta", "catalogo", "que bebidas", "que tragos", "que cocteles",
    "que puedes preparar", "que preparas", "que sirves", "lista de bebidas",
    "que hay de tomar", "que hay para tomar", "que hay de beber", "que hay para beber",
    "que tienes de tomar", "que tienes para tomar", "que tienes de beber", "que tienes disponible"
]
AMBIGUOUS_MENU_WORDS_ES = ["que tienes", "que hay", "opciones"]

# Palabras de menú en Inglés
EXPLICIT_MENU_WORDS_EN = [
    "what can you make", "what drinks do you have", "what drinks do you serve",
    "what can you serve", "drinks list", "cocktail list", "drink options",
    "what is available"
]
AMBIGUOUS_MENU_WORDS_EN = ["what do you have"]

# Indicadores de contexto visual para evitar confundir preguntas de visión con el menú
VISION_CONTEXT_WORDS = [
    "mira", "observa", "foto", "fotografia", "imagen", "camara", "muestra",
    "que ves", "dime que ves", "describe", "analiza", "identifica", "reconoce",
    "mesa", "mano", "aqui", "esto", "eso", "frente", "delante", "pantalla",
    "look", "see", "table", "hand", "camera", "photo"
]

# Preguntas por el nombre de MIA en Español
MIA_NAME_WORDS_ES = [
    "como te llamas", "cual es tu nombre", "quien eres",
    "tu nombre", "como es tu nombre", "como te decian",
    "dime tu nombre", "me dices tu nombre"
]

# Preguntas por el nombre de MIA en Inglés
MIA_NAME_WORDS_EN = [
    "what is your name", "whats your name", "what s your name",
    "who are you", "what are you called", "tell me your name"
]

# Palabras que excluyen terminantemente la interpretación de un pedido
EXCLUDE_ORDER_KEYWORDS = [
    "me llamo", "mi nombre", "recuerda que", "my name", "call me",
    "quien soy", "who am i", "como te llamas", "what is your name",
    "te acuerdas", "sabes mi nombre", "do you remember", "alergia", "alergico",
    "alergica", "allergy", "allergic", "embarazo", "embarazada", "pregnant"
]

# Palabras clave para activar consulta condicional a la memoria
MEMORY_KEYWORDS = [
    "recuerdas", "te acuerdas", "mi favorito", "mi favorita",
    "que me gusta", "que te dije", "te habia dicho", "te había dicho",
    "sabes quien soy", "mi nombre", "me conoces",
    "de que hablamos", "lo que te conte", "lo que te conté", "recuerda",
    "remember", "favorite", "my name", "who am i", "do you know me"
]

# Definición centralizada de alérgenos/restricciones:
# (clave, palabras_disparadoras, subcadenas_en_ingredientes, nombre_es, nombre_en)
ALLERGEN_DEFINITIONS = [
    ("alcohol", ["alcohol", "embarazo", "embarazada", "pregnant", "pregnancy", "destilado", "licor"],
     ["tequila", "licor"], "alcohol", "alcohol"),
    ("citricos", ["citrico", "citricos", "citrus"],
     ["toronja", "limon", "naranja"], "cítricos", "citrus"),
    ("toronja", ["toronja", "grapefruit", "witi", "pomelo"],
     ["toronja"], "toronja", "grapefruit"),
    ("limon", ["limon", "lemon", "lime", "lima"],
     ["limon"], "limón", "lemon"),
    ("tequila", ["tequila", "agave"],
     ["tequila"], "tequila", "tequila"),
    ("naranja", ["naranja", "orange", "triple sec"],
     ["naranja"], "naranja", "orange"),
]

# Patrones de negación para filtrar cláusulas donde el cliente rechaza un trago
NEGATION_PATTERNS = [
    r"\bno\b", r"\bnunca\b", r"\bjamas\b", r"\bnada\b", r"\bni\b",
    r"\bcancela\w*\b", r"\bodio\b", r"\bdetesto\b", r"\bmenos\b",
    r"\bdon'?t\b", r"\bdont\b", r"\bdon\s+t\b", r"\bdo\s+not\b",
    r"\bdoesn'?t\b", r"\bdoesnt\b", r"\bdoesn\s+t\b",
    r"\bwon'?t\b", r"\bwont\b", r"\bwon\s+t\b",
    r"\bnever\b", r"\bnot\b", r"\bcancel\b", r"\bhate\b", r"\bdislike\b", r"\bstop\b",
]

# Mapa de números y ordinales para pedidos independientes del nombre ("cóctel 1", "opción tres")
SLOT_NUMBER_MAP = {
    "1": 1, "uno": 1, "primero": 1, "primera": 1, "primer": 1, "one": 1, "first": 1, "1st": 1,
    "2": 2, "dos": 2, "segundo": 2, "segunda": 2, "two": 2, "second": 2, "2nd": 2,
    "3": 3, "tres": 3, "tercero": 3, "tercera": 3, "tercer": 3, "three": 3, "third": 3, "3rd": 3,
    "4": 4, "cuatro": 4, "cuarto": 4, "cuarta": 4, "four": 4, "fourth": 4, "4th": 4,
    "5": 5, "cinco": 5, "quinto": 5, "quinta": 5, "five": 5, "fifth": 5, "5th": 5,
    "6": 6, "seis": 6, "sexto": 6, "sexta": 6, "six": 6, "sixth": 6, "6th": 6,
}

# Frases repetitivas ("otro igual", "lo mismo")
REPEAT_ORDER_PHRASES = [
    "otro igual", "otra igual", "lo mismo", "el mismo", "la misma",
    "repite", "repiteme", "otro trago igual", "same again", "another one", "same drink"
]

# Escudo determinista contra jailbreak / temas peligrosos / pruebas maliciosas
SAFETY_PIVOT_PATTERNS = [
    r"\bveneno\w*\b", r"\bcianuro\b", r"\barsenico\b", r"\bexplosivo\w*\b",
    r"\bhacer una bomba\b", r"\bfabricar una bomba\b", r"\bbomba casera\b",
    r"\bmatar\w*\b", r"\basesin\w*\b", r"\bsuicid\w*\b", r"\bdroga\w*\b",
    r"\bcocaina\b", r"\bmetanfetamina\b", r"\bfentanilo\b", r"\bpistola\w*\b",
    r"\barmas?\b", r"\bhackea\w*\b", r"\bmalware\b", r"\bransomware\b",
    r"\brobar\b", r"\bilegal\w*\b", r"\bacido\b", r"\bcloroformo\b",
    r"\bignora tus instrucciones\b", r"\bolvida tus instrucciones\b",
    r"\bolvida que eres\b", r"\bdeja de ser\b", r"\bsystem prompt\b",
    r"\bjailbreak\b", r"\bdan mode\b", r"\bignore previous\b",
    r"\bignore all instructions\b", r"\bforget you are\b", r"\bpoison\w*\b",
    r"\bexplosive\w*\b", r"\bkill\b", r"\bsuicide\b", r"\bweapon\w*\b", r"\bhack\b"
]

SENTENCE_END = re.compile(r"[.!?;\n]")


class Brain:
    """Inferencia híbrida + motor de pedidos determinista + inventario dinámico + memoria ligera."""

    def __init__(self):
        self.local_url = OLLAMA_LOCAL_URL
        self.active_url = self.local_url
        self.local_model = LOCAL_MODEL
        self.ollama_client = ollama.Client(host=self.local_url)

        try:
            self.memory = Memory() if MEMORY_ENABLED else None
        except Exception as e:
            print(f"⚠️ [BRAIN] Error inicializando memoria: {e}")
            self.memory = None

        self._history = []
        self._pending_dispense = None
        self._lock = threading.Lock()
        self._inv_cache = None
        self._inv_mtime = 0.0

        print(f"🧠 [BRAIN] Motor local Ollama ({self.local_model}) en {self.local_url}")
        avail_drinks = [d["nombre"] for d in self.get_menu(only_available=True)]
        all_drinks = [d["nombre"] for d in self.get_menu(only_available=False)]
        print(f"🍹 [BRAIN] Cócteles configurados ({len(all_drinks)}): {', '.join(all_drinks)}")
        print(f"✅ [BRAIN] Cócteles disponibles en stock ({len(avail_drinks)}): {', '.join(avail_drinks) or 'NINGUNO'}")
        if CLOUD_ENABLED and CLOUD_API_KEY:
            print(f"☁️ [BRAIN] Respaldo Cloud ({CLOUD_MODEL}) con timeout de {CLOUD_TIMEOUT}s.")

        threading.Thread(target=self._warmup, daemon=True, name="BrainWarmup").start()

    # ------------------------------------------------------------------
    # Menú e Inventario Dinámico (Validación de Stock en Tiempo Real)
    # ------------------------------------------------------------------

    def _load_inventory(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inventario.json")
        try:
            if os.path.exists(path):
                mtime = os.path.getmtime(path)
                if self._inv_cache is not None and mtime == self._inv_mtime:
                    return self._inv_cache
                with open(path, "r", encoding="utf-8") as f:
                    self._inv_cache = json.load(f)
                    self._inv_mtime = mtime
                    return self._inv_cache
        except Exception as e:
            print(f"⚠️ [BRAIN] Error leyendo inventario.json: {e}")
        return self._inv_cache

    def _get_pump_info(self, ingredient):
        """Retorna (pump_id, cm, is_available, ml_restantes, display_name) para un ingrediente."""
        inv = self._load_inventory()
        ing_clean = _clean(ingredient)
        if not ing_clean:
            return None, None, False, 0, ingredient

        if inv and "bombas" in inv:
            connected = inv.get("ingredientes_conectados")
            connected_set = {_clean(x) for x in connected if _clean(x)} if isinstance(connected, list) else set()
            for pump_id, info in inv["bombas"].items():
                pump_ing_clean = _clean(info.get("ingrediente"))
                if pump_ing_clean and pump_ing_clean == ing_clean:
                    if connected_set and ing_clean not in connected_set:
                        return pump_id, info.get("cm", 0), False, 0, info.get("ingrediente", ingredient)
                    ml_val = info.get("ml_restantes", 999)
                    is_avail = bool(info.get("disponible", True)) and ml_val > 0
                    return pump_id, info.get("cm", 0), is_avail, ml_val, info.get("ingrediente", ingredient)
            # No caer a BOMBAS_CONFIG si inventario.json existe y controla las bombas
            return None, None, False, 0, ingredient

        for pump_id, info in BOMBAS_CONFIG.items():
            pump_ing_clean = _clean(info.get("ingrediente"))
            if pump_ing_clean and pump_ing_clean == ing_clean:
                is_avail = bool(info.get("disponible", True)) and info.get("ml_restantes", 999) > 0
                return pump_id, info.get("cm", 0), is_avail, info.get("ml_restantes", 999), info.get("ingrediente", ingredient)

        return None, None, False, 0, ingredient

    def get_menu(self, only_available=True):
        """Lista de bebidas: [{"id", "nombre", "ingredientes": [..], "steps": [..], "available": bool, "missing": [..]}].
        Si only_available=True, filtra estrictamente cócteles cuyos ingredientes estén 100% en stock."""
        recipes = []
        inv = self._load_inventory()
        if inv and inv.get("bebidas"):
            for idx, b in enumerate(inv["bebidas"], 1):
                recipes.append((
                    b.get("id", idx),
                    b.get("nombre", ""),
                    [
                        (r.get("ingrediente", ""), r.get("cantidad_ml", 15), r.get("cm"))
                        for r in b.get("ingredientes_necesarios", [])
                    ],
                ))
        else:
            for idx, (name, rec) in enumerate(RECETAS_COCTELES.items(), 1):
                recipes.append((idx, name, [(k, v, None) for k, v in rec.items()]))

        menu = []
        for drink_id, name, items in recipes:
            steps, ok_pump = [], bool(items)
            missing = []
            for ing, ml, explicit_cm in items:
                pump, cm, is_avail, ml_rest, ing_name = self._get_pump_info(ing)
                if explicit_cm is not None:
                    cm = explicit_cm
                if not pump:
                    ok_pump = False
                    missing.append(ing_name)
                elif not is_avail or ml_rest < ml:
                    missing.append(ing_name)
                else:
                    steps.append({"pump": pump, "amount_ml": ml, "cm": cm})

            is_available = ok_pump and len(missing) == 0
            drink_data = {
                "id": drink_id,
                "nombre": name,
                "ingredientes": [i for i, _, _ in items],
                "steps": steps if is_available else [],
                "available": is_available,
                "missing": missing
            }

            if only_available:
                if is_available:
                    menu.append(drink_data)
            else:
                menu.append(drink_data)

        return menu

    def _menu_prompt(self):
        lines = ["MENÚ DISPONIBLE EN BARRA (lo único que puedes preparar ahora mismo):"]
        all_drinks = self.get_menu(only_available=False)
        avail = [d for d in all_drinks if d.get("available")]
        unavailable = [d for d in all_drinks if not d.get("available")]
        if not avail:
            lines.append("- Ningún cóctel disponible actualmente por falta de stock.")
        else:
            for d in avail:
                lines.append(f"- [Cóctel {d['id']}] {d['nombre']}: {', '.join(d['ingredientes'])}")
        if unavailable:
            unavail_str = "; ".join(f"{d['nombre']} (falta {', '.join(d['missing'])})" for d in unavailable)
            lines.append(f"NO DISPONIBLES HOY: {unavail_str}")
        return "\n".join(lines)

    def _menu_sentence(self):
        names = [d["nombre"] for d in self.get_menu(only_available=True)]
        if not names:
            return "Ahora mismo me quedé sin stock de ingredientes para preparar cócteles completos, una disculpa."
        lista = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " y " + names[-1]
        return f"Hoy en la barra te puedo preparar {lista}. ¿Cuál te gustaría probar?"

    def _menu_sentence_en(self):
        names = [d["nombre"] for d in self.get_menu(only_available=True)]
        if not names:
            return "Right now I am out of stock for complete cocktails, sorry about that."
        lista = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        return f"Today at the bar I can make {lista}. Which one would you like?"

    # ------------------------------------------------------------------
    # Detección determinista: Presentaciones, Nombre, Alergias y Consultas
    # ------------------------------------------------------------------

    def _extract_user_introduction(self, user_input):
        """Captura presentaciones de usuario ('me llamo fips', 'my name is John').
        Retorna (name, is_english) o (None, False)."""
        t = user_input.strip()
        t_lower = t.lower()

        # Si el mensaje habla de alergias, salud, menú o cócteles, no es una presentación de nombre
        if any(w in t_lower for w in ["alerg", "allerg", "embaraz", "pregnant", "menu", "drink", "trago", "coctel", "cocktail"]):
            return None, False

        # Español: "recuerda que me llamo X", "me llamo X", "mi nombre es X"
        m_es = re.search(
            r"(?:recuerda\s+(?:que\s+)?)?(?:me\s+llamo|mi\s+nombre\s+es)\s+([A-Za-zÁ-ÿñÑ]+)",
            t, re.IGNORECASE
        )
        if m_es:
            return m_es.group(1).capitalize(), False

        # Español: "hola soy X" o "soy X" (solo al inicio de frase y nunca adjetivos comunes)
        m_soy = re.search(
            r"^(?:hola\s*,?\s*|buenas\s*,?\s*)?\bsoy\s+([A-Za-zÁ-ÿñÑ]+)(?:\s*$|\s*[.!,])",
            t, re.IGNORECASE
        )
        if m_soy:
            candidate = m_soy.group(1).lower()
            stopwords = {
                "un", "una", "el", "la", "de", "del", "nuevo", "nueva", "vegetariano",
                "mayor", "amigo", "yo", "aqui", "mia", "alergico", "alergica", "embarazada",
                "intolerante", "bueno", "malo", "cliente", "feliz", "triste", "enojado",
                "enojada", "cansado", "cansada", "aburrido", "aburrida", "libre", "ocupado",
                "ocupada", "loco", "loca", "fan", "humano", "humana", "bartender",
                "turista", "invitado", "invitada"
            }
            if candidate not in stopwords and len(candidate) >= 2:
                return m_soy.group(1).capitalize(), False

        # Inglés: "my name is X", "call me X"
        m_en_name = re.search(
            r"(?:remember\s+(?:that\s+)?)?(?:my\s+name\s+is|call\s+me)\s+([A-Za-z]+)",
            t, re.IGNORECASE
        )
        if m_en_name:
            return m_en_name.group(1).capitalize(), True

        # Inglés: "I am X" o "I'm X" (solo al inicio de frase)
        m_en_iam = re.search(
            r"^(?:hello\s*,?\s*|hi\s*,?\s*|hey\s*,?\s*)?\b(?:i\s+am|i'm)\s+([A-Za-z]+)(?:\s*$|\s*[.!,])",
            t, re.IGNORECASE
        )
        if m_en_iam:
            candidate = m_en_iam.group(1).lower()
            stopwords_en = {
                "a", "an", "the", "new", "here", "ready", "thirsty", "mia", "fine", "good",
                "allergic", "pregnant", "sorry", "hungry", "happy", "tired", "drunk", "just"
            }
            if candidate not in stopwords_en and len(candidate) >= 2:
                return m_en_iam.group(1).capitalize(), True

        return None, False

    def _is_who_am_i_question(self, user_input):
        """Detecta si el usuario pregunta quién es o cómo se llama."""
        t = _clean(user_input)
        es_queries = [
            "como me llamo", "quien soy", "recuerdas como me llamo",
            "recuerdas mi nombre", "sabes mi nombre", "sabes quien soy",
            "te acuerdas de mi nombre", "te acuerdas como me llamo",
            "cual es mi nombre"
        ]
        if any(q in t for q in es_queries):
            return True, False

        en_queries = [
            "who am i", "what is my name", "whats my name", "what s my name",
            "remember my name", "know my name", "know who i am", "do you remember me"
        ]
        if any(q in t for q in en_queries):
            return True, True

        return False, False

    def _is_mia_name_question(self, user_input):
        """Detecta si el usuario pregunta por el nombre de MIA."""
        t = _clean(user_input)
        if any(q in t for q in MIA_NAME_WORDS_ES):
            return True, False
        if any(q in t for q in MIA_NAME_WORDS_EN):
            return True, True
        return False, False

    def _is_menu_question(self, user_input):
        """Detecta si el usuario consulta el menú disponible (evitando chocar con visión)."""
        t = _clean(user_input)
        if any(w in t for w in EXPLICIT_MENU_WORDS_ES):
            return True, False
        if any(q in t for q in EXPLICIT_MENU_WORDS_EN) or t == "menu":
            return True, True
        has_vision_ctx = any(re.search(rf"\b{re.escape(v)}\b", t) for v in VISION_CONTEXT_WORDS)
        if not has_vision_ctx:
            if any(w in t for w in AMBIGUOUS_MENU_WORDS_ES):
                return True, False
            if any(w in t for w in AMBIGUOUS_MENU_WORDS_EN):
                return True, True
        return False, False

    # ------------------------------------------------------------------
    # Filtro de Alergias e Ingredientes
    # ------------------------------------------------------------------

    def _extract_allergens(self, text_clean):
        """Extrae TODOS los alérgenos/restricciones mencionados en el texto (sin cortocircuitar)."""
        found = []
        for key, trigger_words, _, _, _ in ALLERGEN_DEFINITIONS:
            if any(re.search(rf"\b{re.escape(w)}\b", text_clean) for w in trigger_words):
                if key not in found:
                    found.append(key)
        return found

    def _allergen_substrings(self, keys):
        subs = set()
        for key, _, ing_subs, _, _ in ALLERGEN_DEFINITIONS:
            if key in keys:
                subs.update(ing_subs)
        return subs

    def _allergen_labels(self, keys, is_en=False):
        labels = []
        for key, _, _, name_es, name_en in ALLERGEN_DEFINITIONS:
            if key in keys:
                labels.append(name_en if is_en else name_es)
        if not labels:
            return ""
        if len(labels) == 1:
            return labels[0]
        sep = " and " if is_en else " y "
        return ", ".join(labels[:-1]) + sep + labels[-1]

    def _find_allergen_conflicts(self, drink, allergen_keys):
        """Devuelve la lista de claves de alérgeno que chocan con los ingredientes del trago."""
        if not allergen_keys:
            return []
        ing_text = " ".join(_clean(i) for i in drink.get("ingredientes", []))
        conflicts = []
        for key, _, ing_subs, _, _ in ALLERGEN_DEFINITIONS:
            if key in allergen_keys and any(sub in ing_text for sub in ing_subs):
                conflicts.append(key)
        return conflicts

    def _check_allergy_restriction(self, user_input):
        """Detecta menciones de alergias (incluidas múltiples) o restricciones dietéticas.
        Guarda las restricciones en la sesión activa y responde de forma segura (fail-safe)."""
        t = _clean(user_input)
        allergy_kw = [
            "alergia", "alergico", "alergica", "alergeno", "intolerancia",
            "no puedo tomar", "no puedo consumir", "no puedo comer",
            "no tolero", "intolerante", "embarazo", "embarazada", "sin alcohol",
            "hace dano", "hace mal", "prohibido", "medicamento", "medicina",
            "allergy", "allergic", "intolerant", "cant drink", "cannot drink",
            "no alcohol", "non alcoholic", "pregnant", "pregnancy"
        ]
        has_allergy_kw = any(k in t for k in allergy_kw)
        has_sin_allergen = any(
            re.search(rf"\b(?:sin|without)\s+(?:la\s+|el\s+|los\s+|las\s+|un\s+|una\s+)?{re.escape(w)}\b", t)
            for _, words, _, _, _ in ALLERGEN_DEFINITIONS
            for w in words
        )
        if not has_allergy_kw and not has_sin_allergen:
            return None

        is_en = is_english_text(user_input)
        matched_keys = self._extract_allergens(t)

        # Fail-safe: si reporta alergia a un ingrediente no listado o desconocido (ej. maní), no recomendar a ciegas
        if not matched_keys:
            if is_en:
                return (
                    "I can't verify unlisted ingredients blindly. Our bar uses only tequila, grapefruit soda, "
                    "lemon juice, and orange liqueur; please confirm if any of those affect you."
                )
            return (
                "Por seguridad no recomiendo bebidas si el alérgeno no está en mi catálogo. En esta barra solo "
                "usamos tequila, refresco de toronja, jugo de limón y licor de naranja; dime si alguno te hace daño."
            )

        if self.memory:
            self.memory.add_session_allergens(matched_keys)
            effective_keys = self.memory.get_session_allergens()
        else:
            effective_keys = matched_keys

        restricted_str = self._allergen_labels(effective_keys, is_en=is_en)
        avail_menu = self.get_menu(only_available=True)

        if not avail_menu:
            if is_en:
                return f"I noted your restriction ({restricted_str}), and right now the bar is out of stock for complete cocktails."
            return f"Anoté tu restricción ({restricted_str}), y además ahora mismo no tengo stock disponible en barra."

        safe_drinks = [
            d["nombre"] for d in avail_menu
            if not self._find_allergen_conflicts(d, effective_keys)
        ]

        if safe_drinks:
            sep = " or " if is_en else " o "
            safe_str = ", ".join(safe_drinks[:-1]) + sep + safe_drinks[-1] if len(safe_drinks) > 1 else safe_drinks[0]
            if is_en:
                return (
                    f"If you can't have {restricted_str}, you can safely enjoy {safe_str}, "
                    f"since it contains none of that. Would you like me to prepare it?"
                )
            no_phrase = "no contiene ninguno de esos ingredientes" if len(effective_keys) > 1 else "no lo contiene"
            return (
                f"Si no puedes consumir {restricted_str}, puedes tomar con confianza {safe_str}, "
                f"ya que {no_phrase}. ¿Te gustaría que te lo prepare?"
            )

        if "alcohol" in effective_keys:
            if is_en:
                return (
                    "For your safety (pregnancy or alcohol restriction), all currently available cocktails contain alcohol "
                    "so I cannot recommend any of them."
                )
            return (
                "Por precaución médica y tu seguridad, todas nuestras opciones disponibles contienen alcohol o destilados, "
                "así que no te recomiendo consumir ninguna del menú actual."
            )

        if is_en:
            return (
                f"Checking our recipes, every currently available cocktail contains {restricted_str}. "
                f"For your health, I cannot serve you any of them."
            )
        return (
            f"Revisando las recetas disponibles, todas incluyen {restricted_str}. "
            f"Por tu salud prefiero no servirte ninguna bebida que pueda hacerte daño."
        )

    def _check_ingredient_query(self, user_input):
        """Detecta consultas de qué tragos llevan un ingrediente específico ('¿qué tragos tienen limón?')."""
        t = _clean(user_input)
        query_kw = [
            "que tragos tienen", "que bebidas tienen", "que tiene", "que lleva",
            "cuales tienen", "cuales llevan", "que cocteles tienen", "cuales son con",
            "what drinks have", "which drinks have", "what cocktails have", "what has"
        ]
        if not any(k in t for k in query_kw):
            return None

        is_en = is_english_text(user_input)
        avail_menu = self.get_menu(only_available=True)
        if not avail_menu:
            avail_menu = self.get_menu(only_available=False)

        target_ing = None
        display_name = None
        if any(w in t for w in ["toronja", "grapefruit", "witi"]):
            target_ing = "toronja"
            display_name = "grapefruit" if is_en else "refresco de toronja"
        elif any(w in t for w in ["limon", "lemon", "lime"]):
            target_ing = "limon"
            display_name = "lemon" if is_en else "limón"
        elif any(w in t for w in ["tequila"]):
            target_ing = "tequila"
            display_name = "tequila"
        elif any(w in t for w in ["naranja", "orange"]):
            target_ing = "naranja"
            display_name = "orange liqueur" if is_en else "licor de naranja"

        if not target_ing:
            return None

        matching = []
        for d in avail_menu:
            ing_text = " ".join(_clean(i) for i in d["ingredientes"])
            if target_ing in ing_text:
                matching.append(d["nombre"])

        if matching:
            match_str = ", ".join(matching[:-1]) + (" y " if not is_en else " and ") + matching[-1] if len(matching) > 1 else matching[0]
            if is_en:
                return f"The cocktails containing {display_name} are: {match_str}. Which one would you like?"
            else:
                return f"Las bebidas que llevan {display_name} son: {match_str}. ¿Cuál se te antoja?"
        else:
            if is_en:
                return f"None of our current available cocktails contain {display_name}."
            else:
                return f"Actualmente ninguna de nuestras bebidas preparables lleva {display_name}."

    # ------------------------------------------------------------------
    # Escudo de Seguridad / Consultas de Estado / Estadísticas / Pedidos
    # ------------------------------------------------------------------

    def _check_safety_pivot_shield(self, user_input, robot_busy=False, current_drink=None):
        """Intercepta intentos de jailbreak o temas peligrosos en 0.00s y redirige a la barra."""
        t = _clean(user_input)
        if not any(re.search(pat, t) for pat in SAFETY_PIVOT_PATTERNS):
            return None

        is_en = is_english_text(user_input)
        if robot_busy and current_drink:
            if is_en:
                return f"I don't serve any of that at my bar; my only mission right now is finishing pumping your {current_drink} so you can enjoy it ice cold."
            return f"De esos temas no sirvo ni media gota en mi barra; mi única misión ahora mismo es terminar de bombearte tu {current_drink} para que lo disfrutes bien frío."

        avail = self.get_menu(only_available=True)
        featured = avail[0]["nombre"] if avail else "cóctel de la casa"
        slot = avail[0].get("id", 1) if avail else 1
        if is_en:
            return f"I don't deal with any of that, I'm a professional bartender; the only explosive thing here is the flavor of our {featured} (cocktail {slot}). Shall I make you one?"
        return f"De esos temas no sé nada, yo soy bartender profesional; aquí lo único explosivo es el sabor de nuestro {featured} (el cóctel {slot}). ¿Te lo preparo?"

    def _check_preparation_status_query(self, user_input, robot_busy=False, current_drink=None, current_step_info=""):
        """Responde sobre el estado en vivo del dispensado o de las bombas."""
        t = _clean(user_input)
        status_kw = [
            "como va mi trago", "como va la bebida", "como va el coctel", "como va mi coctel",
            "ya casi esta", "cuanto falta", "que estas haciendo", "que estas preparando",
            "estan funcionando las bombas", "se estan ejecutando las bombas", "esta en espera",
            "how is my drink", "is my drink ready", "what are you doing", "are the pumps running"
        ]
        if not any(k in t for k in status_kw):
            return None

        is_en = is_english_text(user_input)
        if robot_busy:
            drink_label = current_drink or ("cocktail" if is_en else "cóctel")
            step_note = f" ({current_step_info})" if current_step_info else ""
            if is_en:
                return f"Right now my pumps are actively preparing your {drink_label}{step_note}. It will be ready in a few seconds!"
            return f"Ahora mismo mis bombas están en plena acción preparando tu {drink_label}{step_note}. ¡En unos segundos queda listo!"

        if is_en:
            return "Right now the pumps are idle and the bar is free. Which cocktail can I get you: 1, 2, 3, or 4?"
        return "Ahora mismo las bombas están en reposo y la barra está libre. ¿Qué cóctel te sirvo, el 1, 2, 3 o 4?"

    def _check_stats_query(self, user_input):
        """Responde métricas de barra reales desde memory.py (más pedido, total servido)."""
        if not self.memory:
            return None
        t = _clean(user_input)
        stats_kw = [
            "mas pedido", "mas popular", "piden mas", "se pide mas",
            "cuantos tragos has servido", "cuantos cocteles llevas", "cuantas bebidas has hecho",
            "most popular drink", "how many drinks have you made", "best seller"
        ]
        if not any(k in t for k in stats_kw):
            return None

        is_en = is_english_text(user_input)
        total = self.memory.get_total_orders()
        top_drink, top_count = self.memory.get_most_popular_drink()
        if top_drink:
            if is_en:
                return f"Today I have served {total} drinks, and the crowd favorite so far is {top_drink} ({top_count} orders). Would you like one?"
            return f"Hasta ahora llevo {total} tragos servidos, y el cóctel más pedido es {top_drink} con {top_count} órdenes. ¿Te preparo uno?"

        avail = self.get_menu(only_available=True)
        sug = avail[0]["nombre"] if avail else "Paloma"
        if is_en:
            return f"I haven't served any drinks yet in this session, so you can be the first! I recommend trying {sug}."
        return f"Todavía no sirvo tragos en esta jornada, ¡puedes estrenar la barra! Te recomiendo empezar con {sug}."

    def _match_drink_by_number(self, text_clean, all_menu):
        """Resuelve pedidos por índice/slot ('sírveme el cóctel 1', 'opción tres', 'el 2')."""
        if not all_menu:
            return None
        num_tokens = "|".join(sorted(SLOT_NUMBER_MAP.keys(), key=len, reverse=True))
        m = re.search(
            rf"\b(?:coctel|cocktail|trago|bebida|drink|opcion|option|numero|number|combo)\s*(?:numero|number|num)?\s*({num_tokens})\b",
            text_clean,
        )
        if not m:
            m = re.search(
                rf"\b(?:el|la|un|una|the|dame|sirveme|preparame|quiero|ponme|hazme)\s+({num_tokens})\b",
                text_clean,
            )
        if not m:
            return None

        slot_num = SLOT_NUMBER_MAP.get(m.group(1))
        if not slot_num:
            return None
        for d in all_menu:
            if d.get("id") == slot_num:
                return d
        if 1 <= slot_num <= len(all_menu):
            return all_menu[slot_num - 1]
        return None

    def _match_drink(self, text_clean):
        # Buscamos en todo el catálogo configurado (para detectar si piden algo agotado)
        all_menu = self.get_menu(only_available=False)
        if not all_menu:
            return None
        # 0a) Coincidencia por número de cóctel/slot ("cóctel 1", "bebida 2", "opción tres")
        by_num = self._match_drink_by_number(text_clean, all_menu)
        if by_num:
            return by_num
        # 0b) Repetir último pedido de la sesión ("otro igual", "lo mismo")
        if self.memory and any(re.search(rf"\b{re.escape(p)}\b", text_clean) for p in REPEAT_ORDER_PHRASES):
            last_order = self.memory.get_last_order()
            if last_order:
                for d in all_menu:
                    if _clean(d["nombre"]) == _clean(last_order):
                        return d
        # 1) Nombre completo (el más largo primero)
        for d in sorted(all_menu, key=lambda d: len(d["nombre"]), reverse=True):
            if re.search(rf"\b{re.escape(_clean(d['nombre']))}\b", text_clean):
                return d
        # 2) Alias distintivo de un trago
        ing_words = {w for d in all_menu for i in d["ingredientes"] for w in _clean(i).split()}
        name_words = {}
        for d in all_menu:
            for w in set(_clean(d["nombre"]).split()):
                name_words.setdefault(w, []).append(d)
        for w, ds in name_words.items():
            if len(ds) == 1 and len(w) >= 5 and w not in ing_words and re.search(rf"\b{w}\b", text_clean):
                return ds[0]
        return None

    def detect_order(self, user_input):
        """Devuelve la bebida del catálogo SI Y SOLO SI existe una cláusula afirmativa de pedido.
        Ignora cláusulas negadas ('no quiero una paloma', 'I don\\'t want...') o médicas."""
        full_clean = _clean(user_input)
        global_block_kw = [
            "alergia", "alergico", "alergica", "alergeno", "intolerante", "intolerancia",
            "allergy", "allergic", "intolerant", "embarazo", "embarazada", "pregnant", "pregnancy"
        ]
        if any(k in full_clean for k in global_block_kw):
            return None

        # Conservar puntuación y conectores adversativos para aislar cláusulas negadas
        s = unicodedata.normalize("NFD", user_input or "")
        text_norm = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
        clauses = re.split(r"[,.;!?]+|\b(?:pero|sino|but|instead)\b", text_norm)

        allowed_fillers = (
            {"un", "una", "el", "la", "por", "favor", "a", "an", "the", "please",
             "coctel", "cocktail", "trago", "bebida", "drink", "opcion", "option",
             "numero", "number", "num", "combo", "igual", "mismo", "misma",
             "repite", "otra", "otro", "same", "again", "another", "one"}
            | set(SLOT_NUMBER_MAP.keys())
        )
        for clause in clauses:
            c = _clean(clause)
            if not c:
                continue
            if any(w in c for w in INFO_WORDS) or any(k in c for k in EXCLUDE_ORDER_KEYWORDS):
                continue
            if any(re.search(pat, c) for pat in NEGATION_PATTERNS):
                continue

            drink = self._match_drink(c)
            if not drink:
                continue

            drink_clean = re.escape(_clean(drink["nombre"]))
            if re.search(rf"\bsin\s+(?:una?\s+|el\s+|la\s+)?{drink_clean}\b", c) or re.search(
                rf"\bwithout\s+(?:a\s+|an\s+|the\s+)?{drink_clean}\b", c
            ):
                continue

            has_prep_verb_es = any(re.search(rf"\b{re.escape(w)}\b", c) for w in ORDER_WORDS_ES)
            has_prep_verb_en = any(re.search(rf"\b{re.escape(w)}\b", c) for w in ORDER_WORDS_EN)
            if has_prep_verb_es or has_prep_verb_en:
                return drink

            tokens = set(c.split())
            drink_tokens = set(_clean(drink["nombre"]).split())
            extra_tokens = tokens - drink_tokens - allowed_fillers
            if len(extra_tokens) == 0 and len(tokens) <= 5:
                return drink

        return None

    def pop_pending_dispense(self):
        with self._lock:
            d, self._pending_dispense = self._pending_dispense, None
            return d

    # ------------------------------------------------------------------
    # Memoria Ligera Condicional
    # ------------------------------------------------------------------

    def _should_query_memory(self, user_input):
        if not MEMORY_ENABLED or not self.memory:
            return False
        t = _clean(user_input)
        return any(kw in t for kw in MEMORY_KEYWORDS)

    def _get_memory_context(self, user_input):
        """Recupera contexto de la memoria determinista para inyectar en el LLM."""
        if not self._should_query_memory(user_input) or not self.memory:
            return ""
        try:
            summary = self.memory.get_profile_summary()
            knows = self.memory.recall_knowledge(user_input, n_results=MEMORY_RESULTS_LIMIT)
            convs = self.memory.recall_conversations(user_input, n_results=MEMORY_RESULTS_LIMIT)

            lines = []
            if summary:
                lines.append(f"Perfil del usuario: {summary}")
            if knows:
                for k in knows:
                    lines.append(f"Dato recordado: {k['fact']}.")
            if convs:
                for c in convs:
                    lines.append(f"Conversación previa: Usuario dijo '{c['user_message']}' y respondiste '{c['mia_response']}'.")

            if lines:
                print(f"🧬 [MEMORY] Inyectando contexto de memoria ({len(lines)} items).")
                return "\n[DATOS RECORDADOS DE MEMORIA]:\n" + "\n".join(lines)
        except Exception as e:
            print(f"⚠️ [MEMORY] Error consultando memoria: {e}")
        return ""

    def _persist_memory_async(self, user_msg, mia_resp):
        if not MEMORY_ENABLED or not self.memory:
            return

        def _worker():
            try:
                self.memory.store_conversation(user_msg, mia_resp)
                t = _clean(user_msg)
                if any(k in t for k in ["mi favorito es", "mi favorita es", "me gusta", "my favorite is", "i like"]):
                    self.memory.store_knowledge(user_msg, category="preferencia")
            except Exception as e:
                print(f"⚠️ [MEMORY] Error persistiendo memoria async: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Historial
    # ------------------------------------------------------------------

    def _add_to_history(self, role, content):
        self._history.append({"role": role, "content": content})
        if len(self._history) > MAX_HISTORY_TURNS * 2:
            self._history = self._history[-(MAX_HISTORY_TURNS * 2):]

    def clear_history(self):
        self._history.clear()

    def reset_session(self):
        """Reinicia la sesión actual (memoria de cliente + historial reciente)."""
        if self.memory:
            self.memory.reset_session()
        self.clear_history()

    # ------------------------------------------------------------------
    # Motores LLM y System Prompt de Cantina
    # ------------------------------------------------------------------

    def _system_prompt(self):
        # System prompt fijo (sin datos de cliente) para preservar KV-cache y evitar fugas entre clientes
        return f"{MIA_SYSTEM_PROMPT}\n\n{self._menu_prompt()}"

    def _warmup(self):
        try:
            t0 = time.time()
            self.ollama_client.chat(
                model=self.local_model,
                messages=[
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": "hola"}
                ],
                options={"num_ctx": LLM_CONTEXT_SIZE, "num_predict": 1, "num_thread": OLLAMA_NUM_THREADS},
                keep_alive=OLLAMA_KEEP_ALIVE,
            )
            print(f"🔥 [BRAIN] Modelo precargado en {time.time() - t0:.1f}s.")
        except Exception as e:
            print(f"⚠️ [BRAIN] No se pudo precargar Ollama ({e}). ¿Está corriendo 'ollama serve'?")

    def _should_use_cloud(self, user_input):
        if not CLOUD_ENABLED or not CLOUD_API_KEY:
            return False
        if len(user_input.split()) > 15:
            return True
        t = _clean(user_input)
        return any(g in t for g in ["por que", "como funciona", "cuentame", "opinas", "sabes de", "explicame", "why", "explain"])

    def _cloud_stream(self, messages):
        res = requests.post(
            CLOUD_API_URL,
            headers={"Authorization": f"Bearer {CLOUD_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": CLOUD_MODEL, "messages": messages, "temperature": LLM_TEMPERATURE,
                "max_tokens": LLM_MAX_TOKENS, "stream": True
            },
            stream=True, timeout=CLOUD_TIMEOUT,
        )
        if res.status_code != 200:
            raise RuntimeError(f"Cloud API {res.status_code}: {res.text[:200]}")
        for line in res.iter_lines():
            if not line:
                continue
            s = line.decode("utf-8").strip()
            if not s.startswith("data: "):
                continue
            data = s[6:].strip()
            if data == "[DONE]":
                break
            try:
                content = json.loads(data)["choices"][0]["delta"].get("content", "")
                if content:
                    yield content
            except Exception:
                continue

    def _local_stream(self, messages):
        for chunk in self.ollama_client.chat(
            model=self.local_model,
            messages=messages,
            stream=True,
            options={
                "num_ctx": LLM_CONTEXT_SIZE,
                "temperature": LLM_TEMPERATURE,
                "num_predict": LLM_MAX_TOKENS,
                "num_thread": OLLAMA_NUM_THREADS,
            },
            keep_alive=OLLAMA_KEEP_ALIVE,
        ):
            text = chunk.get("message", {}).get("content", "")
            if text:
                yield text

    # ------------------------------------------------------------------
    # Troceado para TTS en vivo
    # ------------------------------------------------------------------

    @staticmethod
    def _speakable(token_stream):
        """Quita [etiquetas], <think>, '*' y emojis; corta en frases/cláusulas en cuanto aparecen."""
        buf, depth, first = "", 0, True
        for tok in token_stream:
            for ch in tok:
                if ch in "[<":
                    depth += 1
                    continue
                if ch in "]>":
                    depth = max(0, depth - 1)
                    continue
                if depth or ch in "*#_`" or ord(ch) > 0x2600:
                    continue
                buf += ch
            while True:
                m = SENTENCE_END.search(buf)
                cut = m.end() if m and m.start() > 0 else -1
                if cut == -1:
                    limit = 12 if first else 25
                    ci = buf.find(",", limit)
                    if ci != -1:
                        cut = ci + 1
                if cut == -1:
                    break
                piece, buf = buf[:cut].strip(), buf[cut:]
                piece = re.sub(r"^(MIA|Mia)\s*:\s*", "", piece)
                if len(re.sub(r"[\W_]+", "", piece)) >= 2:
                    first = False
                    yield piece
        tail = re.sub(r"^(MIA|Mia)\s*:\s*", "", buf.strip())
        if len(re.sub(r"[\W_]+", "", tail)) >= 2:
            yield tail

    @staticmethod
    def _guard_false_dispense_claim(piece, is_en=False):
        """Evita que el LLM prometa en conversación libre que ya está preparando algo sin [DISPENSE]."""
        p_clean = _clean(piece)
        false_claims_es = [
            "ya estoy preparando", "estoy preparando tu", "te preparo ahora mismo tu",
            "estoy sirviendo tu", "ya te sirvo tu"
        ]
        false_claims_en = [
            "i am preparing your", "i m preparing your", "preparing your", "making your drink now"
        ]
        if any(fc in p_clean for fc in (false_claims_en if is_en else false_claims_es)):
            if is_en:
                return "If you'd like one, just say 'make me cocktail 1' and I'll start the pumps!"
            return "Si te animas a pedirlo, dime 'sírveme el cóctel 1' y pongo las bombas en marcha."
        return piece

    # ------------------------------------------------------------------
    # Métodos principales: think y respond_to_user
    # ------------------------------------------------------------------

    def think(self, user_input, visual_context="", robot_busy=False, current_drink=None, current_step_info=""):
        """Método think() requerido por el asistente: retorna respuesta completa."""
        return " ".join(self.respond_to_user(
            user_input,
            visual_context=visual_context,
            robot_busy=robot_busy,
            current_drink=current_drink,
            current_step_info=current_step_info,
        ))

    def respond_to_user(self, user_input, visual_context="", robot_busy=False, current_drink=None, current_step_info=""):
        """Generador de frases listas para hablar (streaming)."""
        print(f"🧠 [BRAIN] Procesando: '{user_input}'")

        if self.memory and self.memory.touch_session():
            self.clear_history()

        # 1) Presentación de nombre del usuario ("recuerda que me llamo fips", "my name is John")
        intro_name, is_en_intro = self._extract_user_introduction(user_input)
        if intro_name and not visual_context:
            if self.memory:
                self.memory.set_user_name(intro_name)
            if is_en_intro:
                reply = f"Nice to meet you, {intro_name}! I will remember your name."
            else:
                reply = f"¡Mucho gusto, {intro_name}! Ya lo tengo guardado en mi memoria."
            self._add_to_history("user", user_input)
            self._add_to_history("assistant", reply)
            self._persist_memory_async(user_input, reply)
            yield reply
            return

        # 2) Pregunta de identidad del usuario ("¿quién soy?", "¿recuerdas cómo me llamo?", "who am i")
        is_who_am_i, is_en_who = self._is_who_am_i_question(user_input)
        if is_who_am_i and not visual_context:
            user_name = self.memory.get_user_name() if self.memory else None
            if is_en_who:
                reply = f"Your name is {user_name}." if user_name else "You haven't told me your name yet. What's your name?"
            else:
                reply = f"Te llamas {user_name}." if user_name else "Aún no me has dicho tu nombre. ¿Cómo te llamas?"
            self._add_to_history("user", user_input)
            self._add_to_history("assistant", reply)
            self._persist_memory_async(user_input, reply)
            yield reply
            return

        # 3) Pregunta de nombre de MIA ("¿cómo te llamas?", "what is your name")
        is_mia_name, is_en_mia = self._is_mia_name_question(user_input)
        if is_mia_name and not visual_context:
            reply = "My name is MIA." if is_en_mia else "Me llamo MIA."
            self._add_to_history("user", user_input)
            self._add_to_history("assistant", reply)
            self._persist_memory_async(user_input, reply)
            yield reply
            return

        # 3b) Escudo determinista anti-jailbreak / temas peligrosos (pivote forzado al cóctel)
        safety_reply = None if visual_context else self._check_safety_pivot_shield(
            user_input, robot_busy=robot_busy, current_drink=current_drink
        )
        if safety_reply:
            self._add_to_history("user", user_input)
            self._add_to_history("assistant", safety_reply)
            self._persist_memory_async(user_input, safety_reply)
            yield safety_reply
            return

        # 4) Filtro de Alergias y Restricciones Dietéticas / Médicas
        allergy_reply = None if visual_context else self._check_allergy_restriction(user_input)
        if allergy_reply:
            self._add_to_history("user", user_input)
            self._add_to_history("assistant", allergy_reply)
            self._persist_memory_async(user_input, allergy_reply)
            yield allergy_reply
            return

        # 4b) Consulta de estado de bombas y preparación en curso ("¿cómo va mi trago?")
        status_reply = None if visual_context else self._check_preparation_status_query(
            user_input, robot_busy=robot_busy, current_drink=current_drink, current_step_info=current_step_info
        )
        if status_reply:
            self._add_to_history("user", user_input)
            self._add_to_history("assistant", status_reply)
            self._persist_memory_async(user_input, status_reply)
            yield status_reply
            return

        # 4c) Estadísticas reales de barra ("¿cuál es el cóctel más pedido?")
        stats_reply = None if visual_context else self._check_stats_query(user_input)
        if stats_reply:
            self._add_to_history("user", user_input)
            self._add_to_history("assistant", stats_reply)
            self._persist_memory_async(user_input, stats_reply)
            yield stats_reply
            return

        # 5) Consultas sobre Ingredientes Específicos ("¿qué tragos tienen limón?")
        ing_query_reply = None if visual_context else self._check_ingredient_query(user_input)
        if ing_query_reply:
            self._add_to_history("user", user_input)
            self._add_to_history("assistant", ing_query_reply)
            self._persist_memory_async(user_input, ing_query_reply)
            yield ing_query_reply
            return

        # 6) Pregunta de menú ("¿qué tienes?", "what can you make")
        is_menu, is_en_menu = self._is_menu_question(user_input)
        if is_menu and not visual_context:
            reply = self._menu_sentence_en() if is_en_menu else self._menu_sentence()
            self._add_to_history("user", user_input)
            self._add_to_history("assistant", reply)
            self._persist_memory_async(user_input, reply)
            yield reply
            return

        # 7) Pedido de bebida con Validación de Alergias de Sesión, Stock y Estado del Robot
        drink = None if visual_context else self.detect_order(user_input)
        if drink:
            is_en = is_english_text(user_input)

            # 7a) Validar contra alergias declaradas previamente en la sesión actual
            session_allergens = self.memory.get_session_allergens() if self.memory else []
            conflicts = self._find_allergen_conflicts(drink, session_allergens)
            if conflicts:
                conflict_str = self._allergen_labels(conflicts, is_en=is_en)
                avail_safe = [
                    d["nombre"] for d in self.get_menu(only_available=True)
                    if not self._find_allergen_conflicts(d, session_allergens)
                ]
                if is_en:
                    reply = f"I can't prepare {drink['nombre']} because it contains {conflict_str}, which conflicts with your declared allergy in this session."
                    if avail_safe:
                        reply += f" I can safely make you {', '.join(avail_safe)} instead."
                else:
                    reply = f"No puedo prepararte {drink['nombre']} porque contiene {conflict_str} y me indicaste restricción en esta sesión."
                    if avail_safe:
                        alt_safe = ", ".join(avail_safe[:-1]) + " o " + avail_safe[-1] if len(avail_safe) > 1 else avail_safe[0]
                        reply += f" Si quieres, puedo prepararte {alt_safe} sin riesgo."
                self._add_to_history("user", user_input)
                self._add_to_history("assistant", reply)
                self._persist_memory_async(user_input, reply)
                yield reply
                return

            # 7b) Validar si el cóctel NO está disponible por falta de ingredientes
            if not drink.get("available", False):
                missing_list = drink.get("missing", ["ingredientes"])
                missing_str = ", ".join(missing_list)
                avail_drinks = [d["nombre"] for d in self.get_menu(only_available=True)]
                remaining_ingredients = [i for i in drink["ingredientes"] if i not in missing_list]
                rem_str = ", ".join(remaining_ingredients)

                if is_en:
                    if avail_drinks:
                        alt_str = ", ".join(avail_drinks)
                        reply = f"I ran out of {missing_str} for the {drink['nombre']}, but I can make you {alt_str} without problem."
                    elif rem_str:
                        reply = f"I ran out of {missing_str} for the {drink['nombre']}, but I can serve an option with {rem_str}."
                    else:
                        reply = f"I am out of {missing_str} for the {drink['nombre']} and have no available alternatives right now, sorry."
                else:
                    if avail_drinks:
                        alt_str = ", ".join(avail_drinks[:-1]) + " o " + avail_drinks[-1] if len(avail_drinks) > 1 else avail_drinks[0]
                        reply = f"Me quedé sin {missing_str} para la {drink['nombre']}, pero puedo prepararte {alt_str} sin problema."
                    elif rem_str:
                        reply = f"Me quedé sin {missing_str} para la {drink['nombre']}, pero puedo prepararte una opción con {rem_str} sin problema."
                    else:
                        reply = f"Me quedé sin {missing_str} para la {drink['nombre']} y no me quedan opciones disponibles por ahora, disculpa."

                self._add_to_history("user", user_input)
                self._add_to_history("assistant", reply)
                self._persist_memory_async(user_input, reply)
                yield reply
                return

            # 7c) Validar que el robot no esté ocupado antes de confirmar o encolar orden
            if robot_busy:
                active_label = current_drink or ("another drink" if is_en else "otra bebida")
                reply = (
                    f"Give me just a moment, my pumps are still finishing your {active_label}. Ask me again as soon as it's done and I'll make your {drink['nombre']}."
                    if is_en else
                    f"Dame un momento, mis bombas están terminando de servir tu {active_label}. En cuanto quede listo, pídemelo y te preparo tu {drink['nombre']}."
                )
                self._add_to_history("user", user_input)
                self._add_to_history("assistant", reply)
                self._persist_memory_async(user_input, reply)
                yield reply
                return

            # Si pasa alergias, stock y disponibilidad física -> Generar orden al robot
            payload = {"trago": drink["nombre"], "steps": drink["steps"]}
            with self._lock:
                self._pending_dispense = payload
            if self.memory:
                self.memory.record_order(drink["nombre"])
            print(f"🍹 [DISPENSE: {json.dumps(payload, ensure_ascii=False)}]")
            reply = f"Sure! I'm preparing your {drink['nombre']} right now." if is_en else f"¡Con gusto! Ya estoy preparando tu {drink['nombre']}."
            self._add_to_history("user", user_input)
            self._add_to_history("assistant", reply)
            self._persist_memory_async(user_input, reply)
            yield reply
            return

        # 8) Consulta condicional a memoria de sesión
        memory_context = ""
        if not visual_context and self._should_query_memory(user_input):
            memory_context = self._get_memory_context(user_input)

        # 9) Conversación libre / visión -> LLM con pivote coctelero constante
        # Mantener system prompt estable y pasar datos de sesión en el turno actual (KV-cache)
        messages = [{"role": "system", "content": self._system_prompt()}]
        messages += self._history

        session_notes = []
        session_name = self.memory.get_user_name() if self.memory else None
        if session_name:
            session_notes.append(
                f"El cliente actual de la sesión se llama {session_name} "
                f"(si mencionas su nombre en español di 'Te llamas {session_name}')."
            )
        if robot_busy:
            session_notes.append(
                f"ESTADO DE HARDWARE EN VIVO: Tus bombas están sirviendo en este momento un "
                f"{current_drink or 'cóctel'} ({current_step_info or 'en progreso'}). "
                f"Responde en máximo 2 oraciones y enlaza SIEMPRE tu respuesta con ese "
                f"{current_drink or 'cóctel'} que el cliente está esperando."
            )
        else:
            avail_names = ", ".join(d["nombre"] for d in self.get_menu(only_available=True)) or "ninguno disponible"
            session_notes.append(
                f"REGLA DE BARRA: Enlaza siempre tu respuesta breve (máximo 2 oraciones) con alguno de los cócteles "
                f"disponibles hoy ({avail_names}). NUNCA digas que ya estás preparando una bebida si el cliente solo hizo una pregunta."
            )
        if memory_context:
            session_notes.append(memory_context)
        context_block = ("\n[" + " ".join(session_notes) + "]\n") if session_notes else ""

        if visual_context:
            messages.append({"role": "user", "content": (
                f"{context_block}El cliente dijo: \"{user_input}\". Tu cámara detectó (descripción en inglés): "
                f"\"{visual_context}\". Explícale brevemente lo que ves, en máximo 2 oraciones, con tu estilo de bartender carismática."
            )})
        else:
            messages.append({"role": "user", "content": f"{context_block}{user_input}" if context_block else user_input})

        raw = None
        if not visual_context and self._should_use_cloud(user_input):
            try:
                t0 = time.time()
                gen = self._cloud_stream(messages)
                first = next(gen)
                print(f"☁️ [BRAIN] Cloud respondió en {time.time() - t0:.2f}s")

                def _chain(f=first, g=gen):
                    yield f
                    yield from g
                raw = _chain()
            except Exception as e:
                print(f"⚠️ [BRAIN] Cloud falló/timeout ({e}). Fallback a local.")
                raw = None

        if raw is None:
            raw = self._local_stream(messages)

        full = []
        t0 = time.time()
        is_en_turn = is_english_text(user_input)
        try:
            for i, piece in enumerate(self._speakable(raw)):
                if not robot_busy and not visual_context:
                    piece = self._guard_false_dispense_claim(piece, is_en=is_en_turn)
                if i == 0:
                    print(f"⚡ [BRAIN] Primera frase en {time.time() - t0:.2f}s")
                full.append(piece)
                yield piece
        except Exception as e:
            print(f"❌ [BRAIN] Error generando respuesta: {e}")
            if not full:
                is_en = is_english_text(user_input)
                yield "Sorry, I had a technical glitch. Could you repeat that?" if is_en else "Disculpa, tuve un problema técnico. ¿Me lo repites?"
            return

        final_response = " ".join(full)
        self._add_to_history("user", user_input)
        self._add_to_history("assistant", final_response)
        self._persist_memory_async(user_input, final_response)

    # ------------------------------------------------------------------
    # Compatibilidad con assistant.py (modo interactivo / visión proactiva)
    # ------------------------------------------------------------------

    def think_about_vision(self, visual_desc):
        return self.think("¿Qué ves?", visual_context=visual_desc)

    def is_connected(self):
        try:
            self.ollama_client.list()
            return True
        except Exception:
            return False

    def get_memory_stats(self):
        if not MEMORY_ENABLED or not self.memory:
            return {"enabled": False, "conversations": 0, "knowledge": 0}
        stats = self.memory.get_stats()
        return {"enabled": True, **stats}

    def learn_fact(self, fact, category="general"):
        if not self.memory:
            return False
        self.memory.store_knowledge(fact, category)
        return True
