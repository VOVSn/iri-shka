# utils/customer_llm_processor.py
import json
import asyncio
import threading # For type hint

import config
from logger import get_logger

logger = get_logger("Iri-shka_App.utils.CustomerLLMProcessor")

def handle_customer_interaction_package(
    customer_user_id: int, chat_history_ref: list, user_state_ref: dict, assistant_state_ref: dict,
    global_states_lock_ref: threading.Lock, gui_callbacks: dict,
    telegram_bot_handler_instance_ref, state_manager_module_ref,
    ollama_handler_module_ref, tts_manager_module_ref,
    telegram_messaging_utils_module_ref
    ):
    function_signature_for_log = f"handle_customer_interaction_package(cust_id={customer_user_id})"
    logger.info(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Starting processing.")

    if gui_callbacks and callable(gui_callbacks.get('act_status_update')):
        gui_callbacks['act_status_update']("ACT: BUSY", "busy")
    try:
        customer_state_obj = state_manager_module_ref.load_or_initialize_customer_state(customer_user_id, gui_callbacks)
        current_stage = customer_state_obj.get("conversation_stage")
        if current_stage not in ["aggregating_messages", "acknowledged_pending_llm"]: 
            logger.warning(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Customer not in expected stage ('{current_stage}'). Skipping."); return

        interaction_blob_parts = []
        for msg_entry in customer_state_obj.get("chat_history", []):
            if msg_entry.get("sender") == "bot": interaction_blob_parts.append(f"Bot: {msg_entry.get('text')}")
            elif msg_entry.get("sender") == "customer": interaction_blob_parts.append(f"Customer ({customer_user_id}): {msg_entry.get('text')}")
        customer_interaction_text_blob_for_prompt = "\n".join(interaction_blob_parts)
        if not customer_interaction_text_blob_for_prompt: 
            logger.warning(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - No interaction text. Cannot proceed.")
            customer_state_obj["conversation_stage"] = "error_no_history_for_llm"; state_manager_module_ref.save_customer_state(customer_user_id, customer_state_obj, gui_callbacks); return

        with global_states_lock_ref: assistant_state_snapshot_for_customer_llm = assistant_state_ref.copy()
        admin_name_for_customer_prompt = assistant_state_snapshot_for_customer_llm.get("admin_name", config.DEFAULT_ASSISTANT_STATE["admin_name"])

        format_kwargs_customer = { 
            "admin_name_value": admin_name_for_customer_prompt,
            "actual_thanks_and_forwarded_message_value": config.TELEGRAM_NON_ADMIN_THANKS_AND_FORWARDED,
            "customer_user_id": str(customer_user_id),
            "customer_state_string": json.dumps(customer_state_obj, indent=2, ensure_ascii=False),
            "customer_interaction_text_blob": customer_interaction_text_blob_for_prompt,
        }
        expected_keys_customer = ["updated_customer_state", "updated_assistant_state", "message_for_admin", "polite_followup_message_for_customer"]

        ollama_data_cust, ollama_error_cust = ollama_handler_module_ref.call_ollama_for_chat_response(
            prompt_template_to_use=config.OLLAMA_CUSTOMER_PROMPT_TEMPLATE_V3, transcribed_text="", current_chat_history=[],
            current_user_state=customer_state_obj, current_assistant_state=assistant_state_snapshot_for_customer_llm,
            format_kwargs=format_kwargs_customer, expected_keys_override=expected_keys_customer, gui_callbacks=gui_callbacks)

        if ollama_error_cust: 
            logger.error(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Ollama error: {ollama_error_cust}")
            customer_state_obj["conversation_stage"] = "error_llm_processing"; state_manager_module_ref.save_customer_state(customer_user_id, customer_state_obj, gui_callbacks)
            error_admin_msg = f"{config.TELEGRAM_NON_ADMIN_PROCESSING_ERROR_TO_ADMIN_PREFIX} {customer_user_id}: {ollama_error_cust}"
            with global_states_lock_ref: 
                admin_chat_turn = {"user": f"[Sys Alert: Cust LLM Err ID {customer_user_id}]", "assistant": error_admin_msg, "source": "customer_llm_error_internal", "timestamp": state_manager_module_ref.get_current_timestamp_iso()}
                chat_history_ref.append(admin_chat_turn)
                updated_chat_hist = state_manager_module_ref.save_states(chat_history_ref, user_state_ref, assistant_state_ref, gui_callbacks)
                if len(chat_history_ref) != len(updated_chat_hist): chat_history_ref[:] = updated_chat_hist
                if gui_callbacks and callable(gui_callbacks.get('update_chat_display_from_list')): gui_callbacks['update_chat_display_from_list'](chat_history_ref)
            if telegram_bot_handler_instance_ref and config.TELEGRAM_ADMIN_USER_ID: 
                try: asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance_ref.send_text_message_to_user(int(config.TELEGRAM_ADMIN_USER_ID), error_admin_msg), telegram_bot_handler_instance_ref.async_loop).result(timeout=10)
                except Exception as e_tg_send_err: logger.error(f"Failed to send customer LLM error alert to admin TG: {e_tg_send_err}")
            return 

        updated_customer_state_from_llm = ollama_data_cust.get("updated_customer_state") 
        updated_assistant_state_changes_from_llm = ollama_data_cust.get("updated_assistant_state")
        message_for_admin_from_llm = ollama_data_cust.get("message_for_admin")
        polite_followup_for_customer_from_llm = ollama_data_cust.get("polite_followup_message_for_customer")

        if updated_customer_state_from_llm: state_manager_module_ref.save_customer_state(customer_user_id, updated_customer_state_from_llm, gui_callbacks)
        
        if updated_assistant_state_changes_from_llm: 
            with global_states_lock_ref:
                temp_as_copy = assistant_state_ref.copy()
                
                # Handle internal_tasks: pending and completed only
                if "internal_tasks" in updated_assistant_state_changes_from_llm and isinstance(updated_assistant_state_changes_from_llm["internal_tasks"], dict):
                    llm_tasks_dict = updated_assistant_state_changes_from_llm["internal_tasks"]
                    
                    if "internal_tasks" not in temp_as_copy or not isinstance(temp_as_copy.get("internal_tasks"), dict):
                        temp_as_copy["internal_tasks"] = {"pending": [], "completed": []}
                    
                    for task_type in ["pending", "completed"]: # Only process these two
                        if task_type not in temp_as_copy["internal_tasks"] or not isinstance(temp_as_copy["internal_tasks"].get(task_type), list):
                            temp_as_copy["internal_tasks"][task_type] = []
                        
                        new_tasks_from_llm = llm_tasks_dict.get(task_type, [])
                        if not isinstance(new_tasks_from_llm, list): 
                            new_tasks_from_llm = [str(new_tasks_from_llm)] if new_tasks_from_llm else []
                        
                        existing_tasks_in_merged = temp_as_copy["internal_tasks"][task_type]
                        temp_as_copy["internal_tasks"][task_type] = list(dict.fromkeys(
                            [str(t) for t in existing_tasks_in_merged] + [str(t) for t in new_tasks_from_llm]
                        ))
                    if "in_process" in llm_tasks_dict:
                         logger.warning(f"CUSTOMER_LLM_THREAD ({function_signature_for_log}): LLM provided 'in_process' tasks. This key is now ignored. Tasks: {llm_tasks_dict['in_process']}")
                
                # Update other assistant state keys
                for key, val_llm in updated_assistant_state_changes_from_llm.items():
                    if key != "internal_tasks": 
                        temp_as_copy[key] = val_llm
                        
                assistant_state_ref.clear(); assistant_state_ref.update(temp_as_copy)
                state_manager_module_ref.save_assistant_state_only(assistant_state_ref.copy(), gui_callbacks)
                
                if gui_callbacks:
                    asst_tasks_cust = assistant_state_ref.get("internal_tasks", {});
                    if not isinstance(asst_tasks_cust, dict): asst_tasks_cust = {"pending": [], "completed": []}
                    if callable(gui_callbacks.get('update_kanban_pending')): 
                        gui_callbacks['update_kanban_pending'](asst_tasks_cust.get("pending", []))
                    # update_kanban_in_process removed
                    if callable(gui_callbacks.get('update_kanban_completed')): 
                        gui_callbacks['update_kanban_completed'](asst_tasks_cust.get("completed", []))

        if message_for_admin_from_llm: 
            admin_summary_text = f"[Сводка по клиенту {customer_user_id}] {message_for_admin_from_llm}"
            with global_states_lock_ref: 
                admin_chat_turn_cust_summary = {"user": f"[Sys Report: Cust Interaction ID {customer_user_id}]", "assistant": admin_summary_text, "source": "customer_summary_internal", "timestamp": state_manager_module_ref.get_current_timestamp_iso()}
                chat_history_ref.append(admin_chat_turn_cust_summary)
                updated_chat_hist_admin = state_manager_module_ref.save_states(chat_history_ref, user_state_ref, assistant_state_ref, gui_callbacks)
                if len(chat_history_ref) != len(updated_chat_hist_admin): chat_history_ref[:] = updated_chat_hist_admin
                if gui_callbacks and callable(gui_callbacks.get('update_chat_display_from_list')): gui_callbacks['update_chat_display_from_list'](chat_history_ref)
            if telegram_bot_handler_instance_ref and config.TELEGRAM_ADMIN_USER_ID: 
                try: asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance_ref.send_text_message_to_user(int(config.TELEGRAM_ADMIN_USER_ID), admin_summary_text), telegram_bot_handler_instance_ref.async_loop).result(timeout=10)
                except Exception as e_tg_send_summary: logger.error(f"Failed to send customer summary to admin TG for {customer_user_id}: {e_tg_send_summary}")

        if polite_followup_for_customer_from_llm and polite_followup_for_customer_from_llm.upper() != "NO_CUSTOMER_FOLLOWUP_NEEDED":
            if telegram_bot_handler_instance_ref:
                try:
                    asyncio.run_coroutine_threadsafe(telegram_bot_handler_instance_ref.send_text_message_to_user(customer_user_id, polite_followup_for_customer_from_llm), telegram_bot_handler_instance_ref.async_loop).result(timeout=10)
                    if config.TELEGRAM_REPLY_WITH_VOICE: 
                        customer_bark_preset = config.BARK_VOICE_PRESET_RU 
                        telegram_messaging_utils_module_ref.send_voice_reply_to_telegram_user(customer_user_id, polite_followup_for_customer_from_llm, customer_bark_preset, telegram_bot_handler_instance_ref, tts_manager_module_ref)
                except Exception as e_tg_send_followup: logger.error(f"Failed to send polite follow-up to customer {customer_user_id}: {e_tg_send_followup}")
    finally:
        if gui_callbacks and callable(gui_callbacks.get('act_status_update')):
            gui_callbacks['act_status_update']("ACT: IDLE", "idle")
        logger.info(f"CUSTOMER_LLM_THREAD: {function_signature_for_log} - Processing finished.")