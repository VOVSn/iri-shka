# utils/admin_interaction_processor.py
import json
import re
import asyncio
import os
import datetime
import threading # For type hint
import gc # For process_gui_recorded_audio (optional)

import config
from logger import get_logger

logger = get_logger("Iri-shka_App.utils.AdminInteractionProcessor")

def _parse_ollama_error_to_short_code(error_message_from_handler):
    if not error_message_from_handler: return "NRDY", "error"
    lower_msg = error_message_from_handler.lower()
    if "timeout" in lower_msg: return "TMO", "timeout"
    if "connection" in lower_msg or "connect" in lower_msg : return "CON", "conn_error"
    if "502" in lower_msg: return "502", "http_502"
    http_match = re.search(r"http.*?(\d{3})", lower_msg)
    if http_match: code = http_match.group(1); return f"H{code}", "http_other"
    if "json" in lower_msg and ("invalid" in lower_msg or "not valid" in lower_msg) : return "JSON", "error"
    if "empty content" in lower_msg or "empty response" in lower_msg : return "EMP", "error"
    if "missing keys" in lower_msg : return "KEYS", "error"
    if "model not found" in lower_msg or "pull model" in lower_msg : return "NOMDL", "error"
    return "NRDY", "error"

def handle_admin_llm_interaction(
    input_text: str, source: str, detected_language_code,
    chat_history_ref: list, user_state_ref: dict, assistant_state_ref: dict,
    global_states_lock_ref: threading.Lock, gui_callbacks: dict,
    telegram_bot_handler_instance_ref, ollama_handler_module_ref,
    state_manager_module_ref, tts_manager_module_ref,
    telegram_messaging_utils_module_ref, ollama_ready_flag: bool
    ):
    function_signature_for_log = f"handle_admin_llm_interaction(source={source}, input='{input_text[:30]}...')"
    logger.info(f"ADMIN_LLM_FLOW ({source}): {function_signature_for_log} - Starting.")

    if source == "gui" and gui_callbacks and callable(gui_callbacks.get('act_status_update')):
        gui_callbacks['act_status_update']("ACT: BUSY", "busy")

    assistant_response_text_llm = "Error: LLM processing did not complete."
    selected_bark_voice_preset = config.BARK_VOICE_PRESET_EN
    ollama_error_occurred = False 
    ollama_error_message_str = "" 
    current_lang_code_for_state = "en"

    try:
        user_state_snapshot_for_prompt: dict
        assistant_state_snapshot_for_prompt: dict
        chat_history_snapshot_for_prompt: list
        with global_states_lock_ref:
            user_state_snapshot_for_prompt = user_state_ref.copy()
            assistant_state_snapshot_for_prompt = assistant_state_ref.copy()
            chat_history_snapshot_for_prompt = chat_history_ref[:]

            current_lang_code_for_state = detected_language_code if detected_language_code in ["ru", "en"] \
                else assistant_state_snapshot_for_prompt.get("last_used_language", "en")
            
            language_instruction_for_llm = config.LANGUAGE_INSTRUCTION_RUSSIAN if current_lang_code_for_state == "ru" \
                                           else config.LANGUAGE_INSTRUCTION_NON_RUSSIAN
            if current_lang_code_for_state == "ru":
                selected_bark_voice_preset = config.BARK_VOICE_PRESET_RU

        if source == "gui" and gui_callbacks and callable(gui_callbacks.get('add_user_message_to_display')):
            gui_callbacks['add_user_message_to_display'](input_text, source=source)

        if gui_callbacks and callable(gui_callbacks.get('status_update')):
            gui_callbacks['status_update'](f"Thinking (Admin {source})...")
        if gui_callbacks and callable(gui_callbacks.get('mind_status_update')):
            gui_callbacks['mind_status_update']("MIND: THK", "thinking")

        target_customer_id_for_prompt = None
        customer_state_for_prompt_str = "{}"
        is_customer_context_active_for_prompt = False
        history_to_scan = chat_history_snapshot_for_prompt[-(config.MAX_HISTORY_TURNS // 2 or 1):]
        for turn in reversed(history_to_scan):
            assistant_message_hist = turn.get("assistant", "")
            turn_source_hist = turn.get("source", "")
            if turn_source_hist == "customer_summary_internal":
                match_summary = re.search(r"\[Сводка по клиенту (\d+)\]", assistant_message_hist)
                if match_summary:
                    try: target_customer_id_for_prompt = int(match_summary.group(1)); break
                    except ValueError: pass
        
        if target_customer_id_for_prompt:
            try:
                loaded_customer_state = state_manager_module_ref.load_or_initialize_customer_state(
                    target_customer_id_for_prompt, gui_callbacks)
                if loaded_customer_state and loaded_customer_state.get("user_id") == target_customer_id_for_prompt:
                    customer_state_for_prompt_str = json.dumps(loaded_customer_state, indent=2, ensure_ascii=False)
                    is_customer_context_active_for_prompt = True
                else: target_customer_id_for_prompt = None
            except Exception as e_load_ctx_cust: logger.error(f"ADMIN_LLM_FLOW ({source}): Exc loading customer state {target_customer_id_for_prompt}: {e_load_ctx_cust}", exc_info=True); target_customer_id_for_prompt = None

        assistant_state_for_this_prompt_input = assistant_state_snapshot_for_prompt.copy()
        assistant_state_for_this_prompt_input["last_used_language"] = current_lang_code_for_state
        admin_current_name = assistant_state_for_this_prompt_input.get("admin_name", "Partner")

        format_kwargs_for_ollama = {
            "admin_name_value": admin_current_name, "assistant_admin_name_current_value": admin_current_name,
            "is_customer_context_active": is_customer_context_active_for_prompt,
            "active_customer_id": str(target_customer_id_for_prompt) if target_customer_id_for_prompt else "N/A",
            "active_customer_state_string": customer_state_for_prompt_str
        }
        expected_keys_for_response = ["answer_to_user", "updated_user_state", "updated_assistant_state", "updated_active_customer_state"]

        ollama_data, ollama_error_message_str = ollama_handler_module_ref.call_ollama_for_chat_response(
            prompt_template_to_use=config.OLLAMA_PROMPT_TEMPLATE, transcribed_text=input_text,
            current_chat_history=chat_history_snapshot_for_prompt, current_user_state=user_state_snapshot_for_prompt,
            current_assistant_state=assistant_state_for_this_prompt_input, language_instruction=language_instruction_for_llm,
            format_kwargs=format_kwargs_for_ollama, expected_keys_override=expected_keys_for_response,
            gui_callbacks=gui_callbacks
        )

        current_turn_for_history = {"user": input_text, "source": source}
        if detected_language_code: current_turn_for_history[f"detected_language_code_for_{source}_display"] = detected_language_code
        
        with global_states_lock_ref:
            if ollama_error_message_str: 
                ollama_error_occurred = True
                assistant_response_text_llm = "An internal error occurred (admin)."
                if current_lang_code_for_state == "ru": assistant_response_text_llm = "Произошла внутренняя ошибка (админ)."
                current_turn_for_history["assistant"] = f"[LLM Error ({source}): {assistant_response_text_llm}]"
                if gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')):
                    gui_callbacks['add_assistant_message_to_display'](assistant_response_text_llm, is_error=True, source=f"{source}_error")
            else: 
                assistant_response_text_llm = ollama_data.get("answer_to_user", "Error: No LLM answer.")
                
                llm_provided_user_state_changes = ollama_data.get("updated_user_state", {})
                if isinstance(llm_provided_user_state_changes, dict):
                    current_gui_theme_from_live_state = user_state_ref.get("gui_theme", config.DEFAULT_USER_STATE["gui_theme"])
                    llm_theme_suggestion = llm_provided_user_state_changes.get("gui_theme", current_gui_theme_from_live_state)
                    applied_theme_value = current_gui_theme_from_live_state
                    if llm_theme_suggestion != current_gui_theme_from_live_state and llm_theme_suggestion in [config.GUI_THEME_LIGHT, config.GUI_THEME_DARK]:
                        if gui_callbacks and callable(gui_callbacks.get('apply_application_theme')):
                            gui_callbacks['apply_application_theme'](llm_theme_suggestion)
                            applied_theme_value = llm_theme_suggestion
                    llm_provided_user_state_changes["gui_theme"] = applied_theme_value

                    current_font_size_from_live_state = user_state_ref.get("chat_font_size", config.DEFAULT_USER_STATE["chat_font_size"])
                    llm_font_size_str_suggestion = llm_provided_user_state_changes.get("chat_font_size", str(current_font_size_from_live_state))
                    try: llm_font_size_as_int = int(llm_font_size_str_suggestion)
                    except: llm_font_size_as_int = current_font_size_from_live_state
                    clamped_font_size_suggestion = max(config.MIN_CHAT_FONT_SIZE, min(llm_font_size_as_int, config.MAX_CHAT_FONT_SIZE))
                    applied_font_size_value = current_font_size_from_live_state
                    if clamped_font_size_suggestion != current_font_size_from_live_state:
                        if gui_callbacks and callable(gui_callbacks.get('apply_chat_font_size')):
                            gui_callbacks['apply_chat_font_size'](clamped_font_size_suggestion)
                        applied_font_size_value = clamped_font_size_suggestion
                    llm_provided_user_state_changes["chat_font_size"] = applied_font_size_value
                    
                    # Ensure 'todos' is not part of the update from LLM for admin state
                    if "todos" in llm_provided_user_state_changes:
                        del llm_provided_user_state_changes["todos"]
                        logger.warning(f"ADMIN_LLM_FLOW ({source}): LLM tried to update 'todos' for admin state. This key is ignored.")

                    merged_user_state = user_state_snapshot_for_prompt.copy()
                    merged_user_state.update(llm_provided_user_state_changes)
                    user_state_ref.clear(); user_state_ref.update(merged_user_state)
                else:
                    logger.warning(f"ADMIN_LLM_FLOW ({source}): updated_user_state from LLM was not a dict. User state not modified by LLM this turn.")

                llm_provided_assistant_state_changes = ollama_data.get("updated_assistant_state", {})
                if isinstance(llm_provided_assistant_state_changes, dict):
                    merged_assistant_state = assistant_state_snapshot_for_prompt.copy()
                    
                    # Handle internal_tasks: pending and completed only
                    if "internal_tasks" in llm_provided_assistant_state_changes and isinstance(llm_provided_assistant_state_changes["internal_tasks"], dict):
                        llm_tasks_dict = llm_provided_assistant_state_changes["internal_tasks"]
                        
                        if "internal_tasks" not in merged_assistant_state or not isinstance(merged_assistant_state.get("internal_tasks"), dict):
                            merged_assistant_state["internal_tasks"] = {"pending": [], "completed": []}
                        
                        for task_type in ["pending", "completed"]: # Only process these two
                            if task_type not in merged_assistant_state["internal_tasks"] or not isinstance(merged_assistant_state["internal_tasks"].get(task_type), list):
                                merged_assistant_state["internal_tasks"][task_type] = []
                            
                            new_tasks_from_llm = llm_tasks_dict.get(task_type, [])
                            if not isinstance(new_tasks_from_llm, list): 
                                new_tasks_from_llm = [str(new_tasks_from_llm)] if new_tasks_from_llm else []
                            
                            existing_tasks_in_merged = merged_assistant_state["internal_tasks"][task_type]
                            merged_assistant_state["internal_tasks"][task_type] = list(dict.fromkeys(
                                [str(t) for t in existing_tasks_in_merged] + [str(t) for t in new_tasks_from_llm]
                            ))
                        if "in_process" in llm_tasks_dict:
                             logger.warning(f"ADMIN_LLM_FLOW ({source}): LLM provided 'in_process' tasks. This key is now ignored for assistant state. Tasks: {llm_tasks_dict['in_process']}")
                    
                    # Update other assistant state keys
                    for key, val_llm in llm_provided_assistant_state_changes.items():
                        if key != "internal_tasks": 
                            merged_assistant_state[key] = val_llm
                            
                    merged_assistant_state["last_used_language"] = current_lang_code_for_state
                    assistant_state_ref.clear(); assistant_state_ref.update(merged_assistant_state)
                else:
                     logger.warning(f"ADMIN_LLM_FLOW ({source}): updated_assistant_state from LLM was not a dict. Assistant state not modified by LLM this turn.")

                if gui_callbacks:
                    # Removed update_todo_list for admin
                    if callable(gui_callbacks.get('update_calendar_events_list')): 
                        gui_callbacks['update_calendar_events_list'](user_state_ref.get("calendar_events", []))
                    
                    asst_tasks = assistant_state_ref.get("internal_tasks", {});
                    if not isinstance(asst_tasks, dict): asst_tasks = {"pending": [], "completed": []}
                    
                    if callable(gui_callbacks.get('update_kanban_pending')): 
                        gui_callbacks['update_kanban_pending'](asst_tasks.get("pending", []))
                    # Removed update_kanban_in_process
                    if callable(gui_callbacks.get('update_kanban_completed')): 
                        gui_callbacks['update_kanban_completed'](asst_tasks.get("completed", []))

                updated_customer_state_from_llm = ollama_data.get("updated_active_customer_state")
                if updated_customer_state_from_llm and isinstance(updated_customer_state_from_llm, dict) and target_customer_id_for_prompt:
                    if updated_customer_state_from_llm.get("user_id") == target_customer_id_for_prompt:
                        if state_manager_module_ref.save_customer_state(target_customer_id_for_prompt, updated_customer_state_from_llm, gui_callbacks): 
                            logger.info(f"ADMIN_LLM_FLOW ({source}): Updated state for context customer {target_customer_id_for_prompt}.")
                
                current_turn_for_history["assistant"] = assistant_response_text_llm
                if gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')):
                     gui_callbacks['add_assistant_message_to_display'](assistant_response_text_llm, is_error=False, source=source)

            current_turn_for_history["timestamp"] = state_manager_module_ref.get_current_timestamp_iso()
            chat_history_ref.append(current_turn_for_history)
            updated_chat_history = state_manager_module_ref.save_states(
                chat_history_ref, user_state_ref, assistant_state_ref, gui_callbacks)
            if len(chat_history_ref) != len(updated_chat_history): 
                chat_history_ref[:] = updated_chat_history
            if gui_callbacks and callable(gui_callbacks.get('memory_status_update')): gui_callbacks['memory_status_update']("MEM: SAVED", "saved")

        if source == "gui" and tts_manager_module_ref.is_tts_ready() and not ollama_error_occurred:
            def _deferred_gui_display():
                 if gui_callbacks and callable(gui_callbacks.get('status_update')):
                    gui_callbacks['status_update'](f"Speaking (Admin): {assistant_response_text_llm[:40]}...")
            current_persona_name_tts = "Iri-shka"
            with global_states_lock_ref: current_persona_name_tts = assistant_state_ref.get("persona_name", "Iri-shka")
            tts_manager_module_ref.start_speaking_response(
                assistant_response_text_llm, current_persona_name_tts, selected_bark_voice_preset, gui_callbacks,
                on_actual_playback_start_gui_callback=_deferred_gui_display)

        elif (source == "telegram_admin" or source == "telegram_voice_admin") and \
             telegram_bot_handler_instance_ref and not ollama_error_occurred:
            try:
                admin_id_int = int(config.TELEGRAM_ADMIN_USER_ID)
                if config.TELEGRAM_REPLY_WITH_TEXT:
                    asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance_ref.send_text_message_to_user(admin_id_int, assistant_response_text_llm), telegram_bot_handler_instance_ref.async_loop).result(timeout=15)
                if config.TELEGRAM_REPLY_WITH_VOICE:
                    telegram_messaging_utils_module_ref.send_voice_reply_to_telegram_user(
                        admin_id_int, assistant_response_text_llm, selected_bark_voice_preset,
                        telegram_bot_handler_instance_ref, tts_manager_module_ref
                    )
            except Exception as e_tg_send_admin: logger.error(f"ADMIN_LLM_FLOW: Error sending reply to admin TG ({source}): {e_tg_send_admin}", exc_info=True)
        elif ollama_error_occurred and telegram_bot_handler_instance_ref and telegram_bot_handler_instance_ref.async_loop:
            try:
                admin_id_int = int(config.TELEGRAM_ADMIN_USER_ID)
                err_text_for_tg = "LLM processing failed."
                if current_lang_code_for_state == "ru": err_text_for_tg = "Ошибка обработки LLM."
                asyncio.run_coroutine_threadsafe( telegram_bot_handler_instance_ref.send_text_message_to_user(admin_id_int, err_text_for_tg), telegram_bot_handler_instance_ref.async_loop).result(timeout=10)
            except Exception as e_tg_err_send: logger.error(f"Failed to send LLM error to admin TG: {e_tg_err_send}")

        if gui_callbacks:
            if callable(gui_callbacks.get('mind_status_update')):
                if ollama_error_occurred:
                    short_code, status_type = _parse_ollama_error_to_short_code(ollama_error_message_str)
                    gui_callbacks['mind_status_update'](f"MIND: {short_code.upper()}", status_type)
                elif ollama_ready_flag: 
                    gui_callbacks['mind_status_update']("MIND: RDY", "ready")
                else: 
                    gui_callbacks['mind_status_update']("MIND: NRDY", "error")

            if callable(gui_callbacks.get('status_update')):
                will_gui_tts_update_status = False
                if source == "gui" and tts_manager_module_ref.is_tts_ready() and not ollama_error_occurred:
                    will_gui_tts_update_status = True 

                if not will_gui_tts_update_status:
                    final_app_status_text = "Error processing." 
                    if not ollama_error_occurred and ollama_ready_flag:
                        current_admin_name_for_status = "Partner"
                        current_persona_name_for_status = "Iri-shka"
                        with global_states_lock_ref: 
                            current_admin_name_for_status = assistant_state_ref.get("admin_name", "Partner")
                            current_persona_name_for_status = assistant_state_ref.get("persona_name", "Iri-shka")
                        
                        if assistant_response_text_llm and not assistant_response_text_llm.startswith("Error:"):
                            response_snippet = assistant_response_text_llm[:60]
                            if len(assistant_response_text_llm) > 60: response_snippet += "..."
                            final_app_status_text = f"{current_persona_name_for_status} to {current_admin_name_for_status}: {response_snippet}"
                        elif assistant_response_text_llm:
                             final_app_status_text = f"{current_persona_name_for_status} says: {assistant_response_text_llm[:60]}..."
                        else: 
                             final_app_status_text = "Ready."
                    
                    elif ollama_error_occurred: 
                        short_code, _ = _parse_ollama_error_to_short_code(ollama_error_message_str)
                        final_app_status_text = f"LLM Error: {short_code.upper()}"
                    
                    elif not ollama_ready_flag: 
                        final_app_status_text = "LLM Not Ready." 
                    
                    gui_callbacks['status_update'](final_app_status_text)
    finally:
        if source == "gui" and gui_callbacks and callable(gui_callbacks.get('act_status_update')):
            gui_callbacks['act_status_update']("ACT: IDLE", "idle")
        logger.info(f"ADMIN_LLM_FLOW ({source}): {function_signature_for_log} - Processing finished.")


def process_gui_recorded_audio(
    recorded_sample_rate: int, chat_history_ref: list, user_state_ref: dict, assistant_state_ref: dict,
    global_states_lock_ref: threading.Lock, gui_callbacks: dict, telegram_bot_handler_instance_ref, ollama_ready_flag: bool,
    audio_processor_module_ref, whisper_handler_module_ref, tts_manager_module_ref,
    ollama_handler_module_ref, state_manager_module_ref, file_utils_module_ref,
    telegram_messaging_utils_module_ref
    ):
    logger.info(f"Processing recorded audio (Admin GUI). Sample rate: {recorded_sample_rate} Hz.")
    llm_called = False
    if gui_callbacks and callable(gui_callbacks.get('act_status_update')):
        gui_callbacks['act_status_update']("ACT: BUSY", "busy")
    try:
        audio_float32, audio_frames_for_save = audio_processor_module_ref.convert_frames_to_numpy(
            recorded_sample_rate, gui_callbacks)
        if audio_float32 is None: return

        if config.SAVE_RECORDINGS_TO_WAV and audio_frames_for_save:
            file_utils_module_ref.ensure_folder(config.OUTPUT_FOLDER, gui_callbacks)
            filename = f"rec_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
            audio_processor_module_ref.save_wav_data_to_file(
                os.path.join(config.OUTPUT_FOLDER, filename), audio_frames_for_save, recorded_sample_rate, gui_callbacks)
        del audio_frames_for_save; audio_frames_for_save=None; gc.collect()


        if not (whisper_handler_module_ref.WHISPER_CAPABLE and whisper_handler_module_ref.is_whisper_ready()):
            if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update']("Hearing NRDY.")
            if gui_callbacks and callable(gui_callbacks.get('mind_status_update')):
                 current_mind_status_text = "MIND: RDY" if ollama_ready_flag else "MIND: NRDY"
                 current_mind_status_type = "ready" if ollama_ready_flag else "error"
                 gui_callbacks['mind_status_update'](current_mind_status_text, current_mind_status_type)
            return

        if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update']("Transcribing (GUI)...")
        transcribed_text, trans_err, detected_lang = whisper_handler_module_ref.transcribe_audio(
            audio_np_array=audio_float32, language=None, task="transcribe", gui_callbacks=gui_callbacks)
        del audio_float32; audio_float32=None; gc.collect()
        
        if not trans_err and transcribed_text:
            llm_called = True
            handle_admin_llm_interaction(
                input_text=transcribed_text, source="gui", detected_language_code=detected_lang,
                chat_history_ref=chat_history_ref, user_state_ref=user_state_ref, assistant_state_ref=assistant_state_ref,
                global_states_lock_ref=global_states_lock_ref, gui_callbacks=gui_callbacks,
                telegram_bot_handler_instance_ref=telegram_bot_handler_instance_ref,
                ollama_handler_module_ref=ollama_handler_module_ref, state_manager_module_ref=state_manager_module_ref,
                tts_manager_module_ref=tts_manager_module_ref,
                telegram_messaging_utils_module_ref=telegram_messaging_utils_module_ref,
                ollama_ready_flag=ollama_ready_flag 
            )
        elif not transcribed_text and not trans_err: 
            lang_for_err_gui = "en"
            with global_states_lock_ref: lang_for_err_gui = assistant_state_ref.get("last_used_language", "en") 
            err_msg_stt_gui = "I didn't catch that..." if lang_for_err_gui == "en" else "Я не расслышала..."
            if gui_callbacks and callable(gui_callbacks.get('add_user_message_to_display')): gui_callbacks['add_user_message_to_display']("[Silent/Unclear Audio]", source="gui")
            if gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')): gui_callbacks['add_assistant_message_to_display'](err_msg_stt_gui, is_error=False, source="gui") 
            if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update'](err_msg_stt_gui)
            if gui_callbacks and callable(gui_callbacks.get('mind_status_update')):
                 current_mind_status_text = "MIND: RDY" if ollama_ready_flag else "MIND: NRDY"
                 current_mind_status_type = "ready" if ollama_ready_flag else "error"
                 gui_callbacks['mind_status_update'](current_mind_status_text, current_mind_status_type)
            if tts_manager_module_ref.is_tts_ready():
                err_preset_gui = config.BARK_VOICE_PRESET_EN if lang_for_err_gui == "en" else config.BARK_VOICE_PRESET_RU
                current_persona_name_gui = "Iri-shka"; 
                with global_states_lock_ref: current_persona_name_gui = assistant_state_ref.get("persona_name", "Iri-shka") 
                tts_manager_module_ref.start_speaking_response(err_msg_stt_gui, current_persona_name_gui, err_preset_gui, gui_callbacks)
        else: 
             lang_for_err_gui = "en"
             with global_states_lock_ref: lang_for_err_gui = assistant_state_ref.get("last_used_language", "en")
             err_msg_stt_gui = "Sorry, I had trouble understanding that." if lang_for_err_gui == "en" else "Извините, не удалось разобрать речь."
             if gui_callbacks and callable(gui_callbacks.get('add_user_message_to_display')): gui_callbacks['add_user_message_to_display']("[Transcription Error]", source="gui")
             if gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')): gui_callbacks['add_assistant_message_to_display'](err_msg_stt_gui, is_error=True, source="gui")
             if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update'](f"Transcription Error: {trans_err or 'Unknown'}")
             if gui_callbacks and callable(gui_callbacks.get('mind_status_update')):
                 current_mind_status_text = "MIND: RDY" if ollama_ready_flag else "MIND: NRDY"
                 current_mind_status_type = "ready" if ollama_ready_flag else "error"
                 gui_callbacks['mind_status_update'](current_mind_status_text, current_mind_status_type)

    finally:
        if not llm_called: 
            if gui_callbacks and callable(gui_callbacks.get('act_status_update')):
                gui_callbacks['act_status_update']("ACT: IDLE", "idle")


def process_admin_telegram_text_message(
    user_id, text_message, chat_history_ref: list, user_state_ref: dict, assistant_state_ref: dict,
    global_states_lock_ref: threading.Lock, gui_callbacks: dict, telegram_bot_handler_instance_ref, ollama_ready_flag: bool,
    ollama_handler_module_ref, state_manager_module_ref, tts_manager_module_ref,
    telegram_messaging_utils_module_ref
    ):
    logger.info(f"Processing Admin Telegram text from {user_id}: '{text_message[:70]}...'")
    if gui_callbacks and callable(gui_callbacks.get('add_user_message_to_display')):
        gui_callbacks['add_user_message_to_display'](text_message, source="telegram_admin")

    handle_admin_llm_interaction(
        input_text=text_message, source="telegram_admin", detected_language_code=None,
        chat_history_ref=chat_history_ref, user_state_ref=user_state_ref, assistant_state_ref=assistant_state_ref,
        global_states_lock_ref=global_states_lock_ref, gui_callbacks=gui_callbacks,
        telegram_bot_handler_instance_ref=telegram_bot_handler_instance_ref,
        ollama_handler_module_ref=ollama_handler_module_ref, state_manager_module_ref=state_manager_module_ref,
        tts_manager_module_ref=tts_manager_module_ref,
        telegram_messaging_utils_module_ref=telegram_messaging_utils_module_ref,
        ollama_ready_flag=ollama_ready_flag
    )

def process_admin_telegram_voice_message(
    user_id, wav_filepath, chat_history_ref: list, user_state_ref: dict, assistant_state_ref: dict,
    global_states_lock_ref: threading.Lock, gui_callbacks: dict, telegram_bot_handler_instance_ref, ollama_ready_flag: bool,
    whisper_handler_module_ref, _whisper_module_for_load_audio_ref,
    ollama_handler_module_ref, state_manager_module_ref, tts_manager_module_ref,
    telegram_messaging_utils_module_ref
    ):
    logger.info(f"Processing Admin Telegram voice from {user_id}, WAV: {wav_filepath}")
    try:
        if not (whisper_handler_module_ref.WHISPER_CAPABLE and whisper_handler_module_ref.is_whisper_ready() and _whisper_module_for_load_audio_ref):
            logger.error("Cannot process admin voice: Whisper not ready or load_audio missing.")
            if telegram_bot_handler_instance_ref and telegram_bot_handler_instance_ref.async_loop:
                 asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance_ref.send_text_message_to_user(user_id, "Error: Voice processing module (Whisper) is not ready."), telegram_bot_handler_instance_ref.async_loop)
            if gui_callbacks and callable(gui_callbacks.get('mind_status_update')):
                 current_mind_status_text = "MIND: RDY" if ollama_ready_flag else "MIND: NRDY"
                 current_mind_status_type = "ready" if ollama_ready_flag else "error"
                 gui_callbacks['mind_status_update'](current_mind_status_text, current_mind_status_type)
            return

        if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update']("Loading Admin voice (TG)...")
        audio_numpy = _whisper_module_for_load_audio_ref.load_audio(wav_filepath)
        if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update']("Transcribing Admin voice (TG)...")
        
        trans_text, trans_err, detected_lang = whisper_handler_module_ref.transcribe_audio(
            audio_np_array=audio_numpy, language=None, task="transcribe", gui_callbacks=gui_callbacks)
        del audio_numpy; audio_numpy=None; gc.collect()
        
        if not trans_err and trans_text:
            if gui_callbacks and callable(gui_callbacks.get('add_user_message_to_display')):
                gui_callbacks['add_user_message_to_display'](trans_text, source="telegram_voice_admin")
            handle_admin_llm_interaction(
                input_text=trans_text, source="telegram_voice_admin", detected_language_code=detected_lang,
                chat_history_ref=chat_history_ref, user_state_ref=user_state_ref, assistant_state_ref=assistant_state_ref,
                global_states_lock_ref=global_states_lock_ref, gui_callbacks=gui_callbacks,
                telegram_bot_handler_instance_ref=telegram_bot_handler_instance_ref,
                ollama_handler_module_ref=ollama_handler_module_ref, state_manager_module_ref=state_manager_module_ref,
                tts_manager_module_ref=tts_manager_module_ref,
                telegram_messaging_utils_module_ref=telegram_messaging_utils_module_ref,
                ollama_ready_flag=ollama_ready_flag
            )
        elif not trans_text and not trans_err: 
             logger.info(f"Admin TG Voice: No speech detected in {wav_filepath}")
             if gui_callbacks and callable(gui_callbacks.get('add_user_message_to_display')):
                gui_callbacks['add_user_message_to_display']("[Silent/Unclear Audio from Admin TG]", source="telegram_voice_admin")
             if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update']("Admin TG: No speech detected.")
             if gui_callbacks and callable(gui_callbacks.get('mind_status_update')):
                 current_mind_status_text = "MIND: RDY" if ollama_ready_flag else "MIND: NRDY"
                 current_mind_status_type = "ready" if ollama_ready_flag else "error"
                 gui_callbacks['mind_status_update'](current_mind_status_text, current_mind_status_type)
             if telegram_bot_handler_instance_ref and telegram_bot_handler_instance_ref.async_loop:
                asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance_ref.send_text_message_to_user(user_id, "I didn't hear anything in your voice message."), telegram_bot_handler_instance_ref.async_loop)
        else: 
            logger.warning(f"Admin TG Voice Transcription failed: {trans_err}")
            if gui_callbacks and callable(gui_callbacks.get('add_user_message_to_display')):
                gui_callbacks['add_user_message_to_display'](f"[Transcription Error from Admin TG: {trans_err}]", source="telegram_voice_admin")
            if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update'](f"Admin TG Voice Error: {trans_err or 'Recognition error.'}")
            if gui_callbacks and callable(gui_callbacks.get('mind_status_update')):
                 current_mind_status_text = "MIND: RDY" if ollama_ready_flag else "MIND: NRDY"
                 current_mind_status_type = "ready" if ollama_ready_flag else "error"
                 gui_callbacks['mind_status_update'](current_mind_status_text, current_mind_status_type)
            if telegram_bot_handler_instance_ref and telegram_bot_handler_instance_ref.async_loop:
                asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance_ref.send_text_message_to_user(user_id, f"Couldn't transcribe voice: {trans_err or 'Recognition error.'}"), telegram_bot_handler_instance_ref.async_loop)
    except Exception as e:
        logger.error(f"Error processing admin voice WAV {wav_filepath}: {e}", exc_info=True)
        if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update']("Error processing admin voice.")
        if gui_callbacks and callable(gui_callbacks.get('mind_status_update')):
             current_mind_status_text = "MIND: RDY" if ollama_ready_flag else "MIND: NRDY" 
             current_mind_status_type = "ready" if ollama_ready_flag else "error"
             gui_callbacks['mind_status_update'](current_mind_status_text, current_mind_status_type)
        if telegram_bot_handler_instance_ref and telegram_bot_handler_instance_ref.async_loop:
             asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance_ref.send_text_message_to_user(user_id, "An error occurred while processing your voice message."), telegram_bot_handler_instance_ref.async_loop)
    finally:
        if os.path.exists(wav_filepath):
            try: os.remove(wav_filepath)
            except Exception as e_rem: logger.warning(f"Could not remove temp admin WAV {wav_filepath}: {e_rem}")