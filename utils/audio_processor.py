# utils/audio_processor.py
import pyaudio
import wave
import numpy as np
import gc
import threading # Ensure threading is imported if not already
import config

# Assuming logger.py is in the same 'utils' directory
from logger import get_logger

logger = get_logger(__name__)

_is_recording = False
_audio_frames_bytes = []
_pyaudio_instance = None
_audio_stream = None
_active_recording_thread = None

def is_recording_active():
    global _is_recording
    return _is_recording

def start_recording(gui_callbacks=None):
    global _is_recording, _audio_frames_bytes, _pyaudio_instance, _audio_stream, _active_recording_thread

    if _is_recording:
        logger.info("Recording already in progress.")
        return False

    _is_recording = True
    _audio_frames_bytes = []

    if _pyaudio_instance:
        try:
            if _audio_stream and _audio_stream.is_active():
                _audio_stream.stop_stream()
            if _audio_stream:
                _audio_stream.close()
            _pyaudio_instance.terminate()
        except Exception as e:
            logger.warning(f"Error terminating previous PyAudio instance: {e}", exc_info=False)
        _audio_stream = None
        _pyaudio_instance = None

    _pyaudio_instance = pyaudio.PyAudio()
    actual_rate = config.INPUT_RATE
    try:
        _audio_stream = _pyaudio_instance.open(
            format=config.FORMAT,
            channels=config.CHANNELS,
            rate=config.INPUT_RATE,
            input=True,
            frames_per_buffer=config.CHUNK
        )
        logger.info(f"Audio stream opened at {config.INPUT_RATE} Hz.")
    except Exception as e_rate:
        logger.warning(f"Could not open audio at {config.INPUT_RATE}Hz: {e_rate}. Trying {config.ALTERNATIVE_RATE}Hz.", exc_info=False)
        try:
            actual_rate = config.ALTERNATIVE_RATE
            _audio_stream = _pyaudio_instance.open(
                format=config.FORMAT,
                channels=config.CHANNELS,
                rate=config.ALTERNATIVE_RATE,
                input=True,
                frames_per_buffer=config.CHUNK
            )
            logger.info(f"Audio stream opened at {actual_rate} Hz.")
        except Exception as e2:
            error_msg = f"Could not open audio stream: {e2}"
            logger.critical(error_msg, exc_info=True)
            if gui_callbacks and 'messagebox_error' in gui_callbacks:
                gui_callbacks['messagebox_error']("Audio Error", error_msg)
            
            if gui_callbacks and 'status_update' in gui_callbacks: # General status update
                gui_callbacks['status_update']("Audio Error. Check Mic.")
            
            # No longer directly manage speak_button text/state here. main.py handles it.
            _is_recording = False
            if _pyaudio_instance: _pyaudio_instance.terminate()
            _pyaudio_instance = None
            _audio_stream = None
            return False

    if gui_callbacks and 'status_update' in gui_callbacks:
        gui_callbacks['status_update']("Recording...") # General status update
    logger.info("Recording started.")

    def _recording_loop_worker(target_rate, loop_gui_callbacks):
        global _is_recording, _audio_frames_bytes, _audio_stream, _pyaudio_instance
        while _is_recording:
            try:
                data = _audio_stream.read(config.CHUNK, exception_on_overflow=False)
                _audio_frames_bytes.append(data)
            except IOError as e:
                pa_overflow_err_code = getattr(pyaudio, 'paInputOverflowed', -9981) 
                if hasattr(e, 'errno') and e.errno == pa_overflow_err_code:
                    logger.warning("Audio input overflowed.")
                else:
                    logger.error(f"IOError during recording: {e}. Stopping.", exc_info=True)
                    _is_recording = False # Ensure stop if critical IO error
                    break
            except Exception as e:
                logger.error(f"Unexpected error during recording: {e}. Stopping.", exc_info=True)
                _is_recording = False # Ensure stop
                break

        logger.info("Recording finished in thread.")
        if _audio_stream:
            try:
                if _audio_stream.is_active(): _audio_stream.stop_stream()
                _audio_stream.close()
            except Exception as e_close:
                logger.warning(f"Error closing audio stream: {e_close}", exc_info=False)
        if _pyaudio_instance:
            _pyaudio_instance.terminate()

        _audio_stream = None
        _pyaudio_instance = None

        if loop_gui_callbacks and 'status_update' in loop_gui_callbacks:
            loop_gui_callbacks['status_update']("Processing audio...")
        if loop_gui_callbacks and 'on_recording_finished' in loop_gui_callbacks:
            loop_gui_callbacks['on_recording_finished'](target_rate)

    _active_recording_thread = threading.Thread(target=_recording_loop_worker, args=(actual_rate, gui_callbacks), daemon=True, name="AudioRecordingThread")
    _active_recording_thread.start()
    return True


def stop_recording():
    global _is_recording
    if not _is_recording:
        logger.info("No active recording to stop.")
        return
    _is_recording = False # Signal the recording thread to stop
    logger.info("Stop recording signal sent.")


def save_wav_data_to_file(filepath, frames_byte_list, sample_rate_for_wav, gui_callbacks=None):
    pa_temp_save = None
    try:
        pa_temp_save = pyaudio.PyAudio()
        with wave.open(filepath, 'wb') as wf:
            wf.setnchannels(config.CHANNELS)
            wf.setsampwidth(pa_temp_save.get_sample_size(config.FORMAT))
            wf.setframerate(sample_rate_for_wav)
            wf.writeframes(b''.join(frames_byte_list))
        logger.info(f"Saved WAV: {filepath} at {sample_rate_for_wav} Hz")
        return True
    except Exception as e:
        error_msg = f"Could not save WAV file '{filepath}': {e}"
        logger.error(error_msg, exc_info=True)
        if gui_callbacks and 'status_update' in gui_callbacks:
            gui_callbacks['status_update'](f"WAV Save Error: {e}")
        if gui_callbacks and 'messagebox_error' in gui_callbacks:
            gui_callbacks['messagebox_error']("WAV Save Error", error_msg)
        return False
    finally:
        if pa_temp_save:
            pa_temp_save.terminate()


def convert_frames_to_numpy(recorded_sample_rate, gui_callbacks=None):
    global _audio_frames_bytes
    if not _audio_frames_bytes:
        logger.info("No audio recorded to convert.")
        if gui_callbacks and 'status_update' in gui_callbacks:
            gui_callbacks['status_update']("No audio recorded.")
        return None, None

    try:
        audio_data_bytes = b''.join(_audio_frames_bytes)
        frames_copy_for_save = _audio_frames_bytes[:] # Make a copy for saving if needed
        _audio_frames_bytes = [] # Clear original list
        gc.collect()

        audio_int16 = np.frombuffer(audio_data_bytes, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0
        logger.debug("Audio frames converted to NumPy float32 array.")
        return audio_float32, frames_copy_for_save
    except Exception as e:
        error_msg = f"Error converting audio data to NumPy: {e}"
        logger.error(error_msg, exc_info=True)
        if gui_callbacks and 'messagebox_error' in gui_callbacks:
            gui_callbacks['messagebox_error']("Audio Conversion Error", error_msg)
        if gui_callbacks and 'status_update' in gui_callbacks:
            gui_callbacks['status_update']("Audio conversion error.")
        _audio_frames_bytes = [] # Ensure it's cleared on error too
        return None, None


def shutdown_audio_resources():
    global _is_recording, _audio_stream, _pyaudio_instance, _active_recording_thread
    logger.info("Shutting down audio resources...")
    _is_recording = False

    if _active_recording_thread and _active_recording_thread.is_alive():
        logger.info("Waiting for recording thread to finish...")
        _active_recording_thread.join(timeout=1.0) # Give it a second to finish naturally
        if _active_recording_thread.is_alive():
            logger.warning("Recording thread did not finish cleanly during shutdown.")

    if _audio_stream:
        try:
            if _audio_stream.is_active(): _audio_stream.stop_stream()
            _audio_stream.close()
            logger.info("Audio stream closed during shutdown.")
        except Exception as e:
            logger.warning(f"Error closing audio stream during exit: {e}", exc_info=False)
        _audio_stream = None
    if _pyaudio_instance:
        try:
            _pyaudio_instance.terminate()
            logger.info("PyAudio instance terminated during shutdown.")
        except Exception as e:
            logger.warning(f"Error terminating PyAudio instance during exit: {e}", exc_info=False)
        _pyaudio_instance = None
    logger.info("Audio resources shutdown complete.")