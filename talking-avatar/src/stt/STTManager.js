/**
 * STTManager.js
 *
 * Handles microphone capture via MediaRecorder, format detection,
 * cleanup of media tracks, and communication with the local STT service.
 */

export const STTState = {
    IDLE: "IDLE",
    LISTENING: "LISTENING",
    TRANSCRIBING: "TRANSCRIBING",
    READY: "READY",
    ERROR: "ERROR",
};

export class STTManager {
    /**
     * @param {Object} options
     * @param {string} [options.endpoint] - STT server base URL or transcribe endpoint
     * @param {function(string, any): void} [options.onStateChange]
     * @param {function(Object): void} [options.onTranscript]
     * @param {function(string): void} [options.onError]
     */
    constructor(options = {}) {
        this.endpoint = (
            options.endpoint ||
            import.meta.env.VITE_STT_SERVER_URL ||
            "http://localhost:8001"
        ).replace(/\/+$/, "");

        this.onStateChange = options.onStateChange || (() => {});
        this.onTranscript = options.onTranscript || (() => {});
        this.onError = options.onError || (() => {});

        this.state = STTState.IDLE;
        this.mediaRecorder = null;
        this.stream = null;
        this.audioChunks = [];
        this.supportedMimeType = this._detectSupportedMimeType();
        this._recordingStartTime = 0;
    }

    /**
     * Detect supported browser audio MIME types.
     *
     * @private
     * @returns {string}
     */
    _detectSupportedMimeType() {
        if (typeof MediaRecorder === "undefined") {
            return "";
        }

        const candidates = [
            "audio/webm;codecs=opus",
            "audio/webm",
            "audio/ogg;codecs=opus",
            "audio/mp4",
        ];

        for (const candidate of candidates) {
            if (MediaRecorder.isTypeSupported(candidate)) {
                return candidate;
            }
        }

        return "";
    }

    /**
     * Update current state and notify listeners.
     *
     * @param {string} newState
     * @param {any} [detail]
     */
    setState(newState, detail = null) {
        this.state = newState;
        this.onStateChange(newState, detail);
    }

    /**
     * Check if currently recording.
     *
     * @returns {boolean}
     */
    isRecording() {
        return (
            this.state === STTState.LISTENING &&
            this.mediaRecorder &&
            this.mediaRecorder.state === "recording"
        );
    }

    /**
     * Start microphone recording.
     *
     * @returns {Promise<boolean>}
     */
    async startRecording() {
        if (this.isRecording()) {
            return false;
        }

        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            const msg = "Audio recording is not supported in this browser.";
            this.setState(STTState.ERROR, msg);
            this.onError(msg);
            return false;
        }

        try {
            this.audioChunks = [];
            this.stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                },
            });

            const recorderOptions = this.supportedMimeType
                ? { mimeType: this.supportedMimeType }
                : undefined;

            this.mediaRecorder = new MediaRecorder(this.stream, recorderOptions);

            this.mediaRecorder.ondataavailable = (event) => {
                if (event.data && event.data.size > 0) {
                    this.audioChunks.push(event.data);
                }
            };

            this._recordingStartTime = Date.now();
            this.mediaRecorder.start();
            this.setState(STTState.LISTENING);
            return true;
        } catch (err) {
            console.error("Microphone access error:", err);
            let userMessage = "Microphone permission is required to talk to the avatar.";
            if (err.name === "NotFoundError" || err.name === "DevicesNotFoundError") {
                userMessage = "No microphone found on your device.";
            } else if (err.name === "NotAllowedError" || err.name === "PermissionDeniedError") {
                userMessage = "Microphone permission is required to talk to the avatar.";
            }

            this._cleanupTracks();
            this.setState(STTState.ERROR, userMessage);
            this.onError(userMessage);
            return false;
        }
    }

    /**
     * Stop microphone recording and return the audio Blob.
     *
     * @returns {Promise<Blob|null>}
     */
    async stopRecording() {
        if (!this.isRecording() && !this.mediaRecorder) {
            this._cleanupTracks();
            return null;
        }

        return new Promise((resolve) => {
            const recorder = this.mediaRecorder;
            const duration = Date.now() - this._recordingStartTime;

            recorder.onstop = () => {
                const mimeType = recorder.mimeType || this.supportedMimeType || "audio/webm";
                const audioBlob = new Blob(this.audioChunks, { type: mimeType });
                this.audioChunks = [];
                this.mediaRecorder = null;
                this._cleanupTracks();

                if (duration < 200 || audioBlob.size < 500) {
                    resolve(null);
                } else {
                    resolve(audioBlob);
                }
            };

            try {
                if (recorder.state !== "inactive") {
                    recorder.stop();
                } else {
                    this._cleanupTracks();
                    resolve(null);
                }
            } catch (err) {
                console.warn("Error stopping MediaRecorder:", err);
                this._cleanupTracks();
                resolve(null);
            }
        });
    }

    /**
     * Cancel an active recording without saving.
     */
    cancelRecording() {
        if (this.mediaRecorder && this.mediaRecorder.state !== "inactive") {
            try {
                this.mediaRecorder.stop();
            } catch (e) {
                // ignore
            }
        }
        this.audioChunks = [];
        this.mediaRecorder = null;
        this._cleanupTracks();
        this.setState(STTState.IDLE);
    }

    /**
     * Stop and release all microphone tracks for safety and privacy.
     *
     * @private
     */
    _cleanupTracks() {
        if (this.stream) {
            try {
                this.stream.getTracks().forEach((track) => {
                    track.stop();
                });
            } catch (e) {
                console.warn("Error stopping audio track:", e);
            }
            this.stream = null;
        }
    }

    /**
     * Send recorded audio to local STT endpoint for transcription.
     *
     * @param {Blob} audioBlob
     * @returns {Promise<{text: string, language: string, duration: number}|null>}
     */
    async transcribe(audioBlob) {
        if (!audioBlob || audioBlob.size === 0) {
            const msg = "No speech detected. Please try again.";
            this.setState(STTState.IDLE);
            this.onError(msg);
            return null;
        }

        this.setState(STTState.TRANSCRIBING);

        const formData = new FormData();
        const extension = audioBlob.type.includes("wav") ? "wav" : "webm";
        formData.append("audio", audioBlob, `recording.${extension}`);

        const url = `${this.endpoint}/transcribe`;

        try {
            const response = await fetch(url, {
                method: "POST",
                body: formData,
            });

            if (!response.ok) {
                if (response.status === 502 || response.status === 503 || response.status === 504) {
                    throw new Error("Speech recognition service is unavailable.");
                }
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || "Could not understand the audio. Please try again.");
            }

            const data = await response.json();
            const text = (data.text || "").trim();

            if (!text) {
                const msg = "No speech detected. Please try again.";
                this.setState(STTState.READY, { text: "" });
                this.onError(msg);
                return null;
            }

            const result = {
                text,
                language: data.language || "en",
                duration: data.duration || 0,
            };

            this.setState(STTState.READY, result);
            this.onTranscript(result);
            return result;
        } catch (err) {
            console.error("Transcription error:", err);
            let userMessage = err.message;
            if (err.name === "TypeError" && err.message.includes("fetch")) {
                userMessage = "Speech recognition service is unavailable.";
            }

            this.setState(STTState.ERROR, userMessage);
            this.onError(userMessage);
            return null;
        }
    }
}
