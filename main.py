import sys
import os

if __name__ == "__main__":
    print("""
    ╔══════════════════════════════════════╗
    ║       🤖  MIA Voice Assistant       ║
    ║        Servidor Híbrido Groq        ║
    ╠══════════════════════════════════════╣
    ║  Ejecutando servidor web y motor     ║
    ╚══════════════════════════════════════╝
    """)

    # Si se ejecuta 'python main.py --interactive', corre modo consola
    if len(sys.argv) > 1 and sys.argv[1] == "--interactive":
        from assistant import VoiceAssistant
        mia = VoiceAssistant()
        mia.run_interactive()
    else:
        # Por defecto arranca el Servidor Web (server.py) y la IA MIA completa con Groq Cloud API
        import threading
        import time
        import server
        
        mia_thread = threading.Thread(target=server.start_mia_backend, daemon=True)
        mia_thread.start()
        time.sleep(2)
        
        server.socketio.run(server.app, host='0.0.0.0', port=5000, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)