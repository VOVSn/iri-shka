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
    let isSpacebarDown = false; 
    let isPointerDownOnRecordButton = false; // Tracks if mouse/touch is currently pressed on the button

    let isPlayingIrishka = false;
    let currentIrishkaAudioURL = null;
    let typingInterval = null;

    function init() {
        // Record Button listeners
        // Mouse events
        recordButton.addEventListener('mousedown', handlePointerDownOnButton);
        recordButton.addEventListener('mouseup', handlePointerUpOffButton); // Handles release on the button
        
        // Touch events
        recordButton.addEventListener('touchstart', handlePointerDownOnButton, { passive: false }); // passive:false as we call preventDefault
        recordButton.addEventListener('touchend', handlePointerUpOffButton);
        recordButton.addEventListener('touchcancel', handlePointerUpOffButton); // Treat cancel like an "up"

        // Global listeners to catch pointer release even if it happens outside the button
        document.addEventListener('mouseup', handleGlobalPointerUp);
        document.addEventListener('touchend', handleGlobalPointerUp);
        document.addEventListener('touchcancel', handleGlobalPointerUp);
        
        // Optional: Handle mouse leaving button area while pressed (if different behavior desired)
        // recordButton.addEventListener('mouseleave', handlePointerLeaveButtonWhileDown);


        // Spacebar listeners (already PTT)
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
            // if (appStatusText.textContent === "PLAYING IRISHKA...") {
                 // Let fetchAndUpdateStatus handle this or onended
            // }
        };
        irishkaAudioPlayback.onended = () => {
            console.log("Irishka audio finished playing.");
            if (appStatusText.textContent === "PLAYING IRISHKA...") {
                appStatusText.textContent = "RESPONSE READY"; 
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

    function handlePointerDownOnButton(event) {
        // For mouse, only act on primary (left) button
        if (event.type === 'mousedown' && event.button !== 0) {
            return;
        }
        
        // Prevent default browser actions (like text selection on hold for mouse,
        // or scrolling/zooming if finger moves slightly for touch).
        if (event.cancelable) {
            event.preventDefault();
        }

        isPointerDownOnRecordButton = true;
        if (!isRecording) {
            startRecording(); // Asynchronously starts, updates UI
        }
    }

    function handlePointerUpOffButton(event) {
        // This function is called when pointerup/touchend/touchcancel happens *on the button itself*.
        // For mouse, only act on primary (left) button release
        if (event.type === 'mouseup' && event.button !== 0) {
            return;
        }

        // The isPointerDownOnRecordButton flag ensures we only stop if a "down" action
        // was initiated on this button.
        if (isPointerDownOnRecordButton) {
            isPointerDownOnRecordButton = false; // Reset flag: pointer is no longer down on the button
            if (isRecording) {
                stopRecording(); // Asynchronously stops, updates UI
            }
        }
    }
    
    function handleGlobalPointerUp(event) {
        // This handles cases where the pointer is released *anywhere on the document*,
        // useful if the user drags their mouse/finger off the button and then releases.
        // For mouse, only act on primary (left) button release
        if (event.type === 'mouseup' && event.button !== 0) {
            return;
        }

        if (isPointerDownOnRecordButton) { // If a "down" action was initiated on our button
            isPointerDownOnRecordButton = false; // Reset flag
            if (isRecording) {
                stopRecording();
            }
        }
    }

    // function handlePointerLeaveButtonWhileDown(event) {
    //     // This is for 'mouseleave' from the button element.
    //     // If you want recording to stop if mouse drags off button WHILE STILL PRESSED:
    //     if (event.type === 'mouseleave' && isPointerDownOnRecordButton && isRecording) {
    //         stopRecording(); 
    //         isPointerDownOnRecordButton = false; 
    //     }
    //     // Standard PTT: press and hold anywhere (originating on button) -> record. 
    //     // Release (anywhere) -> stop. So, this specific handler might not be needed
    //     // if the global up handlers are robust.
    // }


    function handleSpacebarDown(event) {
        if (event.key === ' ' || event.code === 'Space') {
            if(event.cancelable) event.preventDefault(); // Prevent scrolling
            if (!isRecording && !isSpacebarDown) {
                isSpacebarDown = true;
                startRecording();
            }
        }
    }

    function handleSpacebarUp(event) {
        if (event.key === ' ' || event.code === 'Space') {
            // No preventDefault needed on keyup usually
            if (isSpacebarDown) { // Only act if spacebar was the one that started it
                isSpacebarDown = false;
                if (isRecording) {
                    stopRecording();
                }
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
            const options = { mimeType: 'audio/webm' }; // you can also try 'audio/ogg; codecs=opus'
            mediaRecorder = new MediaRecorder(stream, options);

            mediaRecorder.ondataavailable = event => { audioChunks.push(event.data); };
            mediaRecorder.onstop = async () => {
                const audioBlob = new Blob(audioChunks, { type: options.mimeType });
                stream.getTracks().forEach(track => track.stop()); // Stop the microphone tracks
                
                recordButton.disabled = true;
                recordButton.textContent = "PROCESS"; 
                appStatusText.textContent = "UPLOADING...";
                typeTextOnScreen("[SENDING AUDIO TO IRISHKA...]", "system-message fast");
                
                await sendAudioToServer(audioBlob);
            };

            mediaRecorder.start();
            isRecording = true;
            recordButton.textContent = "LISTENING"; 
            recordButton.classList.add('recording');
            recordingLedIndicator.classList.add('active');
            appStatusText.textContent = "RECORDING...";
            typeTextOnScreen("[LISTENING...]", "system-message");

        } catch (err) {
            console.error('Error accessing microphone:', err);
            typeTextOnScreen(`[MIC ERROR: ${err.message}]`, 'error-message fast');
            appStatusText.textContent = "MIC ERROR";
            resetRecordingButton(); // Reset UI and all PTT state flags if start fails
        }
    }

    function stopRecording() {
        if (mediaRecorder && mediaRecorder.state === "recording") {
            mediaRecorder.stop(); // This will trigger mediaRecorder.onstop for processing
        }
        // Immediate visual feedback that recording has stopped "listening"
        isRecording = false; // Primary recording state flag
        // isPointerDownOnRecordButton and isSpacebarDown are reset by their respective 'up' handlers.
        
        recordButton.classList.remove('recording');
        recordingLedIndicator.classList.remove('active');
        // Button text changes to "PROCESS" etc. via onstop -> sendAudioToServer -> resetRecordingButton
    }

    function resetRecordingButton() {
        isRecording = false; 
        isSpacebarDown = false;
        isPointerDownOnRecordButton = false; // Ensure this is reset on all paths
        recordButton.disabled = false; 
        // Default text, will be updated by fetchAndUpdateStatus based on whisper readiness
        recordButton.textContent = "SPEAK"; 
        recordButton.classList.remove('recording');
        recordingLedIndicator.classList.remove('active');
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
            irishkaAudioPlayback.load(); // Good practice before play
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
                return; // resetRecordingButton() is in finally
            }

            console.log("Response from server:", responseData);
            typeTextOnScreen("[IRISHKA IS THINKING...]", "system-message fast");

            if (responseData.error) {
                typeTextOnScreen(`[ERROR: ${responseData.error}]`, 'error-message fast');
                appStatusText.textContent = "ERROR";
            } else {
                const transcription = responseData.user_transcription;
                if (transcription || transcription === "") { // Handle empty string transcription explicitly
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
                        } else { // No LLM text response
                             appStatusText.textContent = "READY"; // Or "TRANSCRIPTION DONE"
                        }
                    });
                } else if (responseData.llm_text_response) { // No transcription, but LLM response
                     typeTextOnScreen(`${responseData.llm_text_response}`, "assistant-message", () => {
                        if (responseData.audio_url) {
                            currentIrishkaAudioURL = responseData.audio_url;
                            playIrishkaButton.disabled = false;
                        }
                        appStatusText.textContent = "RESPONSE RECEIVED"; // Or "RESPONSE READY" if audio_url
                     });
                } else { // Neither transcription nor LLM text
                    typeTextOnScreen("[NO RESPONSE DATA RECEIVED]", "system-message fast");
                    appStatusText.textContent = "EMPTY RESPONSE";
                }
            }
        } catch (error) {
            console.error('Error sending/processing audio:', error);
            typeTextOnScreen(`[CLIENT ERROR: ${error.message}]`, 'error-message fast');
            appStatusText.textContent = "CLIENT ERROR";
        } finally {
            resetRecordingButton(); // Crucial to reset button and PTT state after all processing
        }
    }

    const MAX_SCREEN_MESSAGES = 15;
    const screenMessages = [];

    function typeTextOnScreen(text, typeClass = "system-message", callback, speed = 30) {
        if (typingInterval) {
            clearInterval(typingInterval);
            // Find the last message being typed and complete it instantly if it's not the one we're about to type
            const lastTypingMsg = screenMessages.find(m => !m.fullyTyped && m.id !== `msg-${Date.now()}-${Math.random()}`); // A bit hacky to find
            if(lastTypingMsg) {
                const elem = document.getElementById(lastTypingMsg.id);
                if (elem) elem.textContent = lastTypingMsg.text;
                lastTypingMsg.fullyTyped = true;
            }
        }


        const messageEntry = { text: text, type: typeClass, fullyTyped: false, id: `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}` };


        screenMessages.push(messageEntry);
        if (screenMessages.length > MAX_SCREEN_MESSAGES) {
            screenMessages.shift();
        }

        renderScreenMessages(); // Re-render to add the new paragraph shell

        let currentMessageElement = document.getElementById(messageEntry.id);
        if (!currentMessageElement) {
            console.warn("Could not find message element for typing effect, likely scrolled off too fast or ID issue.");
            if (callback) callback();
            return;
        }

        let charIndex = 0;
        currentMessageElement.textContent = ''; // Clear it for typing
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
                // Remove cursor span if you were using one
                // let cursorSpan = currentMessageElement.querySelector('.cursor');
                // if(cursorSpan) cursorSpan.remove();

                if (callback) callback();
            }
        }, effectiveSpeed);
    }

    function renderScreenMessages() {
        chatScreen.innerHTML = ''; // Clear existing
        screenMessages.forEach((msg) => {
            const p = document.createElement('p');
            p.id = msg.id;
            p.className = msg.type;
            // If message is not fully typed yet, its content will be empty here initially,
            // and filled by the typingInterval. If it's already fully typed (e.g. from history), show full text.
            p.textContent = msg.fullyTyped ? msg.text : ''; 
            chatScreen.appendChild(p);
        });
        chatScreen.scrollTop = chatScreen.scrollHeight;
    }


    function setLedStatus(ledElement, statusType) {
        if (!ledElement) return;
        ledElement.className = 'status-led'; // Reset classes
        const statusClass = getStatusClassForLed(statusType);
        if (statusClass) {
            ledElement.classList.add(statusClass);
        }
    }

    function getStatusClassForLed(statusTypeStr) {
        if (!statusTypeStr) return 'led-info'; // Default or unknown
        const s = statusTypeStr.toLowerCase();
        if (["ready", "polling", "loaded", "saved", "fresh", "idle", "ok_gpu", "ok", "active"].includes(s)) return "led-ok";
        if (["loading", "checking", "pinging", "thinking", "warn", "busy"].includes(s)) return "led-warn";
        if (["error", "na", "n/a", "timeout", "conn_error", "http_502", "http_other", "initfail", "unreachable", "bad_token", "net_error", "err", "no_token", "no_admin", "err-chk", "unhealthy", "no-conn", "ssl_err"].includes(s)) return "led-error";
        if (s === "off" || s === "cfg off" || s === "paused" || s === "disabled") return "led-off";
        return "led-info"; // Fallback
    }

    async function fetchAndUpdateStatus() {
        try {
            const response = await fetch('/status');
            if (!response.ok) {
                console.warn('Failed to fetch status from server:', response.status, await response.text());
                setLedStatus(ollamaLed, 'error'); setLedStatus(whisperLed, 'error');
                setLedStatus(barkLed, 'error'); setLedStatus(telegramLed, 'error');
                if (!appStatusText.textContent.includes("ERROR") && !appStatusText.textContent.includes("PLAYING")) {
                    appStatusText.textContent = `STATUS N/A (${response.status})`;
                }
                return;
            }
            const statusData = await response.json();

            setLedStatus(ollamaLed, statusData.ollama?.type);
            setLedStatus(whisperLed, statusData.whisper?.type);
            setLedStatus(barkLed, statusData.bark?.type);
            setLedStatus(telegramLed, statusData.telegram?.type);
            // e.g., setLedStatus(document.getElementById('webuiLed'), statusData.webui_operational_status?.type);


            const currentAppStatus = appStatusText.textContent.toUpperCase();
            const nonInterruptingStatuses = ["RECORDING...", "PROCESS", "UPLOADING...", "PLAYING IRISHKA...", "IRISHKA IS THINKING...", "LISTENING..."];
            if (!nonInterruptingStatuses.includes(currentAppStatus)) {
                 if (statusData.app_overall_status) {
                    appStatusText.textContent = statusData.app_overall_status.toUpperCase();
                 } else {
                    appStatusText.textContent = "SYSTEM READY"; // Default if no overall status
                 }
            }
            
            // Update record button state based on Whisper readiness, only if not actively recording/processing
            const whisperIsReady = statusData.whisper?.type === 'ready' || statusData.whisper?.type === 'idle';
            if (!isRecording && !isPointerDownOnRecordButton && !isSpacebarDown && // Not actively being held for PTT
                recordButton.textContent.toUpperCase() !== "PROCESS" && 
                recordButton.textContent.toUpperCase() !== "LISTENING" ) {
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