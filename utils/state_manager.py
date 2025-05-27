# utils/state_manager.py
import json
import os
import sys
from utils.file_utils import backup_corrupted_file 
import config
from logger import get_logger

logger = get_logger("Iri-shka_App.StateManager")

def load_or_initialize_json(filepath, default_content, gui_callbacks=None):
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logger.info(f"Loaded JSON from {filepath}")
                return data
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Error loading '{filepath}': {e}. Backing up and initializing.", exc_info=False)
            backup_corrupted_file(filepath) 
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(default_content, f, indent=4, ensure_ascii=False)
        logger.info(f"Initialized '{filepath}' with default content.")
        return dict(default_content) if isinstance(default_content, dict) else list(default_content)
    except IOError as e:
        error_msg = f"FATAL: Could not write initial '{filepath}': {e}"
        logger.critical(error_msg, exc_info=True)
        if gui_callbacks and 'messagebox_error' in gui_callbacks:
            gui_callbacks['messagebox_error']("File Error", f"Could not write initial {filepath}. Exiting.")
        sys.exit(1) 

def load_initial_states(gui_callbacks=None): 
    logger.info("Loading initial states (chat history, user state, assistant state)...")
    chat_history = list(load_or_initialize_json(config.CHAT_HISTORY_FILE, [], gui_callbacks))
    
    default_user_state = config.DEFAULT_USER_STATE.copy()
    user_state = dict(load_or_initialize_json(config.USER_STATE_FILE, default_user_state, gui_callbacks))
    
    default_assistant_state = config.DEFAULT_ASSISTANT_STATE.copy()
    assistant_state = dict(load_or_initialize_json(config.ASSISTANT_STATE_FILE, default_assistant_state, gui_callbacks))

    # Ensure critical default keys exist if missing from loaded state
    # This is a simple merge, more sophisticated merging might be needed for nested dicts
    for key, value in default_user_state.items():
        if key not in user_state:
            logger.warning(f"Key '{key}' missing in loaded user_state. Adding default value.")
            user_state[key] = value
            
    for key, value in default_assistant_state.items():
        if key not in assistant_state:
            logger.warning(f"Key '{key}' missing in loaded assistant_state. Adding default value.")
            assistant_state[key] = value
            # Special handling for nested dicts like 'internal_tasks' if they might be entirely missing
            if key == "internal_tasks" and not isinstance(assistant_state[key], dict):
                 assistant_state[key] = default_assistant_state["internal_tasks"].copy()
            elif key == "current_emotion" and not isinstance(assistant_state[key], dict):
                 assistant_state[key] = default_assistant_state["current_emotion"].copy()


    logger.info("Initial states loaded/initialized and defaults ensured.")
    return chat_history, user_state, assistant_state

def save_states(chat_history, user_state, assistant_state, gui_callbacks=None):
    logger.debug("Attempting to save user, assistant, and chat history states.")
    try:
        with open(config.USER_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_state, f, indent=4, ensure_ascii=False)
        with open(config.ASSISTANT_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(assistant_state, f, indent=4, ensure_ascii=False)
        logger.info("User and Assistant states saved successfully.")
    except IOError as e:
        error_msg = f"Error saving state files (user/assistant): {e}"
        logger.error(error_msg, exc_info=True)
        if gui_callbacks and 'status_update' in gui_callbacks:
            gui_callbacks['status_update']("Warning: Could not save state files.")
        elif gui_callbacks and 'messagebox_warn' in gui_callbacks: # elif to avoid double messages
             gui_callbacks['messagebox_warn']("Save Error", f"Could not save state files: {e}")

    current_chat_history = list(chat_history) 
    if len(current_chat_history) > config.MAX_HISTORY_TURNS:
        trimmed_count = len(current_chat_history) - config.MAX_HISTORY_TURNS
        current_chat_history = current_chat_history[-config.MAX_HISTORY_TURNS:]
        logger.info(f"Chat history trimmed by {trimmed_count} turns to maintain max {config.MAX_HISTORY_TURNS} turns.")

    try:
        with open(config.CHAT_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(current_chat_history, f, indent=4, ensure_ascii=False)
        logger.info("Chat history saved successfully.")
    except IOError as e:
        error_msg = f"Error saving chat history file: {e}"
        logger.error(error_msg, exc_info=True)
        if gui_callbacks and 'status_update' in gui_callbacks:
            gui_callbacks['status_update']("Warning: Could not save chat history.")
        elif gui_callbacks and 'messagebox_warn' in gui_callbacks:
             gui_callbacks['messagebox_warn']("Save Error", f"Could not save chat history: {e}")

    return current_chat_history