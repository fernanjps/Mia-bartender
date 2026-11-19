import threading
import asyncio
import edge_tts
import base64
import os
import re
import shutil
import subprocess
import tempfile
from queue import Queue
from config import EDGE_TTS_TIMEOUT

ENGLISH_WORDS = {
    "the", "is", "are", "you", "your", "i", "im", "i'm", "my", "we", "they",
    "it", "this", "that", "what", "who", "where", "can", "could", "would",
    "like", "want", "please", "sure", "preparing", "here", "drink", "cocktail",
    "today", "ready", "name", "nice", "meet", "how", "have", "with", "for",
    "and", "hello", "hi", "hey", "welcome", "serve", "order", "right", "now"
}

SPANISH_WORDS = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "en",
    "que", "para", "por", "con", "te", "me", "se", "mi", "tu", "su", "es",
    "son", "estoy", "esta", "llamas", "llamo", "nombre", "gusto", "hola",
    "preparo", "preparando", "bebida", "trago", "coctel", "menu", "carta",
    "hoy", "puedo", "aqui", "gracias", "listo", "recuerdo", "favor"
}

def is_english_text(text):
    """Detecta si un texto está en inglés basándose en palabras clave."""
    if not text:
        return False
    words = re.findall(r"\b[a-zA-Z']+\b", text.lower())
    if not words:
        return False
    en_count = sum(1 for w in words if w in ENGLISH_WORDS)
    es_count = sum(1 for w in words if w in SPANISH_WORDS)
    return en_count > es_count


class Voice:
    """Síntesis de voz usando Edge-TTS — Genera MP3 y emite a la UI (Bilingüe ES/EN)"""

    def __init__(self):
        self._speech_queue = Queue()
        self.is_speaking = False
        self._running = True
        
        # Callback para emitir el audio Base64 hacia el navegador
        self.on_audio_ready = None

        self._worker = threading.Thread(
            target=self._process_queue, daemon=True, name="VoiceWorker"
        )
        self._worker.start()

    def _synthesize_offline_wav(self, text, is_en=False):
        """Fallback offline usando espeak-ng / espeak si no hay internet en Edge-TTS."""
        offline_bin = shutil.which("espeak-ng") or shutil.which("espeak")
        if not offline_bin:
            return b""
        tmp_wav = None
        try:
            fd, tmp_wav = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            voice_lang = "en-us" if is_en else "es-la"
            subprocess.run(
                [offline_bin, "-v", voice_lang, "-s", "150", "-w", tmp_wav, text],
                check=True,
                timeout=5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            with open(tmp_wav, "rb") as f:
                return f.read()
        except Exception as e:
            print(f"⚠️ Error en TTS offline ({offline_bin}): {e}")
            return b""
        finally:
            if tmp_wav and os.path.exists(tmp_wav):
                try:
                    os.remove(tmp_wav)
                except Exception:
                    pass

    def _speak_sync(self, text):
        """Genera audio con Edge-TTS (o fallback offline espeak-ng / speechSynthesis) y emite a la UI."""
        if not text or not text.strip():
            return

        self.is_speaking = True
        is_en = is_english_text(text)
        voice_name = "en-US-JennyNeural" if is_en else "es-MX-DaliaNeural"
        print(f"🔊 MIA dice [{voice_name}]: {text}")

        audio_bytes = b""
        try:
            communicate = edge_tts.Communicate(text, voice_name)

            async def get_bytes():
                nonlocal audio_bytes
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_bytes += chunk["data"]

            asyncio.run(asyncio.wait_for(get_bytes(), timeout=EDGE_TTS_TIMEOUT))
        except Exception as e:
            print(f"⚠️ Edge-TTS sin conexión o lento ({e}). Activando respaldo offline...")
            audio_bytes = b""

        try:
            if not audio_bytes:
                audio_bytes = self._synthesize_offline_wav(text, is_en=is_en)

            if audio_bytes:
                audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
                if self.on_audio_ready:
                    self.on_audio_ready(text, audio_b64)
            else:
                # Emitir texto sin audio para que el navegador use window.speechSynthesis offline
                if self.on_audio_ready:
                    self.on_audio_ready(text, "")
        finally:
            self.is_speaking = False

    def speak_async(self, text):
        """Encola el texto para ser procesado sin bloquear"""
        self._speech_queue.put(text)
        
    def clear_queue(self):
        """Vacía la cola de voz para interrupciones"""
        with self._speech_queue.mutex:
            self._speech_queue.queue.clear()


    def wait_until_done(self):
        """Bloquea hasta que todos los elementos en la cola hayan sido procesados por el worker"""
        self._speech_queue.join()

    def _process_queue(self):
        """Thread eterno que procesa la cola"""
        while self._running:
            try:
                text = self._speech_queue.get(timeout=1)
            except Exception:
                continue

            if text is None:
                break

            self._speak_sync(text)
            self._speech_queue.task_done()

    def stop(self):
        """Detiene el worker"""
        self._running = False
        self._speech_queue.put(None)