import speech_recognition as sr
import threading
import unicodedata
import time
import audioop
from queue import Queue
from config import (
    LISTEN_TIMEOUT, COMMAND_TIMEOUT, COMMAND_PHRASE_LIMIT,
    AMBIENT_NOISE_DURATION, STT_LANGUAGE,
    WAKE_WORD, WAKE_PHRASES, MIN_ENERGY_THRESHOLD,
    DEBUG_EAR, MICROPHONE_NAME
)


class Ear:
    """Escucha con wake word 'Hey MIA' — solo procesa audio cuando se activa.

    Flujo tipo Alexa/Google:
    1. Escucha pasivamente esperando 'Hey MIA' (bajo consumo)
    2. Al detectar wake word → entra en modo activo
    3. Captura el comando del usuario
    4. Vuelve a modo pasivo

    Anti-eco: Se silencia automáticamente cuando MIA está hablando.
    """

    def __init__(self, socketio=None):
        self.recognizer = sr.Recognizer()
        self.socketio = socketio
        
        # Umbral de silencio relajado: a los 0.6 segundos de silencio se detiene la escucha
        # Esto evita que corte a las personas a mitad de frase.
        self.recognizer.pause_threshold = 0.6
        # Desactivamos el ajuste dinámico para evitar que la música fuerte 
        # suba el umbral excesivamente y vuelva sorda a MIA.
        self.recognizer.dynamic_energy_threshold = False
        self.recognizer.energy_threshold = MIN_ENERGY_THRESHOLD
        
        # Buscar el índice del dispositivo para "AudioRelay Virtual Mic" (o cualquier otro provisto)
        device_index = None
        if MICROPHONE_NAME:
            try:
                mics = sr.Microphone.list_microphone_names()
                for i, name in enumerate(mics):
                    # Fallback suave a lower por si caracteres cambian de caso, ignorando errores unicode feos
                    if MICROPHONE_NAME.lower() in str(name).lower():
                        device_index = i
                        print(f"🎙️ Encontrado '{MICROPHONE_NAME}': Dispositivo #{i} ({name})")
                        break
                
                if device_index is None:
                    print(f"⚠️ Micrófono '{MICROPHONE_NAME}' NO encontrado.")
                    print(f"🔹 Micrófonos detectados: {mics}")
                    print("🎙️ Cayendo de nuevo a usar el micrófono por defecto (default).")
            except Exception as e:
                print(f"⚠️ Error buscando micrófono específico: {e}")
                
        self.microphone = sr.Microphone(device_index=device_index)

        # Cola de comandos transcritos (los consume el Assistant)
        self.audio_queue = Queue()
        self.is_listening = False

        # Estado del wake word
        self.is_activated = False

        # Anti-eco: función que indica si MIA está hablando
        # El Assistant la configura con set_mute_check()
        self._mute_check = None

        # Callback cuando se detecta wake word (para confirmación auditiva)
        self._on_wake_callback = None

        # Calibrar ruido ambiental UNA sola vez al inicio
        self._calibrate()

    def _calibrate(self):
        """Calibra el umbral de ruido ambiental.
        
        AudioRelay Virtual Mic produce valores de calibración inflados (10000-25000)
        porque el driver virtual genera ruido diferente al silencio real.
        Para AudioRelay usamos un umbral fijo bajo en lugar de auto-calibrar.
        """
        print("🎤 Calibrando micrófono...")
        is_audiorelay = MICROPHONE_NAME and "audiorelay" in MICROPHONE_NAME.lower()
        
        if is_audiorelay:
            # AudioRelay: usar umbral fijo, la auto-calibración da valores inútiles
            self.recognizer.energy_threshold = MIN_ENERGY_THRESHOLD
            print(f"🎤 AudioRelay detectado — umbral fijo: {self.recognizer.energy_threshold:.0f}")
        else:
            # Micrófono físico: auto-calibrar normalmente
            try:
                with self.microphone as source:
                    self.recognizer.adjust_for_ambient_noise(
                        source, duration=AMBIENT_NOISE_DURATION
                    )
                # Asegurar mínimo
                if self.recognizer.energy_threshold < MIN_ENERGY_THRESHOLD:
                    self.recognizer.energy_threshold = MIN_ENERGY_THRESHOLD
                # Cap máximo para no quedarse sordo (aumentado para entornos con mucho ruido)
                if self.recognizer.energy_threshold > 4000:
                    self.recognizer.energy_threshold = 4000
                print(f"🎤 Micrófono calibrado (umbral: {self.recognizer.energy_threshold:.0f})")
            except Exception as e:
                print(f"⚠️ Error calibrando micrófono: {e}")
                self.recognizer.energy_threshold = MIN_ENERGY_THRESHOLD

    def set_mute_check(self, check_fn):
        """Configura la función anti-eco.

        check_fn: callable que retorna True cuando el oído debe silenciarse
                  (ej: lambda: self.voice.is_speaking)
        """
        self._mute_check = check_fn

    def set_wake_callback(self, callback_fn):
        """Configura función que se llama al detectar wake word.

        callback_fn: callable sin argumentos (ej: reproducir sonido de confirmación)
        """
        self._on_wake_callback = callback_fn

    def _is_muted(self):
        """Verifica si el oído debe estar silenciado (MIA está hablando)"""
        if self._mute_check:
            return self._mute_check()
        return False

    # ------------------------------------------------------------------
    # Transcripción
    # ------------------------------------------------------------------

    def _transcribe(self, audio):
        """Convierte audio a texto usando Google STT"""
        import time
        try:
            text = self.recognizer.recognize_google(audio, language=STT_LANGUAGE)
            return text.strip()
        except sr.UnknownValueError:
            # No se entendió el audio — normal en escucha pasiva
            return None
        except sr.RequestError as e:
            print(f"❌ Error en Google STT (Red Inaccesible): {e}")
            time.sleep(2) # Evitar spam masivo si no hay internet
            return None

    # ------------------------------------------------------------------
    # Detección de Wake Word
    # ------------------------------------------------------------------

    def _normalize(self, text):
        """Normaliza texto: quita acentos, lowercase, limpia espacios"""
        text = text.lower().strip()
        # Quitar acentos (mía → mia, é → e, etc.)
        text = unicodedata.normalize('NFD', text)
        text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
        return text

    def _contains_wake_word(self, text):
        """Verifica si el texto contiene alguna variante del wake word"""
        text_normalized = self._normalize(text)

        # Verificar frases completas ("hey mia", "oye mia", etc.)
        for phrase in WAKE_PHRASES:
            phrase_normalized = self._normalize(phrase)
            if phrase_normalized in text_normalized:
                return True

        # Fallback: si "mia" aparece en cualquier parte del texto
        if WAKE_WORD in text_normalized:
            return True

        return False

    def _extract_command_after_wake(self, text):
        """Extrae el comando que viene después del wake word.

        Ejemplo: 'Hey MIA qué hora es' → 'qué hora es'
        Usa normalize para manejar acentos (mía → mia).
        """
        text_normalized = self._normalize(text)

        # Intentar extraer lo que sigue después de la frase de activación
        for phrase in WAKE_PHRASES:
            phrase_normalized = self._normalize(phrase)
            idx = text_normalized.find(phrase_normalized)
            if idx != -1:
                # Calcular posición en el texto ORIGINAL para preservar acentos
                remaining = text_normalized[idx + len(phrase_normalized):].strip()
                if remaining:
                    # Extraer del texto original (posición proporcional)
                    original_remaining = text[len(text) - len(text.strip()):]
                    # Buscar la porción restante en el texto original
                    for p in WAKE_PHRASES:
                        p_lower = p.lower()
                        p_idx = text.lower().find(p_lower)
                        if p_idx != -1:
                            cmd = text[p_idx + len(p):].strip()
                            if cmd:
                                return cmd

        # Fallback: extraer después de la palabra clave sola
        idx = text_normalized.find(WAKE_WORD)
        if idx != -1:
            # Encontrar posición equivalente en texto original
            for i in range(len(text)):
                if self._normalize(text[:i+1]).find(WAKE_WORD) != -1:
                    command = text[i+1:].strip()
                    if command:
                        return command
                    break

        return None

    # ------------------------------------------------------------------
    # Ciclo de escucha principal
    # ------------------------------------------------------------------

    def _listen_once(self, timeout, phrase_limit=None):
        """Escucha una sola vez y retorna el audio capturado.
        
        Esta es una re-implementación de `recognizer.listen()` para poder
        emitir el evento 'escuchando' en el momento exacto en que la energía
        supera el umbral, en lugar de esperar a que termine la frase.
        """
        assert isinstance(self.microphone, sr.Microphone)
        
        seconds_per_buffer = self.microphone.CHUNK / self.microphone.SAMPLE_RATE
        pause_buffer_count = int(self.recognizer.pause_threshold / seconds_per_buffer)
        
        # Configuración del timeout
        timeout_buffer_count = None
        if timeout is not None:
            timeout_buffer_count = int(timeout / seconds_per_buffer)
        
        phrase_audio_data = bytearray()
        silent_chunks = 0
        
        listening_for_phrase = False
        
        # Contadores
        elapsed_buffers = 0
        
        try:
            with self.microphone as source:
                while True:
                    elapsed_buffers += 1

                    buffer = source.stream.read(source.CHUNK)
                    if len(buffer) == 0: break

                    # Timeout si no se ha empezado a hablar
                    if not listening_for_phrase and timeout_buffer_count is not None and elapsed_buffers > timeout_buffer_count:
                        raise sr.WaitTimeoutError("Timeout: no se detectó habla")

                    energy = audioop.rms(buffer, source.SAMPLE_WIDTH)
                    is_speech = energy > self.recognizer.energy_threshold

                    if is_speech and not listening_for_phrase:
                        # ¡COMIENZO DEL HABLA DETECTADO!
                        listening_for_phrase = True
                        if self.socketio:
                            self.socketio.emit('escuchando')
                    
                    if listening_for_phrase:
                        phrase_audio_data.extend(buffer)

                        if is_speech:
                            silent_chunks = 0
                        else:
                            silent_chunks += 1
                            if silent_chunks > pause_buffer_count:
                                # FIN DE FRASE por silencio
                                break
                    
                    # Límite de tiempo de la frase
                    current_phrase_time = len(phrase_audio_data) / (source.SAMPLE_RATE * source.SAMPLE_WIDTH)
                    if phrase_limit and current_phrase_time > phrase_limit:
                        break

            if len(phrase_audio_data) > 0:
                return sr.AudioData(bytes(phrase_audio_data), source.SAMPLE_RATE, source.SAMPLE_WIDTH)

        except sr.WaitTimeoutError:
            return None # Comportamiento esperado
        except Exception as e:
            print(f"❌ Error capturando audio en _listen_once: {e}")
            return None
        
        return None

    def listen_continuous(self):
        """Ciclo principal: escucha pasiva → wake word → comando → respuesta → repeat"""
        self.is_listening = True
        print("\n🎤 Escucha pasiva activada — Di 'Hey MIA' para activarme")

        was_muted = False

        while self.is_listening:
            try:
                # === ANTI-ECO Y CONFLICTOS: Si MIA está hablando o procesando, no escuchar ===
                if self._is_muted():
                    was_muted = True
                    time.sleep(0.3)
                    continue
                else:
                    if was_muted:
                        # Acaba de terminar de hablar/procesar, avisar en consola
                        print("\n🎤 MIA lista de nuevo — Di 'Hey MIA' para activarme")
                        was_muted = False

                # === FASE 1: Escucha pasiva (esperando wake word) ===
                audio = self._listen_once(timeout=LISTEN_TIMEOUT, phrase_limit=8)
                if audio is None:
                    continue

                # Re-verificar mute después de capturar (pudo empezar a hablar)
                if self._is_muted():
                    continue

                text = self._transcribe(audio)
                if text is None:
                    continue

                # DEBUG: Mostrar qué escuchó el micrófono (solo si DEBUG_EAR=True)
                if DEBUG_EAR:
                    print(f"    👂 [DEBUG] Escuché: '{text}'")

                # ¿Contiene wake word?
                if not self._contains_wake_word(text):
                    continue

                # === WAKE WORD DETECTADO ===
                print(f"🔔 Wake word detectado en: '{text}'")

                # Verificar si ya viene un comando incluido
                # Ej: "Hey MIA qué hora es" → procesar directo, sin "¿Sí?"
                inline_command = self._extract_command_after_wake(text)
                if inline_command:
                    print(f"👤 Comando inline: {inline_command}")
                    self.audio_queue.put(inline_command)
                    continue

                # No hay comando inline → necesitamos Fase 2
                # Confirmación auditiva: MIA dice "¿Sí?" para que el usuario sepa que hable
                if self._on_wake_callback:
                    self._on_wake_callback()

                # === FASE 2: Modo activo — esperando comando ===
                print("🟢 MIA escuchando... ¿Qué necesitas?")
                self.is_activated = True

                command_audio = self._listen_once(
                    timeout=COMMAND_TIMEOUT,
                    phrase_limit=COMMAND_PHRASE_LIMIT
                )

                self.is_activated = False

                if command_audio is None:
                    print("⏹️ No escuché nada, volviendo a modo pasivo")
                    continue

                command_text = self._transcribe(command_audio)
                if command_text:
                    print(f"👤 Comando: {command_text}")
                    self.audio_queue.put(command_text)
                else:
                    print("⏹️ No entendí, volviendo a modo pasivo")

            except Exception as e:
                print(f"❌ Error en escucha continua: {e}")

    # ------------------------------------------------------------------
    # Control de threads
    # ------------------------------------------------------------------

    def start_listening_thread(self):
        """Inicia escucha continua en un thread de background"""
        listener_thread = threading.Thread(
            target=self.listen_continuous, daemon=True, name="EarThread"
        )
        listener_thread.start()
        return listener_thread

    def stop_listening(self):
        """Detiene la escucha"""
        print("🎤 Deteniendo escucha...")
        self.is_listening = False
        self.is_activated = False