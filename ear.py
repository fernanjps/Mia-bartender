"""
MIA - Oído (Raspberry Pi 5)

Wake word EN VIVO (streaming continuo de audio, no por bloques):
  1. openWakeWord con tu modelo entrenado `models/hey_mia.onnx`  (recomendado)
  2. Vosk con gramática restringida ("hey mia", "oye mia", "mia")   (respaldo offline)
  3. Google STT por frases                                         (último recurso)
Botón físico PTT en GPIO 26 (gpiozero) para ambientes ruidosos.
Anti-eco: no escucha mientras MIA procesa o habla.
Si no hay micrófono conectado, se desactiva limpiamente (`has_microphone = False`).
"""

import os
import json
import time
import queue
import threading
import unicodedata

import numpy as np
import pyaudio
import speech_recognition as sr

from config import (
    LISTEN_TIMEOUT, COMMAND_TIMEOUT, COMMAND_PHRASE_LIMIT,
    AMBIENT_NOISE_DURATION, STT_LANGUAGE,
    WAKE_WORD, WAKE_PHRASES, MIN_ENERGY_THRESHOLD,
    DEBUG_EAR, MICROPHONE_NAME,
    VOSK_MODEL_PATH, VOSK_GRAMMAR, GPIO_PIN_BUTTON,
    WAKEWORD_ENGINE, WAKEWORD_MODEL_PATH, WAKEWORD_THRESHOLD, WAKEWORD_COOLDOWN,
)

try:
    from openwakeword.model import Model as OWWModel
    OWW_INSTALLED = True
except Exception:
    OWW_INSTALLED = False

try:
    from vosk import Model as VoskModel, KaldiRecognizer, SetLogLevel
    SetLogLevel(-1)
    VOSK_INSTALLED = True
except Exception:
    VOSK_INSTALLED = False

try:
    from gpiozero import Button as GpioButton
    GPIOZERO_BUTTON_AVAILABLE = True
except Exception:
    GPIOZERO_BUTTON_AVAILABLE = False


TARGET_RATE = 16000
FRAME_16K = 1280  # 80 ms, tamaño que espera openWakeWord


def _pcm_rms(buf: bytes, sample_width: int) -> float:
    """Calcula el RMS de un buffer PCM con NumPy (compatible con Python 3.13+, sin audioop)."""
    if not buf:
        return 0.0
    dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
    dtype = dtype_map.get(sample_width)
    if dtype is None:
        return 0.0
    samples = np.frombuffer(buf, dtype=dtype).astype(np.float64)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples * samples)))


class Ear:
    def __init__(self, socketio=None):
        self.socketio = socketio
        self.audio_queue = queue.Queue()
        self.is_listening = False
        self.is_activated = False
        self._mute_check = None
        self._on_wake_callback = None
        self._button_pressed_event = threading.Event()
        self.button = None
        self._vosk_model = None
        self._vosk_cmd_rec = None
        self._pending_inline_cmd = None

        self.device_index, self.device_rate = self._find_input_device()
        self.has_microphone = self.device_index is not None
        if not self.has_microphone:
            print("🎤 [EAR] No se detectó ningún micrófono. Oído local desactivado.")
            return

        self.recognizer = sr.Recognizer()
        self.recognizer.pause_threshold = 0.6
        self.recognizer.dynamic_energy_threshold = False
        self.recognizer.energy_threshold = MIN_ENERGY_THRESHOLD
        self.microphone = sr.Microphone(device_index=self.device_index)

        self._setup_hardware_button()
        self._init_vosk_backup()
        self.engine, self._detector, self._reset_detector = self._setup_wake_engine()
        self._calibrate()

    # ------------------------------------------------------------------
    # Dispositivo
    # ------------------------------------------------------------------

    def _find_input_device(self):
        try:
            pa = pyaudio.PyAudio()
        except Exception as e:
            print(f"⚠️ [EAR] PyAudio no disponible: {e}")
            return None, None
        try:
            candidates = []
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) > 0:
                    candidates.append((i, info))
            if not candidates:
                return None, None
            if MICROPHONE_NAME:
                for i, info in candidates:
                    if MICROPHONE_NAME.lower() in str(info.get("name", "")).lower():
                        print(f"🎙️ [EAR] Micrófono: #{i} {info['name']}")
                        return i, int(info.get("defaultSampleRate", 16000))
            try:
                info = pa.get_default_input_device_info()
                i = int(info["index"])
            except Exception:
                i, info = candidates[0]
            print(f"🎙️ [EAR] Micrófono por defecto: #{i} {info['name']}")
            return i, int(info.get("defaultSampleRate", 16000))
        finally:
            pa.terminate()

    def _calibrate(self):
        try:
            with self.microphone as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=AMBIENT_NOISE_DURATION)
            self.recognizer.energy_threshold = min(4000, max(MIN_ENERGY_THRESHOLD, self.recognizer.energy_threshold))
        except Exception as e:
            print(f"⚠️ [EAR] Error calibrando: {e}")
        print(f"🎤 [EAR] Umbral de voz: {self.recognizer.energy_threshold:.0f}")

    # ------------------------------------------------------------------
    # Botón físico
    # ------------------------------------------------------------------

    def _setup_hardware_button(self):
        if not GPIOZERO_BUTTON_AVAILABLE:
            return
        try:
            self.button = GpioButton(GPIO_PIN_BUTTON, pull_up=True, bounce_time=0.05)
            self.button.when_pressed = self._on_button_pressed
            print(f"🔘 [EAR] Botón PTT en GPIO {GPIO_PIN_BUTTON}")
        except Exception as e:
            print(f"ℹ️ [EAR] Botón GPIO no disponible ({e}).")

    def _on_button_pressed(self):
        if not self._is_muted():
            print("🔘 [PTT] Botón presionado")
            self._button_pressed_event.set()

    # ------------------------------------------------------------------
    # Motores de wake word
    # ------------------------------------------------------------------

    def _init_vosk_backup(self):
        """Carga Vosk una sola vez en RAM para wake word híbrido y STT offline."""
        if not VOSK_INSTALLED or not os.path.isdir(VOSK_MODEL_PATH):
            return
        try:
            self._vosk_model = VoskModel(VOSK_MODEL_PATH)
            self._vosk_cmd_rec = KaldiRecognizer(self._vosk_model, TARGET_RATE, json.dumps(VOSK_GRAMMAR))
            print(f"🧠 [EAR] Vosk offline listo en: {VOSK_MODEL_PATH}")
        except Exception as e:
            self._vosk_model = None
            self._vosk_cmd_rec = None
            print(f"⚠️ [EAR] No se pudo cargar respaldo Vosk: {e}")

    def _setup_wake_engine(self):
        """Devuelve (nombre, detector(frame_int16) -> (bool, info), reset())."""
        engine = (WAKEWORD_ENGINE or "").lower()

        if engine == "openwakeword":
            det = self._init_oww()
            if det:
                return ("openwakeword",) + det
            print("ℹ️ [EAR] openWakeWord no disponible, probando Vosk...")
            engine = "vosk"

        if engine == "vosk":
            det = self._init_vosk()
            if det:
                return ("vosk",) + det
            print("ℹ️ [EAR] Vosk no disponible, usando Google STT por frases (requiere internet).")

        return "stt", None, None

    def _init_oww(self):
        if not OWW_INSTALLED:
            print("ℹ️ [EAR] Instala 'openwakeword' y 'onnxruntime' para usar hey_mia.onnx.")
            return None
        path = WAKEWORD_MODEL_PATH
        if not os.path.exists(path):
            print(f"ℹ️ [EAR] No encontré el modelo de wake word en: {path}")
            return None
        framework = "tflite" if path.endswith(".tflite") else "onnx"

        def build():
            return OWWModel(wakeword_models=[path], inference_framework=framework)

        try:
            model = build()
        except Exception:
            try:  # Primera ejecución: faltan los modelos de features (melspectrogram/embedding)
                import openwakeword.utils
                print("⬇️ [EAR] Descargando modelos base de openWakeWord (solo la primera vez)...")
                openwakeword.utils.download_models(model_names=["alexa"])
                model = build()
            except Exception as e:
                print(f"⚠️ [EAR] No se pudo cargar openWakeWord: {e}")
                return None

        print(f"✅ [EAR] Wake word 'Hey MIA' activo (openWakeWord, umbral {WAKEWORD_THRESHOLD})")

        def detect(frame):
            scores = model.predict(frame)
            score = max(scores.values()) if scores else 0.0
            if DEBUG_EAR and score > 0.1:
                print(f"    👂 score={score:.2f}")
            if score >= WAKEWORD_THRESHOLD:
                return True, f"oww={score:.2f}"

            if self._vosk_cmd_rec is not None:
                try:
                    raw_bytes = frame.tobytes()
                    if self._vosk_cmd_rec.AcceptWaveform(raw_bytes):
                        v_text = json.loads(self._vosk_cmd_rec.Result()).get("text", "")
                        if self._contains_wake_word(v_text):
                            cmd = self._extract_command_after_wake(v_text)
                            if cmd:
                                self._pending_inline_cmd = cmd
                            return True, f"vosk_final='{v_text}'"
                    else:
                        v_partial = json.loads(self._vosk_cmd_rec.PartialResult()).get("partial", "")
                        if self._contains_wake_word(v_partial):
                            return True, f"vosk_partial='{v_partial}'"
                except Exception:
                    pass

            return False, f"score={score:.2f}"

        def reset():
            try:
                model.reset()
            except Exception:
                pass
            if self._vosk_cmd_rec is not None:
                try:
                    self._vosk_cmd_rec.Reset()
                except Exception:
                    pass

        return detect, reset

    def _init_vosk(self):
        if self._vosk_model is None:
            self._init_vosk_backup()
        if self._vosk_model is None:
            return None
        try:
            rec = KaldiRecognizer(self._vosk_model, TARGET_RATE, json.dumps(VOSK_GRAMMAR))
        except Exception as e:
            print(f"⚠️ [EAR] Error inicializando reconocedor Vosk: {e}")
            return None
        print("✅ [EAR] Wake word activo (Vosk)")

        def detect(frame):
            if rec.AcceptWaveform(frame.tobytes()):
                text = json.loads(rec.Result()).get("text", "")
                if self._contains_wake_word(text):
                    cmd = self._extract_command_after_wake(text)
                    if cmd:
                        self._pending_inline_cmd = cmd
                    return True, text
            else:
                text = json.loads(rec.PartialResult()).get("partial", "")
                if self._contains_wake_word(text):
                    return True, text
            return False, text

        def reset():
            rec.Reset()

        return detect, reset

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def set_mute_check(self, fn):
        self._mute_check = fn

    def set_wake_callback(self, fn):
        self._on_wake_callback = fn

    def _is_muted(self):
        return bool(self._mute_check and self._mute_check())

    # ------------------------------------------------------------------
    # Texto
    # ------------------------------------------------------------------

    def _normalize(self, text):
        text = unicodedata.normalize("NFD", (text or "").lower().strip())
        return "".join(c for c in text if unicodedata.category(c) != "Mn")

    def _contains_wake_word(self, text):
        t = self._normalize(text)
        if not t:
            return False
        return any(self._normalize(p) in t for p in WAKE_PHRASES) or WAKE_WORD in t.split()

    def _extract_command_after_wake(self, text):
        t = self._normalize(text)
        for p in sorted(WAKE_PHRASES, key=len, reverse=True):
            idx = t.find(self._normalize(p))
            if idx != -1:
                cmd = text[idx + len(p):].strip(" ,.")
                return cmd or None
        return None

    def _transcribe_vosk_offline(self, audio):
        """Transcribe AudioData en 100% offline usando el modelo Vosk local."""
        if self._vosk_model is None:
            return None
        try:
            rec = KaldiRecognizer(self._vosk_model, TARGET_RATE, json.dumps(VOSK_GRAMMAR))
            raw_pcm = audio.get_raw_data(convert_rate=TARGET_RATE, convert_width=2)
            rec.AcceptWaveform(raw_pcm)
            text = json.loads(rec.FinalResult()).get("text", "").strip()
            if text:
                print(f"📴 [EAR-OFFLINE Vosk] Transcripción: '{text}'")
            return text or None
        except Exception as e:
            print(f"⚠️ [EAR] Error en STT offline Vosk: {e}")
            return None

    def _transcribe(self, audio):
        try:
            return self.recognizer.recognize_google(audio, language=STT_LANGUAGE).strip()
        except sr.UnknownValueError:
            return None
        except sr.RequestError as e:
            print(f"⚠️ [EAR] Google STT sin conexión ({e}). Usando respaldo Vosk offline...")
            offline_text = self._transcribe_vosk_offline(audio)
            if offline_text:
                return offline_text
            time.sleep(1)
            return None

    # ------------------------------------------------------------------
    # Captura de una frase (comando)
    # ------------------------------------------------------------------

    def _listen_once(self, timeout, phrase_limit=None):
        seconds_per_buffer = self.microphone.CHUNK / self.microphone.SAMPLE_RATE
        pause_count = int(self.recognizer.pause_threshold / seconds_per_buffer)
        timeout_count = int(timeout / seconds_per_buffer) if timeout else None
        data, silent, speaking, n = bytearray(), 0, False, 0
        try:
            with self.microphone as source:
                while self.is_listening:
                    n += 1
                    buf = source.stream.read(source.CHUNK)
                    if not buf:
                        break
                    if not speaking and timeout_count and n > timeout_count:
                        return None
                    is_speech = _pcm_rms(buf, source.SAMPLE_WIDTH) > self.recognizer.energy_threshold
                    if is_speech and not speaking:
                        speaking = True
                        if self.socketio:
                            self.socketio.emit("escuchando")
                    if speaking:
                        data.extend(buf)
                        silent = 0 if is_speech else silent + 1
                        if silent > pause_count:
                            break
                        if phrase_limit and len(data) / (source.SAMPLE_RATE * source.SAMPLE_WIDTH) > phrase_limit:
                            break
                if data:
                    return sr.AudioData(bytes(data), source.SAMPLE_RATE, source.SAMPLE_WIDTH)
        except Exception as e:
            print(f"❌ [EAR] Error capturando audio: {e}")
        return None

    def _capture_active_command(self):
        # Dejar que suene el "¿Sí?" para no grabarlo como comando
        time.sleep(0.3)
        t0 = time.time()
        while self._is_muted() and time.time() - t0 < 3:
            time.sleep(0.05)
        time.sleep(0.4)

        print("🟢 [EAR] Escuchando comando...")
        self.is_activated = True
        audio = self._listen_once(timeout=COMMAND_TIMEOUT, phrase_limit=COMMAND_PHRASE_LIMIT)
        self.is_activated = False

        text = self._transcribe(audio) if audio else None
        if text:
            print(f"👤 [EAR] Comando: {text}")
            self.audio_queue.put(text)
        else:
            print("⏹️ [EAR] No escuché ninguna orden.")

    def _wake_triggered(self):
        print("🔔 [EAR] ¡Hey MIA detectado!")
        if self._on_wake_callback:
            self._on_wake_callback()
        self._capture_active_command()

    # ------------------------------------------------------------------
    # Bucles de escucha
    # ------------------------------------------------------------------

    def _open_stream(self, pa):
        """Abre el micrófono a 16 kHz; si no lo soporta, a su tasa nativa (se re-muestrea)."""
        for rate in (TARGET_RATE, self.device_rate, 48000, 44100):
            if not rate:
                continue
            try:
                frames = int(FRAME_16K * rate / TARGET_RATE)
                stream = pa.open(format=pyaudio.paInt16, channels=1, rate=rate, input=True,
                                 input_device_index=self.device_index, frames_per_buffer=frames)
                return stream, rate, frames
            except Exception:
                continue
        raise RuntimeError("No se pudo abrir el micrófono a ninguna frecuencia")

    @staticmethod
    def _to_16k(samples, rate):
        if rate == TARGET_RATE:
            return samples
        x_old = np.linspace(0, 1, num=len(samples), endpoint=False)
        x_new = np.linspace(0, 1, num=FRAME_16K, endpoint=False)
        return np.interp(x_new, x_old, samples).astype(np.int16)

    def _streaming_loop(self):
        """Wake word en vivo: analiza el audio cada 80 ms."""
        pa = pyaudio.PyAudio()
        stream, rate, frames = self._open_stream(pa)
        print(f"🎤 [EAR] Escuchando en vivo 'Hey MIA' ({self.engine}, {rate} Hz)")
        last_wake, was_muted = 0.0, False

        def reopen():
            nonlocal stream, rate, frames
            stream, rate, frames = self._open_stream(pa)
            self._reset_detector()

        try:
            while self.is_listening:
                if self._button_pressed_event.is_set():
                    self._button_pressed_event.clear()
                    stream.close()
                    self._wake_triggered()
                    reopen()
                    continue

                data = stream.read(frames, exception_on_overflow=False)

                if self._is_muted():
                    was_muted = True
                    continue
                if was_muted:
                    was_muted = False
                    self._reset_detector()  # descarta audio de la propia MIA
                    continue

                frame = self._to_16k(np.frombuffer(data, dtype=np.int16), rate)
                hit, info = self._detector(frame)
                if hit and time.time() - last_wake > WAKEWORD_COOLDOWN:
                    last_wake = time.time()
                    print(f"    ({info})")
                    inline_cmd = self._pending_inline_cmd
                    self._pending_inline_cmd = None
                    if inline_cmd:
                        print(f"👤 [EAR] Comando inline (Vosk): {inline_cmd}")
                        self.audio_queue.put(inline_cmd)
                        self._reset_detector()
                    else:
                        stream.close()
                        self._wake_triggered()
                        reopen()
        finally:
            try:
                stream.close()
            except Exception:
                pass
            pa.terminate()

    def _stt_loop(self):
        """Respaldo: frases completas a Google STT, busca 'mia' en el texto."""
        print("🎤 [EAR] Escucha por frases (Google STT) — di 'Hey MIA ...'")
        while self.is_listening:
            if self._button_pressed_event.is_set():
                self._button_pressed_event.clear()
                self._wake_triggered()
                continue
            if self._is_muted():
                time.sleep(0.2)
                continue
            audio = self._listen_once(timeout=LISTEN_TIMEOUT, phrase_limit=8)
            if not audio or self._is_muted():
                continue
            text = self._transcribe(audio)
            if DEBUG_EAR and text:
                print(f"    👂 '{text}'")
            if not text or not self._contains_wake_word(text):
                continue
            cmd = self._extract_command_after_wake(text)
            if cmd:
                print(f"👤 [EAR] Comando inline: {cmd}")
                self.audio_queue.put(cmd)
            else:
                self._wake_triggered()

    def listen_continuous(self):
        self.is_listening = True
        while self.is_listening:
            try:
                if self._detector:
                    self._streaming_loop()
                else:
                    self._stt_loop()
            except Exception as e:
                print(f"❌ [EAR] Error en escucha: {e}. Reintentando en 2s...")
                time.sleep(2)

    def start_listening_thread(self):
        t = threading.Thread(target=self.listen_continuous, daemon=True, name="EarThread")
        t.start()
        return t

    def stop_listening(self):
        self.is_listening = False
        self.is_activated = False
        if self.button:
            try:
                self.button.close()
            except Exception:
                pass