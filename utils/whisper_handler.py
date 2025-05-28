# utils/whisper_handler.py
import threading
import gc
import config
import numpy as np # Keep numpy import for potential type hints or future use
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

# Attempt to import Whisper and determine capability
try:
    import torch # Whisper depends on PyTorch
    # Check for CUDA availability early, as Whisper can leverage it
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
except Exception as e_init: # Catch any other unexpected errors during import/init
    _whisper_load_error_message = f"Unexpected error during Whisper/PyTorch import: {e_init}. Whisper features disabled."
    logger.critical(_whisper_load_error_message, exc_info=True)
    WHISPER_CAPABLE = False
# --- End Whisper Model Setup ---


def load_whisper_model(model_size=config.WHISPER_MODEL_SIZE, gui_callbacks=None):
    global _whisper_model, whisper_model_ready, whisper_loading_in_progress, _whisper_load_error_message, _whisper_device

    if not WHISPER_CAPABLE:
        logger.error(f"Cannot load Whisper model: Whisper library not capable or not imported. Error: {_whisper_load_error_message or 'Unknown import error'}")
        if gui_callbacks and callable(gui_callbacks.get('hearing_status_update')):
            gui_callbacks['hearing_status_update']("HEAR: N/A", "na")
        if gui_callbacks and callable(gui_callbacks.get('speak_button_update')): # Also update speak button if hearing is N/A
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
    _whisper_load_error_message = None # Reset error message
    status_msg_gui = f"Loading Whisper ({model_size})..."
    logger.info(status_msg_gui)

    if gui_callbacks and callable(gui_callbacks.get('status_update')):
        gui_callbacks['status_update'](status_msg_gui)
    if gui_callbacks and callable(gui_callbacks.get('hearing_status_update')):
        gui_callbacks['hearing_status_update']("HEAR: LOAD", "loading")
    if gui_callbacks and callable(gui_callbacks.get('speak_button_update')):
        gui_callbacks['speak_button_update'](False, "Loading Hear...")

    try:
        if not _whisper_device: # Should have been set during import check
             _whisper_device = "cuda" if torch.cuda.is_available() else "cpu"
             logger.info(f"Re-checked Whisper device: {_whisper_device}")

        logger.info(f"Attempting to load Whisper model: {model_size} onto device: {_whisper_device}")
        _whisper_model = whisper.load_model(model_size, device=_whisper_device) # type: ignore
        whisper_model_ready = True
        success_msg = f"Whisper ready (Model: {model_size} on {_whisper_device})."
        logger.info(success_msg)
        if gui_callbacks and callable(gui_callbacks.get('status_update')):
            gui_callbacks['status_update']("Whisper model loaded.") # More concise for main status
        if gui_callbacks and callable(gui_callbacks.get('hearing_status_update')):
            gui_callbacks['hearing_status_update']("HEAR: RDY", "ready")
        if gui_callbacks and callable(gui_callbacks.get('speak_button_update')):
            # Check TTS status as well before enabling speak button fully
            # For now, just indicate hearing is ready
             is_tts_also_ready = True # Placeholder, ideally check tts_manager.is_tts_ready()
             # This callback is primarily about Whisper's readiness for the button's 'hear' aspect
             gui_callbacks['speak_button_update'](True, "Speak")


    except FileNotFoundError as fnf_err: # For model files not found
        _whisper_load_error_message = f"Whisper model files for '{model_size}' not found. Have you downloaded them (e.g., to ~/.cache/whisper)? Error: {fnf_err}"
        logger.error(_whisper_load_error_message, exc_info=False)
    except RuntimeError as rt_err: # Often CUDA errors or other runtime issues
        _whisper_load_error_message = f"RuntimeError loading Whisper model '{model_size}'. If using CUDA, check VRAM. Error: {rt_err}"
        logger.error(_whisper_load_error_message, exc_info=True)
    except Exception as e:
        _whisper_load_error_message = f"Failed to load Whisper model '{model_size}': {e}"
        logger.critical(_whisper_load_error_message, exc_info=True)
    
    if not whisper_model_ready and gui_callbacks: # If loading failed
        err_gui_msg = _whisper_load_error_message or "Whisper load failed."
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
    Args:
        audio_np_array: NumPy array of audio data (float32, normalized to -1.0 to 1.0).
        language: Optional language code (e.g., 'en', 'ru'). If None, Whisper detects.
        task: "transcribe" or "translate" (to English).
        gui_callbacks: For status updates.
    Returns:
        tuple: (transcribed_text, error_message, detected_language_code)
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

    logger.info(f"Transcribing audio. Language: {language or 'auto-detect'}, Task: {task}. Input array shape: {audio_np_array.shape}")
    if gui_callbacks and callable(gui_callbacks.get('status_update')):
        gui_callbacks['status_update'](f"Transcribing (Whisper)... Lang: {language or 'auto'}")

    transcribed_text = None
    error_msg = None
    detected_language_code = None

    try:
        # Whisper expects float32. Ensure it's correctly formatted.
        # Normalization to [-1.0, 1.0] should have happened before this point.
        if audio_np_array.dtype != np.float32:
            logger.warning(f"Audio array dtype is {audio_np_array.dtype}, converting to float32 for Whisper.")
            audio_np_array = audio_np_array.astype(np.float32)

        # Set fp16 based on device, only for GPU. For CPU, fp16 can be slower or unsupported.
        use_fp16 = True if _whisper_device == "cuda" else False
        
        transcribe_options = {"task": task, "fp16": use_fp16}
        if language:
            transcribe_options["language"] = language
        
        # Make sure whisper object is the imported module for TranscribeOptions
        # This is a bit verbose, but ensures we are using the correct way to pass options if needed
        # For basic use, just passing dict is often fine.
        # options = whisper.DecodingOptions(**transcribe_options) # type: ignore
        # result = _whisper_model.transcribe(audio_np_array, **transcribe_options) # Simpler
        
        # Using the simpler dict spread for options:
        result = _whisper_model.transcribe(audio_np_array, **transcribe_options) # type: ignore

        transcribed_text = result.get("text", "").strip() # type: ignore
        detected_language_code = result.get("language", None) # type: ignore
        logger.info(f"Transcription result: '{transcribed_text[:70]}...', Detected lang: {detected_language_code}")

        if not transcribed_text: # If text is empty after strip
            logger.info("Transcription resulted in empty text.")
            # error_msg = "No speech detected or recognized." # Let main.py handle empty text interpretation

    except Exception as e:
        error_msg = f"Error during Whisper transcription: {e}"
        logger.error(error_msg, exc_info=True)
        if gui_callbacks and callable(gui_callbacks.get('status_update')):
            gui_callbacks['status_update'](f"Transcription Error: {str(e)[:50]}...")
    
    # Final GUI update for status (if transcription takes noticeable time)
    if gui_callbacks and callable(gui_callbacks.get('status_update')):
        if error_msg:
            gui_callbacks['status_update'](f"Tx Error: {error_msg[:30]}")
        elif not transcribed_text:
            gui_callbacks['status_update'](f"Tx: Empty (Lang: {detected_language_code or 'N/A'})")
        else:
            gui_callbacks['status_update'](f"Tx OK (Lang: {detected_language_code or 'N/A'})")


    return transcribed_text, error_msg, detected_language_code


def unload_whisper_model(gui_callbacks=None):
    global _whisper_model, whisper_model_ready, whisper_loading_in_progress, _whisper_load_error_message
    logger.info("Unloading Whisper model...")

    if whisper_loading_in_progress:
        logger.warning("Cannot unload Whisper model: loading is currently in progress.")
        # Optionally, signal the loading thread to stop if such a mechanism exists
        return

    if _whisper_model:
        del _whisper_model
        _whisper_model = None
        gc.collect() # Hint for garbage collection
        if _whisper_device == "cuda" and torch.cuda.is_available():
            try:
                torch.cuda.empty_cache()
                logger.info("PyTorch CUDA cache cleared after Whisper model unload.")
            except Exception as e_cuda_clear:
                logger.warning(f"Could not clear CUDA cache: {e_cuda_clear}")
    
    whisper_model_ready = False
    _whisper_load_error_message = None # Clear any previous load error
    logger.info("Whisper model unloaded.")

    if gui_callbacks:
        if callable(gui_callbacks.get('status_update')):
            gui_callbacks['status_update']("Whisper model unloaded.")
        if callable(gui_callbacks.get('hearing_status_update')):
            gui_callbacks['hearing_status_update']("HEAR: OFF", "off")
        # Update speak button based on Whisper being off (TTS might still be on)
        # This logic should be more centralized in main.py before updating button.
        # For now, just indicate hearing is off.
        if callable(gui_callbacks.get('speak_button_update')):
            gui_callbacks['speak_button_update'](False, "HEAR OFF")


def full_shutdown_whisper_module():
    logger.info("Full Whisper module shutdown for application exit.")
    unload_whisper_model() # Handles model resource release
    # No other specific module-level resources for Whisper to clean up beyond the model itself
    logger.info("Whisper module shutdown sequence complete.")

def is_whisper_ready():
    """Combined check for capability and model readiness."""
    return WHISPER_CAPABLE and whisper_model_ready and not whisper_loading_in_progress

# --- New Status Functions ---
def get_status_short() -> str:
    """Returns a short status string for Whisper."""
    if not WHISPER_CAPABLE:
        return "N/A"
    if whisper_loading_in_progress:
        return "LOAD"
    if whisper_model_ready:
        return "RDY"
    if _whisper_load_error_message: # If there was a specific load error message
        return "ERR"
    return "OFF" # Default if not loading, not ready, and no specific error

def get_status_type() -> str:
    """Returns a status category string for Whisper (for GUI coloring)."""
    if not WHISPER_CAPABLE:
        return "na"
    if whisper_loading_in_progress:
        return "loading"
    if whisper_model_ready:
        return "ready"
    if _whisper_load_error_message: # If there was a specific load error message
        return "error"
    return "off"