from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

import httpx

from app.config import KOKORO_BASE_URL, KOKORO_SPEECH_ENDPOINT, KOKORO_WORKING_DIR


logger = logging.getLogger("storydriver.tts.kokoro")


class KokoroUnavailableError(RuntimeError):
    pass


class KokoroEndpointError(RuntimeError):
    pass


class KokoroPayloadError(RuntimeError):
    pass


@dataclass
class KokoroAudio:
    content: bytes
    content_type: str


class KokoroClient:
    def __init__(
        self,
        base_url: str = KOKORO_BASE_URL,
        speech_endpoint: str = KOKORO_SPEECH_ENDPOINT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.speech_endpoint_config = speech_endpoint or "/v1/audio/speech"

    @property
    def speech_endpoint(self) -> str:
        endpoint = self.speech_endpoint_config.strip()
        if endpoint.lower().startswith(("http://", "https://")):
            return endpoint
        return f"{self.base_url}/{endpoint.lstrip('/')}"

    async def is_reachable(self) -> tuple[bool, str | None]:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{self.base_url}/health")
                if response.status_code < 500:
                    return True, None
        except httpx.HTTPError as error:
            first_error = f"Kokoro-FastAPI is not running at {self.base_url}."
        else:
            first_error = None

        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{self.base_url}/docs")
                if response.status_code < 500:
                    return True, None
                return False, f"HTTP {response.status_code}"
        except httpx.HTTPError as error:
            return False, first_error or f"Kokoro-FastAPI is not running at {self.base_url}."

    async def supported_speech_options(self) -> list[str]:
        known_options = {"temperature", "top_p", "exaggeration", "style", "cfg"}
        openapi_url = f"{self.base_url}/openapi.json"
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                response = await client.get(openapi_url)
                response.raise_for_status()
                schema = response.json()
        except (httpx.HTTPError, ValueError):
            return []

        components = schema.get("components", {}).get("schemas", {})

        def resolve(node: dict[str, Any]) -> dict[str, Any]:
            ref = node.get("$ref")
            if not isinstance(ref, str):
                return node
            name = ref.rsplit("/", 1)[-1]
            target = components.get(name)
            return target if isinstance(target, dict) else node

        endpoint_path = "/" + self.speech_endpoint.split("/", 3)[-1] if "/v1/" in self.speech_endpoint else "/v1/audio/speech"
        path_schema = schema.get("paths", {}).get(endpoint_path) or schema.get("paths", {}).get("/v1/audio/speech") or {}
        post_schema = path_schema.get("post", {}) if isinstance(path_schema, dict) else {}
        content_schema = (
            post_schema.get("requestBody", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
        )
        body_schema = resolve(content_schema)
        properties = body_schema.get("properties", {}) if isinstance(body_schema, dict) else {}
        return sorted(option for option in known_options if option in properties)

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        speed: float = 1.0,
        extra_options: dict[str, Any] | None = None,
    ) -> KokoroAudio:
        selected_voice = voice or "af_heart"
        payload = {
            "model": "kokoro",
            "input": text,
            "voice": selected_voice,
            "response_format": "mp3",
            "speed": speed,
        }
        for key, value in (extra_options or {}).items():
            if value is not None and value != "":
                payload[key] = value

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                # TODO: If your Kokoro-FastAPI fork uses a non-OpenAI-compatible route,
                # adjust this isolated payload/endpoint without changing the browser fallback.
                logger.info(
                    "Calling Kokoro-FastAPI provider=kokoro url=%s voice=%s speed=%s",
                    self.speech_endpoint,
                    selected_voice,
                    speed,
                )
                response = await client.post(self.speech_endpoint, json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as error:
            logger.warning("Kokoro-FastAPI unreachable at %s: %s", self.base_url, error)
            raise KokoroUnavailableError(
                f"Kokoro-FastAPI is not running at {self.base_url}."
            ) from error
        except httpx.HTTPError as error:
            logger.warning("Kokoro-FastAPI request failed at %s: %s", self.speech_endpoint, error)
            raise KokoroUnavailableError(
                f"Kokoro-FastAPI is not reachable at {self.base_url}."
            ) from error

        if response.status_code == 404:
            logger.warning("Kokoro speech endpoint missing url=%s", self.speech_endpoint)
            raise KokoroEndpointError(
                "Kokoro is reachable, but /v1/audio/speech was not found. Endpoint may be wrong."
            )
        if response.status_code in {400, 401, 403, 409, 415, 422}:
            detail = response.text.strip() or f"HTTP {response.status_code}"
            logger.warning(
                "Kokoro rejected request url=%s status=%s detail=%s",
                self.speech_endpoint,
                response.status_code,
                detail,
            )
            raise KokoroPayloadError(
                f"Kokoro rejected the TTS request. Check voice/speed/payload. Detail: {detail}"
            )
        if response.status_code >= 500:
            logger.warning("Kokoro-FastAPI server error url=%s status=%s", self.base_url, response.status_code)
            raise KokoroUnavailableError(f"Kokoro-FastAPI returned HTTP {response.status_code} at {self.base_url}.")
        if response.status_code >= 400:
            detail = response.text.strip() or f"HTTP {response.status_code}"
            logger.warning("Kokoro synthesis failed url=%s status=%s detail=%s", self.base_url, response.status_code, detail)
            raise RuntimeError(f"Kokoro synthesis failed: {detail}")

        content_type = response.headers.get("content-type", "audio/mpeg").split(";")[0]
        return KokoroAudio(content=response.content, content_type=content_type)

    async def list_voices(self) -> list[str]:
        def voice_name(item) -> str:
            if isinstance(item, dict):
                return str(item.get("id") or item.get("name") or item)
            return str(item)

        voice_paths = ("/v1/audio/voices", "/voices")
        async with httpx.AsyncClient(timeout=4.0) as client:
            for path in voice_paths:
                try:
                    response = await client.get(f"{self.base_url}{path}")
                    if response.status_code >= 400:
                        continue
                    data = response.json()
                except (httpx.HTTPError, ValueError):
                    continue

                if isinstance(data, list):
                    return [str(item) for item in data]
                if isinstance(data, dict):
                    voices = data.get("voices") or data.get("data") or []
                    if isinstance(voices, list):
                        names = [voice_name(item) for item in voices]
                        if names:
                            return sorted(set(names))

        local_voices = self.local_voice_files()
        if local_voices:
            return local_voices
        return ["af_heart"]

    def local_voice_files(self) -> list[str]:
        roots: list[Path] = []
        if KOKORO_WORKING_DIR:
            root = Path(KOKORO_WORKING_DIR)
            roots.extend(
                [
                    root / "api" / "src" / "voices" / "v1_0",
                    root / "src" / "voices" / "v1_0",
                    root / "voices",
                ]
            )
        voices: set[str] = set()
        for root in roots:
            if not root.exists():
                continue
            for path in root.glob("*.pt"):
                voices.add(path.stem)
        voices.add("af_heart")
        return sorted(voices)
