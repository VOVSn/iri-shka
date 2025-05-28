# main.py
import tkinter as tk
import threading
import sys
import gc
import os
import re
import logging
import requests
import queue
import asyncio
import datetime # For filename generation and timestamps
import json # For formatting prompts with JSON strings
import time # For the customer interaction checker loop
from concurrent.futures import ThreadPoolExecutor # For concurrent LLM tasks

import nltk
import numpy as np
import soundfile as sf

# --- Setup Custom Logger ---
try:
    import logger as app_logger_module
    logger = app_logger_module.get_logger("Iri-shka_App.Main")
except ImportError as e:
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stderr)
    logger = logging.getLogger("Iri-shka_App.Main_Fallback")
    logger.critical(f"Failed to import custom logger: {e}. Using basicConfig.", exc_info=True)
# --- End Custom Logger Setup ---

logger.info("--- APPLICATION MAIN.PY ENTRY POINT ---")

try:
    import config
    from utils import file_utils, state_manager, whisper_handler, ollama_handler, audio_processor, tts_manager
    from utils import gpu_monitor
    from utils.telegram_handler import TelegramBotHandler, PYDUB_AVAILABLE as TELEGRAM_PYDUB_AVAILABLE
    from utils.customer_interaction_manager import CustomerInteractionManager
    from utils.html_dashboard_generator import generate_dashboard_html
    from gui_manager import GUIManager
    logger.info("Core modules imported successfully.")
except ImportError as e_import:
    logger.critical(f"CRITICAL IMPORT ERROR in main.py: {e_import}", exc_info=True); sys.exit(1)
except Exception as e_gen_import:
    logger.critical(f"CRITICAL UNEXPECTED ERROR during core imports: {e_gen_import}", exc_info=True); sys.exit(1)

PydubAudioSegment = None
PydubExceptions = None
if config.TELEGRAM_REPLY_WITH_VOICE and TELEGRAM_PYDUB_AVAILABLE:
    try:
        from pydub import AudioSegment as PydubAudioSegment_imported
        from pydub import exceptions as PydubExceptions_imported
        PydubAudioSegment = PydubAudioSegment_imported
        PydubExceptions = PydubExceptions_imported
        logger.info("Pydub (AudioSegment, exceptions) imported in main.py for TTS OGG conversion (Admin).")
    except ImportError:
        logger.warning("Failed to import Pydub in main.py; TTS OGG for Admin will be disabled.")
else:
    logger.info("Pydub not available or Admin voice replies disabled, TTS OGG conversion for Admin disabled.")

_whisper_module_for_load_audio = None
if whisper_handler.WHISPER_CAPABLE:
    try:
        import whisper
        _whisper_module_for_load_audio = whisper
        logger.info("Whisper module imported in main.py for admin voice load_audio utility.")
    except ImportError:
        logger.warning("Failed to import whisper in main.py; Admin Telegram voice WAV loading might fail.")

gui: GUIManager = None # type: ignore
app_tk_instance: tk.Tk = None # type: ignore
_active_gpu_monitor = None
telegram_bot_handler_instance: TelegramBotHandler = None # type: ignore
customer_interaction_manager_instance: CustomerInteractionManager = None # type: ignore
admin_llm_message_queue = queue.Queue()
llm_task_executor: ThreadPoolExecutor = None # type: ignore
assistant_state_lock = threading.Lock()
chat_history: list = []
user_state: dict = {}
assistant_state: dict = {}
ollama_ready: bool = False
current_gui_theme: str = config.GUI_THEME_LIGHT
current_chat_font_size_applied: int = config.DEFAULT_CHAT_FONT_SIZE
gui_callbacks: dict = {}

def _parse_ollama_error_to_short_code(error_message_from_handler):
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

def _handle_admin_llm_interaction(input_text, source="gui", detected_language_code=None):
    global chat_history, user_state, assistant_state, ollama_ready
    global current_gui_theme, current_chat_font_size_applied

    function_signature_for_log = f"_handle_admin_llm_interaction(source={source}, input='{input_text[:30]}...')"
    logger.info(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Starting.")
    assistant_state_snapshot_for_prompt = {}
    with assistant_state_lock:
        assistant_state_snapshot_for_prompt = assistant_state.copy()

    current_lang_code_for_state = "en" # Default
    if detected_language_code and detected_language_code in ["ru", "en"]:
        current_lang_code_for_state = detected_language_code
    elif assistant_state_snapshot_for_prompt.get("last_used_language") in ["ru", "en"]:
        current_lang_code_for_state = assistant_state_snapshot_for_prompt.get("last_used_language")

    selected_bark_voice_preset = config.BARK_VOICE_PRESET_EN
    language_instruction_for_llm = config.LANGUAGE_INSTRUCTION_NON_RUSSIAN
    if current_lang_code_for_state == "ru":
        selected_bark_voice_preset = config.BARK_VOICE_PRESET_RU
        language_instruction_for_llm = config.LANGUAGE_INSTRUCTION_RUSSIAN

    # Display user message on GUI immediately
    if gui_callbacks and callable(gui_callbacks.get('add_user_message_to_display')):
        # For GUI source, detected_language_code might be added to input_text by whisper_handler for display
        # For TG source, this is just the raw text.
        display_text = input_text
        if source == "gui" and detected_language_code: # Only for GUI direct input with lang detection
            # This logic might be better handled in add_user_message_to_display itself if we want (Lang:xx) for GUI
            pass # Input_text already contains it if from Whisper with language
        gui_callbacks['add_user_message_to_display'](display_text, source=source)


    if gui_callbacks and callable(gui_callbacks.get('status_update')):
        gui_callbacks['status_update']("Thinking (Admin)...")
    if gui_callbacks and callable(gui_callbacks.get('mind_status_update')):
        gui_callbacks['mind_status_update']("MIND: THK", "thinking")

    target_customer_id_for_prompt = None
    customer_state_for_prompt_str = "{}"
    is_customer_context_active_for_prompt = False

    # Scan recent chat history for customer context (from assistant's system reports)
    history_to_scan_for_customer_context = []
    with assistant_state_lock: # Access global chat_history under lock
        scan_length = config.MAX_HISTORY_TURNS // 2
        if scan_length < 1: scan_length = 1
        start_index = max(0, len(chat_history) - scan_length)
        history_to_scan_for_customer_context = chat_history[start_index:]

    for turn in reversed(history_to_scan_for_customer_context):
        assistant_message = turn.get("assistant", "")
        turn_source = turn.get("source", "") # Check the source of the turn
        if turn_source == "customer_summary_internal": # This identifies a system report about a customer
            match_summary = re.search(r"\[Сводка по клиенту (\d+)\]", assistant_message)
            if match_summary:
                try:
                    target_customer_id_for_prompt = int(match_summary.group(1))
                    logger.info(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Found customer context ID {target_customer_id_for_prompt} from 'customer_summary_internal'.")
                    break
                except ValueError:
                    logger.warning(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Found non-integer customer ID in summary: {match_summary.group(1)}")
                    target_customer_id_for_prompt = None # Reset if invalid

    if target_customer_id_for_prompt:
        try:
            loaded_customer_state = state_manager.load_or_initialize_customer_state(target_customer_id_for_prompt, gui_callbacks)
            if loaded_customer_state and loaded_customer_state.get("user_id") == target_customer_id_for_prompt :
                customer_state_for_prompt_str = json.dumps(loaded_customer_state, indent=2, ensure_ascii=False)
                is_customer_context_active_for_prompt = True
                logger.debug(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Loaded state for context customer {target_customer_id_for_prompt}.")
            else:
                logger.warning(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Failed to load valid state for context customer {target_customer_id_for_prompt} or ID mismatch. Resetting context.")
                target_customer_id_for_prompt = None; customer_state_for_prompt_str = "{}"; is_customer_context_active_for_prompt = False
        except Exception as e_load_ctx_cust:
            logger.error(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Exception loading state for context customer {target_customer_id_for_prompt}: {e_load_ctx_cust}", exc_info=True)
            target_customer_id_for_prompt = None; customer_state_for_prompt_str = "{}"; is_customer_context_active_for_prompt = False
    else:
        logger.debug(f"ADMIN_LLM_FLOW: {function_signature_for_log} - No active customer context identified from chat history.")

    assistant_state_for_this_prompt = assistant_state_snapshot_for_prompt.copy()
    assistant_state_for_this_prompt["last_used_language"] = current_lang_code_for_state # Ensure LLM knows current language context
    admin_current_name = assistant_state_for_this_prompt.get("admin_name", "Partner") # Get current name from assistant's perspective

    format_kwargs_for_ollama = {
        "admin_name_value": admin_current_name, # Name LLM should use for admin in its response
        "assistant_admin_name_current_value": admin_current_name, # Admin's name as currently stored in assistant state
        "is_customer_context_active": is_customer_context_active_for_prompt,
        "active_customer_id": str(target_customer_id_for_prompt) if target_customer_id_for_prompt else "N/A",
        "active_customer_state_string": customer_state_for_prompt_str
    }
    expected_keys_for_response = ["answer_to_user", "updated_user_state", "updated_assistant_state", "updated_active_customer_state"]

    logger.debug(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Calling Ollama. CustContext Active: {is_customer_context_active_for_prompt}, CustID: {target_customer_id_for_prompt}")
    ollama_data, ollama_error = ollama_handler.call_ollama_for_chat_response(
        prompt_template_to_use=config.OLLAMA_PROMPT_TEMPLATE,
        transcribed_text=input_text, # This is the admin's direct input
        current_chat_history=chat_history, # Pass the global admin chat history
        current_user_state=user_state, # Pass the global admin user state
        current_assistant_state=assistant_state_for_this_prompt, # Pass the potentially modified assistant state for this call
        language_instruction=language_instruction_for_llm,
        format_kwargs=format_kwargs_for_ollama,
        expected_keys_override=expected_keys_for_response
    )

    # Prepare current turn for history (user part)
    current_turn_for_history = {"user": input_text, "source": source, "timestamp": state_manager.get_current_timestamp_iso()}
    if source == "gui" and detected_language_code:
        current_turn_for_history["detected_language_code_for_gui_display"] = detected_language_code
    elif source == "telegram_voice_admin" and detected_language_code: # Match source from process_admin_telegram_voice_message
         current_turn_for_history["detected_language_code_for_tele_voice_display"] = detected_language_code


    if ollama_error:
        logger.error(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Ollama call failed: {ollama_error}")
        ollama_ready = False
        short_code, status_type = _parse_ollama_error_to_short_code(ollama_error)
        if gui_callbacks and callable(gui_callbacks.get('mind_status_update')): gui_callbacks['mind_status_update'](f"MIND: {short_code}", status_type)
        if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update'](f"LLM Error (Admin): {ollama_error[:50]}")

        assistant_response_text = "An internal error occurred while processing your request (admin)."
        if current_lang_code_for_state == "ru": assistant_response_text = "При обработке вашего запроса (админ) произошла внутренняя ошибка."

        current_turn_for_history["assistant"] = f"[LLM Error: {assistant_response_text}]" # Add error to history
        if gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')):
            gui_callbacks['add_assistant_message_to_display'](assistant_response_text, is_error=True, source=source) # Display error on GUI
    else:
        logger.info(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Ollama call successful.")
        ollama_ready = True
        if gui_callbacks and callable(gui_callbacks.get('mind_status_update')): gui_callbacks['mind_status_update']("MIND: RDY", "ready")

        assistant_response_text = ollama_data.get("answer_to_user", "Error: LLM did not provide an answer.")
        new_admin_user_state_from_llm = ollama_data.get("updated_user_state", {})
        new_assistant_state_changes_from_llm = ollama_data.get("updated_assistant_state", {})
        updated_customer_state_from_llm = ollama_data.get("updated_active_customer_state")

        # --- Apply Admin User State Changes (Theme, Font Size) ---
        current_gui_theme_from_llm = new_admin_user_state_from_llm.get("gui_theme", current_gui_theme)
        if current_gui_theme_from_llm != current_gui_theme and current_gui_theme_from_llm in [config.GUI_THEME_LIGHT, config.GUI_THEME_DARK]:
            if gui and callable(gui_callbacks.get('apply_application_theme')):
                gui_callbacks['apply_application_theme'](current_gui_theme_from_llm)
                current_gui_theme = current_gui_theme_from_llm # Update global tracker
        new_admin_user_state_from_llm["gui_theme"] = current_gui_theme # Ensure state reflects applied theme

        current_font_size_from_llm = new_admin_user_state_from_llm.get("chat_font_size", current_chat_font_size_applied)
        try: current_font_size_from_llm = int(current_font_size_from_llm)
        except (ValueError, TypeError): current_font_size_from_llm = current_chat_font_size_applied
        clamped_font_size = max(config.MIN_CHAT_FONT_SIZE, min(current_font_size_from_llm, config.MAX_CHAT_FONT_SIZE))
        if clamped_font_size != current_font_size_from_llm : current_font_size_from_llm = clamped_font_size # Update if clamped
        if current_font_size_from_llm != current_chat_font_size_applied:
            if gui and callable(gui_callbacks.get('apply_chat_font_size')):
                gui_callbacks['apply_chat_font_size'](current_font_size_from_llm)
                current_chat_font_size_applied = current_font_size_from_llm # Update global tracker
        new_admin_user_state_from_llm["chat_font_size"] = current_chat_font_size_applied # Ensure state reflects applied font

        # Update global user_state (admin's state)
        user_state.clear(); user_state.update(new_admin_user_state_from_llm)

        # --- Apply Assistant State Changes ---
        with assistant_state_lock:
            # Load current global assistant state to merge changes carefully
            current_global_assistant_state = state_manager.load_assistant_state_only(gui_callbacks)
            for key, value_from_llm in new_assistant_state_changes_from_llm.items():
                if key == "internal_tasks" and isinstance(value_from_llm, dict) and isinstance(current_global_assistant_state.get(key), dict):
                    for task_type in ["pending", "in_process", "completed"]:
                        new_tasks = value_from_llm.get(task_type, [])
                        if not isinstance(new_tasks, list): new_tasks = [str(new_tasks)] # Ensure list of strings
                        existing_tasks = current_global_assistant_state[key].get(task_type, [])
                        if not isinstance(existing_tasks, list): existing_tasks = [str(existing_tasks)] # Ensure list of strings
                        # Merge tasks, avoiding duplicates, keeping order
                        current_global_assistant_state[key][task_type] = list(dict.fromkeys([str(t) for t in existing_tasks] + [str(t) for t in new_tasks]))
                else:
                    current_global_assistant_state[key] = value_from_llm
            current_global_assistant_state["last_used_language"] = current_lang_code_for_state # Persist language used
            assistant_state.clear()
            assistant_state.update(current_global_assistant_state)
            state_manager.save_assistant_state_only(assistant_state, gui_callbacks) # Save updated global assistant state
            logger.debug(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Global assistant_state updated and saved.")

        # --- Update GUI based on new states ---
        if gui_callbacks:
            if callable(gui_callbacks.get('update_todo_list')): gui_callbacks['update_todo_list'](user_state.get("todos", []))
            if callable(gui_callbacks.get('update_calendar_events_list')):
                logger.debug(f"ADMIN_LLM_FLOW: Updating GUI admin calendar with: {user_state.get('calendar_events', [])}")
                gui_callbacks['update_calendar_events_list'](user_state.get("calendar_events", []))

            asst_tasks = assistant_state.get("internal_tasks", {});
            if not isinstance(asst_tasks, dict): asst_tasks = {} # Ensure it's a dict
            if callable(gui_callbacks.get('update_kanban_pending')): gui_callbacks['update_kanban_pending'](asst_tasks.get("pending", []))
            if callable(gui_callbacks.get('update_kanban_in_process')): gui_callbacks['update_kanban_in_process'](asst_tasks.get("in_process", []))
            if callable(gui_callbacks.get('update_kanban_completed')): gui_callbacks['update_kanban_completed'](asst_tasks.get("completed", []))

        # --- Handle Customer State Update (if LLM provided one) ---
        if updated_customer_state_from_llm and isinstance(updated_customer_state_from_llm, dict) and target_customer_id_for_prompt:
            if updated_customer_state_from_llm.get("user_id") == target_customer_id_for_prompt:
                if state_manager.save_customer_state(target_customer_id_for_prompt, updated_customer_state_from_llm, gui_callbacks):
                    logger.info(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Updated state for context customer {target_customer_id_for_prompt}.")
                    # If the customer's calendar was updated AND this might affect the admin's view (e.g., admin is an attendee),
                    # the LLM should have ALSO updated admin's calendar in `updated_user_state`.
                    # The admin GUI calendar update above (`gui_callbacks['update_calendar_events_list'](user_state.get("calendar_events", []))`)
                    # will reflect any changes to the admin's own calendar.
            else:
                logger.warning(f"ADMIN_LLM_FLOW: {function_signature_for_log} - LLM returned updated_active_customer_state for mismatched ID (Expected {target_customer_id_for_prompt}, Got {updated_customer_state_from_llm.get('user_id')}). Not saving customer state.")
        elif updated_customer_state_from_llm is not None and updated_customer_state_from_llm != {}: # Check for non-null and non-empty dict
             logger.warning(f"ADMIN_LLM_FLOW: {function_signature_for_log} - 'updated_active_customer_state' from LLM was not null/empty but invalid or no target_customer_id_for_prompt. Value: {updated_customer_state_from_llm}")

        current_turn_for_history["assistant"] = assistant_response_text # Add assistant's response to current turn

        # Display assistant message on GUI (if not already handled for TG source)
        if source == "gui" and gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')):
             gui_callbacks['add_assistant_message_to_display'](assistant_response_text, source=source)
        elif (source == "telegram_admin" or source == "telegram_voice_admin") and gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')):
            # Also display on GUI what was sent to Admin TG
            gui_callbacks['add_assistant_message_to_display'](assistant_response_text, source=source) # source identifies it as "to Telegram"
            if callable(gui_callbacks.get('status_update')):
                gui_callbacks['status_update'](f"Iri-shka (to Admin TG): {assistant_response_text[:40]}...")


    # --- Save all states and update chat history display ---
    with assistant_state_lock:
        chat_history.append(current_turn_for_history)
        chat_history = state_manager.save_states(chat_history, user_state, assistant_state.copy(), gui_callbacks) # Save all global states
    if gui_callbacks and callable(gui_callbacks.get('memory_status_update')):
        gui_callbacks['memory_status_update']("MEM: SAVED", "saved") # Use 'saved' not 'ready'
    if gui and callable(gui_callbacks.get('update_chat_display_from_list')): # Update GUI chat display
        gui_callbacks['update_chat_display_from_list'](chat_history)

    # --- Speak response or send to Telegram ---
    if source == "gui":
        if tts_manager.is_tts_ready():
            def _deferred_gui_display_on_playback_admin(): # Callback for when TTS actually starts
                if gui_callbacks and callable(gui_callbacks.get('status_update')):
                    gui_callbacks['status_update'](f"Speaking (Admin): {assistant_response_text[:40]}...")
            current_persona_name = "Iri-shka"
            with assistant_state_lock: current_persona_name = assistant_state.get("persona_name", "Iri-shka")
            tts_manager.start_speaking_response(
                assistant_response_text, current_persona_name, selected_bark_voice_preset, gui_callbacks,
                on_actual_playback_start_gui_callback=_deferred_gui_display_on_playback_admin)
    elif source == "telegram_admin" or source == "telegram_voice_admin":
        logger.info(f"ADMIN_LLM_FLOW: Output handler for source '{source}'. Attempting Telegram reply to admin.")
        if telegram_bot_handler_instance and telegram_bot_handler_instance.async_loop and config.TELEGRAM_ADMIN_USER_ID:
            try:
                admin_id_int = int(config.TELEGRAM_ADMIN_USER_ID)
                logger.info(f"ADMIN_LLM_FLOW: Replying to Admin ID: {admin_id_int}. TextEnabled: {config.TELEGRAM_REPLY_WITH_TEXT}, VoiceEnabled: {config.TELEGRAM_REPLY_WITH_VOICE}")
                if config.TELEGRAM_REPLY_WITH_TEXT:
                    logger.info(f"ADMIN_LLM_FLOW: Sending TEXT reply to admin: '{assistant_response_text[:70]}...'")
                    text_send_future = asyncio.run_coroutine_threadsafe(
                        telegram_bot_handler_instance.send_text_message_to_user(admin_id_int, assistant_response_text),
                        telegram_bot_handler_instance.async_loop)
                    text_send_future.result(timeout=15) # Wait for completion
                    logger.info(f"ADMIN_LLM_FLOW: Text reply sent to admin TG.")
                else: logger.info(f"ADMIN_LLM_FLOW: Text reply to admin disabled by config.")

                if config.TELEGRAM_REPLY_WITH_VOICE:
                    logger.info(f"ADMIN_LLM_FLOW: Sending VOICE reply to admin. Lang: {current_lang_code_for_state}, Preset: {selected_bark_voice_preset}")
                    _send_voice_reply_to_telegram_user(admin_id_int, assistant_response_text, selected_bark_voice_preset) # This is synchronous in its TTS part
                    logger.info(f"ADMIN_LLM_FLOW: Voice reply process for admin TG completed.")
                else: logger.info(f"ADMIN_LLM_FLOW: Voice reply to admin disabled by config.")
            except asyncio.TimeoutError as te_async:
                logger.error(f"ADMIN_LLM_FLOW: Timeout sending reply to admin Telegram ({source}). Error: {te_async}", exc_info=True)
            except Exception as e_tg_send_admin:
                logger.error(f"ADMIN_LLM_FLOW: Error sending reply to admin Telegram ({source}): {e_tg_send_admin}", exc_info=True)
        else:
            missing_parts = [];
            if not telegram_bot_handler_instance: missing_parts.append("TGHandler")
            if not (telegram_bot_handler_instance and telegram_bot_handler_instance.async_loop): missing_parts.append("TGLoop")
            if not config.TELEGRAM_ADMIN_USER_ID: missing_parts.append("AdminID")
            logger.warning(f"ADMIN_LLM_FLOW: Cannot send reply to Admin TG. Missing: {', '.join(missing_parts)}. Source: {source}")

    # Final GUI state update for speak button and status label
    if gui_callbacks:
        enable_speak_btn = whisper_handler.is_whisper_ready()
        if callable(gui_callbacks.get('speak_button_update')):
            gui_callbacks['speak_button_update'](enable_speak_btn, "Speak" if enable_speak_btn else "HEAR NRDY")
        # Only update status to "Ready" if TTS is not currently speaking (or about to speak)
        is_speaking_gui = tts_manager.current_tts_thread and tts_manager.current_tts_thread.is_alive()
        if callable(gui_callbacks.get('status_update')) and not is_speaking_gui:
             gui_callbacks['status_update']("Ready (Admin)." if enable_speak_btn else "Hearing N/A (Admin).")
    logger.info(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Finished.")


def _send_voice_reply_to_telegram_user(target_user_id: int, text_to_speak: str, bark_voice_preset: str):
    if not (telegram_bot_handler_instance and telegram_bot_handler_instance.async_loop and
            tts_manager.is_tts_ready() and PydubAudioSegment and nltk and np and sf):
        missing = []
        if not tts_manager.is_tts_ready(): missing.append("TTS")
        if not PydubAudioSegment: missing.append("Pydub") # Pydub is needed for OGG conversion
        if not nltk: missing.append("NLTK")
        logger.warning(f"Cannot send voice reply to {target_user_id}: Missing deps ({', '.join(missing)}). Text: '{text_to_speak[:30]}'")
        return
    logger.info(f"Synthesizing voice for user {target_user_id} (Preset: {bark_voice_preset}): '{text_to_speak[:50]}...'")
    ts_suffix = datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')
    temp_tts_merged_wav_path = os.path.join(config.TELEGRAM_TTS_TEMP_FOLDER, f"tts_u{target_user_id}_merged_{ts_suffix}.wav")
    temp_tts_ogg_path = os.path.join(config.TELEGRAM_TTS_TEMP_FOLDER, f"tts_u{target_user_id}_reply_{ts_suffix}.ogg")
    
    bark_tts_engine = tts_manager.get_bark_model_instance()
    if not bark_tts_engine:
        logger.error(f"Could not get Bark instance for user {target_user_id}. Skipping voice.")
        return

    lang_for_nltk = 'english' if 'en_' in bark_voice_preset.lower() else 'russian'
    try: nltk.data.find(f'tokenizers/punkt/PY3/{lang_for_nltk}.pickle')
    except LookupError:
        try: nltk.download('punkt', quiet=True)
        except Exception as e_nltk_dl: logger.error(f"Failed to download NLTK 'punkt' for {lang_for_nltk}: {e_nltk_dl}. TTS quality might be affected.")
    
    try: sentences = nltk.sent_tokenize(text_to_speak, language=lang_for_nltk)
    except Exception as e_nltk_sent: sentences = [text_to_speak]; logger.warning(f"NLTK sentence tokenization failed: {e_nltk_sent}. Using full text as one chunk.")
    
    text_chunks = []; current_batch = []
    for i, s in enumerate(sentences):
        current_batch.append(s)
        if len(current_batch) >= config.BARK_MAX_SENTENCES_PER_CHUNK or (i + 1) == len(sentences):
            text_chunks.append(" ".join(current_batch)); current_batch = []
    
    all_audio_pieces = []; target_sr = None; first_valid_chunk = False
    for idx, chunk_text in enumerate(text_chunks):
        audio_arr, sr = bark_tts_engine.synthesize_speech_to_array(
            chunk_text, generation_params={"voice_preset": bark_voice_preset}
        )
        if audio_arr is not None and sr is not None:
            if target_sr is None: target_sr = sr
            if sr != target_sr: logger.warning(f"Samplerate mismatch in TTS chunks (expected {target_sr}, got {sr}). Skipping chunk."); continue
            if first_valid_chunk and config.BARK_SILENCE_DURATION_MS > 0:
                silence = np.zeros(int(config.BARK_SILENCE_DURATION_MS / 1000 * target_sr), dtype=audio_arr.dtype)
                all_audio_pieces.append(silence)
            all_audio_pieces.append(audio_arr); first_valid_chunk = True
        else: logger.warning(f"TTS synthesize_speech_to_array failed for chunk {idx} for user {target_user_id}")
    
    if all_audio_pieces and target_sr is not None:
        merged_audio = np.concatenate(all_audio_pieces)
        try:
            sf.write(temp_tts_merged_wav_path, merged_audio, target_sr) # type: ignore
            pydub_seg = PydubAudioSegment.from_wav(temp_tts_merged_wav_path) # type: ignore
            pydub_seg = pydub_seg.set_frame_rate(16000).set_channels(1) # Ensure 16kHz mono for Opus
            pydub_seg.export(temp_tts_ogg_path, format="ogg", codec="libopus", bitrate="24k") # Opus is good for voice
            
            send_future = None
            # Use the generic send_voice_message_to_user from TelegramBotHandler
            if hasattr(telegram_bot_handler_instance, 'send_voice_message_to_user'):
                send_future = asyncio.run_coroutine_threadsafe(
                    telegram_bot_handler_instance.send_voice_message_to_user(target_user_id, temp_tts_ogg_path), # type: ignore
                    telegram_bot_handler_instance.async_loop) # type: ignore
            else:
                logger.error(f"TelegramBotHandler is missing 'send_voice_message_to_user' method. Cannot send voice to user {target_user_id}.")

            if send_future:
                send_future.result(timeout=20) # Wait for the send operation
                logger.info(f"Voice reply sent to user {target_user_id} using {temp_tts_ogg_path}")
        except Exception as e_send_v: logger.error(f"Error processing/sending voice to {target_user_id}: {e_send_v}", exc_info=True)
    else: logger.error(f"No audio pieces synthesized for user {target_user_id}. Cannot send voice.")
    
    # Cleanup temporary files
    if os.path.exists(temp_tts_merged_wav_path):
        try: os.remove(temp_tts_merged_wav_path)
        except OSError as e: logger.warning(f"Could not remove temp WAV {temp_tts_merged_wav_path}: {e}") # nosec B110
    if os.path.exists(temp_tts_ogg_path):
        try: os.remove(temp_tts_ogg_path)
        except OSError as e: logger.warning(f"Could not remove temp OGG {temp_tts_ogg_path}: {e}") # nosec B110


def _send_voice_reply_to_telegram_user(target_user_id: int, text_to_speak: str, bark_voice_preset: str):
    if not (telegram_bot_handler_instance and telegram_bot_handler_instance.async_loop and
            tts_manager.is_tts_ready() and PydubAudioSegment and nltk and np and sf):
        missing = []
        if not tts_manager.is_tts_ready(): missing.append("TTS")
        if not PydubAudioSegment: missing.append("Pydub")
        if not nltk: missing.append("NLTK")
        logger.warning(f"Cannot send voice reply to {target_user_id}: Missing deps ({', '.join(missing)}). Text: '{text_to_speak[:30]}'")
        return
    logger.info(f"Synthesizing voice for user {target_user_id} (Preset: {bark_voice_preset}): '{text_to_speak[:50]}...'")
    ts_suffix = datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')
    temp_tts_merged_wav_path = os.path.join(config.TELEGRAM_TTS_TEMP_FOLDER, f"tts_u{target_user_id}_merged_{ts_suffix}.wav")
    temp_tts_ogg_path = os.path.join(config.TELEGRAM_TTS_TEMP_FOLDER, f"tts_u{target_user_id}_reply_{ts_suffix}.ogg")
    bark_tts_engine = tts_manager.get_bark_model_instance()
    if not bark_tts_engine: logger.error(f"Could not get Bark instance for user {target_user_id}. Skipping voice."); return
    lang_for_nltk = 'english' if 'en_' in bark_voice_preset.lower() else 'russian'
    try: nltk.data.find(f'tokenizers/punkt/PY3/{lang_for_nltk}.pickle')
    except LookupError:
        try: nltk.download('punkt', quiet=True)
        except: logger.error(f"Failed to download NLTK 'punkt' for {lang_for_nltk}. TTS quality might be affected.")
    try: sentences = nltk.sent_tokenize(text_to_speak, language=lang_for_nltk)
    except Exception: sentences = [text_to_speak]
    text_chunks = []; current_batch = []
    for i, s in enumerate(sentences):
        current_batch.append(s)
        if len(current_batch) >= config.BARK_MAX_SENTENCES_PER_CHUNK or (i + 1) == len(sentences):
            text_chunks.append(" ".join(current_batch)); current_batch = []
    all_audio_pieces = []; target_sr = None; first_valid_chunk = False
    for idx, chunk_text in enumerate(text_chunks):
        audio_arr, sr = bark_tts_engine.synthesize_speech_to_array(
            chunk_text, generation_params={"voice_preset": bark_voice_preset}
        )
        if audio_arr is not None and sr is not None:
            if target_sr is None: target_sr = sr
            if sr != target_sr: logger.warning("Samplerate mismatch in TTS chunks. Skipping chunk."); continue
            if first_valid_chunk and config.BARK_SILENCE_DURATION_MS > 0:
                silence = np.zeros(int(config.BARK_SILENCE_DURATION_MS / 1000 * target_sr), dtype=audio_arr.dtype)
                all_audio_pieces.append(silence)
            all_audio_pieces.append(audio_arr); first_valid_chunk = True
        else: logger.warning(f"TTS synthesize_speech_to_array failed for chunk {idx} for user {target_user_id}")
    if all_audio_pieces and target_sr is not None:
        merged_audio = np.concatenate(all_audio_pieces)
        try:
            sf.write(temp_tts_merged_wav_path, merged_audio, target_sr) # type: ignore
            pydub_seg = PydubAudioSegment.from_wav(temp_tts_merged_wav_path) # type: ignore
            pydub_seg = pydub_seg.set_frame_rate(16000).set_channels(1) # Convert to 16kHz mono for Opus
            pydub_seg.export(temp_tts_ogg_path, format="ogg", codec="libopus", bitrate="24k")
            send_future = None
            if str(target_user_id) == config.TELEGRAM_ADMIN_USER_ID:
                 send_future = asyncio.run_coroutine_threadsafe(
                    telegram_bot_handler_instance.send_voice_message_to_admin(temp_tts_ogg_path), # type: ignore
                    telegram_bot_handler_instance.async_loop) # type: ignore
            else: # For customer, might use a generic send_voice or specific customer method if added
                 # This placeholder assumes you might add a generic send_voice_to_user in TelegramBotHandler
                 if hasattr(telegram_bot_handler_instance, 'send_voice_message_to_user'):
                     send_future = asyncio.run_coroutine_threadsafe(
                         telegram_bot_handler_instance.send_voice_message_to_user(target_user_id, temp_tts_ogg_path), # type: ignore
                         telegram_bot_handler_instance.async_loop) # type: ignore
                 else:
                     logger.warning(f"Voice reply for non-admin {target_user_id} requested but general send_voice_message_to_user not implemented in TG handler.")

            if send_future:
                send_future.result(timeout=20)
                logger.info(f"Voice reply sent to user {target_user_id} using {temp_tts_ogg_path}")
        except Exception as e_send_v: logger.error(f"Error processing/sending voice to {target_user_id}: {e_send_v}", exc_info=True)
    else: logger.error(f"No audio pieces synthesized for user {target_user_id}. Cannot send voice.")
    if os.path.exists(temp_tts_merged_wav_path):
        try: os.remove(temp_tts_merged_wav_path)
        except OSError: pass # nosec B110
    if os.path.exists(temp_tts_ogg_path):
        try: os.remove(temp_tts_ogg_path)
        except OSError: pass # nosec B110

def handle_customer_interaction_package(customer_user_id: int):
    global chat_history, user_state, assistant_state # These are for admin context, used here carefully
    function_signature_for_log = f"handle_customer_interaction_package(cust_id={customer_user_id})"
    logger.info(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Starting processing.")
    customer_state = {}
    try: customer_state = state_manager.load_or_initialize_customer_state(customer_user_id, gui_callbacks)
    except Exception as e: logger.error(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - CRITICAL Error loading customer state: {e}", exc_info=True); return
    current_stage = customer_state.get("conversation_stage")
    if current_stage != "aggregating_messages":
        logger.warning(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Customer not in 'aggregating_messages' stage (is '{current_stage}'). Skipping."); return
    ack_sent_successfully = False
    if telegram_bot_handler_instance and telegram_bot_handler_instance.async_loop:
        try:
            ack_future = asyncio.run_coroutine_threadsafe(
                telegram_bot_handler_instance.send_text_message_to_user(customer_user_id, config.TELEGRAM_NON_ADMIN_THANKS_AND_FORWARDED),
                telegram_bot_handler_instance.async_loop)
            ack_future.result(timeout=10); ack_sent_successfully = True
            logger.info(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Sent initial ack to customer.")
        except Exception as e: logger.error(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Error/Timeout sending initial ack: {e}", exc_info=True)
    else: logger.error(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Cannot send initial ack. TG Handler/loop missing.")
    customer_state["conversation_stage"] = "acknowledged_pending_llm"
    state_manager.save_customer_state(customer_user_id, customer_state, gui_callbacks)
    logger.debug(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Customer stage updated to 'acknowledged_pending_llm'.")
    interaction_blob_parts = []
    for turn in customer_state.get("chat_history", []):
        sender = turn.get("sender", "unknown").capitalize(); message_text = turn.get("message", "")
        interaction_blob_parts.append(f"{sender}: {message_text}")
    customer_interaction_text_blob = "\n".join(interaction_blob_parts)
    if not customer_interaction_text_blob.strip():
        logger.info(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - No text content in blob. Skipping LLM.")
        customer_state["conversation_stage"] = "interaction_closed"; customer_state["intent"] = "No actionable content provided."
        state_manager.save_customer_state(customer_user_id, customer_state, gui_callbacks)
        admin_msg_empty = f"Admin Info: Customer {customer_user_id} interaction closed - no actionable text (ack {'sent' if ack_sent_successfully else 'failed'})."
        if gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')):
            gui_callbacks['add_assistant_message_to_display'](admin_msg_empty, source="system_customer_notice")
        if telegram_bot_handler_instance and config.TELEGRAM_ADMIN_USER_ID and telegram_bot_handler_instance.async_loop:
            try:
                admin_id_int = int(config.TELEGRAM_ADMIN_USER_ID)
                asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance.send_text_message_to_user(admin_id_int, admin_msg_empty),
                telegram_bot_handler_instance.async_loop).result(timeout=10)
            except Exception as e: logger.error(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Error sending empty blob notice to admin: {e}")
        return
    assistant_state_snapshot_for_prompt = {};
    with assistant_state_lock: assistant_state_snapshot_for_prompt = assistant_state.copy()
    admin_name_val = assistant_state_snapshot_for_prompt.get("admin_name", "Partner")
    logger.debug(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Preparing to call Ollama.")
    ollama_data, ollama_error = ollama_handler.call_ollama_for_chat_response(
        prompt_template_to_use=config.OLLAMA_CUSTOMER_PROMPT_TEMPLATE_V3,
        transcribed_text="", current_chat_history=[], current_user_state=customer_state.copy(),
        current_assistant_state=assistant_state_snapshot_for_prompt, language_instruction="",
        format_kwargs={
            "admin_name_value": admin_name_val,
            "customer_user_id": str(customer_user_id),
            "customer_state_string": json.dumps(customer_state, indent=2, ensure_ascii=False),
            "customer_interaction_text_blob": customer_interaction_text_blob,
            "assistant_state_string": json.dumps(assistant_state_snapshot_for_prompt, indent=2, ensure_ascii=False),
            "actual_thanks_and_forwarded_message_value": config.TELEGRAM_NON_ADMIN_THANKS_AND_FORWARDED},
        expected_keys_override=["updated_customer_state", "updated_assistant_state", "message_for_admin", "polite_followup_message_for_customer"])
    if ollama_error:
        logger.error(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Ollama call failed: {ollama_error}")
        current_customer_state_for_error_update = state_manager.load_or_initialize_customer_state(customer_user_id, gui_callbacks)
        current_customer_state_for_error_update["conversation_stage"] = "error_forwarded_to_admin"
        current_customer_state_for_error_update["intent"] = f"LLM processing error: {ollama_error[:100]}"
        state_manager.save_customer_state(customer_user_id, current_customer_state_for_error_update, gui_callbacks)
        admin_error_message = (f"{config.TELEGRAM_NON_ADMIN_PROCESSING_ERROR_TO_ADMIN_PREFIX} {customer_user_id} (ack {'sent' if ack_sent_successfully else 'failed'}).\n"
                               f"LLM Error: {ollama_error}\nBlob (1000c):\n{customer_interaction_text_blob[:1000]}")
        if gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')):
            gui_callbacks['add_assistant_message_to_display'](admin_error_message, source="system_customer_error")
        if telegram_bot_handler_instance and config.TELEGRAM_ADMIN_USER_ID and telegram_bot_handler_instance.async_loop:
            try:
                admin_id_int = int(config.TELEGRAM_ADMIN_USER_ID)
                asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance.send_text_message_to_user(admin_id_int, admin_error_message),
                telegram_bot_handler_instance.async_loop).result(timeout=10)
            except Exception as e: logger.error(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Error sending LLM failure notice to admin: {e}")
    else:
        logger.info(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Ollama call successful.")
        updated_customer_state_from_llm = ollama_data.get("updated_customer_state"); updated_assistant_state_changes_from_llm = ollama_data.get("updated_assistant_state")
        message_for_admin = ollama_data.get("message_for_admin"); polite_followup_for_customer = ollama_data.get("polite_followup_message_for_customer")
        if not all([isinstance(o, dict) for o in [updated_customer_state_from_llm, updated_assistant_state_changes_from_llm]] +
                   [isinstance(s, str) for s in [message_for_admin, polite_followup_for_customer]]): # type: ignore
            logger.error(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - LLM response missing/malformed. Data: {ollama_data}")
            # Replicate error handling from above if response structure is bad
            current_customer_state_for_error_update = state_manager.load_or_initialize_customer_state(customer_user_id, gui_callbacks)
            current_customer_state_for_error_update["conversation_stage"] = "error_forwarded_to_admin"
            current_customer_state_for_error_update["intent"] = "LLM response malformed."
            state_manager.save_customer_state(customer_user_id, current_customer_state_for_error_update, gui_callbacks)
            admin_error_message_malformed = f"{config.TELEGRAM_NON_ADMIN_PROCESSING_ERROR_TO_ADMIN_PREFIX} {customer_user_id}. LLM response structure invalid."
            if gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')):
                gui_callbacks['add_assistant_message_to_display'](admin_error_message_malformed, source="system_customer_error")
            if telegram_bot_handler_instance and config.TELEGRAM_ADMIN_USER_ID and telegram_bot_handler_instance.async_loop:
                 try: asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance.send_text_message_to_user(int(config.TELEGRAM_ADMIN_USER_ID), admin_error_message_malformed), telegram_bot_handler_instance.async_loop).result(timeout=10)
                 except Exception as e_tg_malformed: logger.error(f"CUSTOMER_LLM_THREAD: Error sending malformed LLM notice to admin: {e_tg_malformed}")
            return

        if updated_customer_state_from_llm.get("user_id") != customer_user_id: # type: ignore
            logger.warning(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - LLM returned customer state with mismatched user_id. Correcting.")
            updated_customer_state_from_llm["user_id"] = customer_user_id # type: ignore
        state_manager.save_customer_state(customer_user_id, updated_customer_state_from_llm, gui_callbacks) # type: ignore
        logger.debug(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Updated customer state saved.")

        with assistant_state_lock:
            current_global_assistant_state = state_manager.load_assistant_state_only(gui_callbacks)
            for key, value_from_llm in updated_assistant_state_changes_from_llm.items(): # type: ignore
                if key == "internal_tasks" and isinstance(value_from_llm, dict) and isinstance(current_global_assistant_state.get(key), dict):
                    for task_type in ["pending", "in_process", "completed"]:
                        new_tasks = value_from_llm.get(task_type, []); existing_tasks = current_global_assistant_state[key].get(task_type, [])
                        if not isinstance(new_tasks, list): new_tasks = [str(new_tasks)]
                        if not isinstance(existing_tasks, list): existing_tasks = [str(existing_tasks)]
                        # Use dict.fromkeys to merge and keep order of first seen unique tasks
                        current_global_assistant_state[key][task_type] = list(dict.fromkeys([str(t) for t in existing_tasks] + [str(t) for t in new_tasks]))
                else: current_global_assistant_state[key] = value_from_llm
            # Update TG bot status in assistant state if it changed (e.g., due to error)
            if telegram_bot_handler_instance:
                current_global_assistant_state["telegram_bot_status"] = telegram_bot_handler_instance.get_status()
            assistant_state.clear(); assistant_state.update(current_global_assistant_state)
            state_manager.save_assistant_state_only(assistant_state, gui_callbacks)

            # Update GUI Kanban from assistant_state (which was just updated by LLM)
            if gui_callbacks:
                asst_tasks_customer_context = assistant_state.get("internal_tasks", {})
                if not isinstance(asst_tasks_customer_context, dict): asst_tasks_customer_context = {}

                if callable(gui_callbacks.get('update_kanban_pending')):
                    gui_callbacks['update_kanban_pending'](asst_tasks_customer_context.get("pending", []))
                if callable(gui_callbacks.get('update_kanban_in_process')):
                    gui_callbacks['update_kanban_in_process'](asst_tasks_customer_context.get("in_process", []))
                if callable(gui_callbacks.get('update_kanban_completed')):
                    gui_callbacks['update_kanban_completed'](asst_tasks_customer_context.get("completed", []))
                logger.debug(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Triggered GUI Kanban update.")
            logger.debug(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Global assistant_state updated.")

        if message_for_admin:
            with assistant_state_lock: # Protect chat_history and subsequent save_states
                admin_notification_turn = {"assistant": f"[Сводка по клиенту {customer_user_id}] {message_for_admin}", "source": "customer_summary_internal", "timestamp": state_manager.get_current_timestamp_iso()}
                chat_history.append(admin_notification_turn)
                # Save all states, including the potentially updated assistant_state from above and the new chat_history item
                chat_history = state_manager.save_states(chat_history, user_state, assistant_state.copy(), gui_callbacks)
                logger.info(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Added customer summary to admin chat history & saved all states.")
                # Update GUI chat display after chat_history is modified and saved
                if gui and callable(gui_callbacks.get('update_chat_display_from_list')):
                    gui_callbacks['update_chat_display_from_list'](chat_history)

            # This display to GUI is redundant if update_chat_display_from_list runs from save_states. Kept for explicitness.
            # if gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')):
            #      gui_callbacks['add_assistant_message_to_display'](f"[Сводка по клиенту {customer_user_id}] {message_for_admin}", source="customer_summary_report")

            if telegram_bot_handler_instance and config.TELEGRAM_ADMIN_USER_ID and telegram_bot_handler_instance.async_loop:
                try:
                    admin_id_int = int(config.TELEGRAM_ADMIN_USER_ID)
                    if config.TELEGRAM_REPLY_WITH_TEXT:
                        asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance.send_text_message_to_user(admin_id_int, message_for_admin),
                        telegram_bot_handler_instance.async_loop).result(timeout=10)
                    if config.TELEGRAM_REPLY_WITH_VOICE:
                        admin_summary_voice_preset = config.BARK_VOICE_PRESET_RU; temp_as_lang_check = {}
                        with assistant_state_lock: temp_as_lang_check = assistant_state.copy()
                        current_as_lang = temp_as_lang_check.get("last_used_language", "ru")
                        if current_as_lang == "en": admin_summary_voice_preset = config.BARK_VOICE_PRESET_EN
                        _send_voice_reply_to_telegram_user(admin_id_int, message_for_admin, admin_summary_voice_preset)
                except Exception as e: logger.error(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Error sending summary to admin: {e}")

        if polite_followup_for_customer and polite_followup_for_customer.strip().upper() != "NO_CUSTOMER_FOLLOWUP_NEEDED":
            if telegram_bot_handler_instance and telegram_bot_handler_instance.async_loop:
                try:
                    asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance.send_text_message_to_user(customer_user_id, polite_followup_for_customer),
                    telegram_bot_handler_instance.async_loop).result(timeout=10)
                    logger.info(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Sent LLM follow-up to customer.")
                except Exception as e: logger.error(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Error sending follow-up to customer: {e}")
            else: logger.warning(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Cannot send follow-up (TG handler/loop missing).")
        else: logger.info(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - No LLM follow-up needed for customer.")
    logger.info(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Finished processing.")

def process_recorded_audio_and_interact(recorded_sample_rate):
    logger.info(f"Processing recorded audio (Admin GUI). Sample rate: {recorded_sample_rate} Hz.")
    audio_float32, audio_frames_for_save = audio_processor.convert_frames_to_numpy(recorded_sample_rate, gui_callbacks)
    if audio_float32 is None:
        logger.warning("Audio processing (convert_frames_to_numpy) returned None.")
        if gui_callbacks and callable(gui_callbacks.get('speak_button_update')): gui_callbacks['speak_button_update'](True, "Speak")
        return
    if config.SAVE_RECORDINGS_TO_WAV and audio_frames_for_save:
        if file_utils.ensure_folder(config.OUTPUT_FOLDER, gui_callbacks):
            filename = f"rec_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
            audio_processor.save_wav_data_to_file(os.path.join(config.OUTPUT_FOLDER, filename), audio_frames_for_save, recorded_sample_rate, gui_callbacks)
    del audio_frames_for_save; gc.collect()
    if not (whisper_handler.WHISPER_CAPABLE and whisper_handler.is_whisper_ready()):
        logger.warning("Whisper not ready for Admin GUI audio.")
        if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update']("Hearing module not ready.")
        if gui_callbacks and callable(gui_callbacks.get('speak_button_update')): gui_callbacks['speak_button_update'](False, "HEAR NRDY")
        return
    if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update']("Transcribing audio (Admin GUI)...")
    transcribed_text, trans_err, detected_lang = whisper_handler.transcribe_audio(
        audio_np_array=audio_float32, language=None, task="transcribe", gui_callbacks=gui_callbacks # Explicit task
    )
    if not trans_err and transcribed_text:
        _handle_admin_llm_interaction(transcribed_text, source="gui", detected_language_code=detected_lang)
    else:
        logger.warning(f"Admin GUI Transcription failed: {trans_err or 'Empty.'}")
        lang_for_err = "en"
        with assistant_state_lock: lang_for_err = assistant_state.get("last_used_language", "en")
        err_msg_stt = "I didn't catch that..." if lang_for_err == "en" else "Я не расслышала..."
        if gui_callbacks and callable(gui_callbacks.get('add_user_message_to_display')): gui_callbacks['add_user_message_to_display']("[Silent/Unclear Audio]", source="gui")
        if gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')): gui_callbacks['add_assistant_message_to_display'](err_msg_stt, is_error=True, source="gui")
        if tts_manager.is_tts_ready():
            err_preset = config.BARK_VOICE_PRESET_EN if lang_for_err == "en" else config.BARK_VOICE_PRESET_RU
            current_persona_name = "Iri-shka";
            with assistant_state_lock: current_persona_name = assistant_state.get("persona_name", "Iri-shka")
            tts_manager.start_speaking_response(err_msg_stt, current_persona_name, err_preset, gui_callbacks)
        if gui_callbacks and callable(gui_callbacks.get('speak_button_update')): gui_callbacks['speak_button_update'](True, "Speak")

def process_admin_telegram_text_message(user_id, text_message):
    logger.info(f"Processing Admin Telegram text from {user_id}: '{text_message[:70]}...'")
    # We assume text from admin might be in any language; LLM will use last_used_language or detect.
    _handle_admin_llm_interaction(text_message, source="telegram_admin", detected_language_code=None)

def process_admin_telegram_voice_message(user_id, wav_filepath):
    logger.info(f"Processing Admin Telegram voice from {user_id}, WAV: {wav_filepath}")
    if not (whisper_handler.WHISPER_CAPABLE and whisper_handler.is_whisper_ready() and _whisper_module_for_load_audio):
        logger.error("Cannot process admin voice: Whisper not ready or load_audio missing.")
        if telegram_bot_handler_instance and telegram_bot_handler_instance.async_loop:
             asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance.send_text_message_to_user(user_id, "Error: Voice processing module (Whisper) is not ready."), telegram_bot_handler_instance.async_loop)
        return
    try:
        if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update']("Loading Admin voice (TG)...")
        audio_numpy = _whisper_module_for_load_audio.load_audio(wav_filepath)
        if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update']("Transcribing Admin voice (TG)...")
        trans_text, trans_err, detected_lang = whisper_handler.transcribe_audio(
            audio_np_array=audio_numpy, language=None, task="transcribe", gui_callbacks=gui_callbacks # Explicit task
        )
        if not trans_err and trans_text:
            _handle_admin_llm_interaction(trans_text, source="telegram_voice_admin", detected_language_code=detected_lang)
        else:
            logger.warning(f"Admin TG Voice Transcription failed: {trans_err or 'Empty.'}")
            if telegram_bot_handler_instance and telegram_bot_handler_instance.async_loop:
                asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance.send_text_message_to_user(user_id, f"Couldn't transcribe voice: {trans_err or 'No speech detected.'}"), telegram_bot_handler_instance.async_loop)
    except Exception as e:
        logger.error(f"Error processing admin voice WAV {wav_filepath}: {e}", exc_info=True)
        if telegram_bot_handler_instance and telegram_bot_handler_instance.async_loop:
             asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance.send_text_message_to_user(user_id, "An error occurred while processing your voice message."), telegram_bot_handler_instance.async_loop)
    finally:
        if os.path.exists(wav_filepath):
            try: os.remove(wav_filepath)
            except Exception as e_rem: logger.warning(f"Could not remove temp admin WAV {wav_filepath}: {e_rem}")

def toggle_speaking_recording():
    if not audio_processor.is_recording_active():
        if not (whisper_handler.WHISPER_CAPABLE and whisper_handler.is_whisper_ready()):
            logger.warning("Cannot start recording: Whisper module not ready."); return
        if tts_manager.is_tts_loading(): logger.info("Cannot start recording: TTS loading."); return
        tts_manager.stop_current_speech(gui_callbacks)
        if audio_processor.start_recording(gui_callbacks):
            if gui_callbacks and callable(gui_callbacks.get('speak_button_update')): gui_callbacks['speak_button_update'](True, "Listening...")
    else:
        audio_processor.stop_recording()
        if gui_callbacks and callable(gui_callbacks.get('speak_button_update')): gui_callbacks['speak_button_update'](False, "Processing...")

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
    global gui, app_tk_instance, _active_gpu_monitor, telegram_bot_handler_instance, llm_task_executor
    logger.info("Application closing sequence initiated...")
    if llm_task_executor:
        logger.info("Shutting down LLM task thread pool..."); llm_task_executor.shutdown(wait=True, cancel_futures=False)
        llm_task_executor = None; logger.info("LLM task thread pool shutdown complete.")
    if telegram_bot_handler_instance: logger.info("Shutting down Telegram bot..."); telegram_bot_handler_instance.full_shutdown()
    if _active_gpu_monitor: logger.info("Shutting down GPU monitor..."); _active_gpu_monitor.stop()
    logger.info("Shutting down audio resources..."); audio_processor.shutdown_audio_resources()
    if tts_manager.TTS_CAPABLE: logger.info("Shutting down TTS module..."); tts_manager.full_shutdown_tts_module()
    if whisper_handler.WHISPER_CAPABLE: logger.info("Cleaning up Whisper model..."); whisper_handler.full_shutdown_whisper_module()
    if gui:
        logger.info("Destroying GUI window...");
        if hasattr(gui, 'destroy_window') and callable(gui.destroy_window):
            gui.destroy_window()
        gui = None
    if app_tk_instance :
        try: app_tk_instance.destroy()
        except Exception as e: logger.warning(f"Error destroying app_tk_instance: {e}")
    app_tk_instance = None
    logger.info("Application exit sequence fully complete."); logging.shutdown()

def check_search_engine_status():
    logger.debug("LOADER_THREAD: check_search_engine_status called.")
    try:
        response = requests.get(f"{config.SEARCH_ENGINE_URL.rstrip('/')}/search", params={'q': 'ping'}, timeout=config.SEARCH_ENGINE_PING_TIMEOUT)
        response.raise_for_status(); logger.info(f"Search Engine ping OK (Status {response.status_code}).")
        return "INET: RDY", "ready"
    except requests.exceptions.Timeout: logger.error("Search Engine ping timeout."); return "INET: TMO", "timeout"
    except requests.exceptions.RequestException as e: logger.error(f"Search Engine error: {e}"); return "INET: ERR", "error"

def _periodic_customer_interaction_checker():
    if customer_interaction_manager_instance and llm_task_executor and not llm_task_executor._shutdown:
        try:
            expired_customer_ids = customer_interaction_manager_instance.check_and_get_expired_interactions()
            for customer_id in expired_customer_ids:
                if customer_id:
                    logger.info(f"MAIN_PERIODIC_CHECKER: Submitting customer {customer_id} for LLM processing.")
                    llm_task_executor.submit(handle_customer_interaction_package, customer_id)
        except Exception as e: logger.error(f"Error in _periodic_customer_interaction_checker: {e}", exc_info=True)
    if app_tk_instance and hasattr(app_tk_instance, 'winfo_exists') and app_tk_instance.winfo_exists():
        app_tk_instance.after(config.CUSTOMER_INTERACTION_CHECK_INTERVAL_SECONDS * 1000, _periodic_customer_interaction_checker)
    else: logger.info("_periodic_customer_interaction_checker: Tk instance not available. Stopping periodic check.")

def _process_queued_admin_llm_messages():
    try:
        while not admin_llm_message_queue.empty():
            item = admin_llm_message_queue.get_nowait()
            if not isinstance(item, tuple) or len(item)!=3: logger.error(f"Invalid Admin LLM queue item: {item}"); continue
            msg_type, user_id, data = item
            if msg_type == "telegram_text_admin": process_admin_telegram_text_message(user_id, data)
            elif msg_type == "telegram_voice_admin_wav": process_admin_telegram_voice_message(user_id, data)
            else: logger.warning(f"Unknown Admin LLM message type: {msg_type}")
            admin_llm_message_queue.task_done()
    except queue.Empty: pass
    except Exception as e: logger.error(f"Error processing Admin LLM queue: {e}", exc_info=True)
    if app_tk_instance and hasattr(app_tk_instance, 'winfo_exists') and app_tk_instance.winfo_exists():
        app_tk_instance.after(300, _process_queued_admin_llm_messages)
    else: logger.info("_process_queued_admin_llm_messages: Tk instance not available. Stopping queue check.")

def get_dashboard_data_for_telegram() -> dict:
    logger.debug("Gathering data for HTML dashboard...")
    component_statuses = {}
    def get_gui_status_text_and_type(label_attr_name: str, default_text="N/A", default_type="unknown"):
        if gui and hasattr(gui, label_attr_name):
            label = getattr(gui, label_attr_name)
            if label and hasattr(label, 'cget'):
                try:
                    text = label.cget("text")
                    type_ = "unknown"
                    if any(kw in text for kw in ["RDY", "OK", "IDLE", "POLL", "SAVED", "LOADED", "FRESH"]): type_ = "ready"
                    elif any(kw in text for kw in ["CHK", "LOAD", "PING", "THK"]): type_ = "checking"
                    elif any(kw in text for kw in ["ERR", "TMO", "NRDY", "BAD", "CON", "502", "H"]): type_ = "error"
                    elif any(kw in text for kw in ["OFF", "N/A", "NO TOK", "NO ADM"]): type_ = "off"
                    return text, type_
                except tk.TclError: return default_text, default_type
        return default_text, default_type

    component_statuses["act"] = get_gui_status_text_and_type("act_status_text_label", "ACT: N/A")
    component_statuses["inet"] = get_gui_status_text_and_type("inet_status_text_label", "INET: N/A")
    component_statuses["webui"] = get_gui_status_text_and_type("webui_status_text_label", "WEBUI: N/A", "off") # Usually off
    component_statuses["tele"] = ( (f"TELE: {telegram_bot_handler_instance.get_status().upper()}" if telegram_bot_handler_instance else "TELE: N/A"),
                                   (telegram_bot_handler_instance.get_status() if telegram_bot_handler_instance else "unknown") )
    component_statuses["mem"] = get_gui_status_text_and_type("memory_status_text_label", "MEM: N/A")
    component_statuses["hear"] = (f"HEAR: {whisper_handler.get_status_short()}", whisper_handler.get_status_type())
    component_statuses["voice"] = (f"VOICE: {tts_manager.get_status_short()}", tts_manager.get_status_type())
    component_statuses["mind"] = (f"MIND: {'RDY' if ollama_ready else 'NRDY'}", "ready" if ollama_ready else "error")
    component_statuses["vis"] = get_gui_status_text_and_type("vis_status_text_label", "VIS: N/A", "off")
    component_statuses["art"] = get_gui_status_text_and_type("art_status_text_label", "ART: N/A", "off")

    app_overall_status_text = "Status Unavailable"
    if gui and hasattr(gui, 'app_status_label') and gui.app_status_label:
        try: app_overall_status_text = gui.app_status_label.cget("text")
        except tk.TclError: pass

    current_admin_user_state, current_assistant_state_snapshot, current_admin_chat_history = {}, {}, []
    with assistant_state_lock:
        current_admin_user_state = user_state.copy()
        current_assistant_state_snapshot = assistant_state.copy()
        current_admin_chat_history = chat_history[:] # shallow copy is fine for list of dicts

    return {"admin_user_state": current_admin_user_state, "assistant_state": current_assistant_state_snapshot,
            "admin_chat_history": current_admin_chat_history, "component_statuses": component_statuses,
            "app_overall_status": app_overall_status_text}

def load_all_models_and_services():
    global ollama_ready
    logger.info("LOADER_THREAD: --- Starting model and services loading/checking ---")
    def safe_gui_callback(callback_name, *args):
        if gui_callbacks and callable(gui_callbacks.get(callback_name)):
            try: gui_callbacks[callback_name](*args); logger.debug(f"LOADER_THREAD: GUI cb '{callback_name}' called.")
            except Exception as e: logger.error(f"LOADER_THREAD: Error in GUI cb '{callback_name}': {e}", exc_info=False)
        else: logger.debug(f"LOADER_THREAD: GUI cb '{callback_name}' not found/callable.")
    safe_gui_callback('status_update', "Initializing components...")
    for cb_name, text, status in [('act_status_update', "ACT: IDLE", "idle"), ('webui_status_update', "WEBUI: OFF", "off"),
        ('inet_status_update', "INET: CHK", "checking"), ('memory_status_update', "MEM: CHK", "checking"),
        ('hearing_status_update', "HEAR: CHK", "loading"), ('voice_status_update', "VOICE: CHK", "loading"),
        ('mind_status_update', "MIND: CHK", "pinging"), ('tele_status_update', "TELE: CHK", "checking"),
        ('vis_status_update', "VIS: OFF", "off"), ('art_status_update', "ART: OFF", "off")]:
        safe_gui_callback(cb_name, text, status)
    logger.info("LOADER_THREAD: Initial GUI component statuses set.")
    logger.info("LOADER_THREAD: Checking internet/search engine...")
    inet_short_text, inet_status_type = check_search_engine_status()
    safe_gui_callback('inet_status_update', inet_short_text, inet_status_type)
    logger.info(f"LOADER_THREAD: Internet check done. Status: {inet_short_text}")
    with assistant_state_lock:
        if "admin_name" not in assistant_state: assistant_state["admin_name"] = config.DEFAULT_ASSISTANT_STATE["admin_name"]
    safe_gui_callback('memory_status_update', "MEM: LOADED" if chat_history else "MEM: FRESH", "loaded" if chat_history else "fresh") # Use 'loaded' not 'ready'
    logger.info("LOADER_THREAD: Memory status updated.")
    logger.info("LOADER_THREAD: Checking/loading Whisper...")
    if whisper_handler.WHISPER_CAPABLE:
        logger.info("LOADER_THREAD: Whisper is CAPABLE. Calling load_whisper_model...")
        whisper_handler.load_whisper_model(config.WHISPER_MODEL_SIZE, gui_callbacks)
        logger.info("LOADER_THREAD: load_whisper_model call finished.")
    else:
        logger.warning("LOADER_THREAD: Whisper not capable, skipping load.")
        safe_gui_callback('hearing_status_update', "HEAR: N/A", "na"); safe_gui_callback('speak_button_update', False, "HEAR N/A")
    logger.info("LOADER_THREAD: Checking/loading Bark TTS...")
    if tts_manager.TTS_CAPABLE:
        logger.info("LOADER_THREAD: Bark TTS is CAPABLE. Calling load_bark_resources...")
        tts_manager.load_bark_resources(gui_callbacks)
        logger.info("LOADER_THREAD: load_bark_resources call finished.")
    else:
        logger.warning("LOADER_THREAD: TTS (Bark) not capable, skipping load.")
        safe_gui_callback('voice_status_update', "VOICE: N/A", "na")
    logger.info("LOADER_THREAD: Checking Ollama server and model...")
    ollama_ready_flag, ollama_log_msg = ollama_handler.check_ollama_server_and_model()
    ollama_ready = ollama_ready_flag
    if ollama_ready_flag: safe_gui_callback('mind_status_update', "MIND: RDY", "ready")
    else:
        short_code, status_type_ollama = _parse_ollama_error_to_short_code(ollama_log_msg)
        safe_gui_callback('mind_status_update', f"MIND: {short_code}", status_type_ollama)
    logger.info(f"LOADER_THREAD: Ollama check finished. Ready: {ollama_ready_flag}, Msg: {ollama_log_msg}")

    # Telegram status is set by TelegramBotHandler init or start/stop actions
    # Update assistant_state with the current TG status
    current_tele_status_for_as = "off" # Default if handler is None
    if telegram_bot_handler_instance:
        current_tele_status_for_as = telegram_bot_handler_instance.get_status()
    elif not config.TELEGRAM_BOT_TOKEN: current_tele_status_for_as = "no_token"
    elif not config.TELEGRAM_ADMIN_USER_ID: current_tele_status_for_as = "no_admin"

    with assistant_state_lock:
        assistant_state["telegram_bot_status"] = current_tele_status_for_as
        state_manager.save_assistant_state_only(assistant_state.copy(), gui_callbacks) # Save this update
    logger.info(f"LOADER_THREAD: Assistant state 'telegram_bot_status' updated to {current_tele_status_for_as}.")


    logger.info("LOADER_THREAD: Finalizing GUI status updates.")
    if whisper_handler.is_whisper_ready():
        ready_msg = "Ready.";
        if tts_manager.is_tts_loading(): ready_msg = "Ready (TTS loading...)"
        elif not tts_manager.is_tts_ready() and tts_manager.TTS_CAPABLE: ready_msg = "Ready (TTS NRDY)."
        safe_gui_callback('status_update', ready_msg); safe_gui_callback('speak_button_update', True, "Speak")
    else:
        safe_gui_callback('status_update', "Hearing module not ready."); safe_gui_callback('speak_button_update', False, "HEAR NRDY")
    logger.info("LOADER_THREAD: --- Sequential model and services loading/checking thread finished ---")

if __name__ == "__main__":
    logger.info("--- Main __name__ block started ---")

    # Ensure all necessary folders exist
    folders_to_ensure = [
        config.DATA_FOLDER, config.OUTPUT_FOLDER,
        config.TELEGRAM_VOICE_TEMP_FOLDER, config.TELEGRAM_TTS_TEMP_FOLDER,
        config.CUSTOMER_STATES_FOLDER,
        os.path.join(config.DATA_FOLDER, "temp_dashboards") # For HTML dashboards
    ]
    for folder_path in folders_to_ensure:
        if not file_utils.ensure_folder(folder_path, gui_callbacks=None): # No GUI yet for callbacks
            logger.critical(f"CRITICAL: Failed to create folder '{folder_path}'. Exiting.")
            sys.exit(1)

    logger.info("Loading initial states (admin & assistant)...")
    try:
        chat_history, user_state, assistant_state = state_manager.load_initial_states(gui_callbacks=None)
    except Exception as e_state_load:
        logger.critical(f"CRITICAL ERROR loading initial states: {e_state_load}", exc_info=True); sys.exit(1)

    initial_theme_from_state = user_state.get("gui_theme", config.DEFAULT_USER_STATE["gui_theme"])
    current_gui_theme = initial_theme_from_state if initial_theme_from_state in [config.GUI_THEME_LIGHT, config.GUI_THEME_DARK] else config.GUI_THEME_LIGHT
    user_state["gui_theme"] = current_gui_theme

    initial_font_size_state = user_state.get("chat_font_size", config.DEFAULT_USER_STATE["chat_font_size"])
    try: initial_font_size_state = int(initial_font_size_state)
    except (ValueError, TypeError): initial_font_size_state = config.DEFAULT_CHAT_FONT_SIZE
    current_chat_font_size_applied = max(config.MIN_CHAT_FONT_SIZE, min(initial_font_size_state, config.MAX_CHAT_FONT_SIZE))
    user_state["chat_font_size"] = current_chat_font_size_applied
    logger.info(f"Initial GUI theme: {current_gui_theme}, Font size: {current_chat_font_size_applied}")

    logger.info("Initializing ThreadPoolExecutor for LLM tasks...")
    llm_task_executor = ThreadPoolExecutor(max_workers=config.LLM_TASK_THREAD_POOL_SIZE, thread_name_prefix="LLMTaskThread")

    logger.info("Initializing CustomerInteractionManager...")
    customer_interaction_manager_instance = CustomerInteractionManager()

    logger.info("Attempting to initialize Tkinter root & GUIManager...")
    try:
        app_tk_instance = tk.Tk()
    except Exception as e_tk_root:
        logger.critical(f"CRITICAL Tkinter root init: {e_tk_root}", exc_info=True); sys.exit(1)

    action_callbacks_for_gui = {
        'toggle_speaking_recording': toggle_speaking_recording, 'on_exit': on_app_exit,
        'unload_bark_model': _unload_bark_model_action, 'reload_bark_model': _reload_bark_model_action,
        'unload_whisper_model': _unload_whisper_model_action, 'reload_whisper_model': _reload_whisper_model_action,
        'start_telegram_bot': _start_telegram_bot_action, 'stop_telegram_bot': _stop_telegram_bot_action,
    }
    try:
        gui = GUIManager(app_tk_instance, action_callbacks_for_gui,
                         initial_theme=current_gui_theme,
                         initial_font_size=current_chat_font_size_applied)
    except Exception as e_gui:
        logger.critical(f"CRITICAL GUIManager init: {e_gui}", exc_info=True)
        if app_tk_instance :
            try: app_tk_instance.destroy()
            except: pass # nosec
        sys.exit(1)
    logger.info("GUIManager initialized.")

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
            else:
                logger.warning(f"GUIManager missing method '{method_name}' for callback '{cb_key}'.")
        gui_callbacks['on_recording_finished'] = process_recorded_audio_and_interact
        logger.info("GUI callbacks dictionary populated.")
    else:
        logger.error("GUI object is None, callbacks cannot be populated.")

    logger.info("Populating GUI with initial state data (Admin User Info, Assistant Kanban)...")
    if gui and gui_callbacks:
        if callable(gui_callbacks.get('update_chat_display_from_list')):
            gui_callbacks['update_chat_display_from_list'](chat_history)
        if callable(gui_callbacks.get('update_todo_list')):
            gui_callbacks['update_todo_list'](user_state.get("todos", []))
        if callable(gui_callbacks.get('update_calendar_events_list')):
            gui_callbacks['update_calendar_events_list'](user_state.get("calendar_events", []))
        initial_asst_tasks = assistant_state.get("internal_tasks", {})
        if not isinstance(initial_asst_tasks, dict): initial_asst_tasks = {}
        if callable(gui_callbacks.get('update_kanban_pending')):
            gui_callbacks['update_kanban_pending'](initial_asst_tasks.get("pending", []))
        if callable(gui_callbacks.get('update_kanban_in_process')):
            gui_callbacks['update_kanban_in_process'](initial_asst_tasks.get("in_process", []))
        if callable(gui_callbacks.get('update_kanban_completed')):
            gui_callbacks['update_kanban_completed'](initial_asst_tasks.get("completed", []))
        logger.info("Initial Admin User Info and Assistant Kanban populated on GUI.")
    else:
        logger.warning("GUI or gui_callbacks not available for initial data population.")

    logger.info("Initializing Telegram Bot Handler...")
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_ADMIN_USER_ID:
        try:
            telegram_bot_handler_instance = TelegramBotHandler(
                token=config.TELEGRAM_BOT_TOKEN,
                admin_user_id_str=config.TELEGRAM_ADMIN_USER_ID,
                message_queue_for_admin_llm=admin_llm_message_queue,
                customer_interaction_manager=customer_interaction_manager_instance, # type: ignore
                gui_callbacks=gui_callbacks,
                fn_get_dashboard_data=get_dashboard_data_for_telegram
            )
            if config.START_BOT_ON_APP_START:
                logger.info("Config requests Telegram bot start on app start.")
                if telegram_bot_handler_instance:
                    telegram_bot_handler_instance.start_polling()
            else:
                 logger.info("Telegram bot will not be started automatically (config).")
                 current_tg_status_for_gui = "off"
                 if not config.TELEGRAM_BOT_TOKEN: current_tg_status_for_gui = "no_token"
                 elif not config.TELEGRAM_ADMIN_USER_ID: current_tg_status_for_gui = "no_admin"
                 if gui_callbacks and callable(gui_callbacks.get('tele_status_update')):
                     gui_callbacks['tele_status_update'](f"TELE: {current_tg_status_for_gui.upper()}", current_tg_status_for_gui)
        except ValueError:
             logger.error(f"TELEGRAM_ADMIN_USER_ID '{config.TELEGRAM_ADMIN_USER_ID}' is invalid. Bot disabled.")
             if gui_callbacks and callable(gui_callbacks.get('tele_status_update')):
                 gui_callbacks['tele_status_update']("TELE: NO ADM", "no_admin")
        except Exception as e_tele_init:
            logger.error(f"Failed to initialize TelegramBotHandler: {e_tele_init}", exc_info=True)
            if gui_callbacks and callable(gui_callbacks.get('tele_status_update')):
                gui_callbacks['tele_status_update']("TELE: INITERR", "error")
    else:
        errmsg_tele = "Telegram Bot: "
        status_key_tele, status_type_tele = "TELE: OFF", "off"
        if not config.TELEGRAM_BOT_TOKEN: errmsg_tele += "Token not set."; status_key_tele, status_type_tele = "TELE: NO TOK", "no_token"
        if not config.TELEGRAM_ADMIN_USER_ID:
            errmsg_tele += (" " if config.TELEGRAM_BOT_TOKEN else "") + "Admin User ID not set."
            if status_key_tele == "TELE: OFF": status_key_tele, status_type_tele = "TELE: NO ADM", "no_admin"
        logger.warning(f"{errmsg_tele} Telegram features will be disabled.")
        if gui_callbacks and callable(gui_callbacks.get('tele_status_update')):
            gui_callbacks['tele_status_update'](status_key_tele, status_type_tele)

    logger.info("Initializing GPU Monitor (if available)...")
    if gpu_monitor.PYNVML_AVAILABLE:
        _active_gpu_monitor = gpu_monitor.get_gpu_monitor_instance(gui_callbacks=gui_callbacks, update_interval=2)
        if _active_gpu_monitor and _active_gpu_monitor.active:
            _active_gpu_monitor.start()
            logger.info("GPU Monitor started.")
        elif _active_gpu_monitor and not _active_gpu_monitor.active:
            logger.warning("GPUMonitor initialized but not active (e.g. no NVIDIA GPU found by NVML).")
    elif gui_callbacks and callable(gui_callbacks.get('gpu_status_update_display')):
        gui_callbacks['gpu_status_update_display']("N/A", "N/A", "na_nvml")

    logger.info("Starting model and services loader thread...")
    loader_thread = threading.Thread(target=load_all_models_and_services, daemon=True, name="ServicesLoaderThread")
    loader_thread.start()

    if app_tk_instance and hasattr(app_tk_instance, 'winfo_exists') and app_tk_instance.winfo_exists():
        app_tk_instance.after(300, _process_queued_admin_llm_messages)
        app_tk_instance.after(config.CUSTOMER_INTERACTION_CHECK_INTERVAL_SECONDS * 1000, _periodic_customer_interaction_checker)
        logger.info("Admin LLM message queue and Customer interaction checker scheduled on Tkinter main thread.")
    else:
        logger.error("Tkinter instance not available for scheduling periodic tasks. Key functionalities might not work.")

    logger.info("Starting Tkinter mainloop...")
    try:
        if app_tk_instance:
            app_tk_instance.mainloop()
        else:
            logger.critical("Cannot start mainloop: app_tk_instance is None. Application will exit.")
            on_app_exit()
            sys.exit(1)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt detected by mainloop. Initiating shutdown.")
    except tk.TclError as e_tcl:
        if "application has been destroyed" in str(e_tcl).lower():
            logger.info("Tkinter mainloop TclError: Application already destroyed (likely during normal shutdown).")
        else:
            logger.error(f"Unhandled TclError in mainloop: {e_tcl}. Initiating shutdown.", exc_info=True)
    except Exception as e_mainloop:
        logger.critical(f"Unexpected critical error in Tkinter mainloop: {e_mainloop}", exc_info=True)
    finally:
        logger.info("Mainloop exited or error occurred. Ensuring graceful shutdown via on_app_exit().")
        on_app_exit()
        logger.info("Application main thread has finished.")