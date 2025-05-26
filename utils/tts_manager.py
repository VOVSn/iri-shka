# utils/tts_manager.py
import threading
import time
import sys
import os # Make sure os is imported
import config

# Assuming logger.py is in the same 'utils' directory
from logger import get_logger

logger = get_logger(__name__)

_bark_imports_ok = False
_bark_import_error_message = ""
BarkTTS_class = None
StreamingBarkTTS_class = None
sounddevice_bark_module = None
torch_module = None
AutoProcessor_class = None
BarkModel_class = None

# --- Define the path to your local Bark models within the project ---
# Assuming your project structure is something like:
# project_root/
# |-- utils/
# |   |-- tts_manager.py
# |   |-- speak_bark.py
# |   |-- logger.py
# |   |-- config.py (potentially)
# |-- bark/  <-- YOUR BARK MODEL SNAPSHOT FILES ARE HERE
# |   |-- config.json
# |   |-- pytorch_model.bin
# |   |-- ... other model files ...
# |-- main_app.py

# Calculate the path relative to this file (tts_manager.py)
# This file is in utils/, so ../ goes to project_root/
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EXPLICIT_BARK_LOCAL_PATH = os.path.join(PROJECT_ROOT, "bark")
logger.info(f"Calculated explicit local Bark model path: {EXPLICIT_BARK_LOCAL_PATH}")
# --- End Local Model Path Definition ---


try:
    from utils.speak_bark import BarkTTS, StreamingBarkTTS, sd as speak_bark_sd_module
    import torch
    from transformers import AutoProcessor, BarkModel

    BarkTTS_class = BarkTTS
    StreamingBarkTTS_class = StreamingBarkTTS
    sounddevice_bark_module = speak_bark_sd_module
    torch_module = torch
    AutoProcessor_class = AutoProcessor
    BarkModel_class = BarkModel

    if sounddevice_bark_module is None:
        raise ImportError("sounddevice failed to load within speak_bark.py or is not installed.")
    _bark_imports_ok = True
    logger.info("Bark TTS dependencies imported successfully.")
except ImportError as e:
    _bark_import_error_message = f"Bark TTS critical import failed: {e}"
    logger.error(_bark_import_error_message, exc_info=True)
    BarkTTS_class, StreamingBarkTTS_class = None, None
    torch_module, AutoProcessor_class, BarkModel_class = None, None, None
    sounddevice_bark_module = None
    _bark_imports_ok = False
except Exception as e:
    _bark_import_error_message = f"Unexpected error during Bark TTS dependency imports: {e}"
    logger.critical(_bark_import_error_message, exc_info=True)
    BarkTTS_class, StreamingBarkTTS_class = None, None
    torch_module, AutoProcessor_class, BarkModel_class = None, None, None
    sounddevice_bark_module = None
    _bark_imports_ok = False


TTS_AVAILABLE = _bark_imports_ok

_bark_processor = None
_bark_model = None
_bark_device = None
_bark_resources_ready = False
_bark_loading_in_progress = False
_bark_load_error_msg = None

current_tts_thread = None
tts_stop_event = None


def is_tts_ready():
    return TTS_AVAILABLE and _bark_resources_ready and not _bark_loading_in_progress

def is_tts_loading():
    return TTS_AVAILABLE and _bark_loading_in_progress

def load_bark_resources(gui_callbacks=None):
    global _bark_processor, _bark_model, _bark_device, _bark_resources_ready
    global _bark_loading_in_progress, _bark_load_error_msg, TTS_AVAILABLE

    # --- Key Offline Settings ---
    os.environ["HF_HUB_OFFLINE"] = "1"
    logger.info(f"Ensured HF_HUB_OFFLINE is set to '1'.")

    model_load_path = config.BARK_MODEL_NAME # Fallback, e.g., "suno/bark-small"

    if os.path.isdir(EXPLICIT_BARK_LOCAL_PATH):
        logger.info(f"Using explicit local path for Bark model: {EXPLICIT_BARK_LOCAL_PATH}")
        model_load_path = EXPLICIT_BARK_LOCAL_PATH
    else:
        logger.warning(f"Explicit local path '{EXPLICIT_BARK_LOCAL_PATH}' for Bark model NOT FOUND or is not a directory.")
        logger.warning(f"Will attempt to load '{config.BARK_MODEL_NAME}' from Hugging Face cache (if available offline).")
        # If EXPLICIT_BARK_LOCAL_PATH was critical, you might want to set TTS_AVAILABLE = False here
        # and return, or raise an error. For now, it will try the HF cache path.
    # --- End Key Offline Settings ---


    if not TTS_AVAILABLE: # Check if imports themselves failed
        final_import_err_msg = _bark_import_error_message or "speak_bark.py or its dependencies not correctly imported."
        logger.error(f"Cannot load Bark resources. Import failure: {final_import_err_msg}")
        _bark_load_error_msg = final_import_err_msg
        if gui_callbacks and 'status_update' in gui_callbacks:
            gui_callbacks['status_update'](f"Bark TTS unavailable: {final_import_err_msg[:60]}...")
        # No messagebox here as it's an import issue, not a load attempt failure.
        return

    if _bark_resources_ready:
        logger.info("Bark resources already loaded.")
        return
    if _bark_loading_in_progress:
        logger.info("Bark resource loading already in progress. Ignoring new request.")
        return

    _bark_loading_in_progress = True
    _bark_resources_ready = False
    _bark_load_error_msg = None

    status_msg_gui = f"Initializing Bark TTS ({os.path.basename(str(model_load_path))})..."
    logger.info(f"Loading Bark resources from: '{model_load_path}'.")
    if gui_callbacks and 'status_update' in gui_callbacks:
        gui_callbacks['status_update'](status_msg_gui)

    try:
        if not torch_module or not AutoProcessor_class or not BarkModel_class:
            raise RuntimeError("Core PyTorch/Transformers modules for Bark are not available.")

        _bark_device = "cuda" if torch_module.cuda.is_available() else "cpu"
        logger.info(f"Target device for Bark model: {_bark_device}")

        logger.info(f"Loading Bark processor from '{model_load_path}'...")
        _bark_processor = AutoProcessor_class.from_pretrained(
            model_load_path,
            # local_files_only=True # Could also be used if HF_HUB_OFFLINE wasn't enough, but HF_HUB_OFFLINE should suffice.
        )

        logger.info(f"Loading Bark model from '{model_load_path}' to {_bark_device}...")
        _bark_model = BarkModel_class.from_pretrained(
            model_load_path,
            # local_files_only=True
        )
        _bark_model.to(_bark_device)

        _bark_resources_ready = True
        success_msg = f"Bark TTS ready (Model: {os.path.basename(str(model_load_path))} on {_bark_device})."
        logger.info(success_msg)
        if gui_callbacks and 'status_update' in gui_callbacks:
            gui_callbacks['status_update'](success_msg)

    except Exception as e:
        _bark_load_error_msg = f"Failed to load Bark resources from '{model_load_path}': {e}"
        logger.critical(_bark_load_error_msg, exc_info=True)
        TTS_AVAILABLE = False # Critical failure, TTS is not usable
        _bark_resources_ready = False # Ensure this is false
        if gui_callbacks:
            if 'status_update' in gui_callbacks:
                gui_callbacks['status_update'](f"Bark TTS Error: {_bark_load_error_msg[:60]}...")
            if 'messagebox_error' in gui_callbacks:
                gui_callbacks['messagebox_error']("Bark TTS Load Error", _bark_load_error_msg)
    finally:
        _bark_loading_in_progress = False


def start_speaking_response(text_to_speak, persona_name_llm, target_voice_preset,
                            gui_callbacks=None, on_actual_playback_start_gui_callback=None):
    global current_tts_thread, tts_stop_event

    if not is_tts_ready():
        errmsg = "TTS not ready."
        if _bark_loading_in_progress: errmsg = "TTS resources still loading."
        elif _bark_load_error_msg: errmsg = f"TTS load failed: {_bark_load_error_msg[:50]}..."
        elif not TTS_AVAILABLE: # This means either import or load failed critically
            errmsg = _bark_import_error_message or _bark_load_error_msg or "TTS module unavailable or failed to load."
            errmsg = f"TTS unavailable: {errmsg[:50]}..."

        logger.warning(f"Cannot speak. {errmsg} Text: '{text_to_speak[:50]}...'")
        if gui_callbacks and 'status_update' in gui_callbacks:
            gui_callbacks['status_update'](f"Cannot speak: {errmsg}")

        if on_actual_playback_start_gui_callback:
             logger.debug(f"TTS not ready, invoking on_actual_playback_start_gui_callback as fallback for text display.")
             try:
                 on_actual_playback_start_gui_callback()
             except Exception as cb_exc:
                 logger.error(f"Error in fallback on_actual_playback_start_gui_callback: {cb_exc}", exc_info=True)
        return

    if current_tts_thread and current_tts_thread.is_alive():
        logger.info(f"Previous speech thread '{current_tts_thread.name}' active. Signaling stop...")
        if tts_stop_event:
            tts_stop_event.set()
        else:
            logger.warning(f"tts_stop_event is None for alive thread '{current_tts_thread.name}'. This is unexpected.")
        current_tts_thread.join(timeout=2.0)
        if current_tts_thread.is_alive():
            logger.warning(f"Previous speech thread '{current_tts_thread.name}' did not stop cleanly.")
            if gui_callbacks and 'status_update' in gui_callbacks:
                 gui_callbacks['status_update']("TTS: Previous speech did not stop quickly.")
        else:
            logger.info(f"Previous speech thread '{current_tts_thread.name}' stopped.")


    tts_stop_event = threading.Event()

    try:
        if not BarkTTS_class or not StreamingBarkTTS_class:
            raise RuntimeError("BarkTTS or StreamingBarkTTS class not available.")

        bark_tts_instance = BarkTTS_class(
            processor=_bark_processor,
            model=_bark_model,
            device=_bark_device,
            voice_preset=target_voice_preset
        )
        streamer = StreamingBarkTTS_class(
            bark_tts_instance=bark_tts_instance,
            # max_sentences_per_chunk and silence_duration_ms will be taken from speak_bark.py defaults or config
        )

        generation_params = {
            # These will be taken from config inside speak_bark.py's BarkTTS.synthesize_speech_to_array
            # "do_sample": config.BARK_DO_SAMPLE,
            # "fine_temperature": config.BARK_FINE_TEMPERATURE,
            # "coarse_temperature": config.BARK_COARSE_TEMPERATURE
        }

        thread_name = f"BarkTTSStream-{time.strftime('%H%M%S')}"
        current_tts_thread = threading.Thread(
            target=streamer.synthesize_and_play_stream,
            args=(text_to_speak, tts_stop_event, generation_params), # Pass empty dict if no specific overrides
            kwargs={"on_playback_start_callback": on_actual_playback_start_gui_callback},
            name=thread_name,
            daemon=True
        )
        current_tts_thread.start()
        logger.info(f"Started Bark speech thread '{current_tts_thread.name}' for LLM persona '{persona_name_llm}' with voice '{target_voice_preset}'. Text: '{text_to_speak[:70]}...'")
        if gui_callbacks and 'status_update' in gui_callbacks:
             gui_callbacks['status_update'](f"TTS initiated for: {text_to_speak[:40]}...")

    except Exception as e:
        errmsg = f"Error starting Bark speech: {e}"
        logger.error(errmsg, exc_info=True)
        if gui_callbacks:
            if 'messagebox_error' in gui_callbacks:
                gui_callbacks['messagebox_error']("Bark TTS Error", errmsg)
            if 'status_update' in gui_callbacks:
                gui_callbacks['status_update'](f"Bark speech error: {str(e)[:50]}...")
        if on_actual_playback_start_gui_callback:
            logger.debug(f"TTS error during start, invoking on_actual_playback_start_gui_callback as fallback.")
            try: on_actual_playback_start_gui_callback()
            except Exception as cb_exc: logger.error(f"Error in fallback on_playback_start_gui_callback: {cb_exc}", exc_info=True)


def stop_current_speech(gui_callbacks=None):
    global current_tts_thread, tts_stop_event

    if not TTS_AVAILABLE:
        return

    if current_tts_thread and current_tts_thread.is_alive():
        thread_name = current_tts_thread.name
        logger.info(f"Attempting to stop ongoing Bark speech on thread '{thread_name}'...")
        if tts_stop_event:
            tts_stop_event.set()
        else:
            logger.warning(f"tts_stop_event is None for alive thread '{thread_name}'. Cannot signal stop effectively.")

        current_tts_thread.join(timeout=2.5)

        if not current_tts_thread.is_alive():
            logger.info(f"Bark speech thread '{thread_name}' stopped or finished.")
            if gui_callbacks and 'status_update' in gui_callbacks:
                gui_callbacks['status_update']("Speech interrupted.")
        else:
            logger.warning(f"Bark speech thread '{thread_name}' did not stop/join quickly.")
            if gui_callbacks and 'status_update' in gui_callbacks:
                gui_callbacks['status_update']("Speech interruption slow/failed.")
    else:
        logger.debug("No active Bark speech thread to stop.")


def shutdown_tts():
    global _bark_processor, _bark_model, _bark_device, _bark_resources_ready
    global _bark_loading_in_progress, current_tts_thread, TTS_AVAILABLE

    logger.info("Shutdown sequence initiated for Bark TTS.")

    if current_tts_thread and current_tts_thread.is_alive():
        logger.info("Stopping active speech thread during TTS shutdown...")
        stop_current_speech()

    if _bark_model:
        logger.info("Releasing Bark model resources...")
        try:
            if torch_module and _bark_device == "cuda" and hasattr(_bark_model, 'cpu'):
                _bark_model = _bark_model.cpu()
                logger.info("Moved Bark model to CPU.")

            del _bark_model
            _bark_model = None
            if _bark_processor:
                del _bark_processor
                _bark_processor = None

            if torch_module and _bark_device == "cuda" and hasattr(torch_module.cuda, 'empty_cache'):
                 torch_module.cuda.empty_cache()
                 logger.info("PyTorch CUDA cache cleared.")
            logger.info("Bark model and processor resources released.")
        except Exception as e:
            logger.error(f"Error during Bark model cleanup: {e}", exc_info=True)

    _bark_resources_ready = False
    _bark_loading_in_progress = False
    # TTS_AVAILABLE reflects the initial import status unless load_bark_resources critically failed.
    logger.info("Bark TTS shutdown complete.")