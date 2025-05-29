# utils/dashboard_utils.py
import tkinter as tk # For tk.TclError
import threading # For type hint

import config # For default states (indirectly via TG handler status)
from logger import get_logger

logger = get_logger("Iri-shka_App.utils.DashboardUtils")

def get_dashboard_data_for_telegram(
    gui_ref, telegram_bot_handler_instance_ref, ollama_ready_flag: bool,
    whisper_handler_module_ref, tts_manager_module_ref,
    user_state_ref: dict, assistant_state_ref: dict, chat_history_ref: list,
    global_states_lock_ref: threading.Lock
    ) -> dict:
    # ... (content from thought process, ensure all refs are used)
    logger.debug("Gathering data for HTML dashboard...")
    component_statuses = {}
    def get_gui_status_text_and_type(label_attr_name: str, default_text="N/A", default_type="unknown"): # ...
        if gui_ref and hasattr(gui_ref, label_attr_name):
            label = getattr(gui_ref, label_attr_name)
            if label and hasattr(label, 'cget') and label.winfo_exists(): 
                try:
                    text = label.cget("text"); type_ = "unknown" 
                    # Simplified status logic (can be expanded as needed)
                    if any(kw.lower() in text.lower() for kw in ["rdy", "ok", "poll", "idle", "saved", "loaded", "fresh"]): type_ = "ready"
                    elif any(kw.lower() in text.lower() for kw in ["busy", "chk", "load", "ping", "thk"]): type_ = "busy"
                    elif any(kw.lower() in text.lower() for kw in ["err", "tmo", "nrdy", "bad", "con", "502", "no tok", "no adm", "initfail"]): type_ = "error"
                    elif any(kw.lower() in text.lower() for kw in ["off", "n/a"]): type_ = "off"
                    return text, type_
                except tk.TclError: return default_text, default_type
        return default_text, default_type

    component_statuses["act"] = get_gui_status_text_and_type("act_status_text_label", "ACT: N/A")
    # ... (similar for inet, webui, mem, vis, art) ...
    component_statuses["inet"] = get_gui_status_text_and_type("inet_status_text_label", "INET: N/A")
    component_statuses["webui"] = get_gui_status_text_and_type("webui_status_text_label", "WEBUI: N/A", "off") 
    component_statuses["mem"] = get_gui_status_text_and_type("memory_status_text_label", "MEM: N/A")
    component_statuses["vis"] = get_gui_status_text_and_type("vis_status_text_label", "VIS: N/A", "off")
    component_statuses["art"] = get_gui_status_text_and_type("art_status_text_label", "ART: N/A", "off")


    tele_text, tele_type = "TELE: N/A", "unknown" # ... telegram status ...
    if telegram_bot_handler_instance_ref: tele_status = telegram_bot_handler_instance_ref.get_status(); tele_text = f"TELE: {tele_status.upper()}"; tele_type = tele_status
    elif not config.TELEGRAM_BOT_TOKEN: tele_text, tele_type = "TELE: NO TOK", "no_token"
    elif not config.TELEGRAM_ADMIN_USER_ID: tele_text, tele_type = "TELE: NO ADM", "no_admin"
    component_statuses["tele"] = (tele_text, tele_type)

    component_statuses["hear"] = (f"HEAR: {whisper_handler_module_ref.get_status_short()}", whisper_handler_module_ref.get_status_type())
    component_statuses["voice"] = (f"VOICE: {tts_manager_module_ref.get_status_short()}", tts_manager_module_ref.get_status_type())
    component_statuses["mind"] = (f"MIND: {'RDY' if ollama_ready_flag else 'NRDY'}", "ready" if ollama_ready_flag else "error")
    
    app_overall_status_text = "Status Unavailable" # ... app overall status ...
    if gui_ref and hasattr(gui_ref, 'app_status_label') and gui_ref.app_status_label and gui_ref.app_status_label.winfo_exists():
        try: app_overall_status_text = gui_ref.app_status_label.cget("text")
        except tk.TclError: pass

    with global_states_lock_ref: # ... copy states ...
        current_admin_user_state = user_state_ref.copy()
        current_assistant_state_snapshot = assistant_state_ref.copy()
        current_admin_chat_history = chat_history_ref[:]

    return {"admin_user_state": current_admin_user_state, "assistant_state": current_assistant_state_snapshot,
            "admin_chat_history": current_admin_chat_history, "component_statuses": component_statuses,
            "app_overall_status": app_overall_status_text}