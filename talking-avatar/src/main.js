import { TalkingHead } from "./talkinghead/talkinghead.mjs";
import { STTManager, STTState } from "./stt/STTManager.js";
import { KeyboardPushToTalk } from "./stt/keyboardPushToTalk.js";
import { avatarController, AvatarState } from "./avatar/AvatarController.js";
import { ActionQueue } from "./avatar/ActionQueue.js";
import { ListeningNods } from "./avatar/ListeningNods.js";
import { SpeechActions } from "./avatar/SpeechActions.js";
import { playClipAction } from "./avatar/clipAction.js";
import { CLIP_ACTIONS, chooseClip, mountAnimationControls } from "./avatar/clipActions.js";
import { createHeadPitchLimiter } from "./avatar/headPitch.js";
import { GestureTimeline } from "./avatar/GestureTimeline.js";
import { TalkingGestures } from "./avatar/TalkingGestures.js";
import { decodeSpeechAudio } from "./avatar/speechAudio.js";
import { acceptLandmarkFrame, drawLandmarkOverlay, freshLandmarkFrame, landmarkStatus } from "./cv/landmarkOverlay.js";
import { GestureEvents } from "./cv/gestureEvents.js";
import { CameraGestureResponses } from "./cv/cameraGestureResponses.js";
import { SpeakingEvents } from "./cv/speakingEvents.js";
import { setupCameraResize } from "./ui/cameraResize.js";
import { setupAvatarFraming } from "./ui/avatarFraming.js";
import { CallPreferences, avatarOptions, mountCallSettings } from "./ui/callSettings.js";
import { ConversationLog } from "./ui/conversationLog.js";

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
let avatarReady = false;
let loadedAvatarId = null;
let refitAvatar = () => {};
const callPreferences = new CallPreferences();
let avatarZoomFactor = callPreferences.zoomFactor;
let refreshCallSettings = () => {};


/** Serialize decode and enqueue work so streamed segments stay in order. */
let speechPlaybackQueue = Promise.resolve();
const avatarActionQueue = new ActionQueue((error) => console.error("Avatar action failed:", error));
const limitHeadPitch = createHeadPitchLimiter();
const clipActions = Object.fromEntries(Object.keys(CLIP_ACTIONS).map((action) => [
    action, () => playCatalogAction(action),
]));
const speechActions = new SpeechActions(() => Boolean(head?.isSpeaking));
const gestureTimeline = new GestureTimeline((action) => {
    if (action === "talking") return; // Speech already owns automatic low-priority talking motion.
    if (Object.hasOwn(CLIP_ACTIONS, action)) playCatalogAction(action, "tagged");
}, () => avatarActionQueue.busy || document.hidden);
const cameraResponses = new CameraGestureResponses(
    (action) => playCatalogAction(action, "camera"),
    () => !avatarReady || avatarActionQueue.busy || document.hidden
);
const talkingGestures = new TalkingGestures(
    (url, signal) => playClipAction(head, url, signal, { armsOnly: true }),
    () => avatarActionQueue.busy || document.hidden
        || gestureTimeline.hasUpcomingForeground(head?.audioCtx?.currentTime ?? 0)
);
let speechGeneration = 0;
const listeningNods = new ListeningNods(
    () => avatarReady ? head : null,
    () => avatarActionQueue.busy || Boolean(head?.isSpeaking) || document.hidden

);

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

const idleButton =
    document.getElementById("idleButton");









const stopButton =
    document.getElementById("stopButton");

const transcriptBody =
    document.getElementById("transcriptBody");
const conversationLog = new ConversationLog(transcriptBody);
const speechLatencyElement = document.getElementById("speechLatency");
let speechTurnTiming = null;

function setSpeechLatency(key, value) {
    if (!speechTurnTiming) return;
    speechTurnTiming.milestones.set(key, value);
    if (!speechLatencyElement) return;
    speechLatencyElement.hidden = false;
    speechLatencyElement.textContent = [...speechTurnTiming.milestones.values()].join(" · ");
}

function elapsedSpeechMs(from, to = performance.now()) {
    return from == null ? null : Math.max(0, to - from);
}

function formatLatency(ms) {
    return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`;
}

function finalizedTimeLabel() {
    return new Intl.DateTimeFormat("en-US", {
        hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true,
    }).format(new Date());
}

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
    pttBanner.setAttribute("aria-pressed", String(state === STTState.LISTENING));

    switch (state) {
        case STTState.LISTENING:
            pttBanner.classList.add("ptt-listening");
            pttIcon.textContent = "🔴";
            pttText.textContent = "Listening · release to send";
            break;

        case STTState.TRANSCRIBING:
            pttBanner.classList.add("ptt-transcribing");
            pttIcon.textContent = "⏳";
            pttText.textContent = "Finishing transcript…";
            break;

        case "THINKING":
            pttBanner.classList.add("ptt-thinking");
            pttIcon.textContent = "◌";
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
            pttText.textContent = "Hold Space to speak";
            break;
    }
}

/**
 * Append a message and end any previous streamed reply.
 *
 * @param {"user"|"avatar"|"thinking"|"error"|"info"} type
 * @param {string} text
 */
function updateTranscriptUI(type, text) {
    conversationLog.finishReply();
    conversationLog.append(type, text);
}

/**
 * STT Manager instance.
 */
const speakingEvents = new SpeakingEvents();
function visiblePeopleForSpeech() {
    if (!latestCVMetadata || performance.now() - lastMetadataReceivedTime > 1500) return [];
    return (latestCVMetadata.people || []).map((person) => ({
        ...person, source_id: latestCVMetadata.source_id,
    }));
}
const sttManager = new STTManager({
    endpoint: BACKEND_URL,
    getStartOptions: async () => {
        if (!callPreferences.provider) await loadRuntimeInfo();
        if (!callPreferences.provider) throw new Error("Voice settings are unavailable. Check the backend connection.");
        return { voice: callPreferences.voice };
    },
    getContext: () => ({ speaker: speakingEvents.snapshot() }),
    onCaptureStart: () => {
        conversationLog.clearErrors();
        speechGeneration++;
        gestureTimeline.clear();
        void talkingGestures.clear();
        speechActions.clear();
        listeningNods.start();
        for (const event of speakingEvents.start(visiblePeopleForSpeech())) handleCVEvent(event);
    },
    onAudioActivity: (sample) => {
        speakingEvents.addAudio(sample);
        listeningNods.addAudio(sample);
    },
    onCaptureEnd: () => {
        listeningNods.stop();
        for (const event of speakingEvents.stop()) handleCVEvent(event);
    },
    onStateChange: (state, detail) => {
        if (state === STTState.LISTENING) {
            speechTurnTiming = { releaseAt: null, transcriptAt: null, firstAudioAt: null, milestones: new Map() };
            if (speechLatencyElement) speechLatencyElement.hidden = true;
        } else if (state === STTState.TRANSCRIBING && speechTurnTiming) {
            speechTurnTiming.releaseAt = performance.now();
            setSpeechLatency("release", "Released · waiting for transcript");
        }
        updatePTTUI(state, detail);
    },
    onTranscript: (result) => {
        conversationLog.commitUserDraft(
            result.text, speakingEvents.snapshot()?.display_name, finalizedTimeLabel(),
        );
        updatePTTUI("THINKING");
        avatarController.setState(AvatarState.IDLE);
        setStatus("Transcribed — generating reply...");
    },
    onEvent: (event) => {
        if (event.type === "transcript_partial") {
            conversationLog.setUserDraft(event.text || "");
        } else if (event.type === "transcript") {
            if (speechTurnTiming?.releaseAt != null) {
                speechTurnTiming.transcriptAt = performance.now();
                setSpeechLatency("transcript", `release → transcript ${formatLatency(elapsedSpeechMs(speechTurnTiming.releaseAt))}`);
            }
        } else if (event.type === "llm_complete") {
            if (speechTurnTiming?.transcriptAt != null) {
                setSpeechLatency("llm", `transcript → reply + gestures ${formatLatency(elapsedSpeechMs(speechTurnTiming.transcriptAt))}`);
            }
            setSpeechLatency("gesture", `tag parse ${Number(event.gesture_parse_ms || 0).toFixed(2)} ms`);
            conversationLog.appendGestureTags(event.gesture_tags);
            conversationLog.finishReply(finalizedTimeLabel());
        } else if (event.type === "avatar_action") {
            const actions = { gesture: clipActions };
            const action = actions[event.tool]?.[event.action];
            if (action) speechActions.add(action);
        } else if (event.type === "llm_delta") {
            conversationLog.appendReplyDelta(event.text);
        } else if (event.type === "speech") {
            const generation = speechGeneration;
            const receivedAt = performance.now();
            if (speechTurnTiming?.firstAudioAt == null) {
                if (speechTurnTiming) {
                    speechTurnTiming.firstAudioAt = receivedAt;
                    setSpeechLatency("firstAudio", `release → first audio data ${formatLatency(elapsedSpeechMs(speechTurnTiming.releaseAt, receivedAt))}`);
                }
            }
            speechPlaybackQueue = speechPlaybackQueue.then(() => playSpeechResult(event, generation, receivedAt)).catch((error) => {
                if (generation !== speechGeneration) return;
                speechActions.clear();
                console.error("Speech playback failed:", error);
                setStatus("Speech playback failed.");
            });
        } else if (event.type === "metrics") {
            updateProviderBadge(event.metrics);
            console.info("Conversation metrics", event.metrics);
            if (event.metrics?.kokoro_first_byte_ms != null) {
                setSpeechLatency("kokoro", `Kokoro request → first PCM ${formatLatency(event.metrics.kokoro_first_byte_ms)}`);
            }
        } else if (event.type === "done") {
            conversationLog.finishReply();
            setStatus("Ready");
            updatePTTUI(STTState.IDLE);
        }
    },
    onError: (errorMsg) => {
        conversationLog.discardUserDraft();
        speechActions.clear();
        listeningNods.stop();
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
        if (isSpeaking || head?.isSpeaking || avatarActionQueue.busy) {
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
        conversationLog.discardUserDraft();
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

/** Play audio immediately; gesture clips load independently when dispatched. */
async function playSpeechResult(result, generation, receivedAt = performance.now()) {
    if (generation !== speechGeneration) return;
    const decodeStarted = performance.now();
    const audioBuffer = await decodeSpeechAudio(head.audioCtx, result);
    const decodeMs = performance.now() - decodeStarted;
    if (generation !== speechGeneration) return;

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

            wdurations: result.wdurations,
            onAudioStart: (start, rate, duration) => {
                if (generation !== speechGeneration) return;
                const gestureScheduleStarted = performance.now();
                gestureTimeline.schedule(result.gestures, start, rate);
                const gestureScheduleMs = performance.now() - gestureScheduleStarted;
                talkingGestures.until(start + duration / rate);
                speechActions.startSpeech();
                console.info("Speech playback latency", {
                    segment_index: result.index, chunk_index: result.chunk_index,
                    decode_ms: Number(decodeMs.toFixed(2)),
                    receive_to_audio_start_ms: Number((performance.now() - receivedAt).toFixed(2)),
                    gesture_schedule_ms: Number(gestureScheduleMs.toFixed(3)),
                });
            }
        },
        {
            lipsyncLang: "en",
            isRaw: true
        }
    );
    listeningNods.stop();

    setStatus("Speaking...");
}


/**
 * --------------------------------------------------------------------------
 * Stop speech
 * --------------------------------------------------------------------------
 */

function stopSpeech() {
    speechGeneration++;
    gestureTimeline.clear();
    cameraResponses.clear();
    void talkingGestures.clear();
    speechActions.clear();

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
                    // Runs after FBX playback and TalkingHead's additive pose layers.
                    update: () => {
                        limitHeadPitch(head);
                        if (head?.audioCtx) {
                            gestureTimeline.advance(head.audioCtx.currentTime);
                            cameraResponses.update();
                            talkingGestures.update(head.audioCtx.currentTime);
                        }
                    },

                    /*
                     * Camera controls.
                     */
                    cameraRotateEnable:
                        false,

                    cameraPanEnable:
                        false,

                    cameraZoomEnable:
                        false,

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
            avatarOptions(callPreferences.avatar),

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

        refitAvatar = setupAvatarFraming(head, avatarElement, () => avatarZoomFactor);
        loadedAvatarId = callPreferences.avatar;
        avatarReady = true;
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

    listeningNods.stop();
    avatarActionQueue.clear();

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



function playCatalogAction(action, kind = "manual") {
    const spec = CLIP_ACTIONS[action];
    if (!spec) return;
    queueAvatarAction(spec.label, (avatar, signal) => playClipAction(
        avatar, chooseClip(action), signal, { armsOnly: action === "talking" }), action === "talking" ? "talking" : kind);
}

function queueAvatarAction(name, action, kind = "manual") {
    avatarActionQueue.enqueue((signal) => executeAction(name, async (avatar) => {
        await talkingGestures.interrupt();
        if (signal.aborted) return;
        listeningNods.stopNod();
        resetAvatarGesture(avatar);
        await action(avatar, signal);
    }), { kind });
}

function resetAvatarGesture(avatar) {
    avatar.stopGesture(200);
    if (avatar.poseCurrentTemplate) avatar.setPoseFromTemplate(avatar.poseCurrentTemplate, 600);
}

/**
 * Stop everything.
 */
function stopEverything() {

    listeningNods.stop();
    avatarActionQueue.clear();

    try {

        /*
         * Stop TTS and pending HTTP request.
         */
        stopSpeech();

        /*
         * Stop any active speech-to-text recording.
         */
        sttManager.cancelRecording();
        conversationLog.discardUserDraft();
        updatePTTUI(STTState.IDLE);
        avatarController.setState(AvatarState.IDLE);

        /*
         * Stop avatar actions.
         */
        const avatar =
            requireHead();

        resetAvatarGesture(avatar);

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

async function changeAvatar(id) {
    if (!avatarReady) throw new Error("Your avatar is still loading. Try again in a moment.");
    if (id === loadedAvatarId) return;
    const previous = loadedAvatarId;
    stopEverything();
    avatarReady = false;
    head.stop();
    showLoading();
    setLoading("Loading avatar…");
    try {
        await head.showAvatar(avatarOptions(id));
        loadedAvatarId = id;
        avatarReady = true;
        refitAvatar();
        avatarController.setHead(head);
        avatarController.setState(AvatarState.IDLE);
        setStatus("Ready");
    } catch (error) {
        try {
            await head.showAvatar(avatarOptions(previous));
            avatarReady = true;
            refitAvatar();
            avatarController.setHead(head);
            avatarController.setState(AvatarState.IDLE);
        } catch (restoreError) {
            console.error("Could not restore avatar:", restoreError);
        }
        setStatus(avatarReady ? "Previous avatar restored" : "Avatar unavailable. Reload to retry.");
        throw new Error("Could not load that avatar. Please try another model.");
    } finally {
        hideLoading();
        if (!document.hidden && avatarReady) head.start();
    }
}

function registerEventHandlers() {
    refreshCallSettings = mountCallSettings(callPreferences, changeAvatar, (factor) => {
        avatarZoomFactor = factor;
        refitAvatar();
    });

    setupCameraResize(document.getElementById("app"), document.getElementById("cameraResize"),
        document.getElementById("cvCanvasContainer"), document.getElementById("leftPanel"));
    const overlayToggle = document.getElementById("cameraOverlays");
    overlayToggle?.addEventListener("click", () => {
        showCVOverlays = !showCVOverlays;
        overlayToggle.setAttribute("aria-pressed", String(showCVOverlays));
        overlayToggle.setAttribute("aria-label", `${showCVOverlays ? "Hide" : "Show"} computer vision overlays`);
        overlayToggle.title = `${showCVOverlays ? "Hide" : "Show"} computer vision overlays`;
        overlayToggle.classList.toggle("is-active", showCVOverlays);
    });
    let holdingMic = false;
    const startMic = () => {
        if (holdingMic) return;
        holdingMic = true;
        pushToTalk.onStart();
    };
    const stopMic = () => {
        if (!holdingMic) return;
        holdingMic = false;
        pushToTalk.onStop();
    };
    const cancelMic = () => {
        if (!holdingMic) return;
        holdingMic = false;
        pushToTalk.onCancel();
    };
    pttBanner.addEventListener("pointerdown", (event) => {
        if (!event.isPrimary || event.button !== 0) return;
        event.preventDefault();
        pttBanner.setPointerCapture(event.pointerId);
        startMic();
    });
    pttBanner.addEventListener("pointerup", stopMic);
    pttBanner.addEventListener("pointercancel", cancelMic);
    pttBanner.addEventListener("lostpointercapture", cancelMic);
    pttBanner.addEventListener("keydown", (event) => {
        if (![" ", "Enter"].includes(event.key)) return;
        event.preventDefault();
        if (!event.repeat) startMic();
    });
    pttBanner.addEventListener("keyup", (event) => {
        if (![" ", "Enter"].includes(event.key)) return;
        event.preventDefault();
        stopMic();
    });
    window.addEventListener("blur", cancelMic);
    document.addEventListener("visibilitychange", () => { if (document.hidden) cancelMic(); });

    /**
     * Avatar controls.
     */
    idleButton?.addEventListener(
        "click",
        idle
    );






    mountAnimationControls(document.getElementById("animationControls"), (action) => clipActions[action]());



    stopButton?.addEventListener(
        "click",
        stopEverything
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
            cameraResponses.clear();
            speakingEvents.cameraReset();
            gestureEvents.active.clear();
            latestCVMetadata = null;
            window.lastCVMetadata = null;
            window.lastCVWindows = [];
            window.lastCVKeypoints = null;
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
                    // Keypoint inference completes independently of object boxes.
                    // Keep its latest result even when this box frame is older.
                    window.lastCVKeypoints = acceptLandmarkFrame(window.lastCVKeypoints, data);
                    if (data.event_type === "window_summary") {
                        const summaries = window.lastCVWindows || [];
                        const previous = summaries.find((item) => item.window_id === data.summary.window_id);
                        if (!previous || previous.revision < data.summary.revision) {
                            window.lastCVWindows = summaries
                                .filter((item) => item.window_id !== data.summary.window_id)
                                .concat(data.summary)
                                .sort((a, b) => a.window_id - b.window_id)
                                .slice(-30);
                        }
                    } else if (data.type === "keypoints") {
                        // Stored above; the local preview loop draws this result.
                    } else if (data.type === "frame" || data.type === "frame_metadata") {
                        if (latestCVMetadata && data.seq < latestCVMetadata.seq) return;
                        latestCVMetadata = data;
                        window.lastCVMetadata = data;
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
const gestureEvents = new GestureEvents();
let showCVOverlays = true;


function renderCVOverlayCanvas(ctx, canvasW, canvasH, metadata, isMirrored = false) {
    const nowMs = performance.now();
    const pointFrame = freshLandmarkFrame(window.lastCVKeypoints, nowMs);
    const landmarkBadge = document.getElementById("cvLandmarkBadge");
    if (landmarkBadge) landmarkBadge.textContent = landmarkStatus(window.lastCVKeypoints, nowMs);
    if (showCVOverlays && pointFrame) {
        drawLandmarkOverlay(ctx, canvasW, canvasH, pointFrame, {
            mirrored: isMirrored,
            visibleTrackIds: metadata ? new Set((metadata.people || []).map((person) => person.track_id)) : undefined,
        });
    }
    if (!metadata) {
            for (const event of speakingEvents.update(null, [], nowMs)) handleCVEvent(event);
        return;
    }
    const recentPoints = pointFrame !== null;
    const people = (metadata.people || []).map((person) => ({
        ...person,
        source_id: metadata.source_id,
        actions: recentPoints
            ? pointFrame.keypoints.filter((item) => item.track_id === person.track_id).flatMap((item) => item.gestures)
            : (person.actions || []),
    }));
    let activeAction = "None";

    // Real-time action mirroring
    for (const event of gestureEvents.update(people, nowMs)) handleCVEvent(event);
    for (const event of speakingEvents.update(pointFrame, people, nowMs)) handleCVEvent(event);

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

        if (!showCVOverlays) return;

        // Bounding box and identity label.
        ctx.strokeStyle = primaryColor;
        ctx.lineWidth = 2.5;
        ctx.strokeRect(drawX, drawY, w, h);

        // Name & Track Label
        const nameText = isKnown ? p.display_name : "Unknown";
        const idText = `#t${p.track_id}`;
        let actionBadge = "";
        const gestures = p.actions;
        if (gestures.length > 0) {
            actionBadge = ` • [${gestures.join(", ")}]`;
            activeAction = gestures.join(", ");
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
    const objects = showCVOverlays ? (metadata.objects || []) : [];
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
        badge.textContent = "Live";
        badge.title = `Preview ${measuredPreviewFPS} FPS; inference ${measuredInferenceLatencyMs} ms`;
    }

    // Update Track List cards
    if (document.getElementById("cameraDetails")?.open) updateTrackListUI(people);
}

function renderCVFrame(imgBitmap, metadata) {
    window.lastCVMetadata = metadata;
    window.lastCVKeypoints = acceptLandmarkFrame(window.lastCVKeypoints, metadata);
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


let perceptionOverlayTimer;
function appendPerceptionEvent(message) {
    console.log(`[CV] ${message}`);
    const overlay = document.getElementById("perceptionOverlay");
    if (!overlay) return;
    clearTimeout(perceptionOverlayTimer);
    overlay.textContent = message;
    overlay.hidden = false;
    perceptionOverlayTimer = setTimeout(() => { overlay.hidden = true; }, 2500);
}

function handleCVEvent(event) {
    if (!event || !event.event_type) return;

    if (event.event_type === "action_detected") {
        handleActionDetected(event);
    } else if (event.event_type === "speaking") {
        const message = `${event.display_name} speaking ${event.phase}`;
        appendPerceptionEvent(message);
    } else if (event.event_type === "action_ended") {
        handleActionEnded(event);
    } else if (event.event_type === "person_renamed") {
        appendPerceptionEvent(`Name saved: ${event.display_name || event.data?.new_name || "Unknown"}`);
    } else if (event.event_type === "person_identified") {
        if (event.display_name && event.display_name !== "Unknown") {
            setStatus(`Identified: ${event.display_name}`);
        }
    }
}

function handleActionDetected(event) {
    const name = event.display_name || `Unknown (track #${event.track_id})`;
    const message = `${name}: ${event.action === "wave" ? "waving" : event.action}`;
    appendPerceptionEvent(message);
    cameraResponses.handle(event);
}

function handleActionEnded(event) {
    console.log(`[CV] Action ended: ${event.action} from track #${event.track_id}`);
}

/**
 * --------------------------------------------------------------------------
 * Application bootstrap
 * --------------------------------------------------------------------------
 */

function updateProviderBadge(info) {
    const badge = document.getElementById("sttBadge");
    const provider = info?.stt || info?.stt_provider;
    const tts = info?.tts || info?.tts_provider;
    if (tts) { callPreferences.setProvider(tts); refreshCallSettings(); }
    if (badge && provider) {
        badge.textContent = `STT: ${provider}`;
        badge.title = info.stt_model || "";
    }
}

async function loadRuntimeInfo() {
    try {
        const response = await fetch(`${BACKEND_URL}/health`, { signal: AbortSignal.timeout(2000) });
        if (!response.ok) throw new Error("Backend health check failed");
        updateProviderBadge((await response.json()).providers);
    } catch {
        const badge = document.getElementById("sttBadge");
        if (badge) badge.textContent = "STT: unavailable";
    }
}

async function bootstrap() {

    try {

        registerEventHandlers();
        loadRuntimeInfo();

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
