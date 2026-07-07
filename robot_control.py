import socket
import sys
try:
    import serial
except ImportError:
    serial = None

from config import (
    ROBOT_ENABLED, ROBOT_CONNECTION_TYPE, ROBOT_IP,
    ROBOT_PORT, ROBOT_SERIAL_PORT, ROBOT_SERIAL_BAUD
)

class RobotControl:
    """Controla la comunicación con la Raspberry Pi (brazo robot y bombas).
    
    Se conecta via TCP o Serial de forma no bloqueante y robusta.
    Si no detecta la conexión, valida el estado e imprime una advertencia,
    pero no interrumpe el flujo general de MIA.
    """
    def __init__(self):
        self.connected = False
        self.socket_conn = None
        self.serial_conn = None
        
        if not ROBOT_ENABLED:
            print("🤖 [ROBOT] Integración con Raspberry Pi desactivada en la configuración.")
            return

        self.connect()

    def connect(self):
        """Intenta conectar a la Raspberry Pi"""
        self.connected = False
        if ROBOT_CONNECTION_TYPE == "TCP":
            try:
                print(f"🤖 [ROBOT] Conectando a Raspberry Pi vía TCP en {ROBOT_IP}:{ROBOT_PORT}...")
                self.socket_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket_conn.settimeout(2.0)  # Timeout corto para no congelar la app
                self.socket_conn.connect((ROBOT_IP, ROBOT_PORT))
                self.connected = True
                print("🤖 [ROBOT] ✅ Conexión establecida con Raspberry Pi (TCP).")
            except Exception as e:
                print(f"🤖 [ROBOT] ⚠️ No se pudo conectar a la Raspberry Pi en {ROBOT_IP}:{ROBOT_PORT}.")
                print(f"   Detalle: {e}")
                print("   MIA funcionará normalmente, pero el brazo y las bombas no se activarán.")
                self.socket_conn = None
                
        elif ROBOT_CONNECTION_TYPE == "SERIAL":
            if serial is None:
                print("🤖 [ROBOT] ⚠️ Biblioteca 'pyserial' no está instalada.")
                print("   MIA funcionará normalmente, pero el brazo y las bombas no se activarán.")
                return
            try:
                print(f"🤖 [ROBOT] Conectando a Raspberry Pi vía Serial en {ROBOT_SERIAL_PORT}...")
                self.serial_conn = serial.Serial(ROBOT_SERIAL_PORT, ROBOT_SERIAL_BAUD, timeout=1.0)
                self.connected = True
                print(f"🤖 [ROBOT] ✅ Conexión establecida con Raspberry Pi (Serial en {ROBOT_SERIAL_PORT}).")
            except Exception as e:
                print(f"🤖 [ROBOT] ⚠️ No se pudo abrir el puerto serial {ROBOT_SERIAL_PORT}.")
                print(f"   Detalle: {e}")
                print("   MIA funcionará normalmente, pero el brazo y las bombas no se activarán.")
                self.serial_conn = None

    def send_drink_command(self, robot_command):
        """Envía la instrucción cruda del robot a la Raspberry Pi."""
        if not self.connected:
            print(f"🤖 [ROBOT] ⚠️ Intento de ejecutar '{robot_command}' cancelado. Raspberry Pi desconectada.")
            return False
            
        # El comando ya viene con el formato PREPARAR:xxx o MEZCLAR:xxx
        command = f"{robot_command}\n"
        print(f"🤖 [ROBOT] Enviando instrucción a Raspberry Pi: {command.strip()}")
        
        try:
            if ROBOT_CONNECTION_TYPE == "TCP" and self.socket_conn:
                self.socket_conn.sendall(command.encode('utf-8'))
                return True
            elif ROBOT_CONNECTION_TYPE == "SERIAL" and self.serial_conn:
                self.serial_conn.write(command.encode('utf-8'))
                return True
        except Exception as e:
            print(f"🤖 [ROBOT] ❌ Error enviando instrucción a Raspberry Pi: {e}")
            self.connected = False  # Marcar como desconectado
            return False
        return False
