import socket
import time

# Usamos localhost porque el simulador correrá en la misma laptop que server.py
HOST = '127.0.0.1' 
PORT = 8888

def iniciar_simulador():
    print("==================================================")
    print("🍹 INICIANDO SIMULADOR VIRTUAL DE RASPBERRY PI 🍹")
    print(f"Escuchando en {HOST}:{PORT}...")
    print("Esperando órdenes de MIA...")
    print("==================================================")

    # Crear el servidor Socket TCP
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, PORT))
        s.listen()
        
        while True:
            try:
                conn, addr = s.accept()
                with conn:
                    print(f"\n[🔌 CONECTADO] MIA acaba de conectarse desde {addr}")
                    while True:
                        data = conn.recv(1024)
                        if not data:
                            break
                        
                        # Decodificar el mensaje que envía MIA
                        mensaje = data.decode('utf-8').strip()
                        print(f"\n[🤖 ORDEN RECIBIDA]: {mensaje}")
                        
                        # Simular el hardware
                        print("[⚙️ HARDWARE] Activando módulo de relés...")
                        time.sleep(1)
                        print("[💧 BOMBA 1] Sirviendo base (ej. Ron) -> 3 segundos...")
                        time.sleep(3)
                        print("[💧 BOMBA 2] Sirviendo mezcla (ej. Limón) -> 2 segundos...")
                        time.sleep(2)
                        
                        print("[✅ ÉXITO] ¡Bebida preparada virtualmente!")
                        
                        # (Opcional) Responderle a MIA que ya terminamos
                        conn.sendall(b"OK\n")
                        
            except KeyboardInterrupt:
                print("\n[!] Apagando simulador...")
                break
            except Exception as e:
                print(f"[!] Error en el simulador: {e}")

if __name__ == "__main__":
    iniciar_simulador()