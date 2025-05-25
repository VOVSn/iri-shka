# gui_manager.py
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from datetime import datetime # Added for sorting calendar events

import logging
logger = logging.getLogger("Iri-shka_App.GUIManager") # Child logger for this module

class GUIManager:
    def __init__(self, root_tk_instance, action_callbacks):
        self.app_window = root_tk_instance
        self.action_callbacks = action_callbacks
        logger.info("GUIManager initialized.")

        # GUI Elements
        self.speak_button = None
        self.chat_history_display = None
        self.todo_list_display = None
        self.calendar_events_display = None

        # Component Status
        self.memory_status_frame = None
        self.memory_status_text_label = None
        self.hearing_status_frame = None
        self.hearing_status_text_label = None
        self.voice_status_frame = None
        self.voice_status_text_label = None
        self.mind_status_frame = None
        self.mind_status_text_label = None

        # Combined Status Bar Elements
        self.combined_status_bar_frame = None
        self.app_status_label = None
        self.gpu_mem_label = None
        self.gpu_util_label = None


        self._setup_styles()
        self._setup_widgets()
        self._configure_tags_for_chat_display()
        self._setup_protocol_handlers()
        logger.debug("GUIManager setup complete.")

    def _setup_styles(self):
        logger.debug("Setting up GUI styles.")
        style = ttk.Style()

        available_themes = style.theme_names()
        logger.debug(f"Available ttk themes: {available_themes}")
        preferred_themes = ['clam', 'vista', 'alt', 'default', 'classic']
        chosen_theme = None
        for theme_name in preferred_themes:
            if theme_name in available_themes:
                try:
                    style.theme_use(theme_name)
                    logger.info(f"Using ttk theme: '{theme_name}'")
                    chosen_theme = theme_name
                    break
                except tk.TclError as e:
                    logger.warning(f"Could not use theme '{theme_name}': {e}")
        if not chosen_theme:
            logger.warning("Could not set a preferred ttk theme. Using system default.")

        style.configure("TButton", padding=6, font=('Helvetica', 12))
        style.configure("AppStatus.TLabel", padding=(6,3), font=('Helvetica', 10), anchor="w")
        style.configure("GPUStatus.TLabel", font=('Consolas', 9), padding=(3,3))
        style.configure("ComponentStatus.TLabel", font=('Consolas', 9, 'bold'), anchor="center")

        try:
            style.configure("TLabelFrame.Label", font=('Helvetica', 9, 'bold'))
            logger.info("Applied bold font to all TLabelFrame.Label elements.")
        except tk.TclError as e:
            logger.warning(f"Could not configure default TLabelFrame.Label style: {e}. Labels may not be bold.")


    def _setup_widgets(self):
        logger.debug("Setting up GUI widgets.")
        self.app_window.title("Iri-shka: Voice AI Assistant")
        self.app_window.geometry("900x850")

        main_frame = ttk.Frame(self.app_window, padding="10")
        main_frame.pack(expand=True, fill=tk.BOTH)

        # Combined Status Bar - Packed at the bottom of main_frame
        self.combined_status_bar_frame = ttk.Frame(main_frame, height=35, relief=tk.GROOVE, borderwidth=1)
        self.combined_status_bar_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5, 0))
        self.combined_status_bar_frame.pack_propagate(False)
        # ... (Populate combined_status_bar_frame as before) ...
        component_box_width = 100
        component_box_height = 30
        self.memory_status_frame = tk.Frame(self.combined_status_bar_frame, width=component_box_width, height=component_box_height, relief=tk.SUNKEN, borderwidth=1)
        self.memory_status_frame.pack(side=tk.LEFT, padx=(2,2), pady=2, fill=tk.Y); self.memory_status_frame.pack_propagate(False)
        self.memory_status_text_label = ttk.Label(self.memory_status_frame, text="MEM: CHK", style="ComponentStatus.TLabel"); self.memory_status_text_label.pack(expand=True, fill=tk.BOTH)
        self.hearing_status_frame = tk.Frame(self.combined_status_bar_frame, width=component_box_width, height=component_box_height, relief=tk.SUNKEN, borderwidth=1)
        self.hearing_status_frame.pack(side=tk.LEFT, padx=2, pady=2, fill=tk.Y); self.hearing_status_frame.pack_propagate(False)
        self.hearing_status_text_label = ttk.Label(self.hearing_status_frame, text="HEAR: CHK", style="ComponentStatus.TLabel"); self.hearing_status_text_label.pack(expand=True, fill=tk.BOTH)
        self.voice_status_frame = tk.Frame(self.combined_status_bar_frame, width=component_box_width, height=component_box_height, relief=tk.SUNKEN, borderwidth=1)
        self.voice_status_frame.pack(side=tk.LEFT, padx=2, pady=2, fill=tk.Y); self.voice_status_frame.pack_propagate(False)
        self.voice_status_text_label = ttk.Label(self.voice_status_frame, text="VOICE: CHK", style="ComponentStatus.TLabel"); self.voice_status_text_label.pack(expand=True, fill=tk.BOTH)
        self.mind_status_frame = tk.Frame(self.combined_status_bar_frame, width=component_box_width, height=component_box_height, relief=tk.SUNKEN, borderwidth=1)
        self.mind_status_frame.pack(side=tk.LEFT, padx=(2,5), pady=2, fill=tk.Y); self.mind_status_frame.pack_propagate(False)
        self.mind_status_text_label = ttk.Label(self.mind_status_frame, text="MIND: CHK", style="ComponentStatus.TLabel"); self.mind_status_text_label.pack(expand=True, fill=tk.BOTH)
        self.gpu_util_label = ttk.Label(self.combined_status_bar_frame, text="GPU Util: N/A", style="GPUStatus.TLabel"); self.gpu_util_label.pack(side=tk.RIGHT, padx=(2,5), pady=2, fill=tk.Y)
        self.gpu_mem_label = ttk.Label(self.combined_status_bar_frame, text="GPU Mem: N/A", style="GPUStatus.TLabel"); self.gpu_mem_label.pack(side=tk.RIGHT, padx=2, pady=2, fill=tk.Y)
        self.app_status_label = ttk.Label(self.combined_status_bar_frame, text="Initializing...", style="AppStatus.TLabel", relief=tk.FLAT); self.app_status_label.pack(side=tk.LEFT, padx=5, pady=2, fill=tk.BOTH, expand=True)

        # Speak Button Frame - Packed above status bar
        speak_button_frame = ttk.Frame(main_frame)
        speak_button_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5, 5))
        self.speak_button = ttk.Button(speak_button_frame, text="Loading...", command=self.action_callbacks['toggle_speaking_recording'], state=tk.DISABLED)
        self.speak_button.pack(ipady=10, ipadx=20)

        # User Info Frame - Packed above speak button, uses GRID internally
        user_info_frame_height = 350 
        user_info_frame = ttk.Frame(main_frame, height=user_info_frame_height)
        user_info_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5, 5))
        user_info_frame.pack_propagate(False) 

        user_info_frame.columnconfigure(0, weight=3) 
        user_info_frame.columnconfigure(1, weight=2) 
        user_info_frame.rowconfigure(0, weight=1)    

        # Calendar Events List (Left side, wider)
        calendar_labelframe = ttk.LabelFrame(user_info_frame, text="Calendar Events")
        calendar_labelframe.grid(row=0, column=0, sticky="nsew", padx=(0, 5), pady=2)
        self.calendar_events_display = scrolledtext.ScrolledText(
            calendar_labelframe, wrap=tk.WORD, height=9, # Increased height to 9 lines
            state=tk.DISABLED, font=('Helvetica', 9)
        )
        self.calendar_events_display.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0,5))

        # Todos List (Right side, narrower)
        todos_labelframe = ttk.LabelFrame(user_info_frame, text="Pending Todos")
        todos_labelframe.grid(row=0, column=1, sticky="nsew", padx=(5, 0), pady=2)
        self.todo_list_display = scrolledtext.ScrolledText(
            todos_labelframe, wrap=tk.WORD, height=9, # Increased height to 9 lines
            state=tk.DISABLED, font=('Helvetica', 9)
        )
        self.todo_list_display.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0,5))

        # Chat History - Packed at the top of main_frame, expands
        self.chat_history_display = scrolledtext.ScrolledText(main_frame, wrap=tk.WORD, state=tk.DISABLED, font=('Helvetica', 10))
        self.chat_history_display.pack(pady=(0, 10), fill=tk.BOTH, expand=True)

        logger.debug("GUI widgets setup finished.")

    # ... (rest of the GUIManager class remains the same) ...
    def _configure_tags_for_chat_display(self):
        if not self.chat_history_display:
            logger.warning("Chat history display not available for tag configuration.")
            return
        logger.debug("Configuring tags for chat display.")
        self.chat_history_display.tag_configure("user_tag", foreground="blue")
        self.chat_history_display.tag_configure("assistant_tag", foreground="green")
        self.chat_history_display.tag_configure("assistant_tag_error", foreground="orange red")

    def _setup_protocol_handlers(self):
        if self.app_window and 'on_exit' in self.action_callbacks:
            logger.debug("Setting up WM_DELETE_WINDOW protocol handler.")
            self.app_window.protocol("WM_DELETE_WINDOW", self.action_callbacks['on_exit'])
        else:
            logger.warning("App window or on_exit callback not available for protocol handler setup.")

    def _safe_ui_update(self, update_lambda):
        if self.app_window and self.app_window.winfo_exists():
            self.app_window.after(0, update_lambda)
        else:
            logger.warning("Attempted safe UI update, but app window no longer exists.")


    def update_status_label(self, msg):
        if self.app_status_label:
            logger.debug(f"Updating main status label to: '{msg}'")
            self._safe_ui_update(lambda: self.app_status_label.config(text=msg))
        else:
            logger.warning(f"Attempted to update status label, but app_status_label is None. Msg: '{msg}'")

    def update_speak_button(self, enabled, text=None):
        if self.speak_button:
            def _update():
                current_text = self.speak_button.cget("text") if text is None else text
                logger.debug(f"Updating speak button: enabled={enabled}, text='{current_text}'")
                self.speak_button.config(state=tk.NORMAL if enabled else tk.DISABLED, text=current_text)
            self._safe_ui_update(_update)
        else:
            logger.warning(f"Attempted to update speak button, but speak_button is None. Enabled={enabled}, Text='{text}'")

    def _update_component_status_widget_internal(self, widget_frame, widget_label, text_to_display, status_category):
        if not widget_frame or not widget_label:
            logger.warning(f"Attempted to update component status, but frame or label is None. Text: '{text_to_display}', Category: '{status_category}'")
            return

        widget_label.config(text=text_to_display)
        bg_color = "light grey"; text_color = "black"
        if status_category == "ready": bg_color = "#90EE90"; text_color = "dark green"
        elif status_category in ["loaded", "saved", "fresh"]: bg_color = "#ADD8E6"; text_color = "navy"
        elif status_category in ["loading", "checking", "pinging", "thinking"]: bg_color = "#FFFFE0"; text_color = "darkgoldenrod"
        elif status_category in ["error", "na", "timeout", "conn_error", "http_502", "http_other", "InitFail"]: bg_color = "#FFA07A"; text_color = "darkred"
        widget_frame.config(background=bg_color)
        widget_label.config(background=bg_color, foreground=text_color)


    def update_memory_status(self, short_text, status_type_str):
        logger.debug(f"Updating memory status: Text='{short_text}', Type='{status_type_str}'")
        self._safe_ui_update(lambda: self._update_component_status_widget_internal(
            self.memory_status_frame, self.memory_status_text_label, short_text, status_type_str
        ))

    def update_hearing_status(self, short_text, status_type_str):
        logger.debug(f"Updating hearing status: Text='{short_text}', Type='{status_type_str}'")
        self._safe_ui_update(lambda: self._update_component_status_widget_internal(
            self.hearing_status_frame, self.hearing_status_text_label, short_text, status_type_str
        ))

    def update_voice_status(self, short_text, status_type_str):
        logger.debug(f"Updating voice status: Text='{short_text}', Type='{status_type_str}'")
        self._safe_ui_update(lambda: self._update_component_status_widget_internal(
            self.voice_status_frame, self.voice_status_text_label, short_text, status_type_str
        ))

    def update_mind_status(self, short_text, status_type_str):
        logger.debug(f"Updating mind status: Text='{short_text}', Type='{status_type_str}'")
        self._safe_ui_update(lambda: self._update_component_status_widget_internal(
            self.mind_status_frame, self.mind_status_text_label, short_text, status_type_str
        ))

    def update_gpu_status_display(self, mem_text, util_text, status_category):
        def _update():
            if not self.gpu_mem_label or not self.gpu_util_label:
                logger.warning(f"Attempted to update GPU status display, but labels are None. Mem='{mem_text}', Util='{util_text}'")
                return
            self.gpu_mem_label.config(text=f"GPU Mem: {mem_text}")
            self.gpu_util_label.config(text=f"GPU Util: {util_text}")
            fg_color = "dimgray"
            if status_category == "ok_gpu": fg_color = "darkslategrey"
            elif status_category == "na_nvml": fg_color = "silver"
            elif status_category in ["error", "error_nvml_loop", "InitFail", "checking"]: fg_color = "red"
            if status_category == "checking": fg_color = "orange"
            self.gpu_mem_label.config(foreground=fg_color)
            self.gpu_util_label.config(foreground=fg_color)
        self._safe_ui_update(_update)


    def _add_message_to_display_internal(self, message_with_prefix, tag, is_error=False):
        if not self.chat_history_display:
            logger.warning(f"Attempted to add message to display, but chat_history_display is None. Message: '{message_with_prefix[:50]}...'")
            return
        actual_tag = tag
        if is_error and tag == "assistant_tag": actual_tag = "assistant_tag_error"
        self.chat_history_display.config(state=tk.NORMAL)
        self.chat_history_display.insert(tk.END, message_with_prefix, actual_tag)
        self.chat_history_display.see(tk.END)
        self.chat_history_display.config(state=tk.DISABLED)

    def add_user_message_to_display(self, text):
        logger.info(f"Displaying User message: '{text[:70]}...'")
        self._safe_ui_update(lambda: self._add_message_to_display_internal(f"You: {text}\n", "user_tag"))

    def add_assistant_message_to_display(self, text, is_error=False):
        logger.info(f"Displaying Assistant message (Error={is_error}): '{text[:70]}...'")
        if not text.endswith("\n\n"):
            text = text + "\n" if text.endswith("\n") else text + "\n\n"
        self._safe_ui_update(lambda: self._add_message_to_display_internal(f"Iri-shka: {text}", "assistant_tag", is_error=is_error))

    def update_chat_display_from_list(self, chat_history_list):
        if not self.chat_history_display:
            logger.warning("Attempted to update chat display from list, but chat_history_display is None.")
            return
        logger.info(f"Updating chat display from history list (length: {len(chat_history_list)}).")
        def _update():
            self.chat_history_display.config(state=tk.NORMAL)
            self.chat_history_display.delete(1.0, tk.END)
            for turn in chat_history_list:
                user_message = turn.get('user', '')
                assistant_message = turn.get('assistant', '')
                if user_message:
                    self._add_message_to_display_internal(f"You: {user_message}\n", "user_tag")
                if assistant_message:
                    is_error = assistant_message.startswith(("[Ollama Error:", "[LLM Error:", "[LLM Unreachable:")) or \
                               assistant_message == "I didn't catch that, could you please repeat?" or \
                               assistant_message == "Я не расслышала, не могли бы вы повторить?"
                    formatted_assistant_msg = assistant_message
                    if not formatted_assistant_msg.endswith("\n\n"):
                        formatted_assistant_msg = formatted_assistant_msg + "\n" if formatted_assistant_msg.endswith("\n") else formatted_assistant_msg + "\n\n"
                    self._add_message_to_display_internal(f"Iri-shka: {formatted_assistant_msg}", "assistant_tag", is_error=is_error)
            self.chat_history_display.see(tk.END)
            self.chat_history_display.config(state=tk.DISABLED)
        self._safe_ui_update(_update)

    def update_todo_list(self, todos):
        if not self.todo_list_display:
            logger.warning("Todo list display not available for update.")
            return
        logger.info(f"Updating todo list display with {len(todos) if isinstance(todos, list) else 0} items.")
        def _update():
            self.todo_list_display.config(state=tk.NORMAL)
            self.todo_list_display.delete(1.0, tk.END)
            if not todos or not isinstance(todos, list):
                self.todo_list_display.insert(tk.END, "No todos." if not todos else "Invalid todo data.")
            else:
                for todo_item in todos:
                    self.todo_list_display.insert(tk.END, f"- {str(todo_item)}\n")
            self.todo_list_display.config(state=tk.DISABLED)
            self.todo_list_display.see(tk.END)
        self._safe_ui_update(_update)

    def update_calendar_events_list(self, events):
        if not self.calendar_events_display:
            logger.warning("Calendar events display not available for update.")
            return
        logger.info(f"Updating calendar events display with {len(events) if isinstance(events, list) else 0} items.")

        def _sort_key_calendar_event(event):
            if not isinstance(event, dict):
                logger.warning(f"Calendar event item is not a dictionary: {event}. Placing last in sort.")
                return datetime.max
            date_str = event.get("date", "1900-01-01")
            time_str = event.get("time", "00:00")
            try:
                return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            except ValueError:
                logger.debug(f"Malformed date/time '{date_str} {time_str}' for {event}. Trying date only.")
                try: return datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    logger.warning(f"Unparseable date '{date_str}' for {event}. Placing last.")
                    return datetime.max

        sorted_events = []
        if isinstance(events, list):
            try:
                valid_events = [e for e in events if isinstance(e, dict)]
                if len(valid_events) != len(events): logger.warning("Non-dict items in calendar_events.")
                sorted_events = sorted(valid_events, key=_sort_key_calendar_event)
            except Exception as e_sort:
                logger.error(f"Error sorting calendar events: {e_sort}. Displaying as received.", exc_info=True)
                sorted_events = [e for e in events if isinstance(e, dict)] if isinstance(events, list) else []
        else:
            logger.warning(f"Calendar events data is not a list: {type(events)}.")

        def _update():
            self.calendar_events_display.config(state=tk.NORMAL)
            self.calendar_events_display.delete(1.0, tk.END)
            if not sorted_events:
                self.calendar_events_display.insert(tk.END, "No calendar events.")
            else:
                for event in sorted_events:
                    if not isinstance(event, dict):
                        self.calendar_events_display.insert(tk.END, f"- Invalid: {str(event)[:50]}...\n")
                        continue
                    date_str = event.get("date", "N/A")
                    time_str = event.get("time")
                    desc = event.get("description", event.get("name", "Unnamed Event"))
                    display_text = f"{date_str}"
                    if time_str: display_text += f" {time_str}"
                    display_text += f": {desc}\n"
                    self.calendar_events_display.insert(tk.END, display_text)
            self.calendar_events_display.config(state=tk.DISABLED)
            self.calendar_events_display.see(tk.END)
        self._safe_ui_update(_update)

    def show_error_messagebox(self, title, msg):
        logger.error(f"Displaying error messagebox: Title='{title}', Message='{msg}'")
        self._safe_ui_update(lambda: messagebox.showerror(title, msg, parent=self.app_window if self.app_window and self.app_window.winfo_exists() else None))

    def show_info_messagebox(self, title, msg):
        logger.info(f"Displaying info messagebox: Title='{title}', Message='{msg}'")
        self._safe_ui_update(lambda: messagebox.showinfo(title, msg, parent=self.app_window if self.app_window and self.app_window.winfo_exists() else None))

    def show_warning_messagebox(self, title, msg):
        logger.warning(f"Displaying warning messagebox: Title='{title}', Message='{msg}'")
        self._safe_ui_update(lambda: messagebox.showwarning(title, msg, parent=self.app_window if self.app_window and self.app_window.winfo_exists() else None))

    def destroy_window(self):
        if self.app_window:
            logger.info("Destroying Tkinter window...")
            try:
                self.app_window.destroy()
                logger.info("Tkinter window destroyed successfully.")
            except tk.TclError as e:
                logger.warning(f"Tkinter error during destroy (often ignorable if already destroyed): {e}", exc_info=False)
            self.app_window = None
        else:
            logger.info("Destroy window called, but no app_window instance to destroy.")