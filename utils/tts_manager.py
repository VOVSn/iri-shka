# utils/tts_manager.py
import threading
import time
import sys
import os
import config
import gc
import numpy as np # For BarkTTS to return array

# Assuming logger.py is in project root
from logger import get_logger

logger = get_logger("Iri-shka_App.utils.tts_manager")

_bark_imports_ok = False
_bark_import_error_message = ""
BarkTTS_class = None
StreamingBarkTTS_class = None
sounddevice_bark_module = None
torch_module = None # For type hinting and direct use if needed
AutoProcessor_class = None
BarkModel_class = None

TTS_CAPABLE = False # Initialize to False

try:
    # speak_bark.py should handle its own imports of torch, transformers, sounddevice
    from utils.speak_bark import BarkTTS, StreamingBarkTTS, sd as speak_bark_sd_module

    # We might still need torch and transformers here for type hinting or direct use
    import torch
    from transformers import AutoProcessor, BarkModel

    BarkTTS_class = BarkTTS
    StreamingBarkTTS_class = StreamingBarkTTS
    sounddevice_bark_module = speak_bark_sd_module
    torch_module = torch
    AutoProcessor_class = AutoProcessor
    BarkModel_class = BarkModel

    if sounddevice_bark_module is None:
        _bark_import_error_message = "SoundDevice failed to load within speak_bark.py or is not installed."
        raise ImportError(_bark_import_error_message)

    _bark_imports_ok = True
    TTS_CAPABLE = True # Set capability flag
    logger.info("Bark TTS dependencies (via speak_bark and direct) imported successfully.")
except ImportError as e:
    _bark_import_error_message = f"Bark TTS critical import failed: {e}"
    logger.error(_bark_import_error_message, exc_info=True)
    # Ensure all are None if import fails
    BarkTTS_class, StreamingBarkTTS_class = None, None
    sounddevice_bark_module, torch_module = None, None
    AutoProcessor_class, BarkModel_class = None, None
    TTS_CAPABLE = False
except Exception as e_tts_init:
    _bark_import_error_message = f"Unexpected error during Bark TTS dependency imports: {e_tts_init}"
    logger.critical(_bark_import_error_message, exc_info=True)
    BarkTTS_class, StreamingBarkTTS_class = None, None
    sounddevice_bark_module, torch_module = None, None
    AutoProcessor_class, BarkModel_class = None, None
    TTS_CAPABLE = False


_bark_processor_instance = None
_bark_model_instance = None
_bark_device_str = None
_bark_resources_ready = False
_bark_loading_in_progress = False
_bark_load_error_msg = None # Stores specific error from last load attempt

current_tts_thread: threading.Thread = None # type: ignore
tts_stop_event: threading.Event = None # type: ignore


def is_tts_ready():
    """Checks if TTS is capable, resources are loaded, and not currently loading."""
    return TTS_CAPABLE and _bark_resources_ready and not _bark_loading_in_progress

def is_tts_loading():
    """Checks if TTS is capable and resources are currently being loaded."""
    return TTS_CAPABLE and _bark_loading_in_progress

def get_bark_model_instance():
    """
    Returns a BarkTTS instance if resources are ready, or None.
    This uses the globally loaded model and processor for direct synthesis.
    """
    if is_tts_ready() and BarkTTS_class and _bark_processor_instance and _bark_model_instance and _bark_device_str:
        try:
            # Create a new BarkTTS wrapper instance for this specific synthesis task
            return BarkTTS_class(
                processor=_bark_processor_instance,
                model=_bark_model_instance,
                device=_bark_device_str
                # voice_preset is passed during synthesize_speech_to_array
            )
        except Exception as e:
            logger.error(f"Error creating BarkTTS instance in get_bark_model_instance: {e}", exc_info=True)
            return None
    logger.debug("get_bark_model_instance: TTS resources not ready or core components missing.")
    return None


def load_bark_resources(gui_callbacks=None):
    global _bark_processor_instance, _bark_model_instance, _bark_device_str, _bark_resources_ready
    global _bark_loading_in_progress, _bark_load_error_msg

    if not TTS_CAPABLE:
        final_import_err_msg = _bark_import_error_message or "Bark TTS module (speak_bark.py or dependencies) not imported."
        logger.error(f"Cannot load Bark resources. Import failure: {final_import_err_msg}")
        _bark_load_error_msg = final_import_err_msg # Store for status
        if gui_callbacks and callable(gui_callbacks.get('status_update')):
            gui_callbacks['status_update'](f"Bark TTS unavailable: {final_import_err_msg[:60]}...")
        if gui_callbacks and callable(gui_callbacks.get('voice_status_update')):
            gui_callbacks['voice_status_update']("VOICE: N/A", "na")
        return

    if _bark_resources_ready:
        logger.info("Bark resources already loaded.")
        if gui_callbacks and callable(gui_callbacks.get('voice_status_update')):
             gui_callbacks['voice_status_update']("VOICE: RDY", "ready")
        return
    if _bark_loading_in_progress:
        logger.info("Bark resource loading already in progress. Ignoring new request.")
        return

    _bark_loading_in_progress = True
    _bark_resources_ready = False
    _bark_load_error_msg = None # Reset error message for new load attempt
    os.environ["HF_HUB_OFFLINE"] = "1" # Attempt to force offline for cached/local models

    model_load_path = config.BARK_MODEL_NAME
    is_local_path = model_load_path.startswith("./") or os.path.isabs(model_load_path)
    if is_local_path and not os.path.isdir(model_load_path):
        _bark_load_error_msg = f"Local Bark model path '{model_load_path}' NOT FOUND."
        logger.error(_bark_load_error_msg)
        if gui_callbacks:
            if callable(gui_callbacks.get('status_update')): gui_callbacks['status_update'](f"Bark TTS Error: {_bark_load_error_msg[:60]}...")
            if callable(gui_callbacks.get('voice_status_update')): gui_callbacks['voice_status_update']("VOICE: ERR", "error") # Changed from NRDY to ERR
        _bark_loading_in_progress = False
        return

    status_msg_gui = f"Initializing Bark TTS ({os.path.basename(str(model_load_path))})..."
    logger.info(f"Loading Bark resources from: '{model_load_path}'.")
    if gui_callbacks:
        if callable(gui_callbacks.get('status_update')): gui_callbacks['status_update'](status_msg_gui)
        if callable(gui_callbacks.get('voice_status_update')): gui_callbacks['voice_status_update']("VOICE: LOAD", "loading") # Changed from CHK

    try:
        if not torch_module or not AutoProcessor_class or not BarkModel_class: # Should be caught by TTS_CAPABLE
            raise RuntimeError("Core PyTorch/Transformers modules for Bark are not available.")

        _bark_device_str = "cuda" if torch_module.cuda.is_available() else "cpu"
        logger.info(f"Target device for Bark model: {_bark_device_str}")

        logger.info(f"Loading Bark processor from '{model_load_path}'...")
        _bark_processor_instance = AutoProcessor_class.from_pretrained(model_load_path, local_files_only=is_local_path)

        logger.info(f"Loading Bark model from '{model_load_path}' to {_bark_device_str}...")
        _bark_model_instance = BarkModel_class.from_pretrained(model_load_path, local_files_only=is_local_path)
        _bark_model_instance.to(_bark_device_str) # type: ignore

        # Optional: CPU offload for small models if on GPU
        # if _bark_device_str == "cuda" and "small" in model_load_path.lower() and hasattr(_bark_model_instance, "enable_cpu_offload"):
        #     try: _bark_model_instance.enable_cpu_offload(); logger.info("Enabled CPU offload for Bark model.")
        #     except Exception as e_offload: logger.warning(f"Could not enable CPU offload: {e_offload}")

        _bark_resources_ready = True
        success_msg = f"Bark TTS ready (Model: {os.path.basename(str(model_load_path))} on {_bark_device_str})."
        logger.info(success_msg)
        if gui_callbacks:
            if callable(gui_callbacks.get('status_update')): gui_callbacks['status_update'](success_msg)
            if callable(gui_callbacks.get('voice_status_update')): gui_callbacks['voice_status_update']("VOICE: RDY", "ready")
    except Exception as e:
        _bark_load_error_msg = f"Failed to load Bark resources from '{model_load_path}': {e}"
        logger.critical(_bark_load_error_msg, exc_info=True)
        _bark_resources_ready = False
        if gui_callbacks:
            if callable(gui_callbacks.get('status_update')): gui_callbacks['status_update'](f"Bark TTS Error: {_bark_load_error_msg[:60]}...")
            if callable(gui_callbacks.get('voice_status_update')): gui_callbacks['voice_status_update']("VOICE: ERR", "error")
            if callable(gui_callbacks.get('messagebox_error')):
                gui_callbacks['messagebox_error']("Bark TTS Load Error", _bark_load_error_msg)
    finally:
        _bark_loading_in_progress = False
        os.environ["HF_HUB_OFFLINE"] = "0" # Reset offline flag


def start_speaking_response(text_to_speak, persona_name_llm, target_voice_preset,
                            gui_callbacks=None, on_actual_playback_start_gui_callback=None):
    global current_tts_thread, tts_stop_event

    if not is_tts_ready():
        errmsg = "TTS not ready."
        if _bark_loading_in_progress: errmsg = "TTS resources still loading."
        elif _bark_load_error_msg: errmsg = f"TTS load failed: {_bark_load_error_msg[:50]}..."
        elif not TTS_CAPABLE: errmsg = _bark_import_error_message or "TTS module unavailable."
        logger.warning(f"Cannot speak (GUI). {errmsg} Text: '{text_to_speak[:50]}...'")
        if gui_callbacks and callable(gui_callbacks.get('status_update')):
            gui_callbacks['status_update'](f"Cannot speak: {errmsg[:60]}")
        if on_actual_playback_start_gui_callback and callable(on_actual_playback_start_gui_callback):
             logger.debug("TTS not ready, invoking on_actual_playback_start_gui_callback as fallback.")
             try: on_actual_playback_start_gui_callback()
             except Exception as cb_exc: logger.error(f"Error in fallback on_actual_playback_start_gui_callback: {cb_exc}", exc_info=True)
        return

    if current_tts_thread and current_tts_thread.is_alive():
        logger.info(f"Previous speech thread '{current_tts_thread.name}' active. Signaling stop...")
        if tts_stop_event: tts_stop_event.set()
        current_tts_thread.join(timeout=2.0)
        if current_tts_thread.is_alive(): logger.warning(f"Previous speech thread '{current_tts_thread.name}' did not stop cleanly.")
        else: logger.info(f"Previous speech thread '{current_tts_thread.name}' stopped.")
    
    tts_stop_event = threading.Event()

    try:
        if not BarkTTS_class or not StreamingBarkTTS_class:
            raise RuntimeError("BarkTTS or StreamingBarkTTS class not available (should be caught by TTS_CAPABLE).")

        bark_tts_engine_instance = BarkTTS_class(
            processor=_bark_processor_instance, model=_bark_model_instance,
            device=_bark_device_str, voice_preset=target_voice_preset
        )
        streamer_instance = StreamingBarkTTS_class(bark_tts_instance=bark_tts_engine_instance)
        
        # Generation params for Bark itself (do_sample, temperatures) are from config via speak_bark.py defaults
        generation_params_for_streamer = {} # Not passing specific generation params here to streamer

        thread_name = f"BarkTTSStream-{time.strftime('%H%M%S')}"
        current_tts_thread = threading.Thread(
            target=streamer_instance.synthesize_and_play_stream,
            args=(text_to_speak, tts_stop_event, generation_params_for_streamer),
            kwargs={"on_playback_start_callback": on_actual_playback_start_gui_callback},
            name=thread_name, daemon=True
        )
        current_tts_thread.start()
        logger.info(f"Started Bark speech thread '{current_tts_thread.name}' for '{persona_name_llm}' voice '{target_voice_preset}'. Text: '{text_to_speak[:70]}...'")
        if gui_callbacks and callable(gui_callbacks.get('status_update')):
             gui_callbacks['status_update'](f"TTS initiated: {text_to_speak[:40]}...")
    except Exception as e:
        errmsg_speak = f"Error starting Bark speech: {e}"
        logger.error(errmsg_speak, exc_info=True)
        if gui_callbacks:
            if callable(gui_callbacks.get('messagebox_error')): gui_callbacks['messagebox_error']("Bark TTS Error", errmsg_speak)
            if callable(gui_callbacks.get('status_update')): gui_callbacks['status_update'](f"Bark speech error: {str(e)[:50]}...")
        if on_actual_playback_start_gui_callback and callable(on_actual_playback_start_gui_callback):
            logger.debug("TTS error during start, invoking fallback display callback.")
            try: on_actual_playback_start_gui_callback()
            except Exception as cb_exc: logger.error(f"Error in fallback display callback: {cb_exc}", exc_info=True)


def stop_current_speech(gui_callbacks=None):
    global current_tts_thread, tts_stop_event
    if not TTS_CAPABLE: return
    if current_tts_thread and current_tts_thread.is_alive():
        thread_name = current_tts_thread.name
        logger.info(f"Attempting to stop ongoing Bark speech on thread '{thread_name}'...")
        if tts_stop_event: tts_stop_event.set()
        else: logger.warning(f"tts_stop_event is None for alive thread '{thread_name}'. Cannot signal stop effectively.")
        current_tts_thread.join(timeout=2.5)
        if not current_tts_thread.is_alive():
            logger.info(f"Bark speech thread '{thread_name}' stopped or finished.")
            if gui_callbacks and callable(gui_callbacks.get('status_update')):
                gui_callbacks['status_update']("Speech interrupted.")
        else:
            logger.warning(f"Bark speech thread '{thread_name}' did not stop/join quickly.")
            if gui_callbacks and callable(gui_callbacks.get('status_update')):
                gui_callbacks['status_update']("Speech interruption slow/failed.")
        current_tts_thread = None
        tts_stop_event = None
    else: logger.debug("No active Bark speech thread to stop.")


def unload_bark_model(gui_callbacks=None):
    global _bark_processor_instance, _bark_model_instance, _bark_device_str, _bark_resources_ready
    global _bark_loading_in_progress, current_tts_thread, _bark_load_error_msg

    logger.info("Unload sequence initiated for Bark TTS model.")
    if current_tts_thread and current_tts_thread.is_alive():
        logger.info("Stopping active speech thread during Bark model unload...")
        stop_current_speech(gui_callbacks)

    if _bark_model_instance:
        logger.info("Releasing Bark model resources...")
        try:
            if torch_module and _bark_device_str == "cuda" and hasattr(_bark_model_instance, 'cpu'):
                _bark_model_instance = _bark_model_instance.cpu() # type: ignore
                logger.info("Moved Bark model to CPU.")
            del _bark_model_instance; _bark_model_instance = None
            if _bark_processor_instance:
                del _bark_processor_instance; _bark_processor_instance = None
            gc.collect()
            if torch_module and _bark_device_str == "cuda" and hasattr(torch_module.cuda, 'empty_cache'):
                 torch_module.cuda.empty_cache()
                 logger.info("PyTorch CUDA cache cleared.")
            logger.info("Bark model and processor resources released.")
        except Exception as e:
            logger.error(f"Error during Bark model cleanup: {e}", exc_info=True)

    _bark_resources_ready = False
    _bark_loading_in_progress = False
    _bark_load_error_msg = None # Clear previous error on unload
    _bark_device_str = None
    logger.info("Bark TTS model unloaded.")
    if gui_callbacks:
        if callable(gui_callbacks.get('status_update')):
            gui_callbacks['status_update']("Bark TTS model unloaded.")
        if callable(gui_callbacks.get('voice_status_update')):
             gui_callbacks['voice_status_update']("VOICE: OFF", "off")


def full_shutdown_tts_module():
    logger.info("Full Bark TTS module shutdown for application exit.")
    unload_bark_model()
    logger.info("Bark TTS module shutdown sequence complete.")

# --- New Status Functions ---
def get_status_short() -> str:
    """Returns a short status string for Bark TTS."""
    if not TTS_CAPABLE:
        return "N/A"
    if _bark_loading_in_progress:
        return "LOAD"
    if _bark_resources_ready: # is_tts_ready() also checks not loading, but this is fine
        return "RDY"
    if _bark_load_error_msg: # If there was a specific load error message
        return "ERR"
    return "OFF" # Default if not loading, not ready, and no specific error

def get_status_type() -> str:
    """Returns a status category string for Bark TTS (for GUI coloring)."""
    if not TTS_CAPABLE:
        return "na"
    if _bark_loading_in_progress:
        return "loading"
    if _bark_resources_ready:
        return "ready"
    if _bark_load_error_msg: # If there was a specific load error message
        return "error"
    return "off"