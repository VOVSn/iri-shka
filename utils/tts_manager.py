# utils/tts_manager.py
import threading
import time
import sys
import config

# Assuming logger.py is in the same 'utils' directory
from logger import get_logger

logger = get_logger(__name__)

_bark_imports_ok = False
_bark_import_error_message = ""
BarkTTS_class = None # Use a different name to avoid conflict with variable if class name is BarkTTS
StreamingBarkTTS_class = None
sounddevice_bark_module = None
torch_module = None
AutoProcessor_class = None
BarkModel_class = None


try:
    # Import specific classes/modules to control what's loaded
    from .speak_bark import BarkTTS, StreamingBarkTTS, sd as speak_bark_sd_module
    import torch
    from transformers import AutoProcessor, BarkModel

    BarkTTS_class = BarkTTS
    StreamingBarkTTS_class = StreamingBarkTTS
    sounddevice_bark_module = speak_bark_sd_module # From speak_bark, which checks SD_AVAILABLE
    torch_module = torch
    AutoProcessor_class = AutoProcessor
    BarkModel_class = BarkModel

    if sounddevice_bark_module is None: # Check if sounddevice loaded successfully within speak_bark
        raise ImportError("sounddevice failed to load within speak_bark.py or is not installed (speak_bark.sd is None).")
    _bark_imports_ok = True
    logger.info("Bark TTS dependencies (speak_bark, torch, transformers, sounddevice via speak_bark) imported successfully.")
except ImportError as e:
    _bark_import_error_message = f"Bark TTS critical import failed: {e}"
    logger.error(_bark_import_error_message, exc_info=True)
    # Ensure all are None if imports fail
    BarkTTS_class, StreamingBarkTTS_class = None, None
    torch_module, AutoProcessor_class, BarkModel_class = None, None, None
    sounddevice_bark_module = None
    _bark_imports_ok = False # Redundant but clear
except Exception as e: # Catch any other unexpected error during imports
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
_bark_load_error_msg = None # Stores detailed error message if loading fails

current_tts_thread = None
tts_stop_event = None # threading.Event, initialized when speech starts


def is_tts_ready():
    return TTS_AVAILABLE and _bark_resources_ready and not _bark_loading_in_progress

def is_tts_loading():
    return TTS_AVAILABLE and _bark_loading_in_progress

def load_bark_resources(gui_callbacks=None):
    global _bark_processor, _bark_model, _bark_device, _bark_resources_ready
    global _bark_loading_in_progress, _bark_load_error_msg, TTS_AVAILABLE # TTS_AVAILABLE can be set to False on load fail

    if not TTS_AVAILABLE:
        # _bark_import_error_message should already be set and logged
        # This is just a secondary log if load is attempted despite import failure
        final_import_err_msg = _bark_import_error_message or "speak_bark.py or its dependencies not correctly imported."
        logger.error(f"Cannot load Bark resources. Import failure: {final_import_err_msg}")
        _bark_load_error_msg = final_import_err_msg # Store for reference
        if gui_callbacks:
            if 'status_update' in gui_callbacks:
                gui_callbacks['status_update'](f"Bark TTS unavailable: {final_import_err_msg[:60]}...")
            if 'messagebox_warn' in gui_callbacks:
                 gui_callbacks['messagebox_warn']("Bark TTS Error", final_import_err_msg)
        return

    if _bark_resources_ready:
        logger.info("Bark resources already loaded.")
        return
    if _bark_loading_in_progress:
        logger.info("Bark resource loading already in progress. Ignoring new request.")
        return

    _bark_loading_in_progress = True
    _bark_resources_ready = False
    _bark_load_error_msg = None # Clear previous load error

    status_msg_gui = f"Initializing Bark TTS ({config.BARK_MODEL_NAME})..."
    logger.info(f"Loading Bark resources: Model '{config.BARK_MODEL_NAME}'.")
    if gui_callbacks and 'status_update' in gui_callbacks:
        gui_callbacks['status_update'](status_msg_gui)

    try:
        if not torch_module or not AutoProcessor_class or not BarkModel_class: # Should have been caught by TTS_AVAILABLE
            raise RuntimeError("Core PyTorch/Transformers modules for Bark are not available (should have been caught by TTS_AVAILABLE).")

        _bark_device = "cuda" if torch_module.cuda.is_available() else "cpu"
        logger.info(f"Target device for Bark model: {_bark_device}")

        logger.info(f"Loading Bark processor from '{config.BARK_MODEL_NAME}'...")
        _bark_processor = AutoProcessor_class.from_pretrained(config.BARK_MODEL_NAME)

        logger.info(f"Loading Bark model from '{config.BARK_MODEL_NAME}' to {_bark_device}...")
        _bark_model = BarkModel_class.from_pretrained(config.BARK_MODEL_NAME)
        _bark_model.to(_bark_device) # Ensure model is moved to device

        # Small test generation (optional, but good for catching early issues)
        # logger.debug("Performing a minimal test generation with Bark model...")
        # test_inputs = _bark_processor("test", return_tensors="pt").to(_bark_device)
        # with torch_module.no_grad():
        #     _bark_model.generate(**test_inputs, max_new_tokens=5) # Generate a very short sample
        # logger.debug("Minimal test generation successful.")


        _bark_resources_ready = True
        success_msg = f"Bark TTS ready (Model: {config.BARK_MODEL_NAME} on {_bark_device})."
        logger.info(success_msg)
        if gui_callbacks and 'status_update' in gui_callbacks:
            gui_callbacks['status_update'](success_msg)

    except Exception as e:
        _bark_load_error_msg = f"Failed to load Bark resources: {e}"
        logger.critical(_bark_load_error_msg, exc_info=True) # Critical as TTS won't work
        TTS_AVAILABLE = False # Set to False if loading fails, even if imports were initially OK
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
        elif not TTS_AVAILABLE: errmsg = "TTS module or dependencies unavailable."
        # _bark_import_error_message will be part of _bark_load_error_msg if it came from there.

        logger.warning(f"Cannot speak. {errmsg} Text: '{text_to_speak[:50]}...'")
        if gui_callbacks and 'status_update' in gui_callbacks:
            gui_callbacks['status_update'](f"Cannot speak: {errmsg}")

        # If TTS was expected but isn't ready, the callback might still need to run
        # for UI consistency (e.g., to display the text message immediately).
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
        else: # Should not happen if thread is alive
            logger.warning(f"tts_stop_event is None for alive thread '{current_tts_thread.name}'. This is unexpected.")
        current_tts_thread.join(timeout=2.0) # Wait for it to stop
        if current_tts_thread.is_alive():
            logger.warning(f"Previous speech thread '{current_tts_thread.name}' did not stop cleanly. Playback might be stuck or overlap.")
            if gui_callbacks and 'status_update' in gui_callbacks:
                 gui_callbacks['status_update']("TTS: Previous speech did not stop quickly.")
        else:
            logger.info(f"Previous speech thread '{current_tts_thread.name}' stopped.")


    tts_stop_event = threading.Event() # Create a new event for the new thread

    try:
        if not BarkTTS_class or not StreamingBarkTTS_class: # Should be caught by is_tts_ready
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
            # voice_preset is handled by BarkTTS instance
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
        # Fallback for callback if TTS start failed
        if on_actual_playback_start_gui_callback:
            logger.debug(f"TTS error during start, invoking on_actual_playback_start_gui_callback as fallback.")
            try: on_actual_playback_start_gui_callback()
            except Exception as cb_exc: logger.error(f"Error in fallback on_playback_start_gui_callback: {cb_exc}", exc_info=True)


def stop_current_speech(gui_callbacks=None):
    global current_tts_thread, tts_stop_event

    if not TTS_AVAILABLE: # No TTS, nothing to stop
        return

    if current_tts_thread and current_tts_thread.is_alive():
        thread_name = current_tts_thread.name
        logger.info(f"Attempting to stop ongoing Bark speech on thread '{thread_name}'...")
        if tts_stop_event:
            tts_stop_event.set() # Signal the thread to stop
        else:
            logger.warning(f"tts_stop_event is None for alive thread '{thread_name}'. Cannot signal stop effectively.")
            # This case should ideally not happen if the thread was started correctly.

        current_tts_thread.join(timeout=2.5) # Wait for the thread to finish

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
    global _bark_loading_in_progress, current_tts_thread, TTS_AVAILABLE # Removed tts_stop_event as it's instance-specific

    logger.info("Shutdown sequence initiated for Bark TTS.")

    if current_tts_thread and current_tts_thread.is_alive():
        logger.info("Stopping active speech thread during TTS shutdown...")
        stop_current_speech() # This will use the existing tts_stop_event for that thread

    if _bark_model: # Check if model was loaded
        logger.info("Releasing Bark model resources...")
        try:
            if torch_module and _bark_device == "cuda" and hasattr(_bark_model, 'cpu'): # Check torch_module
                _bark_model = _bark_model.cpu() # Move to CPU first
                logger.info("Moved Bark model to CPU.")

            del _bark_model # Delete reference
            _bark_model = None
            if _bark_processor: # Delete processor if it exists
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
    # TTS_AVAILABLE remains as per its initial import status unless loading failed critically.
    logger.info("Bark TTS shutdown complete.")