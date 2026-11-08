# Frontend

This folder contains the browser app: push-to-talk recording, conversation UI,
camera capture, avatar rendering, and speech playback.

## Folder map

| Folder | Purpose |
|---|---|
| [src](src/README.md) | Application modules and styles |
| [public](public/README.md) | Files served directly, including avatar models and animations |
| [tests](tests/README.md) | Node.js regression tests |

`index.html` starts the app. `tokens.css` holds shared style tokens.
`vite.config.js` defines development routes. `package.json` and
`package-lock.json` define Node dependencies. `node_modules/` and `dist/` are
generated folders. `.hallmark/` contains existing tooling metadata.

## Run and test

Use Node.js 22.12 or newer. From this folder:

```powershell
npm ci
npm run dev
```

Open http://localhost:5173. The host API and CV services must also be running.
Vite proxies `/api` to port 8000 and `/cv` to port 8765.

From the repository root:

```powershell
node --test frontend/tests/*.test.js
```

Run `npm run build` from this folder to build the production bundle. Docker
uses [frontend.Dockerfile](../devops/frontend.Dockerfile) and
[Nginx](../devops/nginx.conf) to serve it on port 4173. The Compose service name
is still `talking-avatar`.

Shared catalog and avatar settings are imported from `TalkingHead/`.
See [development](../docs/development.md) and [speech timing](../docs/audio-gesture-synchronization.md).
