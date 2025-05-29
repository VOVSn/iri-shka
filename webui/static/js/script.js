document.addEventListener('DOMContentLoaded', () => {
    const recordButton = document.getElementById('recordButton');
    const chatDisplay = document.getElementById('chatDisplay');
    const appStatus = document.getElementById('appStatus');
    const audioPlayback = document.getElementById('audioPlayback');

    // Status elements
    const ollamaStatus = document.getElementById('ollamaStatus');
    const whisperStatus = document.getElementById('whisperStatus');
    const barkStatus = document.getElementById('barkStatus');
    const telegramStatus = document.getElementById('telegramStatus'); // Changed from webUiStatus


    let mediaRecorder;
    let audioChunks = [];
    let isRecording = false;

    // ... (Recording Logic - addMessageToChat remain the same) ...
    // --- Recording Logic ---
    recordButton.addEventListener('click', async () => {
        if (!isRecording) {
            try {
                audioPlayback.pause();
                audioPlayback.src = ""; 

                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });

                mediaRecorder.ondataavailable = event => {
                    audioChunks.push(event.data);
                };

                mediaRecorder.onstop = async () => {
                    const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                    audioChunks = [];
                    stream.getTracks().forEach(track => track.stop()); 
                    await sendAudioToServer(audioBlob);
                };

                mediaRecorder.start();
                recordButton.textContent = 'Stop Recording';
                recordButton.classList.add('recording');
                isRecording = true;
                recordButton.disabled = false; 
                addMessageToChat('Recording started...', 'system-message');

            } catch (err) {
                console.error('Error accessing microphone:', err);
                addMessageToChat(`Error accessing microphone: ${err.message}. Please grant permission.`, 'error-message');
                recordButton.disabled = false;
                recordButton.textContent = 'Start Recording';
                recordButton.classList.remove('recording');
                isRecording = false;
            }
        } else {
            if (mediaRecorder && mediaRecorder.state !== "inactive") {
                mediaRecorder.stop();
            }
            recordButton.disabled = true; 
            recordButton.textContent = 'Processing...';
            addMessageToChat('Recording stopped. Processing...', 'system-message');
            isRecording = false; 
            recordButton.classList.remove('recording');
        }
    });

    async function sendAudioToServer(audioBlob) {
        const formData = new FormData();
        formData.append('audio_data', audioBlob, 'user_audio.webm');

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
                addMessageToChat('Preparing Iri-shka\'s voice...', 'system-message');
                
                audioPlayback.oncanplaythrough = () => {
                    console.log("Audio is ready to play through.");
                    addMessageToChat('Audio ready. Attempting to play...', 'system-message');
                    const playPromise = audioPlayback.play();
                    if (playPromise !== undefined) {
                        playPromise.then(_ => {
                            console.log("Audio playback started successfully via promise.");
                            addMessageToChat("Playing Iri-shka's voice now.", 'system-message');
                        }).catch(error => {
                            console.error("Audio playback was prevented by browser:", error);
                            addMessageToChat('Autoplay prevented. Click player to play manually.', 'system-message');
                        });
                    }
                    audioPlayback.oncanplaythrough = null; 
                    audioPlayback.onerror = null; 
                };

                audioPlayback.onerror = (e) => {
                    console.error("Error loading audio source:", e, audioPlayback.error);
                    let errorDetail = "Unknown audio error";
                    if (audioPlayback.error) {
                        switch (audioPlayback.error.code) {
                            case MediaError.MEDIA_ERR_ABORTED: errorDetail = 'Playback aborted by user.'; break;
                            case MediaError.MEDIA_ERR_NETWORK: errorDetail = 'A network error caused the audio download to fail.'; break;
                            case MediaError.MEDIA_ERR_DECODE: errorDetail = 'Audio decoding error. File might be corrupt or unsupported.'; break;
                            case MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED: errorDetail = 'Audio source format not supported.'; break;
                            default: errorDetail = 'An unknown error occurred.';
                        }
                    }
                    addMessageToChat(`Error playing audio: ${errorDetail}`, 'error-message');
                    appStatus.textContent = `Audio Error: ${errorDetail}`;
                    audioPlayback.oncanplaythrough = null;
                    audioPlayback.onerror = null;
                };
                
                audioPlayback.src = data.audio_url;
                audioPlayback.load(); 
            } else if (!data.error && data.text_response) {
                 addMessageToChat("Iri-shka (text only).", 'system-message');
            }

            if(data.error) {
                addMessageToChat(`Error: ${data.error}`, 'error-message');
                appStatus.textContent = `Error: ${data.error}`;
            } else if (!data.audio_url) { 
                 appStatus.textContent = 'Ready.';
            }

        } catch (error) {
            console.error('Error sending audio:', error);
            addMessageToChat(`Error: ${error.message}`, 'error-message');
            appStatus.textContent = `Error: ${error.message}`;
        } finally {
            recordButton.disabled = false;
            if(isRecording) { 
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
        chatDisplay.scrollTop = chatDisplay.scrollHeight; 
    }


    // --- Status Update Logic ---
    function getStatusClass(statusTypeStr) {
        if (!statusTypeStr) return 'status-info'; // Default for undefined type
        const s = statusTypeStr.toLowerCase();
        // Green states
        if (["ready", "polling", "loaded", "saved", "fresh", "idle", "ok_gpu", "ok"].includes(s)) return "status-ok";
        // Yellow states
        if (["loading", "checking", "pinging", "thinking", "warn", "busy"].includes(s)) return "status-warn";
        // Red states
        if (["error", "na", "n/a", "timeout", "conn_error", "http_502", "http_other", "initfail", "unreachable", "bad_token", "net_error", "err", "no_token", "no_admin"].includes(s)) return "status-error";
        // Grey states
        if (s === "off") return "status-off";
        // Default blue for unknown or other informational states
        return "status-info";
    }

    async function fetchAndUpdateStatus() {
        try {
            const response = await fetch('/status');
            if (!response.ok) {
                console.warn('Failed to fetch status:', response.status);
                // Indicate error for all statuses if fetch fails badly
                [ollamaStatus, whisperStatus, barkStatus, telegramStatus].forEach(el => {
                    if(el) {
                        el.textContent = 'N/A (Fetch Err)';
                        el.className = `status-value ${getStatusClass('error')}`;
                    }
                });
                if (!appStatus.textContent.startsWith("Error")) { 
                    appStatus.textContent = 'Error fetching status from server.';
                }
                return;
            }
            const statusData = await response.json();

            if (statusData.ollama) {
                ollamaStatus.textContent = statusData.ollama.text || 'N/A';
                ollamaStatus.className = `status-value ${getStatusClass(statusData.ollama.type)}`;
            }

            if (statusData.whisper) {
                whisperStatus.textContent = statusData.whisper.text || 'N/A';
                whisperStatus.className = `status-value ${getStatusClass(statusData.whisper.type)}`;
            }

            if (statusData.bark) {
                barkStatus.textContent = statusData.bark.text || 'N/A';
                barkStatus.className = `status-value ${getStatusClass(statusData.bark.type)}`;
            }
            
            if (statusData.telegram) { // Changed from web_ui to telegram
                telegramStatus.textContent = statusData.telegram.text || 'N/A';
                telegramStatus.className = `status-value ${getStatusClass(statusData.telegram.type)}`;
            }


            if (!appStatus.textContent.startsWith("Error:") || appStatus.textContent.includes("fetching status") || appStatus.textContent.includes("Audio ready") || appStatus.textContent.includes("Playing Iri-shka's voice")) {
                 if (audioPlayback.paused || audioPlayback.ended || !audioPlayback.src.includes("web_tts_output")) {
                    appStatus.textContent = statusData.app_overall_status || 'Ready.';
                 }
            }

            const whisperIsActionable = statusData.whisper && (statusData.whisper.type === 'ready' || statusData.whisper.type === 'ok' || statusData.whisper.type === 'idle');
            if (!isRecording) { 
                recordButton.disabled = !whisperIsActionable;
                if (!whisperIsActionable && recordButton.textContent !== 'Processing...') {
                    recordButton.textContent = `Whisper: ${statusData.whisper ? (statusData.whisper.text || 'N/A') : 'N/A'}`;
                } else if (whisperIsActionable && recordButton.textContent !== 'Processing...') {
                    recordButton.textContent = 'Start Recording';
                }
            }

        } catch (error) {
            console.error('Error fetching status:', error);
            [ollamaStatus, whisperStatus, barkStatus, telegramStatus].forEach(el => {
                if(el) {
                    el.textContent = 'Error';
                    el.className = `status-value ${getStatusClass('error')}`;
                }
            });
             if (!appStatus.textContent.startsWith("Error:") || appStatus.textContent.includes("fetching status")) {
                appStatus.textContent = 'Error fetching status.';
            }
        }
    }

    fetchAndUpdateStatus(); 
    setInterval(fetchAndUpdateStatus, 3000);
});