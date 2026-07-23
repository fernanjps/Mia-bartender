import time
import threading
from flask import Flask, request, jsonify
import RPi.GPIO as GPIO

app = Flask(__name__)

# ==========================================
# CONFIGURACIÓN DE PINES GPIO
# ==========================================
# Motor DC (L298N)
# Conectados según lo indicado por el usuario
PIN_IN1 = 16
PIN_IN2 = 20
PIN_ENA = 21

# Relés de las Bombas (Ajusta según tu conexión)
# Usa los pines reales a los que conectaste cada relé
PUMPS = {
    "pump_1": 19,
    "pump_2": 6,
    "pump_3": 13,
    "pump_4": 5
}

# Configuración del motor DC
VELOCIDAD = 80        # 0-100 (duty cycle en %)
PWM_FREQ = 1000       # Frecuencia del PWM en Hz
SEGUNDOS_POR_CM = 0.001 # Multiplicador para convertir los Milisegundos a Segundos
pwm_motor = None

# ==========================================
# ESTADO GLOBAL
# ==========================================
is_busy = False      # Para evitar que sirva dos bebidas al mismo tiempo
current_position = 0 # Posición actual en CM

# Inicialización de GPIO
def setup_gpio():
    global pwm_motor
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    
    # Motor DC
    GPIO.setup(PIN_IN1, GPIO.OUT)
    GPIO.setup(PIN_IN2, GPIO.OUT)
    GPIO.setup(PIN_ENA, GPIO.OUT)
    
    # Iniciar PWM
    pwm_motor = GPIO.PWM(PIN_ENA, PWM_FREQ)
    pwm_motor.start(0)
    
    # Bombas
    for pin in PUMPS.values():
        GPIO.setup(pin, GPIO.OUT)
        GPIO.output(pin, GPIO.HIGH) # Asumiendo relés activos en LOW (ajustar si es necesario)

def detener_motor():
    GPIO.output(PIN_IN1, GPIO.LOW)
    GPIO.output(PIN_IN2, GPIO.LOW)
    pwm_motor.ChangeDutyCycle(0)

def mover_motor(target_cm):
    global current_position
    if target_cm == current_position:
        return
        
    distance = target_cm - current_position
    tiempo_movimiento = abs(distance) * SEGUNDOS_POR_CM
    
    print(f"⚙️ [MOTOR] Moviendo a {target_cm}cm (Tiempo estimado: {tiempo_movimiento:.2f}s)...")
    
    # Dirección
    if distance > 0:
        # Adelante (invertido físicamente)
        GPIO.output(PIN_IN1, GPIO.LOW)
        GPIO.output(PIN_IN2, GPIO.HIGH)
    else:
        # Atrás (invertido físicamente)
        GPIO.output(PIN_IN1, GPIO.HIGH)
        GPIO.output(PIN_IN2, GPIO.LOW)
        
    # Arrancar motor usando PWM
    pwm_motor.ChangeDutyCycle(VELOCIDAD)
    
    # Esperar el tiempo calculado
    time.sleep(tiempo_movimiento)
    
    # Detener motor
    detener_motor()
    
    current_position = target_cm
    print(f"⚙️ [MOTOR] Llegó a la posición calculada para {target_cm}cm.")

<<<<<<< HEAD
def servir_bebida_hilo_pasos(steps):
    global is_busy
    try:
        print(f"🍹 [SECUENCIA] Iniciando cóctel multibomba con {len(steps)} pasos...")
        for idx, step in enumerate(steps, 1):
            pump_key = step.get('pump')
            amount_ml = step.get('amount_ml', 30)
            target_cm = step.get('cm', 0)
            
            print(f"📌 [Paso {idx}/{len(steps)}] Moviendo a {target_cm}cm para bomba {pump_key}...")
            # 1. Mover el vaso a la posición de este ingrediente
            mover_motor(target_cm)
            time.sleep(0.5) # Pausa para estabilizar vaso
            
            # 2. Encender bomba para este ingrediente
            pump_pin = PUMPS.get(pump_key)
            if pump_pin:
                print(f"💧 [BOMBA] Encendiendo {pump_key} para servir {amount_ml}mL...")
                tiempo_servido = amount_ml / 1.5 
                GPIO.output(pump_pin, GPIO.LOW) # RELÉ ON
                time.sleep(tiempo_servido)
                GPIO.output(pump_pin, GPIO.HIGH) # RELÉ OFF
                print(f"💧 [BOMBA] {pump_key} servido con éxito.")
            
            time.sleep(0.5)
            
        # 3. Al terminar todos los ingredientes, regresar a la posición 0 (Home)
        print("⚙️ [MOTOR] Mezcla finalizada. Regresando a la posición inicial (0cm)...")
        mover_motor(0)
        
    except Exception as e:
        print(f"❌ [ERROR] Falló la secuencia de mezcla: {e}")
    finally:
        is_busy = False
        print("✅ [FIN] Máquina lista para la siguiente bebida.")

=======
>>>>>>> 177b6e5c8a87771b162a202845ee5d0a516403c6
def servir_bebida_hilo(pump_key, amount_ml, target_cm):
    global is_busy
    try:
        # 1. Mover el vaso a la posición
        mover_motor(target_cm)
        time.sleep(0.5) # Pausa para estabilizar
        
        # 2. Encender bomba
        pump_pin = PUMPS.get(pump_key)
        if pump_pin:
            print(f"💧 [BOMBA] Encendiendo {pump_key} para servir {amount_ml}mL...")
            # Cálculo de tiempo (Bombas peristálticas pequeñas: ~1.5mL por segundo)
<<<<<<< HEAD
=======
            # Para servir 150mL, la bomba estará encendida ~100 segundos
>>>>>>> 177b6e5c8a87771b162a202845ee5d0a516403c6
            tiempo_servido = amount_ml / 1.5 
            
            GPIO.output(pump_pin, GPIO.LOW) # RELÉ ON
            time.sleep(tiempo_servido)
            GPIO.output(pump_pin, GPIO.HIGH) # RELÉ OFF
            print(f"💧 [BOMBA] Apagada. Servido exitoso.")
            
        time.sleep(1) # Pausa antes de regresar para evitar goteo en el camino
        
        # 3. Regresar al origen (Posición 0)
        mover_motor(0)
        
    except Exception as e:
        print(f"❌ [ERROR] Falló la secuencia: {e}")
    finally:
        # Liberar la máquina
        is_busy = False
        print("✅ [FIN] Máquina lista para la siguiente bebida.")

# ==========================================
# RUTAS DEL SERVIDOR
# ==========================================
@app.route('/status', methods=['GET'])
def status():
    return jsonify({"status": "ready", "busy": is_busy}), 200

@app.route('/prepare', methods=['POST'])
def prepare_drink():
    global is_busy
    if is_busy:
        return jsonify({"error": "La máquina está ocupada preparando otra bebida"}), 409
        
    data = request.get_json()
<<<<<<< HEAD
    steps = data.get('steps')
    
    # MODO 1: Preparar mezcla secuencial multibomba
    if steps and isinstance(steps, list):
        is_busy = True
        hilo = threading.Thread(target=servir_bebida_hilo_pasos, args=(steps,))
        hilo.start()
        return jsonify({
            "status": "processing",
            "message": f"Iniciando preparación de mezcla con {len(steps)} ingredientes..."
        }), 200

    # MODO 2: Servir una sola bomba (Compatibilidad anterior)
=======
>>>>>>> 177b6e5c8a87771b162a202845ee5d0a516403c6
    pump_key = data.get('pump')
    amount_ml = data.get('amount_ml', 100)
    target_cm = data.get('cm', 0)
    
    if not pump_key or pump_key not in PUMPS:
<<<<<<< HEAD
        return jsonify({"error": "Bomba o pasos no válidos"}), 400
=======
        return jsonify({"error": "Bomba no válida"}), 400
>>>>>>> 177b6e5c8a87771b162a202845ee5d0a516403c6
        
    # Bloquear la máquina
    is_busy = True
    
    # Lanzar hilo en segundo plano (No bloquea la petición web)
    hilo = threading.Thread(target=servir_bebida_hilo, args=(pump_key, amount_ml, target_cm))
    hilo.start()
    
    # Responder INMEDIATAMENTE a MIA (Laptop)
    return jsonify({
        "status": "processing",
        "message": f"Preparando {amount_ml}mL en la bomba {pump_key} a {target_cm}cm..."
    }), 200

if __name__ == '__main__':
    print("🍹 Iniciando Servidor Bartender en Raspberry Pi (MODO HILOS) 🍹")
    setup_gpio()
    try:
        app.run(host='0.0.0.0', port=5001)
    except KeyboardInterrupt:
        print("Apagando servidor...")
    finally:
        GPIO.cleanup()
