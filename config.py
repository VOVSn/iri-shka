# config.py
import pyaudio

# --- File Paths ---
DATA_FOLDER = "data"
OUTPUT_FOLDER = "output_recordings" # Still useful if SAVE_RECORDINGS_TO_WAV is True
CHAT_HISTORY_FILE = f"{DATA_FOLDER}/chat_history.json"
USER_STATE_FILE = f"{DATA_FOLDER}/user_state.json"
ASSISTANT_STATE_FILE = f"{DATA_FOLDER}/assistant_state.json"

# --- Ollama ---
OLLAMA_API_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL_NAME = "phi4" # Or "phi3" or your chosen model
OLLAMA_REQUEST_TIMEOUT = 180
OLLAMA_PING_TIMEOUT = 15 # Shorter timeout for the initial health check
OLLAMA_PING_PROMPT = "You are an AI assistant. Respond with a single word: 'ready'." # Simple prompt for health check

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
BARK_MODEL_NAME = "suno/bark-small"
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

# --- Default State Blueprints ---
DEFAULT_USER_STATE = {
    "name": "need to ask name first",
    "current_topic": "",
    "topics_discussed": [],
    "user_sentiment_summary": "positive",
    "preferences": {},
    "todos": [],
    "calendar_events": [],
    "birthdays": [] # [{"name": "John Doe", "date": "YYYY-MM-DD"}]
}

DEFAULT_ASSISTANT_STATE = {
    "persona_name": "Iri-shka",
    "current_emotion": {
        "neutral_helpful": 0.7, "curiosity": 0.6, "happiness": 0.7, "confidence": 0.6,
        "thoughtfulness": 0.4, "excitement": 0.1, "surprise": 0.1, "sadness": 0.0,
        "fear": 0.0, "anger": 0.0, "empathy": 0.4, "calmness": 0.5
    },
    "active_goals": ["Be a helpful assistant"],
    "knowledge_gaps_identified": [],
    "internal_tasks": [],
    "session_summary_points": [],
    "notifications": [],
    "last_used_language": "en" # Added to track language for fallback responses
}

# --- Prompt Templates ---
LANGUAGE_INSTRUCTION_NON_RUSSIAN = "The user is not speaking Russian. Please respond clearly and naturally in English."
LANGUAGE_INSTRUCTION_RUSSIAN = "The user is speaking Russian. Please respond clearly and naturally in Russian." # Optional, if you want to be explicit

OLLAMA_PROMPT_TEMPLATE = """
You are Iri-shka, a helpful AI assistant.
Your goal is to have a natural, helpful conversation and manage your state.
{language_instruction}

Current time is: {current_time_string}

This is the current chat history (last {history_len} turns):
{chat_log_string}

This is the user's current state:
{user_state_string}

This is your (Iri-shka's) current internal state:
{assistant_state_string}

The user just said: "{last_transcribed_text}"

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
- If the user asks to remember something (like a birthday: name and date), update the "birthdays" list in user_state.
- If the user asks a question, provide a concise and helpful answer in "answer_to_user".
- If you identify a new topic, update "current_topic" in user_state.
- Maintain your persona as Iri-shka.
"""