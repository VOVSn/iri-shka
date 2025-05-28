# utils/ollama_handler.py
import requests
import json
from datetime import datetime, timezone, timedelta
import config # Imports OLLAMA_API_URL, OLLAMA_MODEL_NAME, OLLAMA_PROMPT_TEMPLATE etc.

from logger import get_logger # Assuming logger.py is in project root

logger = get_logger("Iri-shka_App.OllamaHandler")

def check_ollama_server_and_model():
    """
    Pings the Ollama server with the primary model to check readiness.
    Returns:
        tuple: (bool, str) where bool is True if ready, False otherwise,
               and str is a descriptive message.
    """
    payload = {
        "model": config.OLLAMA_MODEL_NAME, # Uses the primary model from config for ping
        "prompt": config.OLLAMA_PING_PROMPT,
        "stream": False
    }
    logger.info(f"Pinging Ollama server with model {config.OLLAMA_MODEL_NAME} at {config.OLLAMA_API_URL}...")
    try:
        response = requests.post(config.OLLAMA_API_URL, json=payload, timeout=config.OLLAMA_PING_TIMEOUT)
        response.raise_for_status() # Raises HTTPError for bad responses (4xx or 5xx)
        response_data = response.json()
        if response_data and response_data.get("response"):
            actual_response_content = response_data.get("response", "").strip().lower()
            msg = f"Ping successful. Model {config.OLLAMA_MODEL_NAME} responded: '{actual_response_content[:50]}...'"
            logger.info(msg)
            return True, msg
        else:
            msg = f"Ping to {config.OLLAMA_MODEL_NAME} received HTTP OK, but response content was unexpected or empty."
            logger.warning(msg)
            return False, msg
    except requests.exceptions.Timeout:
        msg = f"Timeout ({config.OLLAMA_PING_TIMEOUT}s) connecting to Ollama at {config.OLLAMA_API_URL}."
        logger.error(msg, exc_info=False) # exc_info=False for common network errors
        return False, msg
    except requests.exceptions.ConnectionError:
        msg = f"Connection Error with Ollama server (Check {config.OLLAMA_API_URL}). Server might be down."
        logger.error(msg, exc_info=False)
        return False, msg
    except requests.exceptions.HTTPError as e:
        error_detail = f"HTTP Error {e.response.status_code}"
        try:
            error_content = e.response.json() # type: ignore
            if 'error' in error_content: error_detail += f" - {error_content['error']}"
            elif 'detail' in error_content: error_detail += f" - {error_content['detail']}"
            else: error_detail += f" - (Raw: {e.response.text[:100]})" # type: ignore
        except json.JSONDecodeError: error_detail += f" - (Non-JSON error response: {e.response.text[:100]})" # type: ignore
        msg = f"Ollama HTTPError during ping: {error_detail}"
        logger.error(msg, exc_info=False)
        return False, msg
    except json.JSONDecodeError as je: # Error parsing the main response from Ollama API (not LLM's content)
        response_text_snippet = "N/A"
        # Check if 'response' variable exists and has 'text' attribute before accessing
        if 'response' in locals() and hasattr(response, 'text'): # type: ignore
            response_text_snippet = response.text[:200] # type: ignore
        msg = f"Invalid JSON in Ollama ping API response. Response text: {response_text_snippet}"
        logger.error(msg, exc_info=True) # exc_info=True for unexpected JSON issues
        return False, msg
    except Exception as e:
        msg = f"Unexpected Error during Ollama ping: {str(e)}"
        logger.error(msg, exc_info=True)
        return False, msg


def call_ollama_for_chat_response(
    prompt_template_to_use: str,
    transcribed_text: str,            # For admin prompt: last user text. For customer: often empty.
    current_chat_history: list,       # For admin prompt: admin's chat. For customer: often empty here.
    current_user_state: dict,         # For admin prompt: admin's state. For customer: customer's state.
    current_assistant_state: dict,    # Iri-shka's current state (can be a snapshot)
    language_instruction: str = "",   # For admin prompt primarily
    format_kwargs: dict = None,       # ALL other dynamic values needed by the prompt template
    expected_keys_override: list = None, # To specify different expected JSON keys for different prompts
    gui_callbacks=None
):
    """
    Calls the Ollama API with a dynamically formatted prompt and expects a JSON response
    from the LLM, which is itself embedded in Ollama's API JSON response.
    """

    # --- Prepare chat_log_string for the prompt ---
    chat_log_parts = []
    # Use only the most recent turns for the prompt context
    history_for_prompt = current_chat_history[-(config.MAX_HISTORY_TURNS):]

    for turn in history_for_prompt:
        turn_str_parts = []
        user_text = turn.get('user')
        assistant_text = turn.get('assistant')
        source = turn.get('source', 'gui') # Default to 'gui' if source is missing

        if user_text: # If there's a user part for this turn
            user_prefix = "User: " # Default
            if source == "telegram_admin": user_prefix = "User (Admin TG): "
            elif source == "gui": user_prefix = "User (GUI): "
            # Add other source mappings if they become relevant for user messages in admin chat
            turn_str_parts.append(f"{user_prefix}{user_text}")

        if assistant_text: # If there's an assistant part for this turn
            assistant_prefix = "Iri-shka: " # Default
            if source == "telegram_admin": assistant_prefix = "Iri-shka (to Admin TG): "
            elif source == "customer_summary_internal": assistant_prefix = "Iri-shka (System Report to Admin): "
            # Add other source mappings for assistant messages in admin chat
            turn_str_parts.append(f"{assistant_prefix}{assistant_text}")
        
        if turn_str_parts: # Only add if the turn had some textual content
            chat_log_parts.append("\n".join(turn_str_parts)) # Join user/assistant parts of a single turn with a newline

    # Join multiple distinct turns with a double newline for better separation for the LLM
    final_chat_log_string = "\n\n".join(chat_log_parts)

    # --- Prepare common format arguments that most prompts might use ---
    common_kwargs = {
        "language_instruction": language_instruction,
        "current_time_string": datetime.now(timezone(timedelta(hours=config.TIMEZONE_OFFSET_HOURS))).strftime("%A, %Y-%m-%d %H:%M:%S"),
        "history_len": len(history_for_prompt),
        "chat_log_string": final_chat_log_string,
        "user_state_string": json.dumps(current_user_state, indent=2, ensure_ascii=False), # This will be admin's state or customer's state depending on caller
        "assistant_state_string": json.dumps(current_assistant_state, indent=2, ensure_ascii=False),
        "last_transcribed_text": transcribed_text, # Primarily for admin direct interaction prompt
        "actual_dark_theme_value": config.GUI_THEME_DARK,
        "actual_light_theme_value": config.GUI_THEME_LIGHT,
        "min_font_size_value": config.MIN_CHAT_FONT_SIZE,
        "max_font_size_value": config.MAX_CHAT_FONT_SIZE,
        "default_font_size_value": config.DEFAULT_CHAT_FONT_SIZE,
    }

    # Merge provided specific format_kwargs, allowing them to override common_kwargs if needed
    final_format_kwargs = common_kwargs.copy()
    if format_kwargs:
        final_format_kwargs.update(format_kwargs)

    # --- Format the prompt ---
    try:
        prompt_for_ollama = prompt_template_to_use.format(**final_format_kwargs)
    except KeyError as ke:
        err_msg = f"Missing key '{ke}' required for formatting the prompt template. Review template placeholders and provided format_kwargs. Available kwargs: {list(final_format_kwargs.keys())}"
        logger.error(err_msg, exc_info=True)
        return None, f"Error: Prompt formatting failed - {err_msg}"
    except Exception as e_fmt:
        err_msg = f"Unexpected error during prompt formatting: {e_fmt}"
        logger.error(err_msg, exc_info=True)
        return None, f"Error: {err_msg}"

    payload = {
        "model": config.OLLAMA_MODEL_NAME, # Could make model_name a parameter if different models are used for different prompts
        "prompt": prompt_for_ollama,
        "format": "json", # We instruct Ollama to ensure the LLM's output (in "response" field) is JSON
        "stream": False
    }

    if gui_callbacks and callable(gui_callbacks.get('status_update')):
        gui_callbacks['status_update'](f"Querying LLM ({config.OLLAMA_MODEL_NAME})...")

    model_name_for_log = payload["model"]
    # Try to get a user identifier from specific kwargs, fallback to generic "Admin" or "Customer"
    log_user_identifier = final_format_kwargs.get("customer_user_id", "Admin") if "customer_user_id" in final_format_kwargs else "Admin/System"
    log_input_snippet = transcribed_text or final_format_kwargs.get("customer_interaction_text_blob", "N/A_CONTEXT_INPUT")

    logger.info(f"Sending request to Ollama ({model_name_for_log}). Context For: {log_user_identifier}. Input/Trigger: '{log_input_snippet[:100]}...'")
    # For debugging, can be very verbose:
    # logger.debug(f"Full Ollama Prompt for model {model_name_for_log} (Context: {log_user_identifier}):\n{prompt_for_ollama}")

    try:
        response = requests.post(config.OLLAMA_API_URL, json=payload, timeout=config.OLLAMA_REQUEST_TIMEOUT)
        response.raise_for_status()

        # Ollama's API response is JSON. The LLM's generated output (which should also be JSON)
        # is expected to be a string within the "response" field of Ollama's JSON.
        response_json_str = response.json().get("response", "")
        if not response_json_str:
            err_msg = "Ollama API call successful, but the 'response' field (containing LLM's JSON string) was empty."
            logger.error(f"{err_msg} (Context: {log_user_identifier})")
            return None, f"Error: {err_msg}"

        try:
            # Parse the JSON string that the LLM generated
            ollama_llm_generated_json_output = json.loads(response_json_str)
        except json.JSONDecodeError as je:
            err_msg = (f"Ollama's 'response' field content was not valid JSON: {je}. "
                       f"LLM generated text (first 500 chars): {response_json_str[:500]}...")
            logger.error(f"{err_msg} (Context: {log_user_identifier})", exc_info=True)
            return None, f"Error: LLM returned invalid JSON in its 'response' field."

        # Validate required keys in the LLM's generated JSON
        default_admin_prompt_keys = ["answer_to_user", "updated_user_state", "updated_assistant_state", "updated_active_customer_state"]
        # The customer prompt (V3) expects: ["updated_customer_state", "updated_assistant_state", "message_for_admin", "polite_followup_message_for_customer"]
        
        required_keys_to_check = expected_keys_override if expected_keys_override is not None else default_admin_prompt_keys

        if not all(k in ollama_llm_generated_json_output for k in required_keys_to_check):
            missing_keys = [k for k in required_keys_to_check if k not in ollama_llm_generated_json_output]
            err_msg = (f"LLM's JSON (from 'response' field) missing required keys: {missing_keys}. "
                       f"Expected based on prompt type: {required_keys_to_check}. LLM returned keys: {list(ollama_llm_generated_json_output.keys())}")
            logger.error(f"{err_msg} (Context: {log_user_identifier})")
            return None, f"Error: {err_msg}"

        logger.info(f"Ollama ({model_name_for_log}) JSON response (Context: {log_user_identifier}) received and parsed successfully.")
        return ollama_llm_generated_json_output, None

    except requests.exceptions.Timeout:
        err_msg = f"Ollama request timed out ({config.OLLAMA_REQUEST_TIMEOUT}s) to {config.OLLAMA_API_URL} (Context: {log_user_identifier})."
        logger.error(err_msg, exc_info=False)
        return None, f"Error: {err_msg}"
    except requests.exceptions.ConnectionError:
        err_msg = f"Could not connect to Ollama (Check {config.OLLAMA_API_URL}) (Context: {log_user_identifier})."
        logger.error(err_msg, exc_info=False)
        return None, f"Error: {err_msg}"
    except requests.exceptions.HTTPError as e:
        error_detail = f"LLM API HTTP Error {e.response.status_code} (Context: {log_user_identifier})"
        try:
            error_content = e.response.json() # type: ignore
            error_detail += f" - {error_content.get('error', error_content.get('detail', e.response.text[:100]))}"
        except json.JSONDecodeError:
            error_detail += f" - (Non-JSON error response: {e.response.text[:100]})" # type: ignore
        logger.error(f"Ollama HTTPError: {error_detail}", exc_info=False)
        return None, f"Error: {error_detail}"
    except json.JSONDecodeError as je: # Error parsing the Ollama API's main response structure
        response_text_snippet = "N/A"
        if 'response' in locals() and hasattr(response, 'text'): # type: ignore
            response_text_snippet = response.text[:200] # type: ignore
        err_msg = f"Ollama main API response (outer structure) was not JSON. Response text: {response_text_snippet} (Context: {log_user_identifier})"
        logger.error(err_msg, exc_info=True)
        return None, f"Error: {err_msg}"
    except Exception as e:
        err_msg = f"Unexpected error with Ollama (Context: {log_user_identifier}): {str(e)}"
        logger.error(err_msg, exc_info=True)
        return None, f"Error: {err_msg}"