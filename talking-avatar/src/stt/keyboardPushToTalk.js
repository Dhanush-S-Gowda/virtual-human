/**
 * keyboardPushToTalk.js
 *
 * Manages push-to-talk key events (Spacebar), prevents page scrolling,
 * ignores auto-repeat, ensures text inputs retain normal Space behavior,
 * and cancels recording safely on window blur or visibility changes.
 */

export class KeyboardPushToTalk {
    /**
     * @param {Object} options
     * @param {string} [options.key="Space"] - The KeyboardEvent.code to listen for
     * @param {function(): Promise<void>|void} options.onStart - Called when push-to-talk begins
     * @param {function(): Promise<void>|void} options.onStop - Called when push-to-talk ends
     * @param {function(): void} [options.onCancel] - Called if focus is lost while recording
     */
    constructor(options = {}) {
        this.targetCode = options.key || "Space";
        this.onStart = options.onStart || (() => {});
        this.onStop = options.onStop || (() => {});
        this.onCancel = options.onCancel || (() => {});

        this.isKeyDown = false;
        this.isEnabled = false;

        this._boundKeyDown = this._handleKeyDown.bind(this);
        this._boundKeyUp = this._handleKeyUp.bind(this);
        this._boundBlur = this._handleBlur.bind(this);
        this._boundVisibilityChange = this._handleVisibilityChange.bind(this);
    }

    /**
     * Determine if an element is an editable text input or interactive control
     * where normal Spacebar typing must be preserved.
     *
     * @param {EventTarget|null} target
     * @returns {boolean}
     */
    isEditableTarget(target) {
        if (!target || !(target instanceof HTMLElement)) {
            return false;
        }

        const tagName = target.tagName.toUpperCase();
        if (tagName === "INPUT" || tagName === "TEXTAREA" || tagName === "SELECT") {
            return true;
        }

        if (target.isContentEditable || target.getAttribute("contenteditable") === "true") {
            return true;
        }

        return false;
    }

    /**
     * Handle keydown event.
     *
     * @private
     * @param {KeyboardEvent} event
     */
    _handleKeyDown(event) {
        if (!this.isEnabled) {
            return;
        }

        // Match spacebar via code or key
        const isSpace = event.code === this.targetCode || event.key === " " || event.code === "Space";
        if (!isSpace) {
            return;
        }

        // If user is typing inside an input or textarea, do NOT intercept
        if (this.isEditableTarget(event.target)) {
            return;
        }

        // Always prevent page scrolling when Space is used for push-to-talk
        event.preventDefault();

        // Ignore repeated keydown events caused by holding down the key
        if (event.repeat || this.isKeyDown) {
            return;
        }

        this.isKeyDown = true;
        this.onStart();
    }

    /**
     * Handle keyup event.
     *
     * @private
     * @param {KeyboardEvent} event
     */
    _handleKeyUp(event) {
        if (!this.isEnabled) {
            return;
        }

        const isSpace = event.code === this.targetCode || event.key === " " || event.code === "Space";
        if (!isSpace) {
            return;
        }

        if (this.isEditableTarget(event.target)) {
            return;
        }

        event.preventDefault();

        if (this.isKeyDown) {
            this.isKeyDown = false;
            this.onStop();
        }
    }

    /**
     * Handle window blur event (safety fallback if user alt-tabs or clicks outside).
     *
     * @private
     */
    _handleBlur() {
        if (this.isKeyDown) {
            this.isKeyDown = false;
            this.onCancel();
        }
    }

    /**
     * Handle document visibility change (safety fallback if tab is backgrounded).
     *
     * @private
     */
    _handleVisibilityChange() {
        if (document.hidden && this.isKeyDown) {
            this.isKeyDown = false;
            this.onCancel();
        }
    }

    /**
     * Attach event listeners to window.
     */
    attach() {
        if (this.isEnabled) {
            return;
        }

        this.isEnabled = true;
        window.addEventListener("keydown", this._boundKeyDown);
        window.addEventListener("keyup", this._boundKeyUp);
        window.addEventListener("blur", this._boundBlur);
        document.addEventListener("visibilitychange", this._boundVisibilityChange);
    }

    /**
     * Detach event listeners from window.
     */
    detach() {
        if (!this.isEnabled) {
            return;
        }

        this.isEnabled = false;
        this.isKeyDown = false;
        window.removeEventListener("keydown", this._boundKeyDown);
        window.removeEventListener("keyup", this._boundKeyUp);
        window.removeEventListener("blur", this._boundBlur);
        document.removeEventListener("visibilitychange", this._boundVisibilityChange);
    }
}
