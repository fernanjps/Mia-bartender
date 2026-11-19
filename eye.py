import cv2
import base64
import numpy as np
import time
import sys
import ollama
from config import (
    CAMERA_INDEX,
    CHANGE_THRESHOLD, CAMERA_WARMUP,
    OLLAMA_LOCAL_URL, OLLAMA_KEEP_ALIVE, OLLAMA_NUM_THREADS
)


class Eye:
    """Responsable de captura de imágenes y análisis visual via cámara USB-C + Ollama local (RPi 5).

    Modo on-demand: la cámara se abre solo cuando se necesita, descarta frames iniciales
    de estabilización (AGC/AWB para mini sensores USB-C) y se cierra después.
    """

    def __init__(self, vision_model: str = "moondream"):
        self.vision_model = vision_model
        self.ollama_client = ollama.Client(host=OLLAMA_LOCAL_URL)

        # Frame anterior para detección de cambios
        self._previous_frame_gray = None

        # Cámara on-demand (NO se abre al iniciar)
        self._cap = None
        print(f"📷 Cámara USB en modo on-demand con Ollama local ({self.vision_model})")

    # ------------------------------------------------------------------
    # Cámara on-demand (Optimizada para Mini Cámara Espía USB-C en RPi 5)
    # ------------------------------------------------------------------

    def _open_camera(self):
        """Abre cámara UVC USB-C en Linux (V4L2) o Windows, con fallback de índice en RPi 5."""
        if not str(CAMERA_INDEX).isdigit():
            return False
        if self._cap is not None and self._cap.isOpened():
            return True

        primary_idx = int(CAMERA_INDEX)
        candidates = [primary_idx] + [i for i in (0, 1, 2) if i != primary_idx]
        is_linux = sys.platform.startswith("linux")

        for idx in candidates:
            try:
                cap = cv2.VideoCapture(idx, cv2.CAP_V4L2) if is_linux else cv2.VideoCapture(idx)
                if not cap or not cap.isOpened():
                    if cap:
                        cap.release()
                    continue

                # Resolución ligera 640x480 para mini cámara espía USB-C (baja latencia y nitidez en Moondream)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

                if is_linux:
                    # En V4L2 (Linux / RPi 5): 3 = Auto Exposure; NUNCA usar -5 (valor de Windows DirectShow)
                    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)
                else:
                    try:
                        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75)
                    except Exception:
                        pass

                time.sleep(CAMERA_WARMUP)
                # Descartar primeros 5 frames para estabilizar ganancia (AGC) y balance de blancos en mini sensor CMOS
                for _ in range(5):
                    cap.grab()

                self._cap = cap
                return True
            except Exception:
                continue
        return False

    def _close_camera(self):
        if self._cap is not None and self._cap.isOpened():
            self._cap.release()
        self._cap = None

    def capture_frame(self):
        """Captura un frame. Para IP Webcam usa /shot.jpg, de lo contrario cámara local"""
        import urllib.request
        url_str = str(CAMERA_INDEX)

        if url_str.startswith("http"):
            cache_busting = f"?t={int(time.time())}"
            if "/video" in url_str:
                shot_url = url_str.replace("/video", "/shot.jpg") + cache_busting
            else:
                shot_url = url_str + cache_busting
                
            try:
                print("📷 Descargando Snapshot HTTP de la cámara...")
                req = urllib.request.urlopen(shot_url, timeout=6.0)
                arr = np.asarray(bytearray(req.read()), dtype=np.uint8)
                frame = cv2.imdecode(arr, -1)
                return frame
            except Exception as e:
                print(f"❌ Error descargando Snapshot Red: {e}")
                return None
        else:
            if not self._open_camera():
                return None
            ret, frame = self._cap.read()
            return frame if ret else None

    def capture_image(self):
        """Captura una imagen, la guarda en disco y la convierte a base64."""
        frame = self.capture_frame()
        if frame is None:
            return None

        max_size = 800
        height, width = frame.shape[:2]
        if max(height, width) > max_size:
            scale = max_size / max(height, width)
            frame = cv2.resize(frame, (int(width * scale), int(height * scale)))

        try:
            cv2.imwrite("temp_vision.jpg", frame)
            print("💾 Foto física guardada como temp_vision.jpg")
        except Exception as e:
            print("Fallo menor al guardar temp_vision.jpg:", e)
            
        _, buffer = cv2.imencode('.jpg', frame)
        image_b64 = base64.b64encode(buffer).decode('utf-8')

        self._close_camera()
        return image_b64

    # ------------------------------------------------------------------
    # Detección de cambios
    # ------------------------------------------------------------------

    def detect_change(self, threshold=None):
        """Detecta si hubo un cambio visual significativo entre el frame actual y el anterior"""
        if threshold is None:
            threshold = CHANGE_THRESHOLD

        frame = self.capture_frame()
        if frame is None:
            return False

        current_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        if self._previous_frame_gray is None:
            self._previous_frame_gray = current_gray
            self._close_camera()
            return False

        diff = cv2.absdiff(self._previous_frame_gray, current_gray)
        change_magnitude = cv2.countNonZero(diff)
        self._previous_frame_gray = current_gray
        self._close_camera()

        return change_magnitude > threshold

    # ------------------------------------------------------------------
    # Análisis visual con Ollama Local (Moondream)
    # ------------------------------------------------------------------

    def describe_image(self, image_base64, prompt="Describe this image briefly in one or two sentences. Mention the main objects, people and any visible brand or text."):
        """Inferencia visual local mediante Ollama en la RPi 5"""
        print("👁️ Analizando imagen localmente con Ollama...")

        if not image_base64:
            return "(No se recibió imagen para analizar)"

        # Asegurar formato base64 plano sin prefijo data:image
        if "," in image_base64:
            image_base64 = image_base64.split(",", 1)[1]

        try:
            response = self.ollama_client.generate(
                model=self.vision_model,
                prompt=prompt,
                images=[image_base64],
                options={"num_thread": OLLAMA_NUM_THREADS},
                keep_alive=OLLAMA_KEEP_ALIVE,
            )
            description = response.get("response", "").strip()
            if not description:
                description = "(El sistema de visión devolvió una respuesta vacía.)"
                
            print(f"👁️ Ollama ve: {description}")
            return description
            
        except Exception as e:
            print(f"❌ Error en inferencia visual local: {e}")
            return "(No se pudo procesar la imagen localmente)"

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self):
        """Libera la cámara de forma segura"""
        self._close_camera()
        print("📷 Cámara liberada")