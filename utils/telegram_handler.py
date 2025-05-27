# utils/telegram_handler.py
import asyncio
import threading
import queue
import config 
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import InvalidToken, NetworkError, TelegramError
import logging

from logger import get_logger 


logger = get_logger("Iri-shka_App.TelegramBot") 

_current_bot_status = "off" 
_status_lock = threading.Lock() # Lock for thread-safe status updates

def get_telegram_bot_status():
    global _current_bot_status
    with _status_lock:
        return _current_bot_status

def _set_telegram_bot_status(new_status: str, gui_callbacks=None, status_text_override=None, log_level=logging.INFO):
    global _current_bot_status
    with _status_lock:
        if _current_bot_status == new_status and not status_text_override: # Avoid redundant logging if status is the same
            return
        _current_bot_status = new_status
    
    log_message = f"Telegram bot status changed to: {new_status}"
    if status_text_override:
        log_message += f" (Detail: {status_text_override})"
    logger.log(log_level, log_message)


    if gui_callbacks and 'tele_status_update' in gui_callbacks:
        short_text = "TELE: OFF"
        if new_status == "loading": short_text = "TELE: LOAD"
        elif new_status == "polling": short_text = "TELE: POLL"
        elif new_status == "error": short_text = "TELE: ERR"
        elif new_status == "no_token": short_text = "TELE: NO TOK"
        elif new_status == "no_admin": short_text = "TELE: NO ADM"
        elif new_status == "bad_token": short_text = "TELE: BADTOK" # More specific
        elif new_status == "net_error": short_text = "TELE: NETERR" # More specific
        
        # Use status_text_override if provided for the GUI short_text as well
        final_gui_text = status_text_override if status_text_override else short_text
        
        if hasattr(gui_callbacks.get('tele_status_update'), '__call__'):
            gui_callbacks['tele_status_update'](final_gui_text, new_status)


class TelegramBotHandler:
    def __init__(self, token: str, admin_user_id: int, message_queue: queue.Queue, gui_callbacks=None):
        self.token = token
        self.admin_user_id = admin_user_id
        self.message_queue = message_queue 
        self.gui_callbacks = gui_callbacks
        
        self.application: Application = None
        self.polling_thread: threading.Thread = None
        self._stop_polling_event = threading.Event() # Internal event for signaling thread to stop
        self.async_loop = None 
        self._app_lock = threading.Lock() # Lock for accessing self.application and self.async_loop

        if not self.token:
            _set_telegram_bot_status("no_token", self.gui_callbacks, log_level=logging.ERROR)
            return
        if not self.admin_user_id:
            _set_telegram_bot_status("no_admin", self.gui_callbacks, log_level=logging.ERROR)
            return
        
        _set_telegram_bot_status("off", self.gui_callbacks)

    def _setup_application_handlers(self):
        """Sets up command and message handlers for the application object.
           Assumes self.application is already created.
        """
        if not self.application:
            logger.error("Cannot setup handlers: self.application is None.")
            return
        
        # Clear existing handlers if any (important for re-start)
        self.application.handlers = {} # Or use application.remove_handler if more granular control is needed

        self.application.add_handler(CommandHandler("start", self._start_command))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._message_handler))
        logger.debug("Telegram bot handlers configured.")

    async def _set_bot_commands_on_startup(self):
        """Sets the bot commands. Called once the bot's asyncio loop is running."""
        with self._app_lock:
            app = self.application
        
        if app:
            try:
                commands = [BotCommand("start", "Start interacting with Iri-shka")]
                await app.bot.set_my_commands(commands)
                logger.info("Bot commands updated in Telegram.")
            except Exception as e:
                logger.error(f"Failed to set bot commands: {e}", exc_info=True)

    async def _start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user.id != self.admin_user_id:
            logger.warning(f"Unauthorized /start command from user {user.id} ({user.username}). Ignoring.")
            return
        
        logger.info(f"/start command received from admin user {user.id}.")
        try:
            await update.message.reply_text(config.TELEGRAM_START_MESSAGE)
        except TelegramError as e:
            logger.error(f"TelegramError replying to /start: {e}")


    async def _message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user.id != self.admin_user_id:
            logger.warning(f"Unauthorized message from user {user.id} ({user.username}): '{update.message.text}'. Ignoring.")
            return

        text = update.message.text
        logger.info(f"Message from admin user {user.id}: '{text[:70]}...'")
        self.message_queue.put((user.id, text))

    def _run_polling_thread_target(self):
        """Target function for the polling thread."""
        logger.info("Telegram polling thread starting.")
        loop = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            with self._app_lock:
                self.async_loop = loop # Make loop instance available to other methods

            # Create and configure application object within the new thread's event loop context
            with self._app_lock:
                if not self.token: # Should be caught by constructor, but double check
                    raise ValueError("Bot token is missing.")
                self.application = Application.builder().token(self.token).read_timeout(10).connect_timeout(10).build()
                self._setup_application_handlers() # Add handlers to the new application object
            
            # Set bot commands after loop is running and application is initialized
            loop.run_until_complete(self._set_bot_commands_on_startup())
            
            _set_telegram_bot_status("polling", self.gui_callbacks)
            logger.info("Starting Telegram bot polling (Application.run_polling)...")
            
            # PTB's run_polling will block here until an unhandled error or stop
            self.application.run_polling(
                stop_signals=None, # We manage stop externally
                poll_interval=1.0, 
                timeout=config.TELEGRAM_POLLING_TIMEOUT,
                drop_pending_updates=True
            )
            # If run_polling exits cleanly (e.g., due to application.stop() from another thread)
            logger.info("Application.run_polling has finished.")

        except InvalidToken:
            logger.critical("Invalid Telegram Bot Token. Bot cannot start.", exc_info=True)
            _set_telegram_bot_status("bad_token", self.gui_callbacks, log_level=logging.CRITICAL)
            if self.gui_callbacks and 'messagebox_error' in self.gui_callbacks:
                self.gui_callbacks['messagebox_error']("Telegram Error", "Invalid Bot Token. Please check .env.")
        except NetworkError as ne:
            logger.error(f"Telegram NetworkError during polling: {ne}. Bot stopped.", exc_info=True)
            _set_telegram_bot_status("net_error", self.gui_callbacks, log_level=logging.ERROR)
        except asyncio.CancelledError:
            logger.info("Asyncio loop was cancelled. Telegram polling thread shutting down.")
        except Exception as e:
            logger.error(f"Unexpected error in Telegram polling thread: {e}", exc_info=True)
            _set_telegram_bot_status("error", self.gui_callbacks, log_level=logging.ERROR)
        finally:
            logger.info("Telegram polling thread entering finally block.")
            with self._app_lock:
                # Cleanup application and loop references
                if self.application and self.application.running:
                    logger.info("Application still marked as running in finally, attempting to stop it.")
                    # This needs to be called from within the loop, or scheduled.
                    # If loop is already stopped/stopping, this might not work as expected.
                    # loop.run_until_complete(self.application.shutdown()) # More graceful
                    pass # Rely on application.stop() from stop_polling
                
                self.application = None # Clear application object
                current_loop = self.async_loop
                self.async_loop = None # Clear loop reference

            if current_loop: # current_loop is the loop from this thread
                if current_loop.is_running():
                    logger.info("Asyncio loop is still running in finally, requesting stop.")
                    current_loop.call_soon_threadsafe(current_loop.stop)
                # Closing the loop should be done after it has fully stopped.
                # This can be tricky if tasks are still pending.
                # loop.close() often causes issues if not all tasks are done.
                # Let's assume PTB's shutdown handles loop resource cleanup.
                logger.info(f"Asyncio loop ({id(current_loop)}) from thread will be managed by asyncio cleanup.")


            # Ensure status reflects that polling is no longer active
            # unless it's a known configuration error status.
            current_status = get_telegram_bot_status()
            if current_status not in ["off", "no_token", "no_admin", "bad_token"]:
                 _set_telegram_bot_status("off", self.gui_callbacks)
            
            self._stop_polling_event.set() # Ensure event is set, indicating thread completion
            logger.info("Telegram polling thread finished execution.")


    def start_polling(self):
        logger.info("Request to start Telegram polling.")
        if get_telegram_bot_status() in ["polling", "loading"]:
            logger.info(f"Telegram bot polling is already {get_telegram_bot_status()}. Ignoring start request.")
            return False # Indicate not started or already running

        if not self.token:
            logger.error("Cannot start polling: Token missing.")
            _set_telegram_bot_status("no_token", self.gui_callbacks, log_level=logging.ERROR)
            return False
        if not self.admin_user_id:
            logger.error("Cannot start polling: Admin User ID missing.")
            _set_telegram_bot_status("no_admin", self.gui_callbacks, log_level=logging.ERROR)
            return False

        self._stop_polling_event.clear() # Clear stop signal for the new thread
        _set_telegram_bot_status("loading", self.gui_callbacks)
        
        # Clean up any previous thread instance if it exists and is dead
        if self.polling_thread and not self.polling_thread.is_alive():
            logger.debug("Previous polling thread found dead, allowing new thread creation.")
            self.polling_thread = None # Clear old dead thread

        if self.polling_thread is None:
            self.polling_thread = threading.Thread(target=self._run_polling_thread_target, daemon=True, name="TelegramPollingThread")
            self.polling_thread.start()
            logger.info("New Telegram polling thread started.")
            return True
        else: # Should not happen if previous check was done, but as a safeguard
            logger.warning("Polling thread already exists and might be alive. Start polling aborted.")
            _set_telegram_bot_status("error", self.gui_callbacks, status_text_override="TELE: THRD ERR", log_level=logging.WARNING)
            return False


    def stop_polling(self):
        logger.info("Request to stop Telegram polling.")
        self._stop_polling_event.set() # Signal the thread's loop to stop if it checks this

        loop_to_stop = None
        app_to_stop = None
        with self._app_lock:
            loop_to_stop = self.async_loop
            app_to_stop = self.application

        if app_to_stop and loop_to_stop and loop_to_stop.is_running():
            logger.info(f"Attempting to stop PTB application and its asyncio loop ({id(loop_to_stop)}).")
            # Schedule application.stop() to run in the bot's own event loop.
            # This is crucial for PTB v20+ to gracefully shut down polling.
            future = asyncio.run_coroutine_threadsafe(app_to_stop.stop(), loop_to_stop)
            try:
                future.result(timeout=7) # Wait for stop() to complete, increased timeout
                logger.info("PTB application.stop() completed successfully.")
            except asyncio.TimeoutError:
                logger.warning("Timeout waiting for PTB application.stop() to complete. Loop might need forceful stop.")
                # If stop times out, try to stop the loop directly (use with caution)
                if loop_to_stop.is_running():
                    loop_to_stop.call_soon_threadsafe(loop_to_stop.stop)
            except Exception as e: # Catch other errors from future.result()
                logger.error(f"Error during PTB application.stop(): {e}", exc_info=True)
        else:
            logger.warning(f"Cannot call application.stop(): application ({'set' if app_to_stop else 'unset'}) or loop ({('running' if loop_to_stop and loop_to_stop.is_running() else 'not running/set') if loop_to_stop else 'unset'}) not in a stoppable state.")

        # Join the polling thread
        if self.polling_thread and self.polling_thread.is_alive():
            logger.info(f"Waiting for Telegram polling thread ({self.polling_thread.name}) to join...")
            self.polling_thread.join(timeout=10) # Increased timeout for thread join
            if self.polling_thread.is_alive():
                logger.warning(f"Telegram polling thread ({self.polling_thread.name}) did not join cleanly after timeout.")
            else:
                logger.info(f"Telegram polling thread ({self.polling_thread.name}) joined.")
        self.polling_thread = None # Clear thread reference after it's joined or timed out

        # Final status update
        current_status = get_telegram_bot_status()
        if current_status not in ["no_token", "no_admin", "bad_token"]: # Don't override config errors with "off"
            _set_telegram_bot_status("off", self.gui_callbacks)
        
        # Application and loop references are cleared inside _run_polling_thread_target's finally
        logger.info("Stop polling sequence finished.")


    async def send_message_to_admin(self, text: str):
        with self._app_lock:
            app = self.application # Get current application instance under lock
        
        if not app or not self.admin_user_id:
            logger.error("Cannot send Telegram message: Application not available or Admin ID missing.")
            return
        try:
            await app.bot.send_message(chat_id=self.admin_user_id, text=text)
            logger.info(f"Message sent to admin user {self.admin_user_id}: '{text[:70]}...'")
        except TelegramError as e: # Catch more specific PTB errors
            logger.error(f"TelegramError sending message to admin {self.admin_user_id}: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Failed to send message to admin {self.admin_user_id}: {e}", exc_info=True)

    def get_status(self):
        return get_telegram_bot_status()

    def full_shutdown(self):
        logger.info("Performing full shutdown of TelegramBotHandler.")
        self.stop_polling() # Ensures thread is stopped and joined, and attempts to stop app/loop
        
        # Loop cleanup is now more reliant on the thread's finally block and PTB's shutdown
        # We've cleared self.async_loop in the thread's finally block.
        logger.info("TelegramBotHandler full shutdown process complete.")


if __name__ == '__main__':
    import logging 
    
    test_logger = logging.getLogger("Iri-shka_App.TelegramBot.Test")
    if not test_logger.handlers:
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG) 
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        ch.setFormatter(formatter)
        test_logger.addHandler(ch)
        test_logger.setLevel(logging.INFO) 
        # Also configure the module's main logger if it has no handlers, for internal messages
        module_logger = logging.getLogger("Iri-shka_App.TelegramBot")
        if not module_logger.handlers:
            module_logger.addHandler(ch) # Share handler
            module_logger.setLevel(logging.INFO)

    test_logger.info("Running TelegramBotHandler standalone test...")

    from dotenv import load_dotenv
    import os
    dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path)
        test_logger.info(f".env loaded from {dotenv_path} for standalone test.")
    else:
        test_logger.warning(f".env file not found at {dotenv_path}.")


    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_ADMIN_USER_ID:
        test_logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_ADMIN_USER_ID not found. Test cannot run.")
        exit(1)
    
    try:
        admin_id_test = int(config.TELEGRAM_ADMIN_USER_ID)
    except ValueError:
        test_logger.error(f"Invalid TELEGRAM_ADMIN_USER_ID: {config.TELEGRAM_ADMIN_USER_ID}. Must be an integer.")
        exit(1)

    test_message_queue = queue.Queue()
    mock_callbacks = {'tele_status_update': lambda st, typ: test_logger.info(f"Mock GUI: tele_status='{st}', type='{typ}'")}

    bot_handler = TelegramBotHandler(
        token=config.TELEGRAM_BOT_TOKEN,
        admin_user_id=admin_id_test,
        message_queue=test_message_queue,
        gui_callbacks=mock_callbacks
    )

    if bot_handler.get_status() in ["no_token", "no_admin", "bad_token"]:
        test_logger.error(f"TelegramBotHandler initialization failed ({bot_handler.get_status()}). Exiting test.")
        exit(1)

    if not bot_handler.start_polling():
        test_logger.error("Failed to start polling in test. Exiting.")
        exit(1)

    test_logger.info("Bot polling started. Send /start or a message from the admin account.")
    test_logger.info("Type 'stop test' in Telegram to end, or 'quit' in console. Ctrl+C also works.")

    try:
        while True:
            if bot_handler.get_status() not in ["polling", "loading"]:
                test_logger.info(f"Bot status is {bot_handler.get_status()}, not polling. Test loop ending.")
                break
            try:
                user_id, text = test_message_queue.get(timeout=0.5) # Shorter timeout
                test_logger.info(f"Test received from queue: User {user_id} said '{text}'")
                
                reply_text = f"Iri-shka (test) received: '{text}'"
                if text.lower() == "stop test":
                    test_logger.info("'stop test' command received. Signaling bot stop.")
                    asyncio.run_coroutine_threadsafe(bot_handler.send_message_to_admin("Test stopping now."), bot_handler.async_loop).result(5)
                    break 
                
                if bot_handler.async_loop and bot_handler.application:
                     asyncio.run_coroutine_threadsafe(bot_handler.send_message_to_admin(reply_text), bot_handler.async_loop).result(timeout=5)
                else: test_logger.warning("Cannot send reply: async_loop or application not available.")
            except queue.Empty:
                pass # Normal, continue loop
            except Exception as e:
                test_logger.error(f"Error in test message processing loop: {e}", exc_info=True)
                break
            
            # Check for console input to quit test (for environments where Ctrl+C might be tricky)
            # This part is a bit hacky for a quick test, not for production.
            # import sys, select
            # if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
            #     line = sys.stdin.readline().strip()
            #     if line == 'quit':
            #         test_logger.info("Console 'quit' received. Stopping test.")
            #         break

    except KeyboardInterrupt:
        test_logger.info("KeyboardInterrupt received in test. Stopping bot...")
    finally:
        test_logger.info("Initiating bot stop and full shutdown from test block...")
        bot_handler.stop_polling()
        bot_handler.full_shutdown()
        test_logger.info("TelegramBotHandler standalone test finished.")