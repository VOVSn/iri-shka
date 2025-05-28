# gui_manager.py
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, font as tkfont
from datetime import datetime, date 
import config 
import os 
import threading

# Assuming logger.py is in project root
from logger import get_logger
logger = get_logger("Iri-shka_App.GUIManager")


try:
    from tkcalendar import Calendar
    TKCALENDAR_AVAILABLE = True
    logger.info("tkcalendar imported successfully.")
except ImportError:
    TKCALENDAR_AVAILABLE = False
    logger.warning("tkcalendar library not found. Calendar widget will not be available.")
    logger.warning("Install it using: pip install tkcalendar")

PYSTRAY_AVAILABLE = False
pystray = None
Image = None
try:
    import pystray
    from PIL import Image
    PYSTRAY_AVAILABLE = True
    logger.info("pystray and Pillow imported successfully for system tray.")
except ImportError:
    PYSTRAY_AVAILABLE = False
    logger.warning("pystray or Pillow not found. System tray functionality will be disabled.")
    logger.warning("Install them using: pip install pystray pillow")

MODEL_STATUS_CHECK_AVAILABLE = False
TELEGRAM_STATUS_CHECK_AVAILABLE = False 
try:
    from utils import tts_manager, whisper_handler
    from utils import telegram_handler as app_telegram_handler_module # Alias for clarity

    MODEL_STATUS_CHECK_AVAILABLE = True
    TELEGRAM_STATUS_CHECK_AVAILABLE = True # Assume if module exists, we can check
    logger.info("tts_manager, whisper_handler, and telegram_handler imported for tray status.")
except ImportError as e:
    # Don't set both to False if only one fails, but log the specific issue
    if 'tts_manager' not in str(e) and 'whisper_handler' not in str(e):
        TELEGRAM_STATUS_CHECK_AVAILABLE = False # This logic was flawed, if tts_manager fails, model check is false.
    if 'telegram_handler' not in str(e):
        TELEGRAM_STATUS_CHECK_AVAILABLE = False
    # Corrected logic:
    if 'tts_manager' in str(e) or 'whisper_handler' in str(e):
        MODEL_STATUS_CHECK_AVAILABLE = False
    if 'telegram_handler' in str(e):
        TELEGRAM_STATUS_CHECK_AVAILABLE = False
    
    logger.warning(f"Could not import one or more modules (tts, whisper, telegram) for tray status: {e}")


class GUIManager:
    def __init__(self, root_tk_instance, action_callbacks, initial_theme=config.GUI_THEME_LIGHT, initial_font_size=config.DEFAULT_CHAT_FONT_SIZE):
        self.app_window = root_tk_instance
        self.action_callbacks = action_callbacks
        self.current_theme = initial_theme
        self.current_chat_font_size = initial_font_size
        logger.info(f"GUIManager initialized with theme: {self.current_theme}, font size: {self.current_chat_font_size}.")

        self.speak_button = None
        self.chat_history_display = None
        
        self.kanban_pending_display = None
        self.kanban_in_process_display = None
        self.kanban_finished_display = None
        self.calendar_widget = None 
        self.calendar_events_display = None 
        self.todo_list_display = None 
        self.all_calendar_events_data = [] 
        self.selected_calendar_date = date.today() 

        self.act_status_frame = None; self.act_status_text_label = None
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
        
        self.tray_icon = None
        self.tray_thread = None
        self.icon_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'icon.ico'))
        logger.info(f"Calculated icon path: {self.icon_path}")

        self.dark_theme_colors = {
            "bg": "#2B2B2B", "fg": "#D3D3D3", "frame_bg": "#3C3F41", "label_fg": "#E0E0E0",
            "button_bg": "#555555", "button_fg": "#FFFFFF", "button_active_bg": "#6A6A6A",
            "button_disabled_fg": "#888888", "entry_bg": "#333333", "entry_fg": "#D3D3D3",
            "entry_insert_bg": "#FFFFFF", 
            "entry_select_bg": "#0078D7", "entry_select_fg": "#FFFFFF", 
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
        self._configure_tags_for_chat_display() # Call this after widgets are set up
        self._setup_protocol_handlers()
        self._setup_tray_icon() 
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
            try: style.configure("TLabelFrame.Label", font=('Helvetica', 9, 'bold'))
            except tk.TclError: pass 
            style.configure("AppStatus.TLabel", padding=(6,3), font=('Helvetica', 10), anchor="w")
            style.configure("GPUStatus.TLabel", font=('Consolas', 9), padding=(3,3), anchor="w")
            style.configure("ComponentStatus.TLabel", font=('Consolas', 9, 'bold'), anchor="center")
            
            if TKCALENDAR_AVAILABLE and self.calendar_widget:
                self.calendar_widget.configure(
                    background=colors.get("frame_bg", "SystemButtonFace"), 
                    foreground=colors.get("fg", "SystemWindowText"),
                    bordercolor=colors.get("frame_bg", "SystemButtonFace"),
                    headersbackground=colors.get("frame_bg", "SystemButtonFace"),
                    headersforeground=colors.get("fg", "SystemWindowText"),
                    selectbackground=colors.get("button_active_bg", "SystemHighlight"),
                    selectforeground=colors.get("button_fg", "SystemHighlightText"), 
                    normalbackground=colors.get("entry_bg", "white"),
                    normalforeground=colors.get("fg", "black"), 
                    weekendbackground=colors.get("entry_bg", "white"),
                    weekendforeground=colors.get("fg", "black"), 
                    othermonthbackground=colors.get("entry_bg", "white"),
                    othermonthforeground='gray', 
                    othermonthwebackground=colors.get("entry_bg", "white"),
                    othermonthweforeground='gray' 
                )

        if not initial_setup and self.app_window:
            self._reconfigure_standard_tk_widgets()
            self._configure_tags_for_chat_display() 
            self.apply_chat_font_size(self.current_chat_font_size) 
            if TKCALENDAR_AVAILABLE and self.calendar_widget and self.all_calendar_events_data:
                 self._mark_dates_with_events_on_calendar(self.all_calendar_events_data) 
            logger.debug("Theme changed dynamically. Non-ttk widgets, tags, and font reconfigured.")
    
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
            self.act_status_frame, self.inet_status_frame,
            self.webui_status_frame, self.tele_status_frame,
            self.memory_status_frame, self.hearing_status_frame,
            self.voice_status_frame, self.mind_status_frame
        ]
        if self.app_window and isinstance(self.app_window, tk.Tk):
             self.app_window.configure(background=colors["bg"]) 
        
        component_frame_bg = colors.get("component_status_label_default_bg", colors["frame_bg"])
        for frame in tk_frames_to_theme:
            if frame and frame.winfo_exists():
                frame.configure(background=component_frame_bg)
                for child in frame.winfo_children():
                    if isinstance(child, ttk.Label):
                        pass


    def apply_chat_font_size(self, new_size):
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
        
        side_panel_font_size = max(config.MIN_CHAT_FONT_SIZE - 1 , self.current_chat_font_size - 2)
        side_panel_font = tkfont.Font(family=chat_font_family, size=side_panel_font_size)

        if self.chat_history_display and self.chat_history_display.winfo_exists():
            try: self.chat_history_display.configure(font=chat_font)
            except Exception as e: logger.error(f"Error applying font size to chat_history_display: {e}")
        
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
        self.app_window.geometry("1150x900") 

        main_frame = ttk.Frame(self.app_window, padding="10")
        main_frame.pack(expand=True, fill=tk.BOTH)

        self.combined_status_bar_frame = ttk.Frame(main_frame, height=70, relief=tk.GROOVE, borderwidth=1) 
        self.combined_status_bar_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5, 0))
        self.combined_status_bar_frame.pack_propagate(False) 

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
        
        right_info_panel_frame = ttk.Frame(self.combined_status_bar_frame)
        right_info_panel_frame.pack(side=tk.LEFT, padx=(10,2), pady=0, fill=tk.BOTH, expand=True)
        
        self.speak_button = ttk.Button(right_info_panel_frame, text="Loading...", command=self.action_callbacks['toggle_speaking_recording'], state=tk.DISABLED, style="Speak.TButton")
        self.speak_button.pack(side=tk.RIGHT, fill=tk.Y, padx=(5,2), pady=2)
        
        left_detail_frame = ttk.Frame(right_info_panel_frame) 
        left_detail_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=2, padx=(0,5))

        self.app_status_label = ttk.Label(left_detail_frame, text="Initializing...", style="AppStatus.TLabel", relief=tk.FLAT) 
        self.app_status_label.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(0,2))
        
        gpu_stack_frame = ttk.Frame(left_detail_frame) 
        gpu_stack_frame.pack(side=tk.BOTTOM, fill=tk.X)
        self.gpu_mem_label = ttk.Label(gpu_stack_frame, text="GPU Mem: N/A", style="GPUStatus.TLabel") 
        self.gpu_mem_label.pack(side=tk.LEFT, padx=(0,5))
        self.gpu_util_label = ttk.Label(gpu_stack_frame, text="GPU Util: N/A", style="GPUStatus.TLabel") 
        self.gpu_util_label.pack(side=tk.LEFT)

        user_info_overall_height = 420 
        user_info_frame = ttk.Frame(main_frame, height=user_info_overall_height) 
        user_info_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5, 5)) 
        user_info_frame.pack_propagate(False)
        
        user_info_frame.columnconfigure(0, weight=1) 
        user_info_frame.columnconfigure(1, weight=1)
        user_info_frame.columnconfigure(2, weight=1)
        user_info_frame.rowconfigure(0, weight=1) 
        user_info_frame.rowconfigure(1, weight=3) 

        kanban_font_family = 'Helvetica'
        kanban_font_size = max(config.MIN_CHAT_FONT_SIZE - 2 , self.current_chat_font_size - 3)
        kanban_font = tkfont.Font(family=kanban_font_family, size=kanban_font_size)
        
        kanban_scrolled_text_options = {
            "wrap": tk.WORD, "height": 4, "state": tk.DISABLED, 
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

        kanban_in_process_labelframe = ttk.LabelFrame(user_info_frame, text="In Process")
        kanban_in_process_labelframe.grid(row=0, column=1, sticky="nsew", padx=2, pady=(0,5))
        self.kanban_in_process_display = scrolledtext.ScrolledText(kanban_in_process_labelframe, **kanban_scrolled_text_options)
        self.kanban_in_process_display.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.scrolled_text_widgets.append(self.kanban_in_process_display)

        kanban_finished_labelframe = ttk.LabelFrame(user_info_frame, text="Finished")
        kanban_finished_labelframe.grid(row=0, column=2, sticky="nsew", padx=(2,0), pady=(0,5))
        self.kanban_finished_display = scrolledtext.ScrolledText(kanban_finished_labelframe, **kanban_scrolled_text_options)
        self.kanban_finished_display.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.scrolled_text_widgets.append(self.kanban_finished_display)

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
            )
            self.calendar_widget.pack(fill=tk.BOTH, expand=True, padx=2, pady=2) 
            self.calendar_widget.bind("<<CalendarSelected>>", self._on_date_selected)
        else:
            no_cal_label = ttk.Label(calendar_outer_labelframe, text="tkcalendar not found.\nCalendar view disabled.", justify=tk.CENTER, anchor=tk.CENTER)
            no_cal_label.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        side_panel_font_family = 'Helvetica'
        side_panel_font_size = max(config.MIN_CHAT_FONT_SIZE - 1 , self.current_chat_font_size - 2) 
        side_panel_font = tkfont.Font(family=side_panel_font_family, size=side_panel_font_size)

        user_info_list_height = 10 
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

        chat_font_family = 'Helvetica' 
        chat_display_font = tkfont.Font(family=chat_font_family, size=self.current_chat_font_size)
        
        chat_scrolled_text_options = user_info_scrolled_text_options.copy() 
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
                    tm_str = e.get("time", "23:59") 
                    try: return datetime.strptime(tm_str, "%H:%M").time()
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

        telegram_lmargin = 10 
        self.chat_history_display.tag_configure("user_telegram_tag", 
                                                foreground=colors["user_msg_fg"], 
                                                lmargin1=telegram_lmargin, lmargin2=telegram_lmargin)
        self.chat_history_display.tag_configure("assistant_telegram_tag", 
                                                foreground=colors["assistant_msg_fg"], 
                                                lmargin1=telegram_lmargin, lmargin2=telegram_lmargin)
        # Tag for Telegram Voice messages (can be styled same as text or differently)
        self.chat_history_display.tag_configure("user_telegram_voice_tag", 
                                                foreground=colors["user_msg_fg"], 
                                                lmargin1=telegram_lmargin, lmargin2=telegram_lmargin,
                                                font=('Helvetica', self.current_chat_font_size, 'italic')) # Example: italicize

    def _handle_space_key_press(self, event=None): 
        try:
            focused_widget = self.app_window.focus_get()
            if isinstance(focused_widget, (tk.Entry, scrolledtext.ScrolledText, tk.Text)):
                logger.debug("Space key released on a text input widget, not toggling speak.")
                return 
        except Exception:
            pass 

        logger.debug("Space key released (not on text input), calling toggle_speaking_recording callback.")
        if 'toggle_speaking_recording' in self.action_callbacks:
            self.action_callbacks['toggle_speaking_recording']()
        else:
            logger.warning("Space key released, but 'toggle_speaking_recording' callback is missing.")
        return "break" 

    def _on_close_button_override(self): 
        logger.info("Window close button clicked. Hiding window to tray.")
        if self.app_window:
            self.app_window.withdraw() 
        if self.tray_icon and not self.tray_icon.visible: 
            logger.warning("Tried to hide to tray, but tray icon is not visible (or not running).")


    def _setup_protocol_handlers(self):
        if self.app_window:
            self.app_window.protocol("WM_DELETE_WINDOW", self._on_close_button_override)
            logger.debug("WM_DELETE_WINDOW protocol handler set to minimize to tray.")
            self.app_window.bind_all("<KeyRelease-space>", self._handle_space_key_press, add="+")
            logger.info("Bound <KeyRelease-space> globally to toggle speaking/recording (with exceptions for text inputs).")
        else:
            logger.warning("App window not available for protocol handler setup.")

    def _setup_tray_icon(self):
        if not PYSTRAY_AVAILABLE or not self.app_window:
            logger.warning("pystray not available or app_window not set. Cannot setup tray icon.")
            return

        if not os.path.exists(self.icon_path):
            logger.error(f"Tray icon file DOES NOT EXIST at: {self.icon_path}. System tray disabled.")
            return

        try:
            image = Image.open(self.icon_path)
        except FileNotFoundError: 
            logger.error(f"Tray icon file not found (during Image.open) at: {self.icon_path}. System tray disabled.")
            return
        except Exception as e:
            logger.error(f"Error loading tray icon with Pillow: {e}. System tray disabled.", exc_info=True)
            return
            
        menu_items = [
            pystray.MenuItem('Show / Hide App', self._toggle_window_visibility, default=True),
            pystray.MenuItem('Models', pystray.Menu(
                pystray.MenuItem('Unload Bark TTS', self._on_tray_unload_bark,
                                 enabled=self._is_bark_loaded_for_tray), 
                pystray.MenuItem('Reload Bark TTS', self._on_tray_reload_bark,
                                 enabled=self._can_reload_bark_for_tray),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem('Unload Whisper STT', self._on_tray_unload_whisper,
                                 enabled=self._is_whisper_loaded_for_tray),
                pystray.MenuItem('Reload Whisper STT', self._on_tray_reload_whisper,
                                 enabled=self._can_reload_whisper_for_tray)
            )),
            pystray.MenuItem('Telegram Bot', pystray.Menu(
                pystray.MenuItem('Start Bot', self._on_tray_start_telegram_bot,
                                 enabled=self._can_start_telegram_bot_for_tray),
                pystray.MenuItem('Stop Bot', self._on_tray_stop_telegram_bot,
                                 enabled=self._can_stop_telegram_bot_for_tray)
            )),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Exit Iri-shka', self._on_tray_exit)
        ]

        self.tray_icon = pystray.Icon("Iri-shka", image, "Iri-shka Assistant", menu_items)
        
        def run_tray():
            try:
                logger.info("pystray icon.run() called.")
                self.tray_icon.run() 
                logger.info("pystray icon.run() finished.")
            except Exception as e_tray_run:
                logger.error(f"Exception in tray_icon.run(): {e_tray_run}", exc_info=True)

        self.tray_thread = threading.Thread(target=run_tray, daemon=True, name="SysTrayThread")
        self.tray_thread.start()
        logger.info("System tray icon thread started.")

    def _is_bark_loaded_for_tray(self, item=None): 
        return MODEL_STATUS_CHECK_AVAILABLE and tts_manager.is_tts_ready()

    def _can_reload_bark_for_tray(self, item=None):
        return MODEL_STATUS_CHECK_AVAILABLE and tts_manager.TTS_CAPABLE and \
               not tts_manager.is_tts_ready() and not tts_manager.is_tts_loading()

    def _is_whisper_loaded_for_tray(self, item=None):
        return MODEL_STATUS_CHECK_AVAILABLE and whisper_handler.whisper_model_ready

    def _can_reload_whisper_for_tray(self, item=None):
        return MODEL_STATUS_CHECK_AVAILABLE and whisper_handler.WHISPER_CAPABLE and \
               not whisper_handler.whisper_model_ready and not whisper_handler.whisper_loading_in_progress

    def _can_start_telegram_bot_for_tray(self, item=None):
        if not TELEGRAM_STATUS_CHECK_AVAILABLE or not app_telegram_handler_module:
            return False
        current_status = app_telegram_handler_module.get_telegram_bot_status()
        return current_status in ["off", "no_token", "no_admin", "error", "bad_token", "net_error"]


    def _can_stop_telegram_bot_for_tray(self, item=None):
        if not TELEGRAM_STATUS_CHECK_AVAILABLE or not app_telegram_handler_module:
            return False
        current_status = app_telegram_handler_module.get_telegram_bot_status()
        return current_status in ["loading", "polling"]

    def _toggle_window_visibility(self, icon=None, item=None): 
        if not self.app_window: return
        self.app_window.after(0, self._do_toggle_window_visibility)

    def _do_toggle_window_visibility(self):
        if self.app_window.winfo_viewable():
            logger.info("Hiding window from tray request.")
            self.app_window.withdraw()
        else:
            logger.info("Showing window from tray request.")
            self.app_window.deiconify()
            self.app_window.lift() 
            self.app_window.focus_force() 


    def _on_tray_unload_bark(self):
        logger.info("Tray: Unload Bark TTS requested.")
        if 'unload_bark_model' in self.action_callbacks:
            self.action_callbacks['unload_bark_model']()
        else: logger.warning("unload_bark_model callback not found.")

    def _on_tray_reload_bark(self):
        logger.info("Tray: Reload Bark TTS requested.")
        if 'reload_bark_model' in self.action_callbacks:
            self.action_callbacks['reload_bark_model']()
        else: logger.warning("reload_bark_model callback not found.")

    def _on_tray_unload_whisper(self):
        logger.info("Tray: Unload Whisper STT requested.")
        if 'unload_whisper_model' in self.action_callbacks:
            self.action_callbacks['unload_whisper_model']()
        else: logger.warning("unload_whisper_model callback not found.")

    def _on_tray_reload_whisper(self):
        logger.info("Tray: Reload Whisper STT requested.")
        if 'reload_whisper_model' in self.action_callbacks:
            self.action_callbacks['reload_whisper_model']()
        else: logger.warning("reload_whisper_model callback not found.")

    def _on_tray_start_telegram_bot(self):
        logger.info("Tray: Start Telegram Bot requested.")
        if 'start_telegram_bot' in self.action_callbacks:
            self.action_callbacks['start_telegram_bot']()
        else: logger.warning("start_telegram_bot callback not found for tray.")

    def _on_tray_stop_telegram_bot(self):
        logger.info("Tray: Stop Telegram Bot requested.")
        if 'stop_telegram_bot' in self.action_callbacks:
            self.action_callbacks['stop_telegram_bot']()
        else: logger.warning("stop_telegram_bot callback not found for tray.")

    def _on_tray_exit(self):
        logger.info("Exit requested from system tray.")
        if self.app_window: 
            self.app_window.after(0, self._do_tray_exit)
        else: 
            logger.error("Tray exit called but app_window is None.")

    def _do_tray_exit(self): 
        if 'on_exit' in self.action_callbacks:
            self.action_callbacks['on_exit']() 
        else:
            logger.error("on_exit callback not found for tray exit. Forcing quit.")
            if self.app_window: self.app_window.quit() 

    def _safe_ui_update(self, update_lambda):
        if self.app_window and self.app_window.winfo_exists():
            self.app_window.after(0, update_lambda)
        else:
            logger.warning("Attempted safe UI update, but app window no longer exists.")

    def update_status_label(self, msg):
        if self.app_status_label:
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
        
        if status_category == "ready": label_bg = "#90EE90"; label_fg = "dark green"  
        elif status_category in ["loaded", "saved", "fresh", "idle"]: label_bg = "#ADD8E6"; label_fg = "navy"  
        elif status_category == "off": label_bg = "#D3D3D3"; label_fg = "dimgray"  
        elif status_category in ["loading", "checking", "pinging", "thinking"]: label_bg = "#FFFFE0"; label_fg = "darkgoldenrod" 
        elif status_category in ["error", "na", "timeout", "conn_error", "http_502", "http_other", "InitFail", "unreachable", "bad_token", "net_error"]: label_bg = "#FFA07A"; label_fg = "darkred" 
        
        elif status_category == "polling": label_bg = "#90EE90"; label_fg = "dark green" 
        elif status_category == "no_token": label_bg = "#FFA07A"; label_fg = "darkred" 
        elif status_category == "no_admin": label_bg = "#FFA07A"; label_fg = "darkred" 
        
        widget_label.config(text=text_to_display, background=label_bg, foreground=label_fg)
        widget_frame.config(background=label_bg) 

    def update_act_status(self, short_text, status_type_str):
        self._safe_ui_update(lambda: self._update_component_status_widget_internal(
            self.act_status_frame, self.act_status_text_label, short_text, status_type_str
        ))

    def update_inet_status(self, short_text, status_type_str):
        self._safe_ui_update(lambda: self._update_component_status_widget_internal(
            self.inet_status_frame, self.inet_status_text_label, short_text, status_type_str
        ))

    def update_webui_status(self, short_text, status_type_str):
        self._safe_ui_update(lambda: self._update_component_status_widget_internal(
            self.webui_status_frame, self.webui_status_text_label, short_text, status_type_str
        ))

    def update_tele_status(self, short_text, status_type_str): 
        self._safe_ui_update(lambda: self._update_component_status_widget_internal(
            self.tele_status_frame, self.tele_status_text_label, short_text, status_type_str
        ))

    def update_memory_status(self, short_text, status_type_str):
        self._safe_ui_update(lambda: self._update_component_status_widget_internal(
            self.memory_status_frame, self.memory_status_text_label, short_text, status_type_str
        ))

    def update_hearing_status(self, short_text, status_type_str):
        self._safe_ui_update(lambda: self._update_component_status_widget_internal(
            self.hearing_status_frame, self.hearing_status_text_label, short_text, status_type_str
        ))

    def update_voice_status(self, short_text, status_type_str):
        self._safe_ui_update(lambda: self._update_component_status_widget_internal(
            self.voice_status_frame, self.voice_status_text_label, short_text, status_type_str
        ))

    def update_mind_status(self, short_text, status_type_str):
        self._safe_ui_update(lambda: self._update_component_status_widget_internal(
            self.mind_status_frame, self.mind_status_text_label, short_text, status_type_str
        ))

    def update_gpu_status_display(self, mem_text, util_text, status_category):
        def _update():
            if not self.gpu_mem_label or not self.gpu_util_label: return
            self.gpu_mem_label.config(text=f"GPU Mem: {mem_text}")
            self.gpu_util_label.config(text=f"GPU Util: {util_text}")
            
            colors = self.get_current_theme_colors()
            default_fg = colors.get("label_fg", "SystemWindowText") 
            error_fg = colors.get("assistant_error_fg", "red") 
            disabled_fg = colors.get("button_disabled_fg", "silver") 
            checking_fg = "orange" 

            fg_color = default_fg 
            if status_category == "ok_gpu": fg_color = default_fg
            elif status_category == "na_nvml": fg_color = disabled_fg
            elif status_category in ["error", "error_nvml_loop", "InitFail"]: fg_color = error_fg
            elif status_category == "checking": fg_color = checking_fg
            
            self.gpu_mem_label.config(foreground=fg_color)
            self.gpu_util_label.config(foreground=fg_color)
        self._safe_ui_update(_update)

    def _add_message_to_display_internal(self, message_with_prefix, tag_tuple, is_error=False):
        if not self.chat_history_display: return
        
        tags_to_apply = list(tag_tuple) if isinstance(tag_tuple, (list, tuple)) else [tag_tuple]
        
        if is_error and ("assistant_tag" in tags_to_apply or "assistant_telegram_tag" in tags_to_apply) :
            if "assistant_tag_error" not in tags_to_apply: 
                 tags_to_apply.append("assistant_tag_error")
            if "assistant_tag" in tags_to_apply: tags_to_apply.remove("assistant_tag")
            if "assistant_telegram_tag" in tags_to_apply: tags_to_apply.remove("assistant_telegram_tag")

        self.chat_history_display.config(state=tk.NORMAL)
        self.chat_history_display.insert(tk.END, message_with_prefix, tuple(tags_to_apply)) 
        self.chat_history_display.see(tk.END)
        self.chat_history_display.config(state=tk.DISABLED)

    def add_user_message_to_display(self, text, source="gui"):
        # text is the raw transcription or text input
        logger.info(f"Displaying User message (Source: {source}): '{text[:70]}...'")
        
        prefix = "You: "
        tag = "user_tag"
        final_display_text = text # This is the raw text/transcription

        if source == "gui":
            # If main.py passes detected_language_code for GUI source, we could append it here
            # For now, let's assume if it needs to be shown, it's already in 'text'
            # by the caller (e.g. in _handle_llm_interaction for initial display of GUI input)
            pass # Default prefix and tag are fine
        elif source == "telegram":
            prefix = "You (Telegram): "
            tag = "user_telegram_tag"
        elif source == "telegram_voice":
            prefix = "You (Telegram Voice): "
            tag = "user_telegram_voice_tag"
            # final_display_text might have "(Lang: xx)" if transcription included it.
            # If not, 'text' is just the transcription.
            
        self._safe_ui_update(lambda: self._add_message_to_display_internal(f"{prefix}{final_display_text}\n", (tag,)))

    def add_assistant_message_to_display(self, text, is_error=False, source="gui"):
        logger.info(f"Displaying Assistant message (Source: {source}, Error={is_error}): '{text[:70]}...'")
        prefix = "Iri-shka: "
        tag = "assistant_tag"
        
        if source == "telegram" or source == "telegram_voice": # Assistant reply to any Telegram source
            prefix = "Iri-shka (to Telegram): "
            tag = "assistant_telegram_tag"
        
        text_to_add = text 
        if not text_to_add.endswith("\n\n"): 
            if text_to_add.endswith("\n"): text_to_add += "\n"
            else: text_to_add += "\n\n"
        
        self._safe_ui_update(lambda: self._add_message_to_display_internal(f"{prefix}{text_to_add}", (tag,), is_error=is_error))


    def update_chat_display_from_list(self, chat_history_list):
        if not self.chat_history_display: return
        logger.info(f"Updating chat display from history list (length: {len(chat_history_list)}).")
        self._configure_tags_for_chat_display() 
        
        def _update():
            self.chat_history_display.config(state=tk.NORMAL)
            self.chat_history_display.delete(1.0, tk.END)
            for turn in chat_history_list:
                # user_message_content is the raw text or transcription from history
                user_message_content = turn.get('user', '') 
                assistant_message = turn.get('assistant', '')
                source = turn.get('source', 'gui') 

                user_prefix = "You: "
                user_tag_to_use = "user_tag"
                final_user_display_text = user_message_content # Start with raw content
                
                if source == "gui":
                    # If main.py stored "detected_language_code_for_gui_display" in history for GUI turns:
                    lang_code_for_gui = turn.get("detected_language_code_for_gui_display")
                    if lang_code_for_gui:
                        final_user_display_text = f"{user_message_content} (Lang: {lang_code_for_gui})"
                elif source == "telegram":
                    user_prefix = "You (Telegram): "
                    user_tag_to_use = "user_telegram_tag"
                elif source == "telegram_voice":
                    user_prefix = "You (Telegram Voice): " 
                    user_tag_to_use = "user_telegram_voice_tag"
                    # If language detected for Telegram voice was stored in history, could append it here:
                    # lang_code_for_tele_voice = turn.get("detected_language_code_for_tele_voice_display")
                    # if lang_code_for_tele_voice:
                    #    final_user_display_text = f"{user_message_content} (Lang: {lang_code_for_tele_voice})"


                assistant_prefix = "Iri-shka: "
                assistant_tag_to_use = "assistant_tag"
                if source == "telegram" or source == "telegram_voice":
                    assistant_prefix = "Iri-shka (to Telegram): "
                    assistant_tag_to_use = "assistant_telegram_tag"

                if user_message_content: # Check if there's any user message
                     self._add_message_to_display_internal(f"{user_prefix}{final_user_display_text}\n", (user_tag_to_use,))
                
                if assistant_message:
                    is_error = assistant_message.startswith(("[Ollama Error:", "[LLM Error:", "[LLM Unreachable:")) or \
                               assistant_message in ("I didn't catch that, could you please repeat?", "Я не расслышала, не могли бы вы повторить?")
                    
                    fmt_msg = assistant_message 
                    if not fmt_msg.endswith("\n\n"):
                        if fmt_msg.endswith("\n"): fmt_msg += "\n"
                        else: fmt_msg += "\n\n"
                    self._add_message_to_display_internal(f"{assistant_prefix}{fmt_msg}", (assistant_tag_to_use,), is_error=is_error)
            
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
                [e for e in all_events_data if isinstance(e, dict) and "date" in e], 
                key=lambda e: (
                    datetime.strptime(e.get("date", "1900-01-01"), "%Y-%m-%d").date() 
                        if isinstance(e.get("date"), str) else date.min, 
                    datetime.strptime(e.get("time", "00:00"), "%H:%M").time() 
                        if e.get("time") and isinstance(e.get("time"), str) else datetime.min.time()
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
                display_widget.insert(tk.END, f"No {column_name_for_log.lower()} tasks." if not tasks_list else f"Invalid data.")
            else:
                for task_item in tasks_list:
                    display_widget.insert(tk.END, f"- {str(task_item)}\n")
            display_widget.config(state=tk.DISABLED)
            display_widget.see(tk.END) 
        self._safe_ui_update(_update)

    def update_kanban_pending(self, tasks_list):
        self._update_kanban_column(self.kanban_pending_display, tasks_list, "Pending")

    def update_kanban_in_process(self, tasks_list):
        self._update_kanban_column(self.kanban_in_process_display, tasks_list, "In Process")

    def update_kanban_completed(self, tasks_list):
        self._update_kanban_column(self.kanban_finished_display, tasks_list, "Completed")

    def show_error_messagebox(self, title, msg):
        logger.error(f"Displaying error messagebox: Title='{title}', Message='{msg}'")
        parent_window = self.app_window if self.app_window and self.app_window.winfo_exists() else None
        self._safe_ui_update(lambda: messagebox.showerror(title, msg, parent=parent_window))

    def show_info_messagebox(self, title, msg):
        logger.info(f"Displaying info messagebox: Title='{title}', Message='{msg}'")
        parent_window = self.app_window if self.app_window and self.app_window.winfo_exists() else None
        self._safe_ui_update(lambda: messagebox.showinfo(title, msg, parent=parent_window))

    def show_warning_messagebox(self, title, msg):
        logger.warning(f"Displaying warning messagebox: Title='{title}', Message='{msg}'")
        parent_window = self.app_window if self.app_window and self.app_window.winfo_exists() else None
        self._safe_ui_update(lambda: messagebox.showwarning(title, msg, parent=parent_window))


    def destroy_window(self):
        logger.info("GUIManager: destroy_window called.")
        if self.tray_icon:
            logger.info("Stopping system tray icon...")
            self.tray_icon.stop() 
            if self.tray_thread and self.tray_thread.is_alive():
                logger.info("Waiting for tray thread to join...")
                self.tray_thread.join(timeout=2.0) 
                if self.tray_thread.is_alive():
                    logger.warning("Tray thread did not join cleanly.")
            self.tray_icon = None
            self.tray_thread = None
            logger.info("System tray icon stopped and thread joined (or timed out).")

        if self.app_window:
            logger.info("Destroying Tkinter window...")
            try: 
                self.app_window.destroy()
                logger.info("Tkinter window destroyed.")
            except tk.TclError as e: 
                if "application has been destroyed" not in str(e).lower():
                    logger.warning(f"Tkinter error during destroy: {e}", exc_info=False)
            self.app_window = None 
        else: 
            logger.info("Destroy window called, but no app_window instance.")