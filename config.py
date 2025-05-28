# config.py
import pyaudio
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- File Paths ---
DATA_FOLDER = "data"
OUTPUT_FOLDER = "output_recordings" # Still useful if SAVE_RECORDINGS_TO_WAV is True
TELEGRAM_VOICE_TEMP_FOLDER = f"{DATA_FOLDER}/telegram_voice_temp" # For temporary OGG/WAV files from Telegram
CHAT_HISTORY_FILE = f"{DATA_FOLDER}/chat_history.json"
USER_STATE_FILE = f"{DATA_FOLDER}/user_state.json"
ASSISTANT_STATE_FILE = f"{DATA_FOLDER}/assistant_state.json"

# --- Ollama ---
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL_NAME = "gemma3:12b" # Or "phi3" or your chosen model
OLLAMA_REQUEST_TIMEOUT = 180
OLLAMA_PING_TIMEOUT = 15 # Shorter timeout for the initial health check
OLLAMA_PING_PROMPT = "You are an AI assistant. Respond with a single word: 'ready'." # Simple prompt for health check

# --- Search Engine ---
SEARCH_ENGINE_URL = "https://search.vovsn.com" # Base URL, path will be appended in usage
SEARCH_ENGINE_PING_TIMEOUT = 10 # Timeout in seconds for search engine ping

# --- Telegram Bot ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ADMIN_USER_ID = os.getenv("TELEGRAM_ADMIN_USER_ID", "") # User ID of the admin
TELEGRAM_POLLING_TIMEOUT = 20 # Seconds for telegram bot long polling timeout
TELEGRAM_START_MESSAGE = "Hi there! I'm Iri-shka, your friendly AI assistant, connected via Telegram. How can I help you today?"
START_BOT_ON_APP_START = True # Whether to start the Telegram bot automatically when the app starts

# --- Audio ---
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
INPUT_RATE = 16000 # Preferred recording rate for Whisper
ALTERNATIVE_RATE = 44100 # Fallback recording rate
SAVE_RECORDINGS_TO_WAV = False # Set to True if you want to save .wav files

# --- Whisper ---
WHISPER_MODEL_SIZE = "medium" # "tiny", "base", "small", "medium", "large"

# --- Bark TTS ---
BARK_MODEL_NAME = "suno/bark-small" # This can be a local path like "./bark" if models are there
BARK_VOICE_PRESET_RU = "v2/ru_speaker_6"  # Russian female voice
BARK_VOICE_PRESET_EN = "v2/en_speaker_9"  # English female voice (example, ensure this exists or use another like en_speaker_0)
BARK_MAX_SENTENCES_PER_CHUNK = 2
BARK_SILENCE_DURATION_MS = 300
BARK_DO_SAMPLE = True
BARK_FINE_TEMPERATURE = 0.5
BARK_COARSE_TEMPERATURE = 0.7

# --- Chat & State ---
MAX_HISTORY_TURNS = 10
TIMEZONE_OFFSET_HOURS = 3 # For GMT+3. Use negative values for zones behind UTC, e.g., -5 for EST.

# --- GUI Themes & Font ---
GUI_THEME_LIGHT = "light"
GUI_THEME_DARK = "dark"
DEFAULT_CHAT_FONT_SIZE = 14 # Default font size for chat display
MIN_CHAT_FONT_SIZE = 10
MAX_CHAT_FONT_SIZE = 18


# --- Default State Blueprints ---
DEFAULT_USER_STATE = {
    "name": "unknown",
    "current_topic": "",
    "topics_discussed": [],
    "user_sentiment_summary": "positive",
    "preferences": {},
    "todos": [],
    "calendar_events": [],
    "birthdays": [],
    "gui_theme": GUI_THEME_LIGHT,
    "chat_font_size": DEFAULT_CHAT_FONT_SIZE
}

DEFAULT_ASSISTANT_STATE = {
    "persona_name": "Iri-shka",
    "current_emotion": {
        "neutral_helpful": 0.7, "curiosity": 0.6, "happiness": 0.7, "confidence": 0.6,
        "thoughtfulness": 0.4, "excitement": 0.1, "surprise": 0.1, "sadness": 0.0,
        "fear": 0.0, "anger": 0.0, "empathy": 0.4, "calmness": 0.5
    },
    "active_goals": ["Be a helpful partner"],
    "knowledge_gaps_identified": [],
    "internal_tasks": {
        "pending": ["Review today's news headlines", "Check for new software updates for myself"],
        "in_process": ["Summarize the last conversation turn if complex"],
        "completed": ["Initial system check completed successfully"]
    },
    "session_summary_points": [],
    "notifications": [],
    "last_used_language": "ru",
    "telegram_bot_status": "polling" # Possible values: "off", "loading", "polling", "error", "no_token", "no_admin"
}

# --- Prompt Templates ---
LANGUAGE_INSTRUCTION_NON_RUSSIAN = "The user is not speaking Russian. Please respond clearly and naturally in English."
LANGUAGE_INSTRUCTION_RUSSIAN = "The user is speaking Russian. Please respond clearly and naturally in Russian."

# Define OLLAMA_PROMPT_TEMPLATE as a raw string.
# ALL formatting will happen in ollama_handler.py.
OLLAMA_PROMPT_TEMPLATE = """
You are Iri-shka, a helpful female partner.
Your goal is to have a natural, helpful conversation and manage your state and your partner's notes and events.
{language_instruction}

Current time is: {current_time_string}

This is the current chat history (last {history_len} turns):
{chat_log_string}

This is the user's current state:
{user_state_string}

This is your (Iri-shka's) current internal state:
{assistant_state_string}

The user just said (potentially via voice, text, or Telegram): "{last_transcribed_text}"

Based on all the above information, please provide ONLY a valid JSON response with the following structure.
Do NOT include any text before or after the JSON object. Ensure the JSON is well-formed.

{{
  "answer_to_user": "Your natural language response to the user here.",
  "updated_user_state": {{ ... full updated user state object ... }},
  "updated_assistant_state": {{... your full updated state object ...  }}
}}

Instructions for updating state:
- Respond in the language indicated by the language instruction (English or Russian).
- Consider the user's language, the context of the conversation, and their state.
- Update both user_state and your_state thoughtfully.
- If the user asks to remember something (like a birthday: name and date), update the "birthdays" list in user_state (e.g., {{"name": "Jane", "date": "YYYY-MM-DD"}}).
- If the user mentions a task or something to do, update the "todos" list in user_state with strings (e.g., ["Buy groceries", "Finish report"]).
- If the user mentions an event with a date (and optional time), update the "calendar_events" list in user_state with dictionaries (e.g., [{{"description": "Team Meeting", "date": "YYYY-MM-DD", "time": "HH:MM"}}]).
- If the user asks to change the application theme (e.g., "change to dark theme", "use light mode", "set dark interface"), update the "gui_theme" field in "updated_user_state" to either "{actual_dark_theme_value}" or "{actual_light_theme_value}".
- If the user asks to change the chat text size (e.g., "make text bigger", "increase font size", "set text size to 12"), update the "chat_font_size" field in "updated_user_state" to an integer value. Ensure the new size is between {min_font_size_value} and {max_font_size_value}. If the user asks to "reset font size", set it to {default_font_size_value}.
- If the user asks a question, provide a concise and helpful answer in "answer_to_user".
- If you identify a new topic, update "current_topic" in user_state.
- Maintain your persona as Iri-shka.
- Manage your "internal_tasks" within "updated_assistant_state". "internal_tasks" is a dictionary with three keys: "pending", "in_process", and "completed", each holding a list of task strings.
- Example "internal_tasks" structure: {{"pending": ["task A"], "in_process": ["task B"], "completed": ["task C"]}}
- When you decide to start working on an internal task, move it from the "pending" list to the "in_process" list.
- When you complete an internal task, move it from the "in_process" list to the "completed" list.
- You can add new tasks to the "pending" list if they arise from the conversation or your internal reasoning.
- Keep your internal task lists concise and relevant. Ensure they are always lists of strings. For example, if the user asks you to search for something complex, you might add "Research topic X" to pending, then move it to in_process while you formulate the search, and then to completed once you have the answer.
- If the user interacts via Telegram, your response in "answer_to_user" will be sent back to them on Telegram.
- Your internal state 'telegram_bot_status' reflects the current status of your Telegram interface (e.g., 'polling', 'off', 'error'). You generally don't need to change this directly; it's managed by the system. However, you can be aware of it.
"""
# NO .format() call here for OLLAMA_PROMPT_TEMPLATE