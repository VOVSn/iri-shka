document.addEventListener('DOMContentLoaded', () => {
    // Intercom UI Elements
    const recordButton = document.getElementById('recordButton');
    const playIrishkaButton = document.getElementById('playIrishkaButton');
    const chatScreen = document.getElementById('chatScreen');
    const recordingLedIndicator = document.getElementById('recordingLedIndicator');
    const appStatusText = document.getElementById('appStatusText'); 
    const playIrishkaIcon = document.getElementById('playIrishkaIcon');

    // Status LEDs
    const ollamaLed = document.getElementById('ollamaLed');
    const whisperLed = document.getElementById('whisperLed');
    const barkLed = document.getElementById('barkLed');
    const telegramLed = document.getElementById('telegramLed');

    const irishkaAudioPlayback = document.getElementById('irishkaAudioPlayback');

    let mediaRecorder;
    let audioChunks = [];
    let isRecording = false;
    let isPlayingIrishka = false;
    let currentIrishkaAudioURL = null;
    let typingInterval = null; 

    function init() {
        recordButton.addEventListener('click', toggleRecording);
        playIrishkaButton.addEventListener('click', toggleIrishkaPlayback);
        
        irishkaAudioPlayback.onplay = () => {
            isPlayingIrishka = true;
            playIrishkaButton.classList.add('playing');
            playIrishkaIcon.innerHTML = '<div class="pause-icon"><div class="pause-bar"></div><div class="pause-bar"></div></div>';
            appStatusText.textContent = "PLAYING IRISHKA...";
        };
        irishkaAudioPlayback.onpause = () => { 
            isPlayingIrishka = false;
            playIrishkaButton.classList.remove('playing');
            playIrishkaIcon.innerHTML = ''; 
            playIrishkaIcon.className = 'play-icon'; 
            if (appStatusText.textContent === "PLAYING IRISHKA...") { 
                appStatusText.textContent = "READY"; // Or "RESPONSE READY" if audio is still available
            }
        };
        irishkaAudioPlayback.onended = () => {
            // onpause handles visual state reset
            console.log("Irishka audio finished playing.");
            if (appStatusText.textContent === "PLAYING IRISHKA...") {
                appStatusText.textContent = "RESPONSE READY"; // Indicate audio can be replayed
            }
        };
        irishkaAudioPlayback.onerror = (e) => {
            isPlayingIrishka = false;
            playIrishkaButton.classList.remove('playing');
            playIrishkaIcon.innerHTML = ''; 
            playIrishkaIcon.className = 'play-icon';
            console.error("Error with Irishka audio playback:", e, irishkaAudioPlayback.error);
            let errorDetail = getMediaErrorDetail(irishkaAudioPlayback.error);
            typeTextOnScreen(`[AUDIO PLAYBACK ERROR: ${errorDetail}]`, 'error-message fast'); 
            appStatusText.textContent = `AUDIO ERR: ${errorDetail}`;
        };

        typeTextOnScreen("IRISHKA AI WEB INTERFACE ONLINE. STANDBY...", "system-message", () => {
            appStatusText.textContent = "STANDBY";
            fetchAndUpdateStatus(); 
            setInterval(fetchAndUpdateStatus, 4000); 
        });
    }

    async function toggleRecording() {
        if (isRecording) {
            stopRecording();
        } else {
            await startRecording();
        }
    }

    async function startRecording() {
        if (isPlayingIrishka) { 
            irishkaAudioPlayback.pause();
            irishkaAudioPlayback.currentTime = 0; // Reset audio
        }
        audioChunks = [];
        currentIrishkaAudioURL = null; 
        playIrishkaButton.disabled = true;
        playIrishkaButton.classList.remove('playing');
        playIrishkaIcon.innerHTML = ''; 
        playIrishkaIcon.className = 'play-icon';


        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            // Try to get a higher sample rate if browser supports it, otherwise it defaults
            const options = { mimeType: 'audio/webm' }; // Stick to webm for broad compatibility
            // if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
            //     options.mimeType = 'audio/webm;codecs=opus';
            // } // Pydub should handle webm/opus fine.

            mediaRecorder = new MediaRecorder(stream, options);

            mediaRecorder.ondataavailable = event => { audioChunks.push(event.data); };
            mediaRecorder.onstop = async () => {
                const audioBlob = new Blob(audioChunks, { type: options.mimeType });
                stream.getTracks().forEach(track => track.stop());
                recordButton.disabled = true;
                recordButton.textContent = "PROCESS";
                appStatusText.textContent = "UPLOADING...";
                typeTextOnScreen("[SENDING AUDIO TO IRISHKA...]", "system-message fast");
                await sendAudioToServer(audioBlob);
            };

            mediaRecorder.start();
            isRecording = true;
            recordButton.textContent = "STOP REC";
            recordButton.classList.add('recording');
            recordingLedIndicator.classList.add('active');
            appStatusText.textContent = "RECORDING...";
            typeTextOnScreen("[LISTENING...]", "system-message");

        } catch (err) {
            console.error('Error accessing microphone:', err);
            typeTextOnScreen(`[MIC ERROR: ${err.message}]`, 'error-message fast');
            appStatusText.textContent = "MIC ERROR";
            resetRecordingButton();
        }
    }

    function stopRecording() {
        if (mediaRecorder && mediaRecorder.state === "recording") {
            mediaRecorder.stop();
        }
        isRecording = false; // This will be set again if recording restarts successfully
        recordButton.classList.remove('recording');
        recordingLedIndicator.classList.remove('active');
    }
    
    function resetRecordingButton() {
        isRecording = false; // Ensure state is reset
        recordButton.disabled = false;
        recordButton.textContent = "SPEAK";
        recordButton.classList.remove('recording');
        recordingLedIndicator.classList.remove('active');
    }

    function toggleIrishkaPlayback() {
        if (!currentIrishkaAudioURL) {
            typeTextOnScreen("[NO AUDIO FROM IRISHKA TO PLAY]", "system-message fast");
            return;
        }
        if (isPlayingIrishka) {
            irishkaAudioPlayback.pause();
        } else {
            irishkaAudioPlayback.src = currentIrishkaAudioURL;
            irishkaAudioPlayback.load(); // Good practice to call load() before play() if src changes
            const playPromise = irishkaAudioPlayback.play();
            if (playPromise !== undefined) {
                playPromise.catch(error => {
                    console.error("Error attempting to play Irishka audio:", error);
                    typeTextOnScreen(`[PLAYBACK BLOCKED. CLICK PLAY ICON.]`, 'error-message fast');
                    appStatusText.textContent = "PLAYBACK BLOCKED";
                });
            }
        }
    }
    
    function getMediaErrorDetail(mediaError) {
        if (!mediaError) return "Unknown audio error.";
        switch (mediaError.code) {
            case MediaError.MEDIA_ERR_ABORTED: return 'Playback aborted.';
            case MediaError.MEDIA_ERR_NETWORK: return 'Network error during audio download.';
            case MediaError.MEDIA_ERR_DECODE: return 'Audio decoding error.';
            case MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED: return 'Audio format not supported.';
            default: return 'An unknown audio error occurred.';
        }
    }

    async function sendAudioToServer(audioBlob) {
        const formData = new FormData();
        formData.append('audio_data', audioBlob, 'user_web_audio.webm');
        currentIrishkaAudioURL = null; 
        playIrishkaButton.disabled = true;

        try {
            appStatusText.textContent = 'PROCESSING...';
            typeTextOnScreen("[IRISHKA IS THINKING...]", "system-message");

            const response = await fetch('/process_audio', {
                method: 'POST',
                body: formData
            });

            let responseData;
            if (response.ok) {
                responseData = await response.json();
            } else {
                const errorText = await response.text(); // Attempt to get text for non-JSON errors
                console.error(`Server error ${response.status}:`, errorText);
                typeTextOnScreen(`[SERVER ERROR ${response.status}: ${(errorText || "Unknown server error").substring(0,100)}]`, 'error-message fast');
                appStatusText.textContent = `SERVER ERR ${response.status}`;
                resetRecordingButton();
                return;
            }
            
            console.log("Response from server:", responseData);

            if (responseData.error) {
                typeTextOnScreen(`[ERROR: ${responseData.error}]`, 'error-message fast');
                appStatusText.textContent = "ERROR";
            } else {
                // Display transcription first, then LLM response
                const transcription = responseData.user_transcription;
                if (transcription || transcription === "") { // Handle empty transcription as valid (silence)
                    typeTextOnScreen(`${transcription || '[SILENCE/UNCLEAR]'}`, "user-message", () => {
                        if (responseData.llm_text_response) {
                            typeTextOnScreen(`${responseData.llm_text_response}`, "assistant-message", () => {
                                if (responseData.audio_url) {
                                    currentIrishkaAudioURL = responseData.audio_url;
                                    playIrishkaButton.disabled = false;
                                    appStatusText.textContent = "RESPONSE READY";
                                    // toggleIrishkaPlayback(); // Optional: Auto-play
                                } else {
                                    appStatusText.textContent = "TEXT RESPONSE ONLY";
                                }
                            });
                        } else {
                             appStatusText.textContent = "READY"; // Transcription OK, no LLM text
                        }
                    });
                } else if (responseData.llm_text_response) { 
                     // Case: No transcription but LLM response (e.g., direct error from LLM not related to STT)
                     typeTextOnScreen(`${responseData.llm_text_response}`, "assistant-message", () => {
                        if (responseData.audio_url) { // Should generally not happen if LLM response is an error
                            currentIrishkaAudioURL = responseData.audio_url;
                            playIrishkaButton.disabled = false;
                        }
                        appStatusText.textContent = "RESPONSE RECEIVED";
                     });
                } else {
                    // Neither transcription nor LLM response, but no explicit error.
                    typeTextOnScreen("[NO RESPONSE DATA RECEIVED]", "system-message fast");
                    appStatusText.textContent = "EMPTY RESPONSE";
                }
            }
        } catch (error) {
            console.error('Error sending/processing audio:', error);
            typeTextOnScreen(`[CLIENT ERROR: ${error.message}]`, 'error-message fast');
            appStatusText.textContent = "CLIENT ERROR";
        } finally {
            resetRecordingButton(); 
        }
    }

    const MAX_SCREEN_MESSAGES = 15; // Keep more history for dialogue
    const screenMessages = [];

    function typeTextOnScreen(text, typeClass = "system-message", callback, speed = 30) {
        if (typingInterval) {
            clearInterval(typingInterval); 
        }
        
        const messageEntry = { text: text, type: typeClass, fullyTyped: false, id: `msg-${Date.now()}-${Math.random()}` };
        
        screenMessages.push(messageEntry);
        if (screenMessages.length > MAX_SCREEN_MESSAGES) {
            screenMessages.shift(); 
        }
        
        renderScreenMessages(); // Initial render of the new message structure

        let currentMessageElement = document.getElementById(messageEntry.id);
        if (!currentMessageElement) { // Message might have been immediately scrolled off if list was full
            console.warn("Could not find message element for typing effect, likely scrolled off too fast.");
            if (callback) callback();
            return;
        }
        
        let charIndex = 0;
        currentMessageElement.textContent = ''; // Clear for typing
        const effectiveSpeed = typeClass.includes('fast') ? 10 : speed;

        typingInterval = setInterval(() => {
            if (charIndex < text.length) {
                currentMessageElement.textContent += text[charIndex];
                charIndex++;
                chatScreen.scrollTop = chatScreen.scrollHeight; 
            } else {
                clearInterval(typingInterval);
                typingInterval = null;
                messageEntry.fullyTyped = true; // Mark as fully typed
                // Remove cursor from this message if it's not the absolute last action
                let cursorSpan = currentMessageElement.querySelector('.cursor');
                if(cursorSpan) cursorSpan.remove();

                if (callback) callback();
            }
        }, effectiveSpeed);
    }

    function renderScreenMessages() {
        chatScreen.innerHTML = ''; 
        screenMessages.forEach((msg) => {
            const p = document.createElement('p');
            p.id = msg.id;
            p.className = msg.type;
            p.textContent = msg.fullyTyped ? msg.text : ''; // Only show full text if fully typed, otherwise typing effect handles it
            chatScreen.appendChild(p);
        });
        chatScreen.scrollTop = chatScreen.scrollHeight; 
    }


    function setLedStatus(ledElement, statusType) {
        if (!ledElement) return;
        ledElement.className = 'status-led'; 
        const statusClass = getStatusClassForLed(statusType); 
        if (statusClass) {
            ledElement.classList.add(statusClass);
        }
    }
    
    function getStatusClassForLed(statusTypeStr) { 
        if (!statusTypeStr) return 'led-info'; 
        const s = statusTypeStr.toLowerCase();
        if (["ready", "polling", "loaded", "saved", "fresh", "idle", "ok_gpu", "ok"].includes(s)) return "led-ok";
        if (["loading", "checking", "pinging", "thinking", "warn", "busy"].includes(s)) return "led-warn";
        if (["error", "na", "n/a", "timeout", "conn_error", "http_502", "http_other", "initfail", "unreachable", "bad_token", "net_error", "err", "no_token", "no_admin"].includes(s)) return "led-error";
        if (s === "off") return "led-off";
        return "led-info"; 
    }

    async function fetchAndUpdateStatus() {
        try {
            const response = await fetch('/status');
            if (!response.ok) {
                console.warn('Failed to fetch status from server:', response.status);
                setLedStatus(ollamaLed, 'error'); setLedStatus(whisperLed, 'error');
                setLedStatus(barkLed, 'error'); setLedStatus(telegramLed, 'error');
                if (!appStatusText.textContent.includes("ERROR") && !appStatusText.textContent.includes("PLAYING")) {
                    appStatusText.textContent = 'STATUS N/A';
                }
                return;
            }
            const statusData = await response.json();

            setLedStatus(ollamaLed, statusData.ollama?.type);
            setLedStatus(whisperLed, statusData.whisper?.type);
            setLedStatus(barkLed, statusData.bark?.type);
            setLedStatus(telegramLed, statusData.telegram?.type);
            
            const currentAppStatus = appStatusText.textContent.toUpperCase();
            const nonInterruptingStatuses = ["RECORDING...", "PROCESSING...", "UPLOADING...", "PLAYING IRISHKA...", "IRISHKA IS THINKING...", "LISTENING..."];
            if (!nonInterruptingStatuses.includes(currentAppStatus)) {
                 if (statusData.app_overall_status) { // Use main app's overall status if available
                    appStatusText.textContent = statusData.app_overall_status.toUpperCase();
                 } else { // Fallback if overall status is missing
                    appStatusText.textContent = "SYSTEM READY";
                 }
            }

            const whisperIsReady = statusData.whisper?.type === 'ready' || statusData.whisper?.type === 'ok' || statusData.whisper?.type === 'idle';
            if (!isRecording && !recordButton.textContent.toUpperCase().includes("PROCESS")) { 
                recordButton.disabled = !whisperIsReady;
                recordButton.textContent = whisperIsReady ? "SPEAK" : "HEAR N/A";
            }

        } catch (error) {
            console.error('Error fetching/updating status:', error);
            setLedStatus(ollamaLed, 'error'); setLedStatus(whisperLed, 'error');
            setLedStatus(barkLed, 'error'); setLedStatus(telegramLed, 'error');
            if (!appStatusText.textContent.includes("ERROR") && !appStatusText.textContent.includes("PLAYING")) {
                appStatusText.textContent = 'STATUS ERR';
            }
        }
    }

    init(); 
});