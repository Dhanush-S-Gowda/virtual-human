import { TalkingHead } from "./talkinghead/talkinghead.mjs";
import { STTManager, STTState } from "./stt/STTManager.js";
import { KeyboardPushToTalk } from "./stt/keyboardPushToTalk.js";
import { avatarController, AvatarState } from "./avatar/AvatarController.js";

/**
 * --------------------------------------------------------------------------
 * Configuration
 * --------------------------------------------------------------------------
 */

/**
 * Local FastAPI adapter.
 *
 * For local development the default is:
 *   http://localhost:8000
 *
 * In Docker, set VITE_TTS_SERVER_URL to the mapped host URL.
 */
const TTS_SERVER_URL =
    import.meta.env.VITE_TTS_SERVER_URL ||
    "http://localhost:8000";

/**
 * Local faster-whisper STT service.
 *
 * Default is:
 *   http://localhost:8001
 */
const STT_SERVER_URL =
    import.meta.env.VITE_STT_SERVER_URL ||
    "http://localhost:8001";

/**
 * LLM chat endpoint.
 *
 * POST /chat is added to the existing TTS FastAPI server (port 8000).
 * The browser talks to it at TTS_SERVER_URL; we never call llama.cpp directly.
 */
const LLM_SERVER_URL =
    import.meta.env.VITE_TTS_SERVER_URL ||
    "http://localhost:8000";

/**
 * Kokoro voice.
 *
 * af_bella is an English female voice.
 *
 * Other Kokoro voices can be used if your backend/model
 * supports them.
 */
const KOKORO_VOICE =
    "af_bella";

/**
 * Speech speed.
 */
const KOKORO_SPEED = 1.0;


/**
 * --------------------------------------------------------------------------
 * Global state
 * --------------------------------------------------------------------------
 */

/**
 * Global TalkingHead instance.
 */
let head = null;

/**
 * Prevent multiple TTS requests from playing simultaneously.
 */
let speechRequestController = null;

/**
 * Tracks whether the avatar is currently speaking.
 */
let isSpeaking = false;


/**
 * --------------------------------------------------------------------------
 * DOM references
 * --------------------------------------------------------------------------
 */

const avatarElement =
    document.getElementById("avatar");

const loadingElement =
    document.getElementById("loading");

const statusElement =
    document.getElementById("status");

const speechTextElement =
    document.getElementById("speechText");

const speakButton =
    document.getElementById("speakButton");

const idleButton =
    document.getElementById("idleButton");

const waveButton =
    document.getElementById("waveButton");

const danceButton =
    document.getElementById("danceButton");

const poseButton =
    document.getElementById("poseButton");

const thumbButton =
    document.getElementById("thumbButton");

const happyButton =
    document.getElementById("happyButton");

const neutralButton =
    document.getElementById("neutralButton");

const stopButton =
    document.getElementById("stopButton");

const transcriptBody =
    document.getElementById("transcriptBody");

const pttBanner =
    document.getElementById("pttBanner");

const pttIcon =
    document.getElementById("pttIcon");

const pttText =
    document.getElementById("pttText");


/**
 * --------------------------------------------------------------------------
 * STT & Push-to-Talk Setup
 * --------------------------------------------------------------------------
 */

/**
 * Update Push-to-Talk visual feedback banner.
 *
 * @param {string} state
 * @param {string} [message]
 */
function updatePTTUI(state, message = "") {
    if (!pttBanner || !pttText || !pttIcon) {
        return;
    }

    pttBanner.className = "";

    switch (state) {
        case STTState.LISTENING:
            pttBanner.classList.add("ptt-listening");
            pttIcon.textContent = "🔴";
            pttText.textContent = "LISTENING... Release SPACE when finished";
            break;

        case STTState.TRANSCRIBING:
            pttBanner.classList.add("ptt-transcribing");
            pttIcon.textContent = "⏳";
            pttText.textContent = "TRANSCRIBING...";
            break;

        case "THINKING":
            pttBanner.classList.add("ptt-thinking");
            pttIcon.textContent = "🧠";
            pttText.textContent = "Thinking...";
            break;

        case STTState.ERROR:
            pttBanner.classList.add("ptt-error");
            pttIcon.textContent = "⚠️";
            pttText.textContent = message || "Speech recognition error";
            break;

        case STTState.READY:
        case STTState.IDLE:
        default:
            pttBanner.classList.add("ptt-idle");
            pttIcon.textContent = "🎤";
            pttText.textContent = "Hold SPACE to speak";
            break;
    }
}

/**
 * Update transcript box display.
 *
 * @param {"user"|"error"|"info"} type
 * @param {"user"|"avatar"|"thinking"|"error"|"info"} type
 * @param {string} text
 */
function updateTranscriptUI(type, text) {
    if (!transcriptBody) {
        return;
    }

    if (type === "user") {
        transcriptBody.innerHTML = `
            <div class="transcript-user">You said:</div>
            <div class="transcript-text">"${text}"</div>
        `;
    } else if (type === "avatar") {
        transcriptBody.innerHTML = `
            <div class="transcript-avatar">Avatar:</div>
            <div class="transcript-reply">${text}</div>
        `;
    } else if (type === "thinking") {
        transcriptBody.innerHTML = `
            <div class="transcript-thinking">🧠 Thinking...</div>
        `;
    } else if (type === "error") {
        transcriptBody.innerHTML = `
            <div class="transcript-error">${text}</div>
        `;
    } else if (type === "info") {
        transcriptBody.innerHTML = `
            <span style="color: #9ca3af;">${text}</span>
        `;
    }
}

/**
 * Append to transcript without clearing the existing content.
 * Used to show the avatar reply below the user transcript.
 *
 * @param {"avatar"|"thinking"|"error"} type
 * @param {string} text
 */
function appendTranscriptUI(type, text) {
    if (!transcriptBody) {
        return;
    }

    const el = document.createElement("div");

    if (type === "avatar") {
        el.innerHTML = `
            <div class="transcript-avatar">Avatar:</div>
            <div class="transcript-reply">${text}</div>
        `;
    } else if (type === "thinking") {
        el.className = "transcript-thinking";
        el.textContent = "🧠 Thinking...";
    } else if (type === "error") {
        el.className = "transcript-error";
        el.textContent = text;
    }

    transcriptBody.appendChild(el);
    el.scrollIntoView({ behavior: "smooth", block: "end" });
}

/**
 * Send transcribed text to the local LLM and display the reply.
 *
 * Calls POST /chat on the TTS server (which proxies to llama.cpp on
 * the Windows host). The browser never touches llama.cpp directly.
 *
 * @param {string} userText - Transcribed user speech
 * @returns {Promise<void>}
 */
async function sendToLLM(userText) {
    // Show THINKING state
    updatePTTUI("THINKING");
    appendTranscriptUI("thinking", "");
    setStatus("Thinking...");

    try {
        const response = await fetch(`${LLM_SERVER_URL}/chat`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ message: userText }),
            signal: AbortSignal.timeout(130_000), // slightly longer than server timeout
        });

        if (!response.ok) {
            let detail = "LLM error";
            try {
                const errData = await response.json();
                detail = errData.detail || detail;
            } catch (_) {
                // ignore JSON parse failure
            }

            if (response.status === 503) {
                throw new Error(
                    "The local LLM is not running. " +
                    "Start llama.cpp on the host with: " +
                    "llama serve --hf-repo Qwen/Qwen3-8B-GGUF " +
                    "--hf-file qwen3-8b-q4_k_m.gguf --device Vulkan0 --port 8080"
                );
            } else if (response.status === 504) {
                throw new Error("The LLM took too long to respond. Please try again.");
            } else {
                throw new Error(detail);
            }
        }

        const data = await response.json();
        const reply = (data.reply || "").trim();

        if (!reply) {
            throw new Error("The LLM returned an empty reply.");
        }

        // Replace "Thinking..." with the actual reply
        updateTranscriptUI("user", userText);
        appendTranscriptUI("avatar", reply);
        setStatus("Ready");
        updatePTTUI(STTState.IDLE);
        avatarController.setState(AvatarState.IDLE);

    } catch (err) {
        console.error("LLM error:", err);

        let userMsg = err.message;
        if (err.name === "TimeoutError" || err.name === "AbortError") {
            userMsg = "LLM request timed out. Please try again.";
        } else if (err.name === "TypeError" && err.message.includes("fetch")) {
            userMsg = "Cannot reach the LLM server. Is it running?";
        }

        // Show error in transcript (keep user speech visible)
        updateTranscriptUI("user", userText);
        appendTranscriptUI("error", `⚠️ ${userMsg}`);
        setStatus("LLM unavailable");
        updatePTTUI(STTState.ERROR, userMsg);
        avatarController.setState(AvatarState.IDLE);

        setTimeout(() => {
            updatePTTUI(STTState.IDLE);
            setStatus("Ready");
        }, 5000);
    }
}

/**
 * STT Manager instance.
 */
const sttManager = new STTManager({
    endpoint: STT_SERVER_URL,
    onStateChange: (state, detail) => {
        updatePTTUI(state, detail);
    },
    onTranscript: async (result) => {
        // 1. Show user transcript immediately
        updateTranscriptUI("user", result.text);
        setStatus("Ready");
        setTimeout(() => {
            updatePTTUI(STTState.IDLE);
            avatarController.setState(AvatarState.IDLE);
        }, 1200);
        setStatus("Transcribed — sending to LLM...");

        // 2. Send to LLM (async — shows THINKING state inside)
        await sendToLLM(result.text);
    },
    onError: (errorMsg) => {
        updateTranscriptUI("error", errorMsg);
        setStatus(errorMsg);
        updatePTTUI(STTState.ERROR, errorMsg);
        avatarController.setState(AvatarState.IDLE);
        setTimeout(() => {
            updatePTTUI(STTState.IDLE);
        }, 3000);
    },
});

/**
 * Push-to-Talk keyboard controller (Spacebar).
 */
const pushToTalk = new KeyboardPushToTalk({
    key: "Space",
    onStart: async () => {
        if (isSpeaking) {
            stopEverything();
        }
        avatarController.setState(AvatarState.LISTENING);
        setStatus("Listening...");
        const started = await sttManager.startRecording();
        if (!started) {
            avatarController.setState(AvatarState.IDLE);
        }
    },
    onStop: async () => {
        setStatus("Transcribing...");
        const audioBlob = await sttManager.stopRecording();
        if (audioBlob) {
            await sttManager.transcribe(audioBlob);
        } else {
            const msg = "No speech detected. Please try again.";
            updateTranscriptUI("error", msg);
            updatePTTUI(STTState.IDLE);
            avatarController.setState(AvatarState.IDLE);
            setStatus("Ready");
        }
    },
    onCancel: () => {
        sttManager.cancelRecording();
        updatePTTUI(STTState.IDLE);
        avatarController.setState(AvatarState.IDLE);
        setStatus("Ready");
    },
});


/**
 * --------------------------------------------------------------------------
 * UI helpers
 * --------------------------------------------------------------------------
 */

/**
 * Update status text.
 *
 * @param {string} message
 */
function setStatus(message) {

    if (!statusElement) {
        return;
    }

    statusElement.textContent =
        message;
}


/**
 * Update loading text.
 *
 * @param {string} message
 */
function setLoading(message) {

    if (!loadingElement) {
        return;
    }

    loadingElement.textContent =
        message;
}


/**
 * Hide loading indicator.
 */
function hideLoading() {

    if (!loadingElement) {
        return;
    }

    loadingElement.style.display =
        "none";
}


/**
 * Show loading indicator.
 */
function showLoading() {

    if (!loadingElement) {
        return;
    }

    loadingElement.style.display =
        "block";
}


/**
 * Set speaking state.
 *
 * @param {boolean} speaking
 */
function setSpeakingState(speaking) {

    isSpeaking = speaking;

    if (speakButton) {

        speakButton.disabled =
            speaking;
    }
}


/**
 * --------------------------------------------------------------------------
 * TalkingHead helpers
 * --------------------------------------------------------------------------
 */

/**
 * Check whether TalkingHead was initialized.
 *
 * @returns {TalkingHead}
 */
function requireHead() {

    if (!head) {

        throw new Error(
            "TalkingHead has not been initialized yet."
        );
    }

    return head;
}


/**
 * Safely execute an avatar action.
 *
 * This prevents one failed animation from
 * breaking all subsequent UI actions.
 *
 * @param {string} name
 * @param {(avatar: TalkingHead) => Promise<void>|void} action
 */
async function executeAction(
    name,
    action
) {

    try {

        const avatar =
            requireHead();

        setStatus(name);

        await action(avatar);

        /*
         * Don't overwrite a speech state.
         */
        if (!isSpeaking) {
            setStatus("Ready");
        }

    } catch (error) {

        console.error(
            `Avatar action "${name}" failed:`,
            error
        );

        setStatus(
            `Action failed: ${name}`
        );
    }
}


/**
 * --------------------------------------------------------------------------
 * Audio / Base64 helpers
 * --------------------------------------------------------------------------
 */


/**
 * --------------------------------------------------------------------------
 * Kokoro TTS
 * --------------------------------------------------------------------------
 */

/**
 * Stop any currently running Kokoro request.
 */
function cancelSpeechRequest() {

    if (speechRequestController) {

        try {
            speechRequestController.abort();
        } catch (error) {

            console.warn(
                "Failed to abort TTS request:",
                error
            );
        }

        speechRequestController =
            null;
    }
}

/**
 * Convert base64 audio returned by the backend
 * into an ArrayBuffer.
 */
function base64ToArrayBuffer(base64) {
    const binaryString = atob(base64);

    const length = binaryString.length;

    const bytes = new Uint8Array(length);

    for (let i = 0; i < length; i++) {
        bytes[i] = binaryString.charCodeAt(i);
    }

    return bytes.buffer;
}


/**
 * Generate speech using the local Kokoro backend
 * and send the resulting AudioBuffer + word timings
 * to TalkingHead.
 */
async function speakWithKokoro(text) {

    if (!head) {
        throw new Error(
            "TalkingHead has not been initialized."
        );
    }

    const response = await fetch(
        `${TTS_SERVER_URL}/synthesize`,
        {
            method: "POST",

            headers: {
                "Content-Type": "application/json"
            },

            body: JSON.stringify({
                text: text,
                voice: "af_bella",
                speed: 1.0
            })
        }
    );

    if (!response.ok) {
        const errorText = await response.text();

        throw new Error(
            `Kokoro server returned ${response.status}: ${errorText}`
        );
    }

    const result = await response.json();

    /*
     * Validate backend response.
     */
    if (!result.audio_base64) {
        throw new Error(
            "Kokoro response does not contain audio_base64."
        );
    }

    if (
        !Array.isArray(result.words) ||
        !Array.isArray(result.wtimes) ||
        !Array.isArray(result.wdurations)
    ) {
        throw new Error(
            "Kokoro response is missing word timing information."
        );
    }

    /*
     * Convert base64 → WAV ArrayBuffer.
     */
    const wavBuffer =
        base64ToArrayBuffer(
            result.audio_base64
        );

    /*
     * IMPORTANT:
     *
     * TalkingHead does NOT want the WAV
     * ArrayBuffer directly.
     *
     * Decode the WAV using the same AudioContext
     * that TalkingHead uses.
     */
    if (head.audioCtx.state === "suspended") {
        await head.audioCtx.resume();
    }

    const audioBuffer =
        await head.audioCtx.decodeAudioData(
            wavBuffer.slice(0)
        );

    /*
     * Send AudioBuffer + word timings to TalkingHead.
     *
     * TalkingHead will use the English lip-sync
     * module to convert the words/timings into
     * Oculus visemes.
     */
    head.speakAudio(
        {
            audio: audioBuffer,

            words: result.words,

            wtimes: result.wtimes,

            wdurations: result.wdurations
        },
        {
            lipsyncLang: "en"
        }
    );

    setStatus("Speaking...");
}


/**
 * --------------------------------------------------------------------------
 * Speech button handler
 * --------------------------------------------------------------------------
 */

 async function handleSpeak() {

     const text =
         speechTextElement?.value?.trim();

     if (!text) {
         setStatus("Enter something to say.");
         return;
     }

     try {

         setStatus("Generating speech...");

         await speakWithKokoro(text);

     } catch (error) {

         console.error(
             "Kokoro speech failed:",
             error
         );

         setStatus(
             error instanceof Error
                 ? error.message
                 : "Speech failed."
         );
     }
 }

/**
 * --------------------------------------------------------------------------
 * Stop speech
 * --------------------------------------------------------------------------
 */

function stopSpeech() {

    /*
     * Cancel pending FastAPI request.
     */
    cancelSpeechRequest();

    /*
     * Stop browser-native speech if it somehow
     * exists from an older version of the app.
     */
    try {

        if (
            "speechSynthesis" in window
        ) {

            window.speechSynthesis.cancel();
        }

    } catch (error) {

        console.debug(
            "Browser speech cancellation failed:",
            error
        );
    }

    /*
     * Stop TalkingHead speech.
     */
    try {

        if (head) {

            if (
                typeof head.stopSpeaking ===
                "function"
            ) {

                head.stopSpeaking();
            }
        }

    } catch (error) {

        console.error(
            "Failed to stop TalkingHead speech:",
            error
        );
    }

    setSpeakingState(false);
}


/**
 * --------------------------------------------------------------------------
 * Initialize TalkingHead
 * --------------------------------------------------------------------------
 */

async function initializeAvatar() {

    try {

        if (!avatarElement) {

            throw new Error(
                "#avatar element was not found."
            );
        }

        setStatus(
            "Initializing TalkingHead..."
        );

        setLoading(
            "Initializing avatar..."
        );


        /**
         * ------------------------------------------------------------------
         * Create TalkingHead instance
         * ------------------------------------------------------------------
         */

        head =
            new TalkingHead(
                avatarElement,
                {

                    cameraView:
                        "upper",

                    /*
                     * English lip-sync module.
                     *
                     * This is now actually going to be
                     * used by speakAudio().
                     */
                    lipsyncModules:
                        ["en"],

                    /*
                     * Camera controls.
                     */
                    cameraRotateEnable:
                        true,

                    cameraPanEnable:
                        false,

                    cameraZoomEnable:
                        true,

                    /*
                     * Avatar idle behaviour.
                     */
                    avatarIdleEyeContact:
                        0.25,

                    avatarIdleHeadMove:
                        0.5,

                    /*
                     * Speaking behaviour.
                     */
                    avatarSpeakingEyeContact:
                        0.5,

                    avatarSpeakingHeadMove:
                        0.5,

                    /*
                     * Rendering.
                     */
                    modelFPS:
                        30,

                    modelPixelRatio:
                        Math.min(
                            window.devicePixelRatio ||
                                1,
                            2
                        ),
                }
            );

        await head.lipsyncGetProcessor("en");


        /**
         * ------------------------------------------------------------------
         * Load avatar
         * ------------------------------------------------------------------
         */

        setStatus(
            "Loading avatar..."
        );

        setLoading(
            "Loading avatar..."
        );


        await head.showAvatar(
            {

                url:
                    "/avatars/brunette.glb",

                body:
                    "F",

                avatarMood:
                    "neutral",

                lipsyncLang:
                    "en",
            },

            (event) => {

                if (
                    !event ||
                    !event.lengthComputable
                ) {

                    return;
                }

                const progress =
                    Math.min(
                        100,
                        Math.round(
                            (
                                event.loaded /
                                event.total
                            ) *
                                100
                        )
                    );

                setLoading(
                    `Loading avatar: ${progress}%`
                );
            }
        );


        /**
         * ------------------------------------------------------------------
         * Start TalkingHead
         * ------------------------------------------------------------------
         */

        head.start();

        avatarController.setHead(head);
        avatarController.setState(AvatarState.IDLE);

        hideLoading();

        setStatus(
            "Ready"
        );


        console.log(
            "TalkingHead initialized successfully."
        );


    } catch (error) {

        console.error(
            "TalkingHead initialization failed:",
            error
        );

        setLoading(
            "Failed to load avatar."
        );

        setStatus(
            error instanceof Error
                ? error.message
                : String(error)
        );
    }
}


/**
 * --------------------------------------------------------------------------
 * Avatar actions
 * --------------------------------------------------------------------------
 */

/**
 * Idle.
 */
function idle() {

    executeAction(
        "Idle",
        async (avatar) => {

            /*
             * Stop any speech first.
             */
            stopSpeech();

            avatar.stopAnimation();

            avatar.stopPose();

            avatar.stopGesture();

            avatar.setMood(
                "neutral"
            );

            avatar.lookAtCamera(
                1000
            );
        }
    );
}


/**
 * Wave.
 */
function wave() {

    executeAction(
        "Wave",
        async (avatar) => {

            avatar.playGesture(
                "handup",
                3,
                false,
                500
            );
        }
    );
}


/**
 * Dance.
 */
function dance() {

    executeAction(
        "Dance",
        async (avatar) => {

            await avatar.playAnimation(
                "/poses/dance.fbx",

                null,

                8,

                0,

                0.01
            );
        }
    );
}


/**
 * Walking animation.
 */
function walk() {

    executeAction(
        "Walking",
        async (avatar) => {

            await avatar.playAnimation(
                "/animations/walking.fbx",

                null,

                8,

                0,

                0.01
            );
        }
    );
}


/**
 * Play initial pose contained in dance.fbx.
 */
function pose() {

    executeAction(
        "Pose",
        async (avatar) => {

            await avatar.playPose(
                "/poses/dance.fbx",

                null,

                5,

                0,

                0.01
            );
        }
    );
}


/**
 * Thumb-up gesture.
 */
function thumbUp() {

    executeAction(
        "Thumb up",
        async (avatar) => {

            avatar.playGesture(
                "thumbup",
                3,
                false,
                500
            );
        }
    );
}


/**
 * Happy mood.
 */
function happy() {

    executeAction(
        "Happy",
        async (avatar) => {

            avatar.setMood(
                "happy"
            );

            avatar.lookAtCamera(
                1000
            );
        }
    );
}


/**
 * Neutral mood.
 */
function neutral() {

    executeAction(
        "Neutral",
        async (avatar) => {

            avatar.setMood(
                "neutral"
            );

            avatar.lookAtCamera(
                1000
            );
        }
    );
}


/**
 * Stop everything.
 */
function stopEverything() {

    try {

        /*
         * Stop TTS and pending HTTP request.
         */
        stopSpeech();

        /*
         * Stop any active speech-to-text recording.
         */
        sttManager.cancelRecording();
        updatePTTUI(STTState.IDLE);
        avatarController.setState(AvatarState.IDLE);

        /*
         * Stop avatar actions.
         */
        const avatar =
            requireHead();

        avatar.stopAnimation();

        avatar.stopPose();

        avatar.stopGesture();

        avatar.setMood(
            "neutral"
        );

        avatar.lookAtCamera(
            500
        );


        setStatus(
            "Stopped"
        );


    } catch (error) {

        console.error(
            "Failed to stop avatar:",
            error
        );

        setStatus(
            "Stop failed."
        );
    }
}


/**
 * --------------------------------------------------------------------------
 * Event handlers
 * --------------------------------------------------------------------------
 */

function registerEventHandlers() {

    /**
     * Kokoro TTS.
     */
    speakButton?.addEventListener(
        "click",
        handleSpeak
    );


    /**
     * Avatar controls.
     */
    idleButton?.addEventListener(
        "click",
        idle
    );

    waveButton?.addEventListener(
        "click",
        wave
    );

    danceButton?.addEventListener(
        "click",
        dance
    );

    poseButton?.addEventListener(
        "click",
        pose
    );

    thumbButton?.addEventListener(
        "click",
        thumbUp
    );

    happyButton?.addEventListener(
        "click",
        happy
    );

    neutralButton?.addEventListener(
        "click",
        neutral
    );

    stopButton?.addEventListener(
        "click",
        stopEverything
    );


    /**
     * Press Enter to speak.
     */
    speechTextElement?.addEventListener(
        "keydown",
        (event) => {

            if (
                event.key !==
                "Enter"
            ) {

                return;
            }

            event.preventDefault();

            handleSpeak();
        }
    );


    /**
     * Pause TalkingHead when the browser
     * tab becomes hidden.
     */
    document.addEventListener(
        "visibilitychange",
        () => {

            try {

                if (!head) {
                    return;
                }

                /*
                 * Do not restart the renderer if
                 * the document is hidden.
                 */
                if (
                    document.visibilityState ===
                    "visible"
                ) {

                    head.start();

                } else {

                    head.stop();
                }

            } catch (error) {

                console.error(
                    "Visibility handling failed:",
                    error
                );
            }
        }
    );
}


/**
 * --------------------------------------------------------------------------
 * Application bootstrap
 * --------------------------------------------------------------------------
 */

async function bootstrap() {

    try {

        registerEventHandlers();

        pushToTalk.attach();

        await initializeAvatar();

    } catch (error) {

        console.error(
            "Application bootstrap failed:",
            error
        );

        setStatus(
            "Application initialization failed."
        );
    }
}


/**
 * --------------------------------------------------------------------------
 * Start application
 * --------------------------------------------------------------------------
 */

bootstrap();
