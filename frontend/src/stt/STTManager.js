/** Capture microphone audio and send chunks to the conversation websocket. */

import { tabSessionId } from "./session.js";

export const STTState = {
    IDLE: "IDLE",
    LISTENING: "LISTENING",
    TRANSCRIBING: "TRANSCRIBING",
    READY: "READY",
    ERROR: "ERROR",
};

export class STTManager {
    constructor(options = {}) {
        const endpoint = new URL(options.endpoint || import.meta.env?.VITE_BACKEND_URL || "/api", globalThis.location?.origin || "http://localhost:5173").href.replace(/\/+$/, "");
        this.websocketUrl = endpoint.replace(/^http/, "ws") + "/ws";
        this.onStateChange = options.onStateChange || (() => {});
        this.onTranscript = options.onTranscript || (() => {});
        this.onError = options.onError || (() => {});
        this.onEvent = options.onEvent || (() => {});
        this.onAudioActivity = options.onAudioActivity || (() => {});
        this.onCaptureEnd = options.onCaptureEnd || (() => {});
        this.onCaptureStart = options.onCaptureStart || (() => {});
        this.getContext = options.getContext || (() => ({}));
        this.getStartOptions = options.getStartOptions || (() => ({}));
        this.state = STTState.IDLE;
        this.audioContext = null;
        this.captureNode = null;
        this.sourceNode = null;
        this.stream = null;
        this.socket = null;
        this.sessionId = options.sessionId || tabSessionId();
        this.starting = false;
        this.captureGeneration = 0;
    }

    setState(state, detail = null) {
        this.state = state;
        this.onStateChange(state, detail);
    }

    isRecording() {
        return this.state === STTState.LISTENING && this.captureNode !== null;
    }

    async startRecording() {
        if (this.isRecording() || this.starting) return false;
        this.starting = true;
        const generation = ++this.captureGeneration;
        try {
            const startOptions = await this.getStartOptions();
            if (generation !== this.captureGeneration) return false;
            // A previous release may still be waiting for its worklet flush.
            // Release those resources before assigning the new capture stream.
            if (this.captureNode || this.stream || this.audioContext) this._stopCapture();
            if (this.socket && this.socket.readyState !== WebSocket.CLOSED) {
                this.socket.onclose = this.socket.onmessage = null;
                if (this.socket.readyState === WebSocket.OPEN) {
                    this.socket.send(JSON.stringify({ type: "cancel" }));
                }
                this.socket.close();
            }
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
            });
            if (generation !== this.captureGeneration) {
                stream.getTracks().forEach((track) => track.stop());
                return false;
            }
            this.stream = stream;
            this.socket = new WebSocket(this.websocketUrl);
            const socket = this.socket;
            await new Promise((resolve, reject) => {
                const timer = setTimeout(() => {
                    socket.close();
                    reject(new Error("Cannot reach the conversation backend."));
                }, 10000);
                socket.onopen = () => { clearTimeout(timer); resolve(); };
                socket.onerror = socket.onclose = () => {
                    clearTimeout(timer);
                    reject(new Error("Cannot reach the conversation backend."));
                };
            });
            if (generation !== this.captureGeneration) return false;
            socket.onmessage = ({ data }) => {
                if (this.socket !== socket) return;
                try {
                    const event = JSON.parse(data);
                    this.onEvent(event);
                    if (event.type === "transcript") this.onTranscript(event);
                    if (event.type === "error") {
                        this._stopCapture();
                        this.setState(STTState.ERROR, event.message);
                        this.onError(event.message || "Conversation failed.");
                    }
                    if (event.type === "done") this.setState(STTState.READY);
                } catch (error) {
                    console.error("Invalid conversation event:", error);
                }
            };
            socket.onclose = () => {
                if (this.socket !== socket) return;
                if (this.isRecording()) {
                    this._stopCapture();
                    this.setState(STTState.ERROR, "Conversation connection closed while recording.");
                }
                if (this.state === STTState.TRANSCRIBING) this.setState(STTState.READY);
            };
            this.audioContext = new AudioContext({ sampleRate: 16000 });
            await this.audioContext.audioWorklet.addModule(new URL("./pcm-worklet.js", import.meta.url));
            if (generation !== this.captureGeneration) return false;
            await this.audioContext.resume();
            if (generation !== this.captureGeneration) return false;
            this.sourceNode = this.audioContext.createMediaStreamSource(this.stream);
            this.captureNode = new AudioWorkletNode(this.audioContext, "pcm-capture");
            this.captureNode.port.onmessage = ({ data }) => {
                if (data.buffer && this.socket?.readyState === WebSocket.OPEN) {
                    if (this.socket.bufferedAmount > 1024 * 1024) {
                        this.cancelRecording();
                        this.setState(STTState.ERROR, "Audio connection is too slow. Please try again.");
                        return;
                    }
                    this.socket.send(data.buffer);
                    this.onAudioActivity({ rms: data.rms,
                        timestamp: performance.now() - data.durationMs / 2 });
                }
                if (data.stopped) this.captureStopped?.();
            };
            this.socket.send(JSON.stringify({
                ...startOptions,
                type: "start",
                session_id: this.sessionId,
                mime_type: "audio/pcm",
                sample_rate: this.audioContext.sampleRate,
            }));
            this.sourceNode.connect(this.captureNode);
            // The worklet outputs silence; connecting it keeps audio processing active.
            this.captureNode.connect(this.audioContext.destination);
            this.setState(STTState.LISTENING);
            this.onCaptureStart();
            return true;
        } catch (error) {
            if (generation !== this.captureGeneration) return false;
            this._cleanup();
            const message = error.name === "NotAllowedError"
                ? "Microphone permission is required to talk to the avatar."
                : error.name === "NotFoundError"
                    ? "No microphone found on your device."
                    : error.message || "Could not start microphone capture.";
            this.setState(STTState.ERROR, message);
            this.onError(message);
            return false;
        } finally {
            if (generation === this.captureGeneration) this.starting = false;
        }
    }

    async stopRecording() {
        if (this.starting) {
            this.cancelRecording();
            return false;
        }
        if (!this.isRecording()) return false;
        const generation = this.captureGeneration;
        const socket = this.socket;
        this.setState(STTState.TRANSCRIBING);
        try {
            await new Promise((resolve, reject) => {
                this.captureStopped = resolve;
                this.captureNode.port.postMessage("stop");
                this.flushTimer = setTimeout(() => reject(new Error("Audio capture did not stop.")), 2000);
            });
            if (generation !== this.captureGeneration || this.socket !== socket) return false;
            if (socket?.readyState !== WebSocket.OPEN) return false;
            socket.send(JSON.stringify({ type: "end", ...this.getContext() }));
            return true;
        } catch (error) {
            if (generation !== this.captureGeneration) return false;
            this.cancelRecording();
            this.setState(STTState.ERROR, error.message);
            this.onError(error.message);
            return false;
        } finally {
            if (generation === this.captureGeneration) this._stopCapture();
        }
    }

    cancelRecording() {
        this.captureGeneration++;
        this.starting = false;
        if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify({ type: "cancel" }));
        this.socket?.close();
        this.socket = null;
        this._cleanup();
        this.setState(STTState.IDLE);
    }

    _stopTracks() {
        this.stream?.getTracks().forEach((track) => track.stop());
        this.stream = null;
    }

    _stopCapture() {
        clearTimeout(this.flushTimer);
        this.captureStopped?.();
        this.captureStopped = null;
        if (this.captureNode) this.captureNode.port.onmessage = null;
        this.captureNode?.disconnect();
        this.sourceNode?.disconnect();
        this.captureNode = this.sourceNode = null;
        this.audioContext?.close().catch(() => {});
        this.audioContext = null;
        this._stopTracks();
        this.onCaptureEnd();
    }

    _cleanup() {
        this._stopCapture();
        this.socket?.close();
        this.socket = null;
    }
}
