from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings


class LMStudioResourceError(RuntimeError):
    pass


class LMStudioResourceUnavailableError(LMStudioResourceError):
    pass


class LMStudioResourceAuthError(LMStudioResourceError):
    pass


@dataclass(frozen=True)
class LMStudioLoadedInstance:
    id: str
    model_key: str | None = None
    display_name: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "id": self.id,
            "model_key": self.model_key,
            "display_name": self.display_name,
        }


def _first_string(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _normalize(value: str | None) -> str:
    return (value or "").strip().lower().replace("\\", "/")


class LMStudioResourceClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_token: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.lm_studio_rest_base_url).rstrip("/")
        self.api_token = api_token if api_token is not None else settings.lm_studio_api_token

    def _headers(self) -> dict[str, str]:
        token = (self.api_token or "").strip()
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    async def _request(self, method: str, path: str, *, timeout: float = 8.0, **kwargs: Any) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=self._headers()) as client:
                response = await client.request(method, f"{self.base_url}{path}", **kwargs)
                response.raise_for_status()
                return response
        except httpx.ConnectError as exc:
            raise LMStudioResourceUnavailableError(
                f"LM Studio REST API is not reachable at {self.base_url}."
            ) from exc
        except httpx.TimeoutException as exc:
            raise LMStudioResourceUnavailableError(
                f"LM Studio REST API timed out at {self.base_url}."
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            detail = exc.response.text[:500] if exc.response is not None else ""
            if status_code in {401, 403}:
                raise LMStudioResourceAuthError(
                    f"LM Studio REST API rejected the request with HTTP {status_code}. Configure LM_STUDIO_API_TOKEN if LM Studio requires one."
                ) from exc
            raise LMStudioResourceError(
                f"LM Studio REST API returned HTTP {status_code}. {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LMStudioResourceUnavailableError(f"LM Studio REST API request failed: {exc}") from exc

    @staticmethod
    def _models_from_payload(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            raise LMStudioResourceError("LM Studio REST API returned an unexpected /models response.")

        for key in ("data", "models", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

        if any(key in payload for key in ("id", "key", "model_key", "loaded_instances", "loadedInstances")):
            return [payload]

        raise LMStudioResourceError("LM Studio REST API response did not include a models list.")

    @staticmethod
    def _loaded_instances_from_model(model: dict[str, Any]) -> list[LMStudioLoadedInstance]:
        model_key = _first_string(
            model.get("model_key"),
            model.get("modelKey"),
            model.get("key"),
            model.get("id"),
            model.get("path"),
        )
        display_name = _first_string(
            model.get("display_name"),
            model.get("displayName"),
            model.get("name"),
            model.get("label"),
            model_key,
        )
        raw_instances = (
            model.get("loaded_instances")
            or model.get("loadedInstances")
            or model.get("instances")
            or []
        )
        if isinstance(raw_instances, dict):
            raw_instances = [raw_instances]
        if not isinstance(raw_instances, list):
            return []

        instances: list[LMStudioLoadedInstance] = []
        for instance in raw_instances:
            if not isinstance(instance, dict):
                continue
            instance_id = _first_string(
                instance.get("id"),
                instance.get("instance_id"),
                instance.get("instanceId"),
                instance.get("identifier"),
            )
            if not instance_id:
                continue
            instances.append(
                LMStudioLoadedInstance(
                    id=instance_id,
                    model_key=_first_string(
                        instance.get("model_key"),
                        instance.get("modelKey"),
                        instance.get("key"),
                        instance.get("model"),
                        model_key,
                    ),
                    display_name=_first_string(
                        instance.get("display_name"),
                        instance.get("displayName"),
                        instance.get("name"),
                        display_name,
                    ),
                )
            )
        return instances

    @staticmethod
    def _top_level_loaded_instances(payload: Any) -> list[LMStudioLoadedInstance]:
        if not isinstance(payload, dict):
            return []
        raw_instances = payload.get("loaded_instances") or payload.get("loadedInstances") or []
        if isinstance(raw_instances, dict):
            raw_instances = [raw_instances]
        if not isinstance(raw_instances, list):
            return []

        instances: list[LMStudioLoadedInstance] = []
        for instance in raw_instances:
            if not isinstance(instance, dict):
                continue
            instance_id = _first_string(
                instance.get("id"),
                instance.get("instance_id"),
                instance.get("instanceId"),
                instance.get("identifier"),
            )
            if instance_id:
                instances.append(
                    LMStudioLoadedInstance(
                        id=instance_id,
                        model_key=_first_string(
                            instance.get("model_key"),
                            instance.get("modelKey"),
                            instance.get("key"),
                            instance.get("model"),
                        ),
                        display_name=_first_string(
                            instance.get("display_name"),
                            instance.get("displayName"),
                            instance.get("name"),
                        ),
                    )
                )
        return instances

    async def get_lmstudio_models(self) -> list[dict[str, Any]]:
        response = await self._request("GET", "/models")
        try:
            payload = response.json()
        except ValueError as exc:
            raise LMStudioResourceError("LM Studio REST API returned non-JSON model data.") from exc
        return self._models_from_payload(payload)

    async def get_loaded_lmstudio_instances(self) -> list[dict[str, str | None]]:
        response = await self._request("GET", "/models")
        try:
            payload = response.json()
        except ValueError as exc:
            raise LMStudioResourceError("LM Studio REST API returned non-JSON model data.") from exc

        instances: list[LMStudioLoadedInstance] = []
        seen_ids: set[str] = set()
        for instance in self._top_level_loaded_instances(payload):
            if instance.id not in seen_ids:
                instances.append(instance)
                seen_ids.add(instance.id)
        for model in self._models_from_payload(payload):
            for instance in self._loaded_instances_from_model(model):
                if instance.id not in seen_ids:
                    instances.append(instance)
                    seen_ids.add(instance.id)
        return [instance.as_dict() for instance in instances]

    async def find_loaded_instance_for_selected_model(self, selected_model: str | None) -> dict[str, str | None] | None:
        instances = await self.get_loaded_lmstudio_instances()
        selected = _normalize(selected_model)
        if not selected:
            return instances[0] if len(instances) == 1 else None

        for instance in instances:
            values = [
                instance.get("id"),
                instance.get("model_key"),
                instance.get("display_name"),
            ]
            normalized_values = {_normalize(value) for value in values if value}
            basenames = {value.rsplit("/", 1)[-1] for value in normalized_values if value}
            if selected in normalized_values or selected in basenames:
                return instance
        return None

    async def unload_lmstudio_instance(self, instance_id: str) -> dict[str, Any]:
        if not instance_id.strip():
            raise LMStudioResourceError("LM Studio unload requires an instance_id.")
        response = await self._request("POST", "/models/unload", timeout=30.0, json={"instance_id": instance_id})
        if not response.content:
            return {"ok": True}
        try:
            payload = response.json()
        except ValueError:
            return {"ok": True, "raw": response.text[:500]}
        return payload if isinstance(payload, dict) else {"ok": True, "data": payload}

    async def load_lmstudio_model(self, model_key: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
        if not model_key.strip():
            raise LMStudioResourceError("LM Studio load requires a model key.")
        body: dict[str, Any] = {"model": model_key}
        if config:
            body.update({key: value for key, value in config.items() if key not in {"model", "prompt_template"}})
            body["echo_load_config"] = True
        response = await self._request("POST", "/models/load", timeout=120.0, json=body)
        if not response.content:
            return {"ok": True}
        try:
            payload = response.json()
        except ValueError:
            return {"ok": True, "raw": response.text[:500]}
        return payload if isinstance(payload, dict) else {"ok": True, "data": payload}

    async def can_manage_lmstudio_models(self) -> tuple[bool, str | None]:
        try:
            await self.get_lmstudio_models()
        except LMStudioResourceError as error:
            return False, str(error)
        return True, None


async def get_lmstudio_models() -> list[dict[str, Any]]:
    return await LMStudioResourceClient().get_lmstudio_models()


async def get_loaded_lmstudio_instances() -> list[dict[str, str | None]]:
    return await LMStudioResourceClient().get_loaded_lmstudio_instances()


async def find_loaded_instance_for_selected_model(selected_model: str | None) -> dict[str, str | None] | None:
    return await LMStudioResourceClient().find_loaded_instance_for_selected_model(selected_model)


async def unload_lmstudio_instance(instance_id: str) -> dict[str, Any]:
    return await LMStudioResourceClient().unload_lmstudio_instance(instance_id)


async def load_lmstudio_model(model_key: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    return await LMStudioResourceClient().load_lmstudio_model(model_key, config=config)


async def can_manage_lmstudio_models() -> tuple[bool, str | None]:
    return await LMStudioResourceClient().can_manage_lmstudio_models()
