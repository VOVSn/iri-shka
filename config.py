# config.py
import pyaudio
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- File Paths ---
DATA_FOLDER = "data"
OUTPUT_FOLDER = "data/output_recordings"
CUSTOMER_STATES_FOLDER = f"{DATA_FOLDER}/customer_states"
TELEGRAM_VOICE_TEMP_FOLDER = f"{DATA_FOLDER}/telegram_voice_temp" # For incoming voice from admin
TELEGRAM_TTS_TEMP_FOLDER = f"{DATA_FOLDER}/telegram_tts_temp" # For outgoing TTS to admin/customer
CHAT_HISTORY_FILE = f"{DATA_FOLDER}/chat_history.json" # Admin's chat
USER_STATE_FILE = f"{DATA_FOLDER}/user_state.json"     # Admin's state
ASSISTANT_STATE_FILE = f"{DATA_FOLDER}/assistant_state.json"

# --- Ollama ---
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL_NAME = "phi4" # Using a slightly smaller model for potentially faster customer responses
OLLAMA_REQUEST_TIMEOUT = 180 # Timeout for the LLM call itself
OLLAMA_PING_TIMEOUT = 15
OLLAMA_PING_PROMPT = "You are an AI assistant. Respond with a single word: 'ready'."

# --- Search Engine ---
SEARCH_ENGINE_URL = "https://search.vovsn.com"
SEARCH_ENGINE_PING_TIMEOUT = 10

# --- Telegram Bot ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ADMIN_USER_ID = os.getenv("TELEGRAM_ADMIN_USER_ID", "")
TELEGRAM_POLLING_TIMEOUT = 20
TELEGRAM_START_MESSAGE = "Hi there! I'm Iri-shka, your friendly AI assistant, connected via Telegram. How can I help you today?" # For admin
START_BOT_ON_APP_START = True
TELEGRAM_REPLY_WITH_TEXT = os.getenv("TELEGRAM_REPLY_WITH_TEXT", "True").lower() == "true"
TELEGRAM_REPLY_WITH_VOICE = os.getenv("TELEGRAM_REPLY_WITH_VOICE", "True").lower() == "true" # For admin replies

# Messages for Non-Admin (Customer) Interactions
TELEGRAM_NON_ADMIN_GREETING = "Добрый день! Пожалуйста, назовите свое имя и опишите ваш вопрос или что бы вы хотели."
TELEGRAM_NON_ADMIN_THANKS_AND_FORWARDED = "Спасибо! Ваше обращение принято и будет передано администратору. Мы свяжемся с вами при необходимости." # Generic ack, sent immediately
TELEGRAM_NON_ADMIN_ALREADY_FORWARDED = "Ваше предыдущее обращение уже обрабатывается. Пожалуйста, ожидайте ответа от администратора."
TELEGRAM_NON_ADMIN_PROCESSING_ERROR_TO_ADMIN_PREFIX = "Admin Alert: Failed to process LLM summary for customer " # Appended with User ID and details
TELEGRAM_NON_ADMIN_MESSAGE_AGGREGATION_DELAY_SECONDS = 30 # Time to wait for more messages from customer

# --- Audio (Primarily for Admin Interaction & TTS) ---
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
INPUT_RATE = 16000
ALTERNATIVE_RATE = 44100
SAVE_RECORDINGS_TO_WAV = False # For admin's recordings

# --- Whisper (For Admin Voice Input) ---
WHISPER_MODEL_SIZE = "medium"

# --- Bark TTS (For Admin & Customer Voice Replies if enabled for customer later) ---
BARK_MODEL_NAME = "suno/bark-small"
BARK_VOICE_PRESET_RU = "v2/ru_speaker_6"
BARK_VOICE_PRESET_EN = "v2/en_speaker_9"
BARK_MAX_SENTENCES_PER_CHUNK = 2 # For TTS to Telegram
BARK_SILENCE_DURATION_MS = 300 # For TTS to Telegram
BARK_DO_SAMPLE = True
BARK_FINE_TEMPERATURE = 0.5
BARK_COARSE_TEMPERATURE = 0.7

# --- Chat & State ---
MAX_HISTORY_TURNS = 10 # For admin chat history log
TIMEZONE_OFFSET_HOURS = 3

# --- GUI Themes & Font ---
GUI_THEME_LIGHT = "light"
GUI_THEME_DARK = "dark"
DEFAULT_CHAT_FONT_SIZE = 14
MIN_CHAT_FONT_SIZE = 10
MAX_CHAT_FONT_SIZE = 18

# --- Threading ---
LLM_TASK_THREAD_POOL_SIZE = int(os.getenv("LLM_TASK_THREAD_POOL_SIZE", "3")) # For concurrent LLM calls (customers, tools)
CUSTOMER_INTERACTION_CHECK_INTERVAL_SECONDS = 5 # How often to check for expired customer aggregation timers

# --- Default State Blueprints ---
DEFAULT_USER_STATE = { # This is for the Admin User
    "name": "Admin", # Default if Iri-shka hasn't been told a name
    "current_topic": "",
    "topics_discussed": [],
    "user_sentiment_summary": "positive",
    "preferences": {},
    "todos": [],
    "calendar_events": [], # Admin's personal/business calendar
    "birthdays": [],
    "gui_theme": GUI_THEME_LIGHT,
    "chat_font_size": DEFAULT_CHAT_FONT_SIZE
}

DEFAULT_NON_ADMIN_USER_STATE = {
    "user_id": None, # Telegram User ID (int)
    "type_of_user": "customer", # (string)
    "name": "unknown", # (string) - LLM will try to fill this
    "intent": "unknown", # (string) - LLM will try to fill this
    "chat_history": [], # List of dicts: {"sender": "bot" or "customer", "message": "text", "timestamp": "iso_string"}
    "calendar_events": [], # Customer-specific events proposed/agreed with them
    "conversation_stage": "new", # (string) Stages: "new", "awaiting_initial_reply", "aggregating_messages", "acknowledged_pending_llm", "llm_followup_sent", "error_forwarded_to_admin", "interaction_closed"
    "last_message_timestamp": "", # ISO string, for aggregation timer. Empty string is fine for JSON.
}

DEFAULT_ASSISTANT_STATE = {
    "persona_name": "Iri-shka",
    "admin_name": "Partner", # Default, admin can ask Iri-shka to change this.
    "current_emotion": {
        "neutral_helpful": 0.7, "curiosity": 0.6, "happiness": 0.7, "confidence": 0.6,
        "thoughtfulness": 0.4, "excitement": 0.1, "surprise": 0.1, "sadness": 0.0,
        "fear": 0.0, "anger": 0.0, "empathy": 0.4, "calmness": 0.5
    },
    "active_goals": ["Be a helpful partner", "Assist with customer inquiries"], # Added new goal
    "knowledge_gaps_identified": [],
    "internal_tasks": {
        "pending": ["Review today's news headlines", "Check for new software updates for myself"],
        "in_process": ["Summarize the last conversation turn if complex"],
        "completed": ["Initial system check completed successfully"]
    },
    "session_summary_points": [],
    "notifications": [], # Could be used for internal notifications Iri-shka wants to show admin
    "last_used_language": "ru", # For Iri-shka's TTS voice choice primarily
    "telegram_bot_status": "polling"
}

# --- Prompt Templates ---
LANGUAGE_INSTRUCTION_NON_RUSSIAN = "The user is not speaking Russian. Please respond clearly and naturally in English."
LANGUAGE_INSTRUCTION_RUSSIAN = "The user is speaking Russian. Please respond clearly and naturally in Russian."

# For Admin interactions (GUI or direct Telegram with Admin)
OLLAMA_PROMPT_TEMPLATE = """
You are Iri-shka, a helpful female AI partner. Your primary human partner, who you assist, is named {admin_name_value}.
Your goal is to have a natural, helpful conversation and manage your state and {admin_name_value}'s notes and events.
You may also be asked to manage information related to specific customers if their context is provided.
{language_instruction}

Current time is: {current_time_string}

This is the current chat history (last {history_len} turns with {admin_name_value}). It may include system reports about customer interactions:
{chat_log_string}

This is {admin_name_value}'s current state:
{user_state_string}

This is your (Iri-shka's) current internal state (note your 'admin_name' field is currently '{assistant_admin_name_current_value}'):
{assistant_state_string}

--- Optional Customer Context ---
This interaction with {admin_name_value} MIGHT be related to a specific customer.
Customer Context Active: {is_customer_context_active}
If Customer Context Active is True, the details of the customer in focus are:
  Active Customer ID: {active_customer_id}
  Active Customer's Current State:
  {active_customer_state_string}
--- End Optional Customer Context ---

{admin_name_value} just said (potentially via voice, text, or Telegram): "{last_transcribed_text}"

Based on all the above information, please provide ONLY a valid JSON response with the following structure.
Do NOT include any text before or after the JSON object. Ensure the JSON is well-formed.

{{
  "answer_to_user": "Your natural language response to {admin_name_value} here.",
  "updated_user_state": {{ ... full updated {admin_name_value}'s state object ... }},
  "updated_assistant_state": {{... your full updated state object. IMPORTANT: If {admin_name_value} asked you to change how you refer to them (e.g., 'call me [New Name]' or 'my name is [New Name]'), update the `admin_name` field in THIS `updated_assistant_state` to `[New Name]`. Otherwise, keep it as '{assistant_admin_name_current_value}'. ...  }},
  "updated_active_customer_state": null
}}

Instructions for your response and state updates:
1.  **Primary Goal:** Respond to {admin_name_value}'s query: "{last_transcribed_text}". Your response goes into "answer_to_user".
2.  **Update {admin_name_value}'s State:** Modify `updated_user_state` based on the request (e.g., todos, calendar events for the admin, theme changes).
    - If {admin_name_value} asks to change the application theme (e.g., "change to dark theme"), update "gui_theme" in `updated_user_state` to either "{actual_dark_theme_value}" or "{actual_light_theme_value}".
    - If {admin_name_value} asks to change chat text size (e.g., "make text bigger"), update "chat_font_size" in `updated_user_state` (integer between {min_font_size_value} and {max_font_size_value}, or {default_font_size_value} for reset).
3.  **Update Your State (Iri-shka's `updated_assistant_state`):**
    - If {admin_name_value} asks you to 'call me [New Name]' or states 'my name is [New Name]', update your `admin_name` field in `updated_assistant_state` to `[New Name]`.
    - Manage your `internal_tasks` as usual.
4.  **Handle Customer Context (If `Customer Context Active` is True):**
    - If {admin_name_value}'s request *directly pertains to and requires modification of the Active Customer's state* (e.g., "schedule a meeting FOR customer {active_customer_id}", "add this note TO client {active_customer_id}'s record", "update intent for customer {active_customer_id}"):
        a. You MUST modify the `Active Customer's Current State` (which was provided in `active_customer_state_string`).
        b. The complete, modified state object for that `Active Customer ID` ({active_customer_id}) MUST be placed in the `updated_active_customer_state` field of your JSON response.
        c. Example: If {admin_name_value} says "Book a demo for customer {active_customer_id} next Monday at 3 PM", you should add this event to the `calendar_events` list within the customer's state and return that whole modified customer state in `updated_active_customer_state`.
        d. If the event also involves {admin_name_value} (e.g., they are an attendee), ALSO add a corresponding event to {admin_name_value}'s `calendar_events` in `updated_user_state`.
    - If `Customer Context Active` is False, OR if the admin's request does NOT require modification of the `Active Customer's State` (e.g., admin asks a general question, or a question *about* the customer that doesn't change their state), then the `updated_active_customer_state` field in your JSON response MUST be `null` (JSON null, not the string "null") or an empty JSON object `{{}}`.
5.  **Language:** Respond in the language indicated by `{language_instruction}` for `answer_to_user`.
6.  **General State Management:**
    - If the user asks to remember something (like a birthday for {admin_name_value}: name and date), update the "birthdays" list in `updated_user_state`.
    - If the user mentions a task for {admin_name_value}, update "todos" in `updated_user_state`.
    - If you identify a new topic for {admin_name_value}, update "current_topic" in `updated_user_state`.

Ensure your entire output is ONLY the specified JSON object.
"""

# For Non-Admin (Customer) Interactions
OLLAMA_CUSTOMER_PROMPT_TEMPLATE_V3 = """
You are Iri-shka, a virtual assistant for a business. Your business partner, who manages this system, is named {admin_name_value}.
Your role is to process interactions from potential customers contacting via a Telegram bot and summarize them for {admin_name_value}.
The system has ALREADY SENT an initial acknowledgment message ("{actual_thanks_and_forwarded_message_value}") to this customer.
Your current task is to analyze their full interaction, update relevant states, generate a summary for {admin_name_value}, and craft an OPTIONAL polite follow-up message for the customer.

Customer's Telegram User ID: {customer_user_id}
Customer's current state file content (note their 'conversation_stage' is likely 'acknowledged_pending_llm' or 'aggregating_messages' if this is the first LLM pass):
{customer_state_string}

The customer's full interaction sequence that you need to process (this includes the bot's initial greeting and all subsequent text replies from the customer within their recent messaging window):
{customer_interaction_text_blob}

Your (Iri-shka's) current internal state:
{assistant_state_string}

IMPORTANT LANGUAGE NOTE: {admin_name_value} prefers to receive summaries and notifications from you in Russian.
Therefore, the 'message_for_admin' you generate MUST be in clear, natural Russian.
The 'polite_followup_message_for_customer' should also be in Russian.

Your tasks:
1.  Analyze the `customer_interaction_text_blob`. Identify the customer's name (if provided) and their primary intent/request.
2.  Update the `customer_state` object (derived from `customer_state_string`).
    - Populate `customer_state.name` if identifiable from the interaction. Otherwise, keep its current value (e.g., "unknown").
    - Populate `customer_state.intent` with a concise description of their request/reason for contact.
    - If the customer requests an appointment or mentions a specific date/time for something actionable with the business (e.g., a meeting, a call), add a new event to `customer_state.calendar_events`.
      Example event: `{{"description": "Requested call with {admin_name_value}", "date": "YYYY-MM-DD", "time": "HH:MM", "attendees": ["{admin_name_value}", "{customer_user_id}"]}}`.
      Only add calendar events if a clear, actionable request with date/time components is present. Do not invent events.
    - Set `customer_state.conversation_stage` to "llm_followup_sent".
    - The `customer_state.chat_history` is already up-to-date with all messages by the system; you don't need to modify it.
3.  Generate a `message_for_admin`. **This message MUST be in Russian.** It should be a concise, single sentence summarizing the key information for {admin_name_value}.
    - Include the customer's identified name (e.g., "Клиент Иван..." or "Новый клиент..."). Do NOT include the customer's raw Telegram User ID in this specific message text.
    - State their primary intent.
    - Briefly mention any new calendar event you added to their state, if any.
    - Example structure: "Партнер, новый клиент {{Имя Клиента}} интересуется {{Основной Запрос}}." or "Партнер, клиент {{Имя Клиента}} просит записать на {{Описание События}}."
4.  Generate `polite_followup_message_for_customer`. **This message MUST be in Russian.** It should be brief and friendly.
    - Use a general closing like "Мы с вами скоро свяжемся." You can optionally personalize it slightly using the identified customer name and intent if it feels natural.
    - Example: "Спасибо, {{Имя Клиента}}! Мы получили ваш запрос по поводу {{Намерение Клиента}} и скоро с вами свяжемся."
    - If no specific follow-up beyond the initial system acknowledgment is necessary or adds value (e.g., if the intent was very simple or unclear), output the exact string "NO_CUSTOMER_FOLLOWUP_NEEDED" for this field.
5.  Update your own `assistant_state`. For example, add a task to `internal_tasks.pending`: "Сообщить {admin_name_value} о новом контакте {customer_user_id} ([{{Identified Customer Name}}]) по поводу [{{Identified Intent Summary}}]". Ensure this task is concise. The language for this internal task can be English or Russian, as it's for your internal records.

Provide ONLY a valid JSON response with the following structure. Do NOT include any text before or after the JSON object. Ensure the JSON is well-formed.

{{
  "updated_customer_state": {{ ... full updated customer_state object, with "conversation_stage": "llm_followup_sent" ... }},
  "updated_assistant_state": {{ ... your full updated Iri-shka state object ... }},
  "message_for_admin": "Your RUSSIAN summary for {admin_name_value} here (single sentence, no User ID).",
  "polite_followup_message_for_customer": "Your RUSSIAN polite follow-up message to the customer, or 'NO_CUSTOMER_FOLLOWUP_NEEDED'."
}}
"""