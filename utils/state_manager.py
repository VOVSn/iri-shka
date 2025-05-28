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
                    merged_data.update(data)
                    return merged_data
                else: # Should not happen if we always save dicts
                    logger.warning(f"Loaded data from {filepath} is not a dictionary. Initializing.")
                    # Fall through to initialize
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Error loading {entity_type} '{filepath}': {e}. Backing up and initializing.", exc_info=False)
            backup_corrupted_file(filepath)
            # Fall through to initialize
    
    # Initialize or re-initialize if loading failed or data was not dict
    try:
        parent_dir = os.path.dirname(filepath)
        if parent_dir and not os.path.exists(parent_dir): # Check if parent_dir is not empty string
            ensure_folder(parent_dir, gui_callbacks)

        # Always work with a copy of the default dictionary
        content_to_write = default_content_dict.copy()
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(content_to_write, f, indent=4, ensure_ascii=False)
        logger.info(f"Initialized {entity_type} '{filepath}' with default content.")
        return content_to_write # Return the copy that was written
    except IOError as e:
        error_msg = f"CRITICAL: Could not write initial {entity_type} '{filepath}': {e}"
        logger.critical(error_msg, exc_info=True)
        if gui_callbacks and 'messagebox_error' in gui_callbacks:
            gui_callbacks['messagebox_error']("File Error", f"Could not write initial {filepath}. System may be unstable.")
        return default_content_dict.copy() # Return a copy of default as a last resort in-memory state

# --- Main Admin/App State Loading ---
def load_initial_states(gui_callbacks=None):
    logger.info("Loading initial states (admin chat history, admin user state, assistant state)...")
    
    # Chat history is a list, not a dict with defaults in the same way
    chat_history_data = []
    if os.path.exists(config.CHAT_HISTORY_FILE):
        try:
            with open(config.CHAT_HISTORY_FILE, 'r', encoding='utf-8') as f:
                chat_history_data = json.load(f)
            if not isinstance(chat_history_data, list):
                logger.warning(f"{config.CHAT_HISTORY_FILE} did not contain a list. Initializing as empty list.")
                chat_history_data = []
                backup_corrupted_file(config.CHAT_HISTORY_FILE) # Backup the malformed file
                with open(config.CHAT_HISTORY_FILE, 'w', encoding='utf-8') as f: # Write empty list
                    json.dump(chat_history_data, f, indent=4, ensure_ascii=False)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Error loading chat history '{config.CHAT_HISTORY_FILE}': {e}. Backing up and initializing as empty list.", exc_info=False)
            backup_corrupted_file(config.CHAT_HISTORY_FILE)
            chat_history_data = []
            try: # Attempt to write the empty list
                with open(config.CHAT_HISTORY_FILE, 'w', encoding='utf-8') as f:
                    json.dump(chat_history_data, f, indent=4, ensure_ascii=False)
            except IOError as ioe_ch:
                 logger.error(f"Could not write initial empty chat history: {ioe_ch}")
    else: # File doesn't exist, create it with an empty list
        try:
            with open(config.CHAT_HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(chat_history_data, f, indent=4, ensure_ascii=False)
            logger.info(f"Initialized '{config.CHAT_HISTORY_FILE}' with empty list.")
        except IOError as ioe_ch_init:
            logger.error(f"Could not write initial empty chat history on first creation: {ioe_ch_init}")
    chat_history = list(chat_history_data)


    default_user_state_admin = config.DEFAULT_USER_STATE.copy()
    user_state_admin = _load_or_initialize_json_internal(
        config.USER_STATE_FILE, default_user_state_admin, "admin user state", gui_callbacks
    )

    default_assistant_state = config.DEFAULT_ASSISTANT_STATE.copy()
    assistant_state = _load_or_initialize_json_internal(
        config.ASSISTANT_STATE_FILE, default_assistant_state, "assistant state", gui_callbacks
    )
    
    # Ensure admin_name default is specifically checked if file existed but key was missing
    if "admin_name" not in assistant_state:
        assistant_state["admin_name"] = default_assistant_state["admin_name"]
        logger.warning(f"Key 'admin_name' missing in loaded assistant_state. Added default: {assistant_state['admin_name']}")

    logger.info("Initial admin/assistant states loaded/initialized and defaults ensured.")
    return chat_history, user_state_admin, assistant_state

# --- Helpers for just loading/saving assistant state (for thread safety) ---
def load_assistant_state_only(gui_callbacks=None) -> dict:
    default_assistant_state = config.DEFAULT_ASSISTANT_STATE.copy()
    loaded_state = _load_or_initialize_json_internal(
        config.ASSISTANT_STATE_FILE, default_assistant_state, "assistant state (isolated load)", gui_callbacks
    )
    # Ensure admin_name default again after isolated load
    if "admin_name" not in loaded_state:
        loaded_state["admin_name"] = default_assistant_state["admin_name"]
    return loaded_state

def save_assistant_state_only(assistant_state_data: dict, gui_callbacks=None) -> bool:
    try:
        # Ensure parent directory exists for assistant state file if it's somehow deleted
        parent_dir = os.path.dirname(config.ASSISTANT_STATE_FILE)
        if parent_dir and not os.path.exists(parent_dir):
            ensure_folder(parent_dir, gui_callbacks)

        with open(config.ASSISTANT_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(assistant_state_data, f, indent=4, ensure_ascii=False)
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
    
    # Save assistant state (contains admin_name potentially updated by admin)
    save_assistant_state_only(assistant_state, gui_callbacks)

    # Save admin's user state
    try:
        parent_dir = os.path.dirname(config.USER_STATE_FILE) # Ensure parent dir
        if parent_dir and not os.path.exists(parent_dir): ensure_folder(parent_dir, gui_callbacks)
        with open(config.USER_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_state_admin, f, indent=4, ensure_ascii=False)
        logger.info("Admin User state saved successfully.")
    except IOError as e:
        error_msg = f"Error saving admin user state file: {e}"
        logger.error(error_msg, exc_info=True)
        if gui_callbacks and 'messagebox_warn' in gui_callbacks:
             gui_callbacks['messagebox_warn']("Save Error", f"Could not save admin user state: {e}")

    # Save admin's chat history
    current_chat_history = list(chat_history)
    if len(current_chat_history) > config.MAX_HISTORY_TURNS:
        trimmed_count = len(current_chat_history) - config.MAX_HISTORY_TURNS
        current_chat_history = current_chat_history[-config.MAX_HISTORY_TURNS:]
        logger.info(f"Admin chat history trimmed by {trimmed_count} turns to maintain max {config.MAX_HISTORY_TURNS} turns.")

    try:
        parent_dir = os.path.dirname(config.CHAT_HISTORY_FILE) # Ensure parent dir
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
    """
    Loads a customer's state. If not found or corrupted, initializes with defaults.
    The `user_id` in the default state is updated with the provided `telegram_user_id`.
    """
    ensure_folder(config.CUSTOMER_STATES_FOLDER, gui_callbacks) # Ensures base folder like "data/customer_states"
    filepath = get_customer_state_filepath(telegram_user_id)

    default_customer_state_template = config.DEFAULT_NON_ADMIN_USER_STATE.copy()
    default_customer_state_template["user_id"] = telegram_user_id # Set user_id for this instance
    if default_customer_state_template["last_message_timestamp"] == "": # Ensure it is empty if None was default
         default_customer_state_template["last_message_timestamp"] = ""


    customer_state = _load_or_initialize_json_internal(
        filepath, default_customer_state_template, f"customer {telegram_user_id} state", gui_callbacks
    )
    
    # _load_or_initialize_json_internal now merges, so specific key checks are less critical here
    # but ensuring user_id and type_of_user is a good sanity check.
    if customer_state.get("user_id") != telegram_user_id:
        logger.warning(f"Correcting user_id in state for customer {telegram_user_id} post-load. File had: {customer_state.get('user_id')}")
        customer_state["user_id"] = telegram_user_id
    if customer_state.get("type_of_user") != "customer":
        customer_state["type_of_user"] = "customer"
        logger.warning(f"Correcting type_of_user for customer {telegram_user_id}.")

    return customer_state


def save_customer_state(telegram_user_id: int, customer_state_data: dict, gui_callbacks=None) -> bool:
    """Saves a customer's state to their JSON file."""
    # ensure_folder(config.CUSTOMER_STATES_FOLDER, gui_callbacks) # Parent dir ensured by _load_or_initialize_json_internal or if saving directly
    filepath = get_customer_state_filepath(telegram_user_id)
    try:
        parent_dir = os.path.dirname(filepath)
        if parent_dir and not os.path.exists(parent_dir):
             ensure_folder(parent_dir, gui_callbacks) # Make sure it exists before write

        # Ensure last_message_timestamp is a string for JSON
        if "last_message_timestamp" in customer_state_data and customer_state_data["last_message_timestamp"] is None:
            customer_state_data["last_message_timestamp"] = ""
        # Ensure chat_history is a list
        if "chat_history" in customer_state_data and not isinstance(customer_state_data["chat_history"], list):
            customer_state_data["chat_history"] = []
        # Ensure calendar_events is a list
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