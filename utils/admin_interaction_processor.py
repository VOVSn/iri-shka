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
    # ... (same as in thought process)
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
    current_lang_code_for_state = "en" 

    try:
        # Snapshots are taken BEFORE any modification or LLM call
        user_state_snapshot_for_prompt: dict
        assistant_state_snapshot_for_prompt: dict
        chat_history_snapshot_for_prompt: list
        with global_states_lock_ref:
            user_state_snapshot_for_prompt = user_state_ref.copy()
            assistant_state_snapshot_for_prompt = assistant_state_ref.copy()
            chat_history_snapshot_for_prompt = chat_history_ref[:] # Shallow copy is fine for list of dicts here

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

        # Use a copy of the snapshot for the prompt, which might be modified (e.g. last_used_language)
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

        ollama_data, ollama_error = ollama_handler_module_ref.call_ollama_for_chat_response(
            prompt_template_to_use=config.OLLAMA_PROMPT_TEMPLATE, transcribed_text=input_text,
            current_chat_history=chat_history_snapshot_for_prompt, current_user_state=user_state_snapshot_for_prompt, # Pass snapshot
            current_assistant_state=assistant_state_for_this_prompt_input, language_instruction=language_instruction_for_llm,
            format_kwargs=format_kwargs_for_ollama, expected_keys_override=expected_keys_for_response,
            gui_callbacks=gui_callbacks
        )

        current_turn_for_history = {"user": input_text, "source": source}
        if detected_language_code: current_turn_for_history[f"detected_language_code_for_{source}_display"] = detected_language_code
        
        with global_states_lock_ref:
            if ollama_error:
                ollama_error_occurred = True
                assistant_response_text_llm = "An internal error occurred (admin)."
                if current_lang_code_for_state == "ru": assistant_response_text_llm = "Произошла внутренняя ошибка (админ)."
                current_turn_for_history["assistant"] = f"[LLM Error ({source}): {assistant_response_text_llm}]"
                # User state and assistant state remain as per snapshots (unchanged by this error turn)
                if gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')):
                    gui_callbacks['add_assistant_message_to_display'](assistant_response_text_llm, is_error=True, source=f"{source}_error")
            else: # Success
                assistant_response_text_llm = ollama_data.get("answer_to_user", "Error: No LLM answer.")
                
                # --- User State (Admin's state) Processing ---
                llm_provided_user_state_changes = ollama_data.get("updated_user_state", {})
                if isinstance(llm_provided_user_state_changes, dict):
                    # Theme and font updates are applied to llm_provided_user_state_changes IN PLACE
                    # This ensures these GUI-driven values are part of the state to be merged.
                    # Based on user_state_ref (the live, potentially already modified state) for current values.
                    current_gui_theme_from_live_state = user_state_ref.get("gui_theme", config.DEFAULT_USER_STATE["gui_theme"])
                    llm_theme_suggestion = llm_provided_user_state_changes.get("gui_theme", current_gui_theme_from_live_state)
                    
                    applied_theme_value = current_gui_theme_from_live_state
                    if llm_theme_suggestion != current_gui_theme_from_live_state and llm_theme_suggestion in [config.GUI_THEME_LIGHT, config.GUI_THEME_DARK]:
                        if gui_callbacks and callable(gui_callbacks.get('apply_application_theme')):
                            gui_callbacks['apply_application_theme'](llm_theme_suggestion)
                            applied_theme_value = llm_theme_suggestion
                    llm_provided_user_state_changes["gui_theme"] = applied_theme_value # Ensure state change dict has applied theme

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
                    llm_provided_user_state_changes["chat_font_size"] = applied_font_size_value # Ensure state change dict has applied font

                    # Merge: Start with pre-LLM state, then overlay LLM's changes (which now include correct GUI settings)
                    merged_user_state = user_state_snapshot_for_prompt.copy()
                    merged_user_state.update(llm_provided_user_state_changes)
                    user_state_ref.clear(); user_state_ref.update(merged_user_state)
                else:
                    logger.warning(f"ADMIN_LLM_FLOW ({source}): updated_user_state from LLM was not a dict. User state not modified by LLM this turn.")

                # --- Assistant State Processing ---
                llm_provided_assistant_state_changes = ollama_data.get("updated_assistant_state", {})
                if isinstance(llm_provided_assistant_state_changes, dict):
                    merged_assistant_state = assistant_state_snapshot_for_prompt.copy() # Start with pre-LLM state
                    
                    # Special merge for internal_tasks
                    if "internal_tasks" in llm_provided_assistant_state_changes and isinstance(llm_provided_assistant_state_changes["internal_tasks"], dict):
                        llm_tasks_dict = llm_provided_assistant_state_changes["internal_tasks"]
                        # Ensure base structure exists in merged_assistant_state for internal_tasks
                        if "internal_tasks" not in merged_assistant_state or not isinstance(merged_assistant_state.get("internal_tasks"), dict):
                            merged_assistant_state["internal_tasks"] = {"pending": [], "in_process": [], "completed": []}
                        
                        for task_type in ["pending", "in_process", "completed"]:
                            if task_type not in merged_assistant_state["internal_tasks"] or not isinstance(merged_assistant_state["internal_tasks"][task_type], list):
                                merged_assistant_state["internal_tasks"][task_type] = [] # Ensure list exists

                            new_tasks_from_llm = llm_tasks_dict.get(task_type, [])
                            if not isinstance(new_tasks_from_llm, list): new_tasks_from_llm = [str(new_tasks_from_llm)]
                            
                            existing_tasks_in_merged = merged_assistant_state["internal_tasks"][task_type]
                            
                            merged_assistant_state["internal_tasks"][task_type] = list(dict.fromkeys(
                                [str(t) for t in existing_tasks_in_merged] + [str(t) for t in new_tasks_from_llm]
                            ))
                    
                    # Update other assistant state keys
                    for key, val_llm in llm_provided_assistant_state_changes.items():
                        if key != "internal_tasks": # Already handled
                            merged_assistant_state[key] = val_llm
                    
                    merged_assistant_state["last_used_language"] = current_lang_code_for_state # Ensure this is set
                    assistant_state_ref.clear(); assistant_state_ref.update(merged_assistant_state)
                else:
                     logger.warning(f"ADMIN_LLM_FLOW ({source}): updated_assistant_state from LLM was not a dict. Assistant state not modified by LLM this turn.")

                # Update GUI lists from the now-modified user_state_ref and assistant_state_ref
                if gui_callbacks:
                    if callable(gui_callbacks.get('update_todo_list')): gui_callbacks['update_todo_list'](user_state_ref.get("todos", []))
                    if callable(gui_callbacks.get('update_calendar_events_list')): gui_callbacks['update_calendar_events_list'](user_state_ref.get("calendar_events", []))
                    asst_tasks = assistant_state_ref.get("internal_tasks", {});
                    if not isinstance(asst_tasks, dict): asst_tasks = {} 
                    if callable(gui_callbacks.get('update_kanban_pending')): gui_callbacks['update_kanban_pending'](asst_tasks.get("pending", []))
                    if callable(gui_callbacks.get('update_kanban_in_process')): gui_callbacks['update_kanban_in_process'](asst_tasks.get("in_process", []))
                    if callable(gui_callbacks.get('update_kanban_completed')): gui_callbacks['update_kanban_completed'](asst_tasks.get("completed", []))

                # Customer state update (if any)
                updated_customer_state_from_llm = ollama_data.get("updated_active_customer_state")
                if updated_customer_state_from_llm and isinstance(updated_customer_state_from_llm, dict) and target_customer_id_for_prompt:
                    if updated_customer_state_from_llm.get("user_id") == target_customer_id_for_prompt: # Sanity check
                        if state_manager_module_ref.save_customer_state(target_customer_id_for_prompt, updated_customer_state_from_llm, gui_callbacks): 
                            logger.info(f"ADMIN_LLM_FLOW ({source}): Updated state for context customer {target_customer_id_for_prompt}.")
                
                current_turn_for_history["assistant"] = assistant_response_text_llm
                if gui_callbacks and callable(gui_callbacks.get('add_assistant_message_to_display')): # Add LLM text to GUI chat
                     gui_callbacks['add_assistant_message_to_display'](assistant_response_text_llm, is_error=False, source=source)


            # Common operations for both success and LLM error cases:
            current_turn_for_history["timestamp"] = state_manager_module_ref.get_current_timestamp_iso()
            chat_history_ref.append(current_turn_for_history)
            updated_chat_history = state_manager_module_ref.save_states(
                chat_history_ref, user_state_ref, assistant_state_ref, gui_callbacks)
            if len(chat_history_ref) != len(updated_chat_history): 
                chat_history_ref[:] = updated_chat_history # Update local ref if trimmed
            
            if gui_callbacks and callable(gui_callbacks.get('memory_status_update')): gui_callbacks['memory_status_update']("MEM: SAVED", "saved") 
            # If `add_assistant_message_to_display` was called, it handles one line.
            # A full refresh might be too much here if only one message was added.
            # However, if states changed things like username in prompt, a full refresh might be desired by some.
            # Let's assume `add_user_message_to_display` and `add_assistant_message_to_display` are sufficient for GUI updates for now.
            # If chat history trimming or other non-obvious changes occurred, then a full update is good.
            # The save_states might trim, so a full update after it is safer.
            # if gui_callbacks and callable(gui_callbacks.get('update_chat_display_from_list')): gui_callbacks['update_chat_display_from_list'](chat_history_ref)


        # TTS / Telegram reply logic (outside lock but uses the final assistant_response_text_llm)
        if source == "gui" and tts_manager_module_ref.is_tts_ready() and not ollama_error_occurred:
            def _deferred_gui_display():
                 if gui_callbacks and callable(gui_callbacks.get('status_update')):
                    gui_callbacks['status_update'](f"Speaking (Admin): {assistant_response_text_llm[:40]}...")
            current_persona_name_tts = "Iri-shka" # Default
            # Read from potentially updated assistant_state_ref for persona name
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
                    # Pass the live assistant_state_ref for Bark preset selection, or use current_lang_code_for_state determined earlier
                    telegram_messaging_utils_module_ref.send_voice_reply_to_telegram_user(
                        admin_id_int, assistant_response_text_llm, selected_bark_voice_preset, # Uses preset based on lang
                        telegram_bot_handler_instance_ref, tts_manager_module_ref
                    )
            except Exception as e_tg_send_admin: logger.error(f"ADMIN_LLM_FLOW: Error sending reply to admin TG ({source}): {e_tg_send_admin}", exc_info=True)
        elif ollama_error_occurred and telegram_bot_handler_instance_ref and telegram_bot_handler_instance_ref.async_loop: # Ensure loop exists
            try:
                admin_id_int = int(config.TELEGRAM_ADMIN_USER_ID)
                err_text_for_tg = "LLM processing failed."
                # Use current_lang_code_for_state which reflects detected or default language
                if current_lang_code_for_state == "ru": err_text_for_tg = "Ошибка обработки LLM."
                asyncio.run_coroutine_threadsafe( telegram_bot_handler_instance_ref.send_text_message_to_user(admin_id_int, err_text_for_tg), telegram_bot_handler_instance_ref.async_loop).result(timeout=10)
            except Exception as e_tg_err_send: logger.error(f"Failed to send LLM error to admin TG: {e_tg_err_send}")

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
    finally:
        if not llm_called: # If LLM was not called, reset ACT status here. If called, handle_admin_llm_interaction resets it.
            if gui_callbacks and callable(gui_callbacks.get('act_status_update')):
                gui_callbacks['act_status_update']("ACT: IDLE", "idle")
        # The final speak button update is handled by the caller in main.py's on_gui_recording_finished


def process_admin_telegram_text_message(
    user_id, text_message, chat_history_ref: list, user_state_ref: dict, assistant_state_ref: dict,
    global_states_lock_ref: threading.Lock, gui_callbacks: dict, telegram_bot_handler_instance_ref, ollama_ready_flag: bool,
    ollama_handler_module_ref, state_manager_module_ref, tts_manager_module_ref,
    telegram_messaging_utils_module_ref
    ):
    logger.info(f"Processing Admin Telegram text from {user_id}: '{text_message[:70]}...'")
    if gui_callbacks and callable(gui_callbacks.get('add_user_message_to_display')): # Show admin's TG text in GUI
        gui_callbacks['add_user_message_to_display'](text_message, source="telegram_admin")

    handle_admin_llm_interaction(
        input_text=text_message, source="telegram_admin", detected_language_code=None, # lang detection not critical here
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
            return

        if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update']("Loading Admin voice (TG)...")
        audio_numpy = _whisper_module_for_load_audio_ref.load_audio(wav_filepath)
        if gui_callbacks and callable(gui_callbacks.get('status_update')): gui_callbacks['status_update']("Transcribing Admin voice (TG)...")
        
        trans_text, trans_err, detected_lang = whisper_handler_module_ref.transcribe_audio(
            audio_np_array=audio_numpy, language=None, task="transcribe", gui_callbacks=gui_callbacks)
        del audio_numpy; audio_numpy=None; gc.collect()
        
        if not trans_err and trans_text:
            if gui_callbacks and callable(gui_callbacks.get('add_user_message_to_display')): # Show admin's TG voice text in GUI
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
             if telegram_bot_handler_instance_ref and telegram_bot_handler_instance_ref.async_loop:
                asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance_ref.send_text_message_to_user(user_id, "I didn't hear anything in your voice message."), telegram_bot_handler_instance_ref.async_loop)
        else: 
            logger.warning(f"Admin TG Voice Transcription failed: {trans_err}")
            if gui_callbacks and callable(gui_callbacks.get('add_user_message_to_display')):
                gui_callbacks['add_user_message_to_display'](f"[Transcription Error from Admin TG: {trans_err}]", source="telegram_voice_admin")
            if telegram_bot_handler_instance_ref and telegram_bot_handler_instance_ref.async_loop:
                asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance_ref.send_text_message_to_user(user_id, f"Couldn't transcribe voice: {trans_err or 'Recognition error.'}"), telegram_bot_handler_instance_ref.async_loop)
    except Exception as e:
        logger.error(f"Error processing admin voice WAV {wav_filepath}: {e}", exc_info=True)
        if telegram_bot_handler_instance_ref and telegram_bot_handler_instance_ref.async_loop:
             asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance_ref.send_text_message_to_user(user_id, "An error occurred while processing your voice message."), telegram_bot_handler_instance_ref.async_loop)
    finally:
        if os.path.exists(wav_filepath):
            try: os.remove(wav_filepath)
            except Exception as e_rem: logger.warning(f"Could not remove temp admin WAV {wav_filepath}: {e_rem}")