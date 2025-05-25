# utils/whisper_handler.py
import sys
import time
# from tkinter import messagebox # REMOVED
import os

# Assuming logger.py is in the same 'utils' directory
from logger import get_logger

logger = get_logger(__name__) # Gets the Iri-shka_App logger, __name__ will be utils.whisper_handler

WHISPER_AVAILABLE = False
whisper_model = None
whisper_model_ready = False
whisper_loading_in_progress = False
TORCH_CUDA_AVAILABLE = False
_torch_module = None

try:
    import whisper
    WHISPER_AVAILABLE = True
except ImportError:
    whisper = None
    logger.warning("Whisper library not found. Transcription will be disabled.")

if WHISPER_AVAILABLE:
    try:
        import torch
        _torch_module = torch
        if _torch_module.cuda.is_available():
            TORCH_CUDA_AVAILABLE = True
            logger.info("PyTorch CUDA is available. Whisper can use fp16 on GPU.")
        else:
            TORCH_CUDA_AVAILABLE = False
            logger.info("PyTorch CUDA not available. Whisper will use CPU (or fp16=False if GPU selected).")
    except ImportError:
        TORCH_CUDA_AVAILABLE = False
        _torch_module = None
        logger.warning("PyTorch not found. Whisper fp16 optimizations on CUDA will not be available.")

def _is_meta_device(model):
    if not _torch_module: return False
    if hasattr(model, 'device') and model.device is not None and model.device.type == 'meta':
        return True
    if hasattr(model, 'parameters'):
        try:
            return any(p is not None and hasattr(p, 'is_meta') and p.is_meta for p in model.parameters())
        except Exception as e:
            logger.debug(f"Exception checking for meta device parameters: {e}", exc_info=False)
            pass
    return False

def load_whisper_model(model_size, gui_callbacks=None):
    global whisper_model, whisper_model_ready, whisper_loading_in_progress

    if not WHISPER_AVAILABLE or not _torch_module:
        status_msg = "Whisper library not found." if not WHISPER_AVAILABLE else "PyTorch not found for Whisper."
        logger.warning(f"Cannot load Whisper model: {status_msg}")
        if gui_callbacks:
            if 'status_update' in gui_callbacks: gui_callbacks['status_update'](status_msg)
            if 'speak_button_update' in gui_callbacks: gui_callbacks['speak_button_update'](True, "Speak")
        whisper_loading_in_progress = False
        return

    whisper_loading_in_progress = True
    if gui_callbacks:
        if 'status_update' in gui_callbacks: gui_callbacks['status_update'](f"Initializing Whisper ({model_size})...")
        if 'speak_button_update' in gui_callbacks: gui_callbacks['speak_button_update'](False, "Loading...")

    try:
        logger.info(f"Loading Whisper model: {model_size}...")
        logger.info(f"Attempting to load model '{model_size}' to CPU initially.")
        loaded_model = whisper.load_model(model_size, device="cpu")
        logger.info(f"Model '{model_size}' loaded, initial device: {getattr(loaded_model, 'device', 'Unknown')}.")

        if _is_meta_device(loaded_model):
            logger.warning(f"Model or parameters still on 'meta' device after initial load. Forcing to CPU again.")
            loaded_model.to("cpu")
            if _is_meta_device(loaded_model):
                 logger.warning("Model remains on 'meta' device after forced CPU move.")

        current_model_for_use = loaded_model

        if TORCH_CUDA_AVAILABLE:
            logger.info(f"CUDA available. Attempting to move model '{model_size}' to CUDA.")
            if _is_meta_device(current_model_for_use):
                logger.error("Model is on 'meta' device before CUDA transfer attempt. Using CPU forcefully.")
                whisper_model = current_model_for_use.to("cpu")
            else:
                try:
                    gpu_model = current_model_for_use.to("cuda")
                    whisper_model = gpu_model
                    logger.info(f"Model '{model_size}' successfully moved to CUDA. Device: {getattr(whisper_model, 'device', 'Unknown')}")
                except Exception as e_gpu:
                    logger.error(f"Error moving model to GPU: {e_gpu}. Using CPU model.", exc_info=True)
                    whisper_model = current_model_for_use.to("cpu") # Fallback to CPU
        else:
            whisper_model = current_model_for_use.to("cpu") # Ensure it's on CPU if CUDA not available
            logger.info(f"Using model '{model_size}' on CPU. Device: {getattr(whisper_model, 'device', 'Unknown')}")

        if _is_meta_device(whisper_model):
             msg = "Whisper model parameters are on 'meta' device after all setup, transcription will likely fail."
             logger.critical(msg)
             if gui_callbacks and 'messagebox_error' in gui_callbacks:
                 gui_callbacks['messagebox_error']("Whisper Critical Error", msg)
        else:
            logger.info("Whisper model configured and device placement confirmed.")

        if gui_callbacks and 'status_update' in gui_callbacks:
            gui_callbacks['status_update']("Whisper model ready.")
        whisper_model_ready = True

    except Exception as e:
        error_msg_full = f"Error loading Whisper model ('{model_size}'): {e}"
        logger.error(error_msg_full, exc_info=True)
        if gui_callbacks:
            if 'messagebox_error' in gui_callbacks:
                 gui_callbacks['messagebox_error']("Whisper Load Error", f"Could not load Whisper: {str(e)[:150]}")
            if 'status_update' in gui_callbacks:
                 gui_callbacks['status_update']("Whisper load failed.")
        whisper_model = None
        whisper_model_ready = False
    finally:
        if gui_callbacks and 'speak_button_update' in gui_callbacks:
            if whisper_model_ready:
                 gui_callbacks['speak_button_update'](True, "Speak")
            else:
                 gui_callbacks['speak_button_update'](False, "HEAR NRDY")
        whisper_loading_in_progress = False

def transcribe_audio(audio_numpy_array, language=None, gui_callbacks=None):
    """
    Transcribes audio using the loaded Whisper model.
    :param audio_numpy_array: NumPy array of the audio data.
    :param language: Language code (e.g., "en", "ru") or None for auto-detection.
    :param gui_callbacks: Dictionary of GUI callback functions.
    :return: (transcribed_text, error_message, detected_language_code)
             detected_language_code is the code detected by Whisper (e.g., "en", "ru").
    """
    if not whisper_model_ready or whisper_model is None:
        err_msg = "Whisper model not ready for transcription."
        logger.warning(err_msg)
        if gui_callbacks and 'status_update' in gui_callbacks:
            gui_callbacks['status_update']("Transcription skipped: Whisper model not ready.")
        return None, err_msg, None

    if _is_meta_device(whisper_model):
        msg = "Whisper model is on meta device, transcription will fail."
        logger.error(msg)
        if gui_callbacks:
            if 'status_update' in gui_callbacks: gui_callbacks['status_update'](msg)
            if 'messagebox_error' in gui_callbacks: gui_callbacks['messagebox_error']("Whisper Error", msg)
        return None, msg, None

    try:
        if gui_callbacks and 'status_update' in gui_callbacks:
            gui_callbacks['status_update']("Transcribing (Whisper)...")
        logger.info("Starting transcription...")

        use_fp16 = False
        model_device_type = 'cpu'
        if hasattr(whisper_model, 'device') and whisper_model.device is not None:
            model_device_type = whisper_model.device.type

        if TORCH_CUDA_AVAILABLE and model_device_type == 'cuda':
            use_fp16 = True
            logger.info("Using fp16 for transcription on CUDA.")
        else:
            logger.info(f"Not using fp16 (model on {model_device_type}, CUDA available: {TORCH_CUDA_AVAILABLE}).")


        transcribe_options = {"fp16": use_fp16}
        if language: # If a language is forced, use it
            transcribe_options["language"] = language
            logger.info(f"Forcing language to: {language}")
        else: # Otherwise, let Whisper detect
            logger.info("Auto-detecting language.")

        result = whisper_model.transcribe(audio_numpy_array, **transcribe_options)
        transcribed_text = result["text"].strip()
        detected_language_code = result.get("language", "unknown") # Get detected language

        logger.info(f"Transcription: \"{transcribed_text}\" (Detected Lang: {detected_language_code})")
        if gui_callbacks and 'status_update' in gui_callbacks:
            gui_callbacks['status_update']("Transcription complete.")
        return transcribed_text, None, detected_language_code
    except RuntimeError as r_err: # Catch specific runtime errors that might be more informative
        if "meta" in str(r_err).lower():
            msg = f"Whisper transcription failed due to model likely on 'meta' device: {r_err}"
            logger.critical(msg, exc_info=True)
            if gui_callbacks:
                if 'status_update' in gui_callbacks: gui_callbacks['status_update']("Whisper: Meta device error.")
                if 'messagebox_error' in gui_callbacks: gui_callbacks['messagebox_error']("Whisper Critical Error", msg)
            return None, msg, None
        else:
            error_msg = f"Whisper transcription runtime error: {r_err}"
            logger.error(error_msg, exc_info=True)
            if gui_callbacks:
                if 'status_update' in gui_callbacks: gui_callbacks['status_update'](error_msg[:100])
                if 'messagebox_error' in gui_callbacks: gui_callbacks['messagebox_error']("Whisper Transcription Error", error_msg)
            return None, error_msg, None
    except Exception as e:
        error_msg = f"Whisper transcription error: {e}"
        logger.error(error_msg, exc_info=True)
        if gui_callbacks:
            if 'status_update' in gui_callbacks: gui_callbacks['status_update'](error_msg[:100])
            if 'messagebox_error' in gui_callbacks: gui_callbacks['messagebox_error']("Whisper Transcription Error", error_msg)
        return None, error_msg, None

def cleanup_whisper_model():
    global whisper_model, whisper_model_ready
    if WHISPER_AVAILABLE and whisper_model is not None:
        logger.info("Releasing Whisper model resources...")
        try:
            model_was_on_cuda = False
            if _torch_module is not None and hasattr(whisper_model, 'device') and \
               whisper_model.device and whisper_model.device.type == 'cuda':
                model_was_on_cuda = True

            if model_was_on_cuda:
                logger.info("Attempting to move Whisper model to CPU before deletion...")
                try:
                    whisper_model = whisper_model.cpu()
                    logger.info("Moved Whisper model to CPU.")
                except Exception as e_cpu_move:
                    logger.error(f"Error moving model to CPU during cleanup: {e_cpu_move}", exc_info=True)

            del whisper_model
            whisper_model = None

            import gc
            gc.collect()

            if model_was_on_cuda and _torch_module is not None and TORCH_CUDA_AVAILABLE and \
               hasattr(_torch_module.cuda, 'empty_cache'):
                _torch_module.cuda.empty_cache()
                logger.info("PyTorch CUDA cache cleared.")
            logger.info("Whisper model resources released.")
        except Exception as e:
            logger.error(f"Error during Whisper model cleanup: {e}", exc_info=True)

    whisper_model_ready = False
    whisper_model = None
    logger.info("Whisper model state reset.")