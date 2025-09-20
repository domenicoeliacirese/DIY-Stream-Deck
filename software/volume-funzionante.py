# Versione corretta con gestione sincronizzata mute/volume

import serial
import time
import threading
from queue import Queue
from pycaw.pycaw import AudioUtilities
import pythoncom # IMPORTANTE: Aggiungere questa importazione
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

print("Avvio del controller di volume dinamico con gestione mute corretta...")

# --- CONFIGURAZIONE ---
SERIAL_PORT = 'COM7'
BAUD_RATE = 115200
BLACKLIST = {"WhatsApp.exe", "SystemSounds.exe", "Teams.exe", "Steam.exe", "ArmouryCrate.Service.exe", "ArmouryCrate.UserSessionHelper.exe"}
VOLUME_CHECK_INTERVAL = 1.0  # Controllo volumi ogni secondo
FORCE_RESET_INTERVAL = 5.0   # Reset forzato canali vuoti ogni 5 secondi

PRIORITY_RULES = [
    {
        "when": lambda active: "Discord.exe" in active,
        "assign": {
            0: "input",
            1: "Discord.exe",
        },
    },
    {
        "when": lambda active: True,
        "assign": "auto"
    }
]

command_queue = Queue()
last_sent_volumes = [0, 0, 0] # Cache per evitare invii ridondanti
last_sent_mute_states = [False, False, False] # Cache stati mute
last_app_mapping = {}  # Traccia l'ultimo mapping delle app
channel_mute_states = [False, False, False]  # Stati mute per canale
volume_check_thread = None  # Thread per controllo periodico

def get_active_audio_apps():
    """Ottieni applicazioni audio attive con inizializzazione COM corretta"""
    active = {}
    try:
        # INIZIALIZZA COM se non già fatto in questo thread
        try:
            pythoncom.CoInitialize()
        except:
            pass # Già inizializzato in questo thread

        for session in AudioUtilities.GetAllSessions():
            process = session.Process
            if process:
                name = process.name()
                if name not in BLACKLIST:
                    try:
                        vol = session.SimpleAudioVolume.GetMasterVolume()
                        if vol > 0 or not session.SimpleAudioVolume.GetMute():  # Include anche app mutate
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
            else:
                for slot, name in rule["assign"].items():
                    app_map[slot] = name
            break
    return app_map

def reset_empty_channels_to_zero(ser, current_app_map, active_apps):
    """Reset canali vuoti a volume 0"""
    global last_sent_volumes, last_sent_mute_states, last_app_mapping, channel_mute_states

    volumes_changed = False
    mute_changed = False
    new_volumes = last_sent_volumes.copy()
    new_mute_states = last_sent_mute_states.copy()

    for i in range(3):
        app_name = current_app_map.get(i)

        # Se il canale non ha un'app assegnata o l'app non è più attiva
        if not app_name or app_name not in active_apps:
            if new_volumes[i] != 0:
                new_volumes[i] = 0
                volumes_changed = True
            if new_mute_states[i] != False:
                new_mute_states[i] = False
                channel_mute_states[i] = False
                mute_changed = True
                logger.info(f"[Canale {i}] Reset mute - App non trovata/chiusa: {app_name}")

    # Se ci sono stati cambiamenti, invia i nuovi stati
    if volumes_changed or mute_changed:
        response = f"VOLS:{new_volumes[0]},{new_volumes[1]},{new_volumes[2]}\n"
        ser.write(response.encode('utf-8'))
        last_sent_volumes = new_volumes.copy()
        last_sent_mute_states = new_mute_states.copy()
        logger.info(f"Stati aggiornati inviati all'ESP32 - Volumi: {new_volumes}, Mute: {new_mute_states}")

    # Aggiorna il mapping precedente
    last_app_mapping = current_app_map.copy()

def volume_monitor_worker(ser):
    """Worker thread per monitoraggio periodico volumi e reset canali vuoti"""
    pythoncom.CoInitialize()
    logger.info("Thread monitor volumi avviato")

    last_force_reset = time.time()

    try:
        while True:
            try:
                current_time = time.time()

                # Ottieni stato attuale
                active_apps = get_active_audio_apps()
                current_app_map = build_app_map_from_rules(active_apps)

                # Reset forzato periodico o quando le app cambiano
                force_reset = (current_time - last_force_reset) >= FORCE_RESET_INTERVAL
                apps_changed = current_app_map != last_app_mapping

                if force_reset or apps_changed:
                    reset_empty_channels_to_zero(ser, current_app_map, active_apps)
                    if force_reset:
                        last_force_reset = current_time
                        logger.debug("Reset forzato canali vuoti completato")

                # Invia volumi correnti se necessario
                send_volumes_to_esp32(ser, skip_empty_reset=True)

            except Exception as e:
                logger.error(f"Errore nel monitor volumi: {e}")

            time.sleep(VOLUME_CHECK_INTERVAL)
    finally:
        pythoncom.CoUninitialize()
        logger.info("Thread monitor volumi terminato")

def volume_worker():
    """Worker thread per processare comandi volume con COM inizializzato"""
    global channel_mute_states

    # INIZIALIZZA COM per questo thread
    pythoncom.CoInitialize()
    logger.info("COM inizializzato nel worker thread")

    try:
        last_commands = {}
        while True:
            # Raccoglie tutti i comandi in coda, mantenendo solo l'ultimo per slot
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
                    logger.error(f"Errore nel processare comando dalla coda: {e}")

            # Elabora comandi accumulati
            if last_commands:
                try:
                    app_sessions = get_active_audio_apps()
                    app_map = build_app_map_from_rules(app_sessions)

                    for index, (command_type, value) in last_commands.items():
                        app_name = app_map.get(index)

                        if app_name == "input":
                            logger.info(f"[Slot {index}] Input ignorato")
                            continue

                        if app_name in app_sessions:
                            try:
                                if command_type == "volume":
                                    # CONTROLLO CRITICO: Non modificare volume se il canale è mutato
                                    if channel_mute_states[index]:
                                        logger.info(f"[Slot {index}] Comando volume ignorato - Canale mutato")
                                        continue

                                    scalar = max(0.0, min(1.0, value / 100.0))
                                    current = app_sessions[app_name].GetMasterVolume()
                                    if abs(current - scalar) > 0.01:
                                        app_sessions[app_name].SetMasterVolume(scalar, None)
                                        logger.info(f"Volume '{app_name}' impostato a {value}%")

                                elif command_type == "mute":
                                    # Prima aggiorna lo stato interno
                                    channel_mute_states[index] = value

                                    # Poi applica il mute all'app
                                    app_sessions[app_name].SetMute(value, None)
                                    status_text = "MUTATO" if value else "SMUTATO"
                                    logger.info(f"'{app_name}' {status_text}")

                                    # Forza aggiornamento immediato dell'ESP32
                                    time.sleep(0.1)  # Piccola pausa per assicurarsi che Windows aggiorni lo stato

                            except Exception as e:
                                logger.error(f"Errore impostando {command_type} per '{app_name}': {e}")
                        else:
                            logger.warning(f"[Slot {index}] App '{app_name}' non trovata")

                    last_commands.clear()
                except Exception as e:
                    logger.error(f"Errore nell'elaborazione comandi: {e}")

            time.sleep(0.02) # Ridotto per maggiore responsività
    finally:
        # CLEANUP COM quando il thread termina
        pythoncom.CoUninitialize()
        logger.info("COM cleanup completato nel worker thread")

def send_volumes_to_esp32(ser, skip_empty_reset=False):
    """Invia volumi correnti all'ESP32 sincronizzati con stato mute"""
    global last_sent_volumes, last_sent_mute_states, channel_mute_states

    try:
        # INIZIALIZZA COM se necessario
        try:
            pythoncom.CoInitialize()
        except:
            pass

        app_sessions = get_active_audio_apps()
        app_map = build_app_map_from_rules(app_sessions)

        volumes = []
        mute_states = []

        for i in range(3):
            app_name = app_map.get(i)
            if app_name in app_sessions:
                try:
                    # CONTROLLO CRITICO: Prima verifica se l'app è mutata
                    is_app_muted = app_sessions[app_name].GetMute()
                    real_volume = int(app_sessions[app_name].GetMasterVolume() * 100)

                    # Se l'app è mutata O il nostro canale è marcato come mutato
                    if is_app_muted or channel_mute_states[i]:
                        current = 0  # Mostra sempre 0 sulla GUI se mutato
                        channel_mute_states[i] = True  # Sincronizza stato interno
                        mute_states.append(True)
                    else:
                        current = real_volume
                        mute_states.append(False)

                except Exception as e:
                    logger.error(f"Errore lettura stato app {app_name}: {e}")
                    current = 0
                    mute_states.append(False)
            else:
                current = 0  # IMPORTANTE: Reset a 0 per canali vuoti
                mute_states.append(False)
                channel_mute_states[i] = False  # Reset stato mute

            volumes.append(current)

        # Invia solo se i volumi O gli stati mute sono cambiati
        if volumes != last_sent_volumes or mute_states != last_sent_mute_states:
            response = f"VOLS:{volumes[0]},{volumes[1]},{volumes[2]}\n"
            ser.write(response.encode('utf-8'))
            last_sent_volumes = volumes.copy()
            last_sent_mute_states = mute_states.copy()
            logger.info(f"Stati sincronizzati inviati - Volumi: {volumes}, Mute: {mute_states}")

            # Se non stiamo già facendo un reset, controlla canali vuoti
            if not skip_empty_reset:
                reset_empty_channels_to_zero(ser, app_map, app_sessions)
    except Exception as e:
        logger.error(f"Errore nell'invio volumi: {e}")

def initialize_empty_channels(ser):
    """Inizializza tutti i canali a 0 alla connessione"""
    global last_sent_volumes, last_sent_mute_states, channel_mute_states

    logger.info("Inizializzazione canali vuoti...")
    try:
        # Forza l'invio di volumi 0 per tutti i canali
        response = f"VOLS:0,0,0\n"
        ser.write(response.encode('utf-8'))
        last_sent_volumes = [0, 0, 0]
        last_sent_mute_states = [False, False, False]
        channel_mute_states = [False, False, False]
        logger.info("Tutti i canali inizializzati a 0")
    except Exception as e:
        logger.error(f"Errore nell'inizializzazione canali: {e}")

def process_serial_command(line, ser):
    """Processa comandi ricevuti dall'ESP32"""
    logger.debug(f"Comando ricevuto: '{line}'")

    try:
        if line.startswith("SET_VOL:"):
            # Formato: SET_VOL:slot,percent
            parts = line[8:].split(',')
            if len(parts) == 2:
                index = int(parts[0])
                percent = int(parts[1])
                if 0 <= index <= 2 and 0 <= percent <= 100:
                    command_queue.put(("volume", (index, percent)))
                else:
                    logger.warning(f"Valori fuori range in SET_VOL: slot={index}, percent={percent}")
            else:
                logger.error(f"Formato SET_VOL non valido: {line}")

        elif line.startswith("MUTE:"):
            # Formato: MUTE:slot,state
            parts = line[5:].split(',')
            if len(parts) == 2:
                index = int(parts[0])
                mute_state = bool(int(parts[1]))
                if 0 <= index <= 2:
                    command_queue.put(("mute", (index, mute_state)))
                else:
                    logger.warning(f"Slot fuori range in MUTE: {index}")
            else:
                logger.error(f"Formato MUTE non valido: {line}")

        elif line == "GET_ALL_VOLS":
            send_volumes_to_esp32(ser)

        else:
            logger.warning(f"Comando sconosciuto: '{line}'")

    except (ValueError, IndexError) as e:
        logger.error(f"Errore nel parsing comando '{line}': {e}")
    except Exception as e:
        logger.error(f"Errore generico nel processare comando '{line}': {e}")

def main():
    """Funzione principale"""
    global volume_check_thread

    # INIZIALIZZA COM nel thread principale
    pythoncom.CoInitialize()
    logger.info("COM inizializzato nel thread principale")

    try:
        # Avvia worker thread
        worker_thread = threading.Thread(target=volume_worker, daemon=True)
        worker_thread.start()
        logger.info("Worker thread avviato")

        # Connessione seriale con retry
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                logger.info(f"Tentativo di connessione con l'ESP32... ({retry_count + 1}/{max_retries})")

                with serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1) as ser:
                    logger.info(f"Connesso e in ascolto su {SERIAL_PORT}")
                    retry_count = 0 # Reset su connessione riuscita

                    # INIZIALIZZAZIONE: Imposta tutti i canali a 0
                    time.sleep(2) # Attesa per inizializzazione ESP32
                    initialize_empty_channels(ser)

                    # Avvia il thread di monitoraggio volumi
                    volume_check_thread = threading.Thread(
                        target=volume_monitor_worker, 
                        args=(ser,), 
                        daemon=True
                    )
                    volume_check_thread.start()
                    logger.info("Thread monitoraggio volumi avviato")

                    # Invia volumi iniziali dopo l'inizializzazione
                    time.sleep(1)
                    send_volumes_to_esp32(ser)

                    # Loop principale
                    while True:
                        try:
                            if ser.in_waiting > 0:
                                line = ser.readline().decode('utf-8').strip()
                                if line:
                                    process_serial_command(line, ser)
                            time.sleep(0.01)
                        except UnicodeDecodeError:
                            logger.warning("Ricevuti dati seriali non validi")
                        except serial.SerialException as e:
                            logger.error(f"Errore di comunicazione seriale: {e}")
                            break
                        except Exception as e:
                            logger.error(f"Errore nel loop principale: {e}")
                            break

            except serial.SerialException as e:
                retry_count += 1
                logger.error(f"Errore connessione seriale: {e}")
                if retry_count < max_retries:
                    logger.info(f"Riprovo tra 3 secondi...")
                    time.sleep(3)
                else:
                    logger.error("Impossibile stabilire connessione seriale dopo tutti i tentativi")
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
        # CLEANUP COM nel thread principale
        pythoncom.CoUninitialize()
        logger.info("COM cleanup completato nel thread principale")
        logger.info("Programma terminato")

if __name__ == "__main__":
    main()
