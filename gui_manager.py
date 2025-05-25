# gui_manager.py
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from datetime import datetime # Added for sorting calendar events
import config # Import config to access GUI_THEME_LIGHT/DARK

import logging
logger = logging.getLogger("Iri-shka_App.GUIManager") # Child logger for this module

class GUIManager:
    def __init__(self, root_tk_instance, action_callbacks, initial_theme=config.GUI_THEME_LIGHT):
        self.app_window = root_tk_instance
        self.action_callbacks = action_callbacks
        self.current_theme = initial_theme
        logger.info(f"GUIManager initialized with theme: {self.current_theme}.")

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

        # Store ScrolledText widgets for easier re-theming
        self.scrolled_text_widgets = []

        # Define theme color palettes
        self.dark_theme_colors = {
            "bg": "#2B2B2B",
            "fg": "#D3D3D3", # LightGray for general text
            "frame_bg": "#3C3F41", # For frames, labelframes
            "label_fg": "#E0E0E0", # Slightly brighter for labels
            "button_bg": "#555555",
            "button_fg": "#FFFFFF",
            "button_active_bg": "#6A6A6A",
            "button_disabled_fg": "#888888",
            "entry_bg": "#333333", # For Text, ScrolledText entry areas
            "entry_fg": "#D3D3D3",
            "entry_insert_bg": "#FFFFFF", # Cursor color in text areas
            "entry_select_bg": "#0078D7", # Selection background (like VSCode)
            "entry_select_fg": "#FFFFFF", # Selection foreground
            "user_msg_fg": "sky blue",
            "assistant_msg_fg": "light green",
            "assistant_error_fg": "salmon",
            "component_status_label_default_bg": "#4A4A4A", # Base for status labels if not colored by status
            "component_status_label_default_fg": "#CCCCCC",
        }
        self.light_theme_colors = { # Based on typical 'clam' or system defaults
            "bg": "SystemButtonFace", # Use system defaults for light theme
            "fg": "SystemWindowText",
            "frame_bg": "SystemButtonFace",
            "label_fg": "SystemWindowText",
            "button_bg": "SystemButtonFace",
            "button_fg": "SystemButtonText",
            "button_active_bg": "SystemHighlight",
            "button_disabled_fg": "SystemGrayText",
            "entry_bg": "white",
            "entry_fg": "black",
            "entry_insert_bg": "black",
            "entry_select_bg": "SystemHighlight",
            "entry_select_fg": "SystemHighlightText",
            "user_msg_fg": "blue",
            "assistant_msg_fg": "green",
            "assistant_error_fg": "orange red",
            "component_status_label_default_bg": "SystemButtonFace",
            "component_status_label_default_fg": "SystemWindowText",
        }

        self.apply_theme(self.current_theme, initial_setup=True)
        self._setup_widgets()
        self._configure_tags_for_chat_display()
        self._setup_protocol_handlers() # This line was causing the error
        logger.debug("GUIManager setup complete.")

    def get_current_theme_colors(self):
        return self.dark_theme_colors if self.current_theme == config.GUI_THEME_DARK else self.light_theme_colors

    def apply_theme(self, theme_name_to_apply, initial_setup=False):
        logger.info(f"Applying theme: {theme_name_to_apply}. Initial setup: {initial_setup}")
        self.current_theme = theme_name_to_apply
        colors = self.get_current_theme_colors()
        style = ttk.Style()

        if self.app_window:
             self.app_window.configure(background=colors["bg"])

        if self.current_theme == config.GUI_THEME_DARK:
            logger.debug("Configuring Dark Theme styles.")
            try: style.theme_use('clam')
            except tk.TclError: style.theme_use('default')

            style.configure(".", background=colors["bg"], foreground=colors["fg"],
                            fieldbackground=colors["entry_bg"], bordercolor="#666666", lightcolor="#4f4f4f", darkcolor="#222222")
            style.configure("TFrame", background=colors["frame_bg"])
            style.configure("TLabel", background=colors["frame_bg"], foreground=colors["label_fg"])
            style.configure("TButton", padding=6, font=('Helvetica', 12),
                            background=colors["button_bg"], foreground=colors["button_fg"],
                            relief=tk.FLAT, borderwidth=1, bordercolor="#777777") # Added bordercolor
            style.map("TButton",
                      background=[('active', colors["button_active_bg"]), ('disabled', colors["button_bg"])],
                      foreground=[('disabled', colors["button_disabled_fg"])],
                      relief=[('pressed', tk.SUNKEN), ('!pressed', tk.FLAT)],
                      bordercolor=[('focus', '#0078D4'), ('!focus', "#777777")]) # Focus border

            style.configure("TLabelFrame", background=colors["frame_bg"], bordercolor="#777777", relief=tk.GROOVE)
            style.configure("TLabelFrame.Label", background=colors["frame_bg"], foreground=colors["label_fg"],
                            font=('Helvetica', 9, 'bold'))

            style.configure("AppStatus.TLabel", padding=(6,3), font=('Helvetica', 10), anchor="w",
                            background=colors["frame_bg"], foreground=colors["label_fg"])
            style.configure("GPUStatus.TLabel", font=('Consolas', 9), padding=(3,3),
                            background=colors["frame_bg"], foreground=colors["label_fg"])
            style.configure("ComponentStatus.TLabel", font=('Consolas', 9, 'bold'), anchor="center")

        else: # Light Theme
            logger.debug("Configuring Light Theme styles (using preferred built-in).")
            preferred_themes = ['clam', 'vista', 'alt', 'default', 'classic']
            chosen_theme = None
            current_themes = style.theme_names()
            for theme_name_option in preferred_themes:
                if theme_name_option in current_themes:
                    try:
                        style.theme_use(theme_name_option)
                        logger.info(f"Using ttk theme: '{theme_name_option}' for Light mode.")
                        chosen_theme = theme_name_option
                        break
                    except tk.TclError as e:
                        logger.warning(f"Could not use theme '{theme_name_option}': {e}")
            if not chosen_theme:
                logger.warning("Could not set a preferred ttk theme for Light mode. Using system default.")
                if current_themes: style.theme_use(current_themes[0])

            style.configure("TButton", padding=6, font=('Helvetica', 12))
            try:
                style.configure("TLabelFrame.Label", font=('Helvetica', 9, 'bold'))
            except tk.TclError: pass # Ignore if this specific sub-style fails on some themes
            style.configure("AppStatus.TLabel", padding=(6,3), font=('Helvetica', 10), anchor="w")
            style.configure("GPUStatus.TLabel", font=('Consolas', 9), padding=(3,3))
            style.configure("ComponentStatus.TLabel", font=('Consolas', 9, 'bold'), anchor="center")

        if not initial_setup and self.app_window:
            self._reconfigure_standard_tk_widgets()
            self._configure_tags_for_chat_display()
            logger.debug("Theme changed dynamically. Non-ttk widgets and tags reconfigured.")

    def _reconfigure_standard_tk_widgets(self):
        colors = self.get_current_theme_colors()
        for st_widget in self.scrolled_text_widgets:
            if st_widget and st_widget.winfo_exists():
                try:
                    st_widget.configure(
                        background=colors["entry_bg"],
                        foreground=colors["entry_fg"],
                        insertbackground=colors["entry_insert_bg"],
                        selectbackground=colors["entry_select_bg"],
                        selectforeground=colors["entry_select_fg"]
                    )
                except tk.TclError as e:
                    logger.warning(f"Error re-theming ScrolledText widget: {e}")

        tk_frames_to_theme = [
            self.memory_status_frame, self.hearing_status_frame,
            self.voice_status_frame, self.mind_status_frame
        ]
        # Also theme the main app window if it's a tk.Tk instance directly
        if self.app_window and isinstance(self.app_window, tk.Tk):
             self.app_window.configure(background=colors["bg"])


        for frame in tk_frames_to_theme:
            if frame and frame.winfo_exists():
                # These are tk.Frame, so use configure directly
                frame.configure(background=colors.get("component_status_label_default_bg", colors["frame_bg"]))


    def _setup_widgets(self):
        logger.debug("Setting up GUI widgets.")
        self.app_window.title("Iri-shka: Voice AI Assistant")
        self.app_window.geometry("900x850")
        # Main window background already set by apply_theme -> _reconfigure_standard_tk_widgets

        main_frame = ttk.Frame(self.app_window, padding="10")
        main_frame.pack(expand=True, fill=tk.BOTH)

        self.combined_status_bar_frame = ttk.Frame(main_frame, height=35, relief=tk.GROOVE, borderwidth=1)
        self.combined_status_bar_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5, 0))
        self.combined_status_bar_frame.pack_propagate(False)

        colors = self.get_current_theme_colors()
        component_box_width = 100
        component_box_height = 30
        component_frame_bg = colors.get("component_status_label_default_bg", colors["frame_bg"]) # Background for the tk.Frame holding the label

        self.memory_status_frame = tk.Frame(self.combined_status_bar_frame, width=component_box_width, height=component_box_height, relief=tk.SUNKEN, borderwidth=1, background=component_frame_bg)
        self.memory_status_frame.pack(side=tk.LEFT, padx=(2,2), pady=2, fill=tk.Y); self.memory_status_frame.pack_propagate(False)
        self.memory_status_text_label = ttk.Label(self.memory_status_frame, text="MEM: CHK", style="ComponentStatus.TLabel"); self.memory_status_text_label.pack(expand=True, fill=tk.BOTH)

        self.hearing_status_frame = tk.Frame(self.combined_status_bar_frame, width=component_box_width, height=component_box_height, relief=tk.SUNKEN, borderwidth=1, background=component_frame_bg)
        self.hearing_status_frame.pack(side=tk.LEFT, padx=2, pady=2, fill=tk.Y); self.hearing_status_frame.pack_propagate(False)
        self.hearing_status_text_label = ttk.Label(self.hearing_status_frame, text="HEAR: CHK", style="ComponentStatus.TLabel"); self.hearing_status_text_label.pack(expand=True, fill=tk.BOTH)

        self.voice_status_frame = tk.Frame(self.combined_status_bar_frame, width=component_box_width, height=component_box_height, relief=tk.SUNKEN, borderwidth=1, background=component_frame_bg)
        self.voice_status_frame.pack(side=tk.LEFT, padx=2, pady=2, fill=tk.Y); self.voice_status_frame.pack_propagate(False)
        self.voice_status_text_label = ttk.Label(self.voice_status_frame, text="VOICE: CHK", style="ComponentStatus.TLabel"); self.voice_status_text_label.pack(expand=True, fill=tk.BOTH)

        self.mind_status_frame = tk.Frame(self.combined_status_bar_frame, width=component_box_width, height=component_box_height, relief=tk.SUNKEN, borderwidth=1, background=component_frame_bg)
        self.mind_status_frame.pack(side=tk.LEFT, padx=(2,5), pady=2, fill=tk.Y); self.mind_status_frame.pack_propagate(False)
        self.mind_status_text_label = ttk.Label(self.mind_status_frame, text="MIND: CHK", style="ComponentStatus.TLabel"); self.mind_status_text_label.pack(expand=True, fill=tk.BOTH)

        self.gpu_util_label = ttk.Label(self.combined_status_bar_frame, text="GPU Util: N/A", style="GPUStatus.TLabel"); self.gpu_util_label.pack(side=tk.RIGHT, padx=(2,5), pady=2, fill=tk.Y)
        self.gpu_mem_label = ttk.Label(self.combined_status_bar_frame, text="GPU Mem: N/A", style="GPUStatus.TLabel"); self.gpu_mem_label.pack(side=tk.RIGHT, padx=2, pady=2, fill=tk.Y)
        self.app_status_label = ttk.Label(self.combined_status_bar_frame, text="Initializing...", style="AppStatus.TLabel", relief=tk.FLAT); self.app_status_label.pack(side=tk.LEFT, padx=5, pady=2, fill=tk.BOTH, expand=True)

        speak_button_frame = ttk.Frame(main_frame)
        speak_button_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5, 5))
        self.speak_button = ttk.Button(speak_button_frame, text="Loading...", command=self.action_callbacks['toggle_speaking_recording'], state=tk.DISABLED)
        self.speak_button.pack(ipady=10, ipadx=20)

        user_info_frame_height = 350
        user_info_frame = ttk.Frame(main_frame, height=user_info_frame_height)
        user_info_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5, 5))
        user_info_frame.pack_propagate(False)
        user_info_frame.columnconfigure(0, weight=3)
        user_info_frame.columnconfigure(1, weight=2)
        user_info_frame.rowconfigure(0, weight=1)

        font_scrolled_text = ('Helvetica', 9)
        scrolled_text_common_options = {
            "wrap": tk.WORD, "height": 9, "state": tk.DISABLED, "font": font_scrolled_text,
            "background": colors["entry_bg"], "foreground": colors["entry_fg"],
            "insertbackground": colors["entry_insert_bg"], # Cursor color
            "selectbackground": colors["entry_select_bg"],
            "selectforeground": colors["entry_select_fg"],
            "borderwidth": 1, "relief": tk.SUNKEN # Added border for ScrolledText
        }

        calendar_labelframe = ttk.LabelFrame(user_info_frame, text="Calendar Events")
        calendar_labelframe.grid(row=0, column=0, sticky="nsew", padx=(0, 5), pady=2)
        self.calendar_events_display = scrolledtext.ScrolledText(calendar_labelframe, **scrolled_text_common_options)
        self.calendar_events_display.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0,5))
        self.scrolled_text_widgets.append(self.calendar_events_display)

        todos_labelframe = ttk.LabelFrame(user_info_frame, text="Pending Todos")
        todos_labelframe.grid(row=0, column=1, sticky="nsew", padx=(5, 0), pady=2)
        self.todo_list_display = scrolledtext.ScrolledText(todos_labelframe, **scrolled_text_common_options)
        self.todo_list_display.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0,5))
        self.scrolled_text_widgets.append(self.todo_list_display)

        chat_history_font = ('Helvetica', 10)
        self.chat_history_display = scrolledtext.ScrolledText(main_frame, wrap=tk.WORD, state=tk.DISABLED,
                                                              font=chat_history_font,
                                                              background=colors["entry_bg"], foreground=colors["entry_fg"],
                                                              insertbackground=colors["entry_insert_bg"],
                                                              selectbackground=colors["entry_select_bg"],
                                                              selectforeground=colors["entry_select_fg"],
                                                              borderwidth=1, relief=tk.SUNKEN) # Added border
        self.chat_history_display.pack(pady=(0, 10), fill=tk.BOTH, expand=True)
        self.scrolled_text_widgets.append(self.chat_history_display)

        logger.debug("GUI widgets setup finished.")

    def _configure_tags_for_chat_display(self):
        if not self.chat_history_display:
            logger.warning("Chat history display not available for tag configuration.")
            return
        logger.debug("Configuring tags for chat display based on current theme.")
        colors = self.get_current_theme_colors()
        self.chat_history_display.tag_configure("user_tag", foreground=colors["user_msg_fg"])
        self.chat_history_display.tag_configure("assistant_tag", foreground=colors["assistant_msg_fg"])
        self.chat_history_display.tag_configure("assistant_tag_error", foreground=colors["assistant_error_fg"])

    # --- ADDED MISSING METHOD DEFINITION ---
    def _setup_protocol_handlers(self):
        if self.app_window and 'on_exit' in self.action_callbacks:
            logger.debug("Setting up WM_DELETE_WINDOW protocol handler.")
            self.app_window.protocol("WM_DELETE_WINDOW", self.action_callbacks['on_exit'])
        else:
            logger.warning("App window or on_exit callback not available for protocol handler setup.")
    # --- END OF ADDED METHOD ---

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

        colors = self.get_current_theme_colors() # Get current theme colors
        # The tk.Frame (widget_frame) background is set during theme application or initial setup.
        # widget_frame.configure(background=colors.get("component_status_label_default_bg", colors["frame_bg"]))

        label_bg = colors.get("component_status_label_default_bg", colors["frame_bg"]) # Default for the label inside the frame
        label_fg = colors.get("component_status_label_default_fg", colors["fg"])

        if status_category == "ready": label_bg = "#90EE90"; label_fg = "dark green"
        elif status_category in ["loaded", "saved", "fresh"]: label_bg = "#ADD8E6"; label_fg = "navy"
        elif status_category in ["loading", "checking", "pinging", "thinking"]: label_bg = "#FFFFE0"; label_fg = "darkgoldenrod"
        elif status_category in ["error", "na", "timeout", "conn_error", "http_502", "http_other", "InitFail"]: label_bg = "#FFA07A"; label_fg = "darkred"
        
        widget_label.config(text=text_to_display, background=label_bg, foreground=label_fg)


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

            colors = self.get_current_theme_colors()
            default_fg = colors["label_fg"]
            error_fg = colors.get("assistant_error_fg", "red") # Use themed error color

            fg_color = default_fg
            if status_category == "ok_gpu": fg_color = default_fg
            elif status_category == "na_nvml": fg_color = colors.get("button_disabled_fg", "silver")
            elif status_category in ["error", "error_nvml_loop", "InitFail"]: fg_color = error_fg
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