// webui/static/js/script.js
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
    let isSpacebarDown = false; // To prevent multiple start attempts from keydown repeat
    let isMouseDownOnRecordButton = false; // To track button press state

    let isPlayingIrishka = false;
    let currentIrishkaAudioURL = null;
    let typingInterval = null;

    function init() {
        // Record Button listeners
        recordButton.addEventListener('mousedown', handleRecordButtonPress);
        recordButton.addEventListener('mouseup', handleRecordButtonRelease);
        // Global listeners to catch mouseup even if it happens outside the button
        document.addEventListener('mouseup', handleGlobalMouseUp);
        document.addEventListener('mouseleave', handleGlobalMouseLeave); // Handle if mouse leaves button while pressed


        // Spacebar listeners
        document.addEventListener('keydown', handleSpacebarDown);
        document.addEventListener('keyup', handleSpacebarUp);

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
                 // appStatusText.textContent = "READY"; // Or "RESPONSE READY"
                 // Let fetchAndUpdateStatus handle this or onended
            }
        };
        irishkaAudioPlayback.onended = () => {
            // onpause handles visual state reset for the button icon
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

    function handleRecordButtonPress(event) {
        if (event.button === 0) { // Main (left) mouse button
            isMouseDownOnRecordButton = true;
            if (!isRecording) {
                startRecording();
            }
        }
    }

    function handleRecordButtonRelease(event) {
        if (event.button === 0) { // Main (left) mouse button
            isMouseDownOnRecordButton = false;
            if (isRecording) {
                stopRecording();
            }
        }
    }
    
    function handleGlobalMouseUp(event) {
        // If mouse was down on the button and now it's up (anywhere)
        if (event.button === 0 && isMouseDownOnRecordButton) {
            isMouseDownOnRecordButton = false;
            if (isRecording) {
                stopRecording();
            }
        }
    }
    function handleGlobalMouseLeave(event) {
        // If mouse was down on the button and leaves the button area
        if (isMouseDownOnRecordButton && event.target === recordButton) {
            // Optional: could stop recording if mouse leaves button, or let global mouseup handle it.
            // For simplicity, we'll let global mouseup handle it.
        }
    }


    function handleSpacebarDown(event) {
        if (event.key === ' ' || event.code === 'Space') {
            event.preventDefault(); // Prevent scrolling or other default space actions
            if (!isRecording && !isSpacebarDown) {
                isSpacebarDown = true;
                startRecording();
            }
        }
    }

    function handleSpacebarUp(event) {
        if (event.key === ' ' || event.code === 'Space') {
            isSpacebarDown = false;
            if (isRecording) {
                stopRecording();
            }
        }
    }

    async function startRecording() {
        if (isRecording) return; // Already recording

        if (isPlayingIrishka) {
            irishkaAudioPlayback.pause();
            irishkaAudioPlayback.currentTime = 0;
        }
        audioChunks = [];
        currentIrishkaAudioURL = null;
        playIrishkaButton.disabled = true;
        playIrishkaButton.classList.remove('playing');
        playIrishkaIcon.innerHTML = '';
        playIrishkaIcon.className = 'play-icon';

        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            const options = { mimeType: 'audio/webm' };
            mediaRecorder = new MediaRecorder(stream, options);

            mediaRecorder.ondataavailable = event => { audioChunks.push(event.data); };
            mediaRecorder.onstop = async () => {
                const audioBlob = new Blob(audioChunks, { type: options.mimeType });
                stream.getTracks().forEach(track => track.stop()); // Stop the microphone tracks
                
                // UI update for processing stage
                recordButton.disabled = true;
                recordButton.textContent = "PROCESS"; // "PROCESS" or "UPLOADING"
                appStatusText.textContent = "UPLOADING...";
                typeTextOnScreen("[SENDING AUDIO TO IRISHKA...]", "system-message fast");
                
                await sendAudioToServer(audioBlob);
            };

            mediaRecorder.start();
            isRecording = true;
            recordButton.textContent = "LISTENING"; // Changed from "STOP REC"
            recordButton.classList.add('recording');
            recordingLedIndicator.classList.add('active');
            appStatusText.textContent = "RECORDING...";
            typeTextOnScreen("[LISTENING...]", "system-message");

        } catch (err) {
            console.error('Error accessing microphone:', err);
            typeTextOnScreen(`[MIC ERROR: ${err.message}]`, 'error-message fast');
            appStatusText.textContent = "MIC ERROR";
            resetRecordingButton(); // Reset UI if start fails
            isRecording = false; // Ensure state is correct
            isSpacebarDown = false; // Reset spacebar state
            isMouseDownOnRecordButton = false; // Reset mouse state
        }
    }

    function stopRecording() {
        if (mediaRecorder && mediaRecorder.state === "recording") {
            mediaRecorder.stop(); // This will trigger mediaRecorder.onstop
        }
        // Visual reset happens immediately on release,
        // then onstop will change button to "PROCESS"
        isRecording = false;
        recordButton.classList.remove('recording');
        recordingLedIndicator.classList.remove('active');
        // Don't call resetRecordingButton() here, as onstop will handle the next state.
        // If onstop doesn't fire (e.g., error before mediaRecorder.start),
        // resetRecordingButton should be called in the error handler of startRecording.
        // If user releases very quickly before ondataavailable and onstop,
        // resetRecordingButton() in sendAudioToServer's finally block will handle it.
    }

    function resetRecordingButton() {
        isRecording = false; 
        isSpacebarDown = false;
        isMouseDownOnRecordButton = false;
        recordButton.disabled = false; // Will be re-evaluated by fetchAndUpdateStatus
        // Default text, will be updated by fetchAndUpdateStatus based on whisper readiness
        recordButton.textContent = "SPEAK"; 
        recordButton.classList.remove('recording');
        recordingLedIndicator.classList.remove('active');
        // Let fetchAndUpdateStatus handle the text ("SPEAK" vs "HEAR N/A")
        // and disabled state based on Whisper readiness
        fetchAndUpdateStatus(); // Trigger a status update to correctly set button state
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
            irishkaAudioPlayback.load();
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
            // appStatusText.textContent = 'PROCESSING...'; // Already set to UPLOADING, then PROCESS in onstop
            // typeTextOnScreen("[IRISHKA IS THINKING...]", "system-message"); // This will be shown after upload potentially

            const response = await fetch('/process_audio', {
                method: 'POST',
                body: formData
            });

            let responseData;
            if (response.ok) {
                responseData = await response.json();
            } else {
                const errorText = await response.text();
                console.error(`Server error ${response.status}:`, errorText);
                typeTextOnScreen(`[SERVER ERROR ${response.status}: ${(errorText || "Unknown server error").substring(0,100)}]`, 'error-message fast');
                appStatusText.textContent = `SERVER ERR ${response.status}`;
                // resetRecordingButton(); // Moved to finally
                return;
            }

            console.log("Response from server:", responseData);
             // Type "Irishka is thinking" AFTER upload is confirmed and before processing the response
            typeTextOnScreen("[IRISHKA IS THINKING...]", "system-message fast");


            if (responseData.error) {
                typeTextOnScreen(`[ERROR: ${responseData.error}]`, 'error-message fast');
                appStatusText.textContent = "ERROR";
            } else {
                const transcription = responseData.user_transcription;
                if (transcription || transcription === "") {
                    typeTextOnScreen(`${transcription || '[SILENCE/UNCLEAR]'}`, "user-message", () => {
                        if (responseData.llm_text_response) {
                            typeTextOnScreen(`${responseData.llm_text_response}`, "assistant-message", () => {
                                if (responseData.audio_url) {
                                    currentIrishkaAudioURL = responseData.audio_url;
                                    playIrishkaButton.disabled = false;
                                    appStatusText.textContent = "RESPONSE READY";
                                } else {
                                    appStatusText.textContent = "TEXT RESPONSE ONLY";
                                }
                            });
                        } else {
                             appStatusText.textContent = "READY";
                        }
                    });
                } else if (responseData.llm_text_response) {
                     typeTextOnScreen(`${responseData.llm_text_response}`, "assistant-message", () => {
                        if (responseData.audio_url) {
                            currentIrishkaAudioURL = responseData.audio_url;
                            playIrishkaButton.disabled = false;
                        }
                        appStatusText.textContent = "RESPONSE RECEIVED";
                     });
                } else {
                    typeTextOnScreen("[NO RESPONSE DATA RECEIVED]", "system-message fast");
                    appStatusText.textContent = "EMPTY RESPONSE";
                }
            }
        } catch (error) {
            console.error('Error sending/processing audio:', error);
            typeTextOnScreen(`[CLIENT ERROR: ${error.message}]`, 'error-message fast');
            appStatusText.textContent = "CLIENT ERROR";
        } finally {
            resetRecordingButton(); // Crucial to reset button state after all processing
        }
    }

    const MAX_SCREEN_MESSAGES = 15;
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

        renderScreenMessages();

        let currentMessageElement = document.getElementById(messageEntry.id);
        if (!currentMessageElement) {
            console.warn("Could not find message element for typing effect, likely scrolled off too fast.");
            if (callback) callback();
            return;
        }

        let charIndex = 0;
        currentMessageElement.textContent = '';
        const effectiveSpeed = typeClass.includes('fast') ? 10 : speed;

        typingInterval = setInterval(() => {
            if (charIndex < text.length) {
                currentMessageElement.textContent += text[charIndex];
                charIndex++;
                chatScreen.scrollTop = chatScreen.scrollHeight;
            } else {
                clearInterval(typingInterval);
                typingInterval = null;
                messageEntry.fullyTyped = true;
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
            p.textContent = msg.fullyTyped ? msg.text : '';
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
        if (["ready", "polling", "loaded", "saved", "fresh", "idle", "ok_gpu", "ok", "active"].includes(s)) return "led-ok"; // Added "active" for WebUI operational status
        if (["loading", "checking", "pinging", "thinking", "warn", "busy"].includes(s)) return "led-warn";
        if (["error", "na", "n/a", "timeout", "conn_error", "http_502", "http_other", "initfail", "unreachable", "bad_token", "net_error", "err", "no_token", "no_admin", "err-chk", "unhealthy", "no-conn"].includes(s)) return "led-error"; // Added more error types
        if (s === "off" || s === "cfg off" || s === "paused" || s === "disabled") return "led-off"; // Added more off/paused types
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
             // Also update the WebUI operational status LED if you add one in HTML
            // e.g., setLedStatus(document.getElementById('webuiLed'), statusData.webui_operational_status?.type);


            const currentAppStatus = appStatusText.textContent.toUpperCase();
            const nonInterruptingStatuses = ["RECORDING...", "PROCESS", "UPLOADING...", "PLAYING IRISHKA...", "IRISHKA IS THINKING...", "LISTENING..."];
            if (!nonInterruptingStatuses.includes(currentAppStatus)) {
                 if (statusData.app_overall_status) {
                    appStatusText.textContent = statusData.app_overall_status.toUpperCase();
                 } else {
                    appStatusText.textContent = "SYSTEM READY";
                 }
            }

            const whisperIsReady = statusData.whisper?.type === 'ready' || statusData.whisper?.type === 'idle'; // Simplified
            if (!isRecording && recordButton.textContent.toUpperCase() !== "PROCESS" && recordButton.textContent.toUpperCase() !== "LISTENING") {
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