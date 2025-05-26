# main.py
import tkinter as tk
import threading
import sys
import gc
import os
import re
import logging
import requests 

try:
    import logger as app_logger_module
    logger = app_logger_module.get_logger("Iri-shka_App.Main")
except ImportError as e:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger("Iri-shka_App.Main_Fallback")
    logger.critical(f"Failed to import custom logger module: {e}. Using basicConfig.", exc_info=True)
except Exception as e:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger("Iri-shka_App.Main_Fallback")
    logger.critical(f"CRITICAL ERROR during custom logger initialization: {e}. Using basicConfig.", exc_info=True)

logger.info("Application starting...")
logger.info(f"Python version: {sys.version}")
logger.info(f"OS: {sys.platform}")

import config
from utils import file_utils, state_manager, whisper_handler, ollama_handler, audio_processor, tts_manager
from utils import gpu_monitor
from gui_manager import GUIManager

try:
    import numpy as np
    logger.info(f"NumPy version {np.__version__} imported successfully.")
except ImportError:
    logger.critical("CRITICAL: NumPy Not Found. Please install NumPy: pip install numpy. Exiting.")
    sys.exit(1)

gui = None
app_tk_instance = None
_active_gpu_monitor = None

chat_history = []
user_state = {}
assistant_state = {}
ollama_ready = False
current_gui_theme = config.GUI_THEME_LIGHT
current_chat_font_size_applied = config.DEFAULT_CHAT_FONT_SIZE

gui_callbacks = {} # Defined early for state_manager

# ... (_parse_ollama_error_to_short_code unchanged)
# ... (process_recorded_audio_and_interact unchanged)
# ... (toggle_speaking_recording unchanged)
def _parse_ollama_error_to_short_code(error_message_from_handler):
    if not error_message_from_handler: return "NRDY", "error"
    lower_msg = error_message_from_handler.lower()
    if "timeout" in lower_msg: return "TMO", "timeout"
    if "connection" in lower_msg or "connect" in lower_msg : return "CON", "conn_error"
    if "502" in lower_msg: return "502", "http_502"
    http_match = re.search(r"http.*?(\d{3})", lower_msg)
    if http_match:
        code = http_match.group(1)
        return f"H{code}", "http_other"
    if "json" in lower_msg and "invalid" in lower_msg : return "JSON", "error"
    if "empty content" in lower_msg : return "EMP", "error"
    if "missing keys" in lower_msg : return "KEYS", "error"
    return "NRDY", "error"


def process_recorded_audio_and_interact(recorded_sample_rate):
    global chat_history, user_state, assistant_state, ollama_ready, current_gui_theme, current_chat_font_size_applied
    logger.info(f"Processing recorded audio. Sample rate: {recorded_sample_rate} Hz.")
    if not np:
        logger.error("NumPy missing during audio processing. This should not happen.")
        gui_callbacks['status_update']("NumPy missing. Please install.")
        enable_speak = whisper_handler.whisper_model_ready
        gui_callbacks['speak_button_update'](enable_speak, "Speak" if enable_speak else "HEAR NRDY")
        return

    audio_float32, audio_frames_for_save = audio_processor.convert_frames_to_numpy(
        recorded_sample_rate, gui_callbacks
    )

    if audio_float32 is None:
        logger.warning("Audio processing (convert_frames_to_numpy) returned None. Cannot proceed.")
        enable_speak = whisper_handler.whisper_model_ready
        gui_callbacks['speak_button_update'](enable_speak, "Speak" if enable_speak else "HEAR NRDY")
        if gui_callbacks and 'status_update' in gui_callbacks:
            gui_callbacks['status_update']("Audio processing failed.")
        return

    if config.SAVE_RECORDINGS_TO_WAV and audio_frames_for_save:
        logger.info("SAVE_RECORDINGS_TO_WAV is True. Attempting to save WAV.")
        if file_utils.ensure_folder(config.OUTPUT_FOLDER, gui_callbacks):
            import datetime
            filename = f"rec_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
            filepath = os.path.join(config.OUTPUT_FOLDER, filename)
            audio_processor.save_wav_data_to_file(filepath, audio_frames_for_save, recorded_sample_rate, gui_callbacks)
    del audio_frames_for_save; gc.collect()
    logger.debug("Audio frames for WAV save (if any) processed and deleted.")


    if not (whisper_handler.WHISPER_CAPABLE and whisper_handler.whisper_model_ready): # Use WHISPER_CAPABLE
        logger.warning("Whisper module not ready or capable. Cannot transcribe.")
        gui_callbacks['status_update']("Hearing module not ready.")
        gui_callbacks['speak_button_update'](False, "HEAR NRDY")
        return

    gui_callbacks['status_update']("Transcribing audio...")
    transcribed_text, trans_err, detected_language_code = whisper_handler.transcribe_audio(
        audio_float32, language=None, gui_callbacks=gui_callbacks
    )

    user_display_text = ""
    assistant_response_text = "Error: Processing failed."
    playback_callback_for_tts = None
    selected_bark_voice_preset = config.BARK_VOICE_PRESET_EN
    last_interaction_lang = assistant_state.get("last_used_language", "en")
    assistant_error_response_text = "I didn't catch that, could you please repeat?"
    
    current_lang_code_for_state = last_interaction_lang
    language_instruction_for_llm = config.LANGUAGE_INSTRUCTION_NON_RUSSIAN

    if last_interaction_lang == "ru":
        selected_bark_voice_preset_for_error = config.BARK_VOICE_PRESET_RU
        assistant_error_response_text = "Я не расслышала, не могли бы вы повторить?"
        language_instruction_for_llm = config.LANGUAGE_INSTRUCTION_RUSSIAN
    else:
        selected_bark_voice_preset_for_error = config.BARK_VOICE_PRESET_EN

    if trans_err or not transcribed_text:
        logger.warning(f"Transcription failed or empty. Error: {trans_err}, Text: '{transcribed_text}', Lang: {detected_language_code}")
        gui_callbacks['status_update'](f"Transcription: {trans_err or 'Empty.'} (Lang: {detected_language_code or 'N/A'})")
        user_display_text = "[Silent or Unclear Audio]"
        assistant_response_text = assistant_error_response_text
        gui_callbacks['add_user_message_to_display'](user_display_text)
        gui_callbacks['add_assistant_message_to_display'](assistant_response_text, is_error=True)
        current_turn_for_history = {"user": user_display_text, "assistant": assistant_response_text}
        selected_bark_voice_preset = selected_bark_voice_preset_for_error

    else:
        logger.info(f"Transcription successful: '{transcribed_text[:70]}...' (Lang: {detected_language_code})")
        user_display_text_with_lang = f"{transcribed_text} ({detected_language_code or 'unknown language'})"
        gui_callbacks['add_user_message_to_display'](user_display_text_with_lang)
        gui_callbacks['status_update']("Thinking...")
        gui_callbacks['mind_status_update']("MIND: THK", "thinking")

        if detected_language_code == "ru":
            logger.info("Detected language is Russian. Using Russian preset and instruction.")
            selected_bark_voice_preset = config.BARK_VOICE_PRESET_RU
            language_instruction_for_llm = config.LANGUAGE_INSTRUCTION_RUSSIAN
            current_lang_code_for_state = "ru"
        else:
            logger.info(f"Detected language is {detected_language_code} (or not Russian). Using English preset and instruction.")
            selected_bark_voice_preset = config.BARK_VOICE_PRESET_EN
            language_instruction_for_llm = config.LANGUAGE_INSTRUCTION_NON_RUSSIAN
            current_lang_code_for_state = "en"

        assistant_state["last_used_language"] = current_lang_code_for_state
        logger.debug(f"Assistant state 'last_used_language' updated to: {current_lang_code_for_state}")

        ollama_data, ollama_error = ollama_handler.call_ollama_for_chat_response(
            transcribed_text, chat_history, user_state, assistant_state,
            language_instruction_for_llm, gui_callbacks
        )
        current_turn_for_history = {"user": user_display_text_with_lang}

        if ollama_error:
            logger.error(f"Ollama call failed: {ollama_error}")
            ollama_ready = False
            short_code, status_type = _parse_ollama_error_to_short_code(ollama_error)
            gui_callbacks['mind_status_update'](f"MIND: {short_code}", status_type)
            gui_callbacks['status_update'](f"LLM Error: {ollama_error[:60]}")

            if current_lang_code_for_state == "ru":
                if "502" in ollama_error: assistant_response_text = "Кажется, у моего мыслительного процесса временные неполадки..."
                elif "timeout" in ollama_error.lower() or "connect" in ollama_error.lower(): assistant_response_text = "У меня проблемы с доступом к моей базе знаний..."
                else: assistant_response_text = "При обработке вашего запроса произошла ошибка."
            else:
                if "502" in ollama_error: assistant_response_text = "My thinking process seems to be having a temporary issue..."
                elif "timeout" in ollama_error.lower() or "connect" in ollama_error.lower(): assistant_response_text = "I'm having trouble reaching my core knowledge base..."
                else: assistant_response_text = "I encountered an issue while processing your request."
            
            selected_bark_voice_preset = config.BARK_VOICE_PRESET_RU if current_lang_code_for_state == "ru" else config.BARK_VOICE_PRESET_EN
            logger.info(f"Generated fallback error message for user: '{assistant_response_text}'")
            current_turn_for_history["assistant"] = f"[LLM Error: {assistant_response_text}]"
            gui_callbacks['add_assistant_message_to_display'](current_turn_for_history["assistant"], is_error=True)

        else: 
            logger.info("Ollama call successful.")
            ollama_ready = True
            gui_callbacks['mind_status_update']("MIND: RDY", "ready")
            assistant_response_text = ollama_data["answer_to_user"]

            new_user_state_from_llm = ollama_data["updated_user_state"]
            new_theme_from_llm = new_user_state_from_llm.get("gui_theme", current_gui_theme)

            if new_theme_from_llm != current_gui_theme:
                if new_theme_from_llm in [config.GUI_THEME_LIGHT, config.GUI_THEME_DARK]:
                    logger.info(f"Theme change requested. From '{current_gui_theme}' to '{new_theme_from_llm}'.")
                    if gui and 'apply_application_theme' in gui_callbacks:
                        gui_callbacks['apply_application_theme'](new_theme_from_llm)
                        current_gui_theme = new_theme_from_llm
                        logger.info(f"GUI theme changed to {current_gui_theme}.")
                        gui_callbacks['update_chat_display_from_list'](chat_history) 
                    else:
                        logger.warning("Cannot change theme: GUI or callback not found.")
                else:
                    logger.warning(f"LLM suggested invalid theme: '{new_theme_from_llm}'. Ignoring.")
                    new_user_state_from_llm["gui_theme"] = current_gui_theme 
            
            new_font_size_from_llm = new_user_state_from_llm.get("chat_font_size", current_chat_font_size_applied)
            try:
                new_font_size_from_llm = int(new_font_size_from_llm)
            except (ValueError, TypeError):
                logger.warning(f"LLM provided non-integer font size: '{new_font_size_from_llm}'. Using current: {current_chat_font_size_applied}")
                new_font_size_from_llm = current_chat_font_size_applied
                new_user_state_from_llm["chat_font_size"] = current_chat_font_size_applied 

            if new_font_size_from_llm != current_chat_font_size_applied:
                if not (config.MIN_CHAT_FONT_SIZE <= new_font_size_from_llm <= config.MAX_CHAT_FONT_SIZE):
                    logger.warning(f"LLM font size {new_font_size_from_llm} out of range ({config.MIN_CHAT_FONT_SIZE}-{config.MAX_CHAT_FONT_SIZE}). Clamping.")
                    new_font_size_from_llm = max(config.MIN_CHAT_FONT_SIZE, min(new_font_size_from_llm, config.MAX_CHAT_FONT_SIZE))
                    new_user_state_from_llm["chat_font_size"] = new_font_size_from_llm 
                
                logger.info(f"Font size change requested. From '{current_chat_font_size_applied}' to '{new_font_size_from_llm}'.")
                if gui and 'apply_chat_font_size' in gui_callbacks:
                    gui_callbacks['apply_chat_font_size'](new_font_size_from_llm)
                    current_chat_font_size_applied = new_font_size_from_llm
                    logger.info(f"Chat font size changed to {current_chat_font_size_applied}.")
                else:
                    logger.warning("Cannot change font size: GUI or callback not found.")
            
            user_state = new_user_state_from_llm
            assistant_state = ollama_data["updated_assistant_state"] 
            assistant_state["last_used_language"] = current_lang_code_for_state 

            if user_state.get("gui_theme") != current_gui_theme:
                user_state["gui_theme"] = current_gui_theme
            if user_state.get("chat_font_size") != current_chat_font_size_applied:
                user_state["chat_font_size"] = current_chat_font_size_applied

            logger.debug(f"User state updated from LLM: {str(user_state)[:200]}...")
            logger.debug(f"Assistant state updated from LLM: {str(assistant_state)[:200]}...")
            logger.info(f"LLM Response: '{assistant_response_text[:70]}...'")

            if 'update_todo_list' in gui_callbacks:
                gui_callbacks['update_todo_list'](user_state.get("todos", []))
            if 'update_calendar_events_list' in gui_callbacks:
                gui_callbacks['update_calendar_events_list'](user_state.get("calendar_events", []))

            assistant_internal_tasks = assistant_state.get("internal_tasks", {})
            if not isinstance(assistant_internal_tasks, dict): 
                logger.warning(f"LLM returned 'internal_tasks' not as a dict: {assistant_internal_tasks}. Using empty for Kanban.")
                assistant_internal_tasks = {} 
                
            if 'update_kanban_pending' in gui_callbacks:
                gui_callbacks['update_kanban_pending'](assistant_internal_tasks.get("pending", []))
            if 'update_kanban_in_process' in gui_callbacks:
                gui_callbacks['update_kanban_in_process'](assistant_internal_tasks.get("in_process", []))
            if 'update_kanban_completed' in gui_callbacks:
                gui_callbacks['update_kanban_completed'](assistant_internal_tasks.get("completed", []))

            current_turn_for_history["assistant"] = assistant_response_text
            if tts_manager.is_tts_ready():
                logger.debug("TTS is ready. Setting up deferred display for assistant message.")
                captured_response_text = assistant_response_text 
                def _deferred_display_on_playback():
                    logger.debug("Playback started, displaying assistant message via callback.")
                    gui_callbacks['add_assistant_message_to_display'](captured_response_text)
                    gui_callbacks['status_update'](f"Speaking: {captured_response_text[:50]}...")
                playback_callback_for_tts = _deferred_display_on_playback
            else:
                logger.info("TTS not ready. Displaying assistant message immediately.")
                gui_callbacks['add_assistant_message_to_display'](assistant_response_text)
                gui_callbacks['status_update'](f"Iri-shka: {assistant_response_text[:50]}...")


    chat_history.append(current_turn_for_history)
    chat_history = state_manager.save_states(chat_history, user_state, assistant_state, gui_callbacks)
    gui_callbacks['memory_status_update']("MEM: SAVED", "saved") 

    if tts_manager.is_tts_ready():
        tts_manager.start_speaking_response(
            assistant_response_text,
            assistant_state.get("persona_name", "Iri-shka"),
            selected_bark_voice_preset,
            gui_callbacks,
            on_actual_playback_start_gui_callback=playback_callback_for_tts
        )
    elif playback_callback_for_tts is None: 
        logger.info("TTS was not used or failed, message already displayed. Setting final status.")
        if not tts_manager.TTS_CAPABLE: gui_callbacks['status_update']("TTS unavailable. Response shown.")
        elif not tts_manager.is_tts_ready(): gui_callbacks['status_update']("TTS not ready. Response shown.")

    enable_speak_btn = whisper_handler.whisper_model_ready
    gui_callbacks['speak_button_update'](enable_speak_btn, "Speak" if enable_speak_btn else "HEAR NRDY")

    is_speaking = tts_manager.current_tts_thread and tts_manager.current_tts_thread.is_alive()
    if enable_speak_btn and not is_speaking:
        logger.info("Interaction complete. Ready to listen.")
        gui_callbacks['status_update']("Ready to listen.")
    elif not enable_speak_btn:
        logger.warning("Interaction complete, but Hearing module not ready.")
        gui_callbacks['status_update']("Hearing module not ready.")
    elif is_speaking:
        logger.info("Interaction logic complete, but TTS is still speaking.") 
    logger.info("--- End of interaction processing ---")


def toggle_speaking_recording():
    logger.info(f"Toggle speaking/recording requested. Currently recording: {audio_processor.is_recording_active()}")
    if not np:
        logger.error("NumPy missing on toggle_speaking_recording. Should have been caught at startup.")
        gui_callbacks['messagebox_error']("NumPy Missing", "NumPy is required to process audio.")
        return

    if not audio_processor.is_recording_active():
        logger.info("Attempting to start recording.")
        if not (whisper_handler.WHISPER_CAPABLE and whisper_handler.whisper_model_ready): # Use WHISPER_CAPABLE
            logger.warning("Cannot start recording: Whisper module not ready or capable.")
            gui_callbacks['messagebox_warn']("Hearing Not Ready", "The hearing module (Whisper) is not ready.")
            return
        if tts_manager.TTS_CAPABLE and tts_manager.is_tts_loading(): # Use TTS_CAPABLE
            logger.info("Cannot start recording: TTS resources are still loading.")
            gui_callbacks['messagebox_info']("Please Wait", "TTS resources are still loading.")
            return
        if tts_manager.TTS_CAPABLE: 
            logger.debug("Stopping any current TTS speech before recording.")
            tts_manager.stop_current_speech(gui_callbacks) 

        if audio_processor.start_recording(gui_callbacks): 
            logger.info("Recording started successfully via audio_processor.")
            gui_callbacks['speak_button_update'](True, "Listening...")
        else:
            logger.warning("audio_processor.start_recording failed.")
    else:
        logger.info("Attempting to stop recording.")
        audio_processor.stop_recording() 
        gui_callbacks['speak_button_update'](False, "Processing...")

# --- Model Control Actions (for tray menu) ---
def _unload_bark_model_action():
    logger.info("Unloading Bark TTS model from tray request...")
    if 'voice_status_update' in gui_callbacks:
        gui_callbacks['voice_status_update']("VOICE: OFF", "off") # Immediate visual feedback
    if 'status_update' in gui_callbacks:
        gui_callbacks['status_update']("Unloading Bark TTS...")
    
    # Run in a thread to avoid blocking tray menu for long operations (though unload should be fast)
    def _task():
        tts_manager.unload_bark_model(gui_callbacks) # This will update status at end
    threading.Thread(target=_task, daemon=True, name="UnloadBarkThread").start()

def _reload_bark_model_action():
    logger.info("Reloading Bark TTS model from tray request...")
    if 'voice_status_update' in gui_callbacks:
        gui_callbacks['voice_status_update']("VOICE: CHK", "loading")
    if 'status_update' in gui_callbacks:
        gui_callbacks['status_update']("Reloading Bark TTS...")
    
    def _task():
        tts_manager.load_bark_resources(gui_callbacks) # This will update status at end
    threading.Thread(target=_task, daemon=True, name="ReloadBarkThread").start()

def _unload_whisper_model_action():
    logger.info("Unloading Whisper STT model from tray request...")
    if 'hearing_status_update' in gui_callbacks:
        gui_callbacks['hearing_status_update']("HEAR: OFF", "off")
    if 'speak_button_update' in gui_callbacks:
        gui_callbacks['speak_button_update'](False, "HEAR OFF")
    if 'status_update' in gui_callbacks:
        gui_callbacks['status_update']("Unloading Whisper STT...")

    def _task():
        whisper_handler.unload_whisper_model(gui_callbacks)
    threading.Thread(target=_task, daemon=True, name="UnloadWhisperThread").start()

def _reload_whisper_model_action():
    logger.info("Reloading Whisper STT model from tray request...")
    if 'hearing_status_update' in gui_callbacks:
        gui_callbacks['hearing_status_update']("HEAR: CHK", "loading")
    if 'speak_button_update' in gui_callbacks:
        gui_callbacks['speak_button_update'](False, "Loading...")
    if 'status_update' in gui_callbacks:
        gui_callbacks['status_update']("Reloading Whisper STT...")
        
    def _task():
        whisper_handler.load_whisper_model(config.WHISPER_MODEL_SIZE, gui_callbacks)
    threading.Thread(target=_task, daemon=True, name="ReloadWhisperThread").start()
# --- End Model Control Actions ---


def on_app_exit():
    global gui, app_tk_instance, _active_gpu_monitor
    logger.info("Application closing sequence initiated...")

    if _active_gpu_monitor:
        logger.info("Shutting down GPU monitor...")
        _active_gpu_monitor.stop()
        _active_gpu_monitor.cleanup_nvml() # This might be redundant if instance cleanup does it
        _active_gpu_monitor = None
        logger.info("GPU monitor shutdown complete from main.")

    logger.info("Shutting down audio resources...")
    audio_processor.shutdown_audio_resources()
    logger.info("Audio resources shutdown complete from main.")

    if tts_manager.TTS_CAPABLE: # Use TTS_CAPABLE
        logger.info("Stopping current TTS speech and shutting down TTS module...")
        tts_manager.stop_current_speech(gui_callbacks) # Ensure callbacks are passed
        tts_manager.full_shutdown_tts_module() # Use the full shutdown
        logger.info("TTS module shutdown complete from main.")

    if whisper_handler.WHISPER_CAPABLE: # Use WHISPER_CAPABLE
        logger.info("Cleaning up Whisper model...")
        whisper_handler.full_shutdown_whisper_module() # Use the full shutdown
        logger.info("Whisper model cleanup complete from main.")

    if gui:
        logger.info("Destroying GUI window (which also stops tray icon)...")
        gui.destroy_window() # This will handle tray_icon.stop()
        gui = None
        logger.info("GUI window and tray destroyed from main.")

    # app_tk_instance = None # This is tricky. If mainloop exited, it's fine. If called from tray before mainloop ends, it might be needed.
    # The app_tk_instance.quit() or destroy() should be what actually stops the mainloop.
    # If using pystray, the main Tkinter loop might continue if not explicitly quit.
    # However, gui.destroy_window() should call app_window.destroy() which usually ends it.

    logger.info("Application exit sequence fully complete.")
    logging.shutdown()
    # sys.exit(0) # Explicit exit, sometimes useful if threads are lingering. Test carefully.

# ... (check_search_engine_status unchanged)
def check_search_engine_status():
    url = config.SEARCH_ENGINE_URL.rstrip('/') + '/search'
    params = {'q': 'ping', 'format': 'json'}
    short_text_prefix = "INET: "
    
    logger.info(f"Pinging Search Engine at {url} with params {params}")
    try:
        headers = {'User-Agent': 'Iri-shka_AI_Assistant/1.0'}
        response = requests.get(url, params=params, headers=headers, timeout=config.SEARCH_ENGINE_PING_TIMEOUT)
        response.raise_for_status() 
        try:
            response.json() 
            logger.info(f"Search Engine ping successful (Status {response.status_code}), response is valid JSON.")
        except requests.exceptions.JSONDecodeError:
            logger.warning(f"Search Engine ping successful (Status {response.status_code}), but response was not valid JSON. Content: {response.text[:100]}...")
        return f"{short_text_prefix}RDY", "ready"
    except requests.exceptions.Timeout:
        logger.error(f"Search Engine ping timeout ({config.SEARCH_ENGINE_PING_TIMEOUT}s).")
        return f"{short_text_prefix}TMO", "timeout"
    except requests.exceptions.ConnectionError as ce:
        logger.error(f"Search Engine connection error: {ce}")
        return f"{short_text_prefix}CON", "conn_error"
    except requests.exceptions.HTTPError as e:
        logger.error(f"Search Engine HTTP error: {e.response.status_code}.")
        return f"{short_text_prefix}H{e.response.status_code}", "http_other"
    except Exception as e:
        logger.error(f"Search Engine unexpected error: {e}", exc_info=True)
        return f"{short_text_prefix}ERR", "error"

def load_all_models_sequentially():
    global ollama_ready, chat_history, assistant_state 
    logger.info("--- Starting sequential model loading/checking thread ---")

    gui_callbacks['status_update']("Initializing components...")
    gui_callbacks['speak_button_update'](False, "Loading...")

    gui_callbacks['act_status_update']("ACT: IDLE", "idle") 
    gui_callbacks['webui_status_update']("WEBUI: OFF", "off") 
    gui_callbacks['tele_status_update']("TELE: OFF", "off") 

    logger.info("Checking INET (Search Engine) status...")
    gui_callbacks['inet_status_update']("INET: CHK", "checking")
    inet_short_text, inet_status_type = check_search_engine_status()
    gui_callbacks['inet_status_update'](inet_short_text, inet_status_type)


    if not chat_history: 
        gui_callbacks['memory_status_update']("MEM: FRESH", "fresh") 
    else: 
        gui_callbacks['memory_status_update']("MEM: LOADED", "ready") 

    if "last_used_language" not in assistant_state:
        assistant_state["last_used_language"] = config.DEFAULT_ASSISTANT_STATE.get("last_used_language", "en")
        logger.info(f"Initialized 'last_used_language' in assistant_state to default: {assistant_state['last_used_language']}")

    logger.info("Loading Hearing Module (Whisper)...")
    gui_callbacks['hearing_status_update']("HEAR: CHK", "loading") # Changed from "checking" to "loading" for consistency
    if whisper_handler.WHISPER_CAPABLE: # Use WHISPER_CAPABLE
        whisper_handler.load_whisper_model(config.WHISPER_MODEL_SIZE, gui_callbacks)
        # load_whisper_model now handles its own final status update (RDY or NRDY)
    else:
        logger.warning("Whisper not capable, skipping load.")
        gui_callbacks['hearing_status_update']("HEAR: N/A", "na")

    logger.info("Loading Voice Module (TTS - Bark)...")
    gui_callbacks['voice_status_update']("VOICE: CHK", "loading") # Changed from "checking" to "loading"
    if tts_manager.TTS_CAPABLE: # Use TTS_CAPABLE
        tts_manager.load_bark_resources(gui_callbacks)
        # load_bark_resources now handles its own final status update (RDY or NRDY)
    else:
        logger.warning("TTS (Bark) not capable, skipping load.")
        gui_callbacks['voice_status_update']("VOICE: N/A", "na")

    logger.info("Checking Mind Module (Ollama server and model)...")
    gui_callbacks['mind_status_update']("MIND: CHK", "pinging")
    ollama_ready_flag, ollama_log_msg = ollama_handler.check_ollama_server_and_model()
    ollama_ready = ollama_ready_flag 
    if ollama_ready_flag:
        logger.info(f"Ollama server and model '{config.OLLAMA_MODEL_NAME}' ready. Status: {ollama_log_msg}")
        gui_callbacks['mind_status_update']("MIND: RDY", "ready")
    else:
        logger.warning(f"Ollama server or model '{config.OLLAMA_MODEL_NAME}' not ready. Status: {ollama_log_msg}")
        short_code, status_type = _parse_ollama_error_to_short_code(ollama_log_msg)
        gui_callbacks['mind_status_update'](f"MIND: {short_code}", status_type)

    # Final status update based on Whisper, as it's key for "Speak" button
    if whisper_handler.whisper_model_ready:
        logger.info("Initial models loaded/checked. Whisper ready, enabling Speak button.")
        # speak_button_update is handled by load_whisper_model
        if not (tts_manager.is_tts_ready() or tts_manager.is_tts_loading()): # if tts is not ready and not loading
             gui_callbacks['status_update']("Ready to listen (TTS check complete).")
        else:
             gui_callbacks['status_update']("Ready to listen.") # TTS will update status if it's still loading
    else:
        logger.warning("Whisper model not ready after loading sequence. Speak button should be disabled.")
        # speak_button_update is handled by load_whisper_model
        gui_callbacks['status_update']("Hearing module not ready.")
    logger.info("--- Sequential model loading/checking thread finished ---")


if __name__ == "__main__":
    logger.info("Application __main__ block started.")

    if not file_utils.ensure_folder(config.DATA_FOLDER):
        logger.critical(f"Failed to ensure {config.DATA_FOLDER} exists. Exiting.")
        sys.exit(1)
    if not file_utils.ensure_folder(config.OUTPUT_FOLDER): 
        logger.critical(f"Failed to ensure {config.OUTPUT_FOLDER} exists. Exiting.")
        sys.exit(1)

    logger.info("Loading initial states before GUI initialization...")
    chat_history, user_state, assistant_state = state_manager.load_initial_states(gui_callbacks=None) # gui_callbacks not ready yet
    logger.info("Initial states loaded.")

    initial_theme_from_state = user_state.get("gui_theme", config.DEFAULT_USER_STATE["gui_theme"])
    if initial_theme_from_state not in [config.GUI_THEME_LIGHT, config.GUI_THEME_DARK]:
        logger.warning(f"Invalid theme '{initial_theme_from_state}' in user_state.json. Defaulting to '{config.GUI_THEME_LIGHT}'.")
        initial_theme_from_state = config.GUI_THEME_LIGHT
        user_state["gui_theme"] = initial_theme_from_state 
    current_gui_theme = initial_theme_from_state
    logger.info(f"Initial GUI theme set to: {current_gui_theme}")

    initial_font_size_state = user_state.get("chat_font_size", config.DEFAULT_USER_STATE["chat_font_size"])
    try:
        initial_font_size_state = int(initial_font_size_state)
        if not (config.MIN_CHAT_FONT_SIZE <= initial_font_size_state <= config.MAX_CHAT_FONT_SIZE):
            logger.warning(f"Font size {initial_font_size_state} from state out of range. Clamping to default.")
            initial_font_size_state = config.DEFAULT_CHAT_FONT_SIZE
    except (ValueError, TypeError):
        logger.warning(f"Invalid font size '{initial_font_size_state}' in user_state.json. Defaulting.")
        initial_font_size_state = config.DEFAULT_CHAT_FONT_SIZE
    user_state["chat_font_size"] = initial_font_size_state 
    current_chat_font_size_applied = initial_font_size_state
    logger.info(f"Initial chat font size set to: {current_chat_font_size_applied}")


    logger.info("Initializing Tkinter and GUIManager...")
    app_tk_instance = tk.Tk()
    
    # Define ALL action callbacks that GUIManager might need access to
    action_callbacks_for_gui = {
        'toggle_speaking_recording': toggle_speaking_recording, 
        'on_exit': on_app_exit, # For tray exit and potentially other true exits
        'unload_bark_model': _unload_bark_model_action,
        'reload_bark_model': _reload_bark_model_action,
        'unload_whisper_model': _unload_whisper_model_action,
        'reload_whisper_model': _reload_whisper_model_action,
    }

    try:
        gui = GUIManager(app_tk_instance, action_callbacks_for_gui, 
                         initial_theme=current_gui_theme, 
                         initial_font_size=current_chat_font_size_applied)
        logger.info("GUIManager initialized successfully.")
    except Exception as e_gui:
        logger.critical(f"Failed to initialize GUIManager: {e_gui}", exc_info=True)
        if app_tk_instance:
            try: app_tk_instance.destroy()
            except: pass 
        sys.exit(1)

    logger.debug("Populating GUI callbacks dictionary...")
    gui_callbacks['status_update'] = gui.update_status_label
    gui_callbacks['speak_button_update'] = gui.update_speak_button
    gui_callbacks['act_status_update'] = gui.update_act_status
    gui_callbacks['inet_status_update'] = gui.update_inet_status
    gui_callbacks['webui_status_update'] = gui.update_webui_status
    gui_callbacks['tele_status_update'] = gui.update_tele_status
    gui_callbacks['memory_status_update'] = gui.update_memory_status
    gui_callbacks['hearing_status_update'] = gui.update_hearing_status
    gui_callbacks['voice_status_update'] = gui.update_voice_status
    gui_callbacks['mind_status_update'] = gui.update_mind_status
    
    gui_callbacks['messagebox_error'] = gui.show_error_messagebox
    gui_callbacks['messagebox_info'] = gui.show_info_messagebox
    gui_callbacks['messagebox_warn'] = gui.show_warning_messagebox
    gui_callbacks['add_user_message_to_display'] = gui.add_user_message_to_display
    gui_callbacks['add_assistant_message_to_display'] = gui.add_assistant_message_to_display
    gui_callbacks['on_recording_finished'] = process_recorded_audio_and_interact
    gui_callbacks['gpu_status_update_display'] = gui.update_gpu_status_display
    gui_callbacks['update_todo_list'] = gui.update_todo_list
    gui_callbacks['update_calendar_events_list'] = gui.update_calendar_events_list
    gui_callbacks['apply_application_theme'] = gui.apply_theme
    gui_callbacks['apply_chat_font_size'] = gui.apply_chat_font_size
    gui_callbacks['update_chat_display_from_list'] = gui.update_chat_display_from_list
    gui_callbacks['update_kanban_pending'] = gui.update_kanban_pending
    gui_callbacks['update_kanban_in_process'] = gui.update_kanban_in_process
    gui_callbacks['update_kanban_completed'] = gui.update_kanban_completed
    logger.info("GUI callbacks dictionary populated.")

    if "last_used_language" not in assistant_state:
        assistant_state["last_used_language"] = config.DEFAULT_ASSISTANT_STATE.get("last_used_language", "en")
        logger.info(f"Assistant state 'last_used_language' initialized to default: {assistant_state['last_used_language']}")

    gui.update_chat_display_from_list(chat_history)
    logger.info("Initial chat history displayed on GUI.")

    logger.info("Populating initial User Info (Todos, Calendar) and Assistant Kanban on GUI.")
    if 'update_todo_list' in gui_callbacks:
        gui_callbacks['update_todo_list'](user_state.get("todos", []))
    if 'update_calendar_events_list' in gui_callbacks:
        gui_callbacks['update_calendar_events_list'](user_state.get("calendar_events", []))
    
    initial_assistant_tasks = assistant_state.get("internal_tasks", {})
    if not isinstance(initial_assistant_tasks, dict): 
        logger.warning(f"Initial 'internal_tasks' in assistant_state.json is not a dict: {initial_assistant_tasks}. Using empty for Kanban GUI.")
        initial_assistant_tasks = {} 

    if 'update_kanban_pending' in gui_callbacks:
        gui_callbacks['update_kanban_pending'](initial_assistant_tasks.get("pending", []))
    if 'update_kanban_in_process' in gui_callbacks:
        gui_callbacks['update_kanban_in_process'](initial_assistant_tasks.get("in_process", []))
    if 'update_kanban_completed' in gui_callbacks:
        gui_callbacks['update_kanban_completed'](initial_assistant_tasks.get("completed", []))

    logger.info("Initializing GPU Monitor...")
    if gpu_monitor.PYNVML_AVAILABLE:
        _active_gpu_monitor = gpu_monitor.get_gpu_monitor_instance(
            gui_callbacks=gui_callbacks, update_interval=2, gpu_index=0
        )
        if _active_gpu_monitor and _active_gpu_monitor.active:
            _active_gpu_monitor.start()
            logger.info("GPU Monitor started successfully.")
        elif _active_gpu_monitor and not _active_gpu_monitor.active: 
             logger.warning("PYNVML available, but GPUMonitor failed to initialize or activate.")
             if 'gpu_status_update_display' in gui_callbacks:
                 gui_callbacks['gpu_status_update_display']("ERR", "ERR", "InitFail")
        elif not _active_gpu_monitor: 
             logger.error("PYNVML available, but get_gpu_monitor_instance returned None.")
    else:
        logger.info("PYNVML not available, GPU monitor not started.")
        if 'gpu_status_update_display' in gui_callbacks:
            gui_callbacks['gpu_status_update_display']("N/A", "N/A", "na_nvml")

    logger.info("Starting model loader thread...")
    model_loader_thread = threading.Thread(target=load_all_models_sequentially, daemon=True, name="ModelLoaderThread")
    model_loader_thread.start()

    logger.info("Starting Tkinter mainloop...")
    try:
        # If pystray is managing the app lifecycle (e.g. no main window shown initially),
        # then app_tk_instance.mainloop() might not be the primary loop.
        # However, with a visible window that hides to tray, mainloop is still needed.
        app_tk_instance.mainloop()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt detected by mainloop. Initiating shutdown.")
    except tk.TclError as e: 
        if "application has been destroyed" in str(e).lower():
            logger.info("Tkinter mainloop TclError: Application already destroyed (likely during shutdown).")
        else:
            logger.error(f"Unhandled TclError in mainloop: {e}. Initiating shutdown.", exc_info=True)
    except Exception as e_mainloop:
        logger.critical(f"Unexpected critical error in Tkinter mainloop: {e_mainloop}", exc_info=True)
    finally:
        logger.info("Mainloop exited or error occurred. Ensuring graceful shutdown via on_app_exit().")
        on_app_exit() 
        logger.info("Application main thread has finished.")