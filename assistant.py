import re
import threading
import time
from queue import Empty, Queue
from eye import Eye
from ear import Ear
from voice import Voice
from brain import Brain
from robot_control import RobotControl
from config import (
    VISUAL_COMMENT_COOLDOWN, VISION_CHECK_INTERVAL,
    VISION_POST_COMMENT_PAUSE, PROACTIVE_VISION, VISION_KEYWORDS,
    ENABLE_BACKEND_MIC, VISION_MODEL, PROACTIVE_POUR_COMMENT
)


class VoiceAssistant:
    """Orquesta visión, audio, cerebro y robot de MIA (Raspberry Pi 5, todo local).

    Flujo por comando:
      texto (wake word / botón GPIO / micrófono web) -> Brain (streaming de frases)
      -> cada frase va a Edge-TTS en cuanto existe (voz en vivo)
      -> si fue un pedido, el robot arranca EN PARALELO mientras MIA habla.

    Anti-eco: el oído se silencia mientras MIA procesa o habla.
    """

    def __init__(self, socketio=None):
        print("=" * 50)
        print("🤖 Inicializando MIA...")
        print("=" * 50)

        self.socketio = socketio
        self.audio_queue = Queue()  # Cola usada si el Ear está apagado / sin micrófono
        self.clients_connected = 0  # Navegadores conectados (los actualiza server.py)

        self.eye = Eye(vision_model=VISION_MODEL)
        self.voice = Voice()
        self.brain = Brain()
        self.robot = RobotControl()

        self.ear = None
        if ENABLE_BACKEND_MIC:
            try:
                ear = Ear(socketio=self.socketio)
                self.ear = ear if ear.has_microphone else None
            except Exception as e:
                print(f"⚠️ Micrófono local no disponible ({e}).")
        if not self.ear:
            print("🎧 Sin micrófono local: usa el botón 🎤 de la página web para probar.")

        self.is_running = False
        self._last_visual_comment = 0
        self._threads = []

        self.state = "idle"
        self.on_state_change = None

        self._speaking_lock = threading.Lock()
        self.audio_playback_done = threading.Event()
        self.latest_frontend_image = None

        # Todo audio generado (respuestas, "¿Sí?", agradecimientos) se envía al navegador
        self.voice.on_audio_ready = lambda text, b64: self.set_state(
            "audio_payload_chunk", {"text": text, "audio": b64}
        )

        if self.ear:
            self.ear.set_mute_check(lambda: self._speaking_lock.locked() or self.voice.is_speaking)
            self.ear.set_wake_callback(self._on_wake)

        print("=" * 50)
        print("✅ MIA lista para funcionar")
        print("=" * 50)

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    @property
    def command_queue(self):
        return self.ear.audio_queue if self.ear else self.audio_queue

    def submit_text(self, text):
        """Punto de entrada único para comandos de texto (web, API, botón)."""
        self.command_queue.put(text)

    def set_state(self, new_state, data=None):
        if new_state != "audio_payload_chunk":
            self.state = new_state
        if self.on_state_change:
            self.on_state_change(new_state, data)

    def _on_wake(self):
        self.set_state("listening")
        self.voice.speak_async("¿Sí?")

    def _needs_vision(self, text):
        t = text.lower()
        return any(kw in t for kw in VISION_KEYWORDS)

    def _wait_playback(self, spoken_chars):
        """Espera a que el navegador termine de reproducir (o un tiempo estimado si no hay navegador)."""
        estimate = 2.0 + spoken_chars * 0.075  # ~13 caracteres/seg en voz natural
        if self.clients_connected > 0:
            self.audio_playback_done.wait(timeout=min(60.0, estimate + 4.0))
        else:
            time.sleep(0.5)

    def _say(self, text):
        """Habla un texto completo y espera a que termine (usado fuera del streaming)."""
        self.audio_playback_done.clear()
        self.set_state("speaking", data=text)
        self.voice.speak_async(text)
        self.voice.wait_until_done()
        self.set_state("all_audio_sent")
        self._wait_playback(len(text))

    # ------------------------------------------------------------------
    # Robot (segundo plano)
    # ------------------------------------------------------------------

    def _start_dispense(self, dispense):
        if self.robot.is_busy:
            print("⚠️ [ROBOT] Ocupado, pedido ignorado.")
            return False

        def on_step(idx, total, drink_name):
            if not (PROACTIVE_POUR_COMMENT and total >= 2 and idx == 1):
                return
            if self._speaking_lock.locked() or self.voice.is_speaking:
                return
            if self._speaking_lock.acquire(blocking=False):
                try:
                    self.voice.speak_async(f"Ya va el primer ingrediente de tu {drink_name}, el riel sigue trabajando.")
                finally:
                    self._speaking_lock.release()

        def job():
            drink = dispense.get("trago", "bebida")
            try:
                print(f"🍹 [DISPENSER] Preparando '{drink}' en segundo plano...")
                ok = self.robot.execute_dispense(dispense, on_step_completed=on_step)
                if self.socketio:
                    self.socketio.emit("menu_update", {"drinks": self.brain.get_menu(only_available=False)})
                # Esperar a que MIA termine de hablar la confirmación antes de agradecer
                with self._speaking_lock:
                    if ok:
                        self._say(f"Tu {drink} está lista. ¡Que la disfrutes!")
                    else:
                        self._say(f"Lo siento, tuve un problema preparando tu {drink}.")
                    self.set_state("idle")
            except Exception as e:
                print(f"❌ [ROBOT] Error en el dispensado: {e}")
                self.set_state("idle")

        if self.socketio:
            self.socketio.start_background_task(job)
        else:
            threading.Thread(target=job, daemon=True, name="Dispense").start()
        return True

    # ------------------------------------------------------------------
    # Procesamiento de un comando
    # ------------------------------------------------------------------

    def _process(self, user_input):
        print(f"👤 Usuario dice: {user_input}")
        with self._speaking_lock:
            visual_context = ""
            is_menu_q, _ = self.brain._is_menu_question(user_input)
            is_bar_request = bool(self.brain.detect_order(user_input)) or is_menu_q

            if not is_bar_request and self._needs_vision(user_input):
                self.set_state("vision", data=user_input)
                image_b64 = self.latest_frontend_image or self.eye.capture_image()
                self.latest_frontend_image = None
                if image_b64:
                    visual_context = self.eye.describe_image(image_b64)
                    if visual_context.startswith("("):  # mensajes de error de eye.py
                        visual_context = ""
                if not visual_context:
                    self._say("Perdona, ahora mismo no puedo ver bien con mi cámara.")
                    self.set_state("idle")
                    return

            self.set_state("thinking", data=user_input)
            self.audio_playback_done.clear()

            spoken, chars, dispense_started = 0, 0, False
            for sentence in self.brain.respond_to_user(
                user_input,
                visual_context=visual_context,
                robot_busy=self.robot.is_busy,
                current_drink=self.robot.current_drink,
                current_step_info=self.robot.current_step_info,
            ):
                sentence = re.sub(r"\[[^\]]*\]?", "", sentence).strip()
                if not sentence:
                    continue
                if spoken == 0:
                    self.set_state("speaking", data=sentence)
                spoken += 1
                chars += len(sentence)
                self.voice.speak_async(sentence)  # TTS arranca ya, sin esperar al resto

                # El robot empieza en cuanto hay pedido (en paralelo con la voz)
                if not dispense_started:
                    dispense = self.brain.pop_pending_dispense()
                    if dispense:
                        dispense_started = self._start_dispense(dispense)
                        if dispense_started:
                            self.set_state("preparing_drink", data=dispense.get("trago", ""))
                        else:
                            busy_msg = "Dame un momento, la máquina está ocupada preparando otra bebida. Intenta de nuevo en unos segundos."
                            self.voice.speak_async(busy_msg)
                            chars += len(busy_msg)

            if spoken == 0:
                fallback = "Perdona, no te entendí bien. ¿Me lo repites?"
                self.voice.speak_async(fallback)
                chars = len(fallback)

            self.voice.wait_until_done()
            self.set_state("all_audio_sent")
            self._wait_playback(chars)

            if self.robot.is_busy:
                self.set_state("preparing_drink", data=self.robot.current_drink or "")
            else:
                self.set_state("idle")

    def _listen_for_commands(self):
        print("🎤 Thread de procesamiento de comandos iniciado")
        if self.ear:
            self.ear.start_listening_thread()

        while self.is_running:
            try:
                user_input = self.command_queue.get(timeout=1)
            except Empty:
                if self.state == "listening" and not (self.ear and self.ear.is_activated):
                    self.set_state("idle")
                continue
            try:
                self._process(user_input)
            except Exception as e:
                print(f"❌ Error procesando comando: {e}")
                self.set_state("idle")

    # ------------------------------------------------------------------
    # Visión proactiva (opcional)
    # ------------------------------------------------------------------

    def _react_to_vision(self):
        print("👁️ Thread de visión proactiva iniciado")
        while self.is_running:
            try:
                time.sleep(VISION_CHECK_INTERVAL)
                if time.time() - self._last_visual_comment < VISUAL_COMMENT_COOLDOWN:
                    continue
                if self._speaking_lock.locked() or self.voice.is_speaking or self.robot.is_busy:
                    continue
                if not self.eye.detect_change():
                    continue
                img = self.eye.capture_image()
                desc = self.eye.describe_image(img) if img else ""
                if not desc or desc.startswith("("):
                    continue
                with self._speaking_lock:
                    comment = self.brain.think_about_vision(desc)
                    if comment:
                        self._say(comment)
                        self._last_visual_comment = time.time()
                time.sleep(VISION_POST_COMMENT_PAUSE)
            except Exception as e:
                print(f"❌ Error en visión proactiva: {e}")
                time.sleep(10)

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        print("\n🟢 MIA activada — Di 'Hey MIA' para hablarme\n")
        targets = [("Audio", self._listen_for_commands)]
        if PROACTIVE_VISION:
            targets.insert(0, ("Vision", self._react_to_vision))
        for name, target in targets:
            t = threading.Thread(target=target, daemon=True, name=f"MIA-{name}")
            t.start()
            self._threads.append(t)

    def stop(self):
        print("\n🔴 Deteniendo MIA...")
        self.is_running = False
        if self.ear:
            self.ear.stop_listening()
        self.voice.stop()
        self.eye.cleanup()
        self.robot.cleanup()
        for t in self._threads:
            t.join(timeout=3)
        self._threads.clear()
        print("✅ MIA desactivada\n")

    def run_interactive(self):
        """Modo consola: escribe lo que dirías a MIA."""
        self.start()
        try:
            while self.is_running:
                cmd = input("\n📋 Escribe algo para MIA ('salir' para terminar)\n> ").strip()
                if cmd.lower() == "salir":
                    self.stop()
                    break
                if cmd.lower() == "estado":
                    print(f"  🧠 Ollama: {'OK' if self.brain.is_connected() else 'SIN CONEXIÓN'}")
                    print(f"  🎤 Micrófono local: {'sí' if self.ear else 'no'}")
                    print(f"  🍹 Menú: {', '.join(d['nombre'] for d in self.brain.get_menu())}")
                elif cmd:
                    self.submit_text(cmd)
        except KeyboardInterrupt:
            self.stop()
