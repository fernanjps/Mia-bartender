"""
MIA - Gestor de Memoria por Sesión (RAM) + Estadísticas Anónimas (JSON)

Diseño (C6):
- Datos personales del cliente actual (nombre, alérgenos, conversaciones, hechos, último pedido)
  viven EXCLUSIVAMENTE en RAM (`self._session`) y expiran tras `SESSION_TIMEOUT_SECONDS`
  de inactividad (o al llamar `reset_session()`). Nunca se heredan entre clientes ni tras reiniciar.
- En disco (`bar_stats.json`) solo se persisten métricas agregadas anónimas (conteo por bebida).
"""

import os
import sys
import json
import time
import threading

try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass

from config import MEMORY_DIR, MEMORY_RESULTS_LIMIT, SESSION_TIMEOUT_SECONDS

STATS_FILE = os.path.join(MEMORY_DIR, "bar_stats.json")
PROFILE_FILE = STATS_FILE  # Alias por compatibilidad


class Memory:
    """Memoria efímera por sesión de cliente en RAM y estadísticas anónimas en disco."""

    def __init__(self, filepath=STATS_FILE, session_timeout=SESSION_TIMEOUT_SECONDS):
        self.filepath = filepath
        self.session_timeout = session_timeout
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self.filepath) or ".", exist_ok=True)

        self._session = self._fresh_session()
        self.stats_data = {
            "total_orders": 0,
            "drink_counts": {},
            "recent_orders": []
        }
        self._load_stats()
        print(f"🧬 [MEMORY] Memoria por sesión en RAM activa (timeout={self.session_timeout}s); stats en: {self.filepath}")

    @staticmethod
    def _fresh_session():
        return {
            "user_name": None,
            "favorite_drink": None,
            "last_order": None,
            "allergens": [],
            "facts": [],
            "conversations": [],
            "last_activity": time.time(),
        }

    def _load_stats(self):
        with self._lock:
            if os.path.exists(self.filepath):
                try:
                    with open(self.filepath, "r", encoding="utf-8") as f:
                        saved = json.load(f)
                        if isinstance(saved, dict):
                            self.stats_data["total_orders"] = int(saved.get("total_orders", 0))
                            if isinstance(saved.get("drink_counts"), dict):
                                self.stats_data["drink_counts"] = dict(saved["drink_counts"])
                            if isinstance(saved.get("recent_orders"), list):
                                self.stats_data["recent_orders"] = list(saved["recent_orders"])
                except Exception as e:
                    print(f"⚠️ [MEMORY] Error leyendo {self.filepath}: {e}")

    def _save_stats(self):
        temp_path = f"{self.filepath}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(self.stats_data, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.filepath)
        except Exception as e:
            print(f"⚠️ [MEMORY] Error guardando estadísticas en disco: {e}")
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Control de Sesión (RAM)
    # ------------------------------------------------------------------

    def _check_session_expiry_locked(self):
        if self.session_timeout > 0:
            elapsed = time.time() - self._session.get("last_activity", 0)
            if elapsed > self.session_timeout:
                self._session = self._fresh_session()
                print("🧹 [MEMORY] Sesión anterior expirada por inactividad; memoria de cliente limpia.")
                return True
        return False

    def touch_session(self):
        """Verifica si la sesión expiró y actualiza el timestamp de última actividad.
        Retorna True si la sesión acababa de expirar y fue reiniciada."""
        with self._lock:
            expired = self._check_session_expiry_locked()
            self._session["last_activity"] = time.time()
            return expired

    def reset_session(self):
        """Reinicia manualmente la sesión del cliente actual (mantiene stats anónimas)."""
        with self._lock:
            self._session = self._fresh_session()
        print("🧹 [MEMORY] Sesión de cliente reiniciada manualmente.")

    # ------------------------------------------------------------------
    # Alérgenos de la Sesión
    # ------------------------------------------------------------------

    def add_session_allergens(self, allergens):
        if not allergens:
            return
        with self._lock:
            self._check_session_expiry_locked()
            current = self._session["allergens"]
            for a in allergens:
                if a and a not in current:
                    current.append(a)
            self._session["last_activity"] = time.time()

    def get_session_allergens(self):
        with self._lock:
            self._check_session_expiry_locked()
            return list(self._session["allergens"])

    # ------------------------------------------------------------------
    # Perfil del Cliente de la Sesión Actual (RAM)
    # ------------------------------------------------------------------

    def get_user_name(self):
        with self._lock:
            self._check_session_expiry_locked()
            return self._session.get("user_name")

    def set_user_name(self, name):
        if not name or not isinstance(name, str):
            return
        clean_name = name.strip().capitalize()
        with self._lock:
            self._check_session_expiry_locked()
            self._session["user_name"] = clean_name
            self._session["last_activity"] = time.time()
        print(f"🧬 [MEMORY] Nombre guardado en sesión actual: {clean_name}")

    def get_favorite_drink(self):
        with self._lock:
            self._check_session_expiry_locked()
            return self._session.get("favorite_drink")

    def set_favorite_drink(self, drink):
        if not drink:
            return
        with self._lock:
            self._check_session_expiry_locked()
            self._session["favorite_drink"] = drink.strip()
            self._session["last_activity"] = time.time()

    def record_order(self, drink_name):
        if not drink_name:
            return
        with self._lock:
            self._check_session_expiry_locked()
            self._session["last_order"] = drink_name
            self._session["last_activity"] = time.time()

            # Métricas anónimas en disco
            self.stats_data["total_orders"] = int(self.stats_data.get("total_orders", 0)) + 1
            counts = self.stats_data.setdefault("drink_counts", {})
            counts[drink_name] = int(counts.get(drink_name, 0)) + 1
            recent = self.stats_data.setdefault("recent_orders", [])
            recent.append({"drink": drink_name, "timestamp": time.time()})
            self.stats_data["recent_orders"] = recent[-50:]
            self._save_stats()

    def get_last_order(self):
        with self._lock:
            self._check_session_expiry_locked()
            return self._session.get("last_order")

    def get_profile_summary(self):
        with self._lock:
            self._check_session_expiry_locked()
            parts = []
            if self._session.get("user_name"):
                parts.append(f"El cliente actual se llama {self._session['user_name']}.")
            if self._session.get("allergens"):
                parts.append(f"Alergias declaradas en esta sesión: {', '.join(self._session['allergens'])}.")
            if self._session.get("favorite_drink"):
                parts.append(f"Su bebida favorita es {self._session['favorite_drink']}.")
            if self._session.get("last_order"):
                parts.append(f"Su último pedido en esta sesión fue {self._session['last_order']}.")
            return " ".join(parts)

    # ------------------------------------------------------------------
    # Conversaciones y Hechos de la Sesión (RAM)
    # ------------------------------------------------------------------

    def store_conversation(self, user_message, mia_response, visual_context=""):
        with self._lock:
            self._check_session_expiry_locked()
            convs = self._session["conversations"]
            convs.append({
                "user_message": (user_message or "")[:200],
                "mia_response": (mia_response or "")[:200],
                "timestamp": time.time()
            })
            self._session["conversations"] = convs[-20:]
            self._session["last_activity"] = time.time()

    def recall_conversations(self, query, n_results=None):
        if n_results is None:
            n_results = MEMORY_RESULTS_LIMIT
        with self._lock:
            self._check_session_expiry_locked()
            convs = self._session["conversations"]
            if not convs:
                return []
            q_words = set((query or "").lower().split())
            scored = []
            for c in convs:
                msg_words = set(c["user_message"].lower().split())
                score = len(q_words & msg_words)
                scored.append((score, c))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [c for score, c in scored[:n_results] if score > 0 or len(scored) == 1]

    def store_knowledge(self, fact, category="general"):
        if not fact:
            return
        with self._lock:
            self._check_session_expiry_locked()
            facts = self._session["facts"]
            facts.append({
                "fact": fact[:200],
                "category": category,
                "timestamp": time.time()
            })
            self._session["facts"] = facts[-30:]
            self._session["last_activity"] = time.time()
        print(f"🧬 [MEMORY] Hecho en sesión [{category}]: {fact[:60]}")

    def recall_knowledge(self, query, n_results=None):
        if n_results is None:
            n_results = MEMORY_RESULTS_LIMIT
        with self._lock:
            self._check_session_expiry_locked()
            facts = self._session["facts"]
            if not facts:
                return []
            q_lower = (query or "").lower()
            matched = [f for f in facts if any(w in f["fact"].lower() for w in q_lower.split() if len(w) > 3)]
            if not matched:
                matched = facts[-n_results:]
            return matched[:n_results]

    def get_total_orders(self):
        with self._lock:
            return int(self.stats_data.get("total_orders", 0))

    def get_most_popular_drink(self):
        """Retorna (drink_name, count) del cóctel más pedido o (None, 0) si aún no hay pedidos."""
        with self._lock:
            counts = self.stats_data.get("drink_counts", {})
            if not counts:
                return None, 0
            top_drink = max(counts.items(), key=lambda kv: kv[1])
            return top_drink[0], int(top_drink[1])

    def get_stats(self):
        with self._lock:
            self._check_session_expiry_locked()
            return {
                "user_name": self._session.get("user_name"),
                "allergens": list(self._session.get("allergens", [])),
                "conversations": len(self._session["conversations"]),
                "knowledge": len(self._session["facts"]),
                "orders": int(self.stats_data.get("total_orders", 0)),
                "drink_counts": dict(self.stats_data.get("drink_counts", {})),
            }

    def clear(self):
        with self._lock:
            self._session = self._fresh_session()
            self.stats_data = {
                "total_orders": 0,
                "drink_counts": {},
                "recent_orders": []
            }
            self._save_stats()
