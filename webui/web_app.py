# webui/web_app.py

from flask import Flask, render_template, request, jsonify, send_from_directory, current_app, make_response, url_for
import os
import sys
import uuid
import threading # For enabling/disabling flag

project_root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)

import config
from utils import file_utils
from logger import get_logger

PYDUB_FOR_WEB_AVAILABLE = False; AudioSegment_web = None; PydubExceptions_web = None
if config.PYDUB_AVAILABLE_FOR_WEB_CONVERSION:
    try:
        from pydub import AudioSegment, exceptions as PydubExceptionsModule
        AudioSegment_web = AudioSegment; PydubExceptions_web = PydubExceptionsModule
        PYDUB_FOR_WEB_AVAILABLE = True
    except ImportError: pass

web_logger = get_logger("Iri-shka_App.WebApp")
if PYDUB_FOR_WEB_AVAILABLE: web_logger.info("Pydub imported in web_app.py.")
else: web_logger.warning("Pydub not available/enabled for web conversion in web_app.py.")

flask_app = Flask(__name__)
flask_app.main_app_components = {}

# --- WebUI Enabled/Disabled Flag ---
class WebUIEnabledFlag:
    def __init__(self, initial_status=True):
        self._enabled = initial_status
        self._lock = threading.Lock()

    def is_enabled(self):
        with self._lock:
            return self._enabled

    def set_enabled_status(self, status: bool):
        with self._lock:
            self._enabled = status
        web_logger.info(f"Web UI service routes {'ENABLED' if status else 'DISABLED (paused)'}")

WEB_UI_ENABLED_FLAG = WebUIEnabledFlag(initial_status=config.ENABLE_WEB_UI)
# --- End WebUI Enabled/Disabled Flag ---

@flask_app.before_request
def check_webui_enabled():
    # This decorator will apply to all routes except /health and static files
    if request.endpoint and request.endpoint not in ['health_check', 'static']:
        if not config.ENABLE_WEB_UI: # Master config check
            return jsonify({"error": "Web UI is administratively disabled in the main configuration."}), 503
        if not WEB_UI_ENABLED_FLAG.is_enabled():
            return jsonify({"error": "Web UI is currently paused by the administrator."}), 503


@flask_app.route('/')
def index():
    return render_template('index.html')

@flask_app.route('/health')
def health_check():
    """Simple health check endpoint for external monitoring."""
    if not config.ENABLE_WEB_UI:
        return jsonify({"status": "administratively_disabled"}), 503
    if not WEB_UI_ENABLED_FLAG.is_enabled():
        return jsonify({"status": "paused_by_user"}), 503
    return jsonify({"status": "ok", "message": "Irishka WebUI is healthy."}), 200


@flask_app.route('/process_audio', methods=['POST'])
def process_audio_route():
    web_logger.info(f"Accessed /process_audio with method: {request.method}")
    bridge = current_app.main_app_components.get('bridge')
    main_interaction_handler = current_app.main_app_components.get('main_interaction_handler')
    chat_history_ref = current_app.main_app_components.get('chat_history_ref')
    user_state_ref = current_app.main_app_components.get('user_state_ref')
    assistant_state_ref = current_app.main_app_components.get('assistant_state_ref')
    global_lock_ref = current_app.main_app_components.get('global_lock_ref')

    if not all([bridge, main_interaction_handler, chat_history_ref is not None,
                user_state_ref is not None, assistant_state_ref is not None, global_lock_ref]):
        web_logger.critical("CRITICAL: WebApp components or state refs not found in Flask app context.")
        return jsonify({"error": "Server internal configuration error"}), 500

    if 'audio_data' not in request.files:
        return jsonify({"error": "No audio data received"}), 400
    audio_file = request.files['audio_data']
    if not audio_file.filename:
        return jsonify({"error": "Received audio data with no filename"}), 400

    temp_webm_path = ""; temp_wav_path = ""
    try:
        file_utils.ensure_folder(config.WEB_UI_AUDIO_TEMP_FOLDER)
        original_filename = f"web_input_{uuid.uuid4().hex}.webm"
        temp_webm_path = os.path.join(config.WEB_UI_AUDIO_TEMP_FOLDER, original_filename)
        audio_file.save(temp_webm_path)

        if not PYDUB_FOR_WEB_AVAILABLE or not AudioSegment_web:
            return jsonify({"error": "Audio conversion service not available."}), 501

        target_wav_filename = f"web_converted_{uuid.uuid4().hex}.wav"
        temp_wav_path = os.path.join(config.WEB_UI_AUDIO_TEMP_FOLDER, target_wav_filename)
        audio_segment = AudioSegment_web.from_file(temp_webm_path)
        audio_segment = audio_segment.set_channels(1).set_frame_rate(config.INPUT_RATE)
        audio_segment.export(temp_wav_path, format="wav")

        current_chat_history_copy = []
        current_user_state_copy = {}
        current_assistant_state_copy = {}
        with global_lock_ref:
            current_chat_history_copy = list(chat_history_ref)
            current_user_state_copy = dict(user_state_ref)
            current_assistant_state_copy = dict(assistant_state_ref)

        bridge_result = bridge.process_admin_web_audio(
            input_wav_filepath=temp_wav_path,
            current_chat_history=current_chat_history_copy,
            current_user_state=current_user_state_copy,
            current_assistant_state=current_assistant_state_copy,
            global_lock=global_lock_ref
        )
        main_interaction_handler(bridge_result)
        response_data = {
            "user_transcription": bridge_result.get("user_transcription"),
            "llm_text_response": bridge_result.get("llm_text_response"),
            "audio_url": None,
            "error": bridge_result.get("error_message")
        }
        if bridge_result.get("tts_audio_filename"):
            response_data["audio_url"] = url_for('play_audio_route', filename=bridge_result["tts_audio_filename"])
        return jsonify(response_data), 200
    except PydubExceptions_web.CouldntDecodeError if PydubExceptions_web else Exception as e_decode:
        web_logger.error(f"Pydub CouldntDecodeError: {e_decode}", exc_info=True)
        return jsonify({"error": f"Server could not decode audio: {str(e_decode)}"}), 415
    except Exception as e:
        web_logger.error(f"Error in /process_audio: {e}", exc_info=True)
        return jsonify({"error": f"Server-side processing error: {str(e)}"}), 500
    finally:
        if temp_webm_path and os.path.exists(temp_webm_path):
            try: os.remove(temp_webm_path)
            except: pass
        if temp_wav_path and os.path.exists(temp_wav_path):
            try: os.remove(temp_wav_path)
            except: pass

@flask_app.route('/play_audio/<path:filename>')
def play_audio_route(filename):
    web_logger.info(f"Request to play audio file: '{filename}'")
    if ".." in filename or filename.startswith("/") or filename.startswith("\\"):
        web_logger.error(f"Invalid or potentially malicious filename requested: '{filename}'")
        return "Invalid filename", 400
    absolute_tts_serve_folder = os.path.join(project_root_dir, config.WEB_UI_TTS_SERVE_FOLDER)
    try:
        response = make_response(send_from_directory(
            absolute_tts_serve_folder, filename, as_attachment=False ))
        response.headers['Content-Type'] = 'audio/wav'
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'; response.headers['Pragma'] = 'no-cache'; response.headers['Expires'] = '0'
        return response
    except FileNotFoundError: return "Audio file not found", 404
    except Exception as e: web_logger.error(f"Error serving audio file '{filename}': {e}", exc_info=True); return "Error serving audio file", 500

@flask_app.route('/status')
def status_route():
    web_logger.debug("Received /status request")
    main_app_bridge = current_app.main_app_components.get('bridge')
    if not main_app_bridge:
        return jsonify({"error": "Server internal error (application bridge misconfigured)"}), 500
    try:
        status_data = main_app_bridge.get_system_status_for_web()
        # Add the WebUI's own operational status
        if not config.ENABLE_WEB_UI:
             status_data["webui_operational_status"] = {"text": "CFG OFF", "type": "off"}
        elif not WEB_UI_ENABLED_FLAG.is_enabled():
             status_data["webui_operational_status"] = {"text": "PAUSED", "type": "disabled"}
        else:
             status_data["webui_operational_status"] = {"text": "ACTIVE", "type": "active"}
        return jsonify(status_data)
    except Exception as e:
        web_logger.error(f"Error generating system status for web: {e}", exc_info=True)
        return jsonify({"error": f"Could not retrieve system status: {str(e)}"}), 500