import requests
import time

from config import (
    ROBOT_ENABLED, ROBOT_IP, ROBOT_PORT
)

class RobotControl:
    """Controla la comunicación con la Raspberry Pi (brazo robot y bombas) vía HTTP.
    
    Ahora MIA (Windows) es el orquestador maestro y la Raspberry Pi es un esclavo
    que solo obedece /move y /dispense.
    """
    def __init__(self):
        self.connected = False
        
        # Menú fijo con tragos ya preparados en cada bomba
        self.drinks_menu = {
            "Tormenta Púrpura": {"pump": "pump_1", "cm": 0.01},
            "Brisa Marina": {"pump": "pump_2", "cm": .5},
            "Atardecer": {"pump": "pump_3", "cm": 1.25},
            "La Bandera": {"pump": "pump_4", "cm": 1.85}
        }
        
        if not ROBOT_ENABLED:
            print("🤖 [ROBOT] Integración con Raspberry Pi desactivada en la configuración.")
            return

        self.connect()

    def connect(self):
        self.connected = False
        url = f"http://{ROBOT_IP}:{ROBOT_PORT}/status"
        try:
            print(f"🤖 [ROBOT] Verificando conexión HTTP con Raspberry Pi en {url}...")
            res = requests.get(url, timeout=3.0)
            if res.status_code == 200:
                self.connected = True
                print("🤖 [ROBOT] ✅ Conexión establecida con Raspberry Pi (HTTP). Modo Maestro-Esclavo.")
            else:
                print(f"🤖 [ROBOT] ⚠️ La Raspberry Pi respondió con código {res.status_code}.")
        except Exception as e:
            print(f"🤖 [ROBOT] ⚠️ No se pudo conectar a la Raspberry Pi en {url}.")
            print("   MIA funcionará normalmente, pero el hardware no se activará.")

    def send_drink_command(self, robot_command):
        """Orquesta la preparación desde Windows enviando los pasos al esclavo Pi."""
        if not self.connected:
            self.connect()
            if not self.connected:
                return False
            
        drink_name = robot_command
        if robot_command.startswith("PREPARAR:"):
            drink_name = robot_command.replace("PREPARAR:", "", 1).strip()
            
        # Buscar el trago en el menú (ignorando mayúsculas)
        match = None
        for name, data in self.drinks_menu.items():
            if name.lower() in drink_name.lower():
                match = data
                break
                
        if not match:
            print(f"🤖 [ROBOT] ❌ El trago '{drink_name}' no está en las bombas.")
            return False
            
        target_cm = match["cm"]
        pump_key = match["pump"]
        amount_ml = 60 # Servimos 60mL en vez de 150mL
        
        print(f"🤖 [ROBOT] Enviando orden de preparación de {drink_name} a Raspberry Pi...")
        url = f"http://{ROBOT_IP}:{ROBOT_PORT}/prepare"
        
        try:
            res = requests.post(url, json={"pump": pump_key, "amount_ml": amount_ml, "cm": target_cm}, timeout=5.0)
            if res.status_code == 200:
                print(f"🤖 [ROBOT] ✅ Orden recibida. Raspberry Pi preparará la bebida en segundo plano.")
                return True
            else:
                print(f"🤖 [ROBOT] ⚠️ Raspberry Pi rechazó la orden. Código: {res.status_code}")
                return False
        except Exception as e:
            print(f"🤖 [ROBOT] ❌ Error enviando comando a Raspberry Pi: {e}")
            return False

    def wait_for_drink_ready(self, timeout=120):
        if not self.connected: return True
        
        print("🤖 [ROBOT] Esperando a que la Raspberry Pi termine físicamente...")
        url = f"http://{ROBOT_IP}:{ROBOT_PORT}/status"
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                res = requests.get(url, timeout=2.0)
                if res.status_code == 200:
                    data = res.json()
                    if not data.get("busy", False):
                        print("🤖 [ROBOT] ✅ La Raspberry Pi ha finalizado la preparación.")
                        return True
            except:
                pass
            time.sleep(2)
            
        print("🤖 [ROBOT] ⚠️ Timeout esperando a que la máquina termine.")
        return True
