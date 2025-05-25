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
# We will configure a single logger and add multiple handlers to it.
# Modules will get this logger instance.
_app_logger_instance = logging.getLogger("Iri-shka_App")
_app_logger_instance.setLevel(logging.DEBUG)  # Set to lowest level, handlers will filter
_app_logger_instance.propagate = False # Prevent log duplication if root logger is configured elsewhere

# --- Formatter ---
# Consistent format for all log messages
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
    app_file_handler.setLevel(logging.DEBUG)
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
# This will also print logs to the terminal.
# Comment out or set a higher level (e.g., logging.INFO) for production if desired.
# console_handler = logging.StreamHandler(os.sys.stdout)
# console_handler.setLevel(logging.INFO) # Or logging.DEBUG
# console_handler.setFormatter(log_formatter)
# _app_logger_instance.addHandler(console_handler)


def get_logger(module_name: str):
    """
    Returns a logger instance that is a child of the main application logger.
    This allows log messages to be prefixed with the module name.
    Example: get_logger(__name__)
    """
    # Using child loggers helps in identifying the source of the log message
    # and allows for per-module log level control if ever needed,
    # while still using the handlers configured on the parent (_app_logger_instance).
    # For simplicity here, we return the main logger but one could use child loggers:
    # return logging.getLogger(f"Iri-shka_App.{module_name}")
    # For now, let all modules use the same logger instance directly.
    # If module-specific log filtering becomes necessary, this can be changed.
    # This ensures the (name) field in the formatter uses "Iri-shka_App".
    # If we want module-specific names, we'd use logging.getLogger(module_name)
    # and ensure it's a child or also configured.
    # Let's stick to one main logger instance for all modules for simplicity.
    # The %(module)s formatter field will show the module name.
    return _app_logger_instance


# --- Initial log message to confirm setup (uses a distinct logger name for this file) ---
# This helps distinguish logger.py's own messages during initialization.
_internal_setup_logger = logging.getLogger("LoggerSetup")
_internal_setup_logger.setLevel(logging.INFO)
if not _internal_setup_logger.handlers: # Avoid adding handlers if they were already added by mistake elsewhere
    # For this initial message, just print to console if file handlers failed
    _ch = logging.StreamHandler(os.sys.stdout)
    _ch.setFormatter(log_formatter)
    _internal_setup_logger.addHandler(_ch)
    if _app_logger_instance.hasHandlers(): # Also log to file if main logger is set up
        for handler in _app_logger_instance.handlers:
             _internal_setup_logger.addHandler(handler)


_internal_setup_logger.info(
    "Logging system initialized. App log: %s, Error log: %s",
    APP_LOG_FILE_PATH, ERROR_LOG_FILE_PATH
)
if not _app_logger_instance.hasHandlers():
    _internal_setup_logger.warning(
        "No file handlers were successfully attached to the main application logger. Logs may only appear on console."
    )