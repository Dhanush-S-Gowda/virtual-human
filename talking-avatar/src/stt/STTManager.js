/** Capture microphone audio and send chunks to the conversation websocket. */

export const STTState = {
    IDLE: "IDLE",
    LISTENING: "LISTENING",
    TRANSCRIBING: "TRANSCRIBING",
    READY: "READY",
    ERROR: "ERROR",
};

export class STTManager {
    constructor(options = {}) {
        const endpoint = (options.endpoint || import.meta.env.VITE_BACKEND_URL || "http://localhost:8000").replace(/\/+$/, "");
        this.websocketUrl = endpoint.replace(/^http/, "ws") + "/ws";
        this.onStateChange = options.onStateChange || (() => {});
        this.onTranscript = options.onTranscript || (() => {});
        this.onError = options.onError || (() => {});
        this.onEvent = options.onEvent || (() => {});
        this.state = STTState.IDLE;
        this.mediaRecorder = null;
        this.stream = null;
        this.socket = null;
        this.sessionId = crypto.randomUUID();
        this.supportedMimeType = this._detectSupportedMimeType();
    }

    _detectSupportedMimeType() {
        if (typeof MediaRecorder === "undefined") return "";
        return ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"]
            .find((type) => MediaRecorder.isTypeSupported(type)) || "";
    }

    setState(state, detail = null) {
        this.state = state;
        this.onStateChange(state, detail);
    }

    isRecording() {
        return this.state === STTState.LISTENING && this.mediaRecorder?.state === "recording";
    }

    async startRecording() {
        if (this.isRecording()) return false;
        try {
            if (this.socket && this.socket.readyState !== WebSocket.CLOSED) {
                if (this.socket.readyState === WebSocket.OPEN) {
                    this.socket.send(JSON.stringify({ type: "cancel" }));
                }
                this.socket.close();
            }
            this.stream = await navigator.mediaDevices.getUserMedia({
                audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
            });
            this.socket = new WebSocket(this.websocketUrl);
            await new Promise((resolve, reject) => {
                this.socket.onopen = resolve;
                this.socket.onerror = () => reject(new Error("Cannot reach the conversation backend."));
            });
            this.socket.onmessage = ({ data }) => {
                try {
                    const event = JSON.parse(data);
                    this.onEvent(event);
                    if (event.type === "transcript") this.onTranscript(event);
                    if (event.type === "error") {
                        this.setState(STTState.ERROR, event.message);
                        this.onError(event.message || "Conversation failed.");
                    }
                    if (event.type === "done") this.setState(STTState.READY);
                } catch (error) {
                    console.error("Invalid conversation event:", error);
                }
            };
            this.socket.onclose = () => {
                if (this.state === STTState.TRANSCRIBING) this.setState(STTState.READY);
            };
            this.mediaRecorder = new MediaRecorder(
                this.stream,
                this.supportedMimeType ? { mimeType: this.supportedMimeType } : undefined,
            );
            this.mediaRecorder.ondataavailable = ({ data }) => {
                if (data.size && this.socket?.readyState === WebSocket.OPEN) this.socket.send(data);
            };
            this.socket.send(JSON.stringify({
                type: "start",
                session_id: this.sessionId,
                mime_type: this.mediaRecorder.mimeType || this.supportedMimeType || "audio/webm",
            }));
            this.mediaRecorder.start(250);
            this.setState(STTState.LISTENING);
            return true;
        } catch (error) {
            this._cleanup();
            const message = error.name === "NotAllowedError"
                ? "Microphone permission is required to talk to the avatar."
                : error.name === "NotFoundError"
                    ? "No microphone found on your device."
                    : error.message || "Could not start microphone capture.";
            this.setState(STTState.ERROR, message);
            this.onError(message);
            return false;
        }
    }

    async stopRecording() {
        if (!this.mediaRecorder) return false;
        return new Promise((resolve) => {
            const recorder = this.mediaRecorder;
            recorder.onstop = () => {
                this.mediaRecorder = null;
                this._stopTracks();
                if (this.socket?.readyState === WebSocket.OPEN) {
                    this.socket.send(JSON.stringify({ type: "end" }));
                    this.setState(STTState.TRANSCRIBING);
                    resolve(true);
                } else {
                    resolve(false);
                }
            };
            recorder.stop();
        });
    }

    cancelRecording() {
        if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify({ type: "cancel" }));
        this.mediaRecorder?.stop();
        this.mediaRecorder = null;
        this.socket?.close();
        this.socket = null;
        this._cleanup();
        this.setState(STTState.IDLE);
    }

    _stopTracks() {
        this.stream?.getTracks().forEach((track) => track.stop());
        this.stream = null;
    }

    _cleanup() {
        this._stopTracks();
        this.mediaRecorder = null;
        this.socket?.close();
        this.socket = null;
    }
}
