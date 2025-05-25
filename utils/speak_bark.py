# utils/speak_bark.py
#!/usr/bin/env python3
"""
Text-to-Speech using Hugging Face Bark Model
Supports long text input by chunking and playing audio progressively.
Supports CUDA. Script remains active and interruptible during playback.
"""

import torch # Keep torch import here for early checks if needed by BarkModel itself
from transformers import AutoProcessor, BarkModel # Keep these here for BarkTTS class
import numpy as np
import os
import threading
import queue
import time
import config
import logging # Added for standalone test logger setup

# Assuming logger.py is in the same 'utils' directory
from logger import get_logger

logger = get_logger(__name__)

# --- NLTK Setup ---
import nltk
from nltk.tokenize import sent_tokenize # <<< *** ADDED THIS LINE ***
try:
    nltk.data.find('tokenizers/punkt')
    logger.info("NLTK 'punkt' tokenizer found.")
except LookupError as e:
    missing_resource_name = "punkt"
    try:
        error_lines = str(e).splitlines()
        for line_content in error_lines:
            if "Resource" in line_content and "not found" in line_content:
                parts = line_content.strip().split(" ")
                missing_resource_name = parts[1] if len(parts) > 1 else "punkt"
                break
    except Exception:
        pass # Keep default missing_resource_name
    logger.warning(f"NLTK resource '{missing_resource_name}' not found. Attempting to download 'punkt'...")
    try:
        nltk.download('punkt', quiet=True)
        nltk.data.find('tokenizers/punkt') # Verify after download
        logger.info(f"NLTK 'punkt' tokenizer downloaded successfully.")
    except Exception as download_exc:
        logger.error(f"Failed to download NLTK 'punkt' automatically: {download_exc}", exc_info=True)
        logger.error("Please try downloading 'punkt' manually in a Python interpreter:\n"
                     ">>> import nltk\n"
                     ">>> nltk.download('punkt')")
        # Potentially raise an error or set a flag if NLTK is critical and download fails
# --- End NLTK Setup ---

# --- SoundDevice Setup ---
SD_AVAILABLE = False
sd = None
try:
    import sounddevice as sd_module
    sd = sd_module # Assign to sd for use in the rest of the file
    SD_AVAILABLE = True
    logger.info("SoundDevice library imported successfully.")
except ImportError:
    logger.warning("SoundDevice library not found. Please install it: pip install sounddevice")
    logger.warning("Audio playback will not be available via BarkTTS.")
    sd = None # Ensure sd is None
    SD_AVAILABLE = False
except Exception as e: # Catch other potential errors during sounddevice import
    logger.error(f"Error importing SoundDevice: {e}", exc_info=True)
    sd = None
    SD_AVAILABLE = False
# --- End SoundDevice Setup ---


class BarkTTS:
    def __init__(self, processor, model, device, voice_preset="v2/en_speaker_6"):
        self.processor = processor
        self.model = model
        self.device = device
        self.voice_preset = voice_preset
        logger.debug(f"BarkTTS instance created. Voice: {voice_preset}, Device: {device}")

    def synthesize_speech_to_array(self, text, generation_params=None):
        try:
            current_voice_preset = generation_params.pop("voice_preset", self.voice_preset) if generation_params else self.voice_preset
            logger.debug(f"Synthesizing chunk with Bark. Text: '{text[:50]}...', Voice: {current_voice_preset}")

            inputs = self.processor(text, voice_preset=current_voice_preset, return_tensors="pt")
            inputs = {k: v.to(self.device) for k, v in inputs.items()}

            effective_params = {
                "do_sample": config.BARK_DO_SAMPLE,
                "fine_temperature": config.BARK_FINE_TEMPERATURE,
                "coarse_temperature": config.BARK_COARSE_TEMPERATURE
            }
            if generation_params:
                effective_params.update(generation_params)
            logger.debug(f"Bark generation params: {effective_params}")

            with torch.no_grad():
                speech_output = self.model.generate(**inputs, **effective_params)

            audio_array = speech_output.cpu().numpy().squeeze()
            samplerate = self.model.generation_config.sample_rate

            if audio_array.ndim > 1 and audio_array.shape[0] > 1:
                logger.debug("Multiple audio channels in output, selecting first.")
                audio_array = audio_array[0]
            elif audio_array.ndim == 0:
                logger.warning("Speech output from Bark is scalar (empty or error). Returning None.")
                return None, None
            logger.debug(f"Synthesized audio chunk. Samplerate: {samplerate}, Duration: {len(audio_array)/samplerate:.2f}s")
            return audio_array, samplerate
        except Exception as e:
            logger.error(f"Error synthesizing speech chunk with Bark: {e}. Text: '{text[:50]}...'", exc_info=True)
            return None, None


class StreamingBarkTTS:
    def __init__(self, bark_tts_instance, max_sentences_per_chunk=None, silence_duration_ms=None):
        self.bark_tts = bark_tts_instance
        self.max_sentences_per_chunk = max_sentences_per_chunk if max_sentences_per_chunk is not None else config.BARK_MAX_SENTENCES_PER_CHUNK
        self.silence_duration_ms = silence_duration_ms if silence_duration_ms is not None else config.BARK_SILENCE_DURATION_MS
        self.audio_queue = queue.Queue(maxsize=10) # Max 10 chunks in queue
        self.current_on_playback_start_callback = None
        logger.debug("StreamingBarkTTS instance created.")

    def _chunk_text(self, text):
        lang_for_nltk = 'english'
        if self.bark_tts.voice_preset and 'ru_' in self.bark_tts.voice_preset.lower():
            lang_for_nltk = 'russian'
        logger.debug(f"Chunking text for NLTK language: {lang_for_nltk}")

        try:
            # sent_tokenize IS NOW CORRECTLY IMPORTED AND USED
            sentences = sent_tokenize(text, language=lang_for_nltk)
        except LookupError:
            logger.warning(f"NLTK punkt data for '{lang_for_nltk}' not found during chunking, falling back to English tokenization.")
            sentences = sent_tokenize(text, language='english') # Fallback
        except Exception as e:
            logger.error(f"Error during NLTK sentence tokenization: {e}. Treating full text as one chunk.", exc_info=True)
            return [text] # Fallback: treat the whole text as a single chunk

        chunks = []; current_chunk_sentences = []
        for i, sentence in enumerate(sentences):
            current_chunk_sentences.append(sentence)
            if len(current_chunk_sentences) >= self.max_sentences_per_chunk or (i + 1) == len(sentences):
                chunks.append(" ".join(current_chunk_sentences))
                current_chunk_sentences = []
        logger.debug(f"Text chunked into {len(chunks)} parts.")
        return chunks

    # ... (rest of the StreamingBarkTTS class and __main__ block remains the same as the previously refactored version)
    def _synthesis_worker(self, full_text, stop_event: threading.Event, generation_params=None):
        thread_name = threading.current_thread().name
        logger.info(f"Bark Synthesis Worker ({thread_name}) started for text: '{full_text[:70]}...'")
        text_chunks = self._chunk_text(full_text)
        if not text_chunks:
            logger.error(f"Bark TTS ({thread_name}): Text could not be chunked or was empty. Aborting synthesis.")
            self.audio_queue.put(None) # Signal playback to terminate
            return

        first_chunk = True
        for i, chunk_text in enumerate(text_chunks):
            if stop_event.is_set():
                logger.info(f"Bark TTS ({thread_name}): Stop event detected, breaking synthesis loop (chunk {i+1}/{len(text_chunks)}).")
                break

            logger.debug(f"Bark TTS ({thread_name}): Synthesizing chunk {i+1}/{len(text_chunks)}: '{chunk_text[:50]}...'")
            audio_array, samplerate = self.bark_tts.synthesize_speech_to_array(
                chunk_text, generation_params=generation_params.copy() if generation_params else None # Pass a copy
            )

            if stop_event.is_set():
                logger.info(f"Bark TTS ({thread_name}): Stop event detected after synthesis of chunk {i+1}, before queueing.")
                break

            if audio_array is not None and samplerate is not None:
                if not first_chunk and self.silence_duration_ms > 0:
                    silence_samples = int(self.silence_duration_ms / 1000 * samplerate)
                    silence_array = np.zeros(silence_samples, dtype=audio_array.dtype)
                    logger.debug(f"Bark TTS ({thread_name}): Adding {self.silence_duration_ms}ms silence to queue.")
                    try:
                        self.audio_queue.put((silence_array, samplerate), timeout=2)
                    except queue.Full:
                        if stop_event.is_set():
                            logger.info(f"Bark TTS ({thread_name}): Stop event during queue.Full for silence. Breaking.")
                            break
                        logger.warning(f"Bark TTS ({thread_name}): Audio queue full while trying to put silence. Synthesis might be too fast or playback stalled. Skipping silence.")
                        continue # Try to put the main audio next

                logger.debug(f"Bark TTS ({thread_name}): Putting audio chunk {i+1} into queue.")
                try:
                    self.audio_queue.put((audio_array, samplerate), timeout=2)
                except queue.Full:
                    if stop_event.is_set():
                        logger.info(f"Bark TTS ({thread_name}): Stop event during queue.Full for audio chunk {i+1}. Breaking.")
                        break
                    logger.warning(f"Bark TTS ({thread_name}): Audio queue full while trying to put chunk {i+1}. Synthesis might be too fast or playback stalled. Skipping chunk.")
                    continue
                first_chunk = False
            else:
                 logger.warning(f"Bark TTS ({thread_name}): Failed to synthesize chunk {i+1}: '{chunk_text[:30]}...'")

        logger.info(f"Bark TTS ({thread_name}): Synthesis loop finished. Placing sentinel in queue.")
        try:
            self.audio_queue.put(None, timeout=1) # Sentinel to signal end of audio
        except queue.Full:
            if not stop_event.is_set(): # Only warn if not already stopping
                 logger.warning(f"Bark TTS ({thread_name}): Could not place sentinel in queue (full). Playback might not terminate cleanly if not already stopped.")
        logger.info(f"Bark Synthesis Worker ({thread_name}) finished.")


    def _playback_worker(self, stop_event: threading.Event):
        if not SD_AVAILABLE or not sd: # Check sd explicitly
            logger.error("Playback worker cannot start: SoundDevice not available.")
            return

        thread_name = threading.current_thread().name
        logger.info(f"Bark Playback Worker ({thread_name}) started.")
        stream_active = False
        try:
            while not stop_event.is_set():
                try:
                    item = self.audio_queue.get(timeout=0.2) # Check queue periodically
                except queue.Empty:
                    continue # No audio yet, or synthesis is slow

                if item is None: # Sentinel found
                    logger.info(f"Bark TTS ({thread_name}): Sentinel received. Ending playback.")
                    break

                if stop_event.is_set(): # Check stop event after getting item
                    logger.info(f"Bark TTS ({thread_name}): Stop event detected while item in queue. Discarding.")
                    self.audio_queue.task_done() # Mark item as processed
                    break

                audio_array, samplerate = item

                # This check might be redundant if None items are only sentinels, but good for safety
                if audio_array is None or samplerate is None:
                    logger.warning(f"Bark TTS ({thread_name}): Received None audio_array or samplerate unexpectedly. Skipping.")
                    self.audio_queue.task_done()
                    continue

                if not stream_active:
                    try:
                        device_info = sd.query_devices(kind='output')
                        logger.info(f"Bark TTS ({thread_name}): Playback starting. Samplerate: {samplerate}, Device: {device_info['name']}")
                    except Exception as e:
                        logger.warning(f"Bark TTS ({thread_name}): Could not query audio device: {e}. Samplerate: {samplerate}", exc_info=False)

                    if self.current_on_playback_start_callback:
                        logger.debug(f"Bark TTS ({thread_name}): Calling on_playback_start_callback.")
                        try:
                            self.current_on_playback_start_callback()
                        except Exception as cb_exc:
                            logger.error(f"Bark TTS ({thread_name}): Error in on_playback_start_callback: {cb_exc}", exc_info=True)
                    stream_active = True

                logger.debug(f"Bark TTS ({thread_name}): Playing audio chunk. Duration: {len(audio_array)/samplerate:.2f}s")
                try:
                    if not stop_event.is_set(): # Double check before actual play
                        sd.play(audio_array, samplerate)
                        sd.wait() # Wait for this chunk to finish playing
                    else:
                        logger.info(f"Bark TTS ({thread_name}): Stop event set before playing chunk. Stopping playback.")
                        sd.stop() # Attempt to stop any ongoing sound
                        break
                except Exception as e_play:
                    logger.error(f"Bark TTS ({thread_name}): Error during sd.play/wait: {e_play}", exc_info=True)
                    if stop_event.is_set(): break # Exit loop if already stopping
                finally:
                    self.audio_queue.task_done() # Mark item as processed

        except Exception as e: # Catch-all for unexpected errors in the playback loop
            logger.error(f"Bark TTS ({thread_name}): Unhandled error in playback worker: {e}", exc_info=True)
        finally:
            if stream_active and sd:
                logger.info(f"Bark TTS ({thread_name}): Playback worker finishing. Stopping any active sound.")
                sd.stop() # Ensure sound is stopped on exit
            logger.info(f"Bark Playback Worker ({thread_name}) finished.")

    def synthesize_and_play_stream(self, full_text, stop_event: threading.Event, generation_params=None, on_playback_start_callback=None):
        if not SD_AVAILABLE or not sd:
            logger.error(f"Cannot synthesize and play: SoundDevice not available. Text: '{full_text[:50]}...'")
            if stop_event.is_set():
                 logger.info(f"Speech requested for \"{full_text[:50]}...\" but stop_event already set and no audio device.")
            # If a callback was provided, it might expect to be called if TTS was supposed to start
            if on_playback_start_callback:
                logger.debug("SoundDevice unavailable, attempting to call on_playback_start_callback as a fallback.")
                try: on_playback_start_callback()
                except Exception as cb_exc: logger.error(f"Error in fallback on_playback_start_callback: {cb_exc}", exc_info=True)
            return

        self.current_on_playback_start_callback = on_playback_start_callback
        logger.info(f"Starting TTS stream for: '{full_text[:70]}...'")

        synthesis_thread = threading.Thread(
            target=self._synthesis_worker,
            args=(full_text, stop_event, generation_params),
            daemon=True,
            name="BarkSynthesisWorker"
        )
        playback_thread = threading.Thread(
            target=self._playback_worker,
            args=(stop_event,),
            daemon=True,
            name="BarkPlaybackWorker"
        )

        synthesis_thread.start()
        playback_thread.start()

        # Wait for threads to complete or stop_event to be set
        # This loop primarily ensures the main calling thread waits if needed.
        # The actual work is in the worker threads.
        while synthesis_thread.is_alive() or playback_thread.is_alive():
            if stop_event.is_set():
                logger.info("Main stream control: Stop event detected. Waiting for threads to join.")
                break
            time.sleep(0.05) # Brief sleep to avoid busy-waiting

        logger.debug("Main stream control: Joining synthesis thread...")
        synthesis_thread.join(timeout=3)
        if synthesis_thread.is_alive():
            logger.warning("Main stream control: Synthesis thread did not join cleanly after 3s.")

        logger.debug("Main stream control: Joining playback thread...")
        playback_thread.join(timeout=3) # Playback might take time to finish if sd.wait() is long
        if playback_thread.is_alive():
            logger.warning("Main stream control: Playback thread did not join cleanly after 3s.")

        if stop_event.is_set() and sd: # If stopped, ensure sd is stopped
            logger.info("Main stream control: Stop event was set, ensuring sounddevice is stopped.")
            sd.stop()

        # Clear the queue (important if synthesis was faster than stop or if errors occurred)
        logger.debug("Main stream control: Clearing any remaining items from audio queue.")
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break
            self.audio_queue.task_done()

        self.current_on_playback_start_callback = None # Clear callback

        if not stop_event.is_set():
            logger.info(f"Finished speaking \"{full_text[:50]}...\"")
        else:
            logger.info(f"Speech stopped for \"{full_text[:50]}...\" due to stop event.")


# Standalone test functionality
if __name__ == "__main__":
    # For standalone test, configure a basic console logger if main app logger isn't running
    # This is just for the __main__ block. The module itself uses the get_logger.
    if not logger.hasHandlers(): # Check if our module logger already has handlers (e.g. if imported elsewhere)
        test_logger_handler = logging.StreamHandler()
        test_logger_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s')
        test_logger_handler.setFormatter(test_logger_formatter)
        logging.getLogger("utils.speak_bark").addHandler(test_logger_handler) # Add to our module's logger
        logging.getLogger("utils.speak_bark").setLevel(logging.DEBUG) # Set level for test
        # Also, if the root logger for the whole 'utils' package or 'Iri-shka_App' isn't set up,
        # this ensures utils.speak_bark gets output.

    logger.info("=== Bark TTS: Streaming Long Text Showcase (Standalone Test) ===")
    logger.info("-" * 70)

    if not SD_AVAILABLE or not sd:
        logger.critical("SoundDevice library is not installed or failed to import. Progressive playback test will not work.")
        exit(1)

    model_name_choice = config.BARK_MODEL_NAME
    device_choice = "cuda" if torch.cuda.is_available() else "cpu"

    logger.info(f"Loading base Bark model: {model_name_choice} onto device: {device_choice} for test...")
    try:
        test_processor = AutoProcessor.from_pretrained(model_name_choice)
        test_model = BarkModel.from_pretrained(model_name_choice).to(device_choice)
        logger.info("Base Bark Model and Processor loaded successfully for test!")
    except Exception as e:
        logger.critical(f"Error loading base Bark model/processor for test: {e}", exc_info=True)
        exit(1)

    # Use BARK_VOICE_PRESET_EN from config as default for testing
    test_voice_preset = config.BARK_VOICE_PRESET_EN
    long_test_text_en = (
        "Hello, this is a test of the Bark text-to-speech system. "
        "It should be able to handle long inputs by chunking the text into sentences "
        "and synthesizing them one by one. Let's see how well it performs with a slightly longer text. "
        "The audio should play progressively. You can press Ctrl+C to interrupt the speech."
    )
    long_test_text_ru = (
        "Привет, это тест системы преобразования текста в речь Bark. "
        "Она должна справляться с длинными текстами, разбивая их на предложения "
        "и синтезируя по очереди. Посмотрим, как она справится с немного более длинным текстом. "
        "Аудио должно воспроизводиться постепенно. Вы можете нажать Ctrl+C, чтобы прервать речь."
    )

    # Decide which text to use based on the preset (simple check)
    if 'ru_' in test_voice_preset.lower():
        long_test_text = long_test_text_ru
        logger.info(f"Using Russian test text for voice preset: {test_voice_preset}")
    else:
        long_test_text = long_test_text_en
        logger.info(f"Using English test text for voice preset: {test_voice_preset}")


    base_tts_engine = BarkTTS(test_processor, test_model, device_choice, test_voice_preset)

    streamer = StreamingBarkTTS(
        bark_tts_instance=base_tts_engine,
    )

    test_generation_params = {
        "do_sample": config.BARK_DO_SAMPLE,
        "fine_temperature": config.BARK_FINE_TEMPERATURE,
        "coarse_temperature": config.BARK_COARSE_TEMPERATURE,
        # "voice_preset": test_voice_preset # This will be handled by BarkTTS instance's default or overridden
    }

    external_stop_event = threading.Event()

    def test_playback_start_hook():
        logger.info("\n\n>>>> TEST HOOK: Playback actually started! <<<<\n\n")

    def run_tts_test():
        streamer.synthesize_and_play_stream(
            full_text=long_test_text,
            stop_event=external_stop_event,
            generation_params=test_generation_params,
            on_playback_start_callback=test_playback_start_hook
        )

    tts_test_thread = threading.Thread(target=run_tts_test, name="TestTTSStreamThread")

    logger.info(f"\n--- Starting Progressive Playback Test (Speaker: {test_voice_preset}) ---")
    logger.info("Press Ctrl+C to signal stop to the TTS thread.")
    tts_test_thread.start()

    try:
        while tts_test_thread.is_alive():
            tts_test_thread.join(timeout=0.1)
    except KeyboardInterrupt:
        logger.info("\nMain Test: KeyboardInterrupt received. Signaling TTS thread to stop...")
        external_stop_event.set()

    tts_test_thread.join(timeout=5)
    if tts_test_thread.is_alive():
        logger.warning("Main Test: TTS thread still alive after timeout. Forcing stop event again.")
        external_stop_event.set() # Ensure it's set
        tts_test_thread.join(timeout=2) # Another short wait

    logger.info("\n" + "="*70)
    logger.info("Main test function has completed.")

    del test_model
    del test_processor
    if device_choice == "cuda" and torch and hasattr(torch.cuda, "empty_cache"):
        torch.cuda.empty_cache()
        logger.info("Test resources (model, processor) released and CUDA cache cleared.")
    else:
        logger.info("Test resources (model, processor) released.")