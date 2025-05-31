# utils/state_manager.py
import json
import os
import sys
from .file_utils import backup_corrupted_file, ensure_folder # Use relative import for utils
import config # Import config to access default states
from logger import get_logger # Assuming logger.py is in project root, adjust if it's also in utils
import datetime # For timestamps

logger = get_logger("Iri-shka_App.StateManager")

def get_current_timestamp_iso():
    """Returns the current UTC time as an ISO 8601 string."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

# --- Generic JSON Load/Init (Internal Helper) ---
def _load_or_initialize_json_internal(filepath, default_content_dict: dict, entity_type="file", gui_callbacks=None):
    """
    Loads JSON from filepath or initializes with a copy of default_content_dict.
    Ensures parent directory exists. Returns a dictionary.
    """
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logger.info(f"Loaded JSON from {entity_type} path: {filepath}")
                # Ensure it's a dict, merge with defaults to ensure all keys exist
                if isinstance(data, dict):
                    # Start with a copy of defaults, then update with loaded data
                    # This ensures new keys in default_content_dict are added if missing in the file
                    merged_data = default_content_dict.copy()
                    merged_data.update(data) # Overwrites defaults with loaded values, adds new default keys
                    
                    # Specifically for assistant_state, ensure internal_tasks structure is correct
                    if entity_type == "assistant state" or "assistant state (" in entity_type:
                        if "internal_tasks" not in merged_data:
                            merged_data["internal_tasks"] = default_content_dict.get("internal_tasks", {"pending": [], "completed": []}).copy()
                            logger.info(f"Initialized 'internal_tasks' in {filepath} as it was missing.")
                        elif not isinstance(merged_data["internal_tasks"], dict):
                            logger.warning(f"'internal_tasks' in {filepath} is not a dict. Resetting.")
                            merged_data["internal_tasks"] = default_content_dict.get("internal_tasks", {"pending": [], "completed": []}).copy()
                        else: # It is a dict, ensure 'pending' and 'completed' exist, remove 'in_process'
                            tasks_dict = merged_data["internal_tasks"]
                            if "in_process" in tasks_dict:
                                logger.info(f"Removing 'in_process' tasks from {filepath} during load/merge.")
                                # Optionally, move 'in_process' tasks to 'pending' if desired, or just discard
                                # For now, just discarding.
                                # if isinstance(tasks_dict["in_process"], list) and tasks_dict["in_process"]:
                                #     if "pending" not in tasks_dict or not isinstance(tasks_dict.get("pending"),list):
                                #         tasks_dict["pending"] = []
                                #     tasks_dict["pending"].extend(tasks_dict["in_process"])
                                #     tasks_dict["pending"] = list(dict.fromkeys(tasks_dict["pending"])) # Deduplicate
                                del tasks_dict["in_process"]
                            
                            if "pending" not in tasks_dict or not isinstance(tasks_dict.get("pending"), list):
                                tasks_dict["pending"] = default_content_dict.get("internal_tasks",{}).get("pending",[]).copy()
                            if "completed" not in tasks_dict or not isinstance(tasks_dict.get("completed"), list):
                                 tasks_dict["completed"] = default_content_dict.get("internal_tasks",{}).get("completed",[]).copy()
                    
                    # Specifically for user_state, ensure 'todos' is NOT present
                    if entity_type == "admin user state" or "admin user state (" in entity_type:
                        if "todos" in merged_data:
                            logger.info(f"Removing 'todos' key from admin user state in {filepath} during load/merge.")
                            del merged_data["todos"]
                            
                    return merged_data
                else: 
                    logger.warning(f"Loaded data from {filepath} is not a dictionary. Initializing.")
                    # Fall through to initialize
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Error loading {entity_type} '{filepath}': {e}. Backing up and initializing.", exc_info=False)
            backup_corrupted_file(filepath)
            # Fall through to initialize
    
    # Initialize or re-initialize if loading failed or data was not dict
    try:
        parent_dir = os.path.dirname(filepath)
        if parent_dir and not os.path.exists(parent_dir): 
            ensure_folder(parent_dir, gui_callbacks)

        content_to_write = default_content_dict.copy() # Always work with a fresh copy
        
        # Ensure correct structure for assistant state defaults if this is an initialization
        if entity_type == "assistant state" or "assistant state (" in entity_type:
            if "internal_tasks" in content_to_write and "in_process" in content_to_write["internal_tasks"]:
                del content_to_write["internal_tasks"]["in_process"]
            if "internal_tasks" not in content_to_write or not isinstance(content_to_write.get("internal_tasks"), dict):
                 content_to_write["internal_tasks"] = {"pending": [], "completed": []}
            if "pending" not in content_to_write["internal_tasks"]: content_to_write["internal_tasks"]["pending"] = []
            if "completed" not in content_to_write["internal_tasks"]: content_to_write["internal_tasks"]["completed"] = []


        # Ensure 'todos' is not in default admin user state if initializing
        if entity_type == "admin user state" or "admin user state (" in entity_type:
            if "todos" in content_to_write:
                del content_to_write["todos"]

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(content_to_write, f, indent=4, ensure_ascii=False)
        logger.info(f"Initialized {entity_type} '{filepath}' with default content.")
        return content_to_write 
    except IOError as e:
        error_msg = f"CRITICAL: Could not write initial {entity_type} '{filepath}': {e}"
        logger.critical(error_msg, exc_info=True)
        if gui_callbacks and 'messagebox_error' in gui_callbacks:
            gui_callbacks['messagebox_error']("File Error", f"Could not write initial {filepath}. System may be unstable.")
        
        final_default_copy = default_content_dict.copy()
        if entity_type == "assistant state" or "assistant state (" in entity_type:
            if "internal_tasks" in final_default_copy and "in_process" in final_default_copy["internal_tasks"]:
                del final_default_copy["internal_tasks"]["in_process"]
        if entity_type == "admin user state" or "admin user state (" in entity_type:
            if "todos" in final_default_copy:
                del final_default_copy["todos"]
        return final_default_copy


# --- Main Admin/App State Loading ---
def load_initial_states(gui_callbacks=None):
    logger.info("Loading initial states (admin chat history, admin user state, assistant state)...")
    
    chat_history_data = []
    if os.path.exists(config.CHAT_HISTORY_FILE):
        try:
            with open(config.CHAT_HISTORY_FILE, 'r', encoding='utf-8') as f:
                chat_history_data = json.load(f)
            if not isinstance(chat_history_data, list):
                logger.warning(f"{config.CHAT_HISTORY_FILE} did not contain a list. Initializing as empty list.")
                chat_history_data = []
                backup_corrupted_file(config.CHAT_HISTORY_FILE) 
                with open(config.CHAT_HISTORY_FILE, 'w', encoding='utf-8') as f: 
                    json.dump(chat_history_data, f, indent=4, ensure_ascii=False)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Error loading chat history '{config.CHAT_HISTORY_FILE}': {e}. Backing up and initializing as empty list.", exc_info=False)
            backup_corrupted_file(config.CHAT_HISTORY_FILE)
            chat_history_data = []
            try: 
                with open(config.CHAT_HISTORY_FILE, 'w', encoding='utf-8') as f:
                    json.dump(chat_history_data, f, indent=4, ensure_ascii=False)
            except IOError as ioe_ch:
                 logger.error(f"Could not write initial empty chat history: {ioe_ch}")
    else: 
        try:
            with open(config.CHAT_HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(chat_history_data, f, indent=4, ensure_ascii=False)
            logger.info(f"Initialized '{config.CHAT_HISTORY_FILE}' with empty list.")
        except IOError as ioe_ch_init:
            logger.error(f"Could not write initial empty chat history on first creation: {ioe_ch_init}")
    chat_history = list(chat_history_data)


    default_user_state_admin_template = config.DEFAULT_USER_STATE.copy()
    # Ensure 'todos' is not in the template being passed if it somehow lingered in config.py
    if "todos" in default_user_state_admin_template: 
        del default_user_state_admin_template["todos"]
        
    user_state_admin = _load_or_initialize_json_internal(
        config.USER_STATE_FILE, default_user_state_admin_template, "admin user state", gui_callbacks
    )

    default_assistant_state_template = config.DEFAULT_ASSISTANT_STATE.copy()
    # Ensure internal_tasks has the correct structure in the template passed
    if "internal_tasks" in default_assistant_state_template:
        if "in_process" in default_assistant_state_template["internal_tasks"]:
            del default_assistant_state_template["internal_tasks"]["in_process"]
        if "pending" not in default_assistant_state_template["internal_tasks"]: default_assistant_state_template["internal_tasks"]["pending"] = []
        if "completed" not in default_assistant_state_template["internal_tasks"]: default_assistant_state_template["internal_tasks"]["completed"] = []
    else:
        default_assistant_state_template["internal_tasks"] = {"pending": [], "completed": []}


    assistant_state = _load_or_initialize_json_internal(
        config.ASSISTANT_STATE_FILE, default_assistant_state_template, "assistant state", gui_callbacks
    )
    
    if "admin_name" not in assistant_state: # From original logic
        assistant_state["admin_name"] = default_assistant_state_template["admin_name"]
        logger.warning(f"Key 'admin_name' missing in loaded assistant_state. Added default: {assistant_state['admin_name']}")

    logger.info("Initial admin/assistant states loaded/initialized and defaults ensured.")
    return chat_history, user_state_admin, assistant_state

# --- Helpers for just loading/saving assistant state (for thread safety) ---
def load_assistant_state_only(gui_callbacks=None) -> dict:
    default_assistant_state_template = config.DEFAULT_ASSISTANT_STATE.copy()
    if "internal_tasks" in default_assistant_state_template and "in_process" in default_assistant_state_template["internal_tasks"]:
        del default_assistant_state_template["internal_tasks"]["in_process"]
    if "internal_tasks" not in default_assistant_state_template or not isinstance(default_assistant_state_template.get("internal_tasks"),dict):
        default_assistant_state_template["internal_tasks"] = {"pending":[], "completed":[]}
    if "pending" not in default_assistant_state_template["internal_tasks"]: default_assistant_state_template["internal_tasks"]["pending"] = []
    if "completed" not in default_assistant_state_template["internal_tasks"]: default_assistant_state_template["internal_tasks"]["completed"] = []


    loaded_state = _load_or_initialize_json_internal(
        config.ASSISTANT_STATE_FILE, default_assistant_state_template, "assistant state (isolated load)", gui_callbacks
    )
    if "admin_name" not in loaded_state: # From original logic
        loaded_state["admin_name"] = default_assistant_state_template["admin_name"]
    return loaded_state

def save_assistant_state_only(assistant_state_data: dict, gui_callbacks=None) -> bool:
    try:
        parent_dir = os.path.dirname(config.ASSISTANT_STATE_FILE)
        if parent_dir and not os.path.exists(parent_dir):
            ensure_folder(parent_dir, gui_callbacks)

        # Before saving, ensure the structure is correct
        state_to_save = assistant_state_data.copy()
        if "internal_tasks" in state_to_save:
            if isinstance(state_to_save["internal_tasks"], dict) and "in_process" in state_to_save["internal_tasks"]:
                del state_to_save["internal_tasks"]["in_process"]
            # Ensure pending and completed are lists
            if not isinstance(state_to_save["internal_tasks"].get("pending"), list): state_to_save["internal_tasks"]["pending"] = []
            if not isinstance(state_to_save["internal_tasks"].get("completed"), list): state_to_save["internal_tasks"]["completed"] = []
        else: # Ensure it exists if somehow deleted before save
             state_to_save["internal_tasks"] = {"pending": [], "completed": []}


        with open(config.ASSISTANT_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state_to_save, f, indent=4, ensure_ascii=False)
        logger.debug("Assistant state saved successfully (isolated save).")
        return True
    except IOError as e:
        error_msg = f"Error saving assistant state file (isolated save): {e}"
        logger.error(error_msg, exc_info=True)
        if gui_callbacks and 'messagebox_warn' in gui_callbacks:
             gui_callbacks['messagebox_warn']("Save Error", f"Could not save assistant state: {e}")
        return False

# --- Main Admin/App State Saving (user_state is admin's) ---
def save_states(chat_history, user_state_admin, assistant_state, gui_callbacks=None):
    logger.debug("Attempting to save admin user, assistant, and admin chat history states.")
    
    save_assistant_state_only(assistant_state, gui_callbacks) # This now ensures correct structure

    # Save admin's user state
    try:
        parent_dir = os.path.dirname(config.USER_STATE_FILE) 
        if parent_dir and not os.path.exists(parent_dir): ensure_folder(parent_dir, gui_callbacks)
        
        admin_state_to_save = user_state_admin.copy()
        if "todos" in admin_state_to_save: # Ensure 'todos' is removed before saving
            del admin_state_to_save["todos"]
            
        with open(config.USER_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(admin_state_to_save, f, indent=4, ensure_ascii=False)
        logger.info("Admin User state saved successfully.")
    except IOError as e:
        error_msg = f"Error saving admin user state file: {e}"
        logger.error(error_msg, exc_info=True)
        if gui_callbacks and 'messagebox_warn' in gui_callbacks:
             gui_callbacks['messagebox_warn']("Save Error", f"Could not save admin user state: {e}")

    current_chat_history = list(chat_history)
    if len(current_chat_history) > config.MAX_HISTORY_TURNS:
        trimmed_count = len(current_chat_history) - config.MAX_HISTORY_TURNS
        current_chat_history = current_chat_history[-config.MAX_HISTORY_TURNS:]
        logger.info(f"Admin chat history trimmed by {trimmed_count} turns to maintain max {config.MAX_HISTORY_TURNS} turns.")

    try:
        parent_dir = os.path.dirname(config.CHAT_HISTORY_FILE) 
        if parent_dir and not os.path.exists(parent_dir): ensure_folder(parent_dir, gui_callbacks)
        with open(config.CHAT_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(current_chat_history, f, indent=4, ensure_ascii=False)
        logger.info("Admin chat history saved successfully.")
    except IOError as e:
        error_msg = f"Error saving admin chat history file: {e}"
        logger.error(error_msg, exc_info=True)
        if gui_callbacks and 'messagebox_warn' in gui_callbacks:
             gui_callbacks['messagebox_warn']("Save Error", f"Could not save admin chat history: {e}")

    return current_chat_history


# --- Customer State Management ---
def get_customer_state_filepath(telegram_user_id: int) -> str:
    return os.path.join(config.CUSTOMER_STATES_FOLDER, f"{str(telegram_user_id)}_state.json")

def load_or_initialize_customer_state(telegram_user_id: int, gui_callbacks=None) -> dict:
    ensure_folder(config.CUSTOMER_STATES_FOLDER, gui_callbacks) 
    filepath = get_customer_state_filepath(telegram_user_id)

    default_customer_state_template = config.DEFAULT_NON_ADMIN_USER_STATE.copy()
    default_customer_state_template["user_id"] = telegram_user_id 
    if default_customer_state_template["last_message_timestamp"] == "": 
         default_customer_state_template["last_message_timestamp"] = ""


    customer_state = _load_or_initialize_json_internal(
        filepath, default_customer_state_template, f"customer {telegram_user_id} state", gui_callbacks
    )
    
    if customer_state.get("user_id") != telegram_user_id:
        logger.warning(f"Correcting user_id in state for customer {telegram_user_id} post-load. File had: {customer_state.get('user_id')}")
        customer_state["user_id"] = telegram_user_id
    if customer_state.get("type_of_user") != "customer":
        customer_state["type_of_user"] = "customer"
        logger.warning(f"Correcting type_of_user for customer {telegram_user_id}.")

    return customer_state


def save_customer_state(telegram_user_id: int, customer_state_data: dict, gui_callbacks=None) -> bool:
    filepath = get_customer_state_filepath(telegram_user_id)
    try:
        parent_dir = os.path.dirname(filepath)
        if parent_dir and not os.path.exists(parent_dir):
             ensure_folder(parent_dir, gui_callbacks) 

        if "last_message_timestamp" in customer_state_data and customer_state_data["last_message_timestamp"] is None:
            customer_state_data["last_message_timestamp"] = ""
        if "chat_history" in customer_state_data and not isinstance(customer_state_data["chat_history"], list):
            customer_state_data["chat_history"] = []
        if "calendar_events" in customer_state_data and not isinstance(customer_state_data["calendar_events"], list):
            customer_state_data["calendar_events"] = []

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(customer_state_data, f, indent=4, ensure_ascii=False)
        logger.info(f"Customer state for user ID {telegram_user_id} saved to {filepath}")
        return True
    except IOError as e:
        error_msg = f"Error saving customer state for user ID {telegram_user_id} to '{filepath}': {e}"
        logger.error(error_msg, exc_info=True)
        if gui_callbacks and 'messagebox_warn' in gui_callbacks:
            gui_callbacks['messagebox_warn']("Customer State Save Error", error_msg)
        return False