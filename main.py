# main.py
import tkinter as tk
import threading
import sys
import gc
import os
import logging
import queue
import asyncio
import datetime
import json
import time
from concurrent.futures import ThreadPoolExecutor
import uuid
import requests # For WebUI health check

# --- Setup Custom Logger ---
try:
    import logger as app_logger_module
    logger = app_logger_module.get_logger("Iri-shka_App.Main")
    web_logger = app_logger_module.get_logger("Iri-shka_App.WebApp") # web_logger for WebApp related logs from main
except ImportError as e:
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stderr)
    logger = logging.getLogger("Iri-shka_App.Main_Fallback"); web_logger = logging.getLogger("Iri-shka_App.WebApp_Fallback")
    logger.critical(f"Failed to import custom logger: {e}. Using basicConfig.", exc_info=True)

logger.info("--- APPLICATION MAIN.PY ENTRY POINT ---")

try:
    import config
    from utils import file_utils, state_manager, audio_processor, gpu_monitor
    from utils.telegram_handler import TelegramBotHandler, PYDUB_AVAILABLE as TELEGRAM_PYDUB_AVAILABLE
    from utils.customer_interaction_manager import CustomerInteractionManager

    from utils.web_app_bridge import WebAppBridge
    from utils.telegram_messaging_utils import initialize_telegram_audio_dependencies
    from utils.admin_interaction_processor import (
        process_gui_recorded_audio,
        process_admin_telegram_text_message as process_admin_tg_text_util,
        process_admin_telegram_voice_message as process_admin_tg_voice_util
    )
    from utils.customer_llm_processor import handle_customer_interaction_package as handle_customer_pkg_util
    from utils.initialization_manager import load_all_models_and_services as load_services_util
    from utils.dashboard_utils import get_dashboard_data_for_telegram as get_dashboard_data_util

    from utils import whisper_handler, tts_manager, ollama_handler
    from utils import telegram_messaging_utils as telegram_messaging_utils_module

    from gui_manager import GUIManager
    from webui.web_app import flask_app as actual_flask_app, WEB_UI_ENABLED_FLAG as web_app_internal_enabled_flag_ref
    logger.info("Core modules imported successfully.")
except ImportError as e_import:
    logger.critical(f"CRITICAL IMPORT ERROR in main.py: {e_import}", exc_info=True); sys.exit(1)
except Exception as e_gen_import:
    logger.critical(f"CRITICAL UNEXPECTED ERROR during core imports: {e_gen_import}", exc_info=True); sys.exit(1)

PydubAudioSegment_main = None
PydubExceptions_main = None
if config.TELEGRAM_REPLY_WITH_VOICE and TELEGRAM_PYDUB_AVAILABLE:
    try:
        from pydub import AudioSegment as PAS_imported, exceptions as PE_imported
        PydubAudioSegment_main = PAS_imported
        PydubExceptions_main = PE_imported
        initialize_telegram_audio_dependencies(PydubAudioSegment_main, PydubExceptions_main)
        logger.info("Pydub imported in main.py and passed to messaging utils.")
    except ImportError:
        logger.warning("Failed to import Pydub in main.py; TTS OGG conversion for Telegram will be disabled.")
        initialize_telegram_audio_dependencies(None, None)
else:
    logger.info("Pydub not available or Admin/Customer voice replies disabled, TTS OGG conversion for Telegram disabled.")
    initialize_telegram_audio_dependencies(None, None)

_whisper_module_for_load_audio = None
if whisper_handler.WHISPER_CAPABLE:
    try:
        import whisper
        _whisper_module_for_load_audio = whisper
        logger.info("OpenAI Whisper module imported in main.py for admin voice load_audio utility.")
    except ImportError:
        logger.warning("Failed to import OpenAI whisper module in main.py; Admin Telegram voice WAV loading might fail.")

gui: GUIManager = None
app_tk_instance: tk.Tk = None
_active_gpu_monitor: gpu_monitor.GPUMonitor = None # type: ignore
telegram_bot_handler_instance: TelegramBotHandler = None
customer_interaction_manager_instance: CustomerInteractionManager = None
admin_llm_message_queue = queue.Queue()
llm_task_executor: ThreadPoolExecutor = None
flask_thread_instance: threading.Thread = None # To keep a reference to the Flask thread

chat_history: list = []
user_state: dict = {}
assistant_state: dict = {}
global_states_lock = threading.Lock()
ollama_ready: bool = False
current_gui_theme: str = config.GUI_THEME_LIGHT
current_chat_font_size_applied: int = config.DEFAULT_CHAT_FONT_SIZE
gui_callbacks: dict = {}

_web_ui_should_be_running = config.ENABLE_WEB_UI
_web_ui_user_toggle_enabled = config.ENABLE_WEB_UI

def set_web_ui_enabled_state(enable: bool):
    global _web_ui_user_toggle_enabled
    if not config.ENABLE_WEB_UI and enable:
        logger.warning("WebUI cannot be enabled as it's disabled in the main .env configuration.")
        if gui_callbacks and callable(gui_callbacks.get('webui_status_update')):
            gui_callbacks['webui_status_update']("WEBUI: CFGOFF", "off")
        return

    _web_ui_user_toggle_enabled = enable
    web_app_internal_enabled_flag_ref.set_enabled_status(enable)
    logger.info(f"WebUI user toggle set to: {'Enabled' if enable else 'Disabled'}")

    if gui_callbacks and callable(gui_callbacks.get('webui_status_update')):
        if not config.ENABLE_WEB_UI:
            gui_callbacks['webui_status_update']("WEBUI: CFGOFF", "off")
        else:
            gui_callbacks['webui_status_update'](
                "WEBUI: ON" if _web_ui_user_toggle_enabled else "WEBUI: PAUSED",
                "active" if _web_ui_user_toggle_enabled else "disabled"
            )

def _enable_webui_action(): set_web_ui_enabled_state(True)
def _disable_webui_action(): set_web_ui_enabled_state(False)

def check_webui_health():
    global _web_ui_user_toggle_enabled
    if not config.ENABLE_WEB_UI:
        return "WEBUI: CFGOFF", "off"
    if not _web_ui_user_toggle_enabled:
        return "WEBUI: PAUSED", "disabled"

    if flask_thread_instance and flask_thread_instance.is_alive():
        try:
            response = requests.get(f"http://localhost:{config.WEB_UI_PORT}/health", timeout=2)
            if response.status_code == 200 and response.json().get("status") == "ok":
                return "WEBUI: ON", "active"
            else:
                return "WEBUI: UNHEALTHY", "error"
        except requests.ConnectionError:
            return "WEBUI: NO-CONN", "error"
        except requests.Timeout:
            return "WEBUI: TIMEOUT", "timeout"
        except Exception as e:
            logger.warning(f"WebUI health check failed: {e}")
            return "WEBUI: ERR", "error"
    return "WEBUI: OFF", "off"

def set_ollama_ready_main(is_ready: bool):
    global ollama_ready
    ollama_ready = is_ready

def on_gui_recording_finished(recorded_sample_rate):
    global chat_history, user_state, assistant_state, global_states_lock, ollama_ready
    global telegram_bot_handler_instance, audio_processor, whisper_handler, tts_manager
    global ollama_handler, state_manager, file_utils, telegram_messaging_utils_module

    try:
        process_gui_recorded_audio(
            recorded_sample_rate=recorded_sample_rate,
            chat_history_ref=chat_history, user_state_ref=user_state, assistant_state_ref=assistant_state,
            global_states_lock_ref=global_states_lock, gui_callbacks=gui_callbacks,
            telegram_bot_handler_instance_ref=telegram_bot_handler_instance,
            ollama_ready_flag=ollama_ready, audio_processor_module_ref=audio_processor,
            whisper_handler_module_ref=whisper_handler, tts_manager_module_ref=tts_manager,
            ollama_handler_module_ref=ollama_handler, state_manager_module_ref=state_manager,
            file_utils_module_ref=file_utils,
            telegram_messaging_utils_module_ref=telegram_messaging_utils_module
        )
    except Exception as e:
        logger.critical(f"CRITICAL ERROR in on_gui_recording_finished (from process_gui_recorded_audio): {e}", exc_info=True)
        error_message_short = str(e)[:100]
        if gui_callbacks:
            if callable(gui_callbacks.get('status_update')):
                gui_callbacks['status_update'](f"Error after rec: {error_message_short}")
            if callable(gui_callbacks.get('messagebox_error')):
                gui_callbacks['messagebox_error']("Processing Error", f"A critical error occurred while processing audio: {e}")
            if callable(gui_callbacks.get('act_status_update')):
                gui_callbacks['act_status_update']("ACT: IDLE", "idle") # Reset ACT status
            if callable(gui_callbacks.get('mind_status_update')): # Reset MIND status
                mind_text = "MIND: RDY" if ollama_ready else "MIND: ERR-CHK" # Indicate ready or check error
                mind_type = "ready" if ollama_ready else "error"
                gui_callbacks['mind_status_update'](mind_text, mind_type)
    finally:
        # This ensures the speak button is always reset to a sensible state after recording attempt.
        if gui_callbacks and callable(gui_callbacks.get('speak_button_update')):
            speak_btn_ready = whisper_handler.is_whisper_ready()
            gui_callbacks['speak_button_update'](speak_btn_ready, "Speak" if speak_btn_ready else "HEAR NRDY")


def handle_web_admin_interaction_result(bridge_result_data: dict):
    global chat_history, user_state, assistant_state, global_states_lock, current_gui_theme, current_chat_font_size_applied
    logger.info(f"MAIN: Processing WebAppBridge result. Transcription: '{bridge_result_data.get('user_transcription', '')[:50]}...'")

    user_state_snapshot: dict
    assistant_state_snapshot: dict
    with global_states_lock:
        user_state_snapshot = user_state.copy()
        assistant_state_snapshot = assistant_state.copy()

    if bridge_result_data.get("error_message"):
        logger.error(f"MAIN: WebAppBridge reported error: {bridge_result_data['error_message']}")
        if gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')):
            gui_callbacks['add_assistant_message_to_display'](
                f"[Web UI Error: {bridge_result_data['error_message']}]", is_error=True, source="web_admin_error"
            )

    llm_provided_user_state_changes = bridge_result_data.get("updated_user_state")
    if llm_provided_user_state_changes and isinstance(llm_provided_user_state_changes, dict):
        llm_theme_suggestion = llm_provided_user_state_changes.get("gui_theme", current_gui_theme)
        applied_theme_value = current_gui_theme
        if llm_theme_suggestion != current_gui_theme and llm_theme_suggestion in [config.GUI_THEME_LIGHT, config.GUI_THEME_DARK]:
            if gui and callable(gui_callbacks.get('apply_application_theme')):
                gui_callbacks['apply_application_theme'](llm_theme_suggestion)
                current_gui_theme = llm_theme_suggestion
                applied_theme_value = llm_theme_suggestion
        llm_provided_user_state_changes["gui_theme"] = applied_theme_value

        llm_font_size_str_suggestion = llm_provided_user_state_changes.get("chat_font_size", str(current_chat_font_size_applied))
        try: llm_font_size_as_int = int(llm_font_size_str_suggestion)
        except: llm_font_size_as_int = current_chat_font_size_applied
        clamped_font_size_suggestion = max(config.MIN_CHAT_FONT_SIZE, min(llm_font_size_as_int, config.MAX_CHAT_FONT_SIZE))
        applied_font_size_value = current_chat_font_size_applied
        if clamped_font_size_suggestion != current_chat_font_size_applied:
            if gui and callable(gui_callbacks.get('apply_chat_font_size')):
                gui_callbacks['apply_chat_font_size'](clamped_font_size_suggestion)
                current_chat_font_size_applied = clamped_font_size_suggestion
            applied_font_size_value = clamped_font_size_suggestion
        llm_provided_user_state_changes["chat_font_size"] = applied_font_size_value

        with global_states_lock:
            merged_user_state = user_state_snapshot.copy()
            merged_user_state.update(llm_provided_user_state_changes)
            user_state.clear(); user_state.update(merged_user_state)
    else:
        logger.warning("MAIN: WebAppBridge did not return a dictionary for updated_user_state. User state not modified by LLM this turn.")

    llm_provided_assistant_state_changes = bridge_result_data.get("updated_assistant_state")
    if llm_provided_assistant_state_changes and isinstance(llm_provided_assistant_state_changes, dict):
        with global_states_lock:
            merged_assistant_state = assistant_state_snapshot.copy()
            if "internal_tasks" in llm_provided_assistant_state_changes and isinstance(llm_provided_assistant_state_changes["internal_tasks"], dict):
                llm_tasks_dict = llm_provided_assistant_state_changes["internal_tasks"]
                if "internal_tasks" not in merged_assistant_state or not isinstance(merged_assistant_state.get("internal_tasks"), dict):
                    merged_assistant_state["internal_tasks"] = {"pending": [], "in_process": [], "completed": []}
                for task_type in ["pending", "in_process", "completed"]:
                    if task_type not in merged_assistant_state["internal_tasks"] or not isinstance(merged_assistant_state["internal_tasks"][task_type], list):
                        merged_assistant_state["internal_tasks"][task_type] = []
                    new_tasks_from_llm = llm_tasks_dict.get(task_type, [])
                    if not isinstance(new_tasks_from_llm, list): new_tasks_from_llm = [str(new_tasks_from_llm)]
                    existing_tasks_in_merged = merged_assistant_state["internal_tasks"][task_type]
                    merged_assistant_state["internal_tasks"][task_type] = list(dict.fromkeys(
                        [str(t) for t in existing_tasks_in_merged] + [str(t) for t in new_tasks_from_llm]
                    ))
            for key, val_llm in llm_provided_assistant_state_changes.items():
                if key != "internal_tasks":
                    merged_assistant_state[key] = val_llm
            assistant_state.clear(); assistant_state.update(merged_assistant_state)
    else:
        logger.warning("MAIN: WebAppBridge did not return a dictionary for updated_assistant_state. Assistant state not modified by LLM this turn.")

    updated_customer_state_from_bridge = bridge_result_data.get("updated_active_customer_state")
    if updated_customer_state_from_bridge and isinstance(updated_customer_state_from_bridge, dict):
        cust_id_in_state = updated_customer_state_from_bridge.get("user_id")
        if cust_id_in_state:
            if state_manager.save_customer_state(cust_id_in_state, updated_customer_state_from_bridge, gui_callbacks):
                logger.info(f"MAIN: Updated state for context customer {cust_id_in_state} via web interaction.")
        else: logger.warning("MAIN: Web bridge returned customer state without user_id.")

    new_chat_turn_from_bridge = bridge_result_data.get("new_chat_turn")
    if new_chat_turn_from_bridge and isinstance(new_chat_turn_from_bridge, dict):
        with global_states_lock:
            new_chat_turn_from_bridge["timestamp"] = state_manager.get_current_timestamp_iso()
            chat_history.append(new_chat_turn_from_bridge)

    with global_states_lock:
        updated_ch = state_manager.save_states(chat_history, user_state, assistant_state, gui_callbacks)
        if len(chat_history) != len(updated_ch): chat_history[:] = updated_ch

    if gui and gui_callbacks:
        with global_states_lock:
            if callable(gui_callbacks.get('update_chat_display_from_list')): gui_callbacks['update_chat_display_from_list'](chat_history)
            if callable(gui_callbacks.get('update_todo_list')): gui_callbacks['update_todo_list'](user_state.get("todos", []))
            if callable(gui_callbacks.get('update_calendar_events_list')): gui_callbacks['update_calendar_events_list'](user_state.get("calendar_events", []))
            asst_tasks_web = assistant_state.get("internal_tasks", {});
            if not isinstance(asst_tasks_web, dict): asst_tasks_web = {}
            if callable(gui_callbacks.get('update_kanban_pending')): gui_callbacks['update_kanban_pending'](asst_tasks_web.get("pending", []))
            if callable(gui_callbacks.get('update_kanban_in_process')): gui_callbacks['update_kanban_in_process'](asst_tasks_web.get("in_process", []))
            if callable(gui_callbacks.get('update_kanban_completed')): gui_callbacks['update_kanban_completed'](asst_tasks_web.get("completed", []))
    logger.info(f"MAIN: WebAppBridge result processing finished.")


def toggle_speaking_recording():
    if not audio_processor.is_recording_active():
        if not (whisper_handler.WHISPER_CAPABLE and whisper_handler.is_whisper_ready()):
            logger.warning("Cannot start recording: Whisper module not ready."); return
        if tts_manager.is_tts_loading(): logger.info("Cannot start recording: TTS loading."); return
        tts_manager.stop_current_speech(gui_callbacks)
        if audio_processor.start_recording(gui_callbacks):
            if gui_callbacks and callable(gui_callbacks.get('speak_button_update')):
                gui_callbacks['speak_button_update'](True, "Listening...")
    else:
        audio_processor.stop_recording() # This will trigger on_gui_recording_finished
        if gui_callbacks and callable(gui_callbacks.get('speak_button_update')):
            gui_callbacks['speak_button_update'](False, "Processing...")

def _unload_bark_model_action(): threading.Thread(target=lambda: tts_manager.unload_bark_model(gui_callbacks), daemon=True, name="UnloadBarkThread").start()
def _reload_bark_model_action(): threading.Thread(target=lambda: tts_manager.load_bark_resources(gui_callbacks), daemon=True, name="ReloadBarkThread").start()
def _unload_whisper_model_action(): threading.Thread(target=lambda: whisper_handler.unload_whisper_model(gui_callbacks), daemon=True, name="UnloadWhisperThread").start()
def _reload_whisper_model_action(): threading.Thread(target=lambda: whisper_handler.load_whisper_model(config.WHISPER_MODEL_SIZE, gui_callbacks), daemon=True, name="ReloadWhisperThread").start()
def _start_telegram_bot_action():
    if telegram_bot_handler_instance: telegram_bot_handler_instance.start_polling()
    else: logger.error("Cannot start Telegram: instance None.")
def _stop_telegram_bot_action():
    if telegram_bot_handler_instance: telegram_bot_handler_instance.stop_polling()
    else: logger.error("Cannot stop Telegram: instance None.")

def on_app_exit():
    global gui, app_tk_instance, _active_gpu_monitor, telegram_bot_handler_instance, llm_task_executor, flask_thread_instance
    logger.info("Application closing sequence initiated...")

    if llm_task_executor:
        logger.info("Shutting down LLM task thread pool..."); llm_task_executor.shutdown(wait=False, cancel_futures=True)
        llm_task_executor = None; logger.info("LLM task thread pool shutdown initiated.")
    if telegram_bot_handler_instance: logger.info("Shutting down Telegram bot..."); telegram_bot_handler_instance.full_shutdown()
    if _active_gpu_monitor: logger.info("Shutting down GPU monitor..."); _active_gpu_monitor.stop()
    logger.info("Shutting down audio resources..."); audio_processor.shutdown_audio_resources()
    if tts_manager.TTS_CAPABLE: logger.info("Shutting down TTS module..."); tts_manager.full_shutdown_tts_module()
    if whisper_handler.WHISPER_CAPABLE: logger.info("Cleaning up Whisper model..."); whisper_handler.full_shutdown_whisper_module()

    if gui:
        logger.info("Destroying GUI window...");
        if hasattr(gui, 'destroy_window') and callable(gui.destroy_window): gui.destroy_window()
        gui = None
    if app_tk_instance :
        try: app_tk_instance.destroy()
        except Exception as e: logger.warning(f"Error destroying app_tk_instance: {e}")
    app_tk_instance = None
    logger.info("Application exit sequence fully complete."); logging.shutdown()


def _periodic_status_and_task_checker():
    global customer_interaction_manager_instance, llm_task_executor, app_tk_instance, gui_callbacks

    if config.ENABLE_WEB_UI and gui_callbacks and callable(gui_callbacks.get('webui_status_update')):
        webui_text, webui_type = check_webui_health()
        gui_callbacks['webui_status_update'](webui_text, webui_type)

    if customer_interaction_manager_instance and llm_task_executor and not llm_task_executor._shutdown:
        try:
            expired_customer_ids = customer_interaction_manager_instance.check_and_get_expired_interactions()
            for customer_id in expired_customer_ids:
                if customer_id:
                    logger.info(f"MAIN_PERIODIC_CHECKER: Submitting customer {customer_id} for LLM processing.")
                    llm_task_executor.submit(
                        handle_customer_pkg_util,
                        customer_user_id=customer_id, chat_history_ref=chat_history, user_state_ref=user_state,
                        assistant_state_ref=assistant_state, global_states_lock_ref=global_states_lock,
                        gui_callbacks=gui_callbacks, telegram_bot_handler_instance_ref=telegram_bot_handler_instance,
                        state_manager_module_ref=state_manager, ollama_handler_module_ref=ollama_handler,
                        tts_manager_module_ref=tts_manager,
                        telegram_messaging_utils_module_ref=telegram_messaging_utils_module
                    )
        except Exception as e:
            logger.error(f"Error in customer interaction check part of periodic checker: {e}", exc_info=True)

    if app_tk_instance and hasattr(app_tk_instance, 'winfo_exists') and app_tk_instance.winfo_exists():
        app_tk_instance.after(config.CUSTOMER_INTERACTION_CHECK_INTERVAL_SECONDS * 1000, _periodic_status_and_task_checker)
    else:
        logger.info("_periodic_status_and_task_checker: Tk instance not available. Stopping periodic check.")


def _process_queued_admin_llm_messages():
    global admin_llm_message_queue
    try:
        while not admin_llm_message_queue.empty():
            item = admin_llm_message_queue.get_nowait()
            if not isinstance(item, tuple) or len(item)!=3: logger.error(f"Invalid Admin LLM queue item: {item}"); continue
            msg_type, user_id, data = item
            if msg_type == "telegram_text_admin":
                process_admin_tg_text_util(
                    user_id=user_id, text_message=data, chat_history_ref=chat_history, user_state_ref=user_state,
                    assistant_state_ref=assistant_state, global_states_lock_ref=global_states_lock,
                    gui_callbacks=gui_callbacks, telegram_bot_handler_instance_ref=telegram_bot_handler_instance,
                    ollama_ready_flag=ollama_ready, ollama_handler_module_ref=ollama_handler,
                    state_manager_module_ref=state_manager, tts_manager_module_ref=tts_manager,
                    telegram_messaging_utils_module_ref=telegram_messaging_utils_module
                )
            elif msg_type == "telegram_voice_admin_wav":
                process_admin_tg_voice_util(
                    user_id=user_id, wav_filepath=data, chat_history_ref=chat_history, user_state_ref=user_state,
                    assistant_state_ref=assistant_state, global_states_lock_ref=global_states_lock,
                    gui_callbacks=gui_callbacks, telegram_bot_handler_instance_ref=telegram_bot_handler_instance,
                    ollama_ready_flag=ollama_ready, whisper_handler_module_ref=whisper_handler,
                    _whisper_module_for_load_audio_ref=_whisper_module_for_load_audio,
                    ollama_handler_module_ref=ollama_handler, state_manager_module_ref=state_manager,
                    tts_manager_module_ref=tts_manager,
                    telegram_messaging_utils_module_ref=telegram_messaging_utils_module
                )
            else: logger.warning(f"Unknown Admin LLM message type: {msg_type}")
            admin_llm_message_queue.task_done()
    except queue.Empty: pass
    except Exception as e: logger.error(f"Error processing Admin LLM queue: {e}", exc_info=True)
    if app_tk_instance and hasattr(app_tk_instance, 'winfo_exists') and app_tk_instance.winfo_exists():
        app_tk_instance.after(300, _process_queued_admin_llm_messages)
    else: logger.info("_process_queued_admin_llm_messages: Tk instance not available. Stopping queue check.")


if __name__ == "__main__":
    logger.info("--- Main __name__ block started ---")
    logger.info(f"DEBUG MAIN: config.ENABLE_WEB_UI is {config.ENABLE_WEB_UI}")
    logger.info(f"DEBUG MAIN: config.WEB_UI_PORT is {config.WEB_UI_PORT}")

    folders_to_ensure = [
        config.DATA_FOLDER, config.OUTPUT_FOLDER, config.TELEGRAM_VOICE_TEMP_FOLDER,
        config.TELEGRAM_TTS_TEMP_FOLDER, config.CUSTOMER_STATES_FOLDER,
        os.path.join(config.DATA_FOLDER, "temp_dashboards"),
        config.WEB_UI_AUDIO_TEMP_FOLDER, config.WEB_UI_TTS_SERVE_FOLDER
    ]
    for folder_path in folders_to_ensure:
        if not file_utils.ensure_folder(folder_path, gui_callbacks=None):
            logger.critical(f"CRITICAL: Failed to create folder '{folder_path}'. Exiting.")
            sys.exit(1)

    logger.info("Loading initial states (admin & assistant)...")
    try:
        loaded_ch, loaded_us, loaded_as = state_manager.load_initial_states(gui_callbacks=None)
        with global_states_lock:
            chat_history = loaded_ch; user_state = loaded_us; assistant_state = loaded_as
    except Exception as e_state_load:
        logger.critical(f"CRITICAL ERROR loading initial states: {e_state_load}", exc_info=True); sys.exit(1)

    with global_states_lock:
        initial_theme_from_state = user_state.get("gui_theme", config.DEFAULT_USER_STATE["gui_theme"])
        current_gui_theme = initial_theme_from_state if initial_theme_from_state in [config.GUI_THEME_LIGHT, config.GUI_THEME_DARK] else config.GUI_THEME_LIGHT
        user_state["gui_theme"] = current_gui_theme
        initial_font_size_state = user_state.get("chat_font_size", config.DEFAULT_USER_STATE["chat_font_size"])
        try: initial_font_size_state = int(initial_font_size_state)
        except (ValueError, TypeError): initial_font_size_state = config.DEFAULT_CHAT_FONT_SIZE
        current_chat_font_size_applied = max(config.MIN_CHAT_FONT_SIZE, min(initial_font_size_state, config.MAX_CHAT_FONT_SIZE))
        user_state["chat_font_size"] = current_chat_font_size_applied

    logger.info("Initializing ThreadPoolExecutor for LLM tasks...")
    llm_task_executor = ThreadPoolExecutor(max_workers=config.LLM_TASK_THREAD_POOL_SIZE, thread_name_prefix="LLMTaskThread")
    logger.info("Initializing CustomerInteractionManager...")
    customer_interaction_manager_instance = CustomerInteractionManager()

    logger.info("Attempting to initialize Tkinter root & GUIManager...")
    try: app_tk_instance = tk.Tk()
    except Exception as e_tk_root:
        logger.critical(f"CRITICAL Tkinter root init: {e_tk_root}", exc_info=True); sys.exit(1)

    action_callbacks_for_gui = {
        'toggle_speaking_recording': toggle_speaking_recording, 'on_exit': on_app_exit,
        'unload_bark_model': _unload_bark_model_action, 'reload_bark_model': _reload_bark_model_action,
        'unload_whisper_model': _unload_whisper_model_action, 'reload_whisper_model': _reload_whisper_model_action,
        'start_telegram_bot': _start_telegram_bot_action, 'stop_telegram_bot': _stop_telegram_bot_action,
        'enable_webui': _enable_webui_action, 'disable_webui': _disable_webui_action,
    }
    try:
        gui = GUIManager(app_tk_instance, action_callbacks_for_gui,
                         initial_theme=current_gui_theme,
                         initial_font_size=current_chat_font_size_applied)
    except Exception as e_gui:
        logger.critical(f"CRITICAL GUIManager init: {e_gui}", exc_info=True)
        if app_tk_instance :
            try: app_tk_instance.destroy()
            except: pass
        sys.exit(1)

    if gui:
        callback_mapping = {
            'status_update': 'update_status_label', 'speak_button_update': 'update_speak_button',
            'act_status_update': 'update_act_status', 'inet_status_update': 'update_inet_status',
            'webui_status_update': 'update_webui_status', 'tele_status_update': 'update_tele_status',
            'memory_status_update': 'update_memory_status', 'hearing_status_update': 'update_hearing_status',
            'voice_status_update': 'update_voice_status', 'mind_status_update': 'update_mind_status',
            'vis_status_update': 'update_vis_status', 'art_status_update': 'update_art_status',
            'messagebox_error': 'show_error_messagebox', 'messagebox_info': 'show_info_messagebox',
            'messagebox_warn': 'show_warning_messagebox',
            'add_user_message_to_display': 'add_user_message_to_display',
            'add_assistant_message_to_display': 'add_assistant_message_to_display',
            'gpu_status_update_display': 'update_gpu_status_display',
            'update_todo_list': 'update_todo_list', 'update_calendar_events_list': 'update_calendar_events_list',
            'apply_application_theme': 'apply_theme', 'apply_chat_font_size': 'apply_chat_font_size',
            'update_chat_display_from_list': 'update_chat_display_from_list',
            'update_kanban_pending': 'update_kanban_pending',
            'update_kanban_in_process': 'update_kanban_in_process',
            'update_kanban_completed': 'update_kanban_completed'
        }
        for cb_key, method_name in callback_mapping.items():
            if hasattr(gui, method_name) and callable(getattr(gui, method_name)):
                gui_callbacks[cb_key] = getattr(gui, method_name)
        gui_callbacks['on_recording_finished'] = on_gui_recording_finished
    else: logger.error("GUI object is None, callbacks cannot be populated.")

    logger.info("Populating GUI with initial state data...")
    if gui and gui_callbacks:
        with global_states_lock:
            if callable(gui_callbacks.get('update_chat_display_from_list')): gui_callbacks['update_chat_display_from_list'](chat_history)
            if callable(gui_callbacks.get('update_todo_list')): gui_callbacks['update_todo_list'](user_state.get("todos", []))
            if callable(gui_callbacks.get('update_calendar_events_list')): gui_callbacks['update_calendar_events_list'](user_state.get("calendar_events", []))
            initial_asst_tasks = assistant_state.get("internal_tasks", {});
            if not isinstance(initial_asst_tasks, dict): initial_asst_tasks = {}
            if callable(gui_callbacks.get('update_kanban_pending')): gui_callbacks['update_kanban_pending'](initial_asst_tasks.get("pending", []))
            if callable(gui_callbacks.get('update_kanban_in_process')): gui_callbacks['update_kanban_in_process'](initial_asst_tasks.get("in_process", []))
            if callable(gui_callbacks.get('update_kanban_completed')): gui_callbacks['update_kanban_completed'](initial_asst_tasks.get("completed", []))

    logger.info("Initializing Telegram Bot Handler...")
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_ADMIN_USER_ID:
        try:
            telegram_bot_handler_instance = TelegramBotHandler(
                token=config.TELEGRAM_BOT_TOKEN, admin_user_id_str=config.TELEGRAM_ADMIN_USER_ID,
                message_queue_for_admin_llm=admin_llm_message_queue,
                customer_interaction_manager=customer_interaction_manager_instance,
                gui_callbacks=gui_callbacks,
                fn_get_dashboard_data=lambda: get_dashboard_data_util(
                    gui_ref=gui, telegram_bot_handler_instance_ref=telegram_bot_handler_instance,
                    ollama_ready_flag=ollama_ready, whisper_handler_module_ref=whisper_handler,
                    tts_manager_module_ref=tts_manager, user_state_ref=user_state,
                    assistant_state_ref=assistant_state, chat_history_ref=chat_history,
                    global_states_lock_ref=global_states_lock
                )
            )
            if config.START_BOT_ON_APP_START and telegram_bot_handler_instance:
                telegram_bot_handler_instance.start_polling()
        except ValueError:
             logger.error(f"TELEGRAM_ADMIN_USER_ID '{config.TELEGRAM_ADMIN_USER_ID}' is invalid. Bot disabled.")
             if gui_callbacks and callable(gui_callbacks.get('tele_status_update')): gui_callbacks['tele_status_update']("TELE: NO ADM", "no_admin")
        except Exception as e_tele_init:
            logger.error(f"Failed to initialize TelegramBotHandler: {e_tele_init}", exc_info=True)
            if gui_callbacks and callable(gui_callbacks.get('tele_status_update')): gui_callbacks['tele_status_update']("TELE: INITERR", "error")
    else:
        errmsg_tele = "Telegram Bot: "; status_key_tele, status_type_tele = "TELE: OFF", "off"
        if not config.TELEGRAM_BOT_TOKEN: errmsg_tele += "Token not set."; status_key_tele, status_type_tele = "TELE: NO TOK", "no_token"
        if not config.TELEGRAM_ADMIN_USER_ID:
            errmsg_tele += (" " if config.TELEGRAM_BOT_TOKEN else "") + "Admin User ID not set."
            if not (status_key_tele == "TELE: NO TOK" and not config.TELEGRAM_BOT_TOKEN): status_key_tele, status_type_tele = "TELE: NO ADM", "no_admin"
        logger.warning(f"{errmsg_tele} Telegram features will be disabled.")
        if gui_callbacks and callable(gui_callbacks.get('tele_status_update')): gui_callbacks['tele_status_update'](status_key_tele, status_type_tele)

    web_bridge_instance = None
    if config.ENABLE_WEB_UI:
        web_logger.info("Web UI is ENABLED. Initializing bridge and Flask thread...")
        web_bridge_instance = WebAppBridge(
            main_app_ollama_ready_flag_getter=lambda: ollama_ready,
            main_app_status_label_getter_fn=lambda: gui.app_status_label.cget("text") if gui and hasattr(gui, 'app_status_label') and gui.app_status_label and gui.app_status_label.winfo_exists() else "N/A",
            whisper_handler_module=whisper_handler, ollama_handler_module=ollama_handler,
            tts_manager_module=tts_manager, _whisper_module_for_load_audio_ref=_whisper_module_for_load_audio,
            state_manager_module_ref=state_manager, gui_callbacks_ref=gui_callbacks,
            fn_check_webui_health_main=check_webui_health
        )
        actual_flask_app.main_app_components['bridge'] = web_bridge_instance
        actual_flask_app.main_app_components['main_interaction_handler'] = handle_web_admin_interaction_result
        actual_flask_app.main_app_components['chat_history_ref'] = chat_history
        actual_flask_app.main_app_components['user_state_ref'] = user_state
        actual_flask_app.main_app_components['assistant_state_ref'] = assistant_state
        actual_flask_app.main_app_components['global_lock_ref'] = global_states_lock
        web_app_internal_enabled_flag_ref.set_enabled_status(_web_ui_user_toggle_enabled)

        if web_bridge_instance and telegram_bot_handler_instance:
            web_bridge_instance.telegram_handler_instance_ref = telegram_bot_handler_instance

        def run_flask_server_thread_target():
            try:
                web_logger.info(f"Attempting to start Flask web server on http://0.0.0.0:{config.WEB_UI_PORT}")
                actual_flask_app.run(host='0.0.0.0', port=config.WEB_UI_PORT, debug=False, use_reloader=False)
                web_logger.info("Flask server has stopped.")
            except Exception as e_flask_run:
                web_logger.critical(f"Flask server CRASHED or failed to start: {e_flask_run}", exc_info=True)
                if gui_callbacks and callable(gui_callbacks.get('webui_status_update')):
                    gui_callbacks['webui_status_update']("WEBUI: ERR", "error")

        flask_thread_instance = threading.Thread(target=run_flask_server_thread_target, daemon=True, name="FlaskWebUIServerThread")
        flask_thread_instance.start()
    else:
        web_logger.info("Web UI is DISABLED in config.")
        if gui_callbacks and callable(gui_callbacks.get('webui_status_update')):
             gui_callbacks['webui_status_update']("WEBUI: CFGOFF", "off")


    if gpu_monitor.PYNVML_AVAILABLE:
        _active_gpu_monitor = gpu_monitor.get_gpu_monitor_instance(gui_callbacks=gui_callbacks, update_interval=2)
        if _active_gpu_monitor and _active_gpu_monitor.active: _active_gpu_monitor.start()
    elif gui_callbacks and callable(gui_callbacks.get('gpu_status_update_display')):
        gui_callbacks['gpu_status_update_display']("N/A", "N/A", "na_nvml")

    logger.info("Starting model and services loader thread...")
    loader_thread = threading.Thread(
        target=load_services_util,
        args=(gui_callbacks, assistant_state, chat_history, telegram_bot_handler_instance,
              set_ollama_ready_main, whisper_handler, tts_manager, ollama_handler, state_manager,
              global_states_lock),
        daemon=True, name="ServicesLoaderThread"
    )
    loader_thread.start()

    if app_tk_instance and hasattr(app_tk_instance, 'winfo_exists') and app_tk_instance.winfo_exists():
        app_tk_instance.after(300, _process_queued_admin_llm_messages)
        app_tk_instance.after(1000, _periodic_status_and_task_checker)
    else: logger.error("Tkinter instance not available for scheduling periodic tasks.")

    logger.info("Starting Tkinter mainloop...")
    try:
        if app_tk_instance: app_tk_instance.mainloop()
        else: logger.critical("Cannot start mainloop: app_tk_instance is None."); on_app_exit(); sys.exit(1)
    except KeyboardInterrupt: logger.info("KeyboardInterrupt detected by mainloop. Initiating shutdown.")
    except tk.TclError as e_tcl:
        if "application has been destroyed" in str(e_tcl).lower(): logger.info("Tkinter mainloop TclError: Application already destroyed.")
        else: logger.error(f"Unhandled TclError in mainloop: {e_tcl}. Initiating shutdown.", exc_info=True)
    except Exception as e_mainloop: logger.critical(f"Unexpected critical error in Tkinter mainloop: {e_mainloop}", exc_info=True)
    finally:
        logger.info("Mainloop exited. Ensuring graceful shutdown via on_app_exit().")
        on_app_exit()
    logger.info("Application main thread has finished.")