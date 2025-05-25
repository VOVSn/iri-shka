# utils/ollama_handler.py
import requests
import json
from datetime import datetime, timezone, timedelta
import config

# Assuming logger.py is in the same 'utils' directory
from logger import get_logger

logger = get_logger(__name__)

def check_ollama_server_and_model():
    """
    Checks if the Ollama server is reachable and the configured model can respond.
    Returns: (bool: is_ready, str: status_message_for_log)
    """
    payload = {
        "model": config.OLLAMA_MODEL_NAME,
        "prompt": config.OLLAMA_PING_PROMPT,
        "stream": False
    }
    logger.info(f"Pinging Ollama server with model {config.OLLAMA_MODEL_NAME} at {config.OLLAMA_API_URL}...")

    try:
        response = requests.post(config.OLLAMA_API_URL, json=payload, timeout=config.OLLAMA_PING_TIMEOUT)
        response.raise_for_status() # Will raise HTTPError for bad responses (4xx or 5xx)

        response_data = response.json()
        if response_data and response_data.get("response"):
            # Check if the response is the expected "ready"
            # This assumes OLLAMA_PING_PROMPT asks for a specific, simple response
            # For "You are an AI assistant. Respond with a single word: 'ready'.", we expect "ready\n" or similar.
            # Actual response might include newline, so strip and lower for comparison.
            actual_response_content = response_data.get("response", "").strip().lower()
            # if "ready" in actual_response_content: # Be a bit flexible
            msg = f"Ping successful. Model {config.OLLAMA_MODEL_NAME} responded: '{actual_response_content[:50]}...'"
            logger.info(msg)
            return True, msg
            # else:
            #     msg = f"Ping to {config.OLLAMA_MODEL_NAME} received OK, but response content was not as expected: '{actual_response_content[:50]}...'"
            #     logger.warning(msg)
            #     return False, msg # Or True if any response is okay for a ping. For now, let's assume any response is okay.
        else:
            msg = f"Ping to {config.OLLAMA_MODEL_NAME} received HTTP OK, but response content was unexpected or empty."
            logger.warning(msg)
            return False, msg

    except requests.exceptions.Timeout:
        msg = f"Timeout ({config.OLLAMA_PING_TIMEOUT}s) connecting to Ollama at {config.OLLAMA_API_URL}."
        logger.error(msg, exc_info=False) # No need for full trace for a timeout
        return False, msg
    except requests.exceptions.ConnectionError:
        msg = f"Connection Error with Ollama server (Check {config.OLLAMA_API_URL}). Server might be down."
        logger.error(msg, exc_info=False) # No need for full trace for a connection error
        return False, msg
    except requests.exceptions.HTTPError as e:
        error_detail = f"HTTP Error {e.response.status_code}"
        try:
            # Attempt to get more specific error from Ollama's JSON response
            error_content = e.response.json()
            if 'error' in error_content:
                 error_detail += f" - {error_content['error']}"
            elif 'detail' in error_content: # Some Ollama errors use 'detail'
                 error_detail += f" - {error_content['detail']}"
            else: # Fallback to raw text if expected keys aren't there
                error_detail += f" - (Raw: {e.response.text[:100]})"
        except json.JSONDecodeError: # If the error response itself isn't JSON
            error_detail += f" - (Non-JSON error response: {e.response.text[:100]})"

        msg = f"Ollama HTTPError during ping: {error_detail}"
        # For 404 on model, or 500s, this is an error. For 503 (service unavailable), also an error.
        logger.error(msg, exc_info=False) # Log specific HTTP error without full trace usually
        return False, msg
    except json.JSONDecodeError as je:
        msg = f"Invalid JSON in Ollama ping response. Response text: {response.text[:200] if 'response' in locals() and hasattr(response, 'text') else 'N/A'}"
        logger.error(msg, exc_info=True) # Full trace for unexpected JSON
        return False, msg
    except Exception as e:
        msg = f"Unexpected Error during Ollama ping: {str(e)}"
        logger.error(msg, exc_info=True) # Full trace for truly unexpected errors
        return False, msg

def call_ollama_for_chat_response(transcribed_text, chat_history, user_state, assistant_state,
                                  language_instruction_for_llm="", gui_callbacks=None):
    recent_history = chat_history[-(config.MAX_HISTORY_TURNS):]
    chat_log_parts = []
    for turn in recent_history:
        chat_log_parts.append(f"User: {turn['user']}")
        if turn.get('assistant'):
             chat_log_parts.append(f"Iri-shka: {turn['assistant']}")
    chat_log_string = "\n".join(chat_log_parts)

    target_timezone = timezone(timedelta(hours=config.TIMEZONE_OFFSET_HOURS))
    now_in_target_timezone = datetime.now(target_timezone)
    current_time_string = now_in_target_timezone.strftime("%A, %Y-%m-%d %H:%M:%S")

    prompt_for_ollama = config.OLLAMA_PROMPT_TEMPLATE.format(
        language_instruction=language_instruction_for_llm,
        current_time_string=current_time_string,
        history_len=len(recent_history),
        chat_log_string=chat_log_string,
        user_state_string=json.dumps(user_state, indent=2, ensure_ascii=False),
        assistant_state_string=json.dumps(assistant_state, indent=2, ensure_ascii=False),
        last_transcribed_text=transcribed_text
    )

    payload = {
        "model": config.OLLAMA_MODEL_NAME,
        "prompt": prompt_for_ollama,
        "format": "json",
        "stream": False
    }

    if gui_callbacks and 'status_update' in gui_callbacks:
        gui_callbacks['status_update'](f"Querying {config.OLLAMA_MODEL_NAME}...")

    logger.info(f"Sending request to Ollama ({config.OLLAMA_MODEL_NAME}). User said '{transcribed_text[:100]}...'")
    # Limit logged prompt length for brevity in logs, use DEBUG for full prompt if needed.
    logger.debug(f"Full Ollama Prompt for model {config.OLLAMA_MODEL_NAME}:\n{prompt_for_ollama}")

    try:
        response = requests.post(config.OLLAMA_API_URL, json=payload, timeout=config.OLLAMA_REQUEST_TIMEOUT)
        response.raise_for_status()

        # The actual JSON we want is nested inside the "response" field of Ollama's JSON output when format="json"
        response_json_str = response.json().get("response", "") # This should be a string containing JSON
        if not response_json_str:
            err_msg = "Ollama response content (the 'response' field value) was empty."
            logger.error(err_msg)
            return None, f"Error: {err_msg}"

        try:
            ollama_json_output = json.loads(response_json_str) # Parse the string from "response" field
        except json.JSONDecodeError as je:
            err_msg = f"Ollama's 'response' field content was not valid JSON: {je}. Received text: {response_json_str[:200]}..."
            logger.error(err_msg, exc_info=True)
            return None, f"Error: Ollama returned invalid JSON in 'response' field."

        # Validate the structure of the parsed JSON from "response" field
        required_keys = ["answer_to_user", "updated_user_state", "updated_assistant_state"]
        if not all(k in ollama_json_output for k in required_keys):
            missing_keys = [k for k in required_keys if k not in ollama_json_output]
            err_msg = f"Ollama JSON (from 'response' field) missing required keys: {missing_keys}. Received keys: {list(ollama_json_output.keys())}"
            logger.error(err_msg)
            return None, f"Error: {err_msg}"

        logger.info(f"Ollama ({config.OLLAMA_MODEL_NAME}) JSON response received and parsed successfully.")
        return ollama_json_output, None

    except requests.exceptions.Timeout:
        err_msg = f"Ollama request timed out ({config.OLLAMA_REQUEST_TIMEOUT}s) to {config.OLLAMA_API_URL}."
        logger.error(err_msg, exc_info=False)
        return None, f"Error: {err_msg}"
    except requests.exceptions.ConnectionError:
        err_msg = f"Could not connect to Ollama (Check {config.OLLAMA_API_URL})."
        logger.error(err_msg, exc_info=False)
        return None, f"Error: {err_msg}"
    except requests.exceptions.HTTPError as e:
        error_detail = f"LLM API HTTP Error {e.response.status_code}"
        try:
            error_content = e.response.json()
            error_detail += f" - {error_content.get('error', error_content.get('detail', e.response.text[:100]))}"
        except json.JSONDecodeError:
            error_detail += f" - (Non-JSON error response: {e.response.text[:100]})"
        logger.error(f"Ollama HTTPError: {error_detail}", exc_info=False)
        return None, f"Error: {error_detail}"
    except json.JSONDecodeError as je: # This would catch if the main response from Ollama (not the nested one) is not JSON
        err_msg = f"Ollama main response (outer structure) was not JSON. Response text: {response.text[:200] if 'response' in locals() and hasattr(response, 'text') else 'N/A'}"
        logger.error(err_msg, exc_info=True)
        return None, f"Error: {err_msg}"
    except Exception as e:
        err_msg = f"Unexpected error with Ollama: {str(e)}"
        logger.error(err_msg, exc_info=True)
        return None, f"Error: {err_msg}"