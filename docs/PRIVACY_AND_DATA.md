# Privacy And Data

StoryDriver runs local inference and narration. No telemetry, account, analytics, remote fonts or automatic writing-model download is part of normal startup.
Configured service endpoints must be loopback or private LAN. The developer environment override STORYDRIVER_ALLOW_REMOTE_ENDPOINTS is not a normal app option.

## Data

Stories, versions, prompts, settings, continuity, narration cache, backgrounds and voice references live in the chosen data root.
On the development workstation this is D:\StoryDriverData. Portable mode uses its own data directory.
User data, model weights, environment files, logs, caches and recordings are excluded from the public source tree.

Kokoro public model assets and stock voices are bundled in the binary release. They need no personal reference audio.
Optional legacy cloning adapters require explicitly authorized adult reference audio and separate local setup.
The default release does not ship, load or copy any private person's voice.
Browser narration lists and explicitly selects only voices reported as local by the operating system/browser. If none is available, it stops with an error; it never silently selects a cloud/default voice.

## Network Boundary

Native WebView navigation and bridge messages are restricted to the app origin.
The backend blocks cross-origin public-site mutations and refuses public inference endpoints by default.
The Microsoft WebView2 help button opens Microsoft's site only when explicitly clicked.
Build-time dependency/model downloads are separate from offline app runtime.

LAN is opt-in, has no authentication and uses HTTP. Everyone able to reach the trusted LAN service can access its API.
Use only a trusted private network and Private Windows Firewall permissions. Never port-forward the app.
Physical phone background playback and public-network isolation under every network configuration are not guaranteed.

## Logs And Maintenance

Normal diagnostics retain counts, timings, identifiers and errors. Treat local logs as private.
Raw prompt/prose debug capture requires STORYDRIVER_DEBUG_LOGS=true; disable it after a scoped investigation.
The writing pipeline retains bounded temporary planning artifacts, not an unbounded history of raw model reasoning.

Database backup uses SQLite's online backup API. Restore validates identity and integrity and preserves a pre-restore copy.
Uninstall retains data. Cleanup defaults to a dry run and only targets the selected data root's temp files.
Deleting a story removes its scene/version records and generated media without deleting unrelated reusable characters.

See MAINTENANCE.md for exact commands and GITHUB_RELEASE_PROCESS.md for the public-tree gate.
