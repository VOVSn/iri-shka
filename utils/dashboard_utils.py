# utils/dashboard_utils.py
import tkinter as tk # For tk.TclError
import threading # For type hint

import config # For default states (indirectly via TG handler status)
from logger import get_logger
# from gui_manager import GUIManager # Import for type hint if you prefer stricter typing

logger = get_logger("Iri-shka_App.utils.DashboardUtils")

def get_dashboard_data_for_telegram(
    gui_ref, # GUIManager instance
    telegram_bot_handler_instance_ref, 
    ollama_ready_flag: bool,
    whisper_handler_module_ref, 
    tts_manager_module_ref,
    user_state_ref: dict, 
    assistant_state_ref: dict, 
    chat_history_ref: list,
    global_states_lock_ref: threading.Lock
    ) -> dict:
    logger.debug("Gathering data for HTML dashboard...")
    component_statuses = {}

    # Mapping of component keys to their corresponding text label attribute names in GUIManager
    # and a default text prefix if the label somehow fails.
    gui_component_map = {
        "act": ("act_status_text_label", "ACT"),
        "inet": ("inet_status_text_label", "INET"),
        "webui": ("webui_status_text_label", "WEBUI"),
        # "tele" is handled separately below as it gets status from telegram_bot_handler_instance_ref
        "mem": ("memory_status_text_label", "MEM"),
        # "hear" and "voice" are handled by their respective modules' get_status methods
        # "mind" is handled by ollama_ready_flag
        "vis": ("vis_status_text_label", "VIS"),
        "art": ("art_status_text_label", "ART"),
    }

    if gui_ref:
        for key, (label_attr_name, default_prefix) in gui_component_map.items():
            text_label_widget = getattr(gui_ref, label_attr_name, None)
            text_content = f"{default_prefix}: N/A"
            if text_label_widget and hasattr(text_label_widget, 'cget') and text_label_widget.winfo_exists():
                try:
                    text_content = text_label_widget.cget("text")
                except tk.TclError:
                    logger.warning(f"TclError getting text for GUI label {label_attr_name}")
            
            # Get the semantic status type directly from GUIManager's stored types
            status_type = gui_ref.get_component_status_type(key) 
            component_statuses[key] = (text_content, status_type)
    else: # Fallback if gui_ref is None (should not happen in normal operation with GUI)
        for key, (_, default_prefix) in gui_component_map.items():
            component_statuses[key] = (f"{default_prefix}: GUI N/A", "unknown")


    # Handle Telegram status
    tele_text, tele_type = "TELE: N/A", "unknown"
    if telegram_bot_handler_instance_ref and hasattr(telegram_bot_handler_instance_ref, 'get_status'):
        tele_status_val = telegram_bot_handler_instance_ref.get_status()
        tele_text = f"TELE: {str(tele_status_val).upper()}"
        tele_type = str(tele_status_val).lower() # Use the status string itself as the type
    elif not config.TELEGRAM_BOT_TOKEN: 
        tele_text, tele_type = "TELE: NO TOK", "no_token"
    elif not config.TELEGRAM_ADMIN_USER_ID: 
        tele_text, tele_type = "TELE: NO ADM", "no_admin"
    else: # If instance exists but no get_status, or other conditions
        if gui_ref: # Try to get from GUI if TG handler is problematic but GUI might have it
            tele_text_from_gui = gui_ref.tele_status_text_label.cget("text") if gui_ref.tele_status_text_label else "TELE: N/A"
            tele_type_from_gui = gui_ref.get_component_status_type("tele")
            component_statuses["tele"] = (tele_text_from_gui, tele_type_from_gui)
        else:
            component_statuses["tele"] = (tele_text, tele_type) # Default if no GUI and problematic TG handler

    # If 'tele' wasn't set by the specific GUI fallback, set it now
    if "tele" not in component_statuses:
         component_statuses["tele"] = (tele_text, tele_type)


    # Handle components that have their own status reporting methods
    if whisper_handler_module_ref and hasattr(whisper_handler_module_ref, 'get_status_short') and hasattr(whisper_handler_module_ref, 'get_status_type'):
        component_statuses["hear"] = (
            f"HEAR: {whisper_handler_module_ref.get_status_short()}", 
            whisper_handler_module_ref.get_status_type()
        )
    else:
        if gui_ref: component_statuses["hear"] = (gui_ref.hearing_status_text_label.cget("text") if gui_ref.hearing_status_text_label else "HEAR: N/A", gui_ref.get_component_status_type("hear"))
        else: component_statuses["hear"] = ("HEAR: N/A", "unknown")

    if tts_manager_module_ref and hasattr(tts_manager_module_ref, 'get_status_short') and hasattr(tts_manager_module_ref, 'get_status_type'):
        component_statuses["voice"] = (
            f"VOICE: {tts_manager_module_ref.get_status_short()}", 
            tts_manager_module_ref.get_status_type()
        )
    else:
        if gui_ref: component_statuses["voice"] = (gui_ref.voice_status_text_label.cget("text") if gui_ref.voice_status_text_label else "VOICE: N/A", gui_ref.get_component_status_type("voice"))
        else: component_statuses["voice"] = ("VOICE: N/A", "unknown")

    # Mind status from ollama_ready_flag
    mind_status_type = "ready" if ollama_ready_flag else "error" # 'error' for NRDY
    mind_text = f"MIND: {'RDY' if ollama_ready_flag else 'NRDY'}"
    if gui_ref and gui_ref.mind_status_text_label: # Prefer GUI text if available as it might have more detail (e.g. error code)
        mind_text_from_gui = gui_ref.mind_status_text_label.cget("text")
        if mind_text_from_gui and mind_text_from_gui != "MIND: CHK": # Don't use "CHK" if GUI hasn't updated from initial
            mind_text = mind_text_from_gui
    component_statuses["mind"] = (mind_text, mind_status_type)
    
    app_overall_status_text = "Status Unavailable"
    if gui_ref and hasattr(gui_ref, 'app_status_label') and gui_ref.app_status_label and gui_ref.app_status_label.winfo_exists():
        try: 
            app_overall_status_text = gui_ref.app_status_label.cget("text")
        except tk.TclError: 
            logger.warning("TclError getting app_overall_status_text from GUI.")
            pass # Keep default "Status Unavailable"

    with global_states_lock_ref:
        current_admin_user_state = user_state_ref.copy()
        current_assistant_state_snapshot = assistant_state_ref.copy()
        current_admin_chat_history = chat_history_ref[:]

    # Ensure all expected component keys are in component_statuses, even if with defaults
    all_expected_keys = ["act", "inet", "webui", "tele", "mem", "hear", "voice", "mind", "vis", "art"]
    for comp_key in all_expected_keys:
        if comp_key not in component_statuses:
            default_text = f"{comp_key.upper()}: N/A"
            default_type = "unknown"
            if gui_ref and hasattr(gui_ref, f"{comp_key}_status_text_label"): # Final fallback to GUI text
                label = getattr(gui_ref, f"{comp_key}_status_text_label")
                if label and label.winfo_exists():
                    try: default_text = label.cget("text")
                    except: pass
                default_type = gui_ref.get_component_status_type(comp_key)

            component_statuses[comp_key] = (default_text, default_type)
            logger.debug(f"Dashboard: Fallback applied for component_status '{comp_key}'.")


    logger.debug(f"Final component_statuses for dashboard: {component_statuses}")
    return {
        "admin_user_state": current_admin_user_state, 
        "assistant_state": current_assistant_state_snapshot,
        "admin_chat_history": current_admin_chat_history, 
        "component_statuses": component_statuses,
        "app_overall_status": app_overall_status_text
    }