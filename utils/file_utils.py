# utils/file_utils.py
import os
import shutil
import datetime
# from tkinter import messagebox # REMOVED

# Assuming logger.py is in the same 'utils' directory
from logger import get_logger

logger = get_logger(__name__) # Gets the Iri-shka_App logger

def ensure_folder(folder_name, gui_callbacks=None): # Added gui_callbacks parameter
    if not os.path.exists(folder_name):
        try:
            os.makedirs(folder_name)
            logger.info(f"Created folder: {folder_name}")
        except OSError as e:
            error_msg = f"Could not create folder {folder_name}: {e}"
            logger.error(error_msg, exc_info=True) # Log with exception info
            if gui_callbacks and 'messagebox_error' in gui_callbacks:
                gui_callbacks['messagebox_error']("Error", error_msg)
            # else: # logger.error already handled it
            return False
    return True

def backup_corrupted_file(filepath): # No GUI interaction
    corrupted_backup_path = filepath + ".corrupted_" + datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    try:
        shutil.move(filepath, corrupted_backup_path)
        logger.info(f"Backed up corrupted file '{filepath}' to '{corrupted_backup_path}'")
    except OSError as ose:
        logger.error(f"Could not back up corrupted file '{filepath}': {ose}", exc_info=True)