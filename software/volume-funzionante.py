# Volume Controller con supporto -v per verbose logging

import serial
import time
import threading
import sys
from queue import Queue
from pycaw.pycaw import AudioUtilities
import pythoncom
import logging

# Configura logging in base al flag -v
if '-v' in sys.argv:
    log_level = logging.INFO
    print("Modalità verbose attivata - Logs visibili")
else:
    log_level = logging.CRITICAL + 1  # Disabilita tutti i log

logging.basicConfig(level=log_level, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Stampe condizionali solo in modalità verbose
if '-v' in sys.argv:
    print("Controller volume con forcing universale per tutte le app...")

# --- CONFIGURAZIONE ---
SERIAL_PORT = 'COM7'
BAUD_RATE = 115200
BLACKLIST = {"WhatsApp.exe", "SystemSounds.exe", "Teams.exe", "Steam.exe", "ArmouryCrate.Service.exe", "ArmouryCrate.UserSessionHelper.exe"}

PRIORITY_RULES = [{"when": lambda active: True, "assign": "auto"}]

command_queue = Queue()
last_sent_volumes = [0, 0, 0]

def get_active_audio_apps():
    """Ottieni applicazioni audio - incluse quelle a volume 0"""
    active = {}
    try:
        try:
            pythoncom.CoInitialize()
        except:
            pass

        for session in AudioUtilities.GetAllSessions():
            process = session.Process
            if process:
                name = process.name()
                if name not in BLACKLIST:
                    try:
                        # Includi TUTTE le app audio, anche quelle a volume 0
                        active[name] = session.SimpleAudioVolume
                    except Exception:
                        continue
    except Exception as e:
        logger.error(f"Errore nel recuperare sessioni audio: {e}")
    return active

def build_app_map_from_rules(active_apps):
    """Costruisce mapping applicazioni -> slot encoder"""
    app_map = {}
    active_names = set(active_apps.keys())

    for rule in PRIORITY_RULES:
        if rule["when"](active_names):
            if rule["assign"] == "auto":
                for i, app_name in enumerate(sorted(active_names)[:3]):
                    app_map[i] = app_name
            break
    return app_map

def volume_worker():
    """Worker thread con FORCING UNIVERSALE per tutte le app"""
    pythoncom.CoInitialize()
    logger.info("Worker thread con forcing universale avviato")

    try:
        last_commands = {}
        while True:
            while not command_queue.empty():
                try:
                    command_type, data = command_queue.get()
                    if command_type == "volume":
                        index, percent = data
                        last_commands[index] = ("volume", percent)
                    elif command_type == "mute":
                        index, mute_state = data
                        last_commands[index] = ("mute", mute_state)
                except Exception as e:
                    logger.error(f"Errore processando comando: {e}")

            if last_commands:
                try:
                    app_sessions = get_active_audio_apps()
                    app_map = build_app_map_from_rules(app_sessions)

                    for index, (command_type, value) in last_commands.items():
                        app_name = app_map.get(index)

                        if app_name in app_sessions:
                            try:
                                if command_type == "volume":
                                    scalar = max(0.0, min(1.0, value / 100.0))
                                    current = app_sessions[app_name].GetMasterVolume()

                                    # FORCING UNIVERSALE - Applica a TUTTE le app
                                    if abs(current - scalar) > 0.01:
                                        logger.info(f"🔄 FORCING {app_name}: {current:.3f} → {scalar:.3f}")

                                        for attempt in range(3):  # 3 tentativi per tutte le app
                                            app_sessions[app_name].SetMasterVolume(scalar, None)
                                            time.sleep(0.05)  # Pausa tra tentativi
                                            new_vol = app_sessions[app_name].GetMasterVolume()

                                            if abs(new_vol - scalar) < 0.01:
                                                logger.info(f"✅ {app_name} volume impostato: {new_vol:.3f} ({int(new_vol*100)}%)")
                                                break
                                            else:
                                                logger.warning(f"⚠️ {app_name} tentativo {attempt+1}: richiesto {scalar:.3f}, ottenuto {new_vol:.3f}")

                                elif command_type == "mute":
                                    app_sessions[app_name].SetMute(value, None)
                                    status = "MUTATO" if value else "SMUTATO" 
                                    logger.info(f"🔇 {app_name} {status}")

                            except Exception as e:
                                logger.error(f"❌ ERRORE su {app_name}: {e}")
                        else:
                            logger.warning(f"⚠️ App {app_name} non trovata")

                    last_commands.clear()
                except Exception as e:
                    logger.error(f"Errore elaborazione comandi: {e}")

            time.sleep(0.02)
    finally:
        pythoncom.CoUninitialize()
        logger.info("Worker thread terminato")

def send_volumes_to_esp32(ser):
    """Invia volumi correnti all'ESP32"""
    global last_sent_volumes

    try:
        try:
            pythoncom.CoInitialize()
        except:
            pass

        app_sessions = get_active_audio_apps()
        app_map = build_app_map_from_rules(app_sessions)

        volumes = []
        for i in range(3):
            app_name = app_map.get(i)
            if app_name in app_sessions:
                is_muted = app_sessions[app_name].GetMute()
                if is_muted:
                    current = 0
                else:
                    current = int(app_sessions[app_name].GetMasterVolume() * 100)
            else:
                current = 0
            volumes.append(current)

        if volumes != last_sent_volumes:
            response = f"VOLS:{volumes[0]},{volumes[1]},{volumes[2]}\n"
            ser.write(response.encode('utf-8'))
            last_sent_volumes = volumes.copy()
            logger.info(f"📡 Volumi inviati: {volumes}")
    except Exception as e:
        logger.error(f"Errore invio volumi: {e}")

def process_serial_command(line, ser):
    logger.debug(f"📥 Comando: {line}")
    try:
        if line.startswith("SET_VOL:"):
            parts = line[8:].split(',')
            if len(parts) == 2:
                index = int(parts[0])
                percent = int(parts[1])
                if 0 <= index <= 2 and 0 <= percent <= 100:
                    command_queue.put(("volume", (index, percent)))
        elif line.startswith("MUTE:"):
            parts = line[5:].split(',')
            if len(parts) == 2:
                index = int(parts[0])
                mute_state = bool(int(parts[1]))
                if 0 <= index <= 2:
                    command_queue.put(("mute", (index, mute_state)))
        elif line == "GET_ALL_VOLS":
            send_volumes_to_esp32(ser)
    except Exception as e:
        logger.error(f"Errore parsing: {e}")

def main():
    pythoncom.CoInitialize()
    logger.info("🚀 Avvio con forcing universale")

    try:
        worker_thread = threading.Thread(target=volume_worker, daemon=True)
        worker_thread.start()
        logger.info("Worker thread avviato")

        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                logger.info(f"Tentativo connessione ESP32... ({retry_count + 1}/{max_retries})")

                with serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1) as ser:
                    logger.info(f"📡 Connesso su {SERIAL_PORT}")
                    retry_count = 0

                    time.sleep(2)
                    send_volumes_to_esp32(ser)

                    while True:
                        try:
                            if ser.in_waiting > 0:
                                line = ser.readline().decode('utf-8').strip()
                                if line:
                                    process_serial_command(line, ser)
                            time.sleep(0.01)
                        except UnicodeDecodeError:
                            logger.warning("Dati seriali non validi")
                        except serial.SerialException as e:
                            logger.error(f"Errore comunicazione seriale: {e}")
                            break
                        except Exception as e:
                            logger.error(f"Errore loop: {e}")
                            break

            except serial.SerialException as e:
                retry_count += 1
                logger.error(f"Errore connessione seriale: {e}")
                if retry_count < max_retries:
                    logger.info("Riprovo tra 3 secondi...")
                    time.sleep(3)
                else:
                    logger.error("Impossibile stabilire connessione seriale")
                    break
            except KeyboardInterrupt:
                logger.info("Interruzione richiesta dall'utente")
                break
            except Exception as e:
                logger.error(f"Errore critico: {e}")
                retry_count += 1
                if retry_count < max_retries:
                    time.sleep(3)
    finally:
        pythoncom.CoUninitialize()
        logger.info("🛑 Programma terminato")

if __name__ == "__main__":
    main()
