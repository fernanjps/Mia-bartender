from config import (
    S25_IPS, S25_PORT, BRAIN_MODEL, BRAIN_TIMEOUT,
    LLM_CONTEXT_SIZE, LLM_TEMPERATURE,
    MAX_HISTORY_TURNS, MIA_SYSTEM_PROMPT,
    MEMORY_ENABLED
)
from memory import Memory
import os
import re
import requests
import time
import unicodedata


class Brain:
    """Conecta a S25 Ultra (Ktor Server) a través de ADB Forwarding Local 
    y genera respuestas con Gemma-3n.
    """

    def __init__(self):
        self.model = BRAIN_MODEL

        # Usar la URL que viene desde config.py (permite usar IP de WiFi si se desconecta el USB)
        from config import BRAIN_URL
        self.active_url = f"{BRAIN_URL}/chat"

        # Historial de sesión actual (corto plazo)
        self._history = []
        self._inventory_cache = None

        # Memoria a largo plazo (ChromaDB)
        self.memory = None
        if MEMORY_ENABLED:
            try:
                self.memory = Memory()
            except Exception as e:
                print(f"⚠️ Error inicializando memoria ChromaDB: {e}")
                print("   MIA funcionará sin memoria a largo plazo")

        # Eliminamos _test_connection() para evitar el escaneo pesado que congelaba el sistema.
        print(f"✅ S25 Ultra amarrado localmente por USB en: {self.active_url}")

    # ------------------------------------------------------------------
    # Conexión
    # ------------------------------------------------------------------

    def is_connected(self):
        """Verifica si el S25 Ultra está disponible"""
        try:
            response = requests.post(
                self.active_url,
                data=b"status",
                headers={"Content-Type": "text/plain; charset=utf-8"},
                timeout=1.0
            )
            return response.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Historial de sesión (corto plazo)
    # ------------------------------------------------------------------

    def _add_to_history(self, role, content):
        """Agrega un mensaje al historial de sesión"""
        self._history.append({"role": role, "content": content})
        if len(self._history) > MAX_HISTORY_TURNS * 2:
            self._history = self._history[-(MAX_HISTORY_TURNS * 2):]

    def _format_history(self):
        """Formatea el historial como texto para incluir en el prompt (limitado defensivamente)"""
        if not self._history: return ""
        lines = []
        for msg in self._history:
            role_label = "Cliente" if msg['role'] == 'user' else "MIA"
            # Truncar a 150 caracteres para no agotar los tokens en conversaciones largas
            content = msg['content'][:150]
            lines.append(f"{role_label}: {content}")
        return "\n".join(lines)

    def clear_history(self):
        self._history.clear()

    # ------------------------------------------------------------------
    # Generación de respuestas
    # ------------------------------------------------------------------

    def _generate(self, prompt, max_retries=1):
        """MÉTODO HÍBRIDO: Intenta S25 Ultra (Ollama local). Si no está disponible, cae a Groq Cloud API."""
        import requests
        import time
<<<<<<< HEAD
        from config import GROQ_API_KEY, GROQ_MODEL, GROQ_ENABLED

        # 1. INTENTO LOCAL: Probar S25 Ultra (Ktor / Ollama local en celular)
        try:
            body_data = prompt.encode('utf-8')
            headers = {
                "Content-Type": "text/plain; charset=utf-8",
                "Content-Length": str(len(body_data))
            }
            response = requests.post(
                self.active_url,
                data=body_data,
                headers=headers,
                timeout=3.0  # Timeout corto para no hacer esperar si el celular no está conectado
            )
            if response.status_code == 200 and response.text.strip():
                print("🧠 [CEREBRO LOCAL] Respuesta recibida desde S25 Ultra")
=======
        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    print(f"🔄 Reintentando Texto ({attempt}/{max_retries})...")
                    time.sleep(1.5)
                
                body_data = prompt.encode('utf-8')
                headers = {
                    "Content-Type": "text/plain; charset=utf-8",
                    "Content-Length": str(len(body_data))
                }
                
                response = requests.post(
                    self.active_url,
                    data=body_data,     # Texto puro en el body, perfecto para los cócteles
                    headers=headers,
                    timeout=30.0        # Restaurado a 30s para evitar fallas de red
                )
                response.raise_for_status() # Lanza error para 4xx/5xx
                
>>>>>>> 177b6e5c8a87771b162a202845ee5d0a516403c6
                def chunk_generator():
                    yield response.text
                return chunk_generator()
        except Exception as e:
            print(f"⚠️ S25 Ultra no responde ({e}). Activando Respaldo Híbrido Groq Cloud API...")

        # 2. INTENTO CLOUD: Respaldo Híbrido con Groq API (Ultra-rápido)
        if GROQ_ENABLED and GROQ_API_KEY:
            try:
                print(f"⚡ [GROQ CLOUD] Generando respuesta con {GROQ_MODEL} via Groq API...")
                groq_url = "https://api.groq.com/openai/v1/chat/completions"
                groq_headers = {
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json"
                }
                groq_payload = {
                    "model": GROQ_MODEL,
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 512
                }
                groq_response = requests.post(
                    groq_url,
                    json=groq_payload,
                    headers=groq_headers,
                    timeout=10.0
                )
                if groq_response.status_code == 200:
                    data = groq_response.json()
                    ans = data['choices'][0]['message']['content']
                    print("⚡ [GROQ CLOUD] ✅ Respuesta recibida con éxito desde Groq")
                    def chunk_generator():
                        yield ans
                    return chunk_generator()
                else:
                    print(f"❌ Error en Groq API ({groq_response.status_code}): {groq_response.text}")
            except Exception as e:
                print(f"❌ Error conectando a Groq API Cloud: {e}")

        def error_generator(): yield "Lo siento, no pude conectarme con mi celular ni con el servidor de respaldo."
        return error_generator()

    def _generate_vision(self, prompt, max_retries=2):
        """Compatibilidad: el contexto visual ya viene como texto, asi que va por /chat."""
        return self._generate(prompt, max_retries=max_retries)
    def think_about_vision(self, visual_description):
        """MIA observa algo y hace un comentario proactivo"""
        print("🧠 MIA observa y piensa...")
        prompt = f"""{MIA_SYSTEM_PROMPT}

Tu sistema de visión ha detectado la siguiente escena: '{visual_description}'

Describe al usuario lo que estás viendo de forma literal, precisa y amigable.
Ve directo al grano (ej: "Veo que tienes un..."). Máximo 2 oraciones."""

        response_stream = self._generate(prompt)
        response = "".join(list(response_stream)) if response_stream else "Hmm, estoy teniendo problemas para pensar..."
        print(f"🧠 MIA piensa: {response}")
        return response

    def _load_inventory(self):
<<<<<<< HEAD
        """Devuelve las bebidas leyendo el inventario.json (con caché en memoria para máxima velocidad)."""
=======
        """Devuelve las bebidas leyendo el inventario.json."""
>>>>>>> 177b6e5c8a87771b162a202845ee5d0a516403c6
        import json
        import os
        from config import ROBOT_ENABLED
        
<<<<<<< HEAD
        if hasattr(self, "_inventory_cache") and self._inventory_cache is not None:
            return self._inventory_cache
            
        if not ROBOT_ENABLED:
            self._inventory_cache = {
=======
        if not ROBOT_ENABLED:
            return {
>>>>>>> 177b6e5c8a87771b162a202845ee5d0a516403c6
                "ingredientes": [],
                "disponibles": [{"nombre": "Agua (Simulado)"}, {"nombre": "Jugo (Simulado)"}],
                "no_disponibles": [],
                "todas": []
            }
<<<<<<< HEAD
            return self._inventory_cache
=======
>>>>>>> 177b6e5c8a87771b162a202845ee5d0a516403c6
            
        try:
            inv_path = os.path.join(os.path.dirname(__file__), "inventario.json")
            with open(inv_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            recetario = data.get("bebidas", [])
            disponibles = []
            for item in recetario:
                ingredientes_list = item.get("ingredientes_necesarios", [])
                ing_str = ", ".join([ing.get("ingrediente", "") for ing in ingredientes_list])
                disponibles.append({
                    "nombre": item.get("nombre", ""),
                    "descripcion": f"Lleva {ing_str}"
                })
                
<<<<<<< HEAD
            self._inventory_cache = {
=======
            return {
>>>>>>> 177b6e5c8a87771b162a202845ee5d0a516403c6
                "ingredientes": data.get("ingredientes_conectados", []),
                "disponibles": disponibles,
                "no_disponibles": [],
                "todas": disponibles
            }
            return self._inventory_cache
        except Exception as e:
            print(f"⚠️ Error cargando inventario.json: {e}")
            return {
                "ingredientes": [],
                "disponibles": [],
                "no_disponibles": [],
                "todas": []
            }

    def _normalize(self, text):
        text = unicodedata.normalize("NFD", text or "")
        text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
        return text.lower().strip()

    def _direct_response(self, user_input, response, visual_context=""):
        yield response
        self._add_to_history("user", user_input)
        self._add_to_history("assistant", response)
        if self.memory:
            self.memory.store_conversation(user_input, response, visual_context or "")

    def _drinks(self):
        inv_data = self._load_inventory() or {}
        return inv_data.get("disponibles", [])

    def _format_drink_list(self, drinks):
        if not drinks:
            return "Todavia no tengo una lista de cocteles cargada."
        names = [drink.get("nombre", "Sin nombre") for drink in drinks]
        return "Ahora mismo puedo ofrecer: " + ", ".join(names) + "."

    def _drink_details(self, drink):
        name = drink.get("nombre", "este coctel")
        desc = drink.get("descripcion", "").strip()
        ingredients = drink.get("ingredientes", [])
        ingredient_text = ", ".join(ingredients)
        detail = name
        if desc:
            detail += f": {desc}"
        if ingredient_text:
            detail += f" Lleva {ingredient_text}."
        return detail

    def _is_drink_list_request(self, normalized_text):
        list_words = [
            "lista", "carta", "menu", "catalogo", "opciones",
            "que cocteles", "que cocktails", "que bebidas", "bebidas tienes",
            "cocteles tienes", "tragos tienes"
        ]
        return any(word in normalized_text for word in list_words)

    def _is_recommend_request(self, normalized_text):
        recommend_words = ["recomienda", "recomiendame", "sugiere", "sugerirme", "que me aconsejas"]
        return any(word in normalized_text for word in recommend_words)

    def _is_prepare_request(self, normalized_text):
        prepare_words = [
            "prepara", "preparame", "preparar", "sirve", "sirveme",
            "servime", "hazme", "hacerme", "quiero tomar", "quiero beber"
        ]
        return any(word in normalized_text for word in prepare_words)

    def _choose_drink(self, user_input, drinks):
        if not drinks:
            return None
        normalized_text = self._normalize(user_input)
        for drink in drinks:
            name = drink.get("nombre", "")
            if name and self._normalize(name) in normalized_text:
                return drink
        return drinks[0]

    def _extract_memory_fact(self, user_input):
        patterns = [
            r"\brecuerda\s+que\s+(.+)",
            r"\brecuérdame\s+que\s+(.+)",
            r"\bacuerdate\s+de\s+que\s+(.+)",
            r"\bacuérdate\s+de\s+que\s+(.+)",
            r"\bmemoriza\s+que\s+(.+)",
            r"\bguarda\s+que\s+(.+)",
            r"\baprende\s+que\s+(.+)"
        ]
        for pattern in patterns:
            match = re.search(pattern, user_input, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip(" .,:;")
        return ""

    def _is_recall_request(self, normalized_text):
        recall_words = [
            "recuerdas", "que recuerdas", "te acuerdas",
            "que te dije", "que sabes de mi", "mis recuerdos",
            "mi memoria", "busca en tu memoria"
        ]
        return any(word in normalized_text for word in recall_words)

    def _recall_summary(self, query):
        if not self.memory:
            return "La memoria a largo plazo no esta disponible ahora mismo."

        facts = self.memory.recall_knowledge(query, n_results=3)
        conversations = self.memory.recall_conversations(query, n_results=2)

        parts = []
        for fact in facts:
            parts.append(fact.get("fact", ""))
        for memory in conversations:
            user_message = memory.get("user_message", "")
            mia_response = memory.get("mia_response", "")
            if user_message:
                parts.append(f"Me hablaste de: {user_message}")
            elif mia_response:
                parts.append(f"Yo respondi: {mia_response}")

        parts = [part for part in parts if part]
        if not parts:
            return "No encontre un recuerdo claro sobre eso todavia. Puedes decirme: recuerda que me gusta el mojito."

        return "Esto es lo que recuerdo: " + " ".join(parts[:3])

    def _format_inventory_context(self, inv_data):
        if not inv_data:
            return ""
        
        ingredientes_str = ", ".join(inv_data.get("ingredientes", []))
        
        disponibles = []
        for drink in inv_data.get("disponibles", []):
            desc = drink.get("descripcion", "")
            disponibles.append(f"{drink['nombre']} ({desc})")
            
        disponibles_str = ", ".join(disponibles) if disponibles else "Ninguno"
        
        return f"Ingredientes en barra: {ingredientes_str}\nCócteles disponibles para preparar: {disponibles_str}"

    def respond_to_user(self, user_input, visual_context=""):
        """
        Responde a un comando/pregunta del usuario, orquestando memoria y contexto.
        """
        print("🧠 MIA procesa tu pregunta...")

        # 1. FLUJO DE VISIÓN DIRECTO: Si hay contexto visual, respondemos directamente con lo que Moondream vio
        # Esto evita que el LLM de texto alucine cosas sobre la imagen.
        if visual_context:
            print("👁️ Traduciendo descripción visual de Moondream con el LLM de texto...")
            prompt = f"Instrucciones: Eres MIA, asistente virtual. Traduce y describe al cliente lo que ves de forma amigable basándote únicamente en esta descripción: '{visual_context}'. Responde en español, máximo 2 oraciones."
            
            # Generar respuesta traducida sincrónicamente
            response_generator = self._generate(prompt)
            translated_response = ""
            for chunk in response_generator:
                translated_response += chunk
            
            # Limpiar y retornar
            translated_response = translated_response.strip()
            if not translated_response:
                translated_response = visual_context
            
            yield translated_response
            self._add_to_history("user", user_input)
            self._add_to_history("assistant", translated_response)
            if self.memory:
                self.memory.store_conversation(user_input, translated_response, visual_context)
            return

        normalized_input = self._normalize(user_input)

        # 2. Guardar un hecho en la memoria a largo plazo (si el usuario dice "recuerda que...")
        # Guardamos el hecho pero NO retornamos temprano; dejamos que el LLM responda amigablemente.
        memory_fact = self._extract_memory_fact(user_input)
        if memory_fact:
            self.learn_fact(memory_fact, category="usuario")

        # 3. Cargar inventario para inyectar y para control de comandos
        inv_data = self._load_inventory()
        drinks_disponibles = inv_data.get("disponibles", []) if inv_data else []

        # 4. Detectar si el usuario pide preparar un trago específico (de los disponibles)
        target_prepare_drink = None
        if self._is_prepare_request(normalized_input):
            for d in drinks_disponibles:
                name = d.get("nombre", "")
                if name and self._normalize(name) in normalized_input:
                    target_prepare_drink = name
                    break
            
            if not target_prepare_drink:
                generic_words = ["sorprendeme", "sorprende", "random", "aleatorio", "recomienda", "cual sea", "algo", "un trago", "un coctel", "una bebida"]
                if any(w in normalized_input for w in generic_words):
                    import random
                    if drinks_disponibles:
                        target_prepare_drink = random.choice(drinks_disponibles).get("nombre")

        # 5. Buscar recuerdos en la base de datos vectorial (ChromaDB)
        # Solo buscamos recuerdos si la consulta es explícitamente sobre el pasado/memoria,
        # para evitar inyecciones ruidosas e impedir que el LLM alucine sobre temas anteriores.
        memory_context = ""
        is_recall = self._is_recall_request(normalized_input) or "recuerda que" in normalized_input
        if self.memory and is_recall:
            memory_context = self.memory.format_memories_for_prompt(user_input)
            if memory_context:
                print("🧬 Recuerdos relevantes encontrados y cargados en contexto")

        # 6. Formatear el inventario de bebidas SIEMPRE, para que MIA sepa exactamente qué tiene
        # y no alucine cuando le piden tragos fuera del menú.
        menu_context = ""
        if inv_data:
            menu_context = self._format_inventory_context(inv_data)
            print("🍹 Inyectando inventario y bebidas disponibles en el prompt.")

        history_text = self._format_history()

        # 7. Construir el prompt de sistema y contexto en formato de diálogo/transcripción claro
        prompt = f"Instrucciones: {MIA_SYSTEM_PROMPT}\n\n"

        if memory_context:
            prompt += f"Recuerdos de conversaciones pasadas:\n{memory_context}\n\n"

        if menu_context:
            prompt += f"Menú de bebidas disponibles:\n{menu_context}\n\n"

        if history_text:
            prompt += f"Historial de conversación:\n{history_text}\n\n"
        
        # Inyectar notas del sistema para guiar al modelo
        if memory_fact:
            prompt += f"Nota: Acabas de guardar el hecho '{memory_fact}' en tu memoria. Confírmalo amigablemente sin repetir la frase exacta de forma idéntica.\n"
        if target_prepare_drink:
            prompt += f"Nota: Confirma de forma amable que vas a preparar la bebida '{target_prepare_drink}' de inmediato.\n"
        
        # Nota para forzar el uso de la memoria recuperada y evitar que el LLM refuse diciendo que no tiene memoria
        memory_keywords = ["recuerda", "recuerdas", "memoria", "acuerdas", "acuerda", "conversacion", "conversación", "hablamos", "dije", "dijimos", "saber", "sabes", "pasado"]
        if memory_context and any(kw in normalized_input for kw in memory_keywords):
            prompt += "Nota: El cliente te pregunta sobre lo que recuerdas de él o del pasado. Usa la información de la sección 'Recuerdos de conversaciones pasadas' para responderle de forma personalizada y confirmarle lo que recuerdas.\n"

        # Finalizar el prompt con el formato de la última pregunta y el prefijo de MIA para que complete
        prompt += f"Cliente: {user_input}\nMIA: "

        response_stream = self._generate(prompt)

        if response_stream:
            full_response = ""
            current_chunk = ""
            delimiters = [".", "?", "!", "\n"]

            for chunk in response_stream:
                current_chunk += chunk
                full_response += chunk

                # Procesar frases completas acumuladas en current_chunk
                while True:
                    first_idx = -1
                    found_delim = None
                    for d in delimiters:
                        idx = current_chunk.find(d)
                        if idx != -1:
                            if first_idx == -1 or idx < first_idx:
                                first_idx = idx
                                found_delim = d
                    
                    if found_delim is not None:
                        sentence = current_chunk[:first_idx + 1]
                        current_chunk = current_chunk[first_idx + 1:]
                        cleaned = sentence.strip()
                        if len(cleaned) > 1:
                            yield cleaned
                    else:
                        break

            # Retornar residuo final
            if current_chunk.strip():
                yield current_chunk.strip()

            # Forzar el comando del robot si el usuario pidió preparar y el modelo no lo incluyó en el texto
            if target_prepare_drink and f"[ROBOT:PREPARAR:{target_prepare_drink}]" not in full_response:
                robot_cmd = f" [ROBOT:PREPARAR:{target_prepare_drink}]"
                full_response += robot_cmd
                yield robot_cmd

            # Evitamos guardar el contexto visual en el historial corto y largo
            # para que en el siguiente turno MIA no se quede hablando del objeto que vio.
            if not visual_context:
                self._add_to_history("user", user_input)
                self._add_to_history("assistant", full_response)
                
                if self.memory:
                    self.memory.store_conversation(user_input, full_response, "")
        else:
            yield "Lo siento, estoy teniendo problemas para conectarme con mi cerebro."

    def learn_fact(self, fact, category="general"):
        if not self.memory:
            print("⚠️ Memoria no disponible")
            return False
        self.memory.store_knowledge(fact, category)
        return True

    def get_memory_stats(self):
        if not self.memory:
            return {"conversations": 0, "knowledge": 0, "enabled": False}
        stats = self.memory.get_stats()
        stats["enabled"] = True
        return stats

