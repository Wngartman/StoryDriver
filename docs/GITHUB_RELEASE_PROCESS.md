# GitHub Release Process

The owner authorized a public 1.0 release. Publish only a sanitized source tree.
Development history may contain private legacy material; never push it into public main.

Before publication exclude databases, stories, user prompts/settings, reference recordings, generated audio,
models, caches, logs, environment files, private reports, credentials and user backgrounds.
Run scripts/tests/publish_tree_privacy_test.py against the staged public tree.
Inspect history as well as current files. Preserve the local checkpoint instead of destructively rewriting it.

VERSION is the semantic version. Stable release tag: v1.0.0.
Run scripts/tests/run_release_checks.py, live writing/narration tests, desktop/mobile inspection,
installer/portable startup and preserve-data uninstall checks, then scripts/build_native_release.py.

Release assets: StoryDriver-Setup-x64.exe, StoryDriver-Portable-x64.zip,
StoryDriver-ThirdParty-Sources.zip, SHA256SUMS.txt and RELEASE_NOTES.md.
Verify hashes before upload. Binaries are unsigned.

The Windows workflow builds from a clean checkout, fails on test errors, collects artifacts and creates a tagged release
only if it does not already exist. It does not overwrite manually verified release binaries.
Kokoro public assets are fetched at build time with pinned checksums; no private writing model is needed in CI.
