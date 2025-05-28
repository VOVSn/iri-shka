from flask import Flask, render_template, request, jsonify, send_from_directory, current_app
import os
import sys
import datetime
import uuid # For unique filenames
import soundfile as sf # For saving TTS WAV files
import numpy as np

# Add project root to sys.path to allow imports from parent directory
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import config # From project root
from utils import file_utils # From project root/utils
from logger import get_logger # From project root

# Conditional Pydub import for server-side conversion
PYDUB_FOR_WEB_AVAILABLE = False
AudioSegment_web = None
if config.PYDUB_AVAILABLE_FOR_WEB_CONVERSION:
    try:
        from pydub import AudioSegment
        AudioSegment_web = AudioSegment
        PYDUB_FOR_WEB_AVAILABLE = True
    except ImportError:
        pass # Main app logger will warn if pydub is generally expected

web_logger = get_logger("Iri-shka_App.WebApp")

flask_app = Flask(__name__)

# This will be populated by main.py with a bridge object
flask_app.main_app_components = {}


@flask_app.route('/')
def index():
    return render_template('index.html')

@flask_app.route('/process_audio', methods=['POST'])
def process_audio_route():
    web_logger.info("Received /process_audio request")
    if 'audio_data' not in request.files:
        web_logger.error("No audio_data in request files")
        return jsonify({"error": "No audio data received"}), 400

    audio_file = request.files['audio_data']
    
    if not file_utils.ensure_folder(config.WEB_UI_AUDIO_TEMP_FOLDER, gui_callbacks=None):
        web_logger.error(f"Could not ensure/create web UI audio temp folder: {config.WEB_UI_AUDIO_TEMP_FOLDER}")
        return jsonify({"error": "Server configuration error (audio folder)"}), 500

    temp_input_filename = f"web_input_{uuid.uuid4().hex}.webm"
    temp_input_filepath = os.path.join(config.WEB_UI_AUDIO_TEMP_FOLDER, temp_input_filename)
    temp_wav_filename = f"web_input_whisper_{uuid.uuid4().hex}.wav"
    temp_wav_filepath = os.path.join(config.WEB_UI_AUDIO_TEMP_FOLDER, temp_wav_filename)
    audio_to_process_path = None

    try:
        audio_file.save(temp_input_filepath)
        web_logger.info(f"Saved incoming web audio to {temp_input_filepath}")

        if PYDUB_FOR_WEB_AVAILABLE and AudioSegment_web:
            try:
                audio = AudioSegment_web.from_file(temp_input_filepath)
                audio = audio.set_frame_rate(config.INPUT_RATE).set_channels(config.CHANNELS)
                audio.export(temp_wav_filepath, format="wav")
                web_logger.info(f"Converted {temp_input_filepath} to {temp_wav_filepath} for Whisper")
                audio_to_process_path = temp_wav_filepath
            except Exception as e_pydub:
                web_logger.error(f"Pydub conversion failed for {temp_input_filepath}: {e_pydub}", exc_info=True)
                return jsonify({"error": f"Audio conversion failed: {e_pydub}"}), 500
        else:
            web_logger.error("Pydub not available or not enabled for server-side audio conversion. Cannot process webm/ogg.")
            return jsonify({"error": "Server cannot process this audio format (Pydub conversion disabled/missing)"}), 500
        
        main_app_bridge = current_app.main_app_components.get('bridge')
        if not main_app_bridge:
            web_logger.error("Main application bridge not found in Flask app context.")
            return jsonify({"error": "Server internal error (app bridge)"}), 500

        # handle_web_interaction expects path to a processable audio file (WAV)
        result = main_app_bridge.handle_web_interaction(audio_to_process_path)
        web_logger.info(f"Result from main_app_bridge.handle_web_interaction: {result}")

        response_data = {
            "user_transcription": result.get("user_transcription"),
            "text_response": result.get("llm_text_response"),
            "audio_url": None,
            "error": result.get("error")
        }

        if result.get("tts_audio_filename"):
            response_data["audio_url"] = f"/play_audio/{result['tts_audio_filename']}"
        
        return jsonify(response_data)

    except Exception as e:
        web_logger.error(f"Error in /process_audio: {e}", exc_info=True)
        return jsonify({"error": f"Server error: {str(e)}"}), 500
    finally:
        if os.path.exists(temp_input_filepath):
            try: os.remove(temp_input_filepath)
            except OSError: web_logger.warning(f"Could not remove temp input file: {temp_input_filepath}")
        if audio_to_process_path and os.path.exists(audio_to_process_path): # audio_to_process_path is the WAV
            try: os.remove(audio_to_process_path)
            except OSError: web_logger.warning(f"Could not remove temp WAV file: {audio_to_process_path}")


@flask_app.route('/play_audio/<filename>')
def play_audio_route(filename):
    web_logger.info(f"Serving audio file: {filename}")
    if ".." in filename or filename.startswith("/"): # Basic security check
        web_logger.error(f"Invalid filename requested for audio playback: {filename}")
        return "Invalid filename", 400
    
    # TTS files are saved by the bridge into WEB_UI_TTS_SERVE_FOLDER
    return send_from_directory(config.WEB_UI_TTS_SERVE_FOLDER, filename, as_attachment=False)


@flask_app.route('/status')
def status_route():
    main_app_bridge = current_app.main_app_components.get('bridge')
    if not main_app_bridge:
        web_logger.error("Status request failed: Main application bridge not found.")
        return jsonify({"error": "Server internal error (app bridge)"}), 500
    
    status_data = main_app_bridge.get_system_status_for_web()
    return jsonify(status_data)

# Flask app is run from main.py