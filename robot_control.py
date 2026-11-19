"""
MIA - Control de Hardware Embebido (Raspberry Pi 5)
Control directo de Motor DC L298N (Riel) y Bombas Peristálticas mediante gpiozero.
Elimina completamente la arquitectura esclava HTTP y ejecuta secuencias de dispensado [DISPENSE: ...]
de forma nativa y segura en hilos.
"""

import os
import json
import time
import threading
import unicodedata
from config import (
    GPIO_MOTOR_IN1, GPIO_MOTOR_IN2, GPIO_MOTOR_ENA,
    PUMPS_PINS, MOTOR_VELOCIDAD, SEGUNDOS_POR_CM,
    BOMBAS_CONFIG, RECETAS_COCTELES, REQUIRE_CUP_SENSOR,
    GPIO_LIMIT_SWITCH, GPIO_CUP_SENSOR
)

# Intentar importar gpiozero (Bookworm / Raspberry Pi 5)
try:
    from gpiozero import OutputDevice, PWMOutputDevice, Button as GpioButton
    GPIOZERO_AVAILABLE = True
except (ImportError, Exception) as e:
    GPIOZERO_AVAILABLE = False
    GpioButton = None
    print(f"⚠️ [ROBOT] gpiozero no disponible en este entorno: {e}. Activando modo SIMULADO.")


class RobotControl:
    """Controlador físico directo del robot coctelero en Raspberry Pi 5."""

    def __init__(self):
        self.is_busy = False
        self.current_position = 0  # Posición del riel en cm
        self.current_drink = None
        self.current_step_index = 0
        self.total_steps = 0
        self.current_step_info = ""
        self.cup_present = True    # Actualizado por ESP32 / Arduino (/api/iot/telemetry)
        self.last_sensor_distance_cm = None
        self._emergency_abort = False
        self._lock = threading.Lock()
        self._inv_lock = threading.Lock()
        self.hardware_active = False

        self._in1 = None
        self._in2 = None
        self._ena = None
        self._limit_switch = None
        self._cup_sensor_gpio = None
        self.pumps = {}

        self._setup_hardware()

    def _setup_hardware(self):
        """Inicializa los pines GPIO usando gpiozero en Raspberry Pi 5."""
        if not GPIOZERO_AVAILABLE:
            print("🤖 [ROBOT] Inicializado en modo SIMULADO (Sin hardware GPIO físico).")
            return

        try:
            # Motor DC (L298N)
            self._in1 = OutputDevice(GPIO_MOTOR_IN1, initial_value=False)
            self._in2 = OutputDevice(GPIO_MOTOR_IN2, initial_value=False)
            self._ena = PWMOutputDevice(GPIO_MOTOR_ENA, initial_value=0, frequency=1000)

            # Bombas Peristálticas (Relés activos en bajo)
            # active_high=False e initial_value=False asegura que los relés arranquen en reposo (apagados)
            for pump_key, pin in PUMPS_PINS.items():
                self.pumps[pump_key] = OutputDevice(pin, active_high=False, initial_value=False)

            if GPIO_LIMIT_SWITCH > 0 and GpioButton is not None:
                self._limit_switch = GpioButton(GPIO_LIMIT_SWITCH, pull_up=True, bounce_time=0.02)
                print(f"🛑 [ROBOT] Final de carrera (Homing 0cm) activo en GPIO {GPIO_LIMIT_SWITCH}")
            if GPIO_CUP_SENSOR > 0 and GpioButton is not None:
                self._cup_sensor_gpio = GpioButton(GPIO_CUP_SENSOR, pull_up=True, bounce_time=0.05)
                print(f"🥛 [ROBOT] Sensor físico de vaso activo en GPIO {GPIO_CUP_SENSOR}")

            self.hardware_active = True
            print("🤖 [ROBOT] ✅ Hardware GPIO inicializado con éxito en Raspberry Pi 5 (gpiozero).")
        except Exception as e:
            self.hardware_active = False
            print(f"⚠️ [ROBOT] Error configurando pines GPIO con gpiozero: {e}. Modo SIMULADO activado.")

    def is_cup_detected(self) -> bool:
        """Verifica sensor físico local en GPIO o telemetría de ESP32/Arduino."""
        if self._cup_sensor_gpio is not None:
            self.cup_present = bool(self._cup_sensor_gpio.is_pressed)
        return bool(self.cup_present)

    def emergency_stop(self):
        """Parada de emergencia inmediata (corta motor y todas las bombas en <100ms)."""
        self._emergency_abort = True
        self.detener_motor()
        for pump_key, pump in self.pumps.items():
            try:
                pump.off()
            except Exception as e:
                print(f"❌ [ROBOT] Error apagando bomba {pump_key} en emergencia: {e}")
        print("🛑 [ROBOT] ¡PARADA DE EMERGENCIA ACTIVADA! Motor y bombas detenidos.")

    def _interruptible_sleep(self, duration):
        """Duerme en intervalos de 50ms para permitir corte inmediato por parada de emergencia."""
        end_time = time.time() + max(0.0, duration)
        while time.time() < end_time:
            if self._emergency_abort:
                return False
            time.sleep(min(0.05, end_time - time.time()))
        return not self._emergency_abort

    def deduct_inventory_ml(self, steps):
        """Descuenta atómicamente los mL servidos de inventario.json y deshabilita bombas agotadas."""
        inv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inventario.json")
        if not os.path.exists(inv_path) or not steps:
            return
        with self._inv_lock:
            try:
                with open(inv_path, "r", encoding="utf-8") as f:
                    inv = json.load(f)
                bombas = inv.get("bombas", {})
                changed = False
                for step in steps:
                    pkey = step.get("pump")
                    used_ml = int(step.get("amount_ml", 0))
                    if pkey in bombas and used_ml > 0:
                        rem = max(0, int(bombas[pkey].get("ml_restantes", 0)) - used_ml)
                        bombas[pkey]["ml_restantes"] = rem
                        if rem <= 0:
                            bombas[pkey]["disponible"] = False
                        changed = True
                if changed:
                    tmp_path = f"{inv_path}.tmp"
                    with open(tmp_path, "w", encoding="utf-8") as f:
                        json.dump(inv, f, ensure_ascii=False, indent=2)
                    os.replace(tmp_path, inv_path)
            except Exception as e:
                print(f"⚠️ [ROBOT] Error actualizando stock en inventario.json: {e}")

    def detener_motor(self):
        """Detiene el movimiento del motor del riel."""
        if self.hardware_active and self._in1 and self._in2 and self._ena:
            try:
                self._in1.off()
                self._in2.off()
                self._ena.value = 0
            except Exception as e:
                print(f"❌ [ROBOT] Error deteniendo motor: {e}")

    def mover_motor(self, target_cm):
        """Desplaza el carro a la posición indicada en centímetros."""
        if target_cm == self.current_position:
            return

        distance = target_cm - self.current_position
        tiempo_movimiento = abs(distance) * SEGUNDOS_POR_CM
        print(f"⚙️ [ROBOT-MOTOR] Moviendo de {self.current_position}cm a {target_cm}cm ({tiempo_movimiento:.2f}s)...")

        if self.hardware_active and self._in1 and self._in2 and self._ena:
            try:
                if distance > 0:
                    # Adelante (Hacia las bombas)
                    self._in1.off()
                    self._in2.on()
                else:
                    # Atrás (Regreso a origen)
                    self._in1.on()
                    self._in2.off()

                self._ena.value = MOTOR_VELOCIDAD
                if target_cm == 0 and distance < 0 and self._limit_switch is not None:
                    # Homing con final de carrera en pasos de 20 ms
                    end_time = time.time() + max(0.0, tiempo_movimiento * 1.25)
                    while time.time() < end_time and not self._emergency_abort:
                        if self._limit_switch.is_pressed:
                            break
                        time.sleep(0.02)
                else:
                    self._interruptible_sleep(tiempo_movimiento)
                self.detener_motor()
            except Exception as e:
                print(f"❌ [ROBOT] Error en movimiento de motor: {e}")
                self.detener_motor()
        else:
            # Simulación
            self._interruptible_sleep(min(tiempo_movimiento, 1.0))

        if not self._emergency_abort:
            self.current_position = target_cm
            print(f"⚙️ [ROBOT-MOTOR] Carro en posición {self.current_position}cm.")

    def activar_bomba(self, pump_key, amount_ml):
        """Enciende la bomba peristáltica indicada por el tiempo proporcional a los mL."""
        if self._emergency_abort:
            return
        tiempo_servido = amount_ml / 1.5
        print(f"💧 [ROBOT-BOMBA] Activando {pump_key} para servir {amount_ml}mL ({tiempo_servido:.2f}s)...")

        if self.hardware_active and pump_key in self.pumps:
            try:
                pump_device = self.pumps[pump_key]
                pump_device.on()  # Activa relé en LOW (active_high=False)
                self._interruptible_sleep(tiempo_servido)
                pump_device.off() # Desactiva relé
                print(f"💧 [ROBOT-BOMBA] {pump_key} servido completado.")
            except Exception as e:
                print(f"❌ [ROBOT] Error operando bomba {pump_key}: {e}")
                if pump_key in self.pumps:
                    self.pumps[pump_key].off()
        else:
            # Simulación
            self._interruptible_sleep(min(tiempo_servido, 1.5))
            print(f"💧 [ROBOT-SIMULADO] {pump_key} completado.")

    def execute_dispense(self, dispense_data, on_step_completed=None):
        """
        Ejecuta de forma síncrona/hilo-segura la secuencia de pasos recibida en:
        [DISPENSE: {"trago": "<nombre>", "steps": [{"pump": "pump_1", "amount_ml": 45, "cm": 10}, ...]}]
        """
        if REQUIRE_CUP_SENSOR and not self.is_cup_detected():
            print("⚠️ [ROBOT] No se detectó vaso en el sensor (GPIO / ESP32). Dispensado cancelado.")
            return False

        with self._lock:
            if self.is_busy:
                print("⚠️ [ROBOT] La máquina ya está ocupada preparando una bebida.")
                return False

            self.is_busy = True
            self._emergency_abort = False

        try:
            trago = dispense_data.get("trago", "Bebida")
            self.current_drink = trago
            steps = dispense_data.get("steps", [])

            # Si no vienen pasos explícitos pero sí el nombre del trago, resolverlos desde RECETAS_COCTELES
            if not steps and trago:
                steps = self._resolve_steps_for_drink(trago)

            if not steps:
                print(f"❌ [ROBOT] No se encontraron pasos de dispensado para '{trago}'.")
                return False

            print(f"\n🍹 ==================================================")
            print(f"🍹 [DISPENSADOR] Iniciando preparación de '{trago}' ({len(steps)} pasos)")
            print(f"🍹 ==================================================")

            # Ordenar los pasos por posición en cm para minimizar desplazamientos innecesarios
            steps_sorted = sorted(steps, key=lambda s: s.get("cm", 0))
            self.total_steps = len(steps_sorted)
            completed_steps = []

            for idx, step in enumerate(steps_sorted, 1):
                if self._emergency_abort:
                    print("🛑 [ROBOT] Preparación abortada por parada de emergencia.")
                    return False

                pump_key = step.get("pump")
                amount_ml = step.get("amount_ml", 20)
                target_cm = step.get("cm", 0)
                self.current_step_index = idx
                self.current_step_info = f"paso {idx} de {self.total_steps} ({amount_ml} mL en {pump_key})"

                print(f"\n📌 [Paso {idx}/{self.total_steps}] Moviendo a {target_cm}cm para bomba '{pump_key}'...")
                self.mover_motor(target_cm)
                if not self._interruptible_sleep(0.4):
                    return False

                self.activar_bomba(pump_key, amount_ml)
                if self._emergency_abort:
                    return False
                completed_steps.append(step)
                if on_step_completed:
                    try:
                        on_step_completed(idx, self.total_steps, trago)
                    except Exception:
                        pass
                self._interruptible_sleep(0.4)  # Evitar goteo al reiniciar movimiento

            # Descontar stock en hardware real (o si se solicita explícitamente en dispense_data)
            if self.hardware_active or dispense_data.get("deduct_inventory"):
                self.deduct_inventory_ml(completed_steps)

            # Al finalizar todos los ingredientes, regresar a Home (0 cm)
            print("\n⚙️ [ROBOT] Preparación completada. Regresando carro a la posición inicial (0cm)...")
            self.mover_motor(0)
            self._interruptible_sleep(0.5)

            print(f"✅ [ROBOT] ¡'{trago}' servido con éxito!")
            return True

        except Exception as e:
            print(f"❌ [ROBOT] Error durante la ejecución de dispensado: {e}")
            self.detener_motor()
            return False
        finally:
            self.current_drink = None
            self.current_step_index = 0
            self.total_steps = 0
            self.current_step_info = ""
            with self._lock:
                self.is_busy = False

    def _resolve_steps_for_drink(self, drink_name):
        """Resuelve los pasos de bomba y posición para un trago a partir de BOMBAS_CONFIG y RECETAS_COCTELES."""
        def clean(s):
            return "".join(c for c in unicodedata.normalize('NFD', s or '') if unicodedata.category(c) != 'Mn').lower().strip()

        target_clean = clean(drink_name)
        recipe = None

        for name, r in RECETAS_COCTELES.items():
            name_clean = clean(name)
            if name_clean in target_clean or target_clean in name_clean:
                recipe = r
                break

        if not recipe:
            return []

        steps = []
        for ingrediente, ml in recipe.items():
            ing_clean = clean(ingrediente)
            found_pump = None
            target_cm = 0

            for pump_id, pump_info in BOMBAS_CONFIG.items():
                if clean(pump_info.get("ingrediente")) == ing_clean:
                    found_pump = pump_id
                    target_cm = pump_info.get("cm", 0)
                    break

            if found_pump:
                steps.append({
                    "pump": found_pump,
                    "amount_ml": ml,
                    "cm": target_cm
                })

        return steps

    def send_drink_command(self, robot_command):
        """Compatibilidad con versiones anteriores."""
        drink_name = robot_command
        if robot_command.startswith("PREPARAR:"):
            drink_name = robot_command.replace("PREPARAR:", "", 1).strip()
        steps = self._resolve_steps_for_drink(drink_name)
        return self.execute_dispense({"trago": drink_name, "steps": steps})

    def wait_for_drink_ready(self, timeout=120):
        """Espera a que el dispensador termine de servir."""
        start = time.time()
        while time.time() - start < timeout:
            if not self.is_busy:
                return True
            time.sleep(0.5)
        return True

    def cleanup(self):
        """Libera todos los pines GPIO al apagar el sistema."""
        self.detener_motor()
        for pump in self.pumps.values():
            try:
                pump.off()
                pump.close()
            except Exception:
                pass
        if self._ena:
            try: self._ena.close()
            except Exception: pass
        if self._in1:
            try: self._in1.close()
            except Exception: pass
        if self._in2:
            try: self._in2.close()
            except Exception: pass
