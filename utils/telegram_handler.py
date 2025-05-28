# utils/telegram_handler.py
import asyncio
import threading
import queue
import config
from telegram import Update, BotCommand, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import InvalidToken, NetworkError, TelegramError
import logging
import os
import datetime # For dashboard filename & calendar summary

# Relative imports from 'utils' package
from .file_utils import ensure_folder
from .state_manager import (
    load_or_initialize_customer_state,
    save_customer_state,
    get_current_timestamp_iso
)
from .customer_interaction_manager import CustomerInteractionManager
from .html_dashboard_generator import generate_dashboard_html

from logger import get_logger # Assuming logger.py is in project root

logger = get_logger("Iri-shka_App.TelegramBot")

_current_bot_status = "off"
_status_lock = threading.Lock()

PYDUB_AVAILABLE = False
AudioSegment_class = None
pydub_exceptions = None
if config.TELEGRAM_REPLY_WITH_VOICE: # Or any other Pydub-dependent feature
    try:
        from pydub import AudioSegment
        from pydub import exceptions as pd_exceptions
        from pydub.utils import get_player_name # Optional: for checking backend
        AudioSegment_class = AudioSegment
        pydub_exceptions = pd_exceptions
        PYDUB_AVAILABLE = True
        logger.info("Pydub library imported successfully for Telegram handler.")
        try: # Optional check
            player = get_player_name()
            logger.info(f"Pydub has located an audio backend: {player}")
        except Exception as e_pydub_check:
            logger.warning(f"Pydub backend check failed: {e_pydub_check}. FFmpeg/Libav might be needed for some operations.")
    except ImportError:
        logger.warning("Pydub library not found. Voice message features in Telegram handler might be limited.")
        PYDUB_AVAILABLE = False
else:
    logger.info("Pydub-dependent features (e.g., admin voice replies) disabled in config or Pydub not imported for Telegram handler.")


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
    if status_text_override: log_message += f" (Detail: {status_text_override})"

    if log_level >= logging.CRITICAL: logger.critical(log_message)
    elif log_level >= logging.ERROR: logger.error(log_message)
    elif log_level >= logging.WARNING: logger.warning(log_message)
    elif log_level >= logging.INFO: logger.info(log_message)
    else: logger.debug(log_message)

    if gui_callbacks and callable(gui_callbacks.get('tele_status_update')):
        short_text_map = {
            "loading": "TELE: LOAD", "polling": "TELE: POLL", "error": "TELE: ERR",
            "no_token": "TELE: NO TOK", "no_admin": "TELE: NO ADM",
            "bad_token": "TELE: BADTOK", "net_error": "TELE: NETERR", "off": "TELE: OFF"
        }
        short_text = short_text_map.get(new_status, "TELE: UNKN")
        final_gui_text = status_text_override if status_text_override else short_text
        try:
            gui_callbacks['tele_status_update'](final_gui_text, new_status)
        except Exception as e_cb:
            logger.error(f"Error in tele_status_update GUI callback: {e_cb}", exc_info=True)


class TelegramBotHandler:
    def __init__(self, token: str, admin_user_id_str: str,
                 message_queue_for_admin_llm: queue.Queue,
                 customer_interaction_manager: CustomerInteractionManager,
                 gui_callbacks=None,
                 fn_get_dashboard_data=None):
        self.token = token
        self.admin_user_id_str = admin_user_id_str
        try:
            self.admin_user_id_int = int(admin_user_id_str) if admin_user_id_str else 0
        except ValueError:
            logger.critical(f"Invalid TELEGRAM_ADMIN_USER_ID: {admin_user_id_str}. Bot cannot function correctly for admin.")
            self.admin_user_id_int = 0

        self.message_queue_for_admin_llm = message_queue_for_admin_llm
        self.customer_interaction_manager = customer_interaction_manager
        self.gui_callbacks = gui_callbacks
        self.fn_get_dashboard_data = fn_get_dashboard_data

        self.application: Application = None # type: ignore
        self.polling_thread: threading.Thread = None # type: ignore
        self._stop_polling_event = threading.Event()
        self.async_loop = None
        self._app_lock = threading.Lock()
        self._is_shutting_down = False

        if not self.token:
            _set_telegram_bot_status("no_token", self.gui_callbacks, log_level=logging.ERROR); return
        if not self.admin_user_id_int: # Should not happen if ValueError caught above, but defensive.
            _set_telegram_bot_status("no_admin", self.gui_callbacks, log_level=logging.ERROR); return

        _set_telegram_bot_status("off", self.gui_callbacks)
        ensure_folder(config.CUSTOMER_STATES_FOLDER, self.gui_callbacks)
        ensure_folder(config.TELEGRAM_VOICE_TEMP_FOLDER, self.gui_callbacks)
        ensure_folder(config.TELEGRAM_TTS_TEMP_FOLDER, self.gui_callbacks)
        ensure_folder(os.path.join(config.DATA_FOLDER, "temp_dashboards"), self.gui_callbacks)


    def _setup_application_handlers(self):
        if not self.application: logger.error("Cannot setup handlers: self.application is None."); return
        self.application.handlers = {} # Clear existing before adding, if any
        self.application.add_handler(CommandHandler("start", self._start_command))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._text_message_handler))
        if PYDUB_AVAILABLE and self.admin_user_id_int: # Check admin ID too for admin-specific features
            self.application.add_handler(MessageHandler(filters.VOICE & filters.User(user_id=self.admin_user_id_int), self._admin_voice_handler))
            logger.info("Admin voice message handler enabled.")
        else:
            logger.info("Admin voice message handler disabled (Pydub not available or Admin ID invalid). Non-admin voice ignored.")
        logger.debug("Telegram bot handlers configured.")

    async def _set_bot_commands_on_startup(self):
        with self._app_lock: app = self.application
        if app:
            try:
                commands = [BotCommand("start", "Start Iri-shka & Get Status Dashboard")]
                await app.bot.set_my_commands(commands)
                logger.info("Bot commands updated in Telegram.")
            except Exception as e: logger.error(f"Failed to set bot commands: {e}", exc_info=True)

    async def _send_dashboard_to_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.fn_get_dashboard_data or not callable(self.fn_get_dashboard_data):
            logger.error("Dashboard data gathering function not provided to TelegramBotHandler.")
            await update.message.reply_text("Sorry, unable to generate the status dashboard at this time (internal config error).")
            return
        logger.info(f"Admin {update.effective_user.id} requested dashboard. Generating...")
        try: await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing") # type: ignore
        except Exception: pass
        temp_reply_generating = await update.message.reply_text("Generating status dashboard, please wait a moment...")
        try:
            dashboard_data = self.fn_get_dashboard_data()
            if not dashboard_data:
                logger.error("Failed to gather data for the dashboard.")
                await temp_reply_generating.edit_text("Sorry, an error occurred while gathering data for the dashboard.")
                return
            html_content = generate_dashboard_html(
                admin_user_state=dashboard_data.get("admin_user_state", {}),
                assistant_state_snapshot=dashboard_data.get("assistant_state", {}),
                admin_chat_history=dashboard_data.get("admin_chat_history", []),
                component_statuses=dashboard_data.get("component_statuses", {}),
                app_overall_status=dashboard_data.get("app_overall_status", "N/A")
            )
            dashboard_filename = f"irishka_dashboard_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            temp_dashboard_folder = os.path.join(config.DATA_FOLDER, "temp_dashboards")
            ensure_folder(temp_dashboard_folder, self.gui_callbacks)
            dashboard_filepath = os.path.join(temp_dashboard_folder, dashboard_filename)
            with open(dashboard_filepath, "w", encoding="utf-8") as f: f.write(html_content)
            logger.info(f"HTML dashboard generated: {dashboard_filepath}")
            with open(dashboard_filepath, "rb") as doc_to_send:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id, # type: ignore
                    document=doc_to_send, filename=dashboard_filename,
                    caption=f"Iri-shka Status Dashboard ({datetime.datetime.now().strftime('%H:%M:%S')})"
                )
            logger.info(f"Dashboard sent to admin {update.effective_user.id}.")
            await temp_reply_generating.delete()
        except Exception as e:
            logger.error(f"Error generating or sending HTML dashboard: {e}", exc_info=True)
            try: await temp_reply_generating.edit_text("Sorry, an error occurred while generating the status dashboard.")
            except Exception: await update.message.reply_text("Sorry, an error occurred while generating the status dashboard.")
        finally:
            if 'dashboard_filepath' in locals() and os.path.exists(dashboard_filepath):
                try: os.remove(dashboard_filepath)
                except Exception as e_rem_dash: logger.warning(f"Could not remove temp dashboard file {dashboard_filepath}: {e_rem_dash}")

    async def _format_and_send_customer_calendar_summary(self, user_id: int, customer_state: dict, context: ContextTypes.DEFAULT_TYPE):
        """Helper to format and send calendar summary to the customer."""
        calendar_events = customer_state.get("calendar_events", [])
        summary_parts = []
        if calendar_events:
            summary_parts.append(config.TELEGRAM_CUSTOMER_CALENDAR_SUMMARY_HEADER)
            # Sort events by date and time
            try:
                sorted_events = sorted(
                    calendar_events,
                    key=lambda x: (
                        str(x.get("date", "9999-99-99")),
                        str(x.get("time", "99:99"))
                    )
                )
            except: # Fallback if sorting fails
                sorted_events = calendar_events

            for event in sorted_events:
                desc = event.get("description", "Событие")
                date_str = event.get("date", "")
                time_str = event.get("time", "")
                event_line = f"- {desc}"
                if date_str: event_line += f" на {date_str}"
                if time_str: event_line += f" в {time_str}"
                summary_parts.append(event_line)
        else:
            summary_parts.append(config.TELEGRAM_CUSTOMER_NO_CALENDAR_EVENTS)
        
        full_summary_message = "\n".join(summary_parts)
        await context.bot.send_message(chat_id=user_id, text=full_summary_message)
        logger.info(f"Sent calendar summary to customer {user_id}.")


    async def _start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        user_id = user.id
        current_time_iso = get_current_timestamp_iso()

        if user_id == self.admin_user_id_int:
            logger.info(f"/start command received from admin user {user_id}.")
            await update.message.reply_text(config.TELEGRAM_START_MESSAGE)
            await self._send_dashboard_to_admin(update, context)
        else:
            logger.info(f"/start command from non-admin user {user_id} ({user.username}).")
            customer_state = load_or_initialize_customer_state(user_id, self.gui_callbacks)
            customer_name = customer_state.get("name", "unknown")
            is_known_customer_by_name = customer_name != "unknown" and customer_name is not None

            messages_to_send = []
            if is_known_customer_by_name:
                messages_to_send.append(config.TELEGRAM_RETURNING_CUSTOMER_GREETING_KNOWN_NAME.format(customer_name=customer_name))
                # Calendar summary will be handled by a separate call to _format_and_send_customer_calendar_summary
            else: # New user or name still unknown in state
                messages_to_send.append(config.TELEGRAM_NON_ADMIN_GREETING)

            # Send initial greeting part
            if messages_to_send:
                await update.message.reply_text("\n".join(messages_to_send))
            
            # Update chat history with bot's greeting
            customer_state["chat_history"] = [] # Reset history for a /start command re-engagement
            for msg_text in messages_to_send:
                customer_state["chat_history"].append({
                    "sender": "bot", "message": msg_text, "timestamp": current_time_iso
                })

            if is_known_customer_by_name:
                await self._format_and_send_customer_calendar_summary(user_id, customer_state, context)
                # Add calendar summary to chat history (optional, could be verbose)
                # For now, let's assume the action of sending is enough, actual text not in customer history.
                await update.message.reply_text(config.TELEGRAM_RETURNING_CUSTOMER_QUESTION_PROMPT)
                customer_state["chat_history"].append({
                    "sender": "bot", "message": config.TELEGRAM_RETURNING_CUSTOMER_QUESTION_PROMPT, "timestamp": current_time_iso
                })

            customer_state["conversation_stage"] = "awaiting_initial_reply"
            customer_state["last_message_timestamp"] = "" # Reset timer, let their next message trigger it
            save_customer_state(user_id, customer_state, self.gui_callbacks)

    async def _text_message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        user_id = user.id
        text = update.message.text
        current_time_iso = get_current_timestamp_iso()

        if user_id == self.admin_user_id_int:
            logger.info(f"Admin message from {user_id}: '{text[:70]}...'")
            self.message_queue_for_admin_llm.put(("telegram_text_admin", user_id, text))
        else:
            logger.info(f"Customer message from {user_id} ({user.username}): '{text[:70]}...'")
            customer_state = load_or_initialize_customer_state(user_id, self.gui_callbacks)
            current_stage = customer_state.get("conversation_stage", "new")
            customer_name = customer_state.get("name", "unknown")
            is_known_customer_by_name = customer_name != "unknown" and customer_name is not None

            # Always append customer's current message to their history
            customer_state["chat_history"].append({
                "sender": "customer", "message": text, "timestamp": current_time_iso
            })

            # Check if this is a re-engagement from a known customer
            is_new_conversation_cycle = current_stage in ["llm_followup_sent", "interaction_closed", "error_forwarded_to_admin", "new"]

            if is_known_customer_by_name and is_new_conversation_cycle:
                logger.info(f"Re-engagement from known customer {user_id} (name: {customer_name}). Stage was: {current_stage}")
                greeting_msg = config.TELEGRAM_RETURNING_CUSTOMER_GREETING_KNOWN_NAME.format(customer_name=customer_name)
                await update.message.reply_text(greeting_msg)
                customer_state["chat_history"].append({"sender": "bot", "message": greeting_msg, "timestamp": current_time_iso})
                
                await self._format_and_send_customer_calendar_summary(user_id, customer_state, context)
                
                question_prompt_msg = config.TELEGRAM_RETURNING_CUSTOMER_QUESTION_PROMPT
                await update.message.reply_text(question_prompt_msg)
                customer_state["chat_history"].append({"sender": "bot", "message": question_prompt_msg, "timestamp": current_time_iso})
                
                customer_state["conversation_stage"] = "aggregating_messages" # Their current text message starts aggregation
            
            elif not is_known_customer_by_name and (is_new_conversation_cycle or current_stage == "awaiting_initial_reply"):
                # New user, or user whose name we prompted for but haven't gotten/processed via LLM yet.
                # If it's their very first message after /start, "awaiting_initial_reply" is fine.
                # If they send a message and stage is "new", it means /start wasn't used or state was reset.
                if current_stage == "new": # Treat as if /start was implicitly called by their message
                    greeting_msg = config.TELEGRAM_NON_ADMIN_GREETING
                    await update.message.reply_text(greeting_msg)
                    # Insert greeting before their message in history if it's truly "new"
                    customer_state["chat_history"].insert(len(customer_state["chat_history"])-1, # Before last (their current) message
                        {"sender": "bot", "message": greeting_msg, "timestamp": current_time_iso}
                    )
                customer_state["conversation_stage"] = "aggregating_messages"

            else: # Continuing an existing aggregation, or some other intermediate state
                customer_state["conversation_stage"] = "aggregating_messages"
            
            # Update timestamp and record activity for aggregation timer
            customer_state["last_message_timestamp"] = current_time_iso
            save_customer_state(user_id, customer_state, self.gui_callbacks)
            self.customer_interaction_manager.record_customer_activity(user_id)


    async def _admin_voice_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        logger.info(f"Admin voice message from {user.id}. Duration: {update.message.voice.duration}s")
        if not PYDUB_AVAILABLE or not AudioSegment_class or not pydub_exceptions:
            logger.error("Pydub not available, cannot process admin voice message.")
            await update.message.reply_text("Sorry, I'm currently unable to process voice messages (admin audio converter missing).")
            return
        voice = update.message.voice
        file_id_suffix = f"admin_{update.effective_message.message_id}_{user.id}"
        temp_ogg_path = os.path.join(config.TELEGRAM_VOICE_TEMP_FOLDER, f"voice_{file_id_suffix}.oga")
        temp_wav_path = os.path.join(config.TELEGRAM_VOICE_TEMP_FOLDER, f"voice_{file_id_suffix}.wav")
        try:
            if self.gui_callbacks and callable(self.gui_callbacks.get('status_update')):
                self.gui_callbacks['status_update']("Received Admin voice, processing...")
            voice_file = await voice.get_file()
            await voice_file.download_to_drive(custom_path=temp_ogg_path)
            audio = AudioSegment_class.from_file(temp_ogg_path)
            audio = audio.set_frame_rate(config.INPUT_RATE).set_channels(config.CHANNELS)
            audio.export(temp_wav_path, format="wav")
            logger.info(f"Converted admin voice {temp_ogg_path} to {temp_wav_path}")
            self.message_queue_for_admin_llm.put(("telegram_voice_admin_wav", user.id, temp_wav_path))
            await update.message.reply_text("Got your voice message (admin), processing...")
        except FileNotFoundError as fnf_error:
            logger.critical(f"Admin Voice Pydub Error (likely FFmpeg missing): {fnf_error}.", exc_info=True)
            await update.message.reply_text("Admin Voice Error: Could not process audio (FFmpeg/converter missing?).")
        except pydub_exceptions.CouldntDecodeError as pde:
            logger.error(f"Admin Voice Pydub Decode Error for {temp_ogg_path}: {pde}.", exc_info=True)
            await update.message.reply_text("Admin Voice Error: Could not decode audio file.")
        except TelegramError as te:
            logger.error(f"Admin Voice Telegram API error: {te}", exc_info=True)
            await update.message.reply_text("Admin Voice Error: Telegram API issue.")
        except Exception as e:
            logger.error(f"Admin Voice unexpected error: {e}", exc_info=True)
            await update.message.reply_text("Admin Voice Error: Unexpected issue.")
        finally:
            if os.path.exists(temp_ogg_path):
                try: os.remove(temp_ogg_path)
                except Exception as e_rem: logger.warning(f"Could not remove temp admin OGG {temp_ogg_path}: {e_rem}")
            # WAV path is passed to queue, should be deleted by the processor of that queue item.

    def _run_polling_thread_target(self):
        logger.info("Telegram polling thread starting.")
        loop = None; app_for_thread = None
        try:
            loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
            with self._app_lock: self.async_loop = loop
            if not self.token: raise ValueError("Bot token is missing.")
            app_builder = Application.builder().token(self.token)\
                .read_timeout(10).connect_timeout(10)
            app_for_thread = app_builder.build()

            with self._app_lock: self.application = app_for_thread
            self._setup_application_handlers()
            loop.run_until_complete(self._set_bot_commands_on_startup())
            _set_telegram_bot_status("polling", self.gui_callbacks)
            logger.info("Starting Telegram bot polling (Application.run_polling)...")
            app_for_thread.run_polling(
                stop_signals=None, poll_interval=1.0, # type: ignore
                timeout=config.TELEGRAM_POLLING_TIMEOUT, drop_pending_updates=True,
            )
        except InvalidToken:
            logger.critical("Invalid Telegram Bot Token.", exc_info=True)
            _set_telegram_bot_status("bad_token", self.gui_callbacks, log_level=logging.CRITICAL)
        except NetworkError as ne:
            logger.error(f"Telegram NetworkError: {ne}.", exc_info=True)
            _set_telegram_bot_status("net_error", self.gui_callbacks, log_level=logging.ERROR)
        except asyncio.CancelledError: logger.info("Asyncio loop cancelled for polling.")
        except Exception as e:
            if not self._is_shutting_down:
                logger.error(f"Unexpected error in Telegram polling thread: {e}", exc_info=True)
                _set_telegram_bot_status("error", self.gui_callbacks, log_level=logging.ERROR)
            else: logger.info(f"Error during shutdown in polling thread (may be expected): {e}")
        finally:
            logger.info("Telegram polling thread entering finally cleanup.")
            if app_for_thread and hasattr(app_for_thread,'running') and app_for_thread.running and loop and loop.is_running():
                try: loop.run_until_complete(app_for_thread.shutdown())
                except Exception as e_sh: logger.warning(f"Error during app.shutdown() in finally: {e_sh}")
            with self._app_lock: self.application = None; current_loop_ref = self.async_loop; self.async_loop = None # type: ignore
            if current_loop_ref and current_loop_ref.is_running(): current_loop_ref.call_soon_threadsafe(current_loop_ref.stop)
            current_status_final = get_telegram_bot_status()
            if current_status_final not in ["off", "no_token", "no_admin", "bad_token", "net_error"]:
                 _set_telegram_bot_status("off", self.gui_callbacks)
            self._stop_polling_event.set()
            logger.info("Telegram polling thread finished execution.")

    def start_polling(self):
        logger.info("Request to start Telegram polling.")
        self._is_shutting_down = False
        current_status = get_telegram_bot_status()
        if current_status in ["polling", "loading"]: logger.info(f"Polling already {current_status}."); return False
        if not self.token: _set_telegram_bot_status("no_token", self.gui_callbacks,log_level=logging.ERROR); return False
        if not self.admin_user_id_int: _set_telegram_bot_status("no_admin", self.gui_callbacks, log_level=logging.ERROR); return False
        self._stop_polling_event.clear()
        _set_telegram_bot_status("loading", self.gui_callbacks)
        if self.polling_thread and not self.polling_thread.is_alive(): self.polling_thread = None
        if self.polling_thread is None:
            self.polling_thread = threading.Thread(target=self._run_polling_thread_target, daemon=True, name="TelegramPollingThread")
            self.polling_thread.start()
            logger.info("New Telegram polling thread started.")
            return True
        else: logger.warning("Polling thread exists and might be alive. Start aborted."); return False

    def stop_polling(self):
        logger.info("Request to stop Telegram polling.")
        if self._is_shutting_down and get_telegram_bot_status() == "off": logger.info("Stop polling already in progress/completed."); return
        self._is_shutting_down = True; self._stop_polling_event.set()
        loop_to_signal, app_to_signal = None, None
        with self._app_lock: loop_to_signal = self.async_loop; app_to_signal = self.application
        if app_to_signal and hasattr(app_to_signal, 'running') and app_to_signal.running and loop_to_signal and loop_to_signal.is_running():
            logger.info(f"Scheduling PTB application.stop() on its loop.")
            future = asyncio.run_coroutine_threadsafe(app_to_signal.stop(), loop_to_signal)
            try: future.result(timeout=5)
            except Exception as e: logger.warning(f"Error/Timeout during PTB app.stop() future: {e}")
        elif app_to_signal and hasattr(app_to_signal, 'running') and not app_to_signal.running:
            logger.info("PTB application already stopped or not running.")
        else: logger.info("No active PTB application or loop to signal stop to.")

        if self.polling_thread and self.polling_thread.is_alive():
            logger.info(f"Waiting for polling thread {self.polling_thread.name} to join...")
            self.polling_thread.join(timeout=7)
            if self.polling_thread.is_alive(): logger.warning(f"Polling thread {self.polling_thread.name} did not join cleanly.")
            else: logger.info(f"Polling thread {self.polling_thread.name} joined.")
        self.polling_thread = None
        current_status_final = get_telegram_bot_status()
        if current_status_final not in ["no_token", "no_admin", "bad_token", "net_error", "off"]:
            _set_telegram_bot_status("off", self.gui_callbacks)
        logger.info("Stop polling sequence finished.")


    async def send_text_message_to_user(self, target_user_id: int, text: str):
        with self._app_lock: app = self.application
        if not app or not hasattr(app, 'bot') or not app.bot or not target_user_id:
            logger.error(f"Cannot send Telegram text: App/Bot not ready or no target_user_id. App: {bool(app)}, Bot: {bool(hasattr(app, 'bot') and app.bot if app else None)}, TargetID: {target_user_id}")
            return
        try:
            await app.bot.send_message(chat_id=target_user_id, text=text)
            logger.info(f"Text message sent to user {target_user_id}: '{text[:70]}...'")
        except TelegramError as e: logger.error(f"TelegramError sending text to {target_user_id}: {e}", exc_info=True)
        except Exception as e: logger.error(f"Failed to send text to {target_user_id}: {e}", exc_info=True)

    async def send_voice_message_to_admin(self, voice_filepath: str): # Specifically for admin
        with self._app_lock: app = self.application
        if not app or not hasattr(app, 'bot') or not app.bot or not self.admin_user_id_int:
            logger.error(f"Cannot send voice to admin: App/Bot not ready or no Admin ID."); return
        if not os.path.exists(voice_filepath):
            logger.error(f"Cannot send voice to admin: File not found at {voice_filepath}"); return
        try:
            with open(voice_filepath, 'rb') as voice_file_to_send:
                await app.bot.send_voice(chat_id=self.admin_user_id_int, voice=InputFile(voice_file_to_send))
            logger.info(f"Voice message sent to admin {self.admin_user_id_int} from file: {voice_filepath}")
        except TelegramError as e: logger.error(f"TelegramError sending voice to admin: {e}", exc_info=True)
        except Exception as e: logger.error(f"Unexpected error sending voice to admin: {e}", exc_info=True)

    async def send_voice_message_to_user(self, target_user_id: int, voice_filepath: str): # Generic for any user
        with self._app_lock: app = self.application
        if not app or not hasattr(app, 'bot') or not app.bot or not target_user_id:
            logger.error(f"Cannot send voice to user {target_user_id}: App/Bot not ready or no User ID."); return
        if not os.path.exists(voice_filepath):
            logger.error(f"Cannot send voice to user {target_user_id}: File not found at {voice_filepath}"); return
        try:
            with open(voice_filepath, 'rb') as voice_file_to_send:
                await app.bot.send_voice(chat_id=target_user_id, voice=InputFile(voice_file_to_send))
            logger.info(f"Voice message sent to user {target_user_id} from file: {voice_filepath}")
        except TelegramError as e: logger.error(f"TelegramError sending voice to user {target_user_id}: {e}", exc_info=True)
        except Exception as e: logger.error(f"Unexpected error sending voice to user {target_user_id}: {e}", exc_info=True)


    def get_status(self): return get_telegram_bot_status()
    def full_shutdown(self): logger.info("Full shutdown of TelegramBotHandler."); self.stop_polling()