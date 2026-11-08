# Frontend tests

These Node.js tests cover recording, cancellation, conversation rendering,
camera events, avatar gestures, speech pacing, and asset references.

From the repository root:

```powershell
node --test frontend/tests/*.test.js
```

The package uses Node's built-in test runner and has no `npm test` script.
Tests use mocks for browser APIs; they do not replace a real microphone,
camera, and audio playback check. Run `npm run build` from `frontend/` to check
the production bundle as well.

See [development and verification](../../docs/development.md).
