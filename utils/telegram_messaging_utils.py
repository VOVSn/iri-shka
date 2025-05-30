# utils/telegram_messaging_utils.py
import os
import datetime
import asyncio
import numpy as np
import soundfile as sf
import nltk # For sentence tokenization

import config
from logger import get_logger
from utils import file_utils

logger = get_logger("Iri-shka_App.utils.TelegramMessagingUtils")

_PydubAudioSegment = None
_PydubExceptions = None

def initialize_telegram_audio_dependencies(pydub_audio_segment_class, pydub_exceptions_class):
    global _PydubAudioSegment, _PydubExceptions
    _PydubAudioSegment = pydub_audio_segment_class
    _PydubExceptions = pydub_exceptions_class
    if _PydubAudioSegment: logger.info("Pydub reference received in TelegramMessagingUtils.")
    else: logger.warning("Pydub reference not received; OGG conversion will fail.")

def send_voice_reply_to_telegram_user(
    target_user_id: int, text_to_speak: str, bark_voice_preset: str,
    telegram_bot_handler_instance_ref, tts_manager_module_ref
    ):
    # ... (content from thought process, ensure all refs are used)
    if not (telegram_bot_handler_instance_ref and telegram_bot_handler_instance_ref.async_loop and
            tts_manager_module_ref.is_tts_ready() and _PydubAudioSegment and nltk and np and sf):
        missing = [] # ... build missing list ...
        if not tts_manager_module_ref.is_tts_ready(): missing.append("TTS")
        if not _PydubAudioSegment: missing.append("Pydub")
        if not nltk: missing.append("NLTK")
        logger.warning(f"Cannot send voice reply to {target_user_id}: Missing ({', '.join(missing)}). Text: '{text_to_speak[:30]}'")
        return

    ts_suffix = datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')
    file_utils.ensure_folder(config.TELEGRAM_TTS_TEMP_FOLDER)
    temp_tts_merged_wav_path = os.path.join(config.TELEGRAM_TTS_TEMP_FOLDER, f"tts_u{target_user_id}_merged_{ts_suffix}.wav")
    temp_tts_ogg_path = os.path.join(config.TELEGRAM_TTS_TEMP_FOLDER, f"tts_u{target_user_id}_reply_{ts_suffix}.ogg")

    bark_tts_engine = tts_manager_module_ref.get_bark_model_instance()
    if not bark_tts_engine: logger.error(f"No Bark instance for user {target_user_id}."); return

    lang_for_nltk = 'english' if 'en_' in bark_voice_preset.lower() else 'russian'
    try: nltk.data.find(f'tokenizers/punkt/{lang_for_nltk}.pickle')
    except LookupError: # ... download punkt ...
        try: nltk.download('punkt', quiet=True)
        except Exception as e_nltk_dl: logger.error(f"Failed to download NLTK 'punkt' for {lang_for_nltk}: {e_nltk_dl}.")
    
    try: sentences = nltk.sent_tokenize(text_to_speak, language=lang_for_nltk)
    except Exception as e_nltk_sent: sentences = [text_to_speak]; logger.warning(f"NLTK tokenization failed: {e_nltk_sent}.")
    
    text_chunks = []; current_batch = [] # ... chunk sentences ...
    for i, s in enumerate(sentences):
        current_batch.append(s)
        if len(current_batch) >= config.BARK_MAX_SENTENCES_PER_CHUNK or (i + 1) == len(sentences):
            text_chunks.append(" ".join(current_batch)); current_batch = []
    
    all_audio_pieces = []; target_sr = None; first_valid_chunk = False # ... synthesize and gather audio pieces ...
    for idx, chunk_text in enumerate(text_chunks):
        audio_arr, sr = bark_tts_engine.synthesize_speech_to_array(chunk_text, {"voice_preset": bark_voice_preset})
        if audio_arr is not None and sr is not None and audio_arr.size > 0: 
            if target_sr is None: target_sr = sr
            if sr != target_sr: logger.warning(f"SR mismatch (exp {target_sr}, got {sr}). Skip."); continue
            if first_valid_chunk and config.BARK_SILENCE_DURATION_MS > 0:
                all_audio_pieces.append(np.zeros(int(config.BARK_SILENCE_DURATION_MS / 1000 * target_sr), dtype=audio_arr.dtype))
            all_audio_pieces.append(audio_arr); first_valid_chunk = True
        else: logger.warning(f"TTS failed for chunk {idx} for user {target_user_id}")
    
    if all_audio_pieces and target_sr is not None: # ... merge, convert to OGG, send ...
        merged_audio = np.concatenate(all_audio_pieces)
        try:
            sf.write(temp_tts_merged_wav_path, merged_audio, target_sr) 
            pydub_seg = _PydubAudioSegment.from_wav(temp_tts_merged_wav_path).set_frame_rate(16000).set_channels(1)
            pydub_seg.export(temp_tts_ogg_path, format="ogg", codec="libopus", bitrate="24k") 
            
            if hasattr(telegram_bot_handler_instance_ref, 'send_voice_message_to_user'):
                send_future = asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance_ref.send_voice_message_to_user(target_user_id, temp_tts_ogg_path), telegram_bot_handler_instance_ref.async_loop) 
                if send_future: send_future.result(timeout=20); logger.info(f"Voice reply sent to {target_user_id}")
            else: logger.error("TelegramBotHandler missing 'send_voice_message_to_user'.")
        except Exception as e_send_v: logger.error(f"Error processing/sending voice to {target_user_id}: {e_send_v}", exc_info=True)
    else: logger.error(f"No valid audio for {target_user_id}. Cannot send voice.")
    
    for f_path in [temp_tts_merged_wav_path, temp_tts_ogg_path]: # ... cleanup temp files ...
        if os.path.exists(f_path):
            try: os.remove(f_path)
            except OSError as e: logger.warning(f"Could not remove temp TTS file {f_path}: {e}")