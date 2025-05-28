# logger.py
import logging
import os
from logging.handlers import RotatingFileHandler

LOGS_DIR_NAME = "logs"
APP_LOG_FILENAME = "app.log"
ERROR_LOG_FILENAME = "error.log"

# Ensure logs directory exists
if not os.path.exists(LOGS_DIR_NAME):
    try:
        os.makedirs(LOGS_DIR_NAME)
    except OSError as e:
        # This is a critical failure, print to stderr and raise
        print(f"CRITICAL: Could not create logs directory '{LOGS_DIR_NAME}'. Error: {e}", file=os.sys.stderr)
        raise

APP_LOG_FILE_PATH = os.path.join(LOGS_DIR_NAME, APP_LOG_FILENAME)
ERROR_LOG_FILE_PATH = os.path.join(LOGS_DIR_NAME, ERROR_LOG_FILENAME)

# --- Main Application Logger Setup ---
_app_logger_instance = logging.getLogger("Iri-shka_App")
_app_logger_instance.setLevel(logging.INFO)  # Set to DEBUG to capture all levels from modules
_app_logger_instance.propagate = False 

# --- Formatter ---
log_formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s'
)

# --- Handler for app.log (DEBUG and above) ---
try:
    app_file_handler = RotatingFileHandler(
        APP_LOG_FILE_PATH,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=5,
        encoding='utf-8'
    )
    app_file_handler.setLevel(logging.DEBUG) # Capture DEBUG and above for app.log
    app_file_handler.setFormatter(log_formatter)
    _app_logger_instance.addHandler(app_file_handler)
except IOError as e:
    print(f"WARNING: Could not attach file handler for '{APP_LOG_FILE_PATH}'. Error: {e}", file=os.sys.stderr)


# --- Handler for error.log (WARNING and above) ---
try:
    error_file_handler = RotatingFileHandler(
        ERROR_LOG_FILE_PATH,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=5,
        encoding='utf-8'
    )
    error_file_handler.setLevel(logging.WARNING)
    error_file_handler.setFormatter(log_formatter)
    _app_logger_instance.addHandler(error_file_handler)
except IOError as e:
    print(f"WARNING: Could not attach file handler for '{ERROR_LOG_FILE_PATH}'. Error: {e}", file=os.sys.stderr)


# --- Optional: Console Handler (for development/debugging) ---
# console_handler = logging.StreamHandler(os.sys.stdout)
# console_handler.setLevel(logging.INFO) # Or logging.DEBUG for more verbosity
# console_handler.setFormatter(log_formatter)
# _app_logger_instance.addHandler(console_handler)


def get_logger(module_name_or_full_name: str):
    """
    Returns a logger instance. If a full name like "Iri-shka_App.Module" is given,
    it uses that. Otherwise, it creates a child of "Iri-shka_App".
    """
    if "Iri-shka_App" in module_name_or_full_name:
        return logging.getLogger(module_name_or_full_name)
    return logging.getLogger(f"Iri-shka_App.{module_name_or_full_name}")


_internal_setup_logger = logging.getLogger("Iri-shka_App.LoggerSetup")
# If _app_logger_instance has handlers, _internal_setup_logger will use them due to propagation by default
# unless we add a specific handler here or set _internal_setup_logger.propagate = False.
# For simplicity, let it propagate to the main app logger's handlers.
# If _app_logger_instance has NO handlers (e.g., file I/O errors), we add a console handler for setup messages.
if not _app_logger_instance.hasHandlers():
    _ch_setup = logging.StreamHandler(os.sys.stdout)
    _ch_setup.setFormatter(log_formatter)
    _app_logger_instance.addHandler(_ch_setup) # Add to main so LoggerSetup also uses it
    _internal_setup_logger.warning("Main app logger had no file handlers. Added temporary console handler for setup logs.")


_internal_setup_logger.info(
    "Logging system initialized. App log: %s, Error log: %s. Main logger level: %s",
    APP_LOG_FILE_PATH, ERROR_LOG_FILE_PATH, logging.getLevelName(_app_logger_instance.level)
)
if not any(isinstance(h, RotatingFileHandler) for h in _app_logger_instance.handlers):
    _internal_setup_logger.warning(
        "No RotatingFileHandlers were successfully attached to the main application logger. Logs may only appear on console."
    )