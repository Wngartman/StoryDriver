# GitHub Release Process

## Privacy Gate

The repository must remain private unless the owner explicitly changes that decision. Before every push or release, verify the publish tree excludes:

- `app.db`, databases, and backups;
- stories, prompts, continuity records, and private reports;
- generated narration and image artifacts;
- custom/reference voices and cached voice prompts;
- LLM/TTS model weights and local runtimes not intended as source;
- `.env`, local endpoint keys, caches, logs, and PID files;
- user backgrounds and screenshots containing prose;
- machine-specific configuration and absolute private paths.

Run the source audit and inspect `git status` before staging. Do not rewrite the only local history to remove private content; use the verified checkpoint and a sanitized publish branch/export if history is contaminated.

## Versioning

The semantic version is stored in `VERSION`. Release-candidate tags use `vMAJOR.MINOR.PATCH-rc.N`, currently `v1.0.0-rc.1`.

## Local Release Gate

1. Verify database backup/integrity and clean disposable test state.
2. Compile backend and packaged entry points.
3. Build the frontend.
4. Build the WPF desktop shell.
5. Run provider, desktop, settings, continuity, narration, mobile, privacy, and package contracts.
6. Build installer and portable ZIP.
7. Install to a disposable directory and verify startup, hidden sidecar, upgrade, preserve-data uninstall, and explicit remove-data uninstall.
8. Verify SHA-256 sums and package contents.
9. Commit source/docs/tests, create the version tag, and push only the sanitized tree.
10. Create the GitHub Release and upload installer, portable ZIP, checksums, and release notes.

## CI

`.github/workflows/windows-release.yml` runs on Windows. It installs project dependencies, validates the pinned llama.cpp download hash, builds frontend/backend/desktop artifacts, runs non-model contracts, and uploads installer/portable artifacts. A `v*` tag additionally creates release assets.

CI never needs private databases, model weights, local voices, or local provider credentials. Large local models are selected by users after installation.

## Release Assets

- `StoryDriver-Setup-x64.exe`
- `StoryDriver-Portable-x64.zip`
- `SHA256SUMS.txt`
- `RELEASE_NOTES.md`

The artifacts are unsigned until a certificate-backed signing stage is explicitly added.
