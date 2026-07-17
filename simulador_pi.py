import time
import threading
from flask import Flask, request, jsonify

app = Flask(__name__)

is_busy = False

def simular_hardware(pump_key, amount_ml, target_cm):
    global is_busy
    try:
        # Asumiendo velocidad del motor lineal: 2 cm por segundo
        tiempo_viaje = max(1, target_cm / 2.0)
        
        # Asumiendo una bomba de 12V genérica que sirve 5mL por segundo
        tiempo_bomba = amount_ml / 5.0

        print(f"\n[⚙️ MOTOR] Moviendo vaso a {target_cm}cm (Tomará {tiempo_viaje}s)...")
        time.sleep(tiempo_viaje)
        
        print(f"[💧 BOMBA] Encendiendo bomba {pump_key} para servir {amount_ml}mL (Tomará {tiempo_bomba}s)...")
        time.sleep(tiempo_bomba)
        print(f"[💧 BOMBA] Apagada. Servido exitoso.")
        
        print(f"[⚙️ MOTOR] Regresando vaso a 0cm (Tomará {tiempo_viaje}s)...")
        time.sleep(tiempo_viaje)
        
    except Exception as e:
        print(f"[!] Error simulando hardware: {e}")
    finally:
        is_busy = False
        print("[✅ ÉXITO] ¡Bebida preparada virtualmente! Máquina libre.\n")

@app.route('/status', methods=['GET'])
def status():
    return jsonify({"status": "ready", "busy": is_busy}), 200

@app.route('/prepare', methods=['POST'])
def prepare():
    global is_busy
    if is_busy:
        return jsonify({"error": "Ocupado"}), 409
        
    data = request.get_json()
    pump_key = data.get('pump')
    amount_ml = data.get('amount_ml', 100)
    target_cm = data.get('cm', 0)
    
    is_busy = True
    print(f"\n==================================================")
    print(f"🍹 ORDEN RECIBIDA: Preparar {amount_ml}mL en {pump_key} a {target_cm}cm")
    print(f"==================================================")
    
    hilo = threading.Thread(target=simular_hardware, args=(pump_key, amount_ml, target_cm))
    hilo.start()
    
    return jsonify({"status": "processing"}), 200

if __name__ == "__main__":
    print("==================================================")
    print("🍹 INICIANDO SIMULADOR VIRTUAL (HTTP FLASK) 🍹")
    print("Escuchando en http://127.0.0.1:8888...")
    print("==================================================")
    # IMPORTANTE: Desactivamos los logs de werkzeug para no ensuciar la consola
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='127.0.0.1', port=8888)