import { TalkingHead } from "./talkinghead/talkinghead.mjs";
import { STTManager, STTState } from "./stt/STTManager.js";
import { KeyboardPushToTalk } from "./stt/keyboardPushToTalk.js";
import { avatarController, AvatarState } from "./avatar/AvatarController.js";

/**
 * --------------------------------------------------------------------------
 * Configuration
 * --------------------------------------------------------------------------
 */

/** Conversation backend base URL. */
const BACKEND_URL =
    import.meta.env.VITE_BACKEND_URL ||
    "http://localhost:8000";

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
let speechSocket = null;

/** Serialize decode and enqueue work so streamed segments stay in order. */
let speechPlaybackQueue = Promise.resolve();

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

const namasteButton =
    document.getElementById("namasteButton");

const nodButton =
    document.getElementById("nodButton");

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
 * STT Manager instance.
 */
const sttManager = new STTManager({
    endpoint: BACKEND_URL,
    onStateChange: (state, detail) => {
        updatePTTUI(state, detail);
    },
    onTranscript: (result) => {
        updateTranscriptUI("user", result.text);
        updatePTTUI("THINKING");
        avatarController.setState(AvatarState.IDLE);
        setStatus("Transcribed — generating reply...");
        forwardTranscriptToCV(result.text);
    },
    onEvent: (event) => {
        if (event.type === "llm_delta") {
            if (!window.currentAvatarReply) {
                window.currentAvatarReply = "";
                appendTranscriptUI("avatar", "");
            }
            window.currentAvatarReply += event.text;
            const replies = transcriptBody?.querySelectorAll(".transcript-reply");
            if (replies?.length) replies[replies.length - 1].textContent = window.currentAvatarReply;
        } else if (event.type === "speech") {
            speechPlaybackQueue = speechPlaybackQueue.then(() => playSpeechResult(event)).catch((error) => {
                console.error("Speech playback failed:", error);
                setStatus("Speech playback failed.");
            });
        } else if (event.type === "metrics") {
            console.info("Conversation metrics", event.metrics);
        } else if (event.type === "done") {
            window.currentAvatarReply = "";
            setStatus("Ready");
            updatePTTUI(STTState.IDLE);
        }
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
        const sent = await sttManager.stopRecording();
        if (!sent) {
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
    speechSocket?.close();
    speechSocket = null;
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

    const result = await new Promise((resolve, reject) => {
        speechSocket = new WebSocket(BACKEND_URL.replace(/^http/, "ws") + "/ws");
        speechSocket.onopen = () => speechSocket.send(JSON.stringify({
            type: "start", mode: "speak", text,
        }));
        speechSocket.onmessage = ({ data }) => {
            const event = JSON.parse(data);
            if (event.type === "speech") resolve(event);
            else if (event.type === "error") reject(new Error(event.message));
            else if (event.type === "done") {
                speechSocket?.close();
                speechSocket = null;
            }
        };
        speechSocket.onerror = () => reject(new Error("Cannot reach the conversation backend."));
    });
    await playSpeechResult(result);
}

async function playSpeechResult(result) {

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
            lipsyncLang: "en",
            isRaw: true
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

                    // Keep generated speech at unity gain.
                    mixerGainSpeech:
                        1,

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


let activeWaveTimer = null;
let activeWaveFrame = null;

function stopActiveWave(avatar) {
    if (activeWaveTimer) {
        clearTimeout(activeWaveTimer);
        activeWaveTimer = null;
    }
    if (activeWaveFrame) {
        cancelAnimationFrame(activeWaveFrame);
        activeWaveFrame = null;
    }
    if (avatar) {
        try {
            if (avatar.poseDelta && avatar.poseDelta.props) {
                if (avatar.poseDelta.props['RightForeArm.quaternion']) {
                    avatar.poseDelta.props['RightForeArm.quaternion'] = { x: 0, y: 0, z: 0 };
                }
                if (avatar.poseDelta.props['RightHand.quaternion']) {
                    avatar.poseDelta.props['RightHand.quaternion'] = { x: 0, y: 0, z: 0 };
                }
                if (avatar.poseDelta.props['LeftForeArm.quaternion']) {
                    avatar.poseDelta.props['LeftForeArm.quaternion'] = { x: 0, y: 0, z: 0 };
                }
                if (avatar.poseDelta.props['LeftHand.quaternion']) {
                    avatar.poseDelta.props['LeftHand.quaternion'] = { x: 0, y: 0, z: 0 };
                }
            }
            avatar.stopGesture(500);
            if (avatar.poseCurrentTemplate && typeof avatar.setPoseFromTemplate === "function") {
                avatar.setPoseFromTemplate(avatar.poseCurrentTemplate, 600);
            }
        } catch (err) {
            console.debug("stopActiveWave cleanup warning:", err);
        }
    }
}

/**
 * Dynamic Waving animation with natural side-to-side oscillation
 * and clean arm lowering upon completion.
 */
function wave() {

    executeAction(
        "Wave",
        async (avatar) => {
            stopActiveWave(avatar);

            // Raise right hand (mirrored = right hand wave!)
            avatar.playGesture(
                "handup",
                null,
                true,
                400
            );
            avatar.setMood("happy");

            const startTime = performance.now();
            const waveDurationMs = 2800;

            function animateWave(now) {
                const elapsed = now - startTime;
                if (elapsed >= waveDurationMs) {
                    stopActiveWave(avatar);
                    avatar.setMood("neutral");
                    return;
                }

                // Sinusoidal wave oscillation (envelope smooths start and end)
                const progress = elapsed / waveDurationMs;
                const envelope = Math.sin(Math.PI * progress);
                const angle = Math.sin((elapsed / 1000) * Math.PI * 4.5) * 0.36 * envelope;

                if (avatar.poseDelta && avatar.poseDelta.props) {
                    avatar.poseDelta.props['RightForeArm.quaternion'] = { x: 0, y: 0, z: angle };
                    avatar.poseDelta.props['RightHand.quaternion'] = { x: 0, y: 0, z: angle * 0.75 };
                }

                activeWaveFrame = requestAnimationFrame(animateWave);
            }

            activeWaveFrame = requestAnimationFrame(animateWave);
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
            stopActiveWave(avatar);

            avatar.playGesture(
                "thumbup",
                3,
                true,
                400
            );
            avatar.setMood("happy");

            setTimeout(() => {
                if (avatar.poseCurrentTemplate && typeof avatar.setPoseFromTemplate === "function") {
                    avatar.setPoseFromTemplate(avatar.poseCurrentTemplate, 600);
                }
                avatar.setMood("neutral");
            }, 3000);
        }
    );
}


/**
 * Namaste / Folded hands gesture.
 */
function namaste() {

    executeAction(
        "Namaste",
        async (avatar) => {
            stopActiveWave(avatar);

            avatar.playGesture(
                "namaste",
                3,
                false,
                500
            );
            avatar.setMood("happy");

            setTimeout(() => {
                if (avatar.poseCurrentTemplate && typeof avatar.setPoseFromTemplate === "function") {
                    avatar.setPoseFromTemplate(avatar.poseCurrentTemplate, 600);
                }
                avatar.setMood("neutral");
            }, 3000);
        }
    );
}


/**
 * Head nod gesture.
 */
function nod() {

    executeAction(
        "Nod",
        async (avatar) => {
            avatar.playGesture("yes", 2);
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

        stopActiveWave(avatar);

        avatar.stopAnimation();

        avatar.stopPose();

        avatar.stopGesture();

        if (avatar.poseCurrentTemplate && typeof avatar.setPoseFromTemplate === "function") {
            avatar.setPoseFromTemplate(avatar.poseCurrentTemplate, 400);
        }

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

    namasteButton?.addEventListener(
        "click",
        namaste
    );

    nodButton?.addEventListener(
        "click",
        nod
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
 * Computer Vision Integration (Live Video Feed, Bounding Boxes, Gestures)
 * --------------------------------------------------------------------------
 */

const CV_WS_URL = import.meta.env.VITE_CV_URL || "ws://localhost:8765";
let cvSocket = null;
let cvReconnectTimer = null;
let lastGestureTime = 0;

// Frontend Camera Streaming & Zero-Lag Local Preview State
let frontendVideoStream = null;
let frontendVideoElement = null;
let frontendCaptureTimer = null;
let frontendFrameSeq = 0;
let isFrontendCameraActive = false;
let latestCVMetadata = null;
let lastMetadataReceivedTime = 0;

// Local preview performance tracking
let previewFrameCount = 0;
let lastPreviewFPSCheck = performance.now();
let measuredPreviewFPS = 0;
let measuredInferenceLatencyMs = 0;

const offscreenCanvas = document.createElement("canvas");
offscreenCanvas.width = 640;
offscreenCanvas.height = 480;
const offscreenCtx = offscreenCanvas.getContext("2d");

async function startFrontendCamera() {
    if (isFrontendCameraActive) return;
    try {
        if (!frontendVideoElement) {
            frontendVideoElement = document.createElement("video");
            frontendVideoElement.id = "cvFrontendVideo";
            frontendVideoElement.autoplay = true;
            frontendVideoElement.playsInline = true;
            frontendVideoElement.muted = true;
            frontendVideoElement.style.display = "none";
            document.body.appendChild(frontendVideoElement);
        }

        const stream = await navigator.mediaDevices.getUserMedia({
            video: {
                width: { ideal: 640 },
                height: { ideal: 480 },
                frameRate: { ideal: 30 }
            },
            audio: false
        });

        frontendVideoStream = stream;
        frontendVideoElement.srcObject = stream;
        await frontendVideoElement.play();
        isFrontendCameraActive = true;

        updateCVPlaceholder(false);
        updateCVBadge(true);
        console.log("[CV Frontend Camera] Local camera active (640x480). Zero-lag preview running.");

        requestAnimationFrame(renderFrontendPreviewLoop);
        startFrontendFrameStreaming();
    } catch (err) {
        console.warn("[CV Frontend Camera] Local camera not active, falling back to backend stream:", err);
    }
}

function stopFrontendCamera() {
    isFrontendCameraActive = false;
    if (frontendCaptureTimer) {
        clearInterval(frontendCaptureTimer);
        frontendCaptureTimer = null;
    }
    if (frontendVideoStream) {
        frontendVideoStream.getTracks().forEach(t => t.stop());
        frontendVideoStream = null;
    }
    if (frontendVideoElement) {
        frontendVideoElement.srcObject = null;
    }
}

function startFrontendFrameStreaming() {
    if (frontendCaptureTimer) clearInterval(frontendCaptureTimer);

    // Stream frames to CV backend at ~15 FPS (every 66 ms)
    frontendCaptureTimer = setInterval(async () => {
        if (!isFrontendCameraActive || !frontendVideoElement || frontendVideoElement.readyState < 2) {
            return;
        }
        if (!cvSocket || cvSocket.readyState !== WebSocket.OPEN) {
            return;
        }

        try {
            offscreenCtx.drawImage(frontendVideoElement, 0, 0, 640, 480);
            offscreenCanvas.toBlob(async (blob) => {
                if (!blob || !cvSocket || cvSocket.readyState !== WebSocket.OPEN) return;
                const jpegBuffer = await blob.arrayBuffer();
                const jpegLen = jpegBuffer.byteLength;

                // Protocol: [4B Magic 'FRM1'] + [4B Seq uint32] + [8B Timestamp ms float64] + [4B Length uint32] + [JPEG bytes]
                const totalLen = 20 + jpegLen;
                const packet = new Uint8Array(totalLen);

                // 'FRM1' (0x46524D31)
                packet[0] = 0x46; packet[1] = 0x52; packet[2] = 0x4D; packet[3] = 0x31;
                const view = new DataView(packet.buffer);
                frontendFrameSeq = (frontendFrameSeq + 1) >>> 0;
                view.setUint32(4, frontendFrameSeq, false);
                view.setFloat64(8, performance.now(), false);
                view.setUint32(16, jpegLen, false);

                packet.set(new Uint8Array(jpegBuffer), 20);
                cvSocket.send(packet.buffer);
            }, "image/jpeg", 0.70);
        } catch (e) {
            console.debug("[CV Frontend Camera] Frame capture skipped:", e);
        }
    }, 66);
}

function renderFrontendPreviewLoop(now) {
    if (!isFrontendCameraActive) return;

    // Measure local preview FPS
    previewFrameCount++;
    if (now - lastPreviewFPSCheck >= 1000) {
        measuredPreviewFPS = Math.round((previewFrameCount * 1000) / (now - lastPreviewFPSCheck));
        previewFrameCount = 0;
        lastPreviewFPSCheck = now;
    }

    const canvas = document.getElementById("cvCanvas");
    if (canvas && frontendVideoElement && frontendVideoElement.readyState >= 2) {
        const ctx = canvas.getContext("2d");
        if (ctx) {
            if (canvas.width !== 640 || canvas.height !== 480) {
                canvas.width = 640;
                canvas.height = 480;
            }

            // Draw local video with selfie mirroring
            ctx.save();
            ctx.translate(canvas.width, 0);
            ctx.scale(-1, 1);
            ctx.drawImage(frontendVideoElement, 0, 0, canvas.width, canvas.height);
            ctx.restore();

            // Expire stale overlays older than 1.0s
            let currentMeta = null;
            if (latestCVMetadata && (now - lastMetadataReceivedTime <= 1000)) {
                currentMeta = latestCVMetadata;
            }

            renderCVOverlayCanvas(ctx, canvas.width, canvas.height, currentMeta, true);
        }
    }

    requestAnimationFrame(renderFrontendPreviewLoop);
}

window.addEventListener("beforeunload", stopFrontendCamera);

function updateCVBadge(connected) {
    const badge = document.getElementById("cvStatusBadge");
    if (!badge) return;
    if (connected) {
        badge.className = "cv-badge connected";
        badge.textContent = "Live";
    } else {
        badge.className = "cv-badge disconnected";
        badge.textContent = "Disconnected";
    }
}

function updateCVPlaceholder(show, message = "") {
    const ph = document.getElementById("cvPlaceholder");
    if (!ph) return;
    ph.style.display = show ? "flex" : "none";
    if (message) {
        const textEl = ph.querySelector("div:nth-child(2)");
        if (textEl) textEl.textContent = message;
    }
}

function initCVConnection() {
    if (cvSocket && (cvSocket.readyState === WebSocket.OPEN || cvSocket.readyState === WebSocket.CONNECTING)) {
        return;
    }

    try {
        cvSocket = new WebSocket(CV_WS_URL);
        cvSocket.binaryType = "arraybuffer";

        cvSocket.onopen = () => {
            console.log("[CV] Connected to CV adapter WebSocket at", CV_WS_URL);
            updateCVBadge(true);
            updateCVPlaceholder(false);
            startFrontendCamera();
        };

        cvSocket.onmessage = async (event) => {
            if (event.data instanceof ArrayBuffer) {
                // Backend camera fallback stream (if frontend camera not running)
                if (!isFrontendCameraActive) {
                    await handleCVBinaryFrame(event.data);
                }
            } else if (typeof event.data === "string") {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === "frame" || data.type === "frame_metadata") {
                        latestCVMetadata = data;
                        lastMetadataReceivedTime = performance.now();
                        if (data.timestamp) {
                            const tsMs = data.timestamp > 1e11 ? data.timestamp : data.timestamp * 1000;
                            measuredInferenceLatencyMs = Math.max(0, Math.round(performance.now() - tsMs));
                        }
                        if (!isFrontendCameraActive) {
                            const canvas = document.getElementById("cvCanvas");
                            if (canvas) {
                                const ctx = canvas.getContext("2d");
                                if (ctx) renderCVOverlayCanvas(ctx, canvas.width, canvas.height, data, false);
                            }
                        }
                    } else {
                        handleCVEvent(data);
                    }
                } catch (err) {
                    console.error("[CV] Failed to parse JSON event:", err);
                }
            }
        };

        cvSocket.onclose = () => {
            updateCVBadge(false);
            updateCVPlaceholder(true, "Connecting to CV feed...");
            clearTimeout(cvReconnectTimer);
            cvReconnectTimer = setTimeout(initCVConnection, 2000);
        };

        cvSocket.onerror = (err) => {
            console.warn("[CV] WebSocket connection error:", err);
            cvSocket.close();
        };
    } catch (e) {
        console.error("[CV] Failed to create WebSocket:", e);
        clearTimeout(cvReconnectTimer);
        cvReconnectTimer = setTimeout(initCVConnection, 3000);
    }
}

async function handleCVBinaryFrame(arrayBuffer) {
    if (arrayBuffer.byteLength < 4) return;
    const view = new DataView(arrayBuffer);
    const jsonLen = view.getUint32(0, false); // big-endian uint32
    if (arrayBuffer.byteLength < 4 + jsonLen) return;

    const jsonBytes = new Uint8Array(arrayBuffer, 4, jsonLen);
    const metaStr = new TextDecoder("utf-8").decode(jsonBytes);
    let metadata;
    try {
        metadata = JSON.parse(metaStr);
        window.lastCVMetadata = metadata;
    } catch (err) {
        return;
    }

    const jpegBytes = new Uint8Array(arrayBuffer, 4 + jsonLen);
    const blob = new Blob([jpegBytes], { type: "image/jpeg" });

    try {
        const imageBitmap = await createImageBitmap(blob);
        renderCVFrame(imageBitmap, metadata);
    } catch (err) {
        console.debug("[CV] Frame decode skipped:", err);
    }
}


/**
 * --------------------------------------------------------------------------
 * Real-Time Action Mirroring Engine
 * --------------------------------------------------------------------------
 */
let currentMirroredAction = null;
let actionDebounceTimer = null;

function syncActionMirroring(people) {
    if (!head) return;

    // Detect action of primary person or first person with an action
    let activeAction = null;
    if (people && people.length > 0) {
        for (const p of people) {
            if (p.actions && p.actions.length > 0) {
                activeAction = p.actions[0];
                break;
            }
        }
    }

    if (activeAction) {
        // Clear any pending debounce to lower the arm
        if (actionDebounceTimer) {
            clearTimeout(actionDebounceTimer);
            actionDebounceTimer = null;
        }

        if (currentMirroredAction === activeAction) {
            return; // Continuously holding current mirrored action
        }

        console.log(`[ActionMirror] Starting mirror: ${activeAction}`);
        currentMirroredAction = activeAction;

        if (activeAction === "hand_raised") {
            stopActiveWave(head);
            head.playGesture("handup", null, true, 400);
            head.setMood("neutral");
            setStatus("Mirroring: Hand Raised ✋");
        } else if (activeAction === "waving") {
            stopActiveWave(head);
            head.playGesture("handup", null, true, 400);
            head.setMood("happy");
            setStatus("Mirroring: Waving 👋");

            function animateContinuousWave() {
                if (currentMirroredAction !== "waving") return;
                const now = performance.now();
                const angle = Math.sin((now / 1000) * Math.PI * 4.5) * 0.36;
                if (head.poseDelta && head.poseDelta.props) {
                    head.poseDelta.props['RightForeArm.quaternion'] = { x: 0, y: 0, z: angle };
                    head.poseDelta.props['RightHand.quaternion'] = { x: 0, y: 0, z: angle * 0.75 };
                }
                activeWaveFrame = requestAnimationFrame(animateContinuousWave);
            }
            activeWaveFrame = requestAnimationFrame(animateContinuousWave);
        } else if (activeAction === "thumbs_up") {
            stopActiveWave(head);
            head.playGesture("thumbup", null, true, 400);
            head.setMood("happy");
            setStatus("Mirroring: Thumbs Up 👍");
        } else if (activeAction === "namaste") {
            stopActiveWave(head);
            head.playGesture("namaste", null, false, 500);
            head.setMood("neutral");
            setStatus("Mirroring: Namaste 🙏");
        } else if (activeAction === "victory") {
            stopActiveWave(head);
            head.playGesture("handup", null, true, 400);
            head.setMood("happy");
            setStatus("Mirroring: Victory ✌️");
        }
    } else {
        // No active action on current frame
        if (currentMirroredAction !== null) {
            if (!actionDebounceTimer) {
                actionDebounceTimer = setTimeout(() => {
                    console.log(`[ActionMirror] Lowering hand / ending: ${currentMirroredAction}`);
                    currentMirroredAction = null;
                    stopActiveWave(head);
                    if (head) {
                        try {
                            head.stopGesture(400);
                            if (head.poseCurrentTemplate && typeof head.setPoseFromTemplate === "function") {
                                head.setPoseFromTemplate(head.poseCurrentTemplate, 500);
                            }
                            head.setMood("neutral");
                        } catch (err) {
                            console.debug("[ActionMirror] Gesture stop warning:", err);
                        }
                    }
                    setStatus("Idle");
                    actionDebounceTimer = null;
                }, 250);
            }
        }
    }
}

function renderCVOverlayCanvas(ctx, canvasW, canvasH, metadata, isMirrored = false) {
    if (!metadata) return;
    const people = metadata.people || [];
    let activeAction = "None";

    // Real-time action mirroring
    syncActionMirroring(people);

    people.forEach((p) => {
        if (!p.bbox || p.bbox.length < 4) return;
        const [x1, y1, x2, y2] = p.bbox;
        const w = x2 - x1;
        const h = y2 - y1;

        // Coordinate transformation for mirrored selfie camera
        const drawX = isMirrored ? (canvasW - x2) : x1;
        const drawY = y1;

        const isKnown = p.display_name && p.display_name !== "Unknown";
        const primaryColor = isKnown ? "#22c55e" : "#00f0ff";

        // Bounding Box
        ctx.strokeStyle = primaryColor;
        ctx.lineWidth = 2.5;
        ctx.strokeRect(drawX, drawY, w, h);

        // Name & Track Label
        const nameText = isKnown ? p.display_name : "Unknown";
        const idText = `#t${p.track_id}`;
        let actionBadge = "";
        if (p.actions && p.actions.length > 0) {
            actionBadge = ` • [${p.actions.join(", ")}]`;
            activeAction = p.actions.join(", ");
        }
        const label = `${nameText} (${idText})${actionBadge}`;

        ctx.font = "bold 13px Inter, sans-serif";
        const textWidth = ctx.measureText(label).width;
        const labelH = 22;
        const labelW = textWidth + 14;
        const labelX = Math.max(0, drawX);
        const labelY = Math.max(0, drawY - labelH);

        ctx.fillStyle = isKnown ? "rgba(34, 197, 94, 0.9)" : "rgba(0, 0, 0, 0.75)";
        ctx.fillRect(labelX, labelY, labelW, labelH);

        ctx.fillStyle = isKnown ? "#ffffff" : primaryColor;
        ctx.fillText(label, labelX + 7, labelY + 15);
    });

    // Draw detected objects
    const objects = metadata.objects || [];
    objects.forEach((obj) => {
        if (!obj.bbox || obj.bbox.length < 4) return;
        const [x1, y1, x2, y2] = obj.bbox;
        const w = x2 - x1;
        const h = y2 - y1;
        const drawX = isMirrored ? (canvasW - x2) : x1;

        ctx.strokeStyle = "#f59e0b";
        ctx.lineWidth = 1.5;
        ctx.strokeRect(drawX, y1, w, h);
        ctx.fillStyle = "rgba(245, 158, 11, 0.85)";
        const label = `${obj.label} (${Math.round(obj.confidence * 100)}%)`;
        ctx.fillRect(drawX, Math.max(0, y1 - 20), ctx.measureText(label).width + 8, 20);
        ctx.fillStyle = "#ffffff";
        ctx.fillText(label, drawX + 4, Math.max(0, y1 - 6));
    });

    // Update Info Bar
    const peopleCountEl = document.getElementById("cvPeopleCount");
    if (peopleCountEl) peopleCountEl.textContent = `👥 People: ${people.length}`;
    const actionBadgeEl = document.getElementById("cvActionBadge");
    if (actionBadgeEl) actionBadgeEl.textContent = `⚡ Action: ${activeAction}`;

    const badge = document.getElementById("cvStatusBadge");
    if (badge && isFrontendCameraActive) {
        badge.textContent = `Live (${measuredPreviewFPS} FPS | ${measuredInferenceLatencyMs}ms)`;
    }

    // Update Track List cards
    updateTrackListUI(people);
}

function renderCVFrame(imgBitmap, metadata) {
    window.lastCVMetadata = metadata;
    const canvas = document.getElementById("cvCanvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    if (canvas.width !== imgBitmap.width || canvas.height !== imgBitmap.height) {
        canvas.width = imgBitmap.width;
        canvas.height = imgBitmap.height;
    }

    // Draw camera image
    ctx.drawImage(imgBitmap, 0, 0);

    // Render overlays
    renderCVOverlayCanvas(ctx, canvas.width, canvas.height, metadata, false);
}

function updateTrackListUI(people) {
    const listEl = document.getElementById("cvTrackList");
    if (!listEl) return;
    if (!people || people.length === 0) {
        listEl.innerHTML = `<div style="color: #6b7280; font-size: 12px; text-align: center; padding: 6px;">No persons currently tracked</div>`;
        return;
    }
    listEl.innerHTML = people.map(p => {
        const isKnown = p.display_name && p.display_name !== "Unknown";
        const name = isKnown ? p.display_name : "Unknown Person";
        const action = p.actions && p.actions.length ? p.actions.join(", ") : "Idle";
        return `
            <div class="track-card ${isKnown ? "known" : ""}">
                <div>
                    <div class="track-name">${name}</div>
                    <div class="track-sub">Track ID: #${p.track_id} ${p.person_id ? `• ${p.person_id.substring(0, 8)}...` : ""}</div>
                </div>
                <div class="info-pill" style="font-size: 11px;">${action}</div>
            </div>
        `;
    }).join("");
}

function forwardTranscriptToCV(text) {
    if (cvSocket && cvSocket.readyState === WebSocket.OPEN) {
        cvSocket.send(JSON.stringify({
            type: "transcript",
            text: text,
        }));
    }
}

function handleCVEvent(event) {
    if (!event || !event.event_type) return;

    if (event.event_type === "action_detected") {
        handleActionDetected(event);
    } else if (event.event_type === "action_ended") {
        handleActionEnded(event);
    } else if (event.event_type === "person_renamed") {
        appendTranscriptUI("avatar", `👋 Pleased to meet you, ${event.new_name}!`);
        setStatus(`Renamed track to ${event.new_name}`);
    } else if (event.event_type === "person_identified") {
        if (event.display_name && event.display_name !== "Unknown") {
            setStatus(`Identified: ${event.display_name}`);
        }
    }
}

function handleActionDetected(event) {
    console.log(`[CV] Action detected: ${event.action} from track #${event.track_id}`);
    if (event.action === "hand_raised") {
        avatarController.setState(AvatarState.LISTENING);
        setStatus("Hand raised ✋ — listening...");
    }
}

function handleActionEnded(event) {
    console.log(`[CV] Action ended: ${event.action} from track #${event.track_id}`);
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

        initCVConnection();

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
