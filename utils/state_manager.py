# utils/state_manager.py
import json
import os
import sys
# from tkinter import messagebox # REMOVED
from .file_utils import backup_corrupted_file # Assumes backup_corrupted_file logs its own errors
import config

# Assuming logger.py is in the same 'utils' directory
from logger import get_logger

logger = get_logger(__name__)

def load_or_initialize_json(filepath, default_content, gui_callbacks=None):
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logger.info(f"Loaded JSON from {filepath}")
                return data
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Error loading '{filepath}': {e}. Backing up and initializing with defaults.", exc_info=False)
            # backup_corrupted_file already logs its outcome
            backup_corrupted_file(filepath) # backup_corrupted_file doesn't use gui_callbacks directly
            # Continue to initialize with default
    try:
        # This block runs if file didn't exist, or if loading failed and it was backed up
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(default_content, f, indent=4, ensure_ascii=False)
        logger.info(f"Initialized '{filepath}' with default content.")
        # Return a copy to prevent modification of the original default_content dict/list in config
        if isinstance(default_content, list):
            return list(default_content)
        elif isinstance(default_content, dict):
            return dict(default_content)
        else: # Should not happen with current defaults
            return default_content

    except IOError as e:
        error_msg = f"FATAL: Could not write initial '{filepath}': {e}"
        logger.critical(error_msg, exc_info=True)
        if gui_callbacks and 'messagebox_error' in gui_callbacks:
            gui_callbacks['messagebox_error']("File Error", f"Could not write initial {filepath}. Exiting.")
        # else: # logger.critical already handled it
        sys.exit(1) # This is a critical failure

def load_initial_states(gui_callbacks=None): # Added gui_callbacks parameter
    logger.info("Loading initial states (chat history, user state, assistant state)...")
    chat_history = list(load_or_initialize_json(config.CHAT_HISTORY_FILE, [], gui_callbacks))
    user_state = dict(load_or_initialize_json(config.USER_STATE_FILE, config.DEFAULT_USER_STATE, gui_callbacks))
    assistant_state = dict(load_or_initialize_json(config.ASSISTANT_STATE_FILE, config.DEFAULT_ASSISTANT_STATE, gui_callbacks))
    logger.info("Initial states loaded/initialized.")
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
        elif gui_callbacks and 'messagebox_warn' in gui_callbacks:
             gui_callbacks['messagebox_warn']("Save Error", f"Could not save state files: {e}")

    current_chat_history = list(chat_history) # Work with a copy
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

    return current_chat_history # Return the potentially trimmed history