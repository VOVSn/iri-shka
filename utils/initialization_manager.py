# utils/initialization_manager.py
import requests
import re
import threading # For type hint

import config
from logger import get_logger

logger = get_logger("Iri-shka_App.utils.InitializationManager")

def _parse_ollama_error_to_short_code(error_message_from_handler):
    # ... (same as in thought process)
    if not error_message_from_handler: return "NRDY", "error"
    lower_msg = error_message_from_handler.lower()
    if "timeout" in lower_msg: return "TMO", "timeout"
    if "connection" in lower_msg or "connect" in lower_msg : return "CON", "conn_error"
    if "502" in lower_msg: return "502", "http_502"
    http_match = re.search(r"http.*?(\d{3})", lower_msg)
    if http_match: code = http_match.group(1); return f"H{code}", "http_other"
    if "json" in lower_msg and ("invalid" in lower_msg or "not valid" in lower_msg) : return "JSON", "error"
    if "empty content" in lower_msg or "empty response" in lower_msg : return "EMP", "error"
    if "missing keys" in lower_msg : return "KEYS", "error"
    if "model not found" in lower_msg or "pull model" in lower_msg : return "NOMDL", "error"
    return "NRDY", "error"

def check_search_engine_status():
    # ... (content from thought process)
    logger.debug("LOADER: check_search_engine_status called.")
    try:
        response = requests.get(f"{config.SEARCH_ENGINE_URL.rstrip('/')}/search", params={'q': 'ping'}, timeout=config.SEARCH_ENGINE_PING_TIMEOUT)
        response.raise_for_status(); logger.info(f"Search Engine ping OK (Status {response.status_code}).")
        return "INET: RDY", "ready"
    except requests.exceptions.Timeout: logger.error("Search Engine ping timeout."); return "INET: TMO", "timeout"
    except requests.exceptions.RequestException as e: logger.error(f"Search Engine error: {e}"); return "INET: ERR", "error"

def load_all_models_and_services(
    gui_callbacks: dict, assistant_state_ref: dict, chat_history_ref: list,
    telegram_bot_handler_instance_ref, fn_set_ollama_ready_flag,
    whisper_handler_module_ref, tts_manager_module_ref,
    ollama_handler_module_ref, state_manager_module_ref,
    global_states_lock_ref: threading.Lock
    ):
    # ... (content from thought process, ensure all refs are used)
    logger.info("LOADER: --- Starting model and services loading/checking ---")
    def safe_gui_callback(callback_name, *args): # ... safe callback ...
        if gui_callbacks and callable(gui_callbacks.get(callback_name)):
            try: gui_callbacks[callback_name](*args)
            except Exception as e: logger.error(f"LOADER: Error in GUI cb '{callback_name}': {e}", exc_info=False)

    # ... initial GUI status updates ...
    safe_gui_callback('status_update', "Initializing components...")
    safe_gui_callback('act_status_update', "ACT: IDLE", "idle")
    webui_initial_status_text = "WEBUI: LOAD" if config.ENABLE_WEB_UI else "WEBUI: OFF"
    webui_initial_status_type = "loading" if config.ENABLE_WEB_UI else "off"
    safe_gui_callback('webui_status_update', webui_initial_status_text, webui_initial_status_type)
    for cb_name, text, status in [('inet_status_update', "INET: CHK", "checking"), # ... etc.
        ('memory_status_update', "MEM: CHK", "checking"), ('hearing_status_update', "HEAR: CHK", "loading"), 
        ('voice_status_update', "VOICE: CHK", "loading"), ('mind_status_update', "MIND: CHK", "pinging"), 
        ('tele_status_update', "TELE: CHK", "checking"), ('vis_status_update', "VIS: OFF", "off"), 
        ('art_status_update', "ART: OFF", "off")]: safe_gui_callback(cb_name, text, status)

    inet_short_text, inet_status_type = check_search_engine_status() # ... check inet ...
    safe_gui_callback('inet_status_update', inet_short_text, inet_status_type)
    
    with global_states_lock_ref: # ... set default admin_name in assistant_state_ref ...
        if "admin_name" not in assistant_state_ref: assistant_state_ref["admin_name"] = config.DEFAULT_ASSISTANT_STATE["admin_name"]
    
    safe_gui_callback('memory_status_update', "MEM: LOADED" if chat_history_ref else "MEM: FRESH", "loaded" if chat_history_ref else "fresh") 
    
    if whisper_handler_module_ref.WHISPER_CAPABLE: # ... load whisper ...
        whisper_handler_module_ref.load_whisper_model(config.WHISPER_MODEL_SIZE, gui_callbacks)
    else: safe_gui_callback('hearing_status_update', "HEAR: N/A", "na"); safe_gui_callback('speak_button_update', False, "HEAR N/A")
    
    if tts_manager_module_ref.TTS_CAPABLE: # ... load bark ...
        tts_manager_module_ref.load_bark_resources(gui_callbacks)
    else: safe_gui_callback('voice_status_update', "VOICE: N/A", "na")
    
    ollama_is_ready_now, ollama_log_msg = ollama_handler_module_ref.check_ollama_server_and_model() # ... check ollama ...
    fn_set_ollama_ready_flag(ollama_is_ready_now)
    if ollama_is_ready_now: safe_gui_callback('mind_status_update', "MIND: RDY", "ready")
    else: short_code_ollama, status_type_ollama = _parse_ollama_error_to_short_code(ollama_log_msg); safe_gui_callback('mind_status_update', f"MIND: {short_code_ollama}", status_type_ollama)

    current_tele_status_for_as = "off" # ... set telegram status in assistant_state_ref ...
    if telegram_bot_handler_instance_ref: current_tele_status_for_as = telegram_bot_handler_instance_ref.get_status()
    elif not config.TELEGRAM_BOT_TOKEN: current_tele_status_for_as = "no_token"
    elif not config.TELEGRAM_ADMIN_USER_ID: current_tele_status_for_as = "no_admin"
    with global_states_lock_ref: 
        assistant_state_ref["telegram_bot_status"] = current_tele_status_for_as
        state_manager_module_ref.save_assistant_state_only(assistant_state_ref.copy(), gui_callbacks) 

    # ... final GUI status updates ...
    if whisper_handler_module_ref.is_whisper_ready():
        ready_msg = "Ready.";
        if tts_manager_module_ref.is_tts_loading(): ready_msg = "Ready (TTS loading...)"
        elif not tts_manager_module_ref.is_tts_ready() and tts_manager_module_ref.TTS_CAPABLE: ready_msg = "Ready (TTS NRDY)."
        safe_gui_callback('status_update', ready_msg); safe_gui_callback('speak_button_update', True, "Speak")
    else: safe_gui_callback('status_update', "Hearing module not ready."); safe_gui_callback('speak_button_update', False, "HEAR NRDY")
    safe_gui_callback('act_status_update', "ACT: IDLE", "idle")
    logger.info("LOADER: --- Sequential model and services loading/checking thread finished ---")