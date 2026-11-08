/**
 * AvatarController.js
 *
 * Provides a clean state and action abstraction over TalkingHead,
 * decoupling higher-level application states (listening, idle, speaking, thinking)
 * from direct TalkingHead internals.
 */

export const AvatarState = {
    IDLE: "idle",
    LISTENING: "listening",
    THINKING: "thinking",
    SPEAKING: "speaking",
};

export class AvatarController {
    /**
     * @param {Object} [headInstance] - TalkingHead instance
     */
    constructor(headInstance = null) {
        this.head = headInstance;
        this.currentState = AvatarState.IDLE;
    }

    /**
     * Set the active TalkingHead instance.
     *
     * @param {Object} headInstance
     */
    setHead(headInstance) {
        this.head = headInstance;
    }

    /**
     * Change avatar visual state.
     *
     * @param {string} state
     */
    setState(state) {
        this.currentState = state;

        if (!this.head) {
            return;
        }

        try {
            switch (state) {
                case AvatarState.LISTENING:
                    // Attentive posture: look directly into the camera
                    this.head.lookAtCamera(300);
                    this.head.setMood("neutral");
                    break;

                case AvatarState.THINKING:
                    // Future LLM placeholder: slight head tilt or contemplative look
                    this.head.lookAtCamera(500);
                    break;

                case AvatarState.SPEAKING:
                    // Speaking state handled by audio synthesis/visemes
                    this.head.lookAtCamera(500);
                    break;

                case AvatarState.IDLE:
                default:
                    this.head.setMood("neutral");
                    this.head.lookAtCamera(800);
                    break;
            }
        } catch (err) {
            console.warn("AvatarController state transition warning:", err);
        }
    }

    /**
     * Get current avatar state.
     *
     * @returns {string}
     */
    getState() {
        return this.currentState;
    }
}

export const avatarController = new AvatarController();

