# utils/telegram_handler.py
import asyncio
import threading
import queue
import config 
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import InvalidToken, NetworkError, TelegramError
import logging
import os 
from utils import file_utils 

from logger import get_logger 

logger = get_logger("Iri-shka_App.TelegramBot") 

_current_bot_status = "off" 
_status_lock = threading.Lock()

# --- Pydub Setup ---
PYDUB_AVAILABLE = False
AudioSegment_class = None
pydub_exceptions = None
try:
    from pydub import AudioSegment
    from pydub import exceptions as pd_exceptions 
    from pydub.utils import get_player_name 
    AudioSegment_class = AudioSegment
    pydub_exceptions = pd_exceptions
    PYDUB_AVAILABLE = True
    logger.info("Pydub library imported successfully for Telegram voice messages.")
    try:
        player = get_player_name()
        logger.info(f"Pydub has located an audio backend: {player}")
    except Exception as e_pydub_check: 
        logger.warning(f"Pydub backend check failed: {e_pydub_check}. Ensure FFmpeg or Libav is installed and in PATH.")
except ImportError:
    logger.warning("Pydub library not found. Telegram voice message processing will be disabled.")
    PYDUB_AVAILABLE = False
# --- End Pydub Setup ---


def get_telegram_bot_status():
    global _current_bot_status
    with _status_lock:
        return _current_bot_status

def _set_telegram_bot_status(new_status: str, gui_callbacks=None, status_text_override=None, log_level=logging.INFO):
    global _current_bot_status
    with _status_lock:
        if _current_bot_status == new_status and not status_text_override: 
            return
        _current_bot_status = new_status
    
    log_message = f"Telegram bot status changed to: {new_status}"
    if status_text_override:
        log_message += f" (Detail: {status_text_override})"
    
    if log_level == logging.CRITICAL: logger.critical(log_message)
    elif log_level == logging.ERROR: logger.error(log_message)
    elif log_level == logging.WARNING: logger.warning(log_message)
    elif log_level == logging.INFO: logger.info(log_message)
    elif log_level == logging.DEBUG: logger.debug(log_message)
    else: logger.info(log_message) 

    if gui_callbacks and 'tele_status_update' in gui_callbacks:
        short_text = "TELE: OFF" 
        if new_status == "loading": short_text = "TELE: LOAD"
        elif new_status == "polling": short_text = "TELE: POLL"
        elif new_status == "error": short_text = "TELE: ERR"
        elif new_status == "no_token": short_text = "TELE: NO TOK"
        elif new_status == "no_admin": short_text = "TELE: NO ADM"
        elif new_status == "bad_token": short_text = "TELE: BADTOK"
        elif new_status == "net_error": short_text = "TELE: NETERR"
        
        final_gui_text = status_text_override if status_text_override else short_text
        
        if hasattr(gui_callbacks.get('tele_status_update'), '__call__'):
            try:
                gui_callbacks['tele_status_update'](final_gui_text, new_status)
            except Exception as e_cb:
                logger.error(f"Error in tele_status_update GUI callback: {e_cb}", exc_info=True)


class TelegramBotHandler:
    def __init__(self, token: str, admin_user_id: int, message_queue: queue.Queue, gui_callbacks=None):
        self.token = token
        self.admin_user_id = admin_user_id
        self.message_queue = message_queue 
        self.gui_callbacks = gui_callbacks
        
        self.application: Application = None
        self.polling_thread: threading.Thread = None
        self._stop_polling_event = threading.Event() # Used by the polling thread to know when to stop its internal loop
        self.async_loop = None 
        self._app_lock = threading.Lock() 
        self._is_shutting_down = False # New flag to help manage shutdown

        if not self.token:
            _set_telegram_bot_status("no_token", self.gui_callbacks, log_level=logging.ERROR)
            return
        if not self.admin_user_id:
            _set_telegram_bot_status("no_admin", self.gui_callbacks, log_level=logging.ERROR)
            return
        
        _set_telegram_bot_status("off", self.gui_callbacks)
        
        if PYDUB_AVAILABLE:
            if not file_utils.ensure_folder(config.TELEGRAM_VOICE_TEMP_FOLDER, self.gui_callbacks):
                logger.error(f"Failed to create Telegram voice temp folder: {config.TELEGRAM_VOICE_TEMP_FOLDER}. Voice messages might fail.")

    def _setup_application_handlers(self):
        # (Same as before)
        if not self.application:
            logger.error("Cannot setup handlers: self.application is None.")
            return
        self.application.handlers = {} 
        self.application.add_handler(CommandHandler("start", self._start_command))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._message_handler))
        if PYDUB_AVAILABLE: 
            self.application.add_handler(MessageHandler(filters.VOICE, self._voice_handler))
            logger.info("Telegram voice message handler enabled.")
        else:
            logger.warning("Telegram voice message handler disabled (Pydub not available).")
        logger.debug("Telegram bot handlers configured.")

    async def _set_bot_commands_on_startup(self):
        # (Same as before)
        with self._app_lock: app = self.application
        if app:
            try:
                commands = [BotCommand("start", "Start interacting with Iri-shka")]
                await app.bot.set_my_commands(commands)
                logger.info("Bot commands updated in Telegram.")
            except Exception as e: logger.error(f"Failed to set bot commands: {e}", exc_info=True)

    async def _start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # (Same as before)
        user = update.effective_user
        if user.id != self.admin_user_id: return
        logger.info(f"/start command received from admin user {user.id}.")
        try: await update.message.reply_text(config.TELEGRAM_START_MESSAGE)
        except TelegramError as e: logger.error(f"TelegramError replying to /start: {e}")

    async def _message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # (Same as before)
        user = update.effective_user
        if user.id != self.admin_user_id: return
        text = update.message.text
        logger.info(f"Message from admin user {user.id}: '{text[:70]}...'")
        self.message_queue.put(("telegram_text", user.id, text))

    async def _voice_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # (Same as before, ensure FFmpeg is installed for this to work)
        user = update.effective_user
        if user.id != self.admin_user_id: return
        if not PYDUB_AVAILABLE or not AudioSegment_class or not pydub_exceptions:
            await update.message.reply_text("Sorry, I'm currently unable to process voice messages (converter missing).")
            return
        voice = update.message.voice
        logger.info(f"Voice message received from admin user {user.id}. Duration: {voice.duration}s, MIME: {voice.mime_type}")
        file_id_suffix = f"{update.effective_message.message_id}_{user.id}"
        temp_ogg_filename = f"voice_{file_id_suffix}.oga" 
        temp_wav_filename = f"voice_{file_id_suffix}.wav"
        temp_ogg_path = os.path.join(config.TELEGRAM_VOICE_TEMP_FOLDER, temp_ogg_filename)
        temp_wav_path = os.path.join(config.TELEGRAM_VOICE_TEMP_FOLDER, temp_wav_filename)
        try:
            if self.gui_callbacks and 'status_update' in self.gui_callbacks:
                self.gui_callbacks['status_update']("Received Telegram voice, processing...")
            voice_file = await voice.get_file()
            await voice_file.download_to_drive(temp_ogg_path)
            logger.info(f"Downloaded voice message to {temp_ogg_path}")
            audio = AudioSegment_class.from_file(temp_ogg_path) 
            audio = audio.set_frame_rate(16000).set_channels(1) 
            audio.export(temp_wav_path, format="wav")
            logger.info(f"Converted Telegram voice {temp_ogg_path} to {temp_wav_path}")
            self.message_queue.put(("telegram_voice_wav", user.id, temp_wav_path))
            await update.message.reply_text("Got your voice message, I'll process it.")
        except FileNotFoundError as fnf_error: 
            logger.critical(f"FileNotFoundError during Pydub operation: {fnf_error}. FFmpeg/Libav NOT INSTALLED or NOT IN PATH.", exc_info=True)
            await update.message.reply_text("Sorry, I couldn't process voice. Required audio converter (FFmpeg) missing.")
        except pydub_exceptions.CouldntDecodeError as pde:
            logger.error(f"Pydub CouldntDecodeError for {temp_ogg_path}: {pde}.", exc_info=True)
            await update.message.reply_text("Sorry, I couldn't decode voice. Audio corrupted or FFmpeg missing/misconfigured.")
        except TelegramError as te:
            logger.error(f"Telegram API error during voice processing: {te}", exc_info=True)
            await update.message.reply_text("Sorry, Telegram error during voice handling.")
        except Exception as e:
            logger.error(f"Unexpected error processing Telegram voice message: {e}", exc_info=True)
            await update.message.reply_text("Sorry, unexpected error processing voice.")
        finally:
            if os.path.exists(temp_ogg_path):
                try: os.remove(temp_ogg_path)
                except Exception as e_rem_ogg: logger.warning(f"Could not remove temp OGG {temp_ogg_path}: {e_rem_ogg}")

    def _run_polling_thread_target(self):
        logger.info("Telegram polling thread starting.")
        loop = None
        app_for_thread = None # Local variable for application within this thread
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            with self._app_lock: # Set the instance's async_loop
                self.async_loop = loop 

            if not self.token: raise ValueError("Bot token is missing.")
            
            # Create application within this thread's loop context
            app_for_thread = Application.builder().token(self.token).read_timeout(10).connect_timeout(10).build()
            with self._app_lock: # Store it on the instance
                self.application = app_for_thread
            
            self._setup_application_handlers() # Handlers for app_for_thread (which is self.application)
            loop.run_until_complete(self._set_bot_commands_on_startup()) # Uses self.application
            
            _set_telegram_bot_status("polling", self.gui_callbacks)
            logger.info("Starting Telegram bot polling (Application.run_polling)...")
            
            # This is the blocking call. It will run until application.stop() is called
            # or an unhandled error occurs in PTB's internal workings.
            app_for_thread.run_polling(
                stop_signals=None, 
                poll_interval=1.0, 
                timeout=config.TELEGRAM_POLLING_TIMEOUT,
                drop_pending_updates=True,
                # Pass our external stop event to PTB if PTB supports it in a future version,
                # or we rely on calling application.stop()
                # For now, we manage stop by calling application.stop() from stop_polling()
            )
            logger.info("Application.run_polling has finished (likely due to stop signal or error).")

        except InvalidToken:
            logger.critical("Invalid Telegram Bot Token.", exc_info=True)
            _set_telegram_bot_status("bad_token", self.gui_callbacks, log_level=logging.CRITICAL)
        except NetworkError as ne:
            logger.error(f"Telegram NetworkError: {ne}.", exc_info=True)
            _set_telegram_bot_status("net_error", self.gui_callbacks, log_level=logging.ERROR)
        except asyncio.CancelledError:
            logger.info("Asyncio loop was cancelled. Polling thread shutting down.")
        except Exception as e:
            if not self._is_shutting_down: # Only log as error if not part of intentional shutdown
                logger.error(f"Unexpected error in Telegram polling thread: {e}", exc_info=True)
                _set_telegram_bot_status("error", self.gui_callbacks, log_level=logging.ERROR)
            else:
                logger.info(f"Error during shutdown in polling thread (expected if loop forced stop): {e}")
        finally:
            logger.info("Telegram polling thread entering finally block for cleanup.")
            
            # Gracefully shutdown the application if it's still marked as running by PTB
            # This is usually handled by PTB when run_polling exits, but an extra check.
            if app_for_thread and app_for_thread.running:
                logger.info("Application still marked as running in finally, scheduling shutdown.")
                if loop and loop.is_running():
                    try:
                        loop.run_until_complete(app_for_thread.shutdown())
                        logger.info("Application shutdown() called and completed in finally.")
                    except Exception as e_shutdown:
                        logger.warning(f"Error during explicit application.shutdown() in finally: {e_shutdown}")
                else:
                    logger.warning("Loop not running in finally, cannot call app.shutdown().")
            
            with self._app_lock:
                self.application = None # Clear instance's application
                current_loop_ref = self.async_loop
                self.async_loop = None # Clear instance's loop reference

            if current_loop_ref: 
                if current_loop_ref.is_running():
                    logger.info("Asyncio loop is still running in finally, scheduling stop.")
                    # Schedule loop.stop() to allow pending tasks to complete.
                    current_loop_ref.call_soon_threadsafe(current_loop_ref.stop)
                    # Give it a moment to process the stop, then try to close.
                    # loop.run_forever() might have exited, but loop.stop() makes sure.
                    # We don't call loop.close() here; let the thread end and Python's GC handle it,
                    # or ensure all tasks are truly done before closing if we had more control.
                    # For PTB, it's generally safer not to close its loop aggressively.
                logger.info(f"Asyncio loop from thread ({id(current_loop_ref)}) cleanup initiated.")
            
            # Update status unless it's a persistent config error
            current_status_final = get_telegram_bot_status()
            if current_status_final not in ["off", "no_token", "no_admin", "bad_token"]:
                 _set_telegram_bot_status("off", self.gui_callbacks)
            
            self._stop_polling_event.set() # Signal that this thread's work is done
            logger.info("Telegram polling thread finished execution.")


    def start_polling(self):
        # (Largely same, ensure _is_shutting_down is reset)
        logger.info("Request to start Telegram polling.")
        self._is_shutting_down = False # Reset shutdown flag
        current_status = get_telegram_bot_status()
        if current_status in ["polling", "loading"]:
            logger.info(f"Telegram bot polling is already {current_status}. Ignoring start request.")
            return False 
        if not self.token:
            _set_telegram_bot_status("no_token", self.gui_callbacks, log_level=logging.ERROR); return False
        if not self.admin_user_id:
            _set_telegram_bot_status("no_admin", self.gui_callbacks, log_level=logging.ERROR); return False

        self._stop_polling_event.clear() 
        _set_telegram_bot_status("loading", self.gui_callbacks)
        
        if self.polling_thread and not self.polling_thread.is_alive():
            self.polling_thread = None 

        if self.polling_thread is None:
            self.polling_thread = threading.Thread(target=self._run_polling_thread_target, daemon=True, name="TelegramPollingThread")
            self.polling_thread.start()
            logger.info("New Telegram polling thread started.")
            return True
        else: 
            logger.warning("Polling thread already exists. Start aborted."); return False


    def stop_polling(self):
        logger.info("Request to stop Telegram polling.")
        if self._is_shutting_down and get_telegram_bot_status() == "off": # Already processed stop
            logger.info("Stop polling already in progress or completed.")
            return
            
        self._is_shutting_down = True # Indicate shutdown is in progress
        self._stop_polling_event.set() # Signal to the polling thread's logic (if it checks)

        loop_to_signal_stop = None
        app_to_signal_stop = None
        with self._app_lock: # Get current references safely
            loop_to_signal_stop = self.async_loop
            app_to_signal_stop = self.application

        if app_to_signal_stop and loop_to_signal_stop and loop_to_signal_stop.is_running():
            logger.info(f"Scheduling PTB application.stop() on its loop ({id(loop_to_signal_stop)}).")
            # Schedule stop() to be called in the bot's own event loop
            future = asyncio.run_coroutine_threadsafe(app_to_signal_stop.stop(), loop_to_signal_stop)
            try:
                future.result(timeout=5) # Reduced timeout, PTB stop should be quick
                logger.info("PTB application.stop() confirmed complete via future.")
            except RuntimeError as re: # Can happen if loop is stopping/stopped
                if "cannot schedule new futures after shutdown" in str(re).lower() or \
                   "Event loop is closed" in str(re).lower():
                    logger.warning(f"Could not schedule app.stop(): loop already stopping/closed. {re}")
                else:
                    logger.error(f"RuntimeError during app.stop() future: {re}", exc_info=True)
            except asyncio.TimeoutError:
                logger.warning("Timeout waiting for PTB application.stop() future. Application might already be stopping.")
            except Exception as e: 
                logger.error(f"Error during PTB application.stop() future processing: {e}", exc_info=True)
        elif app_to_signal_stop: # App exists but loop might not be running
             logger.warning(f"PTB application exists, but its loop is not running or not set. Cannot reliably call app.stop(). Application running: {app_to_signal_stop.running}")
             # If app thinks it's running but loop isn't, that's an inconsistent state.
             # If not running, the RuntimeError you saw might occur.
             if app_to_signal_stop.running: # Try to call stop directly if it thinks it's running but loop isn't
                 try:
                    # This is a fallback, might raise "This Application is not running!" if PTB state is tricky
                    # loop_to_signal_stop.run_until_complete(app_to_signal_stop.stop()) # NO, this would block or fail
                    logger.warning("Attempting direct stop, but this is less ideal.")
                    # The run_polling in the thread should exit when app.stop() is effective.
                 except Exception as e_direct_stop:
                     logger.error(f"Error on direct stop attempt: {e_direct_stop}")
        else:
            logger.info("No active application or loop to signal stop to.")

        # Join the polling thread
        if self.polling_thread and self.polling_thread.is_alive():
            logger.info(f"Waiting for Telegram polling thread ({self.polling_thread.name}) to join...")
            self.polling_thread.join(timeout=7) # Slightly shorter join timeout
            if self.polling_thread.is_alive():
                logger.warning(f"Telegram polling thread ({self.polling_thread.name}) did not join cleanly after timeout.")
            else:
                logger.info(f"Telegram polling thread ({self.polling_thread.name}) joined successfully.")
        self.polling_thread = None 

        # Final status update after attempting to stop and join
        current_status = get_telegram_bot_status()
        if current_status not in ["no_token", "no_admin", "bad_token", "net_error"]: 
            _set_telegram_bot_status("off", self.gui_callbacks)
        
        logger.info("Stop polling sequence finished.")
        # self._is_shutting_down = False # Reset after full stop, or manage in start_polling

    async def send_message_to_admin(self, text: str):
        # (Same as before)
        with self._app_lock: app = self.application 
        if not app or not self.admin_user_id: return
        try:
            await app.bot.send_message(chat_id=self.admin_user_id, text=text)
            logger.info(f"Message sent to admin user {self.admin_user_id}: '{text[:70]}...'")
        except TelegramError as e: logger.error(f"TelegramError sending: {e}", exc_info=True)
        except Exception as e: logger.error(f"Failed to send: {e}", exc_info=True)

    def get_status(self):
        return get_telegram_bot_status()

    def full_shutdown(self):
        logger.info("Performing full shutdown of TelegramBotHandler.")
        self.stop_polling() 
        logger.info("TelegramBotHandler full shutdown process complete.")

# Standalone test (__main__ block) can remain largely the same.
# Ensure FFmpeg is installed if testing voice messages.
if __name__ == '__main__':
    # (Same standalone test setup as before)
    test_standalone_logger = logging.getLogger() 
    test_standalone_logger.setLevel(logging.DEBUG) 
    ch_test = logging.StreamHandler()
    ch_test.setLevel(logging.DEBUG) 
    formatter_test = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s')
    ch_test.setFormatter(formatter_test)
    if test_standalone_logger.hasHandlers(): test_standalone_logger.handlers.clear()
    test_standalone_logger.addHandler(ch_test)
    logger.info("Running TelegramBotHandler standalone test...") 
    from dotenv import load_dotenv
    project_root_for_test = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    dotenv_path = os.path.join(project_root_for_test, '.env')
    if os.path.exists(dotenv_path): load_dotenv(dotenv_path); logger.info(f".env loaded from {dotenv_path}")
    else: logger.warning(f".env file not found at {dotenv_path}.")
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_ADMIN_USER_ID:
        logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_ADMIN_USER_ID not found. Test cannot run."); exit(1)
    try: admin_id_test = int(config.TELEGRAM_ADMIN_USER_ID)
    except ValueError: logger.error(f"Invalid TELEGRAM_ADMIN_USER_ID: {config.TELEGRAM_ADMIN_USER_ID}."); exit(1)
    test_message_queue = queue.Queue()
    mock_callbacks_for_test = {'tele_status_update': lambda st, typ: logger.info(f"Mock GUI: tele_status='{st}', type='{typ}'")}
    bot_handler_test_instance = TelegramBotHandler(token=config.TELEGRAM_BOT_TOKEN, admin_user_id=admin_id_test, message_queue=test_message_queue, gui_callbacks=mock_callbacks_for_test)
    initial_bot_status_for_test = bot_handler_test_instance.get_status()
    if initial_bot_status_for_test in ["no_token", "no_admin"]: logger.error(f"Init failed ({initial_bot_status_for_test}). Exiting."); exit(1)
    if not bot_handler_test_instance.start_polling(): logger.error("Failed to start polling. Exiting."); exit(1)
    logger.info("Bot polling started. Send /start, text, or voice. 'stop test' in Telegram or Ctrl+C to end.")
    try:
        while True:
            if bot_handler_test_instance.get_status() not in ["polling", "loading"]: logger.info(f"Bot status {bot_handler_test_instance.get_status()}, ending test."); break
            try:
                queued_item = test_message_queue.get(timeout=0.5) 
                msg_type, user_id, data_content = queued_item
                if msg_type == "telegram_text":
                    logger.info(f"Test Q: TEXT User {user_id} said '{data_content}'")
                    reply_text_test = f"Test got text: '{data_content}'"
                    if data_content.lower() == "stop test":
                        logger.info("'stop test' received. Signaling stop.")
                        asyncio.run_coroutine_threadsafe(bot_handler_test_instance.send_message_to_admin("Test stopping."), bot_handler_test_instance.async_loop).result(5)
                        break
                elif msg_type == "telegram_voice_wav":
                    logger.info(f"Test Q: VOICE_WAV User {user_id}, Path: '{data_content}'")
                    reply_text_test = f"Test got voice: {os.path.basename(data_content)}"
                    if os.path.exists(data_content): 
                        try: os.remove(data_content)
                        except Exception as e_rem: logger.warning(f"Test: Could not remove {data_content}: {e_rem}")
                else: logger.warning(f"Test Q: UNKNOWN item: {queued_item}"); continue
                if bot_handler_test_instance.async_loop and bot_handler_test_instance.application:
                     asyncio.run_coroutine_threadsafe(bot_handler_test_instance.send_message_to_admin(reply_text_test), bot_handler_test_instance.async_loop).result(timeout=5)
            except queue.Empty: pass 
            except Exception as e: logger.error(f"Error in test loop: {e}", exc_info=True); break
    except KeyboardInterrupt: logger.info("KeyboardInterrupt in test. Stopping...")
    finally:
        logger.info("Initiating test shutdown...")
        bot_handler_test_instance.full_shutdown()
        logger.info("TelegramBotHandler standalone test finished.")