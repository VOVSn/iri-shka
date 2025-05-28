# utils/whisper_handler.py
import threading
import gc
import config
import numpy as np
import sys

# Assuming logger.py is in project root
from logger import get_logger
logger = get_logger("Iri-shka_App.utils.whisper_handler")

# --- Whisper Model Setup ---
_whisper_model = None
_whisper_device = None
whisper_model_ready = False
whisper_loading_in_progress = False
_whisper_load_error_message = None
WHISPER_CAPABLE = False

try:
    import torch
    if torch.cuda.is_available():
        logger.info("PyTorch CUDA is available. Whisper can use fp16 on GPU.")
        _whisper_device = "cuda"
    else:
        logger.info("PyTorch CUDA not available. Whisper will use CPU.")
        _whisper_device = "cpu"
    
    import whisper # The actual Whisper library by OpenAI
    WHISPER_CAPABLE = True
    logger.info("Whisper library imported successfully. Whisper features enabled.")
except ImportError as e:
    _whisper_load_error_message = f"Whisper library or PyTorch not found: {e}. Whisper features disabled."
    logger.warning(_whisper_load_error_message)
    WHISPER_CAPABLE = False
    _whisper_device = "cpu" # Default to CPU if torch import fails before this point
except Exception as e_init:
    _whisper_load_error_message = f"Unexpected error during Whisper/PyTorch import: {e_init}. Whisper features disabled."
    logger.critical(_whisper_load_error_message, exc_info=True)
    WHISPER_CAPABLE = False
    _whisper_device = "cpu"
# --- End Whisper Model Setup ---


def load_whisper_model(model_size=config.WHISPER_MODEL_SIZE, gui_callbacks=None):
    global _whisper_model, whisper_model_ready, whisper_loading_in_progress, _whisper_load_error_message, _whisper_device

    if not WHISPER_CAPABLE:
        final_err_msg = _whisper_load_error_message or "Whisper library not imported."
        logger.error(f"Cannot load Whisper model: {final_err_msg}")
        if gui_callbacks and callable(gui_callbacks.get('hearing_status_update')):
            gui_callbacks['hearing_status_update']("HEAR: N/A", "na")
        if gui_callbacks and callable(gui_callbacks.get('speak_button_update')):
            gui_callbacks['speak_button_update'](False, "HEAR N/A")
        return

    if whisper_model_ready:
        logger.info(f"Whisper model '{model_size}' already loaded.")
        if gui_callbacks and callable(gui_callbacks.get('hearing_status_update')):
            gui_callbacks['hearing_status_update']("HEAR: RDY", "ready")
        return
    if whisper_loading_in_progress:
        logger.info("Whisper model loading already in progress.")
        return

    whisper_loading_in_progress = True
    _whisper_load_error_message = None
    status_msg_gui = f"Loading Whisper ({model_size})..."
    logger.info(status_msg_gui)

    if gui_callbacks and callable(gui_callbacks.get('status_update')):
        gui_callbacks['status_update'](status_msg_gui)
    if gui_callbacks and callable(gui_callbacks.get('hearing_status_update')):
        gui_callbacks['hearing_status_update']("HEAR: LOAD", "loading")
    if gui_callbacks and callable(gui_callbacks.get('speak_button_update')):
        gui_callbacks['speak_button_update'](False, "Loading Hear...")

    try:
        # _whisper_device should be set during initial import attempt
        if not _whisper_device: # Safety check if it wasn't somehow
             _whisper_device = "cuda" if torch.cuda.is_available() else "cpu" # type: ignore
             logger.info(f"Re-confirmed Whisper device during load: {_whisper_device}")

        logger.info(f"Attempting to load Whisper model: {model_size} onto device: {_whisper_device}")
        _whisper_model = whisper.load_model(model_size, device=_whisper_device) # type: ignore
        whisper_model_ready = True
        success_msg = f"Whisper ready (Model: {model_size} on {_whisper_device})."
        logger.info(success_msg)
        if gui_callbacks and callable(gui_callbacks.get('status_update')):
            gui_callbacks['status_update']("Whisper model loaded.")
        if gui_callbacks and callable(gui_callbacks.get('hearing_status_update')):
            gui_callbacks['hearing_status_update']("HEAR: RDY", "ready")
        if gui_callbacks and callable(gui_callbacks.get('speak_button_update')):
             gui_callbacks['speak_button_update'](True, "Speak") # Assume TTS is handled elsewhere for button state
    except FileNotFoundError as fnf_err:
        _whisper_load_error_message = f"Whisper model files for '{model_size}' not found. Error: {fnf_err}"
        logger.error(_whisper_load_error_message, exc_info=False)
    except RuntimeError as rt_err:
        _whisper_load_error_message = f"RuntimeError loading Whisper model '{model_size}'. Error: {rt_err}"
        logger.error(_whisper_load_error_message, exc_info=True)
    except Exception as e:
        _whisper_load_error_message = f"Failed to load Whisper model '{model_size}': {e}"
        logger.critical(_whisper_load_error_message, exc_info=True)
    
    if not whisper_model_ready and gui_callbacks:
        err_gui_msg = _whisper_load_error_message or "Whisper load failed (unknown error)."
        if callable(gui_callbacks.get('status_update')):
            gui_callbacks['status_update'](f"Whisper Error: {err_gui_msg[:60]}...")
        if callable(gui_callbacks.get('hearing_status_update')):
            gui_callbacks['hearing_status_update']("HEAR: ERR", "error")
        if callable(gui_callbacks.get('messagebox_error')):
            gui_callbacks['messagebox_error']("Whisper Load Error", err_gui_msg)
        if callable(gui_callbacks.get('speak_button_update')):
            gui_callbacks['speak_button_update'](False, "HEAR ERR")
    whisper_loading_in_progress = False


def transcribe_audio(audio_np_array: np.ndarray, language=None, task="transcribe", gui_callbacks=None):
    """
    Transcribes audio using the loaded Whisper model.
    """
    if not whisper_model_ready or not _whisper_model:
        logger.error("Whisper model not ready for transcription.")
        return None, "Whisper model not loaded.", None
    if not isinstance(audio_np_array, np.ndarray):
        logger.error("Invalid audio data type for transcription (must be NumPy array).")
        return None, "Invalid audio data type.", None
    if audio_np_array.size == 0:
        logger.info("Empty audio array provided for transcription.")
        return "", None, None # No error, but empty text

    logger.info(f"Transcribing audio. Language: {language or 'auto-detect'}, Task: {task}. Input shape: {audio_np_array.shape}")
    if gui_callbacks and callable(gui_callbacks.get('status_update')):
        gui_callbacks['status_update'](f"Transcribing (Whisper)... Lang: {language or 'auto'}")

    transcribed_text = None
    error_msg = None
    detected_language_code = None

    try:
        if audio_np_array.dtype != np.float32:
            logger.warning(f"Audio array dtype is {audio_np_array.dtype}, converting to float32 for Whisper.")
            audio_np_array = audio_np_array.astype(np.float32)

        # Explicitly prepare arguments for the transcribe method
        # These are top-level arguments for whisper.model.Whisper.transcribe
        args_for_transcribe = {
            "audio": audio_np_array,
            "task": str(task),  # Ensure task is a string
            "fp16": (_whisper_device == "cuda") # Use fp16 only if on CUDA
        }
        if language is not None: # Only add 'language' if it's not None (for auto-detection)
            args_for_transcribe["language"] = str(language) # Ensure language is a string

        # Optional: Add other direct arguments supported by transcribe method if needed
        # args_for_transcribe["verbose"] = False
        # args_for_transcribe["temperature"] = 0.0 # For more deterministic output
        # args_for_transcribe["condition_on_previous_text"] = True # Default
        # args_for_transcribe["without_timestamps"] = False # Default

        log_args_display = {k:v for k,v in args_for_transcribe.items() if k != 'audio'} # Don't log the huge audio array
        logger.debug(f"Calling _whisper_model.transcribe() with direct arguments: {log_args_display}")
        
        result = _whisper_model.transcribe(**args_for_transcribe)

        transcribed_text = result.get("text", "").strip() # type: ignore
        detected_language_code = result.get("language", None) # type: ignore
        logger.info(f"Transcription result: '{transcribed_text[:70]}...', Detected lang: {detected_language_code}")

        if not transcribed_text:
            logger.info("Transcription resulted in empty text.")
            # error_msg = "No speech detected or recognized." # Caller can interpret empty text

    except TypeError as te:
        error_msg = f"TypeError during Whisper transcription (often 'unhashable type: dict'): {te}"
        logger.error(error_msg, exc_info=True)
        if gui_callbacks and callable(gui_callbacks.get('status_update')):
            gui_callbacks['status_update'](f"Tx Err (Type): {str(te)[:40]}")
    except RuntimeError as rt_err: # Catch runtime errors e.g. CUDA issues during transcribe
        error_msg = f"RuntimeError during Whisper transcription: {rt_err}"
        logger.error(error_msg, exc_info=True)
        if gui_callbacks and callable(gui_callbacks.get('status_update')):
            gui_callbacks['status_update'](f"Tx Err (Runtime): {str(rt_err)[:40]}")
    except Exception as e:
        error_msg = f"Unexpected error during Whisper transcription: {e}"
        logger.error(error_msg, exc_info=True)
        if gui_callbacks and callable(gui_callbacks.get('status_update')):
            gui_callbacks['status_update'](f"Tx Err (Gen): {str(e)[:50]}")
    
    if gui_callbacks and callable(gui_callbacks.get('status_update')):
        if error_msg:
            gui_callbacks['status_update'](f"Tx Err: {error_msg[:30]}")
        elif not transcribed_text:
            gui_callbacks['status_update'](f"Tx: Empty (Lang: {detected_language_code or 'N/A'})")
        else:
            # This might be too quick if transcription is fast and next status update overwrites it.
            # Usually, main logic handles "Ready" status after LLM.
            # gui_callbacks['status_update'](f"Tx OK (Lang: {detected_language_code or 'N/A'})")
            pass # Let the calling function in main.py set the next overall status

    return transcribed_text, error_msg, detected_language_code


def unload_whisper_model(gui_callbacks=None):
    global _whisper_model, whisper_model_ready, whisper_loading_in_progress, _whisper_load_error_message
    logger.info("Unloading Whisper model...")

    if whisper_loading_in_progress:
        logger.warning("Cannot unload Whisper model: loading is currently in progress.")
        return

    if _whisper_model:
        del _whisper_model
        _whisper_model = None
        gc.collect()
        if _whisper_device == "cuda" and torch.cuda.is_available(): # type: ignore
            try:
                torch.cuda.empty_cache() # type: ignore
                logger.info("PyTorch CUDA cache cleared after Whisper model unload.")
            except Exception as e_cuda_clear:
                logger.warning(f"Could not clear CUDA cache: {e_cuda_clear}")
    
    whisper_model_ready = False
    _whisper_load_error_message = None
    logger.info("Whisper model unloaded.")

    if gui_callbacks:
        if callable(gui_callbacks.get('status_update')):
            gui_callbacks['status_update']("Whisper model unloaded.")
        if callable(gui_callbacks.get('hearing_status_update')):
            gui_callbacks['hearing_status_update']("HEAR: OFF", "off")
        if callable(gui_callbacks.get('speak_button_update')):
            gui_callbacks['speak_button_update'](False, "HEAR OFF")


def full_shutdown_whisper_module():
    logger.info("Full Whisper module shutdown for application exit.")
    unload_whisper_model()
    logger.info("Whisper module shutdown sequence complete.")

def is_whisper_ready():
    return WHISPER_CAPABLE and whisper_model_ready and not whisper_loading_in_progress

def get_status_short() -> str:
    if not WHISPER_CAPABLE: return "N/A"
    if whisper_loading_in_progress: return "LOAD"
    if whisper_model_ready: return "RDY"
    if _whisper_load_error_message: return "ERR"
    return "OFF"

def get_status_type() -> str:
    if not WHISPER_CAPABLE: return "na"
    if whisper_loading_in_progress: return "loading"
    if whisper_model_ready: return "ready"
    if _whisper_load_error_message: return "error"
    return "off"