import { TalkingHead } from "./talkinghead/talkinghead.mjs";

/**
 * --------------------------------------------------------------------------
 * Configuration
 * --------------------------------------------------------------------------
 */

/**
 * Local FastAPI adapter.
 *
 * Your Vite frontend:
 *   http://localhost:5173
 *
 * Your FastAPI backend:
 *   http://127.0.0.1:8000
 *
 * Change this if your backend is running on another port.
 */
const TTS_SERVER_URL =
    "http://127.0.0.1:8000";

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
        "http://127.0.0.1:8000/synthesize",
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
