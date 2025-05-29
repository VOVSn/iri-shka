# webui/web_app.py

from flask import Flask, render_template, request, jsonify, send_from_directory, current_app, make_response, url_for
import os
import sys
import uuid # For unique filenames
import tempfile # For temporary file handling

# Add project root to sys.path to allow imports from parent directory
project_root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)

import config # From project root
from utils import file_utils # From project root/utils
from logger import get_logger # From project root

# --- Pydub for Web Audio Conversion ---
PYDUB_FOR_WEB_AVAILABLE = False
AudioSegment_web = None
PydubExceptions_web = None
if config.PYDUB_AVAILABLE_FOR_WEB_CONVERSION:
    try:
        from pydub import AudioSegment, exceptions as PydubExceptionsModule
        AudioSegment_web = AudioSegment
        PydubExceptions_web = PydubExceptionsModule
        PYDUB_FOR_WEB_AVAILABLE = True
    except ImportError:
        pass # Logger will be initialized after this block

web_logger = get_logger("Iri-shka_App.WebApp")

if PYDUB_FOR_WEB_AVAILABLE:
    web_logger.info("Pydub successfully imported in web_app.py for audio conversion.")
else:
    web_logger.warning("Pydub not found or not enabled for web conversion in web_app.py (PYDUB_AVAILABLE_FOR_WEB_CONVERSION=False or import failed). Audio processing will rely on direct WAV input if Pydub is unavailable.")

flask_app = Flask(__name__)
flask_app.main_app_components = {}


@flask_app.route('/')
def index():
    web_logger.debug(f"Serving index.html from template folder: {flask_app.template_folder}")
    return render_template('index.html')

@flask_app.route('/process_audio', methods=['POST'])
def process_audio_route():
    web_logger.info(f"Accessed /process_audio with method: {request.method}")
    bridge = current_app.main_app_components.get('bridge')
    if not bridge:
        web_logger.critical("CRITICAL: WebAppBridge not found in Flask app context.")
        return jsonify({"error": "Server internal configuration error (no bridge)"}), 500

    if 'audio_data' not in request.files:
        web_logger.error("No 'audio_data' in request.files for /process_audio")
        return jsonify({"error": "No audio data received in 'audio_data' field"}), 400

    audio_file = request.files['audio_data']
    if not audio_file.filename:
        web_logger.error("Received 'audio_data' but filename is empty.")
        return jsonify({"error": "Received audio data with no filename"}), 400

    # --- Save and Convert Audio ---
    temp_webm_path = ""
    temp_wav_path = ""
    
    try:
        # Ensure the temporary folder for incoming web audio exists
        file_utils.ensure_folder(config.WEB_UI_AUDIO_TEMP_FOLDER)
        
        # Save the original webm file
        original_filename = f"web_input_{uuid.uuid4().hex}.webm" # Or use audio_file.filename if you trust it
        temp_webm_path = os.path.join(config.WEB_UI_AUDIO_TEMP_FOLDER, original_filename)
        audio_file.save(temp_webm_path)
        web_logger.info(f"Saved incoming web audio to: {temp_webm_path}")

        if not PYDUB_FOR_WEB_AVAILABLE or not AudioSegment_web:
            web_logger.error("Pydub is not available for audio conversion. Cannot process non-WAV audio.")
            return jsonify({"error": "Audio conversion service (Pydub) not available on server."}), 501 # Not Implemented

        # Convert to WAV using Pydub
        target_wav_filename = f"web_converted_{uuid.uuid4().hex}.wav"
        temp_wav_path = os.path.join(config.WEB_UI_AUDIO_TEMP_FOLDER, target_wav_filename)

        web_logger.info(f"Attempting to convert {temp_webm_path} to WAV ({temp_wav_path}) using Pydub...")
        audio_segment = AudioSegment_web.from_file(temp_webm_path) # Let Pydub auto-detect format
        audio_segment = audio_segment.set_channels(1)
        audio_segment = audio_segment.set_frame_rate(config.INPUT_RATE) # Use admin input rate
        audio_segment.export(temp_wav_path, format="wav")
        web_logger.info(f"Successfully converted web audio to WAV: {temp_wav_path} (1ch, {config.INPUT_RATE}Hz)")

        # --- Call the Bridge to Process Admin Web Audio ---
        # The bridge method `process_admin_web_audio` will be implemented in main.py
        # It will handle STT, LLM (with full admin context), and TTS
        if not hasattr(bridge, 'process_admin_web_audio'):
            web_logger.critical("WebAppBridge is missing the 'process_admin_web_audio' method.")
            return jsonify({"error": "Server internal configuration error (bridge method missing)"}), 500

        web_logger.debug(f"Calling bridge.process_admin_web_audio with: {temp_wav_path}")
        interaction_result = bridge.process_admin_web_audio(input_wav_filepath=temp_wav_path)
        web_logger.info(f"Bridge processing complete. Result for web: {interaction_result}")

        # Prepare response for frontend
        response_data = {
            "user_transcription": interaction_result.get("user_transcription"),
            "llm_text_response": interaction_result.get("llm_text_response"),
            "audio_url": None,
            "error": interaction_result.get("error")
        }

        if interaction_result.get("tts_audio_filename"):
            # The filename is relative to WEB_UI_TTS_SERVE_FOLDER
            # url_for will generate the correct path to the /play_audio route
            response_data["audio_url"] = url_for('play_audio_route', filename=interaction_result["tts_audio_filename"])
            web_logger.info(f"Generated TTS audio URL: {response_data['audio_url']}")
        
        return jsonify(response_data), 200

    except PydubExceptions_web.CouldntDecodeError if PydubExceptions_web else Exception as e_decode: # type: ignore
        web_logger.error(f"Pydub CouldntDecodeError processing {audio_file.filename}: {e_decode}", exc_info=True)
        return jsonify({"error": f"Server could not decode audio: {str(e_decode)}"}), 415 # Unsupported Media Type
    except Exception as e:
        web_logger.error(f"Error in /process_audio: {e}", exc_info=True)
        return jsonify({"error": f"Server-side processing error: {str(e)}"}), 500
    finally:
        # Clean up temporary files
        if temp_webm_path and os.path.exists(temp_webm_path):
            try: os.remove(temp_webm_path); web_logger.debug(f"Removed temp webm: {temp_webm_path}")
            except OSError as e_rem_webm: web_logger.warning(f"Failed to remove temp webm {temp_webm_path}: {e_rem_webm}")
        if temp_wav_path and os.path.exists(temp_wav_path):
            try: os.remove(temp_wav_path); web_logger.debug(f"Removed temp wav: {temp_wav_path}")
            except OSError as e_rem_wav: web_logger.warning(f"Failed to remove temp wav {temp_wav_path}: {e_rem_wav}")


@flask_app.route('/play_audio/<path:filename>')
def play_audio_route(filename):
    web_logger.info(f"Request to play audio file: '{filename}'")
    if ".." in filename or filename.startswith("/") or filename.startswith("\\"):
        web_logger.error(f"Invalid or potentially malicious filename requested: '{filename}'")
        return "Invalid filename", 400
    
    absolute_tts_serve_folder = os.path.join(project_root_dir, config.WEB_UI_TTS_SERVE_FOLDER)
    web_logger.debug(f"Attempting to serve '{filename}' from absolute directory: '{absolute_tts_serve_folder}'")

    try:
        response = make_response(send_from_directory(
            absolute_tts_serve_folder, 
            filename, 
            as_attachment=False
        ))
        response.headers['Content-Type'] = 'audio/wav'
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    except FileNotFoundError:
        web_logger.error(f"Audio file not found: '{filename}' in directory '{absolute_tts_serve_folder}'")
        return "Audio file not found", 404
    except Exception as e:
        web_logger.error(f"Error serving audio file '{filename}': {e}", exc_info=True)
        return "Error serving audio file", 500


@flask_app.route('/status')
def status_route():
    web_logger.debug("Received /status request")
    main_app_bridge = current_app.main_app_components.get('bridge')
    if not main_app_bridge:
        web_logger.critical("Status request failed: Main application bridge not found.")
        return jsonify({"error": "Server internal error (application bridge misconfigured)"}), 500
    
    try:
        status_data = main_app_bridge.get_system_status_for_web()
        return jsonify(status_data)
    except Exception as e:
        web_logger.error(f"Error generating system status for web: {e}", exc_info=True)
        return jsonify({"error": f"Could not retrieve system status: {str(e)}"}), 500

# Flask app is run from main.py