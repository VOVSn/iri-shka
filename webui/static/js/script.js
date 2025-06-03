// webui/static/js/script.js
document.addEventListener('DOMContentLoaded', async () => { // Make DOMContentLoaded async
    const bodyElement = document.body;
    const themeSwitchButton = document.getElementById('themeSwitchButton');

    // Intercom UI Elements
    const recordButton = document.getElementById('recordButton');
    const playIrishkaButton = document.getElementById('playIrishkaButton');
    const chatScreen = document.getElementById('chatScreen');
    const recordingLedIndicator = document.getElementById('recordingLedIndicator');
    const playIrishkaIcon = document.getElementById('playIrishkaIcon');
    const screenStatusDisplay = document.getElementById('screenStatusDisplay');

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
    let isPointerDownOnRecordButton = false;
    let isPlayingIrishka = false;
    let currentIrishkaAudioURL = null;
    let typingInterval = null;

    // --- Audio Context for Beep/Boop Sounds ---
    let audioCtx;
    function getAudioContext() {
        if (!audioCtx) {
            audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        return audioCtx;
    }

    function playTone(frequency, duration = 0.1, type = 'sine', volume = 0.3) {
        try {
            const context = getAudioContext();
            if (!context) {
                console.warn("Web Audio API not supported, cannot play tone.");
                return;
            }
            // Ensure context is resumed if it was suspended by browser policy
            if (context.state === 'suspended') {
                context.resume();
            }

            const oscillator = context.createOscillator();
            const gainNode = context.createGain();

            oscillator.type = type;
            oscillator.frequency.setValueAtTime(frequency, context.currentTime);
            
            gainNode.gain.setValueAtTime(volume, context.currentTime);
            gainNode.gain.exponentialRampToValueAtTime(0.00001, context.currentTime + duration);


            oscillator.connect(gainNode);
            gainNode.connect(context.destination);

            oscillator.start(context.currentTime);
            oscillator.stop(context.currentTime + duration);
        } catch (e) {
            console.error("Error playing tone:", e);
        }
    }

    function playStartRecordSound() {
        playTone(880, 0.08, 'square', 0.2); // Higher pitch, short "beep"
    }

    function playStopRecordSound() {
        playTone(440, 0.12, 'triangle', 0.2); // Lower pitch, slightly longer "boop"
    }
    // --- End Audio Context ---


    // --- Theme Switching Logic ---
    let themesConfig = [];
    let currentThemeIndex = 0;
    const THEME_STORAGE_KEY = 'irishkaWebThemePreference';
    const loadedCssFiles = new Set();

    async function loadThemesAndInjectCSS() {
        document.querySelectorAll('link[rel="stylesheet"]').forEach(link => {
            if (link.href) loadedCssFiles.add(link.href);
        });
        try {
            const response = await fetch('/static/themes.json');
            if (!response.ok) {
                console.error('Failed to load themes.json:', response.status);
                themesConfig = [{ id: "dark", className: "theme-dark", displayName: "Dark", cssFile: "/static/css/style.css", buttonIcon: "☀️" }];
            } else {
                themesConfig = await response.json();
            }
            if (!Array.isArray(themesConfig) || themesConfig.length === 0) {
                console.error('themes.json is empty or not an array. Using default.');
                themesConfig = [{ id: "dark", className: "theme-dark", displayName: "Dark", cssFile: "/static/css/style.css", buttonIcon: "☀️" }];
            }
            themesConfig.forEach(theme => {
                if (theme.cssFile) {
                    const absoluteCssPath = new URL(theme.cssFile, window.location.origin).href;
                    if (!loadedCssFiles.has(absoluteCssPath)) {
                        const link = document.createElement('link');
                        link.rel = 'stylesheet'; link.type = 'text/css'; link.href = theme.cssFile; link.id = `theme-css-${theme.id}`;
                        document.head.appendChild(link);
                        loadedCssFiles.add(absoluteCssPath);
                        console.log(`Dynamically loaded CSS for theme: ${theme.displayName} from ${theme.cssFile}`);
                    } else {
                        console.log(`CSS for theme ${theme.displayName} (${theme.cssFile}) already loaded or linked in HTML.`);
                    }
                }
            });
        } catch (error) {
            console.error('Error fetching or processing themes.json:', error);
            themesConfig = [{ id: "dark", className: "theme-dark", displayName: "Dark", cssFile: "/static/css/style.css", buttonIcon: "☀️" }];
            const fallbackCssPath = new URL(themesConfig[0].cssFile, window.location.origin).href;
            if (!loadedCssFiles.has(fallbackCssPath)) {
                const link = document.createElement('link');
                link.rel = 'stylesheet'; link.type = 'text/css'; link.href = themesConfig[0].cssFile;
                document.head.appendChild(link);
                loadedCssFiles.add(fallbackCssPath);
            }
        }
    }

    function applyThemeById(themeId) {
        const theme = themesConfig.find(t => t.id === themeId);
        if (theme) {
            themesConfig.forEach(t => {
                if (t.className) bodyElement.classList.remove(t.className);
            });
            if (theme.className) bodyElement.classList.add(theme.className);
            localStorage.setItem(THEME_STORAGE_KEY, theme.id);
            currentThemeIndex = themesConfig.indexOf(theme);
            updateThemeButton();
            console.log(`Theme changed to: ${theme.displayName} (Class: ${theme.className})`);
        } else {
            console.warn(`Theme ID "${themeId}" not found. Applying default.`);
            if (themesConfig.length > 0) applyThemeByIndex(0);
        }
    }
    function applyThemeByIndex(index) {
        if (themesConfig.length > 0 && index >= 0 && index < themesConfig.length) {
            applyThemeById(themesConfig[index].id);
        } else if (themesConfig.length > 0) {
            applyThemeById(themesConfig[0].id);
        }
    }
    function cycleTheme() {
        if (themesConfig.length === 0) return;
        currentThemeIndex = (currentThemeIndex + 1) % themesConfig.length;
        applyThemeByIndex(currentThemeIndex);
    }
    function updateThemeButton() {
        if (themeSwitchButton && themesConfig.length > 0 && currentThemeIndex < themesConfig.length) {
            const nextThemeIndex = (currentThemeIndex + 1) % themesConfig.length;
            const nextTheme = themesConfig[nextThemeIndex];
            themeSwitchButton.textContent = nextTheme.buttonIcon || 'T';
            themeSwitchButton.title = `Switch to ${nextTheme.displayName}`;
        } else if (themeSwitchButton) {
            themeSwitchButton.textContent = 'T';
            themeSwitchButton.title = 'Switch Theme';
        }
    }
    async function initializeThemingAndApp() {
        await loadThemesAndInjectCSS();
        const savedThemeId = localStorage.getItem(THEME_STORAGE_KEY);
        let themeAppliedFromStorage = false;
        if (savedThemeId) {
            const themeExists = themesConfig.some(t => t.id === savedThemeId);
            if (themeExists) {
                applyThemeById(savedThemeId);
                themeAppliedFromStorage = true;
            } else {
                localStorage.removeItem(THEME_STORAGE_KEY);
            }
        }
        if (!themeAppliedFromStorage && themesConfig.length > 0) {
            applyThemeByIndex(0);
        } else if (themesConfig.length === 0) {
            console.error("No themes configured or loaded.");
            bodyElement.classList.add("theme-dark"); 
            if (themeSwitchButton) themeSwitchButton.style.display = 'none';
        }
        if (themeSwitchButton && themesConfig.length > 1) {
            themeSwitchButton.addEventListener('click', cycleTheme);
        } else if (themeSwitchButton) {
            themeSwitchButton.style.display = 'none';
        }
        initAppCoreLogic();
    }
    // --- End Theme Switching Logic ---

    function setStatusDisplay(message) {
        const upperMessage = message ? message.toUpperCase() : "STATUS UNKNOWN";
        if (screenStatusDisplay) {
            screenStatusDisplay.textContent = upperMessage;
        }
    }

    function initAppCoreLogic() {
        recordButton.addEventListener('mousedown', handlePointerDownOnButton);
        recordButton.addEventListener('mouseup', handlePointerUpOffButton);
        recordButton.addEventListener('touchstart', handlePointerDownOnButton, { passive: false });
        recordButton.addEventListener('touchend', handlePointerUpOffButton);
        recordButton.addEventListener('touchcancel', handlePointerUpOffButton);

        document.addEventListener('mouseup', handleGlobalPointerUp);
        document.addEventListener('touchend', handleGlobalPointerUp);
        document.addEventListener('touchcancel', handleGlobalPointerUp);
        
        document.addEventListener('keydown', handleSpacebarDown);
        document.addEventListener('keyup', handleSpacebarUp);

        playIrishkaButton.addEventListener('click', toggleIrishkaPlayback);

        irishkaAudioPlayback.onplay = () => {
            isPlayingIrishka = true;
            playIrishkaButton.classList.add('playing');
            if (playIrishkaIcon) playIrishkaIcon.innerHTML = '<div class="pause-icon"><div class="pause-bar"></div><div class="pause-bar"></div></div>';
            setStatusDisplay("PLAYING RESPONSE...");
        };
        irishkaAudioPlayback.onpause = () => {
            isPlayingIrishka = false;
            playIrishkaButton.classList.remove('playing');
            if (playIrishkaIcon) {
                playIrishkaIcon.innerHTML = '';
                playIrishkaIcon.className = 'play-icon';
            }
        };
        irishkaAudioPlayback.onended = () => {
            console.log("Irishka audio finished playing.");
            if (screenStatusDisplay.textContent === "PLAYING RESPONSE..." || screenStatusDisplay.textContent === "PLAYBACK PAUSED") {
                setStatusDisplay("RESPONSE READY");
            }
        };
        irishkaAudioPlayback.onerror = (e) => {
            isPlayingIrishka = false;
            playIrishkaButton.classList.remove('playing');
            if (playIrishkaIcon) {
                playIrishkaIcon.innerHTML = '';
                playIrishkaIcon.className = 'play-icon';
            }
            console.error("Error with Irishka audio playback:", e, irishkaAudioPlayback.error);
            let errorDetail = getMediaErrorDetail(irishkaAudioPlayback.error);
            typeTextOnScreen(`[AUDIO PLAYBACK ERROR: ${errorDetail}]`, 'error-message fast');
            setStatusDisplay(`AUDIO ERR: ${errorDetail}`);
        };

        typeTextOnScreen("IRISHKA AI: ONLINE", "system-message", () => {
            fetchAndUpdateStatus();
            setInterval(fetchAndUpdateStatus, 4000);
        });
        if (!screenStatusDisplay.textContent || screenStatusDisplay.textContent.toUpperCase() === "INITIALIZING...") {
             setStatusDisplay("AWAITING SYSTEM STATUS...");
        }
    }
    
    function handlePointerDownOnButton(event) {
        if (event.type === 'mousedown' && event.button !== 0) return;
        if (event.cancelable) event.preventDefault();
        // Attempt to resume audio context on user interaction
        const context = getAudioContext();
        if (context && context.state === 'suspended') {
            context.resume();
        }
        isPointerDownOnRecordButton = true;
        if (!isRecording) startRecording();
    }

    function handlePointerUpOffButton(event) {
        if (event.type === 'mouseup' && event.button !== 0) return;
        if (isPointerDownOnRecordButton) {
            isPointerDownOnRecordButton = false;
            if (isRecording) stopRecording();
        }
    }
    
    function handleGlobalPointerUp(event) {
        if (event.type === 'mouseup' && event.button !== 0) return;
        if (isPointerDownOnRecordButton) {
            isPointerDownOnRecordButton = false;
            if (isRecording) stopRecording();
        }
    }

    function handleSpacebarDown(event) {
        if (event.key === ' ' || event.code === 'Space') {
            if(event.cancelable) event.preventDefault();
            const context = getAudioContext();
            if (context && context.state === 'suspended') {
                context.resume();
            }
            if (!isRecording && !isSpacebarDown) {
                isSpacebarDown = true;
                startRecording();
            }
        }
    }

    function handleSpacebarUp(event) {
        if (event.key === ' ' || event.code === 'Space') {
            if (isSpacebarDown) {
                isSpacebarDown = false;
                if (isRecording) stopRecording();
            }
        }
    }

    async function startRecording() {
        if (isRecording) return;
        playStartRecordSound(); // << PLAY START SOUND

        if (isPlayingIrishka) {
            irishkaAudioPlayback.pause();
            irishkaAudioPlayback.currentTime = 0;
        }
        audioChunks = [];
        currentIrishkaAudioURL = null;
        if(playIrishkaButton) playIrishkaButton.disabled = true;
        if(playIrishkaButton) playIrishkaButton.classList.remove('playing');
        if (playIrishkaIcon) {
            playIrishkaIcon.innerHTML = '';
            playIrishkaIcon.className = 'play-icon';
        }
        
        setStatusDisplay("ACCESSING MICROPHONE...");

        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            const options = { mimeType: 'audio/webm' }; // Consider 'audio/ogg; codecs=opus' for better quality/compression
            mediaRecorder = new MediaRecorder(stream, options);

            mediaRecorder.ondataavailable = event => { audioChunks.push(event.data); };
            mediaRecorder.onstop = async () => {
                // playStopRecordSound() is called in stopRecording() which calls mediaRecorder.stop()
                const audioBlob = new Blob(audioChunks, { type: options.mimeType });
                stream.getTracks().forEach(track => track.stop());
                
                if(recordButton) recordButton.disabled = true;
                if(recordButton) recordButton.textContent = "PROCESS";
                setStatusDisplay("UPLOADING AUDIO...");
                typeTextOnScreen("[SENDING AUDIO...]", "system-message fast");
                
                await sendAudioToServer(audioBlob);
            };

            mediaRecorder.start();
            isRecording = true;
            if(recordButton) recordButton.textContent = "LISTENING";
            if(recordButton) recordButton.classList.add('recording');
            if(recordingLedIndicator) recordingLedIndicator.classList.add('active');
            setStatusDisplay("LISTENING...");
            typeTextOnScreen("[LISTENING...]", "system-message");

        } catch (err) {
            console.error('Error accessing microphone:', err);
            typeTextOnScreen(`[MIC ERROR: ${err.message}]`, 'error-message fast');
            setStatusDisplay(`MIC ERROR: ${err.message.substring(0,30)}...`);
            playStopRecordSound(); // Play stop sound even if start failed after an attempt
            resetRecordingButton();
        }
    }

    function stopRecording() {
        if (!isRecording) return; // Only stop if actually recording
        
        if (mediaRecorder && mediaRecorder.state === "recording") {
            playStopRecordSound(); // << PLAY STOP SOUND
            mediaRecorder.stop(); // This will trigger mediaRecorder.onstop
        } else {
            // If mediaRecorder wasn't in recording state but isRecording was true (e.g. error during start)
            playStopRecordSound();
        }
        
        isRecording = false; // Set this flag immediately
        if(recordButton) recordButton.classList.remove('recording');
        if(recordingLedIndicator) recordingLedIndicator.classList.remove('active');
        
        // UI update to "PROCESSING..." will happen in mediaRecorder.onstop
        // If mediaRecorder.onstop doesn't fire (e.g. stop called before start finished fully),
        // resetRecordingButton() will be called eventually by sendAudioToServer's finally block or error handlers.
        if (screenStatusDisplay && screenStatusDisplay.textContent === "LISTENING...") {
             setStatusDisplay("STOPPING...");
        }
    }

    function resetRecordingButton() {
        isRecording = false; 
        isSpacebarDown = false;
        isPointerDownOnRecordButton = false;
        if(recordButton) recordButton.disabled = false;
        if(recordButton) recordButton.textContent = "SPEAK";
        if(recordButton) recordButton.classList.remove('recording');
        if(recordingLedIndicator) recordingLedIndicator.classList.remove('active');
        fetchAndUpdateStatus();
    }

    function toggleIrishkaPlayback() {
        if (!currentIrishkaAudioURL) {
            typeTextOnScreen("[NO AUDIO TO PLAY]", "system-message fast");
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
                    typeTextOnScreen(`[PLAYBACK BLOCKED]`, 'error-message fast');
                    setStatusDisplay("PLAYBACK BLOCKED");
                });
            }
        }
    }

    function getMediaErrorDetail(mediaError) {
        if (!mediaError) return "Unknown audio error.";
        switch (mediaError.code) {
            case MediaError.MEDIA_ERR_ABORTED: return 'Playback aborted.';
            case MediaError.MEDIA_ERR_NETWORK: return 'Network error.';
            case MediaError.MEDIA_ERR_DECODE: return 'Audio decoding error.';
            case MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED: return 'Audio format not supported.';
            default: return 'Unknown audio error.';
        }
    }

    async function sendAudioToServer(audioBlob) {
        const formData = new FormData();
        formData.append('audio_data', audioBlob, 'user_web_audio.webm');
        currentIrishkaAudioURL = null;
        if(playIrishkaButton) playIrishkaButton.disabled = true;

        setStatusDisplay("PROCESSING AUDIO...");

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
                typeTextOnScreen(`[SERVER ERROR ${response.status}]`, 'error-message fast');
                setStatusDisplay(`SERVER ERR ${response.status}`);
                return;
            }

            console.log("Response from server:", responseData);
            typeTextOnScreen("[IRISHKA THINKING...]", "system-message fast");
            setStatusDisplay("IRISHKA THINKING...");

            if (responseData.error) {
                typeTextOnScreen(`[ERROR: ${responseData.error}]`, 'error-message fast');
                setStatusDisplay(`ERROR: ${responseData.error.substring(0,30)}...`);
            } else {
                const transcription = responseData.user_transcription;
                if (transcription || transcription === "") {
                    typeTextOnScreen(`${transcription || '[SILENCE/UNCLEAR]'}`, "user-message", () => {
                        if (responseData.llm_text_response) {
                            typeTextOnScreen(`${responseData.llm_text_response}`, "assistant-message", () => {
                                if (responseData.audio_url) {
                                    currentIrishkaAudioURL = responseData.audio_url;
                                    if(playIrishkaButton) playIrishkaButton.disabled = false;
                                    setStatusDisplay("RESPONSE READY");
                                } else {
                                    setStatusDisplay("TEXT RESPONSE ONLY");
                                }
                            });
                        } else {
                             setStatusDisplay("READY");
                        }
                    });
                } else if (responseData.llm_text_response) {
                     typeTextOnScreen(`${responseData.llm_text_response}`, "assistant-message", () => {
                        if (responseData.audio_url) {
                            currentIrishkaAudioURL = responseData.audio_url;
                            if(playIrishkaButton) playIrishkaButton.disabled = false;
                        }
                        setStatusDisplay("RESPONSE RECEIVED");
                     });
                } else {
                    typeTextOnScreen("[NO RESPONSE DATA]", "system-message fast");
                    setStatusDisplay("EMPTY RESPONSE");
                }
            }
        } catch (error) {
            console.error('Error sending/processing audio:', error);
            typeTextOnScreen(`[CLIENT ERROR]`, 'error-message fast');
            setStatusDisplay(`CLIENT ERROR: ${error.message.substring(0,30)}...`);
        } finally {
            resetRecordingButton();
        }
    }

    const MAX_SCREEN_MESSAGES = 15;
    const screenMessages = [];

    function typeTextOnScreen(text, typeClass = "system-message", callback, speed = 30) {
        if (typingInterval) {
            clearInterval(typingInterval);
            const lastTypingMsg = screenMessages.find(m => !m.fullyTyped);
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
        renderScreenMessages();

        let currentMessageElement = document.getElementById(messageEntry.id);
        if (!currentMessageElement) {
            if (callback) callback(); return;
        }

        let charIndex = 0;
        currentMessageElement.textContent = '';
        const effectiveSpeed = typeClass.includes('fast') ? 10 : speed;

        typingInterval = setInterval(() => {
            if (charIndex < text.length) {
                currentMessageElement.textContent += text[charIndex];
                charIndex++;
                if (chatScreen) chatScreen.scrollTop = chatScreen.scrollHeight;
            } else {
                clearInterval(typingInterval);
                typingInterval = null;
                messageEntry.fullyTyped = true;
                if (callback) callback();
            }
        }, effectiveSpeed);
    }

    function renderScreenMessages() {
        if (!chatScreen) return;
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
        ledElement.className = 'status-led'; // Reset
        const statusClass = getStatusClassForLed(statusType);
        if (statusClass) {
            ledElement.classList.add(statusClass);
        }
    }

    function getStatusClassForLed(statusTypeStr) {
        if (!statusTypeStr) return 'led-info';
        const s = statusTypeStr.toLowerCase();
        if (["ready", "polling", "loaded", "saved", "fresh", "idle", "ok_gpu", "ok", "active"].includes(s)) return "led-ok";
        if (["loading", "checking", "pinging", "thinking", "warn", "busy"].includes(s)) return "led-warn";
        if (["error", "na", "n/a", "timeout", "conn_error", "http_502", "http_other", "initfail", "unreachable", "bad_token", "net_error", "err", "no_token", "no_admin", "err-chk", "unhealthy", "no-conn", "ssl_err"].includes(s)) return "led-error";
        if (s === "off" || s === "cfg off" || s === "paused" || s === "disabled") return "led-off";
        return "led-info";
    }

    async function fetchAndUpdateStatus() {
        try {
            const response = await fetch('/status');
            if (!response.ok) {
                console.warn('Failed to fetch status:', response.status);
                setLedStatus(ollamaLed,'error'); setLedStatus(whisperLed,'error'); setLedStatus(barkLed,'error'); setLedStatus(telegramLed,'error');
                setStatusDisplay(`STATUS N/A (${response.status})`);
                return;
            }
            const statusData = await response.json();

            setLedStatus(ollamaLed, statusData.ollama?.type);
            setLedStatus(whisperLed, statusData.whisper?.type);
            setLedStatus(barkLed, statusData.bark?.type);
            setLedStatus(telegramLed, statusData.telegram?.type);

            const currentSystemStatus = screenStatusDisplay ? screenStatusDisplay.textContent : "";
            const nonInterruptingStatuses = [
                "LISTENING...", "STOPPING...", "UPLOADING AUDIO...", "PROCESSING AUDIO...", 
                "IRISHKA THINKING...", "PLAYING RESPONSE...", "ACCESSING MICROPHONE...",
                "PLAYBACK BLOCKED", "MIC ERROR",
            ].map(s => s.toUpperCase());
            
            let statusIsInterruptible = true;
            if (nonInterruptingStatuses.includes(currentSystemStatus) ||
                currentSystemStatus.startsWith("ERROR:") ||
                currentSystemStatus.startsWith("SERVER ERR") ||
                currentSystemStatus.startsWith("CLIENT ERR") ||
                currentSystemStatus.startsWith("AUDIO ERR")) {
                statusIsInterruptible = false;
            }

            if (statusIsInterruptible) {
                 let newStatusText = "SYSTEM READY"; 
                 if (statusData.app_overall_status) {
                     newStatusText = statusData.app_overall_status.toUpperCase();
                 }
                 setStatusDisplay(newStatusText);
            }
            
            const whisperIsReady = statusData.whisper?.type === 'ready' || statusData.whisper?.type === 'idle';
            if (recordButton && !isRecording && !isPointerDownOnRecordButton && !isSpacebarDown &&
                recordButton.textContent.toUpperCase() !== "PROCESS" && 
                recordButton.textContent.toUpperCase() !== "LISTENING" ) {
                recordButton.disabled = !whisperIsReady;
                recordButton.textContent = whisperIsReady ? "SPEAK" : "HEAR N/A";
                
                if (statusIsInterruptible) {
                    setStatusDisplay(whisperIsReady ? "READY" : "HEAR N/A");
                }
            }

        } catch (error) {
            console.error('Error fetching/updating status:', error);
            setLedStatus(ollamaLed,'error');setLedStatus(whisperLed,'error');setLedStatus(barkLed,'error');setLedStatus(telegramLed,'error');
            setStatusDisplay('STATUS UPDATE ERROR');
        }
    }
    
    // Start the application
    initializeThemingAndApp();

}); // End of DOMContentLoaded