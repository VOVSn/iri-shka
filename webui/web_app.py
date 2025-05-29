# webui/web_app.py

from flask import Flask, render_template, request, jsonify, send_from_directory, current_app, make_response
import os
import sys
import datetime # Not strictly needed here anymore as main.py handles time for LLM
import uuid # For unique filenames

# Add project root to sys.path to allow imports from parent directory
# This needs to be at the very top before other project imports
project_root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)

import config # From project root
from utils import file_utils # From project root/utils
from logger import get_logger # From project root

# --- Pydub for Web Audio Conversion (Optional but Recommended) ---
PYDUB_FOR_WEB_AVAILABLE = False
AudioSegment_web = None # To store the pydub AudioSegment class
if config.PYDUB_AVAILABLE_FOR_WEB_CONVERSION:
    try:
        from pydub import AudioSegment
        AudioSegment_web = AudioSegment
        PYDUB_FOR_WEB_AVAILABLE = True
        # No need to log success here, main logger or bridge can handle it
    except ImportError:
        # Logger for web_app will be initialized after this block
        # print("Warning: Pydub not found or not enabled for web conversion in web_app.py.")
        pass
# --- End Pydub Setup ---

web_logger = get_logger("Iri-shka_App.WebApp") # Initialize logger after path setup

flask_app = Flask(__name__)

# This dictionary will be populated by main.py with the WebAppBridge instance
flask_app.main_app_components = {}


@flask_app.route('/')
def index():
    """Serves the main HTML page for the Web UI."""
    web_logger.debug(f"Serving index.html from template folder: {flask_app.template_folder}")
    return render_template('index.html')

@flask_app.route('/process_audio', methods=['POST'])
def process_audio_route():
    """
    Handles audio data uploaded from the web UI.
    It saves the audio, converts it to WAV (if Pydub is available),
    passes it to the main application bridge for STT, LLM, and TTS,
    and returns the results as JSON.
    """
    web_logger.info("Received /process_audio request")
    if 'audio_data' not in request.files:
        web_logger.error("No 'audio_data' key in request.files")
        return jsonify({"error": "No audio data part in the request"}), 400

    audio_file = request.files['audio_data']
    if audio_file.filename == '':
        web_logger.error("No selected file in 'audio_data' part")
        return jsonify({"error": "No selected audio file"}), 400

    # Ensure the temporary folder for web audio input/conversion exists
    if not file_utils.ensure_folder(config.WEB_UI_AUDIO_TEMP_FOLDER, gui_callbacks=None):
        web_logger.critical(f"Could not ensure/create web UI audio temp folder: {config.WEB_UI_AUDIO_TEMP_FOLDER}")
        return jsonify({"error": "Server configuration error (audio temp folder)"}), 500

    # Use a unique filename for the initially received audio (e.g., webm)
    # Mimetype can be checked from audio_file.mimetype if needed for more robust naming
    # For now, assuming webm or ogg based on common browser MediaRecorder outputs
    original_extension = ".webm" # Default
    if audio_file.mimetype:
        if "webm" in audio_file.mimetype: original_extension = ".webm"
        elif "ogg" in audio_file.mimetype: original_extension = ".ogg"
        # Add more if your browser records in other formats
    
    temp_input_filename = f"web_input_{uuid.uuid4().hex}{original_extension}"
    temp_input_filepath = os.path.join(config.WEB_UI_AUDIO_TEMP_FOLDER, temp_input_filename)
    
    # Filename for the WAV file that Whisper will process
    temp_wav_filename = f"web_input_whisper_{uuid.uuid4().hex}.wav"
    temp_wav_filepath = os.path.join(config.WEB_UI_AUDIO_TEMP_FOLDER, temp_wav_filename)
    
    audio_to_process_path = None # This will be the path to the WAV file for Whisper

    try:
        audio_file.save(temp_input_filepath)
        web_logger.info(f"Saved incoming web audio (mimetype: {audio_file.mimetype}) to {temp_input_filepath}")

        # --- Convert to WAV using Pydub if available and enabled ---
        if PYDUB_FOR_WEB_AVAILABLE and AudioSegment_web:
            web_logger.info(f"Attempting Pydub conversion from '{temp_input_filepath}' to WAV.")
            try:
                audio = AudioSegment_web.from_file(temp_input_filepath) # Pydub usually infers format
                audio = audio.set_frame_rate(config.INPUT_RATE).set_channels(config.CHANNELS)
                audio.export(temp_wav_filepath, format="wav")
                web_logger.info(f"Successfully converted to WAV: {temp_wav_filepath}")
                audio_to_process_path = temp_wav_filepath
            except Exception as e_pydub:
                web_logger.error(f"Pydub conversion failed for '{temp_input_filepath}': {e_pydub}", exc_info=True)
                error_message = f"Audio conversion failed server-side. Error: {str(e_pydub)[:100]}"
                if "ffmpeg" in str(e_pydub).lower() or "couldn't find" in str(e_pydub).lower() :
                    error_message += " (This might be due to FFmpeg not being installed or not in system PATH.)"
                return jsonify({"error": error_message}), 500
        else:
            # If Pydub is not available/enabled, we cannot reliably convert.
            # This is a critical issue as Whisper typically expects WAV.
            web_logger.error("Pydub audio conversion is not available or not enabled in config. Cannot process non-WAV web audio reliably.")
            return jsonify({"error": "Server audio processing error: Audio conversion module (Pydub) is not available or FFmpeg is missing. Please check server configuration."}), 500
        
        # --- Pass the WAV audio to the main application bridge ---
        main_app_bridge = current_app.main_app_components.get('bridge')
        if not main_app_bridge:
            web_logger.critical("Main application bridge not found in Flask app context. This is a severe configuration issue.")
            return jsonify({"error": "Server internal error (application bridge misconfigured)"}), 500

        # The bridge's handle_web_interaction method performs STT, LLM, and TTS (saving audio)
        result_from_bridge = main_app_bridge.handle_web_interaction(audio_to_process_path)
        web_logger.info(f"Result from main_app_bridge.handle_web_interaction: { {k: (str(v)[:70] + '...' if isinstance(v, str) and len(v) > 70 else v) for k,v in result_from_bridge.items()} }") # Log snippet

        # Prepare the JSON response for the web UI
        response_data_for_ui = {
            "user_transcription": result_from_bridge.get("user_transcription"),
            "text_response": result_from_bridge.get("llm_text_response"),
            "audio_url": None, # Will be populated if TTS was successful
            "error": result_from_bridge.get("error") # Any error from the bridge processing
        }

        if result_from_bridge.get("tts_audio_filename"):
            # The filename is relative to WEB_UI_TTS_SERVE_FOLDER as saved by the bridge
            response_data_for_ui["audio_url"] = f"/play_audio/{result_from_bridge['tts_audio_filename']}"
        
        return jsonify(response_data_for_ui)

    except Exception as e:
        web_logger.error(f"Unexpected error in /process_audio route: {e}", exc_info=True)
        return jsonify({"error": f"An unexpected server error occurred: {str(e)}"}), 500
    finally:
        # --- Clean up temporary files ---
        if os.path.exists(temp_input_filepath):
            try:
                os.remove(temp_input_filepath)
                web_logger.debug(f"Cleaned up temporary input file: {temp_input_filepath}")
            except OSError as e_rem_in:
                web_logger.warning(f"Could not remove temporary input file '{temp_input_filepath}': {e_rem_in}")
        
        # audio_to_process_path is the WAV file path if conversion was successful
        if audio_to_process_path and os.path.exists(audio_to_process_path): 
            try:
                os.remove(audio_to_process_path)
                web_logger.debug(f"Cleaned up temporary WAV file: {audio_to_process_path}")
            except OSError as e_rem_wav:
                web_logger.warning(f"Could not remove temporary WAV file '{audio_to_process_path}': {e_rem_wav}")


@flask_app.route('/play_audio/<path:filename>') # Using <path:filename> to handle potential subdirs if ever needed
def play_audio_route(filename):
    """Serves the TTS audio files generated by the application."""
    web_logger.info(f"Request to play audio file: '{filename}'")

    # Basic security: Prevent directory traversal.
    # os.path.normpath and checking if it starts with the intended base can be more robust,
    # but for simple flat filenames, this is a basic check.
    if ".." in filename or filename.startswith("/") or filename.startswith("\\"):
        web_logger.error(f"Invalid or potentially malicious filename requested for audio playback: '{filename}'")
        return "Invalid filename", 400
    
    # Construct the absolute path to the directory containing the TTS files
    # config.WEB_UI_TTS_SERVE_FOLDER is relative to project_root_dir
    absolute_tts_serve_folder = os.path.join(project_root_dir, config.WEB_UI_TTS_SERVE_FOLDER)
    web_logger.debug(f"Attempting to serve '{filename}' from absolute directory: '{absolute_tts_serve_folder}'")

    try:
        # send_from_directory handles security aspects like ensuring the path is within the directory.
        response = make_response(send_from_directory(
            absolute_tts_serve_folder, 
            filename, 
            as_attachment=False # Serve inline for the <audio> tag
        ))
        # Explicitly set Content-Type for WAV files, good practice.
        response.headers['Content-Type'] = 'audio/wav'
        # Advise browser not to cache, useful for development or if filenames could be reused.
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
    """Provides the status of various application components to the web UI."""
    web_logger.debug("Received /status request")
    main_app_bridge = current_app.main_app_components.get('bridge')
    if not main_app_bridge:
        web_logger.critical("Status request failed: Main application bridge not found. This is a severe configuration issue.")
        return jsonify({"error": "Server internal error (application bridge misconfigured)"}), 500
    
    try:
        status_data = main_app_bridge.get_system_status_for_web()
        return jsonify(status_data)
    except Exception as e:
        web_logger.error(f"Error generating system status for web: {e}", exc_info=True)
        return jsonify({"error": f"Could not retrieve system status: {str(e)}"}), 500

# The Flask application is run from main.py, not here.
# if __name__ == '__main__':
#     # This block is for standalone testing of web_app.py, which is not its primary run mode.
#     web_logger.info("Running web_app.py in standalone mode for testing (not recommended for production).")
#     # For standalone test, we need to mock the bridge or main_app_components
#     class MockBridge:
#         def handle_web_interaction(self, wav_path): return {"user_transcription":"test tx", "llm_text_response":"test llm", "tts_audio_filename":"mock.wav", "error":None}
#         def get_system_status_for_web(self): return {"ollama":{"text":"Mock OK", "type":"ready"}, "whisper":{"text":"Mock OK", "type":"ready"}, "bark":{"text":"Mock OK", "type":"ready"}, "web_ui":{"text":"OK","type":"ready"}, "app_overall_status":"Mock Mode"}
    
#     if not os.path.exists(config.WEB_UI_AUDIO_TEMP_FOLDER): os.makedirs(config.WEB_UI_AUDIO_TEMP_FOLDER)
#     if not os.path.exists(config.WEB_UI_TTS_SERVE_FOLDER): os.makedirs(config.WEB_UI_TTS_SERVE_FOLDER)
#     # Create a dummy mock.wav for standalone testing of /play_audio
#     if not os.path.exists(os.path.join(project_root_dir, config.WEB_UI_TTS_SERVE_FOLDER, "mock.wav")):
#         try:
#             import soundfile as sf_mock
#             import numpy as np_mock
#             sf_mock.write(os.path.join(project_root_dir, config.WEB_UI_TTS_SERVE_FOLDER, "mock.wav"), np_mock.zeros(1000), 16000, subtype='PCM_16')
#         except Exception as e_mock_wav:
#             web_logger.warning(f"Could not create mock.wav for standalone test: {e_mock_wav}")

#     flask_app.main_app_components['bridge'] = MockBridge()
#     flask_app.run(host='0.0.0.0', port=config.WEB_UI_PORT, debug=True)