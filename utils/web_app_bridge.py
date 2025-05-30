# utils/web_app_bridge.py
import os
import re
import json
import uuid
import threading # For lock type hinting
import numpy as np
import soundfile as sf

from logger import get_logger
import config # For BARK presets, folder paths etc.
from utils import file_utils # For ensure_folder

web_logger = get_logger("Iri-shka_App.utils.WebAppBridge") # Logger for this specific module

class WebAppBridge:
    def __init__(self,
                 main_app_ollama_ready_flag_getter,
                 main_app_status_label_getter_fn,
                 whisper_handler_module,
                 ollama_handler_module,
                 tts_manager_module,
                 _whisper_module_for_load_audio_ref, # Reference to whisper.load_audio
                 state_manager_module_ref, # For customer context loading
                 gui_callbacks_ref, # For customer context loading if it needs gui_callbacks
                 fn_check_webui_health_main # New: function from main.py to check WebUI health
                ):
        self.get_ollama_ready = main_app_ollama_ready_flag_getter
        self.get_main_app_status_label = main_app_status_label_getter_fn
        self.whisper_handler_module = whisper_handler_module
        self.ollama_handler_module = ollama_handler_module
        self.tts_manager_module = tts_manager_module
        self._whisper_module_for_load_audio = _whisper_module_for_load_audio_ref
        self.state_manager_module = state_manager_module_ref
        self.gui_callbacks = gui_callbacks_ref # Primarily for state_manager if it uses them
        self.fn_check_webui_health_main = fn_check_webui_health_main # Store the health check function
        self.telegram_handler_instance_ref = None # To be set by main.py after TelegramBotHandler is initialized
        web_logger.info("WebAppBridge initialized.")

    def process_admin_web_audio(self,
                                input_wav_filepath: str,
                                current_chat_history: list, # Snapshot from main
                                current_user_state: dict,   # Snapshot from main (admin's state)
                                current_assistant_state: dict, # Snapshot from main
                                global_lock: threading.Lock # Main app's lock (currently unused here, but good for future)
                                ):
        web_logger.info(f"WebAppBridge: Processing ADMIN web audio: {input_wav_filepath}")
        
        result_data = {
            "user_transcription": None,
            "llm_text_response": None,
            "tts_audio_filename": None,
            "error_message": None,
            "updated_user_state": None, # To be filled by LLM
            "updated_assistant_state": None, # To be filled by LLM
            "updated_active_customer_state": None, # To be filled by LLM
            "new_chat_turn": None, # To be constructed for main.py to append
            "detected_language_for_tts": "en" # Default, will be updated
        }
        
        detected_lang_from_stt = None
        
        if not self.whisper_handler_module.is_whisper_ready():
            result_data["error_message"] = "Whisper (STT) service not ready."
            web_logger.error(f"WebAppBridge-Admin: STT Aborted - {result_data['error_message']}")
            return result_data
        
        try:
            web_logger.debug(f"WebAppBridge-Admin: Attempting to load audio for STT from: {input_wav_filepath}")
            if not self._whisper_module_for_load_audio:
                 result_data["error_message"] = "Whisper 'load_audio' utility not available in bridge."
                 raise RuntimeError(result_data["error_message"])

            audio_np_array_web_admin = self._whisper_module_for_load_audio.load_audio(input_wav_filepath)
            web_logger.debug(f"WebAppBridge-Admin: Audio loaded for STT. Shape: {audio_np_array_web_admin.shape if audio_np_array_web_admin is not None else 'None'}")
            
            transcribed_text, trans_err, detected_lang_from_stt = self.whisper_handler_module.transcribe_audio(
                audio_np_array=audio_np_array_web_admin, language=None, task="transcribe"
            )

            if trans_err:
                result_data["error_message"] = f"Admin Web Transcription error: {trans_err}"
                web_logger.error(f"WebAppBridge-Admin: STT Error - {result_data['error_message']}")
                return result_data
            
            if not transcribed_text: # No speech detected
                web_logger.info("WebAppBridge-Admin: No speech detected from web input.")
                result_data["user_transcription"] = "" # Indicate silence to frontend
                # No LLM call if no transcription
                return result_data
            
            result_data["user_transcription"] = transcribed_text
            web_logger.info(f"WebAppBridge-Admin: Transcription successful: '{transcribed_text[:70]}...', Detected Lang: {detected_lang_from_stt}")

        except Exception as e_stt:
            result_data["error_message"] = f"Admin Web STT processing failed: {str(e_stt)}"
            web_logger.error(f"WebAppBridge-Admin: STT Exception - {result_data['error_message']}", exc_info=True)
            return result_data

        # Proceed to LLM if transcription was successful
        if not self.get_ollama_ready():
            result_data["error_message"] = "Ollama (LLM) service not ready for admin web input."
            web_logger.error(f"WebAppBridge-Admin: LLM Aborted - {result_data['error_message']}")
            return result_data
        
        try:
            # Use copies of states passed from main for this interaction
            assistant_state_snapshot_for_prompt = current_assistant_state.copy()
            user_state_snapshot_for_prompt = current_user_state.copy()
            chat_history_snapshot_for_prompt = current_chat_history[:] # Shallow copy is fine for list of dicts

            # Determine language for LLM response and subsequent TTS
            current_lang_code_for_state = "en"
            if detected_lang_from_stt and detected_lang_from_stt in ["ru", "en"]:
                current_lang_code_for_state = detected_lang_from_stt
            elif assistant_state_snapshot_for_prompt.get("last_used_language") in ["ru", "en"]:
                current_lang_code_for_state = assistant_state_snapshot_for_prompt.get("last_used_language")
            result_data["detected_language_for_tts"] = current_lang_code_for_state


            language_instruction_for_llm = config.LANGUAGE_INSTRUCTION_RUSSIAN if current_lang_code_for_state == "ru" \
                                           else config.LANGUAGE_INSTRUCTION_NON_RUSSIAN
            web_logger.debug(f"WebAppBridge-Admin: Language for LLM & TTS: {current_lang_code_for_state}")

            # --- Customer Context Detection ---
            target_customer_id_for_prompt = None
            customer_state_for_prompt_str = "{}"
            is_customer_context_active_for_prompt = False
            # Scan recent history for a customer summary to activate context
            history_to_scan = chat_history_snapshot_for_prompt[-(config.MAX_HISTORY_TURNS // 2 or 1):]
            for turn in reversed(history_to_scan):
                assistant_msg_hist = turn.get("assistant", "")
                turn_source_hist = turn.get("source", "")
                if turn_source_hist == "customer_summary_internal":
                    match_summary = re.search(r"\[Сводка по клиенту (\d+)\]", assistant_msg_hist)
                    if match_summary:
                        try: target_customer_id_for_prompt = int(match_summary.group(1)); break
                        except ValueError: target_customer_id_for_prompt = None
            
            if target_customer_id_for_prompt:
                # Load customer state using state_manager (passed as ref)
                loaded_customer_state = self.state_manager_module.load_or_initialize_customer_state(
                    target_customer_id_for_prompt, self.gui_callbacks # Pass GUI callbacks if state_manager uses them
                )
                if loaded_customer_state and loaded_customer_state.get("user_id") == target_customer_id_for_prompt:
                    customer_state_for_prompt_str = json.dumps(loaded_customer_state, indent=2, ensure_ascii=False)
                    is_customer_context_active_for_prompt = True
                else:
                    target_customer_id_for_prompt = None # Reset if loading failed or ID mismatch
            # --- End Customer Context Detection ---

            assistant_state_for_this_prompt = assistant_state_snapshot_for_prompt.copy()
            assistant_state_for_this_prompt["last_used_language"] = current_lang_code_for_state # Inform LLM of current language context
            admin_current_name = assistant_state_for_this_prompt.get("admin_name", "Partner")

            format_kwargs_for_ollama = {
                "admin_name_value": admin_current_name,
                "assistant_admin_name_current_value": admin_current_name,
                "is_customer_context_active": is_customer_context_active_for_prompt,
                "active_customer_id": str(target_customer_id_for_prompt) if target_customer_id_for_prompt else "N/A",
                "active_customer_state_string": customer_state_for_prompt_str,
            }
            expected_keys_for_response = ["answer_to_user", "updated_user_state", "updated_assistant_state", "updated_active_customer_state"]

            web_logger.info(f"WebAppBridge-Admin: Calling Ollama with admin prompt. Input: '{transcribed_text[:50]}...'")
            ollama_data, ollama_error = self.ollama_handler_module.call_ollama_for_chat_response(
                prompt_template_to_use=config.OLLAMA_PROMPT_TEMPLATE,
                transcribed_text=transcribed_text,
                current_chat_history=chat_history_snapshot_for_prompt,
                current_user_state=user_state_snapshot_for_prompt,
                current_assistant_state=assistant_state_for_this_prompt,
                language_instruction=language_instruction_for_llm,
                format_kwargs=format_kwargs_for_ollama,
                expected_keys_override=expected_keys_for_response,
                gui_callbacks=None # Bridge doesn't directly update GUI during LLM call
            )

            # Construct the new chat turn object to be returned
            current_turn_for_history_obj = {"user": transcribed_text, "source": "web_admin"}
            if detected_lang_from_stt:
                current_turn_for_history_obj["detected_language_code_for_web_display"] = detected_lang_from_stt
            
            if ollama_error:
                web_logger.error(f"WebAppBridge-Admin: Ollama call failed: {ollama_error}")
                result_data["error_message"] = (result_data["error_message"] or "") + f" LLM error: {ollama_error}"
                
                err_lang = assistant_state_snapshot_for_prompt.get("last_used_language", "en")
                ollama_response_text_for_web = "An internal error occurred (LLM)." if err_lang == "en" \
                                           else "Произошла внутренняя ошибка (LLM)."
                current_turn_for_history_obj["assistant"] = f"[LLM Error from Web UI: {ollama_response_text_for_web}]"
                result_data["llm_text_response"] = ollama_response_text_for_web # Set for display
            else:
                web_logger.info("WebAppBridge-Admin: Ollama call successful. Preparing states for main.py.")
                result_data["llm_text_response"] = ollama_data.get("answer_to_user", "Error: LLM did not provide an answer.")
                result_data["updated_user_state"] = ollama_data.get("updated_user_state", {})
                # Ensure language of response is set in assistant state update
                new_assistant_state_changes_from_llm = ollama_data.get("updated_assistant_state", {})
                new_assistant_state_changes_from_llm["last_used_language"] = current_lang_code_for_state # Ensure it's part of the update
                result_data["updated_assistant_state"] = new_assistant_state_changes_from_llm

                result_data["updated_active_customer_state"] = ollama_data.get("updated_active_customer_state")
                # Ensure customer ID in updated_active_customer_state if present
                if result_data["updated_active_customer_state"] and isinstance(result_data["updated_active_customer_state"], dict) and \
                   "user_id" not in result_data["updated_active_customer_state"] and target_customer_id_for_prompt:
                    result_data["updated_active_customer_state"]["user_id"] = target_customer_id_for_prompt

                current_turn_for_history_obj["assistant"] = result_data["llm_text_response"]
            
            result_data["new_chat_turn"] = current_turn_for_history_obj

        except Exception as e_llm_admin_web:
            result_data["error_message"] = (result_data["error_message"] or "") + f" Admin Web LLM processing/state update failed: {str(e_llm_admin_web)}"
            web_logger.error(f"WebAppBridge-Admin: LLM/State Exception - {result_data['error_message']}", exc_info=True)
            # If LLM text response wasn't set due to this error, provide a generic one
            if not result_data["llm_text_response"]:
                err_lang_state = current_assistant_state.get("last_used_language", "en") # Use original pre-call state
                result_data["llm_text_response"] = "An internal error occurred during LLM/state processing." if err_lang_state == "en" \
                                               else "Внутренняя ошибка при обработке LLM/состояния."

        # Proceed to TTS if there's an LLM response and no critical error prevented it
        if not result_data["llm_text_response"] or result_data["error_message"]: # Check for explicit error or no response
            web_logger.info(f"WebAppBridge-Admin: Skipping TTS due to LLM error ('{result_data['error_message']}') or no LLM response.")
            return result_data

        if not self.tts_manager_module.is_tts_ready():
            web_logger.warning("WebAppBridge-Admin: TTS service not ready. Returning text only for admin web.")
            return result_data # Return text response even if TTS not ready
        
        try:
            web_logger.debug(f"WebAppBridge-Admin: Preparing to synthesize TTS for: '{result_data['llm_text_response'][:70]}...'")
            bark_engine_for_web_admin = self.tts_manager_module.get_bark_model_instance()
            if not bark_engine_for_web_admin:
                web_logger.error("WebAppBridge-Admin: Failed to get Bark TTS engine instance.")
                return result_data # Return text if engine fails

            # Use the language determined for the LLM response for TTS
            tts_voice_preset_to_use = config.BARK_VOICE_PRESET_RU if result_data["detected_language_for_tts"] == "ru" \
                                                               else config.BARK_VOICE_PRESET_EN
            web_logger.info(f"WebAppBridge-Admin: Using TTS voice preset: {tts_voice_preset_to_use} (based on lang: {result_data['detected_language_for_tts']})")

            audio_array, samplerate = bark_engine_for_web_admin.synthesize_speech_to_array(
                result_data["llm_text_response"],
                generation_params={"voice_preset": tts_voice_preset_to_use}
            )

            if audio_array is not None and samplerate is not None and audio_array.size > 0 :
                if not file_utils.ensure_folder(config.WEB_UI_TTS_SERVE_FOLDER, gui_callbacks=None): # GUI callbacks not relevant here
                    web_logger.critical(f"WebAppBridge-Admin: Critical - Could not create/access TTS serve folder: {config.WEB_UI_TTS_SERVE_FOLDER}")
                    result_data["error_message"] = (result_data["error_message"] or "") + " Server error: Cannot save TTS audio (folder issue)."
                    return result_data

                tts_filename = f"web_admin_tts_{uuid.uuid4().hex}.wav"
                tts_filepath = os.path.join(config.WEB_UI_TTS_SERVE_FOLDER, tts_filename)
                
                # Ensure audio_array is float32 for soundfile
                if audio_array.dtype != np.float32:
                    web_logger.debug(f"WebAppBridge-Admin: Converting TTS audio array from {audio_array.dtype} to float32 for soundfile.")
                    audio_array = audio_array.astype(np.float32)
                
                sf.write(tts_filepath, audio_array, samplerate, subtype='PCM_16')
                result_data["tts_audio_filename"] = tts_filename
                web_logger.info(f"WebAppBridge-Admin: Synthesized TTS and saved to {tts_filepath}")
            else:
                web_logger.error("WebAppBridge-Admin: TTS synthesis returned no audio data or samplerate.")
                result_data["error_message"] = (result_data["error_message"] or "") + " TTS synthesis failed to produce audio."
        except Exception as e_tts:
            result_data["error_message"] = (result_data["error_message"] or "") + f" TTS synthesis/saving failed: {str(e_tts)}"
            web_logger.error(f"WebAppBridge-Admin: TTS Exception - {result_data['error_message']}", exc_info=True)

        web_logger.info(f"WebAppBridge-Admin: Interaction processing complete. Result summary: Error: '{result_data['error_message']}', Transcription: '{str(result_data['user_transcription'])[:30]}...', LLM: '{str(result_data['llm_text_response'])[:30]}...', TTS File: '{result_data['tts_audio_filename']}'")
        return result_data


    def get_system_status_for_web(self):
        ollama_stat_text, ollama_stat_type = "N/A", "unknown"
        if self.get_ollama_ready():
            ollama_stat_text, ollama_stat_type = "Ready", "ready"
        else:
            _, ollama_ping_msg_from_last_check = self.ollama_handler_module.check_ollama_server_and_model()
            if self.get_ollama_ready(): # Check again, as check_ollama_server_and_model might update it
                 ollama_stat_text, ollama_stat_type = "Ready", "ready"
            else:
                if "timeout" in (ollama_ping_msg_from_last_check or "").lower(): ollama_stat_type = "timeout"
                elif "connection" in (ollama_ping_msg_from_last_check or "").lower(): ollama_stat_type = "conn_error"
                else: ollama_stat_type = "error"
                ollama_stat_text = ollama_ping_msg_from_last_check[:30] if ollama_ping_msg_from_last_check else "Error"

        main_app_status_from_gui = "N/A"
        try: main_app_status_from_gui = self.get_main_app_status_label()
        except Exception: pass # GUI might not be fully up

        tele_stat_text, tele_stat_type = "N/A", "unknown"
        if self.telegram_handler_instance_ref and hasattr(self.telegram_handler_instance_ref, 'get_status'):
            current_tele_status = self.telegram_handler_instance_ref.get_status()
            tele_stat_type = current_tele_status
            status_text_map = { "loading": "Load...", "polling": "Poll", "error": "Err",
                                "no_token": "NoTok", "no_admin": "NoAdm", "bad_token": "BadTok",
                                "net_error": "NetErr", "off": "Off" }
            tele_stat_text = status_text_map.get(current_tele_status, current_tele_status.capitalize())
        elif not config.TELEGRAM_BOT_TOKEN: tele_stat_text, tele_stat_type = "NoTok", "no_token"
        elif not config.TELEGRAM_ADMIN_USER_ID: tele_stat_text, tele_stat_type = "NoAdm", "no_admin"
        else: tele_stat_text, tele_stat_type = "N/A", "unknown"

        # Get WebUI status from main.py's health check function
        webui_text_main, webui_type_main = "WEBUI: N/A", "unknown"
        if callable(self.fn_check_webui_health_main):
            try:
                webui_text_main, webui_type_main = self.fn_check_webui_health_main()
            except Exception as e_web_health:
                web_logger.warning(f"Error calling main WebUI health check function: {e_web_health}")
                webui_text_main, webui_type_main = "WEBUI: ERR-CHK", "error"
        else:
            web_logger.warning("Main WebUI health check function not callable in WebAppBridge.")


        return {
            "ollama": {"text": ollama_stat_text, "type": ollama_stat_type},
            "whisper": {"text": self.whisper_handler_module.get_status_short(), "type": self.whisper_handler_module.get_status_type()},
            "bark": {"text": self.tts_manager_module.get_status_short(), "type": self.tts_manager_module.get_status_type()},
            "telegram": {"text": tele_stat_text, "type": tele_stat_type},
            "webui": {"text": webui_text_main, "type": webui_type_main}, # Use status from main health check
            "app_overall_status": main_app_status_from_gui
        }