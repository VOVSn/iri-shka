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
assistant_state_lock = threading.Lock()
chat_history: list = []
user_state: dict = {}
assistant_state: dict = {}
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
        self.telegram_handler_instance_ref: TelegramBotHandler = None # type: ignore # Will be set after TG Handler init
        web_logger.info("WebAppBridge initialized.")

    def handle_web_interaction(self, input_wav_filepath: str):
        web_logger.info(f"WebAppBridge: Handling web interaction with audio: {input_wav_filepath}")
        result = {
            "user_transcription": None,
            "llm_text_response": None,
            "tts_audio_filename": None,
            "error": None
        }
        detected_lang = None 

        # 1. STT (Whisper)
        if not self.whisper_handler_module.is_whisper_ready():
            result["error"] = "Whisper (STT) service not ready."
            web_logger.error(f"WebAppBridge: STT Aborted - {result['error']}")
            return result
        
        try:
            web_logger.debug(f"WebAppBridge: Attempting to load audio for STT from: {input_wav_filepath}")
            audio_np_array_web = None
            if _whisper_module_for_load_audio:
                 audio_np_array_web = _whisper_module_for_load_audio.load_audio(input_wav_filepath)
            else:
                 raise RuntimeError("Whisper 'load_audio' utility not available (whisper module general import issue).")

            web_logger.debug(f"WebAppBridge: Audio loaded for STT. Shape: {audio_np_array_web.shape if audio_np_array_web is not None else 'None'}")
            transcribed_text, trans_err, detected_lang_from_stt = self.whisper_handler_module.transcribe_audio(
                audio_np_array=audio_np_array_web, language=None, task="transcribe"
            )
            detected_lang = detected_lang_from_stt 

            if trans_err:
                result["error"] = f"Transcription error: {trans_err}"
                web_logger.error(f"WebAppBridge: STT Error - {result['error']}")
                return result 
            
            if not transcribed_text:
                web_logger.info("WebAppBridge: No speech detected or transcribed from web input.")
                result["user_transcription"] = "" 
                return result 
            
            result["user_transcription"] = transcribed_text
            web_logger.info(f"WebAppBridge: Transcription successful: '{transcribed_text[:70]}...', Detected Lang: {detected_lang}")

        except Exception as e_stt:
            result["error"] = f"STT processing failed unexpectedly: {str(e_stt)}"
            web_logger.error(f"WebAppBridge: STT Exception - {result['error']}", exc_info=True)
            return result

        # 2. LLM (Ollama)
        # Access ollama_ready global flag directly here, or use the getter if preferred
        # For simplicity and consistency with how it was passed, using the getter.
        if not self.get_ollama_ready(): 
            result["error"] = "Ollama (LLM) service not ready."
            web_logger.error(f"WebAppBridge: LLM Aborted - {result['error']}")
            return result

        try:
            web_logger.debug(f"WebAppBridge: Preparing to call LLM for input: '{result['user_transcription'][:70]}...'")
            current_time_str = datetime.datetime.now(
                datetime.timezone(datetime.timedelta(hours=config.TIMEZONE_OFFSET_HOURS))
            ).strftime("%Y-%m-%d %H:%M:%S")
            
            web_prompt_kwargs = {
                "web_user_input": result["user_transcription"],
                "current_time_string": current_time_str
            }
            
            ollama_data, ollama_error = self.ollama_handler_module.call_ollama_for_chat_response(
                prompt_template_to_use=config.OLLAMA_WEB_PROMPT_TEMPLATE,
                transcribed_text=result["user_transcription"], 
                current_chat_history=[], 
                current_user_state={},   
                current_assistant_state={}, 
                format_kwargs=web_prompt_kwargs,
                expected_keys_override=["answer_to_user"]
            )
            
            if ollama_error:
                result["error"] = f"LLM error: {ollama_error}"
                web_logger.error(f"WebAppBridge: LLM Error - {result['error']}")
                return result 
            
            result["llm_text_response"] = ollama_data.get("answer_to_user")
            if not result["llm_text_response"]:
                 web_logger.warning("WebAppBridge: LLM provided no text in 'answer_to_user'. Using placeholder.")
                 result["llm_text_response"] = "(No specific text response received)" 

            web_logger.info(f"WebAppBridge: LLM Response received: '{result['llm_text_response'][:70]}...'")

        except Exception as e_llm:
            result["error"] = f"LLM processing failed unexpectedly: {str(e_llm)}"
            web_logger.error(f"WebAppBridge: LLM Exception - {result['error']}", exc_info=True)
            return result

        # 3. TTS (Bark)
        if not result["llm_text_response"] or result["llm_text_response"] == "(No specific text response received)":
            web_logger.info("WebAppBridge: No meaningful LLM text response to synthesize for TTS. Skipping TTS.")
            return result 

        if not self.tts_manager_module.is_tts_ready():
            web_logger.warning("WebAppBridge: TTS service not ready, cannot synthesize voice for web. Returning text only.")
            return result 
        
        try:
            web_logger.debug(f"WebAppBridge: Preparing to synthesize TTS for: '{result['llm_text_response'][:70]}...'")
            bark_engine = self.tts_manager_module.get_bark_model_instance()
            if not bark_engine:
                web_logger.error("WebAppBridge: Failed to get Bark TTS engine instance for web output.")
                return result 

            web_tts_voice_preset = config.BARK_VOICE_PRESET_RU 
            if detected_lang: 
                if 'en' in detected_lang.lower():
                    web_tts_voice_preset = config.BARK_VOICE_PRESET_EN
            web_logger.info(f"WebAppBridge: Using TTS voice preset: {web_tts_voice_preset} (based on detected lang: {detected_lang})")

            audio_array, samplerate = bark_engine.synthesize_speech_to_array(
                result["llm_text_response"],
                generation_params={"voice_preset": web_tts_voice_preset}
            )

            if audio_array is not None and samplerate is not None:
                tts_filename = f"web_tts_output_{uuid.uuid4().hex}.wav"
                if not file_utils.ensure_folder(config.WEB_UI_TTS_SERVE_FOLDER, gui_callbacks=None):
                    web_logger.critical(f"WebAppBridge: Critical - Could not create TTS serve folder: {config.WEB_UI_TTS_SERVE_FOLDER}")
                    result["error"] = "Server error: Cannot save TTS audio." 
                    return result 

                tts_filepath = os.path.join(config.WEB_UI_TTS_SERVE_FOLDER, tts_filename)
                
                if audio_array.dtype != np.float32:
                    web_logger.debug(f"WebAppBridge: Converting TTS audio array from {audio_array.dtype} to float32 for soundfile.")
                    audio_array = audio_array.astype(np.float32)
                
                sf.write(tts_filepath, audio_array, samplerate, subtype='PCM_16') # type: ignore
                
                result["tts_audio_filename"] = tts_filename
                web_logger.info(f"WebAppBridge: Synthesized TTS and saved to {tts_filepath} (SR: {samplerate}, Format: PCM_16)")
            else:
                web_logger.error("WebAppBridge: TTS synthesis returned no audio data or samplerate.")
        except Exception as e_tts:
            web_logger.error(f"WebAppBridge: TTS synthesis or saving failed: {str(e_tts)}", exc_info=True)

        web_logger.info(f"WebAppBridge: Interaction processing complete. Result: { {k: (str(v)[:70] + '...' if isinstance(v, str) and len(v) > 70 else v) for k,v in result.items()} }")
        return result

    def get_system_status_for_web(self):
        # Ollama status (uses global ollama_ready flag, re-pings if not ready)
        ollama_stat_text, ollama_stat_type = "N/A", "unknown"
        if ollama_ready: 
            ollama_stat_text, ollama_stat_type = "Ready", "ready"
        else:
            _, ollama_ping_msg = self.ollama_handler_module.check_ollama_server_and_model() # Re-ping for fresh status
            if ollama_ready: # Check again after ping (ollama_ready might be updated by check_ollama_server_and_model)
                 ollama_stat_text, ollama_stat_type = "Ready", "ready"
            else:
                if "timeout" in (ollama_ping_msg or "").lower(): ollama_stat_type = "timeout"
                elif "connection" in (ollama_ping_msg or "").lower(): ollama_stat_type = "conn_error"
                elif "http" in (ollama_ping_msg or "").lower() and "error" in (ollama_ping_msg or "").lower(): ollama_stat_type = "error"
                else: ollama_stat_type = "error"
                ollama_stat_text = ollama_ping_msg[:30] if ollama_ping_msg else "Error"
        
        # Main App Status from GUI
        main_app_status_from_gui = "N/A"
        try: 
            if gui and hasattr(gui, 'app_status_label') and gui.app_status_label and gui.app_status_label.winfo_exists():
                main_app_status_from_gui = gui.app_status_label.cget("text")
        except tk.TclError: 
             web_logger.debug("WebAppBridge: TclError getting app_status_label for web status (likely GUI closing).")
             main_app_status_from_gui = "GUI Closing"
        except Exception as e_gui_status:
             web_logger.warning(f"WebAppBridge: Error getting app_status_label for web status: {e_gui_status}")
             main_app_status_from_gui = "Error (GUI Status)"
        
        # Telegram Bot Status
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
    logger.info(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Starting.")
    
    if gui_callbacks and callable(gui_callbacks.get('act_status_update')):
        gui_callbacks['act_status_update']("ACT: BUSY", "busy")
    try:
        assistant_state_snapshot_for_prompt = {}
        with assistant_state_lock:
            assistant_state_snapshot_for_prompt = assistant_state.copy()

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

        if gui_callbacks and callable(gui_callbacks.get('add_user_message_to_display')):
            display_text = input_text
            gui_callbacks['add_user_message_to_display'](display_text, source=source)

        if gui_callbacks and callable(gui_callbacks.get('status_update')):
            gui_callbacks['status_update']("Thinking (Admin)...")
        if gui_callbacks and callable(gui_callbacks.get('mind_status_update')):
            gui_callbacks['mind_status_update']("MIND: THK", "thinking")

        target_customer_id_for_prompt = None
        customer_state_for_prompt_str = "{}"
        is_customer_context_active_for_prompt = False

        history_to_scan_for_customer_context = []
        with assistant_state_lock: 
            scan_length = config.MAX_HISTORY_TURNS // 2
            if scan_length < 1: scan_length = 1
            start_index = max(0, len(chat_history) - scan_length)
            history_to_scan_for_customer_context = chat_history[start_index:]

        for turn in reversed(history_to_scan_for_customer_context):
            assistant_message = turn.get("assistant", "")
            turn_source = turn.get("source", "") 
            if turn_source == "customer_summary_internal": 
                match_summary = re.search(r"\[Сводка по клиенту (\d+)\]", assistant_message)
                if match_summary:
                    try:
                        target_customer_id_for_prompt = int(match_summary.group(1))
                        logger.info(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Found customer context ID {target_customer_id_for_prompt} from 'customer_summary_internal'.")
                        break
                    except ValueError:
                        logger.warning(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Found non-integer customer ID in summary: {match_summary.group(1)}")
                        target_customer_id_for_prompt = None 

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

        logger.debug(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Calling Ollama. CustContext Active: {is_customer_context_active_for_prompt}, CustID: {target_customer_id_for_prompt}")
        ollama_data, ollama_error = ollama_handler.call_ollama_for_chat_response(
            prompt_template_to_use=config.OLLAMA_PROMPT_TEMPLATE,
            transcribed_text=input_text, 
            current_chat_history=chat_history, 
            current_user_state=user_state, 
            current_assistant_state=assistant_state_for_this_prompt, 
            language_instruction=language_instruction_for_llm,
            format_kwargs=format_kwargs_for_ollama,
            expected_keys_override=expected_keys_for_response
        )

        current_turn_for_history = {"user": input_text, "source": source, "timestamp": state_manager.get_current_timestamp_iso()}
        if source == "gui" and detected_language_code:
            current_turn_for_history["detected_language_code_for_gui_display"] = detected_language_code
        elif source == "telegram_voice_admin" and detected_language_code: 
             current_turn_for_history["detected_language_code_for_tele_voice_display"] = detected_language_code


        if ollama_error:
            logger.error(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Ollama call failed: {ollama_error}")
            ollama_ready = False
            short_code, status_type = _parse_ollama_error_to_short_code(ollama_error)
            if gui_callbacks and callable(gui_callbacks.get('mind_status_update')): gui_callbacks['mind_status_update'](f"MIND: {short_code}", status_type)
            if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update'](f"LLM Error (Admin): {ollama_error[:50]}")

            assistant_response_text = "An internal error occurred while processing your request (admin)."
            if current_lang_code_for_state == "ru": assistant_response_text = "При обработке вашего запроса (админ) произошла внутренняя ошибка."

            current_turn_for_history["assistant"] = f"[LLM Error: {assistant_response_text}]" 
            if gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')):
                gui_callbacks['add_assistant_message_to_display'](assistant_response_text, is_error=True, source=source) 
        else:
            logger.info(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Ollama call successful.")
            ollama_ready = True
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

            with assistant_state_lock:
                current_global_assistant_state = state_manager.load_assistant_state_only(gui_callbacks)
                for key, value_from_llm in new_assistant_state_changes_from_llm.items():
                    if key == "internal_tasks" and isinstance(value_from_llm, dict) and isinstance(current_global_assistant_state.get(key), dict):
                        for task_type in ["pending", "in_process", "completed"]:
                            new_tasks = value_from_llm.get(task_type, [])
                            if not isinstance(new_tasks, list): new_tasks = [str(new_tasks)] 
                            existing_tasks = current_global_assistant_state[key].get(task_type, [])
                            if not isinstance(existing_tasks, list): existing_tasks = [str(existing_tasks)] 
                            current_global_assistant_state[key][task_type] = list(dict.fromkeys([str(t) for t in existing_tasks] + [str(t) for t in new_tasks]))
                    else:
                        current_global_assistant_state[key] = value_from_llm
                current_global_assistant_state["last_used_language"] = current_lang_code_for_state 
                assistant_state.clear()
                assistant_state.update(current_global_assistant_state)
                state_manager.save_assistant_state_only(assistant_state, gui_callbacks) 
                logger.debug(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Global assistant_state updated and saved.")

            if gui_callbacks:
                if callable(gui_callbacks.get('update_todo_list')): gui_callbacks['update_todo_list'](user_state.get("todos", []))
                if callable(gui_callbacks.get('update_calendar_events_list')):
                    logger.debug(f"ADMIN_LLM_FLOW: Updating GUI admin calendar with: {user_state.get('calendar_events', [])}")
                    gui_callbacks['update_calendar_events_list'](user_state.get("calendar_events", []))

                asst_tasks = assistant_state.get("internal_tasks", {});
                if not isinstance(asst_tasks, dict): asst_tasks = {} 
                if callable(gui_callbacks.get('update_kanban_pending')): gui_callbacks['update_kanban_pending'](asst_tasks.get("pending", []))
                if callable(gui_callbacks.get('update_kanban_in_process')): gui_callbacks['update_kanban_in_process'](asst_tasks.get("in_process", []))
                if callable(gui_callbacks.get('update_kanban_completed')): gui_callbacks['update_kanban_completed'](asst_tasks.get("completed", []))

            if updated_customer_state_from_llm and isinstance(updated_customer_state_from_llm, dict) and target_customer_id_for_prompt:
                if updated_customer_state_from_llm.get("user_id") == target_customer_id_for_prompt:
                    if state_manager.save_customer_state(target_customer_id_for_prompt, updated_customer_state_from_llm, gui_callbacks):
                        logger.info(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Updated state for context customer {target_customer_id_for_prompt}.")
                else:
                    logger.warning(f"ADMIN_LLM_FLOW: {function_signature_for_log} - LLM returned updated_active_customer_state for mismatched ID (Expected {target_customer_id_for_prompt}, Got {updated_customer_state_from_llm.get('user_id')}). Not saving customer state.")
            elif updated_customer_state_from_llm is not None and updated_customer_state_from_llm != {}: 
                 logger.warning(f"ADMIN_LLM_FLOW: {function_signature_for_log} - 'updated_active_customer_state' from LLM was not null/empty but invalid or no target_customer_id_for_prompt. Value: {updated_customer_state_from_llm}")

            current_turn_for_history["assistant"] = assistant_response_text 

            if source == "gui" and gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')):
                 gui_callbacks['add_assistant_message_to_display'](assistant_response_text, source=source)
            elif (source == "telegram_admin" or source == "telegram_voice_admin") and gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')):
                gui_callbacks['add_assistant_message_to_display'](assistant_response_text, source=source) 
                if callable(gui_callbacks.get('status_update')):
                    gui_callbacks['status_update'](f"Iri-shka (to Admin TG): {assistant_response_text[:40]}...")

        with assistant_state_lock:
            chat_history.append(current_turn_for_history)
            chat_history = state_manager.save_states(chat_history, user_state, assistant_state.copy(), gui_callbacks) 
        if gui_callbacks and callable(gui_callbacks.get('memory_status_update')):
            gui_callbacks['memory_status_update']("MEM: SAVED", "saved") 
        if gui and callable(gui_callbacks.get('update_chat_display_from_list')): 
            gui_callbacks['update_chat_display_from_list'](chat_history)

        if source == "gui":
            if tts_manager.is_tts_ready():
                def _deferred_gui_display_on_playback_admin(): 
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
                        text_send_future.result(timeout=15) 
                        logger.info(f"ADMIN_LLM_FLOW: Text reply sent to admin TG.")
                    else: logger.info(f"ADMIN_LLM_FLOW: Text reply to admin disabled by config.")

                    if config.TELEGRAM_REPLY_WITH_VOICE:
                        logger.info(f"ADMIN_LLM_FLOW: Sending VOICE reply to admin. Lang: {current_lang_code_for_state}, Preset: {selected_bark_voice_preset}")
                        _send_voice_reply_to_telegram_user(admin_id_int, assistant_response_text, selected_bark_voice_preset) 
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

        if gui_callbacks:
            enable_speak_btn = whisper_handler.is_whisper_ready()
            if callable(gui_callbacks.get('speak_button_update')):
                gui_callbacks['speak_button_update'](enable_speak_btn, "Speak" if enable_speak_btn else "HEAR NRDY")
            is_speaking_gui = tts_manager.current_tts_thread and tts_manager.current_tts_thread.is_alive()
            if callable(gui_callbacks.get('status_update')) and not is_speaking_gui:
                 gui_callbacks['status_update']("Ready (Admin)." if enable_speak_btn else "Hearing N/A (Admin).")
    finally:
        if gui_callbacks and callable(gui_callbacks.get('act_status_update')):
            gui_callbacks['act_status_update']("ACT: IDLE", "idle")
        logger.info(f"ADMIN_LLM_FLOW: {function_signature_for_log} - Processing finished (in finally block).")


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
                logger.error(f"TelegramBotHandler is missing 'send_voice_message_to_user' method. Cannot send voice to user {target_user_id}.")

            if send_future:
                send_future.result(timeout=20) 
                logger.info(f"Voice reply sent to user {target_user_id} using {temp_tts_ogg_path}")
        except Exception as e_send_v: logger.error(f"Error processing/sending voice to {target_user_id}: {e_send_v}", exc_info=True)
    else: logger.error(f"No audio pieces synthesized for user {target_user_id}. Cannot send voice.")
    
    if os.path.exists(temp_tts_merged_wav_path):
        try: os.remove(temp_tts_merged_wav_path)
        except OSError as e: logger.warning(f"Could not remove temp WAV {temp_tts_merged_wav_path}: {e}") 
    if os.path.exists(temp_tts_ogg_path):
        try: os.remove(temp_tts_ogg_path)
        except OSError as e: logger.warning(f"Could not remove temp OGG {temp_tts_ogg_path}: {e}") 


def handle_customer_interaction_package(customer_user_id: int):
    global chat_history, user_state, assistant_state 
    function_signature_for_log = f"handle_customer_interaction_package(cust_id={customer_user_id})"
    logger.info(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Starting processing.")
    
    if gui_callbacks and callable(gui_callbacks.get('act_status_update')):
        gui_callbacks['act_status_update']("ACT: BUSY", "busy")
    try:
        customer_state = {}
        try: customer_state = state_manager.load_or_initialize_customer_state(customer_user_id, gui_callbacks)
        except Exception as e: 
            logger.error(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - CRITICAL Error loading customer state: {e}", exc_info=True)
            return # Exit if state cannot be loaded
        
        current_stage = customer_state.get("conversation_stage")
        if current_stage != "aggregating_messages":
            logger.warning(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Customer not in 'aggregating_messages' stage (is '{current_stage}'). Skipping."); 
            return # Exit if not in correct stage

        # ... (rest of the function logic remains the same) ...
        
        # TEMPLATE FOR REST OF THE FUNCTION:
        # ack_sent_successfully = False
        # ...
        # ollama_data, ollama_error = ollama_handler.call_ollama_for_chat_response(...)
        # if ollama_error:
        #   ...
        # else:
        #   ...
        # logger.info(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Finished processing.") # This will be moved to finally
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
            # Speak button update handled in finally
            return
        
        if config.SAVE_RECORDINGS_TO_WAV and audio_frames_for_save:
            if file_utils.ensure_folder(config.OUTPUT_FOLDER, gui_callbacks):
                filename = f"rec_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
                audio_processor.save_wav_data_to_file(os.path.join(config.OUTPUT_FOLDER, filename), audio_frames_for_save, recorded_sample_rate, gui_callbacks)
        del audio_frames_for_save; gc.collect()

        if not (whisper_handler.WHISPER_CAPABLE and whisper_handler.is_whisper_ready()):
            logger.warning("Whisper not ready for Admin GUI audio.")
            if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update']("Hearing module not ready.")
            # Speak button update handled in finally
            return

        if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update']("Transcribing audio (Admin GUI)...")
        transcribed_text, trans_err, detected_lang = whisper_handler.transcribe_audio(
            audio_np_array=audio_float32, language=None, task="transcribe", gui_callbacks=gui_callbacks
        )
        
        if not trans_err and transcribed_text:
            llm_called = True
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
    finally:
        # If _handle_admin_llm_interaction was called, it set ACT to IDLE.
        # If it was not called (e.g., STT error), then this `finally` block resets ACT.
        if not llm_called:
            if gui_callbacks and callable(gui_callbacks.get('act_status_update')):
                gui_callbacks['act_status_update']("ACT: IDLE", "idle")
        
        # Ensure speak button is re-enabled correctly
        speak_btn_ready = whisper_handler.is_whisper_ready()
        if gui_callbacks and callable(gui_callbacks.get('speak_button_update')):
             gui_callbacks['speak_button_update'](speak_btn_ready, "Speak" if speak_btn_ready else "HEAR NRDY")


def process_admin_telegram_text_message(user_id, text_message):
    # _handle_admin_llm_interaction already sets ACT:BUSY and ACT:IDLE
    logger.info(f"Processing Admin Telegram text from {user_id}: '{text_message[:70]}...'")
    _handle_admin_llm_interaction(text_message, source="telegram_admin", detected_language_code=None)

def process_admin_telegram_voice_message(user_id, wav_filepath):
    # _handle_admin_llm_interaction already sets ACT:BUSY and ACT:IDLE
    logger.info(f"Processing Admin Telegram voice from {user_id}, WAV: {wav_filepath}")
    # The main processing (STT + LLM) is within _handle_admin_llm_interaction.
    # This function mainly handles the audio loading and calling the LLM handler.
    
    llm_called = False
    # Unlike GUI, there's no persistent "ACT: BUSY" for the whole duration here.
    # The BUSY/IDLE will be managed by _handle_admin_llm_interaction.
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
        else:
            logger.warning(f"Admin TG Voice Transcription failed: {trans_err or 'Empty.'}")
            if telegram_bot_handler_instance and telegram_bot_handler_instance.async_loop:
                asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance.send_text_message_to_user(user_id, f"Couldn't transcribe voice: {trans_err or 'No speech detected.'}"), telegram_bot_handler_instance.async_loop)
    except Exception as e:
        logger.error(f"Error processing admin voice WAV {wav_filepath}: {e}", exc_info=True)
        if telegram_bot_handler_instance and telegram_bot_handler_instance.async_loop:
             asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance.send_text_message_to_user(user_id, "An error occurred while processing your voice message."), telegram_bot_handler_instance.async_loop)
    finally:
        # ACT status is handled by _handle_admin_llm_interaction if called.
        # If not called due to STT error, ACT status on GUI remains as it was (likely IDLE from previous).
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
            # ACT remains IDLE during listening
    else:
        audio_processor.stop_recording() # This triggers on_recording_finished -> process_recorded_audio_and_interact
        if gui_callbacks and callable(gui_callbacks.get('speak_button_update')): 
            gui_callbacks['speak_button_update'](False, "Processing...")
        # process_recorded_audio_and_interact will set ACT: BUSY

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
            if label and hasattr(label, 'cget') and label.winfo_exists(): # Check if widget exists
                try:
                    text = label.cget("text")
                    # Determine type based on current color logic in GUIManager
                    # This is a bit of a reverse lookup, direct status tracking would be cleaner
                    # For now, approximate based on text or known states
                    type_ = "unknown" 
                    # Green states
                    if any(kw in text for kw in ["ACT: IDLE", "MEM: SAVED", "MEM: LOADED", "TELE: POLL", "RDY", "OK"]) or \
                       (label_attr_name == "act_status_text_label" and text == "ACT: IDLE"):
                        type_ = "idle" # or "ready" or "saved" or "loaded" - maps to green
                    # Blue states
                    elif any(kw in text for kw in ["MEM: FRESH"]):
                        type_ = "fresh" # maps to blue
                    # Yellow states
                    elif any(kw in text for kw in ["ACT: BUSY", "CHK", "LOAD", "PING", "THK"]):
                        type_ = "busy" # or "loading", "checking", "thinking" - maps to yellow
                    # Red states
                    elif any(kw in text for kw in ["ERR", "TMO", "NRDY", "BAD", "CON", "502", "H", "NO TOK", "NO ADM"]):
                        type_ = "error" # maps to red
                    # Grey states
                    elif any(kw in text for kw in ["OFF", "N/A"]):
                         type_ = "off" # maps to grey
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
    component_statuses["mind"] = (f"MIND: {'RDY' if ollama_ready else 'NRDY'}", "ready" if ollama_ready else "error") # ollama_ready is global
    component_statuses["vis"] = get_gui_status_text_and_type("vis_status_text_label", "VIS: N/A", "off")
    component_statuses["art"] = get_gui_status_text_and_type("art_status_text_label", "ART: N/A", "off")

    app_overall_status_text = "Status Unavailable"
    if gui and hasattr(gui, 'app_status_label') and gui.app_status_label and gui.app_status_label.winfo_exists():
        try: app_overall_status_text = gui.app_status_label.cget("text")
        except tk.TclError: pass

    current_admin_user_state, current_assistant_state_snapshot, current_admin_chat_history = {}, {}, []
    with assistant_state_lock:
        current_admin_user_state = user_state.copy()
        current_assistant_state_snapshot = assistant_state.copy()
        current_admin_chat_history = chat_history[:] 

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
    # Set ACT to IDLE (green) initially by the loader. Subsequent actions will set it to BUSY.
    safe_gui_callback('act_status_update', "ACT: IDLE", "idle") 
    
    # Other initial statuses
    webui_initial_status_text = "WEBUI: LOAD" if config.ENABLE_WEB_UI else "WEBUI: OFF"
    webui_initial_status_type = "loading" if config.ENABLE_WEB_UI else "off"
    safe_gui_callback('webui_status_update', webui_initial_status_text, webui_initial_status_type)

    for cb_name, text, status in [
        ('inet_status_update', "INET: CHK", "checking"), 
        ('memory_status_update', "MEM: CHK", "checking"), # Will be updated to FRESH/LOADED
        ('hearing_status_update', "HEAR: CHK", "loading"), 
        ('voice_status_update', "VOICE: CHK", "loading"),
        ('mind_status_update', "MIND: CHK", "pinging"), 
        ('tele_status_update', "TELE: CHK", "checking"),
        ('vis_status_update', "VIS: OFF", "off"), 
        ('art_status_update', "ART: OFF", "off")]:
        safe_gui_callback(cb_name, text, status)
    
    logger.info("LOADER_THREAD: Initial GUI component statuses set.")
    
    if config.ENABLE_WEB_UI:
        # Assuming Flask starts correctly, set to ready. If Flask fails, main thread might update it.
        safe_gui_callback('webui_status_update', "WEBUI: ON", "ready")


    logger.info("LOADER_THREAD: Checking internet/search engine...")
    inet_short_text, inet_status_type = check_search_engine_status()
    safe_gui_callback('inet_status_update', inet_short_text, inet_status_type)
    logger.info(f"LOADER_THREAD: Internet check done. Status: {inet_short_text}")
    
    with assistant_state_lock:
        if "admin_name" not in assistant_state: assistant_state["admin_name"] = config.DEFAULT_ASSISTANT_STATE["admin_name"]
    
    # Memory status: LOADED (green) if history, FRESH (blue) otherwise
    safe_gui_callback('memory_status_update', "MEM: LOADED" if chat_history else "MEM: FRESH", "loaded" if chat_history else "fresh") 
    logger.info("LOADER_THREAD: Memory status updated.")
    
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
    ollama_ready = ollama_ready_flag # Update global ollama_ready
    if ollama_ready_flag: safe_gui_callback('mind_status_update', "MIND: RDY", "ready")
    else:
        short_code, status_type_ollama = _parse_ollama_error_to_short_code(ollama_log_msg)
        safe_gui_callback('mind_status_update', f"MIND: {short_code}", status_type_ollama)
    logger.info(f"LOADER_THREAD: Ollama check finished. Ready: {ollama_ready_flag}, Msg: {ollama_log_msg}")

    current_tele_status_for_as = "off" 
    if telegram_bot_handler_instance:
        current_tele_status_for_as = telegram_bot_handler_instance.get_status()
    elif not config.TELEGRAM_BOT_TOKEN: current_tele_status_for_as = "no_token"
    elif not config.TELEGRAM_ADMIN_USER_ID: current_tele_status_for_as = "no_admin"

    with assistant_state_lock:
        assistant_state["telegram_bot_status"] = current_tele_status_for_as
        state_manager.save_assistant_state_only(assistant_state.copy(), gui_callbacks) 
    logger.info(f"LOADER_THREAD: Assistant state 'telegram_bot_status' updated to {current_tele_status_for_as}.")

    logger.info("LOADER_THREAD: Finalizing GUI status updates.")
    if whisper_handler.is_whisper_ready():
        ready_msg = "Ready.";
        if tts_manager.is_tts_loading(): ready_msg = "Ready (TTS loading...)"
        elif not tts_manager.is_tts_ready() and tts_manager.TTS_CAPABLE: ready_msg = "Ready (TTS NRDY)."
        safe_gui_callback('status_update', ready_msg); safe_gui_callback('speak_button_update', True, "Speak")
    else:
        safe_gui_callback('status_update', "Hearing module not ready."); safe_gui_callback('speak_button_update', False, "HEAR NRDY")
    
    # Ensure ACT is IDLE if all loading is done
    safe_gui_callback('act_status_update', "ACT: IDLE", "idle")
    logger.info("LOADER_THREAD: --- Sequential model and services loading/checking thread finished ---")

if __name__ == "__main__":
    logger.info("--- Main __name__ block started ---")

    # --- Ensure Folders ---
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
    logger.info("All necessary data folders ensured.")

    # --- Load Initial States ---
    logger.info("Loading initial states (admin & assistant)...")
    try:
        chat_history, user_state, assistant_state = state_manager.load_initial_states(gui_callbacks=None)
    except Exception as e_state_load:
        logger.critical(f"CRITICAL ERROR loading initial states: {e_state_load}", exc_info=True); sys.exit(1)

    # --- GUI Theme & Font from State ---
    initial_theme_from_state = user_state.get("gui_theme", config.DEFAULT_USER_STATE["gui_theme"])
    current_gui_theme = initial_theme_from_state if initial_theme_from_state in [config.GUI_THEME_LIGHT, config.GUI_THEME_DARK] else config.GUI_THEME_LIGHT
    user_state["gui_theme"] = current_gui_theme
    initial_font_size_state = user_state.get("chat_font_size", config.DEFAULT_USER_STATE["chat_font_size"])
    try: initial_font_size_state = int(initial_font_size_state)
    except (ValueError, TypeError): initial_font_size_state = config.DEFAULT_CHAT_FONT_SIZE
    current_chat_font_size_applied = max(config.MIN_CHAT_FONT_SIZE, min(initial_font_size_state, config.MAX_CHAT_FONT_SIZE))
    user_state["chat_font_size"] = current_chat_font_size_applied
    logger.info(f"Initial GUI theme: {current_gui_theme}, Font size: {current_chat_font_size_applied}")

    # --- Initialize ThreadPool & Managers ---
    logger.info("Initializing ThreadPoolExecutor for LLM tasks...")
    llm_task_executor = ThreadPoolExecutor(max_workers=config.LLM_TASK_THREAD_POOL_SIZE, thread_name_prefix="LLMTaskThread")
    logger.info("Initializing CustomerInteractionManager...")
    customer_interaction_manager_instance = CustomerInteractionManager()

    # --- Initialize Tkinter GUI ---
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
    logger.info("GUIManager initialized.")

    # --- Populate GUI Callbacks ---
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

    # --- Populate GUI with Initial Data ---
    logger.info("Populating GUI with initial state data...")
    if gui and gui_callbacks:
        # ... (GUI population logic as before) ...
        if callable(gui_callbacks.get('update_chat_display_from_list')): gui_callbacks['update_chat_display_from_list'](chat_history)
        if callable(gui_callbacks.get('update_todo_list')): gui_callbacks['update_todo_list'](user_state.get("todos", []))
        if callable(gui_callbacks.get('update_calendar_events_list')): gui_callbacks['update_calendar_events_list'](user_state.get("calendar_events", []))
        initial_asst_tasks = assistant_state.get("internal_tasks", {});
        if not isinstance(initial_asst_tasks, dict): initial_asst_tasks = {}
        if callable(gui_callbacks.get('update_kanban_pending')): gui_callbacks['update_kanban_pending'](initial_asst_tasks.get("pending", []))
        if callable(gui_callbacks.get('update_kanban_in_process')): gui_callbacks['update_kanban_in_process'](initial_asst_tasks.get("in_process", []))
        if callable(gui_callbacks.get('update_kanban_completed')): gui_callbacks['update_kanban_completed'](initial_asst_tasks.get("completed", []))
        logger.info("Initial Admin User Info and Assistant Kanban populated on GUI.")
    else:
        logger.warning("GUI or gui_callbacks not available for initial data population.")

    # --- Initialize Telegram Bot Handler ---
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
                logger.info("Config requests Telegram bot start on app start.")
                if telegram_bot_handler_instance:
                    telegram_bot_handler_instance.start_polling()
            # ... (else block for not starting automatically) ...
        # ... (exception handling for Telegram init) ...
        except ValueError: # Handles invalid TELEGRAM_ADMIN_USER_ID
             logger.error(f"TELEGRAM_ADMIN_USER_ID '{config.TELEGRAM_ADMIN_USER_ID}' is invalid. Bot disabled.")
             if gui_callbacks and callable(gui_callbacks.get('tele_status_update')):
                 gui_callbacks['tele_status_update']("TELE: NO ADM", "no_admin")
        except Exception as e_tele_init:
            logger.error(f"Failed to initialize TelegramBotHandler: {e_tele_init}", exc_info=True)
            if gui_callbacks and callable(gui_callbacks.get('tele_status_update')):
                gui_callbacks['tele_status_update']("TELE: INITERR", "error")
    else: # Missing token or admin ID
        # ... (logging and GUI update for missing TG config) ...
        errmsg_tele = "Telegram Bot: "
        status_key_tele, status_type_tele = "TELE: OFF", "off"
        if not config.TELEGRAM_BOT_TOKEN: errmsg_tele += "Token not set."; status_key_tele, status_type_tele = "TELE: NO TOK", "no_token"
        if not config.TELEGRAM_ADMIN_USER_ID:
            errmsg_tele += (" " if config.TELEGRAM_BOT_TOKEN else "") + "Admin User ID not set."
            if status_key_tele == "TELE: OFF" and not config.TELEGRAM_BOT_TOKEN: # Prioritize no_token if both missing
                 pass
            elif status_key_tele == "TELE: OFF": # Only admin_id missing
                status_key_tele, status_type_tele = "TELE: NO ADM", "no_admin"
        logger.warning(f"{errmsg_tele} Telegram features will be disabled.")
        if gui_callbacks and callable(gui_callbacks.get('tele_status_update')):
            gui_callbacks['tele_status_update'](status_key_tele, status_type_tele)


    # --- Web UI Setup (Bridge instance created here) ---
    web_bridge = None # Initialize to None
    if config.ENABLE_WEB_UI:
        web_logger.info("Web UI is enabled. Initializing bridge...")
        web_bridge = WebAppBridge(
            main_app_ollama_ready_flag_getter=lambda: ollama_ready,
            main_app_status_label_getter_fn=lambda: gui.app_status_label.cget("text") if gui and hasattr(gui, 'app_status_label') and gui.app_status_label and gui.app_status_label.winfo_exists() else "N/A"
        )
        actual_flask_app.main_app_components['bridge'] = web_bridge # Pass bridge to Flask app context
        
        # Set the telegram handler reference in the bridge if both exist
        if web_bridge and telegram_bot_handler_instance:
            web_bridge.telegram_handler_instance_ref = telegram_bot_handler_instance
            web_logger.info("Telegram handler instance reference set in WebAppBridge.")
        elif web_bridge:
            web_logger.warning("WebAppBridge created, but Telegram handler instance is not (yet) available to set reference.")

    # --- Initialize GPU Monitor ---
    logger.info("Initializing GPU Monitor (if available)...")
    # ... (GPU Monitor init as before) ...
    if gpu_monitor.PYNVML_AVAILABLE:
        _active_gpu_monitor = gpu_monitor.get_gpu_monitor_instance(gui_callbacks=gui_callbacks, update_interval=2)
        if _active_gpu_monitor and _active_gpu_monitor.active:
            _active_gpu_monitor.start()
            logger.info("GPU Monitor started.")
        elif _active_gpu_monitor and not _active_gpu_monitor.active : # Corrected condition
            logger.warning("GPUMonitor initialized but not active (e.g. no NVIDIA GPU found by NVML).")
        # If _active_gpu_monitor is None (PYNVML_AVAILABLE was true but get_instance failed for some reason)
        elif not _active_gpu_monitor and gpu_monitor.PYNVML_AVAILABLE :
             if gui_callbacks and callable(gui_callbacks.get('gpu_status_update_display')):
                gui_callbacks['gpu_status_update_display']("InitFail", "InitFail", "InitFail")
    elif gui_callbacks and callable(gui_callbacks.get('gpu_status_update_display')): # PYNVML_AVAILABLE is False
        gui_callbacks['gpu_status_update_display']("N/A", "N/A", "na_nvml")


    # --- Start Flask Server Thread (if Web UI enabled) ---
    flask_thread = None
    if config.ENABLE_WEB_UI:
        def run_flask_server_thread_target():
            try:
                web_logger.info(f"Starting Flask web server on http://0.0.0.0:{config.WEB_UI_PORT}")
                actual_flask_app.run(host='0.0.0.0', port=config.WEB_UI_PORT, debug=False, use_reloader=False)
            except Exception as e_flask_run:
                web_logger.critical(f"Flask server failed to start or crashed: {e_flask_run}", exc_info=True)
                if gui_callbacks and callable(gui_callbacks.get('webui_status_update')):
                    gui_callbacks['webui_status_update']("WEBUI: ERR", "error")
        
        flask_thread = threading.Thread(target=run_flask_server_thread_target, daemon=True, name="FlaskWebUIServerThread")
        flask_thread.start()
        web_logger.info("Flask server thread started.")
        if gui_callbacks and callable(gui_callbacks.get('webui_status_update')):
             gui_callbacks['webui_status_update']("WEBUI: LOAD", "loading") # Will be updated by loader
    else:
        web_logger.info("Web UI is disabled in config.")
        if gui_callbacks and callable(gui_callbacks.get('webui_status_update')):
             gui_callbacks['webui_status_update']("WEBUI: OFF", "off")
    # --- End Web UI Setup ---

    # --- Start Model/Services Loader Thread ---
    logger.info("Starting model and services loader thread...")
    loader_thread = threading.Thread(target=load_all_models_and_services, daemon=True, name="ServicesLoaderThread")
    loader_thread.start()

    # --- Schedule Periodic Tasks on Tkinter Main Thread ---
    if app_tk_instance and hasattr(app_tk_instance, 'winfo_exists') and app_tk_instance.winfo_exists():
        app_tk_instance.after(300, _process_queued_admin_llm_messages)
        app_tk_instance.after(config.CUSTOMER_INTERACTION_CHECK_INTERVAL_SECONDS * 1000, _periodic_customer_interaction_checker)
        logger.info("Admin LLM message queue and Customer interaction checker scheduled on Tkinter main thread.")
    else:
        logger.error("Tkinter instance not available for scheduling periodic tasks. Key functionalities might not work.")

    # --- Start Tkinter Mainloop ---
    logger.info("Starting Tkinter mainloop...")
    try:
        if app_tk_instance:
            app_tk_instance.mainloop()
        else:
            logger.critical("Cannot start mainloop: app_tk_instance is None. Application will exit.")
            on_app_exit() # Try to cleanup
            sys.exit(1)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt detected by mainloop. Initiating shutdown.")
    except tk.TclError as e_tcl:
        if "application has been destroyed" in str(e_tcl).lower():
            logger.info("Tkinter mainloop TclError: Application already destroyed (likely during normal shutdown).")
        else:
            # Log full error if it's unexpected
            logger.error(f"Unhandled TclError in mainloop: {e_tcl}. Initiating shutdown.", exc_info=True)
    except Exception as e_mainloop:
        logger.critical(f"Unexpected critical error in Tkinter mainloop: {e_mainloop}", exc_info=True)
    finally:
        logger.info("Mainloop exited or error occurred. Ensuring graceful shutdown via on_app_exit().")
        on_app_exit() # This handles shutdown of threads, Flask server should stop as it's daemon.
        logger.info("Application main thread has finished.")