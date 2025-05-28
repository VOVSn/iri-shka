document.addEventListener('DOMContentLoaded', () => {
    const recordButton = document.getElementById('recordButton');
    const chatDisplay = document.getElementById('chatDisplay');
    const appStatus = document.getElementById('appStatus');
    const audioPlayback = document.getElementById('audioPlayback');

    // Status elements
    const ollamaStatus = document.getElementById('ollamaStatus');
    const whisperStatus = document.getElementById('whisperStatus');
    const barkStatus = document.getElementById('barkStatus');
    const webUiStatus = document.getElementById('webUiStatus');


    let mediaRecorder;
    let audioChunks = [];
    let isRecording = false;

    // --- Recording Logic ---
    recordButton.addEventListener('click', async () => {
        if (!isRecording) {
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                mediaRecorder = new MediaRecorder(stream);

                mediaRecorder.ondataavailable = event => {
                    audioChunks.push(event.data);
                };

                mediaRecorder.onstop = async () => {
                    const audioBlob = new Blob(audioChunks, { type: 'audio/webm' }); // or 'audio/ogg' depending on browser
                    audioChunks = [];
                    stream.getTracks().forEach(track => track.stop()); // Stop microphone access
                    await sendAudioToServer(audioBlob);
                    recordButton.disabled = false;
                    recordButton.textContent = 'Start Recording';
                    recordButton.classList.remove('recording');
                    isRecording = false;
                };

                mediaRecorder.start();
                recordButton.textContent = 'Stop Recording';
                recordButton.classList.add('recording');
                isRecording = true;
                recordButton.disabled = false; // Re-enable in case it was disabled during processing
                addMessageToChat('Recording started...', 'system-message');

            } catch (err) {
                console.error('Error accessing microphone:', err);
                addMessageToChat('Error accessing microphone. Please grant permission.', 'error-message');
                recordButton.disabled = false;
                recordButton.textContent = 'Start Recording';
                isRecording = false;
            }
        } else {
            mediaRecorder.stop();
            recordButton.disabled = true; // Disable until processing is done
            recordButton.textContent = 'Processing...';
            addMessageToChat('Recording stopped. Processing...', 'system-message');
        }
    });

    async function sendAudioToServer(audioBlob) {
        const formData = new FormData();
        formData.append('audio_data', audioBlob, 'user_audio.webm'); // Filename is optional but good practice

        try {
            appStatus.textContent = 'Sending audio to server...';
            const response = await fetch('/process_audio', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ detail: "Server error, non-JSON response" }));
                throw new Error(`Server error: ${response.status} - ${errorData.detail || 'Unknown error'}`);
            }

            const data = await response.json();
            appStatus.textContent = 'Received response from server.';

            if (data.user_transcription) {
                 addMessageToChat(`You: ${data.user_transcription}`, 'user-message');
            }
            if (data.text_response) {
                addMessageToChat(`Iri-shka: ${data.text_response}`, 'assistant-message');
            }
            if (data.audio_url) {
                audioPlayback.src = data.audio_url;
                audioPlayback.play().catch(e => console.error("Error playing audio:", e));
                addMessageToChat('Playing Iri-shka\'s voice...', 'system-message');
            }
            if(data.error) {
                addMessageToChat(`Error: ${data.error}`, 'error-message');
                appStatus.textContent = `Error: ${data.error}`;
            } else {
                 appStatus.textContent = 'Ready.';
            }

        } catch (error) {
            console.error('Error sending audio:', error);
            addMessageToChat(`Error: ${error.message}`, 'error-message');
            appStatus.textContent = `Error: ${error.message}`;
        } finally {
            recordButton.disabled = false;
            if(isRecording) { // Should not happen if onstop is used right, but a safeguard
                 recordButton.textContent = 'Stop Recording';
                 recordButton.classList.add('recording');
            } else {
                 recordButton.textContent = 'Start Recording';
                 recordButton.classList.remove('recording');
            }
        }
    }

    function addMessageToChat(message, type) {
        const p = document.createElement('p');
        p.textContent = message;
        p.className = type;
        chatDisplay.appendChild(p);
        chatDisplay.scrollTop = chatDisplay.scrollHeight; // Scroll to bottom
    }

    // --- Status Update Logic ---
    function getStatusClass(statusTypeStr) {
        if (!statusTypeStr) return 'status-info';
        const s = statusTypeStr.toLowerCase();
        if (["ready", "polling", "loaded", "saved", "fresh", "idle", "ok_gpu", "ok"].includes(s)) return "status-ok";
        if (["loading", "checking", "pinging", "thinking", "warn"].includes(s)) return "status-warn";
        if (["error", "na", "n/a", "timeout", "conn_error", "http_502", "http_other", "initfail", "unreachable", "bad_token", "net_error", "err"].includes(s)) return "status-error";
        if (s === "off") return "status-off";
        return "status-info";
    }

    async function fetchAndUpdateStatus() {
        try {
            const response = await fetch('/status');
            if (!response.ok) {
                console.warn('Failed to fetch status:', response.status);
                webUiStatus.textContent = 'N/A';
                webUiStatus.className = `status-value ${getStatusClass('error')}`;
                return;
            }
            const statusData = await response.json();

            ollamaStatus.textContent = statusData.ollama.text || 'N/A';
            ollamaStatus.className = `status-value ${getStatusClass(statusData.ollama.type)}`;

            whisperStatus.textContent = statusData.whisper.text || 'N/A';
            whisperStatus.className = `status-value ${getStatusClass(statusData.whisper.type)}`;

            barkStatus.textContent = statusData.bark.text || 'N/A';
            barkStatus.className = `status-value ${getStatusClass(statusData.bark.type)}`;
            
            webUiStatus.textContent = statusData.web_ui.text || 'OK';
            webUiStatus.className = `status-value ${getStatusClass(statusData.web_ui.type)}`;

            appStatus.textContent = statusData.app_overall_status || 'Ready.';

            // Enable/disable record button based on Whisper status
            recordButton.disabled = !(statusData.whisper.type === 'ready' || statusData.whisper.type === 'ok');


        } catch (error) {
            console.error('Error fetching status:', error);
            webUiStatus.textContent = 'Error';
            webUiStatus.className = `status-value ${getStatusClass('error')}`;
            appStatus.textContent = 'Error fetching status.';
        }
    }

    fetchAndUpdateStatus(); // Initial status fetch
    setInterval(fetchAndUpdateStatus, 5000); // Poll every 5 seconds
});