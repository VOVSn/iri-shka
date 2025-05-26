# utils/tts_manager.py
import threading
import time
import sys
import os
import config
import json # Keep for potential future use, though not directly used in this version of load
import glob
from pathlib import Path

# Set environment variables to force offline mode BEFORE any HF imports
# These are crucial for truly offline operation.
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_DATASETS_OFFLINE'] = '1' # Though not directly used by Bark, good practice
# Setting TRANSFORMERS_CACHE is deprecated, HF_HOME is preferred, but let's ensure cache is known
hf_cache_home = os.path.join(os.path.expanduser('~'), '.cache', 'huggingface')
os.environ['HF_HOME'] = hf_cache_home
os.environ['TRANSFORMERS_CACHE'] = os.path.join(hf_cache_home, 'transformers') # For older compatibility if needed
os.environ['HF_HUB_CACHE'] = os.path.join(hf_cache_home, 'hub')


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
# For more specific tokenizer loading if needed
# AutoTokenizer_class = None


try:
    # Import specific classes/modules to control what's loaded
    from .speak_bark import BarkTTS, StreamingBarkTTS, sd as speak_bark_sd_module
    import torch
    from transformers import AutoProcessor, BarkModel #, AutoTokenizer # Might need AutoTokenizer explicitly

    BarkTTS_class = BarkTTS
    StreamingBarkTTS_class = StreamingBarkTTS
    sounddevice_bark_module = speak_bark_sd_module
    torch_module = torch
    AutoProcessor_class = AutoProcessor
    BarkModel_class = BarkModel
    # AutoTokenizer_class = AutoTokenizer


    if sounddevice_bark_module is None:
        raise ImportError("sounddevice failed to load within speak_bark.py or is not installed (speak_bark.sd is None).")
    _bark_imports_ok = True
    logger.info("Bark TTS dependencies (speak_bark, torch, transformers, sounddevice via speak_bark) imported successfully.")
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


def get_hf_cache_dir():
    """Gets the Hugging Face cache directory, preferring HF_HOME."""
    if os.getenv('HF_HOME'):
        return os.getenv('HF_HOME')
    return os.path.join(os.path.expanduser('~'), '.cache', 'huggingface')

def find_fully_cached_model_path(model_id: str):
    """
    Attempts to find the latest snapshot of a cached model in the Hugging Face Hub cache.
    Args:
        model_id (str): The model ID, e.g., "suno/bark-small".
    Returns:
        str: The path to the model snapshot directory if found, else None.
    """
    cache_dir = get_hf_cache_dir()
    model_path_prefix = f"models--{model_id.replace('/', '--')}"
    potential_model_dir = os.path.join(cache_dir, 'hub', model_path_prefix)

    logger.info(f"Searching for cached model '{model_id}' in '{potential_model_dir}'")

    if not os.path.isdir(potential_model_dir):
        logger.warning(f"Base directory for model '{model_id}' not found at '{potential_model_dir}'.")
        return None

    snapshots_dir = os.path.join(potential_model_dir, "snapshots")
    if not os.path.isdir(snapshots_dir):
        logger.warning(f"Snapshots directory not found for model '{model_id}' at '{snapshots_dir}'. Model might be incompletely cached or in an old format.")
        # Fallback: Check if potential_model_dir itself contains model files (older cache format)
        if os.path.exists(os.path.join(potential_model_dir, "config.json")):
            logger.info(f"Found model files directly in '{potential_model_dir}' (possibly older cache format).")
            return potential_model_dir
        return None

    available_snapshots = [
        d for d in os.listdir(snapshots_dir)
        if os.path.isdir(os.path.join(snapshots_dir, d))
    ]

    if not available_snapshots:
        logger.warning(f"No snapshots found in '{snapshots_dir}' for model '{model_id}'.")
        return None

    # Try to find a snapshot that looks complete (has a config.json)
    # And sort them to get the "latest" (alphanumerically, often corresponds to commit hash)
    valid_snapshots = []
    for snap in sorted(available_snapshots, reverse=True):
        snap_path = os.path.join(snapshots_dir, snap)
        if os.path.exists(os.path.join(snap_path, "config.json")): # A good indicator of a model snapshot
            valid_snapshots.append(snap_path)
    
    if valid_snapshots:
        latest_valid_snapshot = valid_snapshots[0] # Take the first one after sorting reverse
        logger.info(f"Found latest valid cached snapshot for '{model_id}': '{latest_valid_snapshot}'")
        return latest_valid_snapshot
    else:
        logger.warning(f"No valid snapshots (containing config.json) found for '{model_id}'. Cache might be corrupted or incomplete.")
        return None

def load_bark_resources(gui_callbacks=None):
    global _bark_processor, _bark_model, _bark_device, _bark_resources_ready
    global _bark_loading_in_progress, _bark_load_error_msg, TTS_AVAILABLE

    if not TTS_AVAILABLE:
        final_import_err_msg = _bark_import_error_message or "speak_bark.py or its dependencies not correctly imported."
        logger.error(f"Cannot load Bark resources. Import failure: {final_import_err_msg}")
        _bark_load_error_msg = final_import_err_msg
        if gui_callbacks and 'status_update' in gui_callbacks:
            gui_callbacks['status_update'](f"Bark TTS unavailable: {final_import_err_msg[:60]}...")
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

    model_id_from_config = config.BARK_MODEL_NAME
    status_msg_gui = f"Initializing Bark TTS ({model_id_from_config}) from local cache..."
    logger.info(f"Loading Bark resources: Model '{model_id_from_config}' (offline enforced).")
    if gui_callbacks and 'status_update' in gui_callbacks:
        gui_callbacks['status_update'](status_msg_gui)

    try:
        if not torch_module or not AutoProcessor_class or not BarkModel_class:
            raise RuntimeError("Core PyTorch/Transformers modules for Bark are not available (should have been caught by TTS_AVAILABLE).")

        _bark_device = "cuda" if torch_module.cuda.is_available() else "cpu"
        logger.info(f"Target device for Bark model: {_bark_device}")

        # --- Find the exact local path to the cached model ---
        local_model_path = find_fully_cached_model_path(model_id_from_config)

        if not local_model_path:
            err_msg = (
                f"Bark model '{model_id_from_config}' not found in local Hugging Face cache. "
                "Please ensure the model was fully downloaded by running the application "
                "with an internet connection first. Also, verify HF_HOME/cache paths."
            )
            logger.critical(err_msg)
            raise FileNotFoundError(err_msg)
        
        logger.info(f"Using fully resolved local cache path for '{model_id_from_config}': {local_model_path}")

        # --- Load Processor ---
        # When using a local path, local_files_only=True is somewhat redundant but harmless.
        # The key is that we are providing a local file path, not a model ID to be resolved online.
        logger.info(f"Loading Bark processor from local path: '{local_model_path}'")
        _bark_processor = AutoProcessor_class.from_pretrained(
            local_model_path,
            local_files_only=True, # Still good to keep
            trust_remote_code=False # Important for security with local models
        )

        # --- Load Model ---
        logger.info(f"Loading Bark model from local path: '{local_model_path}' to {_bark_device}")
        _bark_model = BarkModel_class.from_pretrained(
            local_model_path,
            local_files_only=True, # Still good to keep
            trust_remote_code=False # Important for security
        )
        _bark_model.to(_bark_device)

        _bark_resources_ready = True
        success_msg = f"Bark TTS ready (Model: {model_id_from_config} on {_bark_device} from local cache: {local_model_path})."
        logger.info(success_msg)
        if gui_callbacks and 'status_update' in gui_callbacks:
            gui_callbacks['status_update'](success_msg)

    except FileNotFoundError as fnf_e: # Specifically for model not found in cache
        _bark_load_error_msg = str(fnf_e)
        logger.critical(_bark_load_error_msg, exc_info=False) # No full traceback if it's just "not found"
        TTS_AVAILABLE = False
        if gui_callbacks:
            if 'status_update' in gui_callbacks: gui_callbacks['status_update']("Bark Error: Model not cached.")
            if 'messagebox_error' in gui_callbacks: gui_callbacks['messagebox_error']("Bark Model Not Found", _bark_load_error_msg)
    except Exception as e: # Catch other errors during loading from local path
        _bark_load_error_msg = f"Failed to load Bark resources from local path '{local_model_path if 'local_model_path' in locals() else model_id_from_config}': {e}"
        logger.critical(_bark_load_error_msg, exc_info=True)
        TTS_AVAILABLE = False # If loading from local fails, something is wrong
        if gui_callbacks:
            if 'status_update' in gui_callbacks: gui_callbacks['status_update'](f"Bark TTS Error: {_bark_load_error_msg[:60]}...")
            if 'messagebox_error' in gui_callbacks: gui_callbacks['messagebox_error']("Bark TTS Load Error", _bark_load_error_msg)
    finally:
        _bark_loading_in_progress = False


def start_speaking_response(text_to_speak, persona_name_llm, target_voice_preset,
                            gui_callbacks=None, on_actual_playback_start_gui_callback=None):
    global current_tts_thread, tts_stop_event

    if not is_tts_ready():
        errmsg = "TTS not ready."
        if _bark_loading_in_progress: errmsg = "TTS resources still loading."
        elif _bark_load_error_msg: errmsg = f"TTS load failed: {_bark_load_error_msg[:50]}..."
        elif not TTS_AVAILABLE: errmsg = "TTS module or dependencies unavailable."

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
            logger.warning(f"Previous speech thread '{current_tts_thread.name}' did not stop cleanly. Playback might be stuck or overlap.")
            if gui_callbacks and 'status_update' in gui_callbacks:
                 gui_callbacks['status_update']("TTS: Previous speech did not stop quickly.")
        else:
            logger.info(f"Previous speech thread '{current_tts_thread.name}' stopped.")

    tts_stop_event = threading.Event()

    try:
        if not BarkTTS_class or not StreamingBarkTTS_class:
            raise RuntimeError("BarkTTS or StreamingBarkTTS class not available (should be caught by is_tts_ready).")

        bark_tts_instance = BarkTTS_class(
            processor=_bark_processor,
            model=_bark_model,
            device=_bark_device,
            voice_preset=target_voice_preset
        )
        streamer = StreamingBarkTTS_class(
            bark_tts_instance=bark_tts_instance,
            max_sentences_per_chunk=config.BARK_MAX_SENTENCES_PER_CHUNK,
            silence_duration_ms=config.BARK_SILENCE_DURATION_MS
        )

        generation_params = {
            "do_sample": config.BARK_DO_SAMPLE,
            "fine_temperature": config.BARK_FINE_TEMPERATURE,
            "coarse_temperature": config.BARK_COARSE_TEMPERATURE
        }

        thread_name = f"BarkTTSStream-{time.strftime('%H%M%S')}"
        current_tts_thread = threading.Thread(
            target=streamer.synthesize_and_play_stream,
            args=(text_to_speak, tts_stop_event, generation_params),
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
            except Exception as cb_exc: logger.error(f"Error in fallback on_actual_playback_start_gui_callback: {cb_exc}", exc_info=True)


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
            logger.warning(f"Bark speech thread '{thread_name}' did not stop/join quickly. It might be stuck in I/O or synthesis.")
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
    logger.info("Bark TTS shutdown complete.")