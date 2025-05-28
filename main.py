# main.py
import tkinter as tk
import threading
import sys
import gc
import os
import re
import logging # Keep standard logging for initial fallback
import requests 
import queue # For communication with Telegram thread
import asyncio # For Telegram bot interaction

# --- Setup Custom Logger ---
try:
    # Assuming logger.py is in the project root
    import logger as app_logger_module 
    logger = app_logger_module.get_logger("Iri-shka_App.Main")
except ImportError as e:
    # Fallback basic logging if custom logger fails
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stderr)
    logger = logging.getLogger("Iri-shka_App.Main_Fallback")
    logger.critical(f"Failed to import custom logger module from 'logger.py': {e}. Using basicConfig.", exc_info=True)
except Exception as e:
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stderr)
    logger = logging.getLogger("Iri-shka_App.Main_Fallback")
    logger.critical(f"CRITICAL ERROR during custom logger initialization: {e}. Using basicConfig.", exc_info=True)
# --- End Custom Logger Setup ---

logger.info("--- APPLICATION MAIN.PY ENTRY POINT ---") 

try:
    import config # Application specific configuration
    from utils import file_utils, state_manager, whisper_handler, ollama_handler, audio_processor, tts_manager
    from utils import gpu_monitor
    from utils.telegram_handler import TelegramBotHandler # Telegram bot class
    from gui_manager import GUIManager # GUI management class
    logger.info("Core modules imported successfully.")
except ImportError as e_import:
    logger.critical(f"CRITICAL IMPORT ERROR in main.py: {e_import}", exc_info=True)
    sys.exit(1)
except Exception as e_gen_import:
    logger.critical(f"CRITICAL UNEXPECTED ERROR during core imports in main.py: {e_gen_import}", exc_info=True)
    sys.exit(1)


logger.info(f"Python version: {sys.version}")
logger.info(f"OS: {sys.platform}")


try:
    import numpy as np
    logger.info(f"NumPy version {np.__version__} imported successfully.")
except ImportError:
    logger.critical("CRITICAL: NumPy Not Found. Please install NumPy: pip install numpy. Exiting.")
    sys.exit(1)

# Import whisper specifically for whisper.load_audio if WHISPER_CAPABLE
_whisper_module_for_load_audio = None
if whisper_handler.WHISPER_CAPABLE:
    try:
        import whisper
        _whisper_module_for_load_audio = whisper
        logger.info("Whisper module imported in main.py for load_audio utility.")
    except ImportError:
        logger.warning("Failed to import whisper in main.py; Telegram voice WAV loading might fail.")


# --- Global Variables ---
gui = None
app_tk_instance = None
_active_gpu_monitor = None
telegram_bot_handler_instance = None 
telegram_message_queue = queue.Queue() 

chat_history = []
user_state = {}
assistant_state = {}
ollama_ready = False 
current_gui_theme = config.GUI_THEME_LIGHT 
current_chat_font_size_applied = config.DEFAULT_CHAT_FONT_SIZE 

gui_callbacks = {} 

# --- Utility Functions ---
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
    if "json" in lower_msg and ("invalid" in lower_msg or "not valid" in lower_msg) : return "JSON", "error"
    if "empty content" in lower_msg or "empty response" in lower_msg : return "EMP", "error"
    if "missing keys" in lower_msg : return "KEYS", "error"
    if "model not found" in lower_msg or "pull model" in lower_msg : return "NOMDL", "error"
    return "NRDY", "error"

# --- Core Interaction Logic ---
def _handle_llm_interaction(input_text, source="gui", detected_language_code=None):
    global chat_history, user_state, assistant_state, ollama_ready 
    global current_gui_theme, current_chat_font_size_applied 
    
    logger.info(f"Handling LLM interaction. Source: {source}, Input: '{input_text[:70]}...'")

    assistant_response_text = "Error: Processing failed." 
    playback_callback_for_tts = None 
    
    current_lang_code_for_state = "en" 
    if detected_language_code and detected_language_code in ["ru", "en"]:
        current_lang_code_for_state = detected_language_code
    elif assistant_state.get("last_used_language") in ["ru", "en"]:
        current_lang_code_for_state = assistant_state.get("last_used_language")

    selected_bark_voice_preset = config.BARK_VOICE_PRESET_EN
    language_instruction_for_llm = config.LANGUAGE_INSTRUCTION_NON_RUSSIAN
    assistant_error_response_text = "I encountered an issue. Please try again." 

    if current_lang_code_for_state == "ru":
        selected_bark_voice_preset = config.BARK_VOICE_PRESET_RU
        language_instruction_for_llm = config.LANGUAGE_INSTRUCTION_RUSSIAN
        assistant_error_response_text = "Произошла ошибка. Пожалуйста, попробуйте еще раз." 
    
    user_display_text = input_text # Default
    if source == "gui" and detected_language_code: 
        user_display_text = f"{input_text} (Lang: {detected_language_code})"
    elif source == "telegram_voice": # For messages from Telegram voice
        user_display_text = f"{input_text} (from Telegram voice)"
    # For "telegram" (text), input_text is already fine.
    
    if gui_callbacks and 'add_user_message_to_display' in gui_callbacks:
        # Let add_user_message_to_display handle prefix based on source
        gui_callbacks['add_user_message_to_display'](input_text, source=source) # Pass original input_text
    
    if gui_callbacks and 'status_update' in gui_callbacks: gui_callbacks['status_update']("Thinking...")
    if gui_callbacks and 'mind_status_update' in gui_callbacks: gui_callbacks['mind_status_update']("MIND: THK", "thinking")

    assistant_state["last_used_language"] = current_lang_code_for_state
    logger.debug(f"Assistant state 'last_used_language' set to: {current_lang_code_for_state}")

    ollama_data, ollama_error = ollama_handler.call_ollama_for_chat_response(
        input_text, chat_history, user_state, assistant_state,
        language_instruction_for_llm, gui_callbacks
    )
    current_turn_for_history = {"user": user_display_text, "source": source} # Use the formatted user_display_text for history

    if ollama_error:
        logger.error(f"Ollama call failed: {ollama_error}")
        ollama_ready = False 
        short_code, status_type = _parse_ollama_error_to_short_code(ollama_error)
        if gui_callbacks and 'mind_status_update' in gui_callbacks:
            gui_callbacks['mind_status_update'](f"MIND: {short_code}", status_type)
        if gui_callbacks and 'status_update' in gui_callbacks:
            gui_callbacks['status_update'](f"LLM Error: {ollama_error[:60]}")

        if current_lang_code_for_state == "ru":
            if "502" in ollama_error or "connection" in ollama_error.lower(): assistant_response_text = "Кажется, у моего мыслительного центра временные неполадки. Пожалуйста, проверьте его состояние."
            elif "timeout" in ollama_error.lower(): assistant_response_text = "Я слишком долго думала и не смогла ответить вовремя."
            elif "not found" in ollama_error.lower() and "model" in ollama_error.lower() : assistant_response_text = "Модель для обработки вашего запроса не найдена. Возможно, нужно ее загрузить."
            else: assistant_response_text = "При обработке вашего запроса произошла внутренняя ошибка."
        else: 
            if "502" in ollama_error or "connection" in ollama_error.lower(): assistant_response_text = "My thinking center seems to be having a temporary issue. Please check its status."
            elif "timeout" in ollama_error.lower(): assistant_response_text = "I took too long to think and couldn't respond in time."
            elif "not found" in ollama_error.lower() and "model" in ollama_error.lower() : assistant_response_text = "The model to process your request was not found. It might need to be downloaded."
            else: assistant_response_text = "An internal error occurred while processing your request."
        
        selected_bark_voice_preset = config.BARK_VOICE_PRESET_RU if current_lang_code_for_state == "ru" else config.BARK_VOICE_PRESET_EN
        logger.info(f"Generated fallback error message for user: '{assistant_response_text}'")
        current_turn_for_history["assistant"] = f"[LLM Error: {assistant_response_text}]"
        if gui_callbacks and 'add_assistant_message_to_display' in gui_callbacks:
            gui_callbacks['add_assistant_message_to_display'](current_turn_for_history["assistant"], is_error=True, source=source)

    else: 
        logger.info("Ollama call successful.")
        ollama_ready = True
        if gui_callbacks and 'mind_status_update' in gui_callbacks:
            gui_callbacks['mind_status_update']("MIND: RDY", "ready")
        
        assistant_response_text = ollama_data["answer_to_user"]
        new_user_state_from_llm = ollama_data["updated_user_state"]
        
        new_theme_from_llm = new_user_state_from_llm.get("gui_theme", current_gui_theme)
        if new_theme_from_llm != current_gui_theme:
            if new_theme_from_llm in [config.GUI_THEME_LIGHT, config.GUI_THEME_DARK]:
                logger.info(f"Theme change requested by LLM. From '{current_gui_theme}' to '{new_theme_from_llm}'.")
                if gui and 'apply_application_theme' in gui_callbacks:
                    gui_callbacks['apply_application_theme'](new_theme_from_llm) 
                    current_gui_theme = new_theme_from_llm 
                    logger.info(f"GUI theme changed to {current_gui_theme}.")
                else: logger.warning("Cannot change theme: GUI or callback not found.")
            else: 
                logger.warning(f"LLM suggested invalid theme: '{new_theme_from_llm}'. Ignoring.")
                new_user_state_from_llm["gui_theme"] = current_gui_theme 
        
        new_font_size_from_llm = new_user_state_from_llm.get("chat_font_size", current_chat_font_size_applied)
        try: new_font_size_from_llm = int(new_font_size_from_llm)
        except (ValueError, TypeError):
            logger.warning(f"LLM provided non-integer font size: '{new_font_size_from_llm}'. Using current: {current_chat_font_size_applied}")
            new_font_size_from_llm = current_chat_font_size_applied
        
        clamped_font_size = max(config.MIN_CHAT_FONT_SIZE, min(new_font_size_from_llm, config.MAX_CHAT_FONT_SIZE))
        if clamped_font_size != new_font_size_from_llm:
             logger.warning(f"LLM font size {new_font_size_from_llm} out of range. Clamped to {clamped_font_size}.")
             new_font_size_from_llm = clamped_font_size
        
        if new_font_size_from_llm != current_chat_font_size_applied:
            logger.info(f"Font size change requested by LLM. From '{current_chat_font_size_applied}' to '{new_font_size_from_llm}'.")
            if gui and 'apply_chat_font_size' in gui_callbacks:
                gui_callbacks['apply_chat_font_size'](new_font_size_from_llm) 
                current_chat_font_size_applied = new_font_size_from_llm
                logger.info(f"Chat font size changed to {current_chat_font_size_applied}.")
            else: logger.warning("Cannot change font size: GUI or callback not found.")
        new_user_state_from_llm["chat_font_size"] = current_chat_font_size_applied 

        user_state = new_user_state_from_llm 
        assistant_state = ollama_data["updated_assistant_state"] 
        assistant_state["last_used_language"] = current_lang_code_for_state 

        logger.debug(f"User state updated from LLM: {str(user_state)[:200]}...")
        logger.debug(f"Assistant state updated from LLM: {str(assistant_state)[:200]}...")
        logger.info(f"LLM Response: '{assistant_response_text[:70]}...'")

        if gui_callbacks:
            if 'update_todo_list' in gui_callbacks: gui_callbacks['update_todo_list'](user_state.get("todos", []))
            if 'update_calendar_events_list' in gui_callbacks: gui_callbacks['update_calendar_events_list'](user_state.get("calendar_events", []))
            
            assistant_internal_tasks = assistant_state.get("internal_tasks", {})
            if not isinstance(assistant_internal_tasks, dict): 
                logger.warning(f"LLM returned 'internal_tasks' not as a dict: {assistant_internal_tasks}. Using empty for Kanban.")
                assistant_internal_tasks = {} 
            if 'update_kanban_pending' in gui_callbacks: gui_callbacks['update_kanban_pending'](assistant_internal_tasks.get("pending", []))
            if 'update_kanban_in_process' in gui_callbacks: gui_callbacks['update_kanban_in_process'](assistant_internal_tasks.get("in_process", []))
            if 'update_kanban_completed' in gui_callbacks: gui_callbacks['update_kanban_completed'](assistant_internal_tasks.get("completed", []))

        current_turn_for_history["assistant"] = assistant_response_text
        
        if source == "gui" and tts_manager.is_tts_ready():
            logger.debug("TTS is ready for GUI source. Setting up deferred display for assistant message.")
            captured_response_text_for_tts_cb = assistant_response_text 
            def _deferred_display_on_playback():
                logger.debug("Playback started (GUI), displaying assistant message via callback.")
                if gui_callbacks and 'add_assistant_message_to_display' in gui_callbacks:
                    gui_callbacks['add_assistant_message_to_display'](captured_response_text_for_tts_cb, source="gui")
                if gui_callbacks and 'status_update' in gui_callbacks:
                    gui_callbacks['status_update'](f"Speaking: {captured_response_text_for_tts_cb[:50]}...")
            playback_callback_for_tts = _deferred_display_on_playback
        elif gui_callbacks and 'add_assistant_message_to_display' in gui_callbacks : 
            logger.info(f"Displaying assistant message immediately (Source: {source}, TTS Ready: {tts_manager.is_tts_ready()}).")
            gui_callbacks['add_assistant_message_to_display'](assistant_response_text, source=source)
            if gui_callbacks and 'status_update' in gui_callbacks:
                gui_callbacks['status_update'](f"Iri-shka: {assistant_response_text[:50]}...")
    
    chat_history.append(current_turn_for_history)
    chat_history = state_manager.save_states(chat_history, user_state, assistant_state, gui_callbacks) 
    if gui_callbacks and 'memory_status_update' in gui_callbacks:
        gui_callbacks['memory_status_update']("MEM: SAVED", "saved") 

    if source == "gui":
        if tts_manager.is_tts_ready():
            tts_manager.start_speaking_response(
                assistant_response_text,
                assistant_state.get("persona_name", "Iri-shka"),
                selected_bark_voice_preset, 
                gui_callbacks,
                on_actual_playback_start_gui_callback=playback_callback_for_tts
            )
        elif playback_callback_for_tts is None: 
            logger.info("TTS was not used or failed for GUI, message already displayed. Setting final status.")
            if gui_callbacks and 'status_update' in gui_callbacks:
                status_key = "TTS unavailable" if not tts_manager.TTS_CAPABLE else "TTS not ready"
                gui_callbacks['status_update'](f"{status_key}. Response shown.")
    
    elif source == "telegram" or source == "telegram_voice": # Include telegram_voice here
        if telegram_bot_handler_instance and telegram_bot_handler_instance.async_loop:
            logger.info(f"Sending response to Telegram: '{assistant_response_text[:70]}...'")
            asyncio.run_coroutine_threadsafe(
                telegram_bot_handler_instance.send_message_to_admin(assistant_response_text),
                telegram_bot_handler_instance.async_loop 
            )
        else:
            logger.error("Telegram source specified, but telegram_bot_handler or its loop is None. Cannot send reply.")

    if gui_callbacks:
        enable_speak_btn = whisper_handler.whisper_model_ready
        if 'speak_button_update' in gui_callbacks:
            gui_callbacks['speak_button_update'](enable_speak_btn, "Speak" if enable_speak_btn else "HEAR NRDY")

        is_speaking = tts_manager.current_tts_thread and tts_manager.current_tts_thread.is_alive()
        if 'status_update' in gui_callbacks: 
            if enable_speak_btn and not is_speaking:
                gui_callbacks['status_update']("Ready to listen.")
            elif not enable_speak_btn and not is_speaking: 
                gui_callbacks['status_update']("Hearing module not ready.")
    
    if telegram_bot_handler_instance: # Update assistant_state with current bot status after LLM interaction
        new_bot_status = telegram_bot_handler_instance.get_status()
        if assistant_state.get("telegram_bot_status") != new_bot_status:
            assistant_state["telegram_bot_status"] = new_bot_status
            state_manager.save_states(chat_history, user_state, assistant_state, gui_callbacks)

    logger.info(f"--- End of LLM interaction processing (Source: {source}) ---")


def process_recorded_audio_and_interact(recorded_sample_rate):
    logger.info(f"Processing recorded audio. Sample rate: {recorded_sample_rate} Hz.")
    if not np: 
        logger.error("NumPy missing during audio processing (should be caught at startup).")
        if gui_callbacks and 'status_update' in gui_callbacks: gui_callbacks['status_update']("NumPy missing.")
        if gui_callbacks and 'speak_button_update' in gui_callbacks: gui_callbacks['speak_button_update'](whisper_handler.whisper_model_ready, "Speak" if whisper_handler.whisper_model_ready else "HEAR NRDY")
        return

    audio_float32, audio_frames_for_save = audio_processor.convert_frames_to_numpy(recorded_sample_rate, gui_callbacks)

    if audio_float32 is None:
        logger.warning("Audio processing (convert_frames_to_numpy) returned None. Cannot proceed.")
        if gui_callbacks and 'speak_button_update' in gui_callbacks: gui_callbacks['speak_button_update'](whisper_handler.whisper_model_ready, "Speak" if whisper_handler.whisper_model_ready else "HEAR NRDY")
        if gui_callbacks and 'status_update' in gui_callbacks: gui_callbacks['status_update']("Audio processing failed.")
        return

    if config.SAVE_RECORDINGS_TO_WAV and audio_frames_for_save:
        if file_utils.ensure_folder(config.OUTPUT_FOLDER, gui_callbacks): 
            import datetime
            filename = f"rec_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
            filepath = os.path.join(config.OUTPUT_FOLDER, filename)
            audio_processor.save_wav_data_to_file(filepath, audio_frames_for_save, recorded_sample_rate, gui_callbacks)
    del audio_frames_for_save; gc.collect()

    if not (whisper_handler.WHISPER_CAPABLE and whisper_handler.whisper_model_ready):
        logger.warning("Whisper module not ready or capable. Cannot transcribe.")
        if gui_callbacks and 'status_update' in gui_callbacks: gui_callbacks['status_update']("Hearing module not ready.")
        if gui_callbacks and 'speak_button_update' in gui_callbacks: gui_callbacks['speak_button_update'](False, "HEAR NRDY")
        return

    if gui_callbacks and 'status_update' in gui_callbacks: gui_callbacks['status_update']("Transcribing audio...")
    transcribed_text, trans_err, detected_language_code = whisper_handler.transcribe_audio(audio_float32, language=None, gui_callbacks=gui_callbacks)
    
    lang_for_error_msg = assistant_state.get("last_used_language", "en")
    if detected_language_code and detected_language_code in ["ru", "en"]: 
        lang_for_error_msg = detected_language_code
    
    error_message_on_transcription_fail = "I didn't catch that, could you please repeat?"
    error_voice_preset = config.BARK_VOICE_PRESET_EN
    if lang_for_error_msg == "ru":
        error_message_on_transcription_fail = "Я не расслышала, не могли бы вы повторить?"
        error_voice_preset = config.BARK_VOICE_PRESET_RU

    if trans_err or not transcribed_text:
        logger.warning(f"Transcription failed or empty. Error: {trans_err}, Text: '{transcribed_text}', Lang: {detected_language_code}")
        if gui_callbacks:
            if 'status_update' in gui_callbacks: gui_callbacks['status_update'](f"Transcription: {trans_err or 'Empty.'} (Lang: {detected_language_code or 'N/A'})")
            if 'add_user_message_to_display' in gui_callbacks: gui_callbacks['add_user_message_to_display']("[Silent or Unclear Audio]", source="gui")
            if 'add_assistant_message_to_display' in gui_callbacks: gui_callbacks['add_assistant_message_to_display'](error_message_on_transcription_fail, is_error=True, source="gui")
        
        current_turn_for_history = {"user": "[Silent or Unclear Audio]", "assistant": error_message_on_transcription_fail, "source": "gui"}
        chat_history.append(current_turn_for_history)
        state_manager.save_states(chat_history, user_state, assistant_state, gui_callbacks) 
        if gui_callbacks and 'memory_status_update' in gui_callbacks: gui_callbacks['memory_status_update']("MEM: SAVED", "saved")

        if tts_manager.is_tts_ready():
            tts_manager.start_speaking_response(error_message_on_transcription_fail, assistant_state.get("persona_name", "Iri-shka"), error_voice_preset, gui_callbacks)
        
        if gui_callbacks and 'speak_button_update' in gui_callbacks: gui_callbacks['speak_button_update'](whisper_handler.whisper_model_ready, "Speak" if whisper_handler.whisper_model_ready else "HEAR NRDY")
        if gui_callbacks and 'status_update' in gui_callbacks and not (tts_manager.current_tts_thread and tts_manager.current_tts_thread.is_alive()):
             gui_callbacks['status_update']("Ready to listen.")
    else: 
        logger.info(f"Transcription successful: '{transcribed_text[:70]}...' (Lang: {detected_language_code})")
        _handle_llm_interaction(transcribed_text, source="gui", detected_language_code=detected_language_code)


def process_telegram_text_message(user_id, text_message): # Renamed for clarity
    logger.info(f"Processing Telegram text message from user ID {user_id}: '{text_message[:70]}...'")
    lang_context_for_llm = assistant_state.get("last_used_language", "en")
    # For text messages, we don't have a detected language from Whisper upfront.
    # LLM will infer or use assistant's last_used_language.
    _handle_llm_interaction(text_message, source="telegram", detected_language_code=None)


def process_telegram_voice_message(user_id, wav_filepath):
    logger.info(f"Processing Telegram voice message from user ID {user_id}, WAV: {wav_filepath}")
    
    audio_numpy_array = None
    transcribed_text = None
    trans_err = "File load or transcription pre-check failed."
    detected_language_code = None

    if not (whisper_handler.WHISPER_CAPABLE and whisper_handler.whisper_model_ready):
        logger.warning("Whisper module not ready or capable. Cannot transcribe Telegram voice.")
        trans_err = "Hearing module not ready."
    elif not _whisper_module_for_load_audio:
        logger.error("Whisper module (for load_audio) not available in main.py. Cannot load Telegram voice WAV.")
        trans_err = "Audio loading module missing."
    else:
        try:
            if gui_callbacks and 'status_update' in gui_callbacks:
                gui_callbacks['status_update']("Loading Telegram voice audio...")
            audio_numpy_array = _whisper_module_for_load_audio.load_audio(wav_filepath)
            logger.info(f"Telegram voice WAV {wav_filepath} loaded into NumPy array.")
            
            if gui_callbacks and 'status_update' in gui_callbacks:
                gui_callbacks['status_update']("Transcribing Telegram voice...")
            transcribed_text, trans_err, detected_language_code = whisper_handler.transcribe_audio(
                audio_numpy_array, language=None, gui_callbacks=gui_callbacks
            )
        except Exception as e:
            logger.error(f"Error loading or transcribing Telegram voice WAV {wav_filepath}: {e}", exc_info=True)
            trans_err = f"Error processing voice: {e}"
            transcribed_text = None # Ensure it's None

    # Determine language for error message
    lang_for_error_msg = assistant_state.get("last_used_language", "en")
    if detected_language_code and detected_language_code in ["ru", "en"]: 
        lang_for_error_msg = detected_language_code
    
    error_message_on_transcription_fail = "I couldn't understand your voice message. Please try again or send text."
    if lang_for_error_msg == "ru":
        error_message_on_transcription_fail = "Я не смогла разобрать ваше голосовое сообщение. Пожалуйста, попробуйте еще раз или отправьте текст."

    if trans_err or not transcribed_text:
        logger.warning(f"Telegram voice transcription failed or empty. Error: {trans_err}, Text: '{transcribed_text}', Lang: {detected_language_code}")
        if gui_callbacks:
            if 'status_update' in gui_callbacks:
                gui_callbacks['status_update'](f"Telegram Voice Transcription: {trans_err or 'Empty.'}")
            # Display transcription error in GUI chat as well
            if 'add_user_message_to_display' in gui_callbacks:
                 gui_callbacks['add_user_message_to_display']("[Unclear Telegram Voice]", source="telegram_voice")
            if 'add_assistant_message_to_display' in gui_callbacks:
                 gui_callbacks['add_assistant_message_to_display'](error_message_on_transcription_fail, is_error=True, source="telegram_voice")
        
        # Send error back to Telegram
        if telegram_bot_handler_instance and telegram_bot_handler_instance.async_loop:
            asyncio.run_coroutine_threadsafe(
                telegram_bot_handler_instance.send_message_to_admin(error_message_on_transcription_fail),
                telegram_bot_handler_instance.async_loop
            )
        
        # Save to chat history
        current_turn_for_history = {"user": "[Unclear Telegram Voice]", "assistant": error_message_on_transcription_fail, "source": "telegram_voice"}
        chat_history.append(current_turn_for_history)
        state_manager.save_states(chat_history, user_state, assistant_state, gui_callbacks)
        if gui_callbacks and 'memory_status_update' in gui_callbacks:
            gui_callbacks['memory_status_update']("MEM: SAVED", "saved")

    else:
        logger.info(f"Telegram voice transcription successful: '{transcribed_text[:70]}...' (Lang: {detected_language_code})")
        _handle_llm_interaction(transcribed_text, source="telegram_voice", detected_language_code=detected_language_code)

    # Clean up the temporary WAV file
    if os.path.exists(wav_filepath):
        try:
            os.remove(wav_filepath)
            logger.info(f"Removed temporary Telegram voice WAV: {wav_filepath}")
        except Exception as e_rem_wav:
            logger.warning(f"Could not remove temporary WAV file {wav_filepath}: {e_rem_wav}")


def toggle_speaking_recording():
    logger.info(f"Toggle speaking/recording requested. Currently recording: {audio_processor.is_recording_active()}")
    if not np:
        logger.error("NumPy missing on toggle_speaking_recording (should be caught at startup).")
        if gui_callbacks and 'messagebox_error' in gui_callbacks: gui_callbacks['messagebox_error']("NumPy Missing", "NumPy is required to process audio.")
        return

    if not audio_processor.is_recording_active(): 
        logger.info("Attempting to start recording.")
        if not (whisper_handler.WHISPER_CAPABLE and whisper_handler.whisper_model_ready):
            logger.warning("Cannot start recording: Whisper module not ready or capable.")
            if gui_callbacks and 'messagebox_warn' in gui_callbacks: gui_callbacks['messagebox_warn']("Hearing Not Ready", "The hearing module (Whisper) is not ready.")
            return
        if tts_manager.TTS_CAPABLE and tts_manager.is_tts_loading():
            logger.info("Cannot start recording: TTS resources are still loading.")
            if gui_callbacks and 'messagebox_info' in gui_callbacks: gui_callbacks['messagebox_info']("Please Wait", "TTS resources are still loading.")
            return
        if tts_manager.TTS_CAPABLE: 
            logger.debug("Stopping any current TTS speech before recording.")
            tts_manager.stop_current_speech(gui_callbacks) 

        if audio_processor.start_recording(gui_callbacks): 
            logger.info("Recording started successfully via audio_processor.")
            if gui_callbacks and 'speak_button_update' in gui_callbacks: gui_callbacks['speak_button_update'](True, "Listening...")
        else: 
            logger.warning("audio_processor.start_recording failed (details should be in AudioProcessor logs).")
    else: 
        logger.info("Attempting to stop recording.")
        audio_processor.stop_recording() 
        if gui_callbacks and 'speak_button_update' in gui_callbacks: gui_callbacks['speak_button_update'](False, "Processing...")

# --- Model and Bot Control Actions (for tray menu & auto-start) ---
def _unload_bark_model_action():
    logger.info("Unloading Bark TTS model action triggered.")
    if gui_callbacks and 'voice_status_update' in gui_callbacks: gui_callbacks['voice_status_update']("VOICE: OFF", "off") 
    if gui_callbacks and 'status_update' in gui_callbacks: gui_callbacks['status_update']("Unloading Bark TTS...")
    threading.Thread(target=lambda: tts_manager.unload_bark_model(gui_callbacks), daemon=True, name="UnloadBarkThread").start()

def _reload_bark_model_action():
    logger.info("Reloading Bark TTS model action triggered.")
    if gui_callbacks and 'voice_status_update' in gui_callbacks: gui_callbacks['voice_status_update']("VOICE: CHK", "loading")
    if gui_callbacks and 'status_update' in gui_callbacks: gui_callbacks['status_update']("Reloading Bark TTS...")
    threading.Thread(target=lambda: tts_manager.load_bark_resources(gui_callbacks), daemon=True, name="ReloadBarkThread").start()

def _unload_whisper_model_action():
    logger.info("Unloading Whisper STT model action triggered.")
    if gui_callbacks and 'hearing_status_update' in gui_callbacks: gui_callbacks['hearing_status_update']("HEAR: OFF", "off")
    if gui_callbacks and 'speak_button_update' in gui_callbacks: gui_callbacks['speak_button_update'](False, "HEAR OFF")
    if gui_callbacks and 'status_update' in gui_callbacks: gui_callbacks['status_update']("Unloading Whisper STT...")
    threading.Thread(target=lambda: whisper_handler.unload_whisper_model(gui_callbacks), daemon=True, name="UnloadWhisperThread").start()

def _reload_whisper_model_action():
    logger.info("Reloading Whisper STT model action triggered.")
    if gui_callbacks and 'hearing_status_update' in gui_callbacks: gui_callbacks['hearing_status_update']("HEAR: CHK", "loading")
    if gui_callbacks and 'speak_button_update' in gui_callbacks: gui_callbacks['speak_button_update'](False, "Loading...")
    if gui_callbacks and 'status_update' in gui_callbacks: gui_callbacks['status_update']("Reloading Whisper STT...")
    threading.Thread(target=lambda: whisper_handler.load_whisper_model(config.WHISPER_MODEL_SIZE, gui_callbacks), daemon=True, name="ReloadWhisperThread").start()

def _start_telegram_bot_action():
    logger.info("Start Telegram Bot action triggered from main.")
    if telegram_bot_handler_instance:
        if not telegram_bot_handler_instance.start_polling(): # start_polling now returns success/fail
            logger.warning("Telegram bot start_polling indicated it did not start (e.g. already running/loading or config issue).")
            # GUI status should be updated by TelegramBotHandler itself.
    else:
        logger.error("Cannot start Telegram bot: instance is None.")
        if gui_callbacks:
            if 'messagebox_error' in gui_callbacks: gui_callbacks['messagebox_error']("Telegram Error", "Bot handler not initialized. Check token/admin ID.")
            if 'tele_status_update' in gui_callbacks: gui_callbacks['tele_status_update']("TELE: ERR", "error")

def _stop_telegram_bot_action():
    logger.info("Stop Telegram Bot action triggered from main.")
    if telegram_bot_handler_instance:
        telegram_bot_handler_instance.stop_polling() 
    else:
        logger.error("Cannot stop Telegram bot: instance is None.")
        if gui_callbacks and 'tele_status_update' in gui_callbacks: gui_callbacks['tele_status_update']("TELE: ERR", "error")


# --- Application Lifecycle Functions ---
def on_app_exit():
    global gui, app_tk_instance, _active_gpu_monitor, telegram_bot_handler_instance
    logger.info("Application closing sequence initiated...")

    if telegram_bot_handler_instance:
        logger.info("Shutting down Telegram bot...")
        telegram_bot_handler_instance.stop_polling() 
        telegram_bot_handler_instance.full_shutdown() 
        telegram_bot_handler_instance = None
        logger.info("Telegram bot shutdown complete from main.")

    if _active_gpu_monitor:
        logger.info("Shutting down GPU monitor...")
        _active_gpu_monitor.stop() 
        _active_gpu_monitor = None
        logger.info("GPU monitor shutdown complete from main.")

    logger.info("Shutting down audio resources...")
    audio_processor.shutdown_audio_resources()
    logger.info("Audio resources shutdown complete from main.")

    if tts_manager.TTS_CAPABLE:
        logger.info("Stopping current TTS speech and shutting down TTS module...")
        tts_manager.stop_current_speech(gui_callbacks) 
        tts_manager.full_shutdown_tts_module() 
        logger.info("TTS module shutdown complete from main.")

    if whisper_handler.WHISPER_CAPABLE:
        logger.info("Cleaning up Whisper model...")
        whisper_handler.full_shutdown_whisper_module()
        logger.info("Whisper model cleanup complete from main.")

    if gui:
        logger.info("Destroying GUI window (which also stops tray icon)...")
        gui.destroy_window() 
        gui = None
        app_tk_instance = None 
        logger.info("GUI window and tray destroyed from main.")
    elif app_tk_instance : 
        try: app_tk_instance.destroy()
        except: pass

    logger.info("Application exit sequence fully complete.")
    logging.shutdown() 


def check_search_engine_status():
    url = config.SEARCH_ENGINE_URL.rstrip('/') + '/search' 
    params = {'q': 'ping', 'format': 'json'} 
    short_text_prefix = "INET: "
    
    logger.info(f"Pinging Search Engine at {url} with params {params}")
    try:
        headers = {'User-Agent': 'Iri-shka_AI_Assistant/1.0'} 
        response = requests.get(url, params=params, headers=headers, timeout=config.SEARCH_ENGINE_PING_TIMEOUT)
        response.raise_for_status() 
        try: response.json() 
        except requests.exceptions.JSONDecodeError: 
            logger.warning(f"Search Engine ping HTTP OK, but response not valid JSON. Content: {response.text[:100]}...")
        logger.info(f"Search Engine ping successful (Status {response.status_code}).")
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

def _process_queued_telegram_messages():
    try:
        while not telegram_message_queue.empty():
            queued_item = telegram_message_queue.get_nowait() 
            
            if not isinstance(queued_item, tuple) or len(queued_item) != 3:
                logger.error(f"Invalid item dequeued from Telegram queue: {queued_item}")
                telegram_message_queue.task_done()
                continue

            msg_type, user_id, data = queued_item
            
            if msg_type == "telegram_text":
                logger.info(f"Dequeued Telegram TEXT message from user {user_id} for processing.")
                process_telegram_text_message(user_id, data)
            elif msg_type == "telegram_voice_wav":
                logger.info(f"Dequeued Telegram VOICE_WAV message from user {user_id} for processing. Path: {data}")
                process_telegram_voice_message(user_id, data) # data is wav_filepath
            else:
                logger.warning(f"Unknown Telegram message type dequeued: {msg_type}")
                
            telegram_message_queue.task_done() 
    except queue.Empty:
        pass 
    except Exception as e:
        logger.error(f"Error processing queued Telegram message in main thread: {e}", exc_info=True)
    
    if gui and app_tk_instance and app_tk_instance.winfo_exists():
        app_tk_instance.after(300, _process_queued_telegram_messages) 

def load_all_models_and_services():
    global ollama_ready, chat_history, user_state, assistant_state 
    logger.info("--- Starting sequential model and services loading/checking thread ---")

    if gui_callbacks and 'status_update' in gui_callbacks: gui_callbacks['status_update']("Initializing components...")
    if gui_callbacks and 'speak_button_update' in gui_callbacks: gui_callbacks['speak_button_update'](False, "Loading...")
    if gui_callbacks and 'act_status_update' in gui_callbacks: gui_callbacks['act_status_update']("ACT: IDLE", "idle") 
    if gui_callbacks and 'webui_status_update' in gui_callbacks: gui_callbacks['webui_status_update']("WEBUI: OFF", "off") 

    if gui_callbacks and 'inet_status_update' in gui_callbacks: gui_callbacks['inet_status_update']("INET: CHK", "checking")
    inet_short_text, inet_status_type = check_search_engine_status()
    if gui_callbacks and 'inet_status_update' in gui_callbacks: gui_callbacks['inet_status_update'](inet_short_text, inet_status_type)

    if gui_callbacks and 'memory_status_update' in gui_callbacks:
        gui_callbacks['memory_status_update']("MEM: LOADED" if chat_history else "MEM: FRESH", "ready" if chat_history else "fresh") 

    if "last_used_language" not in assistant_state:
        assistant_state["last_used_language"] = config.DEFAULT_ASSISTANT_STATE.get("last_used_language", "en")

    if gui_callbacks and 'hearing_status_update' in gui_callbacks: gui_callbacks['hearing_status_update']("HEAR: CHK", "loading") 
    if whisper_handler.WHISPER_CAPABLE: whisper_handler.load_whisper_model(config.WHISPER_MODEL_SIZE, gui_callbacks)
    else:
        logger.warning("Whisper not capable, skipping load.")
        if gui_callbacks and 'hearing_status_update' in gui_callbacks: gui_callbacks['hearing_status_update']("HEAR: N/A", "na")

    if gui_callbacks and 'voice_status_update' in gui_callbacks: gui_callbacks['voice_status_update']("VOICE: CHK", "loading") 
    if tts_manager.TTS_CAPABLE: tts_manager.load_bark_resources(gui_callbacks)
    else:
        logger.warning("TTS (Bark) not capable, skipping load.")
        if gui_callbacks and 'voice_status_update' in gui_callbacks: gui_callbacks['voice_status_update']("VOICE: N/A", "na")

    if gui_callbacks and 'mind_status_update' in gui_callbacks: gui_callbacks['mind_status_update']("MIND: CHK", "pinging")
    ollama_ready_flag, ollama_log_msg = ollama_handler.check_ollama_server_and_model()
    ollama_ready = ollama_ready_flag 
    if ollama_ready_flag:
        logger.info(f"Ollama server and model '{config.OLLAMA_MODEL_NAME}' ready. Status: {ollama_log_msg}")
        if gui_callbacks and 'mind_status_update' in gui_callbacks: gui_callbacks['mind_status_update']("MIND: RDY", "ready")
    else:
        logger.warning(f"Ollama server or model '{config.OLLAMA_MODEL_NAME}' not ready. Status: {ollama_log_msg}")
        short_code, status_type = _parse_ollama_error_to_short_code(ollama_log_msg)
        if gui_callbacks and 'mind_status_update' in gui_callbacks: gui_callbacks['mind_status_update'](f"MIND: {short_code}", status_type)

    if config.START_BOT_ON_APP_START:
        logger.info("START_BOT_ON_APP_START is True. Attempting to start Telegram bot.")
        if telegram_bot_handler_instance:
            telegram_bot_handler_instance.start_polling() 
        else: 
            logger.error("Cannot auto-start Telegram bot: instance is None.")
            if gui_callbacks and 'tele_status_update' in gui_callbacks:
                current_tele_status_from_config = "error" # Default if instance is None
                current_tele_text = "TELE: ERR"
                if not config.TELEGRAM_BOT_TOKEN: current_tele_text = "TELE: NO TOK"; current_tele_status_from_config="no_token"
                elif not config.TELEGRAM_ADMIN_USER_ID: current_tele_text = "TELE: NO ADM"; current_tele_status_from_config="no_admin"
                gui_callbacks['tele_status_update'](current_tele_text, current_tele_status_from_config)
    else: 
        logger.info("START_BOT_ON_APP_START is False. Telegram bot will not be started automatically.")
        if gui_callbacks and 'tele_status_update' in gui_callbacks:
            initial_tele_status = "off"
            initial_tele_text = "TELE: OFF"
            if not config.TELEGRAM_BOT_TOKEN: initial_tele_text = "TELE: NO TOK"; initial_tele_status = "no_token"
            elif not config.TELEGRAM_ADMIN_USER_ID: initial_tele_text = "TELE: NO ADM"; initial_tele_status = "no_admin" 
            gui_callbacks['tele_status_update'](initial_tele_text, initial_tele_status)

    if whisper_handler.whisper_model_ready:
        logger.info("Initial models loaded/checked. Whisper ready.")
        ready_msg = "Ready to listen."
        if tts_manager.is_tts_loading(): ready_msg = "Ready (TTS loading...)"
        elif not tts_manager.is_tts_ready() and tts_manager.TTS_CAPABLE: ready_msg = "Ready (TTS not ready)."
        if gui_callbacks and 'status_update' in gui_callbacks: gui_callbacks['status_update'](ready_msg)
    else:
        logger.warning("Whisper model not ready after loading sequence.")
        if gui_callbacks and 'status_update' in gui_callbacks: gui_callbacks['status_update']("Hearing module not ready.")
    
    if telegram_bot_handler_instance: 
        # Get current status after potential start_polling and set in assistant_state
        assistant_state["telegram_bot_status"] = telegram_bot_handler_instance.get_status()
    else: # Set initial status in assistant_state if bot couldn't be initialized
        if not config.TELEGRAM_BOT_TOKEN: assistant_state["telegram_bot_status"] = "no_token"
        elif not config.TELEGRAM_ADMIN_USER_ID: assistant_state["telegram_bot_status"] = "no_admin"
        else: assistant_state["telegram_bot_status"] = "off" # If token/admin exists but instance is None (e.g. START_BOT_ON_APP_START=False)
    
    state_manager.save_states(chat_history, user_state, assistant_state, gui_callbacks)
    logger.info("--- Sequential model and services loading/checking thread finished ---")


# --- Main Application Execution ---
if __name__ == "__main__":
    logger.info("--- Main __name__ block started ---")

    try:
        if not os.path.exists(config.DATA_FOLDER): 
            os.makedirs(config.DATA_FOLDER)
            logger.info(f"Created folder: {config.DATA_FOLDER}")
        if not os.path.exists(config.OUTPUT_FOLDER):
            os.makedirs(config.OUTPUT_FOLDER)
            logger.info(f"Created folder: {config.OUTPUT_FOLDER}")
        if not os.path.exists(config.TELEGRAM_VOICE_TEMP_FOLDER): # Ensure this exists
            os.makedirs(config.TELEGRAM_VOICE_TEMP_FOLDER)
            logger.info(f"Created folder: {config.TELEGRAM_VOICE_TEMP_FOLDER}")
    except Exception as e_folder:
        logger.critical(f"CRITICAL: Failed to create data/output folders: {e_folder}. Exiting.", exc_info=True)
        sys.exit(1)

    logger.info("Loading initial states before GUI initialization...")
    try:
        chat_history, user_state, assistant_state = state_manager.load_initial_states(gui_callbacks=None)
        logger.info("Initial states loaded and defaults ensured.")
    except Exception as e_state:
        logger.critical(f"CRITICAL ERROR loading initial states: {e_state}", exc_info=True)
        sys.exit(1)

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

    logger.info("--- PRE-GUI INITIALIZATION COMPLETE ---") 

    logger.info("Attempting to initialize Tkinter root...")
    try:
        app_tk_instance = tk.Tk()
        logger.info("Tkinter root initialized.")
    except Exception as e_tk_root:
        logger.critical(f"CRITICAL ERROR initializing Tkinter root: {e_tk_root}", exc_info=True)
        sys.exit(1)

    logger.info("Attempting to initialize GUIManager...")
    action_callbacks_for_gui = { 
        'toggle_speaking_recording': toggle_speaking_recording, 
        'on_exit': on_app_exit, 
        'unload_bark_model': _unload_bark_model_action,
        'reload_bark_model': _reload_bark_model_action,
        'unload_whisper_model': _unload_whisper_model_action,
        'reload_whisper_model': _reload_whisper_model_action,
        'start_telegram_bot': _start_telegram_bot_action, 
        'stop_telegram_bot': _stop_telegram_bot_action,   
    }
    try:
        gui = GUIManager(app_tk_instance, action_callbacks_for_gui, 
                         initial_theme=current_gui_theme, 
                         initial_font_size=current_chat_font_size_applied)
        logger.info("GUIManager initialized successfully.")
    except Exception as e_gui:
        logger.critical(f"CRITICAL ERROR Failed to initialize GUIManager: {e_gui}", exc_info=True)
        if app_tk_instance:
            try: app_tk_instance.destroy() 
            except: pass 
        sys.exit(1) 

    logger.info("--- GUI INITIALIZATION COMPLETE ---") 

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

    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_ADMIN_USER_ID:
        logger.info("Token and Admin ID found. Initializing Telegram Bot Handler...")
        try:
            admin_id_int = int(config.TELEGRAM_ADMIN_USER_ID)
            telegram_bot_handler_instance = TelegramBotHandler(
                token=config.TELEGRAM_BOT_TOKEN,
                admin_user_id=admin_id_int,
                message_queue=telegram_message_queue, 
                gui_callbacks=gui_callbacks 
            )
            logger.info("TelegramBotHandler initialized.")
            # Set initial GUI status based on config.START_BOT_ON_APP_START, only if bot initialized
            if 'tele_status_update' in gui_callbacks and not config.START_BOT_ON_APP_START:
                 gui_callbacks['tele_status_update']("TELE: OFF", "off")
        except ValueError:
            logger.error(f"TELEGRAM_ADMIN_USER_ID '{config.TELEGRAM_ADMIN_USER_ID}' is not valid. Bot disabled.")
            if 'tele_status_update' in gui_callbacks: gui_callbacks['tele_status_update']("TELE: NO ADM", "no_admin")
            telegram_bot_handler_instance = None
        except Exception as e_tele_init: 
            logger.error(f"Failed to initialize TelegramBotHandler: {e_tele_init}", exc_info=True)
            if 'tele_status_update' in gui_callbacks: gui_callbacks['tele_status_update']("TELE: INITERR", "error") 
            telegram_bot_handler_instance = None
    else: 
        errmsg_tele = "Telegram Bot: "
        status_key_tele = "TELE: OFF"; status_type_tele = "off"
        if not config.TELEGRAM_BOT_TOKEN: errmsg_tele += "Token not set."; status_key_tele = "TELE: NO TOK"; status_type_tele = "no_token"
        if not config.TELEGRAM_ADMIN_USER_ID: 
            errmsg_tele += (" " if config.TELEGRAM_BOT_TOKEN else "") + "Admin User ID not set."
            if status_key_tele == "TELE: OFF": status_key_tele = "TELE: NO ADM"; status_type_tele = "no_admin" 
        logger.warning(f"{errmsg_tele} Telegram features will be disabled.")
        if 'tele_status_update' in gui_callbacks: gui_callbacks['tele_status_update'](status_key_tele, status_type_tele)
        telegram_bot_handler_instance = None 

    # Set initial assistant_state for telegram_bot_status based on actual bot status or config
    if telegram_bot_handler_instance: 
        assistant_state["telegram_bot_status"] = telegram_bot_handler_instance.get_status()
    else: # If bot instance couldn't be created
        if not config.TELEGRAM_BOT_TOKEN: assistant_state["telegram_bot_status"] = "no_token"
        elif not config.TELEGRAM_ADMIN_USER_ID: assistant_state["telegram_bot_status"] = "no_admin"
        else: assistant_state["telegram_bot_status"] = "off" # Token/admin ID exist but init failed for other reason or not started

    gui.update_chat_display_from_list(chat_history)
    logger.info("Initial chat history displayed on GUI.")
    if 'update_todo_list' in gui_callbacks: gui_callbacks['update_todo_list'](user_state.get("todos", []))
    if 'update_calendar_events_list' in gui_callbacks: gui_callbacks['update_calendar_events_list'](user_state.get("calendar_events", []))
    
    initial_assistant_tasks = assistant_state.get("internal_tasks", {})
    if not isinstance(initial_assistant_tasks, dict): 
        logger.warning(f"Initial 'internal_tasks' from state is not a dict: {initial_assistant_tasks}. Using empty.")
        initial_assistant_tasks = {} 
    if 'update_kanban_pending' in gui_callbacks: gui_callbacks['update_kanban_pending'](initial_assistant_tasks.get("pending", []))
    if 'update_kanban_in_process' in gui_callbacks: gui_callbacks['update_kanban_in_process'](initial_assistant_tasks.get("in_process", []))
    if 'update_kanban_completed' in gui_callbacks: gui_callbacks['update_kanban_completed'](initial_assistant_tasks.get("completed", []))
    logger.info("Initial User Info (Todos, Calendar) and Assistant Kanban populated on GUI.")

    logger.info("Initializing GPU Monitor...")
    if gpu_monitor.PYNVML_AVAILABLE:
        _active_gpu_monitor = gpu_monitor.get_gpu_monitor_instance(gui_callbacks=gui_callbacks, update_interval=2, gpu_index=0)
        if _active_gpu_monitor and _active_gpu_monitor.active: _active_gpu_monitor.start(); logger.info("GPU Monitor started.")
        elif _active_gpu_monitor and not _active_gpu_monitor.active: logger.warning("GPUMonitor initialized but not active.") 
    elif gui_callbacks and 'gpu_status_update_display' in gui_callbacks: 
        gui_callbacks['gpu_status_update_display']("N/A", "N/A", "na_nvml")

    logger.info("Starting model and services loader thread...")
    loader_thread = threading.Thread(target=load_all_models_and_services, daemon=True, name="ServicesLoaderThread")
    loader_thread.start()

    if app_tk_instance and app_tk_instance.winfo_exists():
        app_tk_instance.after(300, _process_queued_telegram_messages) 
        logger.info("Telegram message queue processor scheduled on main Tkinter thread.")

    logger.info("Starting Tkinter mainloop...")
    try:
        app_tk_instance.mainloop()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt detected by mainloop. Initiating shutdown.")
    except tk.TclError as e: 
        if "application has been destroyed" in str(e).lower():
            logger.info("Tkinter mainloop TclError: Application already destroyed (likely during normal shutdown).")
        else: 
            logger.error(f"Unhandled TclError in mainloop: {e}. Initiating shutdown.", exc_info=True)
    except Exception as e_mainloop: 
        logger.critical(f"Unexpected critical error in Tkinter mainloop: {e_mainloop}", exc_info=True)
    finally:
        logger.info("Mainloop exited or error occurred. Ensuring graceful shutdown via on_app_exit().")
        on_app_exit() 
        logger.info("Application main thread has finished.")