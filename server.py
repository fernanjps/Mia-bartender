from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import hmac
import ipaddress
import threading
import time
import sys
import re
from assistant import VoiceAssistant
from config import API_TOKEN, ALLOWED_ORIGINS, ALLOW_TAILSCALE_WITHOUT_TOKEN, LOCAL_MODEL

try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass

app = Flask(__name__)
_cors_origins = ALLOWED_ORIGINS if ALLOWED_ORIGINS else None
socketio = SocketIO(app, cors_allowed_origins=_cors_origins, async_mode='threading')

mia = None
_clients = set()

WAKE_PREFIX = re.compile(r'^\s*(hey|ey|oye|hola|m)?\s*m[ií]a\b[\s,.:]*', re.IGNORECASE)


def start_mia_backend():
    global mia
    mia = VoiceAssistant(socketio)

    def on_mia_state_change(new_state, data=None):
        if new_state == "audio_payload_chunk":
            socketio.emit('play_audio_chunk', {'text': data['text'], 'audio_b64': data['audio']})
            return
        # El frontend espera SIEMPRE un string en 'text'
        if isinstance(data, dict):
            data = data.get("text", "")
        socketio.emit('state_update', {"state": new_state, "text": str(data) if data else ""})

    mia.on_state_change = on_mia_state_change
    mia.clients_connected = len(_clients)
    mia.start()


def _submit(text, origin):
    """Limpia el wake word, interrumpe audio pendiente y encola el comando."""
    if not mia:
        return False
    text = WAKE_PREFIX.sub('', (text or '').strip()).strip()
    print(f"{origin}: {text or '(solo wake word)'}")
    mia.voice.clear_queue()
    socketio.emit('stop_audio')
    if not text:
        mia.set_state("listening")
        mia.voice.speak_async("¿Sí? Dime qué te preparo.")
        return True
    mia.submit_text(text)
    return True


_LOOPBACK_ADDRS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
_TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")


def _is_loopback(addr):
    return (addr or "").strip() in _LOOPBACK_ADDRS


def _is_tailscale(addr):
    raw = (addr or "").strip()
    if raw.startswith("::ffff:"):
        raw = raw[7:]
    try:
        ip = ipaddress.ip_address(raw)
        return ip.version == 4 and ip in _TAILSCALE_NET
    except ValueError:
        return False


def _is_trusted_origin_ip(addr):
    return _is_loopback(addr) or (ALLOW_TAILSCALE_WITHOUT_TOKEN and _is_tailscale(addr))


def _extract_http_token():
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return (request.headers.get("X-API-Token") or "").strip()


def _check_http_auth():
    """Valida token (si API_TOKEN está configurado) o restringe a loopback / red Tailscale."""
    if API_TOKEN:
        token = _extract_http_token()
        if not token or not hmac.compare_digest(token, API_TOKEN):
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
        return None
    if not _is_trusted_origin_ip(request.remote_addr):
        return jsonify({
            "status": "error",
            "message": "Acceso remoto bloqueado: usa Tailscale (100.64.0.0/10) o configura MIA_API_TOKEN"
        }), 403
    return None


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/command', methods=['POST'])
def handle_api_command():
    auth_err = _check_http_auth()
    if auth_err:
        return auth_err
    data = request.get_json(silent=True) or {}
    if _submit(data.get("text", ""), "📱 API"):
        return jsonify({"status": "success"}), 200
    return jsonify({"status": "error", "message": "MIA offline"}), 400


@app.route('/api/reset_session', methods=['POST'])
def handle_api_reset_session():
    auth_err = _check_http_auth()
    if auth_err:
        return auth_err
    if not mia:
        return jsonify({"status": "error", "message": "MIA offline"}), 400
    mia.brain.reset_session()
    return jsonify({"status": "success"}), 200


# ------------------------------------------------------------------
# Endpoints IoT para App Móvil, ESP32 y Arduino (vía Tailscale / HTTP)
# ------------------------------------------------------------------

@app.route('/api/iot/status', methods=['GET'])
def handle_iot_status():
    auth_err = _check_http_auth()
    if auth_err:
        return auth_err
    if not mia:
        return jsonify({"status": "offline", "model": LOCAL_MODEL}), 200
    return jsonify({
        "status": "online",
        "state": mia.state,
        "model": mia.brain.local_model,
        "robot_busy": mia.robot.is_busy,
        "current_drink": mia.robot.current_drink,
        "current_position_cm": mia.robot.current_position,
        "hardware_active": mia.robot.hardware_active,
        "cup_present": mia.robot.cup_present,
        "last_sensor_distance_cm": mia.robot.last_sensor_distance_cm,
        "connected_clients": len(_clients),
    }), 200


@app.route('/api/iot/menu', methods=['GET'])
def handle_iot_menu():
    auth_err = _check_http_auth()
    if auth_err:
        return auth_err
    if not mia:
        return jsonify({"status": "error", "message": "MIA offline"}), 400
    inv = mia.brain._load_inventory() or {}
    return jsonify({
        "status": "success",
        "drinks": mia.brain.get_menu(only_available=False),
        "pumps": inv.get("bombas", {}),
    }), 200


@app.route('/api/iot/order', methods=['POST'])
def handle_iot_order():
    """Pedido directo estructurado desde App Móvil o botonera ESP32/Arduino."""
    auth_err = _check_http_auth()
    if auth_err:
        return auth_err
    if not mia:
        return jsonify({"status": "error", "message": "MIA offline"}), 400

    data = request.get_json(silent=True) or {}
    drink_id = data.get("id") or data.get("coctel") or data.get("number")
    drink_name = (data.get("drink") or data.get("trago") or "").strip()
    if not drink_name and drink_id is not None:
        drink_name = f"coctel {drink_id}"
    if not drink_name:
        return jsonify({"status": "error", "message": "Falta el campo 'drink' o 'id'"}), 400

    if mia.robot.is_busy:
        return jsonify({
            "status": "busy",
            "message": f"Robot ocupado preparando {mia.robot.current_drink or 'otra bebida'}"
        }), 409

    matched = mia.brain._match_drink(drink_name.lower())
    if not matched:
        return jsonify({"status": "error", "message": f"Bebida '{drink_name}' no existe en el catálogo"}), 404
    if not matched.get("available", False):
        return jsonify({
            "status": "out_of_stock",
            "drink": matched["nombre"],
            "missing": matched.get("missing", []),
        }), 400

    _submit(f"prepara una {matched['nombre']}", "📲 IoT-Order")
    return jsonify({
        "status": "accepted",
        "drink": matched["nombre"],
        "steps": matched["steps"],
    }), 200


@app.route('/api/iot/telemetry', methods=['POST'])
def handle_iot_telemetry():
    """Recibe telemetría desde ESP32 / Arduino (sensor de vaso ultrasónico/IR, parada de emergencia)."""
    auth_err = _check_http_auth()
    if auth_err:
        return auth_err
    if not mia:
        return jsonify({"status": "error", "message": "MIA offline"}), 400

    data = request.get_json(silent=True) or {}
    if data.get("emergency") or data.get("emergencia"):
        mia.robot.emergency_stop()
        mia.voice.clear_queue()
        socketio.emit('stop_audio')
        mia.set_state("idle", "Parada de emergencia activada")
        return jsonify({"status": "emergency_stopped"}), 200

    if "cup_present" in data or "vaso" in data:
        mia.robot.cup_present = bool(data.get("cup_present", data.get("vaso")))
    if "distance_cm" in data:
        try:
            mia.robot.last_sensor_distance_cm = float(data["distance_cm"])
        except (TypeError, ValueError):
            pass

    socketio.emit('iot_telemetry', {
        "cup_present": mia.robot.cup_present,
        "distance_cm": mia.robot.last_sensor_distance_cm,
    })
    return jsonify({
        "status": "success",
        "cup_present": mia.robot.cup_present,
        "distance_cm": mia.robot.last_sensor_distance_cm,
    }), 200


@socketio.on('connect')
def handle_connect(auth=None):
    if API_TOKEN and not _is_loopback(request.remote_addr):
        token = ""
        if isinstance(auth, dict):
            token = str(auth.get("token") or "").strip()
        if not token:
            token = (request.args.get("token") or "").strip()
        if not token or not hmac.compare_digest(token, API_TOKEN):
            print(f"🔒 Conexión Socket.IO rechazada desde {request.remote_addr}")
            return False

    _clients.add(request.sid)
    if mia:
        mia.clients_connected = len(_clients)
        socketio.emit('state_update', {'state': mia.state, 'text': ''})
        socketio.emit('menu_update', {'drinks': mia.brain.get_menu(only_available=False)})
    print(f"🔗 Navegador conectado ({len(_clients)} activos)")


@socketio.on('reset_session')
def handle_reset_session():
    if mia:
        mia.brain.reset_session()
        socketio.emit('state_update', {'state': mia.state, 'text': ''})


@socketio.on('disconnect')
def handle_disconnect():
    _clients.discard(request.sid)
    if mia:
        mia.clients_connected = len(_clients)
        mia.audio_playback_done.set()  # no dejar a MIA esperando a un navegador que se fue


@socketio.on('audio_finished')
def handle_audio_finished():
    if mia:
        mia.audio_playback_done.set()


@socketio.on('request_state')
def handle_request_state():
    socketio.emit('state_update', {'state': mia.state if mia else 'idle', 'text': ''})
    if mia:
        socketio.emit('menu_update', {'drinks': mia.brain.get_menu(only_available=False)})


@socketio.on('user_command')
def handle_text_command(data):
    """Texto desde la web: caja de texto o botón 🎤 (reconocimiento de voz del navegador)."""
    if isinstance(data, dict):
        _submit(data.get("text", ""), "🎧 Web")


@socketio.on('request_vision')
def handle_request_vision(data=None):
    if not mia:
        return
    mia.latest_frontend_image = data.get("image") if isinstance(data, dict) else None
    mia.submit_text("mira esto y dime qué ves")


if __name__ == '__main__':
    threading.Thread(target=start_mia_backend, daemon=True).start()
    time.sleep(2)
    print("\n" + "=" * 50)
    print("🌐 SERVIDOR MIA INICIADO")
    print("👉 En la Pi: http://localhost:5000   |   Desde la laptop: http://<IP_DE_LA_PI>:5000")
    print("=" * 50 + "\n")
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
