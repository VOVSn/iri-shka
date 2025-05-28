# utils/tts_manager.py
import threading
import time
import sys
import os 
import config
import gc # Explicitly import gc

# Assuming logger.py is in the same 'utils' directory
from logger import get_logger

logger = get_logger(__name__)

_bark_imports_ok = False
_bark_import_error_message = ""
BarkTTS_class = None
StreamingBarkTTS_class = None
sounddevice_bark_module = None # This will be speak_bark's sd
torch_module = None
AutoProcessor_class = None
BarkModel_class = None

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
# Use the BARK_MODEL_NAME from config, which might be a local path or Hugging Face ID
# EXPLICIT_BARK_LOCAL_PATH is now effectively handled by how config.BARK_MODEL_NAME is set
# If config.BARK_MODEL_NAME is like "./bark", it's a local path.

try:
    # speak_bark.py should handle its own imports of torch, transformers, sounddevice
    from utils.speak_bark import BarkTTS, StreamingBarkTTS, sd as speak_bark_sd_module
    
    # We still need torch and transformers here for type hinting or direct use if speak_bark API changes
    import torch 
    from transformers import AutoProcessor, BarkModel

    BarkTTS_class = BarkTTS
    StreamingBarkTTS_class = StreamingBarkTTS
    sounddevice_bark_module = speak_bark_sd_module # Get sounddevice from speak_bark
    torch_module = torch
    AutoProcessor_class = AutoProcessor
    BarkModel_class = BarkModel

    if sounddevice_bark_module is None : # Check if sd was successfully imported in speak_bark
        _bark_import_error_message = "SoundDevice failed to load within speak_bark.py or is not installed."
        raise ImportError(_bark_import_error_message)
        
    _bark_imports_ok = True
    logger.info("Bark TTS dependencies (via speak_bark and direct) imported successfully.")
except ImportError as e:
    _bark_import_error_message = f"Bark TTS critical import failed: {e}"
    logger.error(_bark_import_error_message, exc_info=True)
    BarkTTS_class, StreamingBarkTTS_class = None, None
    torch_module, AutoProcessor_class, BarkModel_class = None, None, None
    sounddevice_bark_module = None
    _bark_imports_ok = False
except Exception as e: # Catch other potential errors during imports
    _bark_import_error_message = f"Unexpected error during Bark TTS dependency imports: {e}"
    logger.critical(_bark_import_error_message, exc_info=True)
    BarkTTS_class, StreamingBarkTTS_class = None, None
    torch_module, AutoProcessor_class, BarkModel_class = None, None, None
    sounddevice_bark_module = None
    _bark_imports_ok = False


TTS_CAPABLE = _bark_imports_ok 

_bark_processor_instance = None # Renamed from _bark_processor
_bark_model_instance = None     # Renamed from _bark_model
_bark_device_str = None         # Renamed from _bark_device
_bark_resources_ready = False
_bark_loading_in_progress = False
_bark_load_error_msg = None

current_tts_thread = None
tts_stop_event = None # This is a threading.Event


def is_tts_ready():
    return TTS_CAPABLE and _bark_resources_ready and not _bark_loading_in_progress

def is_tts_loading():
    return TTS_CAPABLE and _bark_loading_in_progress

def get_bark_model_instance():
    """
    Returns a BarkTTS instance if resources are ready, or None.
    This instance can be used for direct synthesis (e.g., for Telegram voice replies).
    It uses the globally loaded model and processor.
    """
    if is_tts_ready() and BarkTTS_class and _bark_processor_instance and _bark_model_instance and _bark_device_str:
        try:
            # Create a new BarkTTS wrapper instance using the shared, loaded model/processor
            return BarkTTS_class(
                processor=_bark_processor_instance,
                model=_bark_model_instance,
                device=_bark_device_str
                # The voice_preset for synthesis will be passed during its synthesize_speech_to_array call
            )
        except Exception as e:
            logger.error(f"Error creating BarkTTS instance in get_bark_model_instance: {e}", exc_info=True)
            return None
    logger.warning("get_bark_model_instance: TTS resources not ready or core components missing.")
    return None


def load_bark_resources(gui_callbacks=None):
    global _bark_processor_instance, _bark_model_instance, _bark_device_str, _bark_resources_ready
    global _bark_loading_in_progress, _bark_load_error_msg

    # Attempt to force offline mode for Hugging Face Hub if models are local/cached
    # This helps prevent unexpected downloads if an online connection is available
    # but we intend to use local/cached models.
    os.environ["HF_HUB_OFFLINE"] = "1"
    logger.debug(f"Ensured HF_HUB_OFFLINE is set to '1'.")

    model_load_path = config.BARK_MODEL_NAME 
    is_local_path = model_load_path.startswith("./") or os.path.isabs(model_load_path)

    if is_local_path and not os.path.isdir(model_load_path):
        logger.error(f"Specified local Bark model path '{model_load_path}' NOT FOUND or is not a directory. Cannot load.")
        _bark_load_error_msg = f"Local path '{model_load_path}' not found."
        if gui_callbacks:
            if 'status_update' in gui_callbacks: gui_callbacks['status_update'](f"Bark TTS Error: {_bark_load_error_msg[:60]}...")
            if 'voice_status_update' in gui_callbacks: gui_callbacks['voice_status_update']("VOICE: NRDY", "error")
        return # Cannot proceed if a specified local path is invalid

    if not TTS_CAPABLE: 
        final_import_err_msg = _bark_import_error_message or "speak_bark.py or dependencies not imported."
        logger.error(f"Cannot load Bark resources. Import failure: {final_import_err_msg}")
        _bark_load_error_msg = final_import_err_msg
        if gui_callbacks and 'status_update' in gui_callbacks:
            gui_callbacks['status_update'](f"Bark TTS unavailable: {final_import_err_msg[:60]}...")
        return

    if _bark_resources_ready:
        logger.info("Bark resources already loaded.")
        if gui_callbacks and 'voice_status_update' in gui_callbacks:
             gui_callbacks['voice_status_update']("VOICE: RDY", "ready")
        return
    if _bark_loading_in_progress:
        logger.info("Bark resource loading already in progress. Ignoring new request.")
        return

    _bark_loading_in_progress = True
    _bark_resources_ready = False 
    _bark_load_error_msg = None

    status_msg_gui = f"Initializing Bark TTS ({os.path.basename(str(model_load_path))})..."
    logger.info(f"Loading Bark resources from: '{model_load_path}'.")
    if gui_callbacks:
        if 'status_update' in gui_callbacks: gui_callbacks['status_update'](status_msg_gui)
        if 'voice_status_update' in gui_callbacks: gui_callbacks['voice_status_update']("VOICE: CHK", "loading")

    try:
        if not torch_module or not AutoProcessor_class or not BarkModel_class:
            raise RuntimeError("Core PyTorch/Transformers modules for Bark are not available (should be caught by TTS_CAPABLE).")

        _bark_device_str = "cuda" if torch_module.cuda.is_available() else "cpu"
        logger.info(f"Target device for Bark model: {_bark_device_str}")

        logger.info(f"Loading Bark processor from '{model_load_path}'...")
        # Pass local_files_only=True if it's a local path and we want to be strict.
        # If it's an HF ID, HF_HUB_OFFLINE=1 should prevent downloads if not cached.
        _bark_processor_instance = AutoProcessor_class.from_pretrained(model_load_path, local_files_only=is_local_path)

        logger.info(f"Loading Bark model from '{model_load_path}' to {_bark_device_str}...")
        _bark_model_instance = BarkModel_class.from_pretrained(model_load_path, local_files_only=is_local_path)
        _bark_model_instance.to(_bark_device_str)
        
        # For small models, enable CPU offload on GPU if CUDA and not enough VRAM (optional, more complex)
        # if _bark_device_str == "cuda" and hasattr(_bark_model_instance, "enable_cpu_offload"):
        # try: _bark_model_instance.enable_cpu_offload() logger.info("Enabled CPU offload for Bark model on GPU.")
        # except Exception as e_offload: logger.warning(f"Could not enable CPU offload: {e_offload}")

        _bark_resources_ready = True
        success_msg = f"Bark TTS ready (Model: {os.path.basename(str(model_load_path))} on {_bark_device_str})."
        logger.info(success_msg)
        if gui_callbacks:
            if 'status_update' in gui_callbacks: gui_callbacks['status_update'](success_msg)
            if 'voice_status_update' in gui_callbacks: gui_callbacks['voice_status_update']("VOICE: RDY", "ready")

    except Exception as e:
        _bark_load_error_msg = f"Failed to load Bark resources from '{model_load_path}': {e}"
        logger.critical(_bark_load_error_msg, exc_info=True)
        _bark_resources_ready = False 
        if gui_callbacks:
            if 'status_update' in gui_callbacks: gui_callbacks['status_update'](f"Bark TTS Error: {_bark_load_error_msg[:60]}...")
            if 'voice_status_update' in gui_callbacks: gui_callbacks['voice_status_update']("VOICE: NRDY", "error")
            if 'messagebox_error' in gui_callbacks: 
                gui_callbacks['messagebox_error']("Bark TTS Load Error", _bark_load_error_msg)
    finally:
        _bark_loading_in_progress = False


def start_speaking_response(text_to_speak, persona_name_llm, target_voice_preset,
                            gui_callbacks=None, on_actual_playback_start_gui_callback=None):
    global current_tts_thread, tts_stop_event

    if not is_tts_ready(): # Checks capability, readiness, and not loading
        errmsg = "TTS not ready."
        if _bark_loading_in_progress: errmsg = "TTS resources still loading."
        elif _bark_load_error_msg: errmsg = f"TTS load failed: {_bark_load_error_msg[:50]}..."
        elif not TTS_CAPABLE: 
            errmsg = _bark_import_error_message or "TTS module unavailable."
            errmsg = f"TTS unavailable: {errmsg[:50]}..."

        logger.warning(f"Cannot speak (GUI). {errmsg} Text: '{text_to_speak[:50]}...'")
        if gui_callbacks and 'status_update' in gui_callbacks:
            gui_callbacks['status_update'](f"Cannot speak: {errmsg}")

        # If a callback for GUI display update was provided, call it as a fallback
        # so the message at least appears in the chat, even if not spoken.
        if on_actual_playback_start_gui_callback:
             logger.debug(f"TTS not ready for GUI speech, invoking on_actual_playback_start_gui_callback as fallback for text display.")
             try: on_actual_playback_start_gui_callback()
             except Exception as cb_exc: logger.error(f"Error in fallback on_actual_playback_start_gui_callback: {cb_exc}", exc_info=True)
        return

    if current_tts_thread and current_tts_thread.is_alive():
        logger.info(f"Previous speech thread '{current_tts_thread.name}' active. Signaling stop...")
        if tts_stop_event: tts_stop_event.set()
        current_tts_thread.join(timeout=2.0) # Wait for it to finish
        if current_tts_thread.is_alive(): logger.warning(f"Previous speech thread '{current_tts_thread.name}' did not stop cleanly.")
        else: logger.info(f"Previous speech thread '{current_tts_thread.name}' stopped.")

    tts_stop_event = threading.Event() # Create a new event for the new thread

    try:
        if not BarkTTS_class or not StreamingBarkTTS_class: # Should be caught by TTS_CAPABLE
            raise RuntimeError("BarkTTS or StreamingBarkTTS class not available.")

        # Create a BarkTTS wrapper instance using the globally loaded model and processor
        bark_tts_engine_instance = BarkTTS_class(
            processor=_bark_processor_instance,
            model=_bark_model_instance,
            device=_bark_device_str,
            voice_preset=target_voice_preset # Pass the specific voice for this speech
        )
        
        streamer_instance = StreamingBarkTTS_class(
            bark_tts_instance=bark_tts_engine_instance
            # max_sentences_per_chunk and silence_duration_ms will use defaults from config via speak_bark.py
        )
        
        # Generation params for Bark itself (do_sample, temperatures) are set in speak_bark.py from config
        # We pass the target_voice_preset via the BarkTTS_class instance.
        generation_params_for_streamer = {} 

        thread_name = f"BarkTTSStream-{time.strftime('%H%M%S')}"
        current_tts_thread = threading.Thread(
            target=streamer_instance.synthesize_and_play_stream,
            args=(text_to_speak, tts_stop_event, generation_params_for_streamer), 
            kwargs={"on_playback_start_callback": on_actual_playback_start_gui_callback},
            name=thread_name,
            daemon=True
        )
        current_tts_thread.start()
        logger.info(f"Started Bark speech thread '{current_tts_thread.name}' for LLM persona '{persona_name_llm}' with voice '{target_voice_preset}'. Text: '{text_to_speak[:70]}...'")
        # GUI status update is handled by the on_actual_playback_start_gui_callback for "Speaking..."
        # Initial status before playback starts:
        if gui_callbacks and 'status_update' in gui_callbacks:
             gui_callbacks['status_update'](f"TTS initiated for: {text_to_speak[:40]}...")

    except Exception as e:
        errmsg = f"Error starting Bark speech: {e}"
        logger.error(errmsg, exc_info=True)
        if gui_callbacks:
            if 'messagebox_error' in gui_callbacks: gui_callbacks['messagebox_error']("Bark TTS Error", errmsg)
            if 'status_update' in gui_callbacks: gui_callbacks['status_update'](f"Bark speech error: {str(e)[:50]}...")
        # Fallback display if speech initiation fails
        if on_actual_playback_start_gui_callback:
            logger.debug(f"TTS error during start, invoking fallback display callback.")
            try: on_actual_playback_start_gui_callback()
            except Exception as cb_exc: logger.error(f"Error in fallback display callback: {cb_exc}", exc_info=True)


def stop_current_speech(gui_callbacks=None):
    global current_tts_thread, tts_stop_event

    if not TTS_CAPABLE: return # No capability, nothing to stop

    if current_tts_thread and current_tts_thread.is_alive():
        thread_name = current_tts_thread.name
        logger.info(f"Attempting to stop ongoing Bark speech on thread '{thread_name}'...")
        if tts_stop_event:
            tts_stop_event.set() # Signal the StreamingBarkTTS's stop_event
        else: # Should not happen if thread is alive and was started by start_speaking_response
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
        current_tts_thread = None # Clear reference after stopping/joining
        tts_stop_event = None
    else:
        logger.debug("No active Bark speech thread to stop.")


def unload_bark_model(gui_callbacks=None): 
    global _bark_processor_instance, _bark_model_instance, _bark_device_str, _bark_resources_ready
    global _bark_loading_in_progress, current_tts_thread, _bark_load_error_msg

    logger.info("Unload sequence initiated for Bark TTS model.")

    if current_tts_thread and current_tts_thread.is_alive():
        logger.info("Stopping active speech thread during Bark model unload...")
        stop_current_speech(gui_callbacks) 

    if _bark_model_instance: # Check if model instance exists
        logger.info("Releasing Bark model resources...")
        try:
            # Move to CPU first if on CUDA to free VRAM before del
            if torch_module and _bark_device_str == "cuda" and hasattr(_bark_model_instance, 'cpu'):
                _bark_model_instance = _bark_model_instance.cpu() 
                logger.info("Moved Bark model to CPU.")

            del _bark_model_instance 
            _bark_model_instance = None
            if _bark_processor_instance:
                del _bark_processor_instance
                _bark_processor_instance = None
            
            gc.collect() # Explicitly call garbage collector

            if torch_module and _bark_device_str == "cuda" and hasattr(torch_module.cuda, 'empty_cache'):
                 torch_module.cuda.empty_cache()
                 logger.info("PyTorch CUDA cache cleared.")
            logger.info("Bark model and processor resources released.")
        except Exception as e:
            logger.error(f"Error during Bark model cleanup: {e}", exc_info=True)

    _bark_resources_ready = False
    _bark_loading_in_progress = False 
    _bark_load_error_msg = None 
    _bark_device_str = None # Reset device string

    logger.info("Bark TTS model unloaded.")
    if gui_callbacks:
        if 'status_update' in gui_callbacks:
            gui_callbacks['status_update']("Bark TTS model unloaded.")
        if 'voice_status_update' in gui_callbacks:
             gui_callbacks['voice_status_update']("VOICE: OFF", "off")


def full_shutdown_tts_module():
    logger.info("Full Bark TTS module shutdown for application exit.")
    unload_bark_model() # This handles stopping speech and releasing model resources
    # No other specific module-level resources to clean up for Bark beyond the model.
    logger.info("Bark TTS module shutdown sequence complete.")