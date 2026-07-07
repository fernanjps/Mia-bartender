from assistant import VoiceAssistant

if __name__ == "__main__":
    print("""
    ╔══════════════════════════════════════╗
    ║       🤖  MIA Voice Assistant       ║
    ║   Local AI with Vision & Sarcasm    ║
    ╠══════════════════════════════════════╣
    ║  Di 'Hey MIA' para activarme        ║
    ║  Escribe 'salir' para detener       ║
    ╚══════════════════════════════════════╝
    """)

    mia = VoiceAssistant()
    mia.run_interactive()