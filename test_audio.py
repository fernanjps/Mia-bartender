import speech_recognition as sr
import time

mics = sr.Microphone.list_microphone_names()
for i, name in enumerate(mics):
    if 'audiorelay' in name.lower() and 'speaker' not in name.lower():
        print(f"\nProbando: {name} (Index {i})")
        try:
            m = sr.Microphone(device_index=i)
            r = sr.Recognizer()
            with m as source:
                print("Calibrando...")
                r.adjust_for_ambient_noise(source, duration=2)
                print(f"Energy threshold: {r.energy_threshold}")
                print("Escuchando por 2 segundos...")
                try:
                    audio = r.listen(source, timeout=2, phrase_time_limit=2)
                    print(f"Audio capturado! Longitud: {len(audio.frame_data)} bytes")
                except sr.WaitTimeoutError:
                    print("Timeout: no se detectó sonido por encima del umbral.")
        except Exception as e:
            print(f"Error con este dispositivo: {e}")
