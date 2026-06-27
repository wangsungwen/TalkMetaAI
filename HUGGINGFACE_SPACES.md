# Hugging Face Spaces deployment

This repository can run as a Docker Space.

## Recommended Space settings

- SDK: Docker
- App port: 7860
- Hardware: CPU Basic can start the full stack with more memory than Render Free, but GPU hardware is recommended for useful response speed.

## Runtime mode

The Docker image uses `TALKMETA_LIGHTWEIGHT=auto`.

- On Render, it defaults to lightweight mode so the Free instance can boot.
- On Hugging Face Spaces, it detects Space environment variables and starts full AI mode by default.

To force a mode, add a Space variable:

- `TALKMETA_LIGHTWEIGHT=0` for full Whisper/Qwen/Kokoro mode.
- `TALKMETA_LIGHTWEIGHT=1` for lightweight web-only mode.

## Deploy steps

1. Create a new Hugging Face Space.
2. Choose Docker as the SDK.
3. Import or push this repository to the Space.
4. Use at least CPU Basic for memory; use a GPU Space for practical inference latency.
5. Wait for the build to finish, then open the Space URL.
