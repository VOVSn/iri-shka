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
import uuid # For web TTS filenames

import nltk
import numpy as np
import soundfile as sf

# --- Setup Custom Logger ---
try:
    import logger as app_logger_module
    logger = app_logger_module.get_logger("Iri-shka_App.Main")
    web_logger = app_logger_module.get_logger("Iri-shka_App.WebApp") # For Flask related logs from main
except ImportError as e:
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stderr)
    logger = logging.getLogger("Iri-shka_App.Main_Fallback")
    web_logger = logging.getLogger("Iri-shka_App.WebApp_Fallback")
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
    from webui.web_app import flask_app as actual_flask_app # Import the Flask app instance
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

# --- Global States (accessed by GUI, Telegram, and Web UI via Bridge) ---
chat_history: list = []
user_state: dict = {} # Admin's user state
assistant_state: dict = {}
# Locks for synchronizing access to global states
global_states_lock = threading.Lock() 


ollama_ready: bool = False
current_gui_theme: str = config.GUI_THEME_LIGHT
current_chat_font_size_applied: int = config.DEFAULT_CHAT_FONT_SIZE
gui_callbacks: dict = {}

# --- WebApp Bridge Class ---
class WebAppBridge:
    def __init__(self, main_app_ollama_ready_flag_getter, main_app_status_label_getter_fn):
        self.get_ollama_ready = main_app_ollama_ready_flag_getter
        self.get_main_app_status_label = main_app_status_label_getter_fn
        self.whisper_handler_module = whisper_handler
        self.ollama_handler_module = ollama_handler
        self.tts_manager_module = tts_manager
        self.telegram_handler_instance_ref: TelegramBotHandler = None # type: ignore
        web_logger.info("WebAppBridge initialized.")

    def process_admin_web_audio(self, input_wav_filepath: str):
        global chat_history, user_state, assistant_state, ollama_ready 

        web_logger.info(f"WebAppBridge: Processing ADMIN web audio: {input_wav_filepath}")
        result = {
            "user_transcription": None,
            "llm_text_response": None,
            "tts_audio_filename": None, 
            "error": None
        }
        detected_lang_from_stt = None
        
        if not self.whisper_handler_module.is_whisper_ready():
            result["error"] = "Whisper (STT) service not ready for admin web input."
            web_logger.error(f"WebAppBridge-Admin: STT Aborted - {result['error']}")
            return result
        
        try:
            web_logger.debug(f"WebAppBridge-Admin: Attempting to load audio for STT from: {input_wav_filepath}")
            audio_np_array_web_admin = None
            if _whisper_module_for_load_audio:
                 audio_np_array_web_admin = _whisper_module_for_load_audio.load_audio(input_wav_filepath)
            else:
                 raise RuntimeError("Whisper 'load_audio' utility not available.")

            web_logger.debug(f"WebAppBridge-Admin: Audio loaded for STT. Shape: {audio_np_array_web_admin.shape if audio_np_array_web_admin is not None else 'None'}")
            transcribed_text, trans_err, detected_lang_from_stt = self.whisper_handler_module.transcribe_audio(
                audio_np_array=audio_np_array_web_admin, language=None, task="transcribe"
            )

            if trans_err:
                result["error"] = f"Admin Web Transcription error: {trans_err}"
                web_logger.error(f"WebAppBridge-Admin: STT Error - {result['error']}")
                return result 
            
            if not transcribed_text:
                web_logger.info("WebAppBridge-Admin: No speech detected from web input.")
                result["user_transcription"] = "" 
                if gui_callbacks and callable(gui_callbacks.get('add_user_message_to_display')):
                    gui_callbacks['add_user_message_to_display']("[Silent/Unclear Audio from Web UI]", source="web_admin")
                return result
            
            result["user_transcription"] = transcribed_text
            web_logger.info(f"WebAppBridge-Admin: Transcription successful: '{transcribed_text[:70]}...', Detected Lang: {detected_lang_from_stt}")
            
            if gui_callbacks and callable(gui_callbacks.get('add_user_message_to_display')):
                gui_callbacks['add_user_message_to_display'](transcribed_text, source="web_admin")

        except Exception as e_stt:
            result["error"] = f"Admin Web STT processing failed: {str(e_stt)}"
            web_logger.error(f"WebAppBridge-Admin: STT Exception - {result['error']}", exc_info=True)
            return result

        if not self.get_ollama_ready(): 
            result["error"] = "Ollama (LLM) service not ready for admin web input."
            web_logger.error(f"WebAppBridge-Admin: LLM Aborted - {result['error']}")
            return result
        
        ollama_response_text_for_web = "Error: LLM processing failed."
        
        with global_states_lock: 
            try:
                assistant_state_snapshot_for_prompt = assistant_state.copy()
                user_state_snapshot_for_prompt = user_state.copy()
                chat_history_snapshot_for_prompt = chat_history[:]

                current_lang_code_for_state = "en" 
                if detected_lang_from_stt and detected_lang_from_stt in ["ru", "en"]:
                    current_lang_code_for_state = detected_lang_from_stt
                elif assistant_state_snapshot_for_prompt.get("last_used_language") in ["ru", "en"]:
                    current_lang_code_for_state = assistant_state_snapshot_for_prompt.get("last_used_language")

                selected_bark_voice_preset_for_web = config.BARK_VOICE_PRESET_EN
                language_instruction_for_llm = config.LANGUAGE_INSTRUCTION_NON_RUSSIAN
                if current_lang_code_for_state == "ru":
                    selected_bark_voice_preset_for_web = config.BARK_VOICE_PRESET_RU
                    language_instruction_for_llm = config.LANGUAGE_INSTRUCTION_RUSSIAN
                
                web_logger.debug(f"WebAppBridge-Admin: Language for LLM & TTS: {current_lang_code_for_state}, Preset: {selected_bark_voice_preset_for_web}")

                target_customer_id_for_prompt = None
                customer_state_for_prompt_str = "{}"
                is_customer_context_active_for_prompt = False
                history_to_scan = chat_history_snapshot_for_prompt[-(config.MAX_HISTORY_TURNS // 2 or 1):]
                for turn in reversed(history_to_scan):
                    assistant_msg_hist = turn.get("assistant", "")
                    turn_source_hist = turn.get("source", "")
                    if turn_source_hist == "customer_summary_internal":
                        match_summary = re.search(r"\[Сводка по клиенту (\d+)\]", assistant_msg_hist)
                        if match_summary:
                            try:
                                target_customer_id_for_prompt = int(match_summary.group(1))
                                break
                            except ValueError: target_customer_id_for_prompt = None
                
                if target_customer_id_for_prompt:
                    loaded_customer_state = state_manager.load_or_initialize_customer_state(target_customer_id_for_prompt, gui_callbacks=None) 
                    if loaded_customer_state and loaded_customer_state.get("user_id") == target_customer_id_for_prompt:
                        customer_state_for_prompt_str = json.dumps(loaded_customer_state, indent=2, ensure_ascii=False)
                        is_customer_context_active_for_prompt = True
                    else:
                        target_customer_id_for_prompt = None 

                assistant_state_for_this_prompt = assistant_state_snapshot_for_prompt.copy()
                assistant_state_for_this_prompt["last_used_language"] = current_lang_code_for_state
                admin_current_name = assistant_state_for_this_prompt.get("admin_name", "Partner")

                format_kwargs_for_ollama = {
                    "admin_name_value": admin_current_name,
                    "assistant_admin_name_current_value": admin_current_name,
                    "is_customer_context_active": is_customer_context_active_for_prompt,
                    "active_customer_id": str(target_customer_id_for_prompt) if target_customer_id_for_prompt else "N/A",
                    "active_customer_state_string": customer_state_for_prompt_str,
                }
                expected_keys_for_response = ["answer_to_user", "updated_user_state", "updated_assistant_state", "updated_active_customer_state"]

                web_logger.info(f"WebAppBridge-Admin: Calling Ollama with admin prompt. Input: '{transcribed_text[:50]}...'")
                ollama_data, ollama_error = self.ollama_handler_module.call_ollama_for_chat_response(
                    prompt_template_to_use=config.OLLAMA_PROMPT_TEMPLATE,
                    transcribed_text=transcribed_text,
                    current_chat_history=chat_history_snapshot_for_prompt, 
                    current_user_state=user_state_snapshot_for_prompt,     
                    current_assistant_state=assistant_state_for_this_prompt, 
                    language_instruction=language_instruction_for_llm,
                    format_kwargs=format_kwargs_for_ollama,
                    expected_keys_override=expected_keys_for_response,
                    gui_callbacks=None 
                )

                current_turn_for_history = {"user": transcribed_text, "source": "web_admin", "timestamp": state_manager.get_current_timestamp_iso()}
                if detected_lang_from_stt:
                    current_turn_for_history["detected_language_code_for_web_display"] = detected_lang_from_stt


                if ollama_error:
                    web_logger.error(f"WebAppBridge-Admin: Ollama call failed: {ollama_error}")
                    result["error"] = f"LLM error: {ollama_error}"
                    
                    err_lang = assistant_state.get("last_used_language", "en") 
                    ollama_response_text_for_web = "An internal error occurred (LLM)."
                    if err_lang == "ru": ollama_response_text_for_web = "Произошла внутренняя ошибка (LLM)."
                    
                    current_turn_for_history["assistant"] = f"[LLM Error from Web UI: {ollama_response_text_for_web}]"
                    if gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')):
                        gui_callbacks['add_assistant_message_to_display'](ollama_response_text_for_web, is_error=True, source="web_admin_error")

                else: 
                    web_logger.info("WebAppBridge-Admin: Ollama call successful. Updating global states.")
                    ollama_response_text_for_web = ollama_data.get("answer_to_user", "Error: LLM did not provide an answer.")
                    new_admin_user_state_from_llm = ollama_data.get("updated_user_state", {})
                    new_assistant_state_changes_from_llm = ollama_data.get("updated_assistant_state", {})
                    updated_customer_state_from_llm = ollama_data.get("updated_active_customer_state")

                    user_state.clear()
                    user_state.update(new_admin_user_state_from_llm)
                    
                    current_global_assistant_state_snapshot = assistant_state.copy() 
                    for key, value_from_llm in new_assistant_state_changes_from_llm.items():
                        if key == "internal_tasks" and isinstance(value_from_llm, dict) and isinstance(current_global_assistant_state_snapshot.get(key), dict):
                            for task_type in ["pending", "in_process", "completed"]:
                                new_tasks = value_from_llm.get(task_type, [])
                                if not isinstance(new_tasks, list): new_tasks = [str(new_tasks)] 
                                existing_tasks = current_global_assistant_state_snapshot[key].get(task_type, [])
                                if not isinstance(existing_tasks, list): existing_tasks = [str(existing_tasks)]
                                current_global_assistant_state_snapshot[key][task_type] = list(dict.fromkeys([str(t) for t in existing_tasks] + [str(t) for t in new_tasks]))
                        else:
                            current_global_assistant_state_snapshot[key] = value_from_llm
                    current_global_assistant_state_snapshot["last_used_language"] = current_lang_code_for_state
                    assistant_state.clear()
                    assistant_state.update(current_global_assistant_state_snapshot)

                    if updated_customer_state_from_llm and isinstance(updated_customer_state_from_llm, dict) and target_customer_id_for_prompt:
                        if updated_customer_state_from_llm.get("user_id") == target_customer_id_for_prompt:
                            if state_manager.save_customer_state(target_customer_id_for_prompt, updated_customer_state_from_llm, gui_callbacks=None):
                                web_logger.info(f"WebAppBridge-Admin: Updated state for context customer {target_customer_id_for_prompt} via web interaction.")
                        else:
                            web_logger.warning(f"WebAppBridge-Admin: LLM returned updated_active_customer_state for mismatched ID via web. Not saving.")
                    
                    current_turn_for_history["assistant"] = ollama_response_text_for_web
                    
                    if gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')):
                        gui_callbacks['add_assistant_message_to_display'](ollama_response_text_for_web, source="web_admin")


                chat_history.append(current_turn_for_history)
                chat_history = state_manager.save_states(chat_history, user_state, assistant_state, gui_callbacks=None) 
                
                if gui and gui_callbacks:
                    if callable(gui_callbacks.get('update_chat_display_from_list')): gui_callbacks['update_chat_display_from_list'](chat_history)
                    if callable(gui_callbacks.get('update_todo_list')): gui_callbacks['update_todo_list'](user_state.get("todos", []))
                    if callable(gui_callbacks.get('update_calendar_events_list')): gui_callbacks['update_calendar_events_list'](user_state.get("calendar_events", []))
                    
                    asst_tasks_web = assistant_state.get("internal_tasks", {});
                    if not isinstance(asst_tasks_web, dict): asst_tasks_web = {} 
                    if callable(gui_callbacks.get('update_kanban_pending')): gui_callbacks['update_kanban_pending'](asst_tasks_web.get("pending", []))
                    if callable(gui_callbacks.get('update_kanban_in_process')): gui_callbacks['update_kanban_in_process'](asst_tasks_web.get("in_process", []))
                    if callable(gui_callbacks.get('update_kanban_completed')): gui_callbacks['update_kanban_completed'](asst_tasks_web.get("completed", []))
                
                result["llm_text_response"] = ollama_response_text_for_web

            except Exception as e_llm_admin_web:
                result["error"] = f"Admin Web LLM processing/state update failed: {str(e_llm_admin_web)}"
                web_logger.error(f"WebAppBridge-Admin: LLM/State Exception - {result['error']}", exc_info=True)
                err_lang_state = assistant_state.get("last_used_language", "en")
                ollama_response_text_for_web = "An internal error occurred during LLM/state processing."
                if err_lang_state == "ru": ollama_response_text_for_web = "Внутренняя ошибка при обработке LLM/состояния."

        if not result["llm_text_response"] or result["error"]: 
            web_logger.info(f"WebAppBridge-Admin: Skipping TTS due to LLM error ('{result['error']}') or no LLM response.")
            return result

        if not self.tts_manager_module.is_tts_ready():
            web_logger.warning("WebAppBridge-Admin: TTS service not ready. Returning text only for admin web.")
            return result 
        
        try:
            web_logger.debug(f"WebAppBridge-Admin: Preparing to synthesize TTS for: '{result['llm_text_response'][:70]}...'")
            bark_engine_for_web_admin = self.tts_manager_module.get_bark_model_instance()
            if not bark_engine_for_web_admin:
                web_logger.error("WebAppBridge-Admin: Failed to get Bark TTS engine instance.")
                return result

            tts_voice_preset_to_use = config.BARK_VOICE_PRESET_EN 
            with global_states_lock: 
                 lang_for_tts = assistant_state.get("last_used_language", "en")
            if lang_for_tts == "ru":
                tts_voice_preset_to_use = config.BARK_VOICE_PRESET_RU
            
            web_logger.info(f"WebAppBridge-Admin: Using TTS voice preset: {tts_voice_preset_to_use} (based on lang: {lang_for_tts})")

            audio_array, samplerate = bark_engine_for_web_admin.synthesize_speech_to_array(
                result["llm_text_response"],
                generation_params={"voice_preset": tts_voice_preset_to_use}
            )

            if audio_array is not None and samplerate is not None and audio_array.size > 0 :
                if not file_utils.ensure_folder(config.WEB_UI_TTS_SERVE_FOLDER, gui_callbacks=None): 
                    web_logger.critical(f"WebAppBridge-Admin: Critical - Could not create/access TTS serve folder: {config.WEB_UI_TTS_SERVE_FOLDER}")
                    result["error"] = (result["error"] or "") + " Server error: Cannot save TTS audio (folder issue)."
                    return result

                tts_filename = f"web_admin_tts_{uuid.uuid4().hex}.wav"
                tts_filepath = os.path.join(config.WEB_UI_TTS_SERVE_FOLDER, tts_filename)
                
                if audio_array.dtype != np.float32: 
                    web_logger.debug(f"WebAppBridge-Admin: Converting TTS audio array from {audio_array.dtype} to float32 for soundfile.")
                    audio_array = audio_array.astype(np.float32)
                
                sf.write(tts_filepath, audio_array, samplerate, subtype='PCM_16')
                
                result["tts_audio_filename"] = tts_filename 
                web_logger.info(f"WebAppBridge-Admin: Synthesized TTS and saved to {tts_filepath}")
            else:
                web_logger.error("WebAppBridge-Admin: TTS synthesis returned no audio data or samplerate.")
                result["error"] = (result["error"] or "") + " TTS synthesis failed to produce audio."
        except Exception as e_tts:
            result["error"] = (result["error"] or "") + f" TTS synthesis/saving failed: {str(e_tts)}"
            web_logger.error(f"WebAppBridge-Admin: TTS Exception - {result['error']}", exc_info=True)

        web_logger.info(f"WebAppBridge-Admin: Interaction processing complete. Result: { {k: (str(v)[:70] + '...' if isinstance(v, str) and len(v) > 70 else v) for k,v in result.items()} }")
        return result


    def get_system_status_for_web(self):
        ollama_stat_text, ollama_stat_type = "N/A", "unknown"
        if self.get_ollama_ready():
            ollama_stat_text, ollama_stat_type = "Ready", "ready"
        else: 
            _, ollama_ping_msg_from_last_check = self.ollama_handler_module.check_ollama_server_and_model()
            if self.get_ollama_ready():
                 ollama_stat_text, ollama_stat_type = "Ready", "ready"
            else:
                if "timeout" in (ollama_ping_msg_from_last_check or "").lower(): ollama_stat_type = "timeout"
                elif "connection" in (ollama_ping_msg_from_last_check or "").lower(): ollama_stat_type = "conn_error"
                elif "http" in (ollama_ping_msg_from_last_check or "").lower() and "error" in (ollama_ping_msg_from_last_check or "").lower(): ollama_stat_type = "error" 
                else: ollama_stat_type = "error" 
                ollama_stat_text = ollama_ping_msg_from_last_check[:30] if ollama_ping_msg_from_last_check else "Error"
        
        main_app_status_from_gui = "N/A"
        try: 
            main_app_status_from_gui = self.get_main_app_status_label()
        except Exception as e_gui_status_get:
             web_logger.debug(f"WebAppBridge: Error getting main app status label for web: {e_gui_status_get}", exc_info=False)
             main_app_status_from_gui = "Error (GUI Status)" 
        
        tele_stat_text, tele_stat_type = "N/A", "unknown"
        if self.telegram_handler_instance_ref and hasattr(self.telegram_handler_instance_ref, 'get_status'):
            current_tele_status = self.telegram_handler_instance_ref.get_status()
            tele_stat_type = current_tele_status 
            status_text_map = {
                "loading": "Loading...", "polling": "Polling", "error": "Error",
                "no_token": "No Token", "no_admin": "No Admin ID",
                "bad_token": "Bad Token", "net_error": "Network Err", "off": "Off"
            }
            tele_stat_text = status_text_map.get(current_tele_status, current_tele_status.capitalize())
        elif not config.TELEGRAM_BOT_TOKEN:
            tele_stat_text, tele_stat_type = "No Token", "no_token"
        elif not config.TELEGRAM_ADMIN_USER_ID:
            tele_stat_text, tele_stat_type = "No Admin ID", "no_admin"
        else: 
            tele_stat_text, tele_stat_type = "Unavailable", "unknown"

        return {
            "ollama": {"text": ollama_stat_text, "type": ollama_stat_type},
            "whisper": {"text": self.whisper_handler_module.get_status_short(), "type": self.whisper_handler_module.get_status_type()},
            "bark": {"text": self.tts_manager_module.get_status_short(), "type": self.tts_manager_module.get_status_type()},
            "telegram": {"text": tele_stat_text, "type": tele_stat_type},
            "app_overall_status": main_app_status_from_gui
        }
# --- End WebApp Bridge Class ---

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
    logger.info(f"ADMIN_LLM_FLOW ({source}): {function_signature_for_log} - Starting.")
    
    if source == "gui" and gui_callbacks and callable(gui_callbacks.get('act_status_update')):
        gui_callbacks['act_status_update']("ACT: BUSY", "busy")
    
    try:
        # Make a local variable for assistant_response_text to avoid UnboundLocalError if ollama_error is True
        assistant_response_text = "Error: LLM processing did not complete."

        with global_states_lock: 
            assistant_state_snapshot_for_prompt = assistant_state.copy()
            user_state_snapshot_for_prompt = user_state.copy()
            chat_history_snapshot_for_prompt = chat_history[:]

            current_lang_code_for_state = "en" 
            if detected_language_code and detected_language_code in ["ru", "en"]:
                current_lang_code_for_state = detected_language_code
            elif assistant_state_snapshot_for_prompt.get("last_used_language") in ["ru", "en"]:
                current_lang_code_for_state = assistant_state_snapshot_for_prompt.get("last_used_language")

            selected_bark_voice_preset = config.BARK_VOICE_PRESET_EN
            language_instruction_for_llm = config.LANGUAGE_INSTRUCTION_NON_RUSSIAN
            if current_lang_code_for_state == "ru":
                selected_bark_voice_preset = config.BARK_VOICE_PRESET_RU
                language_instruction_for_llm = config.LANGUAGE_INSTRUCTION_RUSSIAN

            if source == "gui" and gui_callbacks and callable(gui_callbacks.get('add_user_message_to_display')):
                gui_callbacks['add_user_message_to_display'](input_text, source=source)

            if gui_callbacks and callable(gui_callbacks.get('status_update')):
                gui_callbacks['status_update'](f"Thinking (Admin {source})...")
            if gui_callbacks and callable(gui_callbacks.get('mind_status_update')):
                gui_callbacks['mind_status_update']("MIND: THK", "thinking")

            target_customer_id_for_prompt = None
            customer_state_for_prompt_str = "{}"
            is_customer_context_active_for_prompt = False
            history_to_scan = chat_history_snapshot_for_prompt[-(config.MAX_HISTORY_TURNS // 2 or 1):] 
            for turn in reversed(history_to_scan):
                assistant_message_hist = turn.get("assistant", "")
                turn_source_hist = turn.get("source", "") 
                if turn_source_hist == "customer_summary_internal": 
                    match_summary = re.search(r"\[Сводка по клиенту (\d+)\]", assistant_message_hist)
                    if match_summary:
                        try:
                            target_customer_id_for_prompt = int(match_summary.group(1))
                            logger.info(f"ADMIN_LLM_FLOW ({source}): Found customer context ID {target_customer_id_for_prompt} from 'customer_summary_internal'.")
                            break
                        except ValueError:
                            logger.warning(f"ADMIN_LLM_FLOW ({source}): Found non-integer customer ID in summary: {match_summary.group(1)}")
                            target_customer_id_for_prompt = None 
            
            if target_customer_id_for_prompt:
                try:
                    loaded_customer_state = state_manager.load_or_initialize_customer_state(target_customer_id_for_prompt, gui_callbacks)
                    if loaded_customer_state and loaded_customer_state.get("user_id") == target_customer_id_for_prompt :
                        customer_state_for_prompt_str = json.dumps(loaded_customer_state, indent=2, ensure_ascii=False)
                        is_customer_context_active_for_prompt = True
                    else: target_customer_id_for_prompt = None 
                except Exception as e_load_ctx_cust:
                    logger.error(f"ADMIN_LLM_FLOW ({source}): Exception loading state for context customer {target_customer_id_for_prompt}: {e_load_ctx_cust}", exc_info=True)
                    target_customer_id_for_prompt = None

            assistant_state_for_this_prompt = assistant_state_snapshot_for_prompt.copy()
            assistant_state_for_this_prompt["last_used_language"] = current_lang_code_for_state 
            admin_current_name = assistant_state_for_this_prompt.get("admin_name", "Partner") 

            format_kwargs_for_ollama = {
                "admin_name_value": admin_current_name, 
                "assistant_admin_name_current_value": admin_current_name, 
                "is_customer_context_active": is_customer_context_active_for_prompt,
                "active_customer_id": str(target_customer_id_for_prompt) if target_customer_id_for_prompt else "N/A",
                "active_customer_state_string": customer_state_for_prompt_str
            }
            expected_keys_for_response = ["answer_to_user", "updated_user_state", "updated_assistant_state", "updated_active_customer_state"]

            logger.debug(f"ADMIN_LLM_FLOW ({source}): Calling Ollama. CustContext Active: {is_customer_context_active_for_prompt}, CustID: {target_customer_id_for_prompt}")
            ollama_data, ollama_error = ollama_handler.call_ollama_for_chat_response(
                prompt_template_to_use=config.OLLAMA_PROMPT_TEMPLATE,
                transcribed_text=input_text, 
                current_chat_history=chat_history_snapshot_for_prompt, 
                current_user_state=user_state_snapshot_for_prompt,     
                current_assistant_state=assistant_state_for_this_prompt, 
                language_instruction=language_instruction_for_llm,
                format_kwargs=format_kwargs_for_ollama,
                expected_keys_override=expected_keys_for_response,
                gui_callbacks=gui_callbacks 
            )

            current_turn_for_history = {"user": input_text, "source": source, "timestamp": state_manager.get_current_timestamp_iso()}
            if detected_language_code : 
                current_turn_for_history[f"detected_language_code_for_{source}_display"] = detected_language_code


            if ollama_error:
                logger.error(f"ADMIN_LLM_FLOW ({source}): Ollama call failed: {ollama_error}")
                short_code, status_type = _parse_ollama_error_to_short_code(ollama_error)
                if gui_callbacks and callable(gui_callbacks.get('mind_status_update')): gui_callbacks['mind_status_update'](f"MIND: {short_code}", status_type)
                if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update'](f"LLM Error ({source}): {ollama_error[:50]}")

                assistant_response_text = "An internal error occurred while processing your request (admin)."
                if current_lang_code_for_state == "ru": assistant_response_text = "При обработке вашего запроса (админ) произошла внутренняя ошибка."

                current_turn_for_history["assistant"] = f"[LLM Error ({source}): {assistant_response_text}]" 
                if gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')):
                    gui_callbacks['add_assistant_message_to_display'](assistant_response_text, is_error=True, source=f"{source}_error") 
            else: 
                logger.info(f"ADMIN_LLM_FLOW ({source}): Ollama call successful.")
                if gui_callbacks and callable(gui_callbacks.get('mind_status_update')): gui_callbacks['mind_status_update']("MIND: RDY", "ready")

                assistant_response_text = ollama_data.get("answer_to_user", "Error: LLM did not provide an answer.")
                new_admin_user_state_from_llm = ollama_data.get("updated_user_state", {})
                new_assistant_state_changes_from_llm = ollama_data.get("updated_assistant_state", {})
                updated_customer_state_from_llm = ollama_data.get("updated_active_customer_state")

                current_gui_theme_from_llm = new_admin_user_state_from_llm.get("gui_theme", current_gui_theme)
                if current_gui_theme_from_llm != current_gui_theme and current_gui_theme_from_llm in [config.GUI_THEME_LIGHT, config.GUI_THEME_DARK]:
                    if gui and callable(gui_callbacks.get('apply_application_theme')):
                        gui_callbacks['apply_application_theme'](current_gui_theme_from_llm)
                        current_gui_theme = current_gui_theme_from_llm 
                new_admin_user_state_from_llm["gui_theme"] = current_gui_theme 

                current_font_size_from_llm = new_admin_user_state_from_llm.get("chat_font_size", current_chat_font_size_applied)
                try: current_font_size_from_llm = int(current_font_size_from_llm)
                except (ValueError, TypeError): current_font_size_from_llm = current_chat_font_size_applied
                clamped_font_size = max(config.MIN_CHAT_FONT_SIZE, min(current_font_size_from_llm, config.MAX_CHAT_FONT_SIZE))
                if clamped_font_size != current_font_size_from_llm : current_font_size_from_llm = clamped_font_size 
                if current_font_size_from_llm != current_chat_font_size_applied:
                    if gui and callable(gui_callbacks.get('apply_chat_font_size')):
                        gui_callbacks['apply_chat_font_size'](current_font_size_from_llm)
                        current_chat_font_size_applied = current_font_size_from_llm 
                new_admin_user_state_from_llm["chat_font_size"] = current_chat_font_size_applied 

                user_state.clear(); user_state.update(new_admin_user_state_from_llm)

                current_global_assistant_state_snapshot_for_update = assistant_state.copy() 
                for key, value_from_llm in new_assistant_state_changes_from_llm.items():
                    if key == "internal_tasks" and isinstance(value_from_llm, dict) and isinstance(current_global_assistant_state_snapshot_for_update.get(key), dict):
                        for task_type in ["pending", "in_process", "completed"]:
                            new_tasks = value_from_llm.get(task_type, [])
                            if not isinstance(new_tasks, list): new_tasks = [str(new_tasks)] 
                            existing_tasks = current_global_assistant_state_snapshot_for_update[key].get(task_type, [])
                            if not isinstance(existing_tasks, list): existing_tasks = [str(existing_tasks)] 
                            current_global_assistant_state_snapshot_for_update[key][task_type] = list(dict.fromkeys([str(t) for t in existing_tasks] + [str(t) for t in new_tasks]))
                    else:
                        current_global_assistant_state_snapshot_for_update[key] = value_from_llm
                current_global_assistant_state_snapshot_for_update["last_used_language"] = current_lang_code_for_state 
                assistant_state.clear()
                assistant_state.update(current_global_assistant_state_snapshot_for_update)
                logger.debug(f"ADMIN_LLM_FLOW ({source}): Global assistant_state prepared for saving.")

                if gui_callbacks:
                    if callable(gui_callbacks.get('update_todo_list')): gui_callbacks['update_todo_list'](user_state.get("todos", []))
                    if callable(gui_callbacks.get('update_calendar_events_list')):
                        gui_callbacks['update_calendar_events_list'](user_state.get("calendar_events", []))
                    asst_tasks = assistant_state.get("internal_tasks", {});
                    if not isinstance(asst_tasks, dict): asst_tasks = {} 
                    if callable(gui_callbacks.get('update_kanban_pending')): gui_callbacks['update_kanban_pending'](asst_tasks.get("pending", []))
                    if callable(gui_callbacks.get('update_kanban_in_process')): gui_callbacks['update_kanban_in_process'](asst_tasks.get("in_process", []))
                    if callable(gui_callbacks.get('update_kanban_completed')): gui_callbacks['update_kanban_completed'](asst_tasks.get("completed", []))

                if updated_customer_state_from_llm and isinstance(updated_customer_state_from_llm, dict) and target_customer_id_for_prompt:
                    if updated_customer_state_from_llm.get("user_id") == target_customer_id_for_prompt:
                        if state_manager.save_customer_state(target_customer_id_for_prompt, updated_customer_state_from_llm, gui_callbacks): 
                            logger.info(f"ADMIN_LLM_FLOW ({source}): Updated state for context customer {target_customer_id_for_prompt}.")
                    else:
                        logger.warning(f"ADMIN_LLM_FLOW ({source}): LLM returned updated_active_customer_state for mismatched ID. Not saving customer state.")
                
                current_turn_for_history["assistant"] = assistant_response_text 

                if source == "gui" and gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')):
                     gui_callbacks['add_assistant_message_to_display'](assistant_response_text, source=source)
                elif (source == "telegram_admin" or source == "telegram_voice_admin") and gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')):
                    gui_callbacks['add_assistant_message_to_display'](assistant_response_text, source=source) 
                    if callable(gui_callbacks.get('status_update')):
                        gui_callbacks['status_update'](f"Iri-shka (to Admin TG): {assistant_response_text[:40]}...")

            chat_history.append(current_turn_for_history)
            chat_history = state_manager.save_states(chat_history, user_state, assistant_state, gui_callbacks) 
            
            if gui_callbacks and callable(gui_callbacks.get('memory_status_update')):
                gui_callbacks['memory_status_update']("MEM: SAVED", "saved") 
            if gui and callable(gui_callbacks.get('update_chat_display_from_list')): 
                gui_callbacks['update_chat_display_from_list'](chat_history)

        if source == "gui": 
            if tts_manager.is_tts_ready() and not ollama_error: 
                def _deferred_gui_display_on_playback_admin(): 
                    if gui_callbacks and callable(gui_callbacks.get('status_update')):
                        gui_callbacks['status_update'](f"Speaking (Admin): {assistant_response_text[:40]}...")
                
                current_persona_name_tts = "Iri-shka"
                with global_states_lock: current_persona_name_tts = assistant_state.get("persona_name", "Iri-shka") 
                
                tts_manager.start_speaking_response(
                    assistant_response_text, current_persona_name_tts, selected_bark_voice_preset, gui_callbacks,
                    on_actual_playback_start_gui_callback=_deferred_gui_display_on_playback_admin)
        
        elif source == "telegram_admin" or source == "telegram_voice_admin": 
            logger.info(f"ADMIN_LLM_FLOW: Output handler for source '{source}'. Attempting Telegram reply to admin.")
            if telegram_bot_handler_instance and telegram_bot_handler_instance.async_loop and config.TELEGRAM_ADMIN_USER_ID and not ollama_error:
                try:
                    admin_id_int = int(config.TELEGRAM_ADMIN_USER_ID)
                    if config.TELEGRAM_REPLY_WITH_TEXT:
                        logger.info(f"ADMIN_LLM_FLOW: Sending TEXT reply to admin TG: '{assistant_response_text[:70]}...'")
                        text_send_future = asyncio.run_coroutine_threadsafe(
                            telegram_bot_handler_instance.send_text_message_to_user(admin_id_int, assistant_response_text),
                            telegram_bot_handler_instance.async_loop)
                        text_send_future.result(timeout=15) 
                    if config.TELEGRAM_REPLY_WITH_VOICE:
                        logger.info(f"ADMIN_LLM_FLOW: Sending VOICE reply to admin TG. Lang: {current_lang_code_for_state}, Preset: {selected_bark_voice_preset}")
                        _send_voice_reply_to_telegram_user(admin_id_int, assistant_response_text, selected_bark_voice_preset) 
                except asyncio.TimeoutError as te_async:
                    logger.error(f"ADMIN_LLM_FLOW: Timeout sending reply to admin Telegram ({source}). Error: {te_async}", exc_info=True)
                except Exception as e_tg_send_admin:
                    logger.error(f"ADMIN_LLM_FLOW: Error sending reply to admin Telegram ({source}): {e_tg_send_admin}", exc_info=True)
            elif ollama_error and telegram_bot_handler_instance : 
                 try:
                    admin_id_int = int(config.TELEGRAM_ADMIN_USER_ID) # type: ignore
                    err_text_for_tg = "LLM processing failed for your request."
                    # Access assistant_state under lock for language preference
                    with global_states_lock:
                        if assistant_state.get("last_used_language", "en") == "ru": 
                            err_text_for_tg = "Ошибка обработки вашего запроса в LLM."
                    asyncio.run_coroutine_threadsafe(
                        telegram_bot_handler_instance.send_text_message_to_user(admin_id_int, err_text_for_tg), # type: ignore
                        telegram_bot_handler_instance.async_loop # type: ignore
                    ).result(timeout=10) # type: ignore
                 except Exception as e_tg_err_send: logger.error(f"Failed to send LLM error to admin TG: {e_tg_err_send}")

        if source == "gui" and gui_callbacks:
            enable_speak_btn = whisper_handler.is_whisper_ready()
            if callable(gui_callbacks.get('speak_button_update')):
                gui_callbacks['speak_button_update'](enable_speak_btn, "Speak" if enable_speak_btn else "HEAR NRDY")
            
            is_speaking_gui = tts_manager.current_tts_thread and tts_manager.current_tts_thread.is_alive()
            if callable(gui_callbacks.get('status_update')) and not is_speaking_gui:
                 gui_callbacks['status_update']("Ready (Admin)." if enable_speak_btn else "Hearing N/A (Admin).")

    finally:
        if source == "gui" and gui_callbacks and callable(gui_callbacks.get('act_status_update')):
            gui_callbacks['act_status_update']("ACT: IDLE", "idle")
        logger.info(f"ADMIN_LLM_FLOW ({source}): {function_signature_for_log} - Processing finished.")


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
    file_utils.ensure_folder(config.TELEGRAM_TTS_TEMP_FOLDER) 
    
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
        if audio_arr is not None and sr is not None and audio_arr.size > 0: 
            if target_sr is None: target_sr = sr
            if sr != target_sr: logger.warning(f"Samplerate mismatch in TTS chunks (expected {target_sr}, got {sr}). Skipping chunk."); continue
            if first_valid_chunk and config.BARK_SILENCE_DURATION_MS > 0:
                silence = np.zeros(int(config.BARK_SILENCE_DURATION_MS / 1000 * target_sr), dtype=audio_arr.dtype)
                all_audio_pieces.append(silence)
            all_audio_pieces.append(audio_arr); first_valid_chunk = True
        else: logger.warning(f"TTS synthesize_speech_to_array failed or returned empty for chunk {idx} for user {target_user_id}")
    
    if all_audio_pieces and target_sr is not None:
        merged_audio = np.concatenate(all_audio_pieces)
        try:
            sf.write(temp_tts_merged_wav_path, merged_audio, target_sr) 
            pydub_seg = PydubAudioSegment.from_wav(temp_tts_merged_wav_path) 
            pydub_seg = pydub_seg.set_frame_rate(16000).set_channels(1) 
            pydub_seg.export(temp_tts_ogg_path, format="ogg", codec="libopus", bitrate="24k") 
            
            send_future = None
            if hasattr(telegram_bot_handler_instance, 'send_voice_message_to_user'):
                send_future = asyncio.run_coroutine_threadsafe(
                    telegram_bot_handler_instance.send_voice_message_to_user(target_user_id, temp_tts_ogg_path), 
                    telegram_bot_handler_instance.async_loop) 
            else:
                logger.error(f"TelegramBotHandler is missing 'send_voice_message_to_user' method.")

            if send_future:
                send_future.result(timeout=20) 
                logger.info(f"Voice reply sent to user {target_user_id} using {temp_tts_ogg_path}")
        except Exception as e_send_v: logger.error(f"Error processing/sending voice to {target_user_id}: {e_send_v}", exc_info=True)
    else: logger.error(f"No valid audio pieces synthesized for user {target_user_id}. Cannot send voice.")
    
    for f_path in [temp_tts_merged_wav_path, temp_tts_ogg_path]:
        if os.path.exists(f_path):
            try: os.remove(f_path)
            except OSError as e: logger.warning(f"Could not remove temp TTS file {f_path}: {e}") 


def handle_customer_interaction_package(customer_user_id: int):
    global chat_history, user_state, assistant_state
    
    function_signature_for_log = f"handle_customer_interaction_package(cust_id={customer_user_id})"
    logger.info(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Starting processing.")
    
    if gui_callbacks and callable(gui_callbacks.get('act_status_update')):
        gui_callbacks['act_status_update']("ACT: BUSY", "busy")
    try:
        customer_state_obj = {}
        try: 
            customer_state_obj = state_manager.load_or_initialize_customer_state(customer_user_id, gui_callbacks)
        except Exception as e: 
            logger.error(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - CRITICAL Error loading customer state: {e}", exc_info=True)
            return 
        
        current_stage = customer_state_obj.get("conversation_stage")
        if current_stage not in ["aggregating_messages", "acknowledged_pending_llm"]: 
            logger.warning(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Customer not in expected stage (is '{current_stage}'). Skipping for now."); 
            return

        interaction_blob_parts = []
        for msg_entry in customer_state_obj.get("chat_history", []):
            if msg_entry.get("sender") == "bot":
                interaction_blob_parts.append(f"Bot: {msg_entry.get('text')}")
            elif msg_entry.get("sender") == "customer":
                interaction_blob_parts.append(f"Customer ({customer_user_id}): {msg_entry.get('text')}")
        customer_interaction_text_blob_for_prompt = "\n".join(interaction_blob_parts)
        if not customer_interaction_text_blob_for_prompt:
            logger.warning(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - No interaction text found in customer chat history. Cannot proceed.")
            customer_state_obj["conversation_stage"] = "error_no_history_for_llm"
            state_manager.save_customer_state(customer_user_id, customer_state_obj, gui_callbacks)
            return

        with global_states_lock:
            assistant_state_snapshot_for_customer_llm = assistant_state.copy()
            admin_name_for_customer_prompt = assistant_state_snapshot_for_customer_llm.get("admin_name", config.DEFAULT_ASSISTANT_STATE["admin_name"])

        format_kwargs_customer = {
            "admin_name_value": admin_name_for_customer_prompt,
            "actual_thanks_and_forwarded_message_value": config.TELEGRAM_NON_ADMIN_THANKS_AND_FORWARDED,
            "customer_user_id": str(customer_user_id),
            "customer_state_string": json.dumps(customer_state_obj, indent=2, ensure_ascii=False),
            "customer_interaction_text_blob": customer_interaction_text_blob_for_prompt,
        }
        expected_keys_customer = ["updated_customer_state", "updated_assistant_state", "message_for_admin", "polite_followup_message_for_customer"]

        logger.info(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Calling Ollama for customer interaction summary.")
        ollama_data_cust, ollama_error_cust = ollama_handler.call_ollama_for_chat_response(
            prompt_template_to_use=config.OLLAMA_CUSTOMER_PROMPT_TEMPLATE_V3,
            transcribed_text="", 
            current_chat_history=[], 
            current_user_state=customer_state_obj, 
            current_assistant_state=assistant_state_snapshot_for_customer_llm, 
            format_kwargs=format_kwargs_customer,
            expected_keys_override=expected_keys_customer,
            gui_callbacks=gui_callbacks 
        )

        if ollama_error_cust:
            logger.error(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Ollama error processing customer: {ollama_error_cust}")
            customer_state_obj["conversation_stage"] = "error_llm_processing"
            state_manager.save_customer_state(customer_user_id, customer_state_obj, gui_callbacks)
            error_admin_msg = f"{config.TELEGRAM_NON_ADMIN_PROCESSING_ERROR_TO_ADMIN_PREFIX} {customer_user_id}: {ollama_error_cust}"
            
            with global_states_lock: 
                admin_chat_turn = {
                    "user": f"[System Alert: Customer LLM Error ID {customer_user_id}]",
                    "assistant": error_admin_msg,
                    "source": "customer_llm_error_internal",
                    "timestamp": state_manager.get_current_timestamp_iso()
                }
                chat_history.append(admin_chat_turn)
                chat_history = state_manager.save_states(chat_history, user_state, assistant_state, gui_callbacks)
                if gui and callable(gui_callbacks.get('update_chat_display_from_list')):
                    gui_callbacks['update_chat_display_from_list'](chat_history)

            if telegram_bot_handler_instance and config.TELEGRAM_ADMIN_USER_ID:
                try:
                    asyncio.run_coroutine_threadsafe(
                        telegram_bot_handler_instance.send_text_message_to_user(int(config.TELEGRAM_ADMIN_USER_ID), error_admin_msg), # type: ignore
                        telegram_bot_handler_instance.async_loop # type: ignore
                    ).result(timeout=10)
                except Exception as e_tg_send_err:
                    logger.error(f"Failed to send customer LLM error alert to admin TG: {e_tg_send_err}")
            return 

        logger.info(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - LLM processing successful for customer.")
        updated_customer_state_from_llm = ollama_data_cust.get("updated_customer_state")
        updated_assistant_state_changes_from_llm = ollama_data_cust.get("updated_assistant_state")
        message_for_admin_from_llm = ollama_data_cust.get("message_for_admin")
        polite_followup_for_customer_from_llm = ollama_data_cust.get("polite_followup_message_for_customer")

        if updated_customer_state_from_llm:
            state_manager.save_customer_state(customer_user_id, updated_customer_state_from_llm, gui_callbacks)
            logger.info(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Updated customer state saved for {customer_user_id}.")
        else:
            logger.warning(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - LLM did not return 'updated_customer_state'.")


        if updated_assistant_state_changes_from_llm:
            with global_states_lock:
                current_global_as_snapshot = assistant_state.copy()
                for key, value_llm in updated_assistant_state_changes_from_llm.items():
                    if key == "internal_tasks" and isinstance(value_llm, dict) and isinstance(current_global_as_snapshot.get(key), dict):
                        for task_type in ["pending", "in_process", "completed"]:
                            new_tasks = value_llm.get(task_type, [])
                            if not isinstance(new_tasks, list): new_tasks = [str(new_tasks)]
                            existing_tasks = current_global_as_snapshot[key].get(task_type, [])
                            if not isinstance(existing_tasks, list): existing_tasks = [str(existing_tasks)]
                            current_global_as_snapshot[key][task_type] = list(dict.fromkeys([str(t) for t in existing_tasks] + [str(t) for t in new_tasks]))
                    else:
                        current_global_as_snapshot[key] = value_llm
                assistant_state.clear()
                assistant_state.update(current_global_as_snapshot)
                state_manager.save_assistant_state_only(assistant_state, gui_callbacks) 
                
                if gui and gui_callbacks:
                    asst_tasks_cust = assistant_state.get("internal_tasks", {});
                    if not isinstance(asst_tasks_cust, dict): asst_tasks_cust = {}
                    if callable(gui_callbacks.get('update_kanban_pending')): gui_callbacks['update_kanban_pending'](asst_tasks_cust.get("pending", []))
                    if callable(gui_callbacks.get('update_kanban_in_process')): gui_callbacks['update_kanban_in_process'](asst_tasks_cust.get("in_process", []))
                    if callable(gui_callbacks.get('update_kanban_completed')): gui_callbacks['update_kanban_completed'](asst_tasks_cust.get("completed", []))


        if message_for_admin_from_llm:
            admin_summary_text = f"[Сводка по клиенту {customer_user_id}] {message_for_admin_from_llm}"
            with global_states_lock: 
                admin_chat_turn_cust_summary = {
                    "user": f"[System Report: Customer Interaction ID {customer_user_id}]",
                    "assistant": admin_summary_text,
                    "source": "customer_summary_internal", 
                    "timestamp": state_manager.get_current_timestamp_iso()
                }
                chat_history.append(admin_chat_turn_cust_summary)
                chat_history = state_manager.save_states(chat_history, user_state, assistant_state, gui_callbacks)
                if gui and callable(gui_callbacks.get('update_chat_display_from_list')):
                    gui_callbacks['update_chat_display_from_list'](chat_history)
            
            if telegram_bot_handler_instance and config.TELEGRAM_ADMIN_USER_ID:
                try:
                    asyncio.run_coroutine_threadsafe(
                        telegram_bot_handler_instance.send_text_message_to_user(int(config.TELEGRAM_ADMIN_USER_ID), admin_summary_text), # type: ignore
                        telegram_bot_handler_instance.async_loop # type: ignore
                    ).result(timeout=10)
                    logger.info(f"CUSTOMER_LLM_THREAD: Summary for customer {customer_user_id} sent to admin TG.")
                except Exception as e_tg_send_summary:
                    logger.error(f"Failed to send customer summary to admin TG for {customer_user_id}: {e_tg_send_summary}")

        if polite_followup_for_customer_from_llm and polite_followup_for_customer_from_llm.upper() != "NO_CUSTOMER_FOLLOWUP_NEEDED":
            if telegram_bot_handler_instance:
                try:
                    asyncio.run_coroutine_threadsafe(
                        telegram_bot_handler_instance.send_text_message_to_user(customer_user_id, polite_followup_for_customer_from_llm),
                        telegram_bot_handler_instance.async_loop # type: ignore
                    ).result(timeout=10)
                    logger.info(f"CUSTOMER_LLM_THREAD: Polite text follow-up sent to customer {customer_user_id}.")

                    if config.TELEGRAM_REPLY_WITH_VOICE: 
                        customer_lang_for_tts = "ru" 
                        customer_bark_preset = config.BARK_VOICE_PRESET_RU
                        _send_voice_reply_to_telegram_user(customer_user_id, polite_followup_for_customer_from_llm, customer_bark_preset)
                        logger.info(f"CUSTOMER_LLM_THREAD: Polite voice follow-up sent to customer {customer_user_id}.")

                except Exception as e_tg_send_followup:
                    logger.error(f"Failed to send polite follow-up to customer {customer_user_id}: {e_tg_send_followup}")
        else:
            logger.info(f"CUSTOMER_LLM_THREAD: No polite follow-up needed for customer {customer_user_id} or LLM indicated none.")

    finally:
        if gui_callbacks and callable(gui_callbacks.get('act_status_update')):
            gui_callbacks['act_status_update']("ACT: IDLE", "idle")
        logger.info(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Processing finished (in finally block).")


def process_recorded_audio_and_interact(recorded_sample_rate):
    logger.info(f"Processing recorded audio (Admin GUI). Sample rate: {recorded_sample_rate} Hz.")
    llm_called = False 
    if gui_callbacks and callable(gui_callbacks.get('act_status_update')):
        gui_callbacks['act_status_update']("ACT: BUSY", "busy")
    try:
        audio_float32, audio_frames_for_save = audio_processor.convert_frames_to_numpy(recorded_sample_rate, gui_callbacks)
        if audio_float32 is None:
            logger.warning("Audio processing (convert_frames_to_numpy) returned None.")
            return
        
        if config.SAVE_RECORDINGS_TO_WAV and audio_frames_for_save:
            file_utils.ensure_folder(config.OUTPUT_FOLDER, gui_callbacks)
            filename = f"rec_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
            audio_processor.save_wav_data_to_file(os.path.join(config.OUTPUT_FOLDER, filename), audio_frames_for_save, recorded_sample_rate, gui_callbacks)
        del audio_frames_for_save; gc.collect()

        if not (whisper_handler.WHISPER_CAPABLE and whisper_handler.is_whisper_ready()):
            logger.warning("Whisper not ready for Admin GUI audio.")
            if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update']("Hearing module not ready.")
            return

        if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update']("Transcribing audio (Admin GUI)...")
        transcribed_text, trans_err, detected_lang = whisper_handler.transcribe_audio(
            audio_np_array=audio_float32, language=None, task="transcribe", gui_callbacks=gui_callbacks
        )
        
        if not trans_err and transcribed_text:
            llm_called = True
            _handle_admin_llm_interaction(transcribed_text, source="gui", detected_language_code=detected_lang)
        elif not transcribed_text and not trans_err: 
            logger.info("Admin GUI: No speech detected in audio.")
            lang_for_err_gui = "en"
            with global_states_lock: lang_for_err_gui = assistant_state.get("last_used_language", "en") 
            err_msg_stt_gui = "I didn't catch that..." if lang_for_err_gui == "en" else "Я не расслышала..."
            if gui_callbacks and callable(gui_callbacks.get('add_user_message_to_display')): gui_callbacks['add_user_message_to_display']("[Silent/Unclear Audio]", source="gui")
            if gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')): gui_callbacks['add_assistant_message_to_display'](err_msg_stt_gui, is_error=False, source="gui") 
            if tts_manager.is_tts_ready():
                err_preset_gui = config.BARK_VOICE_PRESET_EN if lang_for_err_gui == "en" else config.BARK_VOICE_PRESET_RU
                current_persona_name_gui = "Iri-shka";
                with global_states_lock: current_persona_name_gui = assistant_state.get("persona_name", "Iri-shka") 
                tts_manager.start_speaking_response(err_msg_stt_gui, current_persona_name_gui, err_preset_gui, gui_callbacks)
        else: 
            logger.warning(f"Admin GUI Transcription failed: {trans_err}")
            lang_for_err_gui = "en"
            with global_states_lock: lang_for_err_gui = assistant_state.get("last_used_language", "en")
            err_msg_stt_gui = "Sorry, I had trouble understanding that." if lang_for_err_gui == "en" else "Извините, не удалось разобрать речь."
            if gui_callbacks and callable(gui_callbacks.get('add_user_message_to_display')): gui_callbacks['add_user_message_to_display']("[Transcription Error]", source="gui")
            if gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')): gui_callbacks['add_assistant_message_to_display'](err_msg_stt_gui, is_error=True, source="gui")
    finally:
        if not llm_called:
            if gui_callbacks and callable(gui_callbacks.get('act_status_update')):
                gui_callbacks['act_status_update']("ACT: IDLE", "idle")
        
        speak_btn_ready = whisper_handler.is_whisper_ready()
        if gui_callbacks and callable(gui_callbacks.get('speak_button_update')):
             gui_callbacks['speak_button_update'](speak_btn_ready, "Speak" if speak_btn_ready else "HEAR NRDY")


def process_admin_telegram_text_message(user_id, text_message):
    logger.info(f"Processing Admin Telegram text from {user_id}: '{text_message[:70]}...'")
    _handle_admin_llm_interaction(text_message, source="telegram_admin", detected_language_code=None)

def process_admin_telegram_voice_message(user_id, wav_filepath):
    logger.info(f"Processing Admin Telegram voice from {user_id}, WAV: {wav_filepath}")
    llm_called = False
    try:
        if not (whisper_handler.WHISPER_CAPABLE and whisper_handler.is_whisper_ready() and _whisper_module_for_load_audio):
            logger.error("Cannot process admin voice: Whisper not ready or load_audio missing.")
            if telegram_bot_handler_instance and telegram_bot_handler_instance.async_loop:
                 asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance.send_text_message_to_user(user_id, "Error: Voice processing module (Whisper) is not ready."), telegram_bot_handler_instance.async_loop)
            return

        if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update']("Loading Admin voice (TG)...")
        audio_numpy = _whisper_module_for_load_audio.load_audio(wav_filepath)
        if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update']("Transcribing Admin voice (TG)...")
        
        trans_text, trans_err, detected_lang = whisper_handler.transcribe_audio(
            audio_np_array=audio_numpy, language=None, task="transcribe", gui_callbacks=gui_callbacks 
        )
        
        if not trans_err and trans_text:
            llm_called = True
            _handle_admin_llm_interaction(trans_text, source="telegram_voice_admin", detected_language_code=detected_lang)
        elif not trans_text and not trans_err: 
             logger.info(f"Admin TG Voice: No speech detected in {wav_filepath}")
             if telegram_bot_handler_instance and telegram_bot_handler_instance.async_loop:
                asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance.send_text_message_to_user(user_id, "I didn't hear anything in your voice message."), telegram_bot_handler_instance.async_loop)
        else: 
            logger.warning(f"Admin TG Voice Transcription failed: {trans_err}")
            if telegram_bot_handler_instance and telegram_bot_handler_instance.async_loop:
                asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance.send_text_message_to_user(user_id, f"Couldn't transcribe voice: {trans_err or 'Recognition error.'}"), telegram_bot_handler_instance.async_loop)
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
    global gui, app_tk_instance, _active_gpu_monitor, telegram_bot_handler_instance, llm_task_executor
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
    if app_tk_instance and hasattr(app_tk_instance, 'winfo_exists') and app_tk_instance.winfo_exists(): # type: ignore
        app_tk_instance.after(config.CUSTOMER_INTERACTION_CHECK_INTERVAL_SECONDS * 1000, _periodic_customer_interaction_checker) # type: ignore
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
    if app_tk_instance and hasattr(app_tk_instance, 'winfo_exists') and app_tk_instance.winfo_exists(): # type: ignore
        app_tk_instance.after(300, _process_queued_admin_llm_messages) # type: ignore
    else: logger.info("_process_queued_admin_llm_messages: Tk instance not available. Stopping queue check.")

def get_dashboard_data_for_telegram() -> dict:
    logger.debug("Gathering data for HTML dashboard...")
    component_statuses = {}
    def get_gui_status_text_and_type(label_attr_name: str, default_text="N/A", default_type="unknown"):
        if gui and hasattr(gui, label_attr_name):
            label = getattr(gui, label_attr_name)
            if label and hasattr(label, 'cget') and label.winfo_exists(): 
                try:
                    text = label.cget("text")
                    type_ = "unknown" 
                    if any(kw in text for kw in ["ACT: IDLE", "MEM: SAVED", "MEM: LOADED", "TELE: POLL", "RDY", "OK"]) or \
                       (label_attr_name == "act_status_text_label" and text == "ACT: IDLE"): type_ = "idle"
                    elif any(kw in text for kw in ["MEM: FRESH"]): type_ = "fresh"
                    elif any(kw in text for kw in ["ACT: BUSY", "CHK", "LOAD", "PING", "THK"]): type_ = "busy"
                    elif any(kw in text for kw in ["ERR", "TMO", "NRDY", "BAD", "CON", "502", "H", "NO TOK", "NO ADM"]): type_ = "error"
                    elif any(kw in text for kw in ["OFF", "N/A"]): type_ = "off"
                    return text, type_
                except tk.TclError: return default_text, default_type
        return default_text, default_type

    component_statuses["act"] = get_gui_status_text_and_type("act_status_text_label", "ACT: N/A")
    component_statuses["inet"] = get_gui_status_text_and_type("inet_status_text_label", "INET: N/A")
    component_statuses["webui"] = get_gui_status_text_and_type("webui_status_text_label", "WEBUI: N/A", "off") 
    component_statuses["tele"] = ( (f"TELE: {telegram_bot_handler_instance.get_status().upper()}" if telegram_bot_handler_instance else "TELE: N/A"),
                                   (telegram_bot_handler_instance.get_status() if telegram_bot_handler_instance else "unknown") )
    component_statuses["mem"] = get_gui_status_text_and_type("memory_status_text_label", "MEM: N/A")
    component_statuses["hear"] = (f"HEAR: {whisper_handler.get_status_short()}", whisper_handler.get_status_type())
    component_statuses["voice"] = (f"VOICE: {tts_manager.get_status_short()}", tts_manager.get_status_type())
    component_statuses["mind"] = (f"MIND: {'RDY' if ollama_ready else 'NRDY'}", "ready" if ollama_ready else "error")
    component_statuses["vis"] = get_gui_status_text_and_type("vis_status_text_label", "VIS: N/A", "off")
    component_statuses["art"] = get_gui_status_text_and_type("art_status_text_label", "ART: N/A", "off")

    app_overall_status_text = "Status Unavailable"
    if gui and hasattr(gui, 'app_status_label') and gui.app_status_label and gui.app_status_label.winfo_exists():
        try: app_overall_status_text = gui.app_status_label.cget("text")
        except tk.TclError: pass

    with global_states_lock:
        current_admin_user_state = user_state.copy()
        current_assistant_state_snapshot = assistant_state.copy()
        current_admin_chat_history = chat_history[:] 

    return {"admin_user_state": current_admin_user_state, "assistant_state": current_assistant_state_snapshot,
            "admin_chat_history": current_admin_chat_history, "component_statuses": component_statuses,
            "app_overall_status": app_overall_status_text}

def load_all_models_and_services():
    global ollama_ready, assistant_state # Added assistant_state to globals for this function
    logger.info("LOADER_THREAD: --- Starting model and services loading/checking ---")
    def safe_gui_callback(callback_name, *args):
        if gui_callbacks and callable(gui_callbacks.get(callback_name)):
            try: gui_callbacks[callback_name](*args); logger.debug(f"LOADER_THREAD: GUI cb '{callback_name}' called.")
            except Exception as e: logger.error(f"LOADER_THREAD: Error in GUI cb '{callback_name}': {e}", exc_info=False)
        else: logger.debug(f"LOADER_THREAD: GUI cb '{callback_name}' not found/callable.")
    
    safe_gui_callback('status_update', "Initializing components...")
    safe_gui_callback('act_status_update', "ACT: IDLE", "idle") 
    
    # This will set WEBUI: LOAD or WEBUI: OFF based on config
    webui_initial_status_text = "WEBUI: LOAD" if config.ENABLE_WEB_UI else "WEBUI: OFF"
    webui_initial_status_type = "loading" if config.ENABLE_WEB_UI else "off"
    safe_gui_callback('webui_status_update', webui_initial_status_text, webui_initial_status_type)
    logger.info(f"LOADER_THREAD: Initial WebUI status set to: {webui_initial_status_text}")


    for cb_name, text, status in [
        ('inet_status_update', "INET: CHK", "checking"), 
        ('memory_status_update', "MEM: CHK", "checking"),
        ('hearing_status_update', "HEAR: CHK", "loading"), 
        ('voice_status_update', "VOICE: CHK", "loading"),
        ('mind_status_update', "MIND: CHK", "pinging"), 
        ('tele_status_update', "TELE: CHK", "checking"),
        ('vis_status_update', "VIS: OFF", "off"), 
        ('art_status_update', "ART: OFF", "off")]:
        safe_gui_callback(cb_name, text, status)
    
    # No direct change to webui status here; Flask thread handles its own actual status.
    # The initial 'WEBUI: LOAD' or 'WEBUI: OFF' from above is sufficient for the loader.

    logger.info("LOADER_THREAD: Checking internet/search engine...")
    inet_short_text, inet_status_type = check_search_engine_status()
    safe_gui_callback('inet_status_update', inet_short_text, inet_status_type)
    
    with global_states_lock:
        if "admin_name" not in assistant_state: 
            assistant_state["admin_name"] = config.DEFAULT_ASSISTANT_STATE["admin_name"]
    
    with global_states_lock: 
        safe_gui_callback('memory_status_update', "MEM: LOADED" if chat_history else "MEM: FRESH", "loaded" if chat_history else "fresh") 
    
    logger.info("LOADER_THREAD: Checking/loading Whisper...")
    if whisper_handler.WHISPER_CAPABLE:
        whisper_handler.load_whisper_model(config.WHISPER_MODEL_SIZE, gui_callbacks)
    else:
        safe_gui_callback('hearing_status_update', "HEAR: N/A", "na"); safe_gui_callback('speak_button_update', False, "HEAR N/A")
    
    logger.info("LOADER_THREAD: Checking/loading Bark TTS...")
    if tts_manager.TTS_CAPABLE:
        tts_manager.load_bark_resources(gui_callbacks)
    else:
        safe_gui_callback('voice_status_update', "VOICE: N/A", "na")
    
    logger.info("LOADER_THREAD: Checking Ollama server and model...")
    ollama_ready_flag, ollama_log_msg = ollama_handler.check_ollama_server_and_model()
    ollama_ready = ollama_ready_flag 
    if ollama_ready_flag: safe_gui_callback('mind_status_update', "MIND: RDY", "ready")
    else:
        short_code_ollama, status_type_ollama = _parse_ollama_error_to_short_code(ollama_log_msg)
        safe_gui_callback('mind_status_update', f"MIND: {short_code_ollama}", status_type_ollama)

    current_tele_status_for_as = "off" 
    if telegram_bot_handler_instance:
        current_tele_status_for_as = telegram_bot_handler_instance.get_status()
    elif not config.TELEGRAM_BOT_TOKEN: current_tele_status_for_as = "no_token"
    elif not config.TELEGRAM_ADMIN_USER_ID: current_tele_status_for_as = "no_admin"

    with global_states_lock: 
        assistant_state["telegram_bot_status"] = current_tele_status_for_as
        state_manager.save_assistant_state_only(assistant_state.copy(), gui_callbacks) 

    logger.info("LOADER_THREAD: Finalizing GUI status updates.")
    if whisper_handler.is_whisper_ready():
        ready_msg = "Ready.";
        if tts_manager.is_tts_loading(): ready_msg = "Ready (TTS loading...)"
        elif not tts_manager.is_tts_ready() and tts_manager.TTS_CAPABLE: ready_msg = "Ready (TTS NRDY)."
        safe_gui_callback('status_update', ready_msg); safe_gui_callback('speak_button_update', True, "Speak")
    else:
        safe_gui_callback('status_update', "Hearing module not ready."); safe_gui_callback('speak_button_update', False, "HEAR NRDY")
    
    safe_gui_callback('act_status_update', "ACT: IDLE", "idle")
    logger.info("LOADER_THREAD: --- Sequential model and services loading/checking thread finished ---")

if __name__ == "__main__":
    logger.info("--- Main __name__ block started ---")
    # DEBUG: Print the crucial config value at the very start
    logger.info(f"DEBUG MAIN: config.ENABLE_WEB_UI is {config.ENABLE_WEB_UI} (type: {type(config.ENABLE_WEB_UI)})")
    logger.info(f"DEBUG MAIN: config.WEB_UI_PORT is {config.WEB_UI_PORT}")


    folders_to_ensure = [
        config.DATA_FOLDER, config.OUTPUT_FOLDER,
        config.TELEGRAM_VOICE_TEMP_FOLDER, config.TELEGRAM_TTS_TEMP_FOLDER,
        config.CUSTOMER_STATES_FOLDER,
        os.path.join(config.DATA_FOLDER, "temp_dashboards"),
        config.WEB_UI_AUDIO_TEMP_FOLDER, 
        config.WEB_UI_TTS_SERVE_FOLDER    
    ]
    for folder_path in folders_to_ensure:
        if not file_utils.ensure_folder(folder_path, gui_callbacks=None): 
            logger.critical(f"CRITICAL: Failed to create folder '{folder_path}'. Exiting.")
            sys.exit(1)

    logger.info("Loading initial states (admin & assistant)...")
    try:
        loaded_ch, loaded_us, loaded_as = state_manager.load_initial_states(gui_callbacks=None)
        with global_states_lock:
            chat_history = loaded_ch
            user_state = loaded_us
            assistant_state = loaded_as
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
        gui_callbacks['on_recording_finished'] = process_recorded_audio_and_interact
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
                token=config.TELEGRAM_BOT_TOKEN,
                admin_user_id_str=config.TELEGRAM_ADMIN_USER_ID,
                message_queue_for_admin_llm=admin_llm_message_queue,
                customer_interaction_manager=customer_interaction_manager_instance, 
                gui_callbacks=gui_callbacks,
                fn_get_dashboard_data=get_dashboard_data_for_telegram
            )
            if config.START_BOT_ON_APP_START:
                if telegram_bot_handler_instance: telegram_bot_handler_instance.start_polling()
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
            if not (status_key_tele == "TELE: NO TOK" and not config.TELEGRAM_BOT_TOKEN): 
                status_key_tele, status_type_tele = "TELE: NO ADM", "no_admin"
        logger.warning(f"{errmsg_tele} Telegram features will be disabled.")
        if gui_callbacks and callable(gui_callbacks.get('tele_status_update')):
            gui_callbacks['tele_status_update'](status_key_tele, status_type_tele)


    # --- Web UI Setup (Bridge instance created here) ---
    web_bridge = None 
    if config.ENABLE_WEB_UI:
        web_logger.info("Web UI is ENABLED in config. Initializing bridge and Flask thread...")
        web_bridge = WebAppBridge(
            main_app_ollama_ready_flag_getter=lambda: ollama_ready,
            main_app_status_label_getter_fn=lambda: gui.app_status_label.cget("text") if gui and hasattr(gui, 'app_status_label') and gui.app_status_label and gui.app_status_label.winfo_exists() else "N/A"
        )
        actual_flask_app.main_app_components['bridge'] = web_bridge 
        
        if web_bridge and telegram_bot_handler_instance:
            web_bridge.telegram_handler_instance_ref = telegram_bot_handler_instance
            web_logger.info("Telegram handler instance reference set in WebAppBridge.")
        elif web_bridge:
            web_logger.warning("WebAppBridge created, but Telegram handler instance is not (yet) available to set reference.")

        flask_thread = None        
        def run_flask_server_thread_target():
            try:
                web_logger.info(f"Attempting to start Flask web server on http://0.0.0.0:{config.WEB_UI_PORT}")
                actual_flask_app.run(host='0.0.0.0', port=config.WEB_UI_PORT, debug=False, use_reloader=False)
                web_logger.info("Flask server has stopped.") # Should only happen on clean shutdown or if run returns
            except Exception as e_flask_run:
                web_logger.critical(f"Flask server CRASHED or failed to start: {e_flask_run}", exc_info=True)
                if gui_callbacks and callable(gui_callbacks.get('webui_status_update')):
                    gui_callbacks['webui_status_update']("WEBUI: ERR", "error")
        
        flask_thread = threading.Thread(target=run_flask_server_thread_target, daemon=True, name="FlaskWebUIServerThread")
        flask_thread.start()
        web_logger.info("Flask server thread started (object created and .start() called).")
        # GUI status for webui will be updated by the loader_thread or by Flask thread exception handler

    else: 
        web_logger.info("Web UI is DISABLED in config. Flask thread will not be started.")
        if gui_callbacks and callable(gui_callbacks.get('webui_status_update')):
             gui_callbacks['webui_status_update']("WEBUI: OFF", "off")
    # --- End Web UI Setup ---


    if gpu_monitor.PYNVML_AVAILABLE:
        _active_gpu_monitor = gpu_monitor.get_gpu_monitor_instance(gui_callbacks=gui_callbacks, update_interval=2)
        if _active_gpu_monitor and _active_gpu_monitor.active: _active_gpu_monitor.start()
        elif _active_gpu_monitor and not _active_gpu_monitor.active : logger.warning("GPUMonitor initialized but not active.")
        elif not _active_gpu_monitor and gpu_monitor.PYNVML_AVAILABLE :
             if gui_callbacks and callable(gui_callbacks.get('gpu_status_update_display')):
                gui_callbacks['gpu_status_update_display']("InitFail", "InitFail", "InitFail")
    elif gui_callbacks and callable(gui_callbacks.get('gpu_status_update_display')):
        gui_callbacks['gpu_status_update_display']("N/A", "N/A", "na_nvml")


    logger.info("Starting model and services loader thread...")
    loader_thread = threading.Thread(target=load_all_models_and_services, daemon=True, name="ServicesLoaderThread")
    loader_thread.start()

    if app_tk_instance and hasattr(app_tk_instance, 'winfo_exists') and app_tk_instance.winfo_exists(): # type: ignore
        app_tk_instance.after(300, _process_queued_admin_llm_messages) # type: ignore
        app_tk_instance.after(config.CUSTOMER_INTERACTION_CHECK_INTERVAL_SECONDS * 1000, _periodic_customer_interaction_checker) # type: ignore
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
    finally: logger.info("Mainloop exited. Ensuring graceful shutdown via on_app_exit()."); on_app_exit()
    logger.info("Application main thread has finished.")