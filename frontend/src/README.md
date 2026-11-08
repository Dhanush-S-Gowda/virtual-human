# Browser application source

`main.js` connects the UI, microphone, camera, avatar, and backend events.
`call.css` styles the call screen; `style.css` contains other application styles.

| Folder | Purpose |
|---|---|
| [avatar](avatar/README.md) | Speech playback and avatar action scheduling |
| [cv](cv/README.md) | Camera events, overlays, and speaker evidence |
| [stt](stt/README.md) | Microphone capture and conversation WebSocket |
| [ui](ui/README.md) | Settings, conversation log, and layout helpers |
| [assets](assets/README.md) | Assets imported and bundled by Vite |
| [talkinghead](talkinghead/README.md) | Browser avatar engine and audio worklet |

See the [frontend guide](../README.md) for startup and test commands.
