# gui_manager.py
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, font as tkfont
from datetime import datetime, date 
import config

import logging
logger = logging.getLogger("Iri-shka_App.GUIManager")

try:
    from tkcalendar import Calendar
    TKCALENDAR_AVAILABLE = True
    logger.info("tkcalendar imported successfully.")
except ImportError:
    TKCALENDAR_AVAILABLE = False
    logger.warning("tkcalendar library not found. Calendar widget will not be available.")
    logger.warning("Install it using: pip install tkcalendar")


class GUIManager:
    def __init__(self, root_tk_instance, action_callbacks, initial_theme=config.GUI_THEME_LIGHT, initial_font_size=config.DEFAULT_CHAT_FONT_SIZE):
        self.app_window = root_tk_instance
        self.action_callbacks = action_callbacks
        self.current_theme = initial_theme
        self.current_chat_font_size = initial_font_size
        logger.info(f"GUIManager initialized with theme: {self.current_theme}, font size: {self.current_chat_font_size}.")

        self.speak_button = None
        self.chat_history_display = None
        
        # User Info Area & Kanban Widgets
        self.kanban_pending_display = None
        self.kanban_in_process_display = None
        self.kanban_finished_display = None
        self.calendar_widget = None 
        self.calendar_events_display = None 
        self.todo_list_display = None 
        self.all_calendar_events_data = [] 
        self.selected_calendar_date = date.today() 

        # Status Bar Widgets (same as before)
        self.act_status_frame = None; self.act_status_text_label = None
        # ... (other status bar widget declarations remain the same) ...
        self.inet_status_frame = None; self.inet_status_text_label = None
        self.webui_status_frame = None; self.webui_status_text_label = None
        self.tele_status_frame = None; self.tele_status_text_label = None
        self.memory_status_frame = None; self.memory_status_text_label = None
        self.hearing_status_frame = None; self.hearing_status_text_label = None
        self.voice_status_frame = None; self.voice_status_text_label = None
        self.mind_status_frame = None; self.mind_status_text_label = None
        self.combined_status_bar_frame = None
        self.app_status_label = None
        self.gpu_mem_label = None
        self.gpu_util_label = None


        self.scrolled_text_widgets = []
        self.dark_theme_colors = {
            "bg": "#2B2B2B", "fg": "#D3D3D3", "frame_bg": "#3C3F41", "label_fg": "#E0E0E0",
            "button_bg": "#555555", "button_fg": "#FFFFFF", "button_active_bg": "#6A6A6A",
            "button_disabled_fg": "#888888", "entry_bg": "#333333", "entry_fg": "#D3D3D3",
            "entry_insert_bg": "#FFFFFF", "entry_select_bg": "#0078D7", "entry_select_fg": "#FFFFFF",
            "user_msg_fg": "sky blue", "assistant_msg_fg": "light green", "assistant_error_fg": "salmon",
            "component_status_label_default_bg": "#4A4A4A", "component_status_label_default_fg": "#CCCCCC",
            "calendar_event_mark_bg": "sky blue",
        }
        self.light_theme_colors = {
            "bg": "SystemButtonFace", "fg": "SystemWindowText", "frame_bg": "SystemButtonFace",
            "label_fg": "SystemWindowText", "button_bg": "SystemButtonFace", "button_fg": "SystemButtonText",
            "button_active_bg": "SystemHighlight", "button_disabled_fg": "SystemGrayText",
            "entry_bg": "white", "entry_fg": "black", "entry_insert_bg": "black",
            "entry_select_bg": "SystemHighlight",
            "entry_select_fg": "SystemHighlightText",
            "user_msg_fg": "blue", "assistant_msg_fg": "green", "assistant_error_fg": "orange red",
            "component_status_label_default_bg": "SystemButtonFace", "component_status_label_default_fg": "SystemWindowText",
            "calendar_event_mark_bg": "light sky blue",
        }

        self.apply_theme(self.current_theme, initial_setup=True)
        self._setup_widgets()
        self._configure_tags_for_chat_display()
        self._setup_protocol_handlers()
        logger.debug("GUIManager setup complete.")

    def get_current_theme_colors(self):
        return self.dark_theme_colors if self.current_theme == config.GUI_THEME_DARK else self.light_theme_colors

    def apply_theme(self, theme_name_to_apply, initial_setup=False):
        # ... (theme application logic, including calendar theming, remains mostly the same) ...
        # Ensure calendar_widget theming is robust to its absence
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
            style.configure("Speak.TButton", padding=(10,5), font=('Helvetica', 11, 'bold'), 
                            background=colors["button_bg"], foreground=colors["button_fg"],
                            relief=tk.RAISED, borderwidth=1, bordercolor="#777777")
            style.map("Speak.TButton",
                      background=[('active', colors["button_active_bg"]), ('disabled', colors["button_bg"])],
                      foreground=[('disabled', colors["button_disabled_fg"])],
                      relief=[('pressed', tk.SUNKEN), ('!pressed', tk.RAISED)],
                      bordercolor=[('focus', '#0078D4'), ('!focus', "#777777")])
            style.configure("TButton", padding=6, font=('Helvetica', 10),
                            background=colors["button_bg"], foreground=colors["button_fg"],
                            relief=tk.FLAT, borderwidth=1, bordercolor="#777777")
            style.map("TButton",
                      background=[('active', colors["button_active_bg"]), ('disabled', colors["button_bg"])],
                      foreground=[('disabled', colors["button_disabled_fg"])],
                      relief=[('pressed', tk.SUNKEN), ('!pressed', tk.FLAT)],
                      bordercolor=[('focus', '#0078D4'), ('!focus', "#777777")])

            style.configure("TLabelFrame", background=colors["frame_bg"], bordercolor="#777777", relief=tk.GROOVE)
            style.configure("TLabelFrame.Label", background=colors["frame_bg"], foreground=colors["label_fg"],
                            font=('Helvetica', 9, 'bold'))

            style.configure("AppStatus.TLabel", padding=(6,3), font=('Helvetica', 10), anchor="w",
                            background=colors["frame_bg"], foreground=colors["label_fg"])
            style.configure("GPUStatus.TLabel", font=('Consolas', 9), padding=(3,3),
                            background=colors["frame_bg"], foreground=colors["label_fg"], anchor="w")
            style.configure("ComponentStatus.TLabel", font=('Consolas', 9, 'bold'), anchor="center")
            if TKCALENDAR_AVAILABLE and self.calendar_widget:
                self.calendar_widget.configure(
                    background=colors["frame_bg"], foreground=colors["fg"],
                    bordercolor=colors["frame_bg"], 
                    headersbackground=colors["frame_bg"], headersforeground=colors["fg"],
                    selectbackground=colors["button_active_bg"], selectforeground=colors["button_fg"],
                    normalbackground=colors["entry_bg"], normalforeground=colors["fg"],
                    othermonthbackground=colors["entry_bg"], othermonthforeground=colors["button_disabled_fg"],
                    othermonthwebackground=colors["entry_bg"], othermonthweforeground=colors["button_disabled_fg"],
                    weekendbackground=colors["entry_bg"], weekendforeground=colors["fg"]
                )

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
            
            style.configure("Speak.TButton", padding=(10,5), font=('Helvetica', 11, 'bold'))
            style.configure("TButton", padding=6, font=('Helvetica', 10))
            try:
                style.configure("TLabelFrame.Label", font=('Helvetica', 9, 'bold'))
            except tk.TclError: pass
            style.configure("AppStatus.TLabel", padding=(6,3), font=('Helvetica', 10), anchor="w")
            style.configure("GPUStatus.TLabel", font=('Consolas', 9), padding=(3,3), anchor="w")
            style.configure("ComponentStatus.TLabel", font=('Consolas', 9, 'bold'), anchor="center")
            if TKCALENDAR_AVAILABLE and self.calendar_widget:
                self.calendar_widget.configure(
                    background=colors["frame_bg"], foreground=colors["fg"],
                    bordercolor=colors["frame_bg"], 
                    headersbackground=colors["frame_bg"], headersforeground=colors["fg"],
                    selectbackground=colors["button_active_bg"], selectforeground=colors["button_fg"],
                    normalbackground=colors["entry_bg"], normalforeground=colors["fg"],
                    othermonthbackground=colors["entry_bg"], othermonthforeground="gray",
                    othermonthwebackground=colors["entry_bg"], othermonthweforeground="gray",
                    weekendbackground=colors["entry_bg"], weekendforeground=colors["fg"]
                )

        if not initial_setup and self.app_window:
            self._reconfigure_standard_tk_widgets()
            self._configure_tags_for_chat_display()
            self.apply_chat_font_size(self.current_chat_font_size)
            if TKCALENDAR_AVAILABLE and self.calendar_widget and self.all_calendar_events_data:
                 self._mark_dates_with_events_on_calendar(self.all_calendar_events_data)
            logger.debug("Theme changed dynamically. Non-ttk widgets, tags, and font reconfigured.")

    def _reconfigure_standard_tk_widgets(self):
        # ... (same as before) ...
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
            self.act_status_frame, self.inet_status_frame,
            self.webui_status_frame, self.tele_status_frame,
            self.memory_status_frame, self.hearing_status_frame,
            self.voice_status_frame, self.mind_status_frame
        ]
        if self.app_window and isinstance(self.app_window, tk.Tk):
             self.app_window.configure(background=colors["bg"])
        for frame in tk_frames_to_theme:
            if frame and frame.winfo_exists():
                frame.configure(background=colors.get("component_status_label_default_bg", colors["frame_bg"]))

    def apply_chat_font_size(self, new_size):
        # ... (same as before) ...
        if not isinstance(new_size, int) or not (config.MIN_CHAT_FONT_SIZE <= new_size <= config.MAX_CHAT_FONT_SIZE):
            logger.warning(f"Invalid font size requested: {new_size}. Clamping to default or bounds.")
            try:
                new_size = int(new_size)
                new_size = max(config.MIN_CHAT_FONT_SIZE, min(new_size, config.MAX_CHAT_FONT_SIZE))
            except (ValueError, TypeError):
                 new_size = config.DEFAULT_CHAT_FONT_SIZE
            if not (config.MIN_CHAT_FONT_SIZE <= new_size <= config.MAX_CHAT_FONT_SIZE):
                new_size = config.DEFAULT_CHAT_FONT_SIZE

        self.current_chat_font_size = new_size
        logger.info(f"Applying chat font size: {self.current_chat_font_size}")
        chat_font_family = 'Helvetica'
        chat_font = tkfont.Font(family=chat_font_family, size=self.current_chat_font_size)
        
        side_panel_font_size = max(config.MIN_CHAT_FONT_SIZE -1 , self.current_chat_font_size - 2)
        side_panel_font = tkfont.Font(family=chat_font_family, size=side_panel_font_size)

        if self.chat_history_display and self.chat_history_display.winfo_exists():
            try: self.chat_history_display.configure(font=chat_font)
            except Exception as e: logger.error(f"Error applying font size to chat_history_display: {e}")
        
        # Apply to new Kanban and existing user info lists
        text_widgets_for_side_font = [
            self.kanban_pending_display, self.kanban_in_process_display, self.kanban_finished_display,
            self.calendar_events_display, self.todo_list_display
        ]
        for widget in text_widgets_for_side_font:
            if widget and widget.winfo_exists():
                try: widget.configure(font=side_panel_font)
                except Exception as e: logger.error(f"Error applying font size to side panel widget: {e}")


    def _setup_widgets(self):
        logger.debug("Setting up GUI widgets.")
        self.app_window.title("Iri-shka: Voice AI Assistant")
        self.app_window.geometry("1150x900") # Adjusted window size for more vertical space

        main_frame = ttk.Frame(self.app_window, padding="10")
        main_frame.pack(expand=True, fill=tk.BOTH)

        # --- Status Bar (Packed at the very bottom of main_frame) ---
        self.combined_status_bar_frame = ttk.Frame(main_frame, height=70, relief=tk.GROOVE, borderwidth=1)
        self.combined_status_bar_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5, 0))
        self.combined_status_bar_frame.pack_propagate(False)
        # ... (status bar content setup as in previous response, no changes here) ...
        colors = self.get_current_theme_colors() 
        component_box_width = 95; component_box_height = 28
        component_frame_bg = colors.get("component_status_label_default_bg", colors["frame_bg"])
        left_status_panel_frame = ttk.Frame(self.combined_status_bar_frame)
        left_status_panel_frame.pack(side=tk.LEFT, padx=(2,5), pady=2, fill=tk.Y)
        status_row1_frame = ttk.Frame(left_status_panel_frame); status_row1_frame.pack(side=tk.TOP, fill=tk.X)
        status_row2_frame = ttk.Frame(left_status_panel_frame); status_row2_frame.pack(side=tk.TOP, fill=tk.X, pady=(2,0))
        self.act_status_frame = tk.Frame(status_row1_frame, width=component_box_width, height=component_box_height, relief=tk.SUNKEN, borderwidth=1, background=component_frame_bg); self.act_status_frame.pack(side=tk.LEFT, padx=(0,2), pady=0); self.act_status_frame.pack_propagate(False)
        self.act_status_text_label = ttk.Label(self.act_status_frame, text="ACT: IDLE", style="ComponentStatus.TLabel"); self.act_status_text_label.pack(expand=True, fill=tk.BOTH)
        self.inet_status_frame = tk.Frame(status_row1_frame, width=component_box_width, height=component_box_height, relief=tk.SUNKEN, borderwidth=1, background=component_frame_bg); self.inet_status_frame.pack(side=tk.LEFT, padx=2, pady=0); self.inet_status_frame.pack_propagate(False)
        self.inet_status_text_label = ttk.Label(self.inet_status_frame, text="INET: CHK", style="ComponentStatus.TLabel"); self.inet_status_text_label.pack(expand=True, fill=tk.BOTH)
        self.webui_status_frame = tk.Frame(status_row1_frame, width=component_box_width, height=component_box_height, relief=tk.SUNKEN, borderwidth=1, background=component_frame_bg); self.webui_status_frame.pack(side=tk.LEFT, padx=2, pady=0); self.webui_status_frame.pack_propagate(False)
        self.webui_status_text_label = ttk.Label(self.webui_status_frame, text="WEBUI: OFF", style="ComponentStatus.TLabel"); self.webui_status_text_label.pack(expand=True, fill=tk.BOTH)
        self.tele_status_frame = tk.Frame(status_row1_frame, width=component_box_width, height=component_box_height, relief=tk.SUNKEN, borderwidth=1, background=component_frame_bg); self.tele_status_frame.pack(side=tk.LEFT, padx=(2,0), pady=0); self.tele_status_frame.pack_propagate(False)
        self.tele_status_text_label = ttk.Label(self.tele_status_frame, text="TELE: OFF", style="ComponentStatus.TLabel"); self.tele_status_text_label.pack(expand=True, fill=tk.BOTH)
        self.memory_status_frame = tk.Frame(status_row2_frame, width=component_box_width, height=component_box_height, relief=tk.SUNKEN, borderwidth=1, background=component_frame_bg); self.memory_status_frame.pack(side=tk.LEFT, padx=(0,2), pady=0); self.memory_status_frame.pack_propagate(False)
        self.memory_status_text_label = ttk.Label(self.memory_status_frame, text="MEM: CHK", style="ComponentStatus.TLabel"); self.memory_status_text_label.pack(expand=True, fill=tk.BOTH)
        self.hearing_status_frame = tk.Frame(status_row2_frame, width=component_box_width, height=component_box_height, relief=tk.SUNKEN, borderwidth=1, background=component_frame_bg); self.hearing_status_frame.pack(side=tk.LEFT, padx=2, pady=0); self.hearing_status_frame.pack_propagate(False)
        self.hearing_status_text_label = ttk.Label(self.hearing_status_frame, text="HEAR: CHK", style="ComponentStatus.TLabel"); self.hearing_status_text_label.pack(expand=True, fill=tk.BOTH)
        self.voice_status_frame = tk.Frame(status_row2_frame, width=component_box_width, height=component_box_height, relief=tk.SUNKEN, borderwidth=1, background=component_frame_bg); self.voice_status_frame.pack(side=tk.LEFT, padx=2, pady=0); self.voice_status_frame.pack_propagate(False)
        self.voice_status_text_label = ttk.Label(self.voice_status_frame, text="VOICE: CHK", style="ComponentStatus.TLabel"); self.voice_status_text_label.pack(expand=True, fill=tk.BOTH)
        self.mind_status_frame = tk.Frame(status_row2_frame, width=component_box_width, height=component_box_height, relief=tk.SUNKEN, borderwidth=1, background=component_frame_bg); self.mind_status_frame.pack(side=tk.LEFT, padx=(2,0), pady=0); self.mind_status_frame.pack_propagate(False)
        self.mind_status_text_label = ttk.Label(self.mind_status_frame, text="MIND: CHK", style="ComponentStatus.TLabel"); self.mind_status_text_label.pack(expand=True, fill=tk.BOTH)
        right_info_panel_frame = ttk.Frame(self.combined_status_bar_frame); right_info_panel_frame.pack(side=tk.LEFT, padx=(10,2), pady=0, fill=tk.BOTH, expand=True)
        self.speak_button = ttk.Button(right_info_panel_frame, text="Loading...", command=self.action_callbacks['toggle_speaking_recording'], state=tk.DISABLED, style="Speak.TButton"); self.speak_button.pack(side=tk.RIGHT, fill=tk.Y, padx=(5,2), pady=2)
        left_detail_frame = ttk.Frame(right_info_panel_frame); left_detail_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=2, padx=(0,5))
        self.app_status_label = ttk.Label(left_detail_frame, text="Initializing...", style="AppStatus.TLabel", relief=tk.FLAT); self.app_status_label.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(0,2))
        gpu_stack_frame = ttk.Frame(left_detail_frame); gpu_stack_frame.pack(side=tk.BOTTOM, fill=tk.X)
        self.gpu_mem_label = ttk.Label(gpu_stack_frame, text="GPU Mem: N/A", style="GPUStatus.TLabel"); self.gpu_mem_label.pack(side=tk.LEFT, padx=(0,5))
        self.gpu_util_label = ttk.Label(gpu_stack_frame, text="GPU Util: N/A", style="GPUStatus.TLabel"); self.gpu_util_label.pack(side=tk.LEFT)


        # --- User Info & Kanban Frame (Packed ABOVE status bar) ---
        user_info_overall_height = 420 # Increased height for Kanban + taller user info row
        user_info_frame = ttk.Frame(main_frame, height=user_info_overall_height)
        user_info_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5, 5)) 
        user_info_frame.pack_propagate(False)
        
        # Configure 3 columns with equal weight
        user_info_frame.columnconfigure(0, weight=1) 
        user_info_frame.columnconfigure(1, weight=1)
        user_info_frame.columnconfigure(2, weight=1)
        # Configure 2 rows, give more weight to the bottom user info row
        user_info_frame.rowconfigure(0, weight=1) # Kanban row
        user_info_frame.rowconfigure(1, weight=3) # User info row (Calendar, Events, Todos)


        # --- Kanban Row (Row 0) ---
        kanban_font_size = max(config.MIN_CHAT_FONT_SIZE -2 , self.current_chat_font_size - 3)
        kanban_font = tkfont.Font(family='Helvetica', size=kanban_font_size)
        
        kanban_scrolled_text_options = {
            "wrap": tk.WORD, "height": 4, "state": tk.DISABLED, # Shorter height for Kanban
            "background": colors["entry_bg"], "foreground": colors["entry_fg"],
            "insertbackground": colors["entry_insert_bg"],
            "selectbackground": colors["entry_select_bg"],
            "selectforeground": colors["entry_select_fg"],
            "borderwidth": 1, "relief": tk.SUNKEN,
            "font": kanban_font
        }

        kanban_pending_labelframe = ttk.LabelFrame(user_info_frame, text="Pending")
        kanban_pending_labelframe.grid(row=0, column=0, sticky="nsew", padx=(0,2), pady=(0,5))
        self.kanban_pending_display = scrolledtext.ScrolledText(kanban_pending_labelframe, **kanban_scrolled_text_options)
        self.kanban_pending_display.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.scrolled_text_widgets.append(self.kanban_pending_display)
        # Placeholder content REMOVED


        kanban_in_process_labelframe = ttk.LabelFrame(user_info_frame, text="In Process")
        kanban_in_process_labelframe.grid(row=0, column=1, sticky="nsew", padx=2, pady=(0,5))
        self.kanban_in_process_display = scrolledtext.ScrolledText(kanban_in_process_labelframe, **kanban_scrolled_text_options)
        self.kanban_in_process_display.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.scrolled_text_widgets.append(self.kanban_in_process_display)
        # Placeholder content REMOVED

        kanban_finished_labelframe = ttk.LabelFrame(user_info_frame, text="Finished")
        kanban_finished_labelframe.grid(row=0, column=2, sticky="nsew", padx=(2,0), pady=(0,5))
        self.kanban_finished_display = scrolledtext.ScrolledText(kanban_finished_labelframe, **kanban_scrolled_text_options)
        self.kanban_finished_display.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.scrolled_text_widgets.append(self.kanban_finished_display)
        # Placeholder content REMOVED


        # --- User Info Row (Row 1 - Calendar, Events, Todos) ---
        calendar_outer_labelframe = ttk.LabelFrame(user_info_frame, text="Calendar")
        calendar_outer_labelframe.grid(row=1, column=0, sticky="nsew", padx=(0, 2), pady=(5,0))
        if TKCALENDAR_AVAILABLE:
            current_colors_for_cal = self.get_current_theme_colors()
            self.calendar_widget = Calendar(
                calendar_outer_labelframe, selectmode='day', date_pattern='y-mm-dd',
                year=self.selected_calendar_date.year, month=self.selected_calendar_date.month, day=self.selected_calendar_date.day,
                background=current_colors_for_cal.get("frame_bg", "SystemButtonFace"), 
                foreground=current_colors_for_cal.get("fg", "SystemWindowText"),
                bordercolor=current_colors_for_cal.get("frame_bg", "SystemButtonFace"),
                headersbackground=current_colors_for_cal.get("frame_bg", "SystemButtonFace"),
                headersforeground=current_colors_for_cal.get("fg", "SystemWindowText"),
                selectbackground=current_colors_for_cal.get("button_active_bg", "SystemHighlight"),
                selectforeground=current_colors_for_cal.get("button_fg", "SystemHighlightText"),
                normalbackground=current_colors_for_cal.get("entry_bg", "white"),
                normalforeground=current_colors_for_cal.get("fg", "black"),
                weekendbackground=current_colors_for_cal.get("entry_bg", "white"),
                weekendforeground=current_colors_for_cal.get("fg", "black"),
                othermonthbackground=current_colors_for_cal.get("entry_bg", "white"),
                othermonthforeground='gray',
                othermonthwebackground=current_colors_for_cal.get("entry_bg", "white"),
                othermonthweforeground='gray',
                font=('Helvetica', 9), showweeknumbers=False,
                #relief=tk.FLAT, borderwidth=0 # Attempt to make calendar flatter
            )
            self.calendar_widget.pack(fill=tk.BOTH, expand=True, padx=2, pady=2) # Minimal padding inside its frame
            self.calendar_widget.bind("<<CalendarSelected>>", self._on_date_selected)
        else:
            no_cal_label = ttk.Label(calendar_outer_labelframe, text="tkcalendar not found.\nCalendar view disabled.", justify=tk.CENTER, anchor=tk.CENTER)
            no_cal_label.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        chat_font_family = 'Helvetica'
        side_panel_font_size = max(config.MIN_CHAT_FONT_SIZE -1 , self.current_chat_font_size - 2)
        side_panel_font = tkfont.Font(family=chat_font_family, size=side_panel_font_size)

        # Increased height for user info lists
        user_info_list_height = 10 # Approx 30% more than previous 7
        
        user_info_scrolled_text_options = {
            "wrap": tk.WORD, "height": user_info_list_height, "state": tk.DISABLED,
            "background": colors["entry_bg"], "foreground": colors["entry_fg"],
            "insertbackground": colors["entry_insert_bg"],
            "selectbackground": colors["entry_select_bg"],
            "selectforeground": colors["entry_select_fg"],
            "borderwidth": 1, "relief": tk.SUNKEN,
            "font": side_panel_font
        }

        calendar_events_labelframe = ttk.LabelFrame(user_info_frame, text="Selected Day's Events")
        calendar_events_labelframe.grid(row=1, column=1, sticky="nsew", padx=2, pady=(5,0))
        self.calendar_events_display = scrolledtext.ScrolledText(calendar_events_labelframe, **user_info_scrolled_text_options)
        self.calendar_events_display.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0,5))
        self.scrolled_text_widgets.append(self.calendar_events_display)

        todos_labelframe = ttk.LabelFrame(user_info_frame, text="Pending Todos")
        todos_labelframe.grid(row=1, column=2, sticky="nsew", padx=(2,0), pady=(5,0))
        self.todo_list_display = scrolledtext.ScrolledText(todos_labelframe, **user_info_scrolled_text_options)
        self.todo_list_display.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0,5))
        self.scrolled_text_widgets.append(self.todo_list_display)

        # --- Chat History (Main central widget - packed ABOVE user_info_frame) ---
        chat_display_font = tkfont.Font(family=chat_font_family, size=self.current_chat_font_size)
        chat_scrolled_text_options = user_info_scrolled_text_options.copy() # Start from user info options
        chat_scrolled_text_options["font"] = chat_display_font 
        chat_scrolled_text_options["height"] = 15 
        
        self.chat_history_display = scrolledtext.ScrolledText(main_frame, **chat_scrolled_text_options)
        self.chat_history_display.pack(pady=(0, 10), fill=tk.BOTH, expand=True)
        self.scrolled_text_widgets.append(self.chat_history_display)

        logger.debug("GUI widgets setup finished.")
        if TKCALENDAR_AVAILABLE:
            self._on_date_selected()
    
    def _on_date_selected(self, event=None):
        if not TKCALENDAR_AVAILABLE or not self.calendar_widget:
            return
        try:
            new_selected_date_str = self.calendar_widget.get_date()
            self.selected_calendar_date = datetime.strptime(new_selected_date_str, '%Y-%m-%d').date()
            logger.info(f"Calendar date selected: {self.selected_calendar_date}")
        except Exception as e:
            logger.warning(f"Could not get or parse date from calendar: {e}. Using last known: {self.selected_calendar_date}")
        self._update_filtered_event_display()

    def _mark_dates_with_events_on_calendar(self, all_events):
        if not TKCALENDAR_AVAILABLE or not self.calendar_widget:
            return
        logger.debug(f"Marking dates with events on calendar. Total events: {len(all_events)}")
        self.calendar_widget.calevent_remove('all')
        event_dates = set()
        for event_item in all_events:
            if isinstance(event_item, dict) and "date" in event_item:
                try:
                    event_date_obj = datetime.strptime(event_item["date"], "%Y-%m-%d").date()
                    event_dates.add(event_date_obj)
                except ValueError:
                    logger.warning(f"Invalid date format in event item for calendar marking: {event_item}")
        colors = self.get_current_theme_colors()
        mark_bg_color = colors.get("calendar_event_mark_bg", "yellow")
        for dt in event_dates:
            try:
                self.calendar_widget.calevent_create(dt, text='', tags=['has_event'])
            except Exception as e:
                logger.error(f"Error marking date {dt} on calendar: {e}")
        self.calendar_widget.tag_config('has_event', background=mark_bg_color, foreground='black')
        logger.debug(f"Marked {len(event_dates)} unique dates on the calendar.")

    def _update_filtered_event_display(self):
        if not self.calendar_events_display:
            return
        logger.debug(f"Updating filtered event display for date: {self.selected_calendar_date}")
        self.calendar_events_display.config(state=tk.NORMAL)
        self.calendar_events_display.delete(1.0, tk.END)
        if not self.all_calendar_events_data:
            self.calendar_events_display.insert(tk.END, "No calendar events stored.")
        else:
            events_for_selected_date = []
            for ev in self.all_calendar_events_data:
                if isinstance(ev, dict) and "date" in ev:
                    try:
                        event_date_obj = datetime.strptime(ev["date"], "%Y-%m-%d").date()
                        if event_date_obj == self.selected_calendar_date:
                            events_for_selected_date.append(ev)
                    except ValueError:
                        pass
            if not events_for_selected_date:
                self.calendar_events_display.insert(tk.END, f"No events for {self.selected_calendar_date.strftime('%Y-%m-%d')}.")
            else:
                def _sort_key_time(e):
                    tm = e.get("time", "23:59")
                    try: return datetime.strptime(tm, "%H:%M").time()
                    except ValueError: return datetime.strptime("23:59", "%H:%M").time()
                sorted_day_events = sorted(events_for_selected_date, key=_sort_key_time)
                for ev in sorted_day_events:
                    tm, dsc = ev.get("time"), ev.get("description", ev.get("name", "Unnamed Event"))
                    event_text = f"{tm+': ' if tm else ''}{dsc}\n"
                    self.calendar_events_display.insert(tk.END, event_text)
        self.calendar_events_display.config(state=tk.DISABLED)
        self.calendar_events_display.see(tk.END)

    def _configure_tags_for_chat_display(self):
        if not self.chat_history_display:
            logger.warning("Chat history display not available for tag configuration.")
            return
        logger.debug("Configuring tags for chat display based on current theme.")
        colors = self.get_current_theme_colors()
        self.chat_history_display.tag_configure("user_tag", foreground=colors["user_msg_fg"])
        self.chat_history_display.tag_configure("assistant_tag", foreground=colors["assistant_msg_fg"])
        self.chat_history_display.tag_configure("assistant_tag_error", foreground=colors["assistant_error_fg"])

    def _handle_space_key_press(self, event=None):
        logger.debug("Space key released, calling toggle_speaking_recording callback.")
        if 'toggle_speaking_recording' in self.action_callbacks:
            self.action_callbacks['toggle_speaking_recording']()
        else:
            logger.warning("Space key released, but 'toggle_speaking_recording' callback is missing.")
        return "break"

    def _setup_protocol_handlers(self):
        if self.app_window and 'on_exit' in self.action_callbacks:
            logger.debug("Setting up WM_DELETE_WINDOW protocol handler.")
            self.app_window.protocol("WM_DELETE_WINDOW", self.action_callbacks['on_exit'])
        else:
            logger.warning("App window or on_exit callback not available for protocol handler setup.")
        if self.app_window:
            self.app_window.bind("<KeyRelease-space>", self._handle_space_key_press)
            logger.info("Bound <KeyRelease-space> to toggle speaking/recording.")

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
        colors = self.get_current_theme_colors()
        label_bg = colors.get("component_status_label_default_bg", colors["frame_bg"])
        label_fg = colors.get("component_status_label_default_fg", colors["fg"])
        
        if status_category == "ready": label_bg = "#90EE90"; label_fg = "dark green" # Green
        elif status_category in ["loaded", "saved", "fresh", "idle", "off"]: label_bg = "#ADD8E6"; label_fg = "navy" # Blue
        elif status_category in ["loading", "checking", "pinging", "thinking"]: label_bg = "#FFFFE0"; label_fg = "darkgoldenrod" # Yellow
        elif status_category in ["error", "na", "timeout", "conn_error", "http_502", "http_other", "InitFail", "unreachable"]: label_bg = "#FFA07A"; label_fg = "darkred" # Red/Orange
        
        widget_label.config(text=text_to_display, background=label_bg, foreground=label_fg)

    def update_act_status(self, short_text, status_type_str):
        logger.debug(f"Updating ACT status: Text='{short_text}', Type='{status_type_str}'")
        self._safe_ui_update(lambda: self._update_component_status_widget_internal(
            self.act_status_frame, self.act_status_text_label, short_text, status_type_str
        ))

    def update_inet_status(self, short_text, status_type_str):
        logger.debug(f"Updating INET status: Text='{short_text}', Type='{status_type_str}'")
        self._safe_ui_update(lambda: self._update_component_status_widget_internal(
            self.inet_status_frame, self.inet_status_text_label, short_text, status_type_str
        ))

    def update_webui_status(self, short_text, status_type_str):
        logger.debug(f"Updating WEBUI status: Text='{short_text}', Type='{status_type_str}'")
        self._safe_ui_update(lambda: self._update_component_status_widget_internal(
            self.webui_status_frame, self.webui_status_text_label, short_text, status_type_str
        ))

    def update_tele_status(self, short_text, status_type_str):
        logger.debug(f"Updating TELE status: Text='{short_text}', Type='{status_type_str}'")
        self._safe_ui_update(lambda: self._update_component_status_widget_internal(
            self.tele_status_frame, self.tele_status_text_label, short_text, status_type_str
        ))

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
            if not self.gpu_mem_label or not self.gpu_util_label: return
            self.gpu_mem_label.config(text=f"GPU Mem: {mem_text}")
            self.gpu_util_label.config(text=f"GPU Util: {util_text}")
            colors = self.get_current_theme_colors()
            default_fg = colors["label_fg"]
            error_fg = colors.get("assistant_error_fg", "red")
            fg_color = default_fg
            if status_category == "ok_gpu": fg_color = default_fg
            elif status_category == "na_nvml": fg_color = colors.get("button_disabled_fg", "silver")
            elif status_category in ["error", "error_nvml_loop", "InitFail"]: fg_color = error_fg
            if status_category == "checking": fg_color = "orange"
            self.gpu_mem_label.config(foreground=fg_color)
            self.gpu_util_label.config(foreground=fg_color)
        self._safe_ui_update(_update)

    def _add_message_to_display_internal(self, message_with_prefix, tag, is_error=False):
        if not self.chat_history_display: return
        actual_tag = "assistant_tag_error" if is_error and tag == "assistant_tag" else tag
        self.chat_history_display.config(state=tk.NORMAL)
        self.chat_history_display.insert(tk.END, message_with_prefix, actual_tag)
        self.chat_history_display.see(tk.END)
        self.chat_history_display.config(state=tk.DISABLED)

    def add_user_message_to_display(self, text):
        logger.info(f"Displaying User message: '{text[:70]}...'")
        self._safe_ui_update(lambda: self._add_message_to_display_internal(f"You: {text}\n", "user_tag"))

    def add_assistant_message_to_display(self, text, is_error=False):
        logger.info(f"Displaying Assistant message (Error={is_error}): '{text[:70]}...'")
        text = text + "\n" if text.endswith("\n") else text + "\n\n"
        if not text.endswith("\n\n"): text += "\n"
        self._safe_ui_update(lambda: self._add_message_to_display_internal(f"Iri-shka: {text}", "assistant_tag", is_error=is_error))

    def update_chat_display_from_list(self, chat_history_list):
        if not self.chat_history_display: return
        logger.info(f"Updating chat display from history list (length: {len(chat_history_list)}).")
        def _update():
            self.chat_history_display.config(state=tk.NORMAL)
            self.chat_history_display.delete(1.0, tk.END)
            for turn in chat_history_list:
                user_message, assistant_message = turn.get('user', ''), turn.get('assistant', '')
                if user_message: self._add_message_to_display_internal(f"You: {user_message}\n", "user_tag")
                if assistant_message:
                    is_error = assistant_message.startswith(("[Ollama Error:", "[LLM Error:", "[LLM Unreachable:")) or \
                               assistant_message in ("I didn't catch that, could you please repeat?", "Я не расслышала, не могли бы вы повторить?")
                    fmt_msg = assistant_message + ("\n" if assistant_message.endswith("\n") else "\n\n")
                    if not fmt_msg.endswith("\n\n"): fmt_msg += "\n"
                    self._add_message_to_display_internal(f"Iri-shka: {fmt_msg}", "assistant_tag", is_error=is_error)
            self.chat_history_display.see(tk.END)
            self.chat_history_display.config(state=tk.DISABLED)
        self._safe_ui_update(_update)

    def update_todo_list(self, todos):
        if not self.todo_list_display: return
        logger.info(f"Updating todo list display with {len(todos) if isinstance(todos, list) else 0} items.")
        def _update():
            self.todo_list_display.config(state=tk.NORMAL)
            self.todo_list_display.delete(1.0, tk.END)
            if not todos or not isinstance(todos, list):
                self.todo_list_display.insert(tk.END, "No todos." if not todos else "Invalid todo data.")
            else:
                for todo_item in todos: self.todo_list_display.insert(tk.END, f"- {str(todo_item)}\n")
            self.todo_list_display.config(state=tk.DISABLED); self.todo_list_display.see(tk.END)
        self._safe_ui_update(_update)

    def update_calendar_events_list(self, all_events_data):
        if not isinstance(all_events_data, list):
            logger.warning(f"Invalid data type for calendar events: {type(all_events_data)}. Expected list.")
            self.all_calendar_events_data = []
        else:
            self.all_calendar_events_data = sorted(
                [e for e in all_events_data if isinstance(e, dict)], 
                key=lambda e: (
                    datetime.strptime(e.get("date", "1900-01-01"), "%Y-%m-%d").date(),
                    datetime.strptime(e.get("time", "00:00"), "%H:%M").time() if e.get("time") else datetime.min.time()
                )
            )
            logger.info(f"Storing and sorting {len(self.all_calendar_events_data)} calendar events.")
        if TKCALENDAR_AVAILABLE and self.calendar_widget:
            self._mark_dates_with_events_on_calendar(self.all_calendar_events_data)
        self._update_filtered_event_display()

    def _update_kanban_column(self, display_widget, tasks_list, column_name_for_log):
        if not display_widget:
            logger.warning(f"Kanban display widget for '{column_name_for_log}' not available.")
            return
        
        logger.info(f"Updating Kanban '{column_name_for_log}' display with {len(tasks_list) if isinstance(tasks_list, list) else 0} items.")
        def _update():
            display_widget.config(state=tk.NORMAL)
            display_widget.delete(1.0, tk.END)
            if not tasks_list or not isinstance(tasks_list, list):
                display_widget.insert(tk.END, f"No {column_name_for_log.lower()} tasks." if not tasks_list else f"Invalid data for {column_name_for_log.lower()} tasks.")
            else:
                for task_item in tasks_list:
                    display_widget.insert(tk.END, f"- {str(task_item)}\n")
            display_widget.config(state=tk.DISABLED)
            display_widget.see(tk.END) # Scroll to the end
        self._safe_ui_update(_update)

    def update_kanban_pending(self, tasks_list):
        self._update_kanban_column(self.kanban_pending_display, tasks_list, "Pending")

    def update_kanban_in_process(self, tasks_list):
        self._update_kanban_column(self.kanban_in_process_display, tasks_list, "In Process")

    def update_kanban_completed(self, tasks_list):
        self._update_kanban_column(self.kanban_finished_display, tasks_list, "Completed")

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
            try: self.app_window.destroy(); logger.info("Tkinter window destroyed.")
            except tk.TclError as e: logger.warning(f"Tkinter error during destroy: {e}", exc_info=False)
            self.app_window = None
        else: logger.info("Destroy window called, but no app_window instance.")