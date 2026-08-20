from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import DATA_DIR


CUSTOM_PRESET_ROOT = DATA_DIR / "ui_presets"
CUSTOM_PRESETS_FILE = CUSTOM_PRESET_ROOT / "custom_presets.json"
CUSTOM_PRESET_ASSETS_DIR = CUSTOM_PRESET_ROOT / "assets"
EXPORT_DIR = DATA_DIR / "exports" / "ui_presets"

MAX_PRESET_JSON_BYTES = 256 * 1024
CURRENT_COMPATIBILITY_VERSION = "1"
BUILT_IN_PRESET_IDS = {
    "default",
    "emberfall",
    "midnight-atelier",
    "chronicle-hall",
    "writers-workshop",
}

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
CSS_RGB_TRIPLET_RE = re.compile(r"^\s*(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})\s*$")
SAFE_KEY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
SAFE_TEXT_RE = re.compile(r"^[a-zA-Z0-9\s.,:;!?()/_#%+'\"-]{0,260}$")
SAFE_CSS_TOKEN_RE = re.compile(r"^[a-zA-Z0-9\s.,:()/_#%+'\"-]{0,900}$")
SAFE_GRADIENT_RE = re.compile(
    r"^[a-zA-Z0-9\s.,:()#%+-]{0,1800}$"
)

ALLOWED_CSS_VARIABLES = {
    "--sd-color-ink",
    "--sd-color-panel",
    "--sd-color-panel-soft",
    "--sd-color-line",
    "--sd-color-story",
    "--sd-color-muted",
    "--sd-color-moss",
    "--sd-color-ember",
    "--sd-color-tide",
    "--sd-surface-sunken",
    "--sd-surface-hover",
    "--sd-surface-warning",
    "--sd-font-sans",
    "--sd-font-story",
    "--sd-app-texture",
    "--sd-app-texture-size",
    "--sd-app-texture-opacity",
    "--sd-shadow-glow",
    "--sd-shadow-focus",
    "--sd-shell-gradient",
    "--sd-shell-vignette",
    "--sd-sidebar-material",
    "--sd-sidebar-row-material",
    "--sd-sidebar-row-hover-material",
    "--sd-sidebar-row-active-material",
    "--sd-topbar-material",
    "--sd-right-rail-material",
    "--sd-panel-material",
    "--sd-panel-subtle-material",
    "--sd-card-material",
    "--sd-card-selected-material",
    "--sd-prose-panel-material",
    "--sd-director-note-material",
    "--sd-composer-material",
    "--sd-drawer-material",
    "--sd-mini-player-material",
    "--sd-button-material",
    "--sd-button-hover-material",
    "--sd-button-primary-material",
    "--sd-button-primary-text",
    "--sd-chip-material",
    "--sd-chip-selected-material",
    "--sd-field-material",
    "--sd-divider",
    "--sd-border-strong",
    "--sd-radius-card",
    "--sd-radius-panel",
    "--sd-radius-control",
    "--sd-reading-column-max",
    "--sd-utility-tray-material",
    "--sd-utility-pill-material",
    "--sd-prose-frame-material",
    "--sd-ornament-line",
    "--sd-ornament-soft",
    "--sd-ornament-glow",
    "--sd-prose-size",
    "--sd-prose-line-height",
    "--sd-prose-measure",
    "--sd-prose-shadow",
    "--sd-right-rail-width",
    "--sd-follow-highlight-bg",
    "--sd-follow-highlight-bg-phrase",
    "--sd-follow-highlight-bg-word",
    "--sd-follow-highlight-border",
    "--sd-follow-highlight-border-word",
}

RGB_VARIABLES = {
    "--sd-color-ink",
    "--sd-color-panel",
    "--sd-color-panel-soft",
    "--sd-color-line",
    "--sd-color-story",
    "--sd-color-muted",
    "--sd-color-moss",
    "--sd-color-ember",
    "--sd-color-tide",
    "--sd-surface-sunken",
    "--sd-surface-hover",
    "--sd-surface-warning",
    "--sd-button-primary-text",
}

FONT_VARIABLES = {"--sd-font-sans", "--sd-font-story"}
GRADIENT_VARIABLES = {
    "--sd-app-texture",
    "--sd-shell-gradient",
    "--sd-shell-vignette",
    "--sd-sidebar-material",
    "--sd-sidebar-row-material",
    "--sd-sidebar-row-hover-material",
    "--sd-sidebar-row-active-material",
    "--sd-topbar-material",
    "--sd-right-rail-material",
    "--sd-panel-material",
    "--sd-panel-subtle-material",
    "--sd-card-material",
    "--sd-card-selected-material",
    "--sd-prose-panel-material",
    "--sd-director-note-material",
    "--sd-composer-material",
    "--sd-drawer-material",
    "--sd-mini-player-material",
    "--sd-button-material",
    "--sd-button-hover-material",
    "--sd-button-primary-material",
    "--sd-chip-material",
    "--sd-chip-selected-material",
    "--sd-field-material",
    "--sd-utility-tray-material",
    "--sd-utility-pill-material",
    "--sd-prose-frame-material",
}
NUMBER_VARIABLES = {"--sd-app-texture-opacity"}

DEFAULT_CSS_VARIABLES = {
    "--sd-color-ink": "9 10 12",
    "--sd-color-panel": "17 19 24",
    "--sd-color-panel-soft": "23 26 33",
    "--sd-color-line": "39 42 50",
    "--sd-color-story": "237 234 226",
    "--sd-color-muted": "156 163 175",
    "--sd-color-moss": "144 201 154",
    "--sd-color-ember": "241 182 109",
    "--sd-color-tide": "143 199 216",
    "--sd-surface-sunken": "13 14 17",
    "--sd-surface-hover": "29 32 40",
    "--sd-surface-warning": "23 18 12",
    "--sd-font-sans": "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
    "--sd-font-story": "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
    "--sd-app-texture":
        "radial-gradient(circle at 74% 0%, rgba(143,199,216,0.075), transparent 30%), repeating-linear-gradient(118deg, rgba(255,255,255,0.018) 0 1px, transparent 1px 18px), radial-gradient(circle at 50% 120%, rgba(0,0,0,0.26), transparent 44%)",
    "--sd-app-texture-size": "auto, 280px 280px, auto",
    "--sd-app-texture-opacity": "0.72",
    "--sd-shadow-glow": "0 24px 80px rgba(0, 0, 0, 0.38)",
    "--sd-shadow-focus": "0 0 0 1px rgba(143, 199, 216, 0.48), 0 0 0 5px rgba(143, 199, 216, 0.1)",
    "--sd-shell-gradient": "radial-gradient(circle at 72% 0%, rgba(143,199,216,0.10), transparent 30%), linear-gradient(180deg, rgba(9,10,12,1), rgba(11,12,15,1))",
    "--sd-shell-vignette": "radial-gradient(circle at 50% 0%, rgba(255,255,255,0.045), transparent 34%), radial-gradient(circle at 50% 100%, rgba(0,0,0,0.42), transparent 40%)",
    "--sd-sidebar-material": "linear-gradient(180deg, rgba(17,19,24,0.96), rgba(13,14,17,0.98))",
    "--sd-sidebar-row-material": "linear-gradient(180deg, rgba(23,26,33,0.66), rgba(17,19,24,0.74))",
    "--sd-sidebar-row-hover-material": "linear-gradient(180deg, rgba(29,32,40,0.94), rgba(23,26,33,0.92))",
    "--sd-sidebar-row-active-material": "linear-gradient(135deg, rgba(143,199,216,0.16), rgba(23,26,33,0.92))",
    "--sd-topbar-material": "linear-gradient(180deg, rgba(13,14,17,0.88), rgba(13,14,17,0.68))",
    "--sd-right-rail-material": "linear-gradient(180deg, rgba(17,19,24,0.48), rgba(13,14,17,0.58))",
    "--sd-panel-material": "linear-gradient(180deg, rgba(23,26,33,0.94), rgba(17,19,24,0.98))",
    "--sd-panel-subtle-material": "linear-gradient(180deg, rgba(23,26,33,0.72), rgba(13,14,17,0.78))",
    "--sd-card-material": "linear-gradient(180deg, rgba(23,26,33,0.90), rgba(17,19,24,0.96))",
    "--sd-card-selected-material": "linear-gradient(180deg, rgba(31,40,48,0.96), rgba(17,19,24,0.98))",
    "--sd-prose-panel-material": "linear-gradient(180deg, rgba(13,14,17,0.30), rgba(13,14,17,0.16))",
    "--sd-director-note-material": "linear-gradient(180deg, rgba(13,14,17,0.62), rgba(13,14,17,0.46))",
    "--sd-composer-material": "linear-gradient(180deg, rgba(23,26,33,0.96), rgba(17,19,24,0.98))",
    "--sd-drawer-material": "linear-gradient(180deg, rgba(17,19,24,0.98), rgba(13,14,17,0.99))",
    "--sd-mini-player-material": "linear-gradient(180deg, rgba(23,26,33,0.96), rgba(17,19,24,0.98))",
    "--sd-button-material": "linear-gradient(180deg, rgba(23,26,33,0.94), rgba(13,14,17,0.92))",
    "--sd-button-hover-material": "linear-gradient(180deg, rgba(29,32,40,0.98), rgba(23,26,33,0.98))",
    "--sd-button-primary-material": "linear-gradient(180deg, rgba(237,234,226,1), rgba(201,209,218,1))",
    "--sd-button-primary-text": "9 10 12",
    "--sd-chip-material": "linear-gradient(180deg, rgba(13,14,17,0.70), rgba(13,14,17,0.52))",
    "--sd-chip-selected-material": "linear-gradient(180deg, rgba(143,199,216,0.18), rgba(143,199,216,0.09))",
    "--sd-field-material": "linear-gradient(180deg, rgba(13,14,17,0.90), rgba(13,14,17,0.74))",
    "--sd-divider": "rgba(39,42,50,0.78)",
    "--sd-border-strong": "rgba(143,199,216,0.36)",
    "--sd-radius-card": "18px",
    "--sd-radius-panel": "16px",
    "--sd-radius-control": "12px",
    "--sd-reading-column-max": "min(1120px, calc(100vw - 2rem))",
    "--sd-utility-tray-material": "linear-gradient(180deg, rgba(21,24,30,0.84), rgba(12,13,16,0.92))",
    "--sd-utility-pill-material": "linear-gradient(180deg, rgba(18,20,25,0.88), rgba(12,13,16,0.94))",
    "--sd-prose-frame-material": "linear-gradient(180deg, rgba(143,199,216,0.10), transparent 30%, rgba(39,42,50,0.14))",
    "--sd-ornament-line": "rgba(143,199,216,0.24)",
    "--sd-ornament-soft": "rgba(237,234,226,0.032)",
    "--sd-ornament-glow": "rgba(143,199,216,0.12)",
    "--sd-prose-size": "17px",
    "--sd-prose-line-height": "1.96",
    "--sd-prose-measure": "76ch",
    "--sd-prose-shadow": "none",
    "--sd-right-rail-width": "276px",
    "--sd-follow-highlight-bg": "rgba(125, 211, 252, 0.105)",
    "--sd-follow-highlight-bg-phrase": "rgba(125, 211, 252, 0.13)",
    "--sd-follow-highlight-bg-word": "rgba(125, 211, 252, 0.075)",
    "--sd-follow-highlight-border": "rgba(125, 211, 252, 0.42)",
    "--sd-follow-highlight-border-word": "rgba(125, 211, 252, 0.28)",
}

COLOR_TO_VARIABLE = {
    "ink": "--sd-color-ink",
    "panel": "--sd-color-panel",
    "panelSoft": "--sd-color-panel-soft",
    "line": "--sd-color-line",
    "story": "--sd-color-story",
    "muted": "--sd-color-muted",
    "moss": "--sd-color-moss",
    "ember": "--sd-color-ember",
    "tide": "--sd-color-tide",
}

SURFACE_TO_VARIABLE = {
    "sunken": "--sd-surface-sunken",
    "hover": "--sd-surface-hover",
    "warning": "--sd-surface-warning",
}


class PresetValidationError(ValueError):
    pass


class PresetConflictError(ValueError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dirs() -> None:
    CUSTOM_PRESET_ROOT.mkdir(parents=True, exist_ok=True)
    CUSTOM_PRESET_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def _load_store() -> dict[str, Any]:
    _ensure_dirs()
    if not CUSTOM_PRESETS_FILE.exists():
        return {"version": 1, "updated_at": None, "presets": []}
    try:
        raw = json.loads(CUSTOM_PRESETS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "updated_at": None, "presets": []}
    if not isinstance(raw, dict):
        return {"version": 1, "updated_at": None, "presets": []}
    presets = raw.get("presets") if isinstance(raw.get("presets"), list) else []
    return {"version": 1, "updated_at": raw.get("updated_at"), "presets": presets}


def _save_store(store: dict[str, Any]) -> None:
    _ensure_dirs()
    store["version"] = 1
    store["updated_at"] = _now_iso()
    tmp_path = CUSTOM_PRESETS_FILE.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(store, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(CUSTOM_PRESETS_FILE)


def _slugify(value: str, fallback: str = "custom-preset") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    slug = slug[:58].strip("-") or fallback
    if not re.match(r"^[a-z0-9]", slug):
        slug = f"{fallback}-{slug}".strip("-")
    if len(slug) < 2:
        slug = f"{slug}-preset"
    return slug[:64]


def _unique_id(base_id: str, existing_ids: set[str]) -> str:
    candidate = base_id
    if candidate not in existing_ids:
        return candidate
    for index in range(2, 1000):
        suffix = f"-{index}"
        candidate = f"{base_id[:64 - len(suffix)]}{suffix}"
        if candidate not in existing_ids:
            return candidate
    raise PresetConflictError("Could not find a safe unique preset id.")


def _json_size_ok(payload: dict[str, Any]) -> None:
    try:
        size = len(json.dumps(payload).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise PresetValidationError(f"Preset must be JSON serializable: {exc}") from exc
    if size > MAX_PRESET_JSON_BYTES:
        raise PresetValidationError("Preset JSON is too large. Keep custom presets under 256 KB.")


def _assert_safe_text(value: Any, field: str, max_length: int = 260) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > max_length:
        raise PresetValidationError(f"{field} is too long.")
    lowered = text.lower()
    if any(token in lowered for token in ("javascript:", "http://", "https://", "@import", "<script", "url(")):
        raise PresetValidationError(f"{field} contains unsupported remote or executable content.")
    if not SAFE_TEXT_RE.fullmatch(text):
        raise PresetValidationError(f"{field} contains unsupported characters.")
    return text


def _assert_safe_font(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise PresetValidationError(f"{field} cannot be empty.")
    lowered = text.lower()
    forbidden = ("url(", "@import", "javascript:", "http://", "https://", "<", ">", "{", "}", ";")
    if any(token in lowered for token in forbidden):
        raise PresetValidationError(f"{field} contains unsupported font/CSS content.")
    if len(text) > 260 or not SAFE_CSS_TOKEN_RE.fullmatch(text):
        raise PresetValidationError(f"{field} contains unsupported characters.")
    return text


def _hex_to_rgb_triplet(value: str) -> str:
    color = value.strip()
    if not HEX_COLOR_RE.fullmatch(color):
        raise PresetValidationError(f"Invalid color value: {value}")
    digits = color.lstrip("#")
    if len(digits) == 3:
        digits = "".join(char * 2 for char in digits)
    rgb = [int(digits[index:index + 2], 16) for index in range(0, 6, 2)]
    return f"{rgb[0]} {rgb[1]} {rgb[2]}"


def _validate_rgb_triplet(value: Any, field: str) -> str:
    text = str(value or "").strip()
    match = CSS_RGB_TRIPLET_RE.fullmatch(text)
    if not match:
        raise PresetValidationError(f"{field} must be an RGB triplet like '12 34 56'.")
    channels = [int(part) for part in match.groups()]
    if any(channel < 0 or channel > 255 for channel in channels):
        raise PresetValidationError(f"{field} RGB channels must be between 0 and 255.")
    return f"{channels[0]} {channels[1]} {channels[2]}"


def _validate_css_token(value: Any, field: str, max_length: int = 900) -> str:
    text = str(value or "").strip()
    lowered = text.lower()
    forbidden = ("javascript:", "http://", "https://", "@import", "<", ">", "{", "}", ";")
    if any(token in lowered for token in forbidden):
        raise PresetValidationError(f"{field} contains unsupported remote or executable content.")
    if len(text) > max_length or not SAFE_CSS_TOKEN_RE.fullmatch(text):
        raise PresetValidationError(f"{field} contains unsupported characters.")
    return text


def _validate_gradient(value: Any, field: str) -> str:
    text = str(value or "").strip()
    lowered = text.lower()
    if "url(" in lowered or "http://" in lowered or "https://" in lowered or "javascript:" in lowered:
        raise PresetValidationError(f"{field} cannot use remote URLs or executable content.")
    if any(char in text for char in (";", "{", "}", "<", ">")):
        raise PresetValidationError(f"{field} contains unsafe CSS characters.")
    if len(text) > 1800 or not SAFE_GRADIENT_RE.fullmatch(text):
        raise PresetValidationError(f"{field} contains unsupported gradient characters.")
    allowed_functions = ("linear-gradient(", "radial-gradient(", "repeating-linear-gradient(", "repeating-radial-gradient(")
    if "gradient(" in lowered and not any(function in lowered for function in allowed_functions):
        raise PresetValidationError(f"{field} uses an unsupported gradient function.")
    return text


def _validate_asset_path(value: Any, field: str) -> str:
    text = str(value or "").replace("\\", "/").strip().lstrip("/")
    if not text:
        return ""
    if "://" in text or text.startswith("/") or ".." in Path(text).parts:
        raise PresetValidationError(f"{field} must be a relative path inside ui_presets/assets.")
    candidate = (CUSTOM_PRESET_ASSETS_DIR / text).resolve(strict=False)
    root = CUSTOM_PRESET_ASSETS_DIR.resolve(strict=False)
    try:
        if os.path.commonpath([str(candidate), str(root)]) != str(root):
            raise PresetValidationError(f"{field} escapes the approved preset assets folder.")
    except ValueError as exc:
        raise PresetValidationError(f"{field} escapes the approved preset assets folder.") from exc
    return text


def _sanitize_token_value(value: Any, field: str, depth: int = 0) -> Any:
    if depth > 4:
        raise PresetValidationError(f"{field} is nested too deeply.")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if abs(float(value)) > 10000:
            raise PresetValidationError(f"{field} numeric value is too large.")
        return value
    if isinstance(value, str):
        if field.endswith(".asset") or field.endswith(".assetPath"):
            return _validate_asset_path(value, field)
        return _validate_css_token(value, field)
    if isinstance(value, list):
        if len(value) > 24:
            raise PresetValidationError(f"{field} has too many entries.")
        return [_sanitize_token_value(item, field, depth + 1) for item in value]
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        if len(value) > 80:
            raise PresetValidationError(f"{field} has too many keys.")
        for key, item in value.items():
            key_text = str(key)
            if not SAFE_KEY_RE.fullmatch(key_text) or "__" in key_text:
                raise PresetValidationError(f"{field} contains an unsupported token key: {key_text}")
            sanitized[key_text] = _sanitize_token_value(item, f"{field}.{key_text}", depth + 1)
        return sanitized
    raise PresetValidationError(f"{field} contains an unsupported value type.")


def _sanitize_tokens(tokens: Any) -> dict[str, Any]:
    if not isinstance(tokens, dict):
        raise PresetValidationError("Preset tokens must be an object.")
    allowed_sections = {
        "colors",
        "typography",
        "surfaces",
        "borders",
        "shadows",
        "texture",
        "radii",
        "spacing",
        "components",
        "componentOverrides",
    }
    sanitized: dict[str, Any] = {}
    for section, value in tokens.items():
        if section not in allowed_sections:
            raise PresetValidationError(f"Unsupported token section: {section}")
        if not isinstance(value, dict):
            raise PresetValidationError(f"Token section {section} must be an object.")
        section_data: dict[str, Any] = {}
        for key, item in value.items():
            if not SAFE_KEY_RE.fullmatch(str(key)):
                raise PresetValidationError(f"Unsupported token key in {section}: {key}")
            if section == "colors":
                color = str(item or "").strip()
                if not HEX_COLOR_RE.fullmatch(color):
                    raise PresetValidationError(f"Color token {key} must be a hex color.")
                section_data[str(key)] = color
            elif section == "typography":
                section_data[str(key)] = _assert_safe_font(item, f"tokens.typography.{key}")
            else:
                section_data[str(key)] = _sanitize_token_value(item, f"tokens.{section}.{key}")
        sanitized[str(section)] = section_data
    if "colors" not in sanitized:
        raise PresetValidationError("Preset tokens.colors is required.")
    return sanitized


def _css_variables_from_tokens(tokens: dict[str, Any]) -> dict[str, str]:
    variables = dict(DEFAULT_CSS_VARIABLES)
    colors = tokens.get("colors", {})
    surfaces = tokens.get("surfaces", {})
    typography = tokens.get("typography", {})
    shadows = tokens.get("shadows", {})
    texture = tokens.get("texture", {})

    for token_name, css_name in COLOR_TO_VARIABLE.items():
        if token_name in colors:
            variables[css_name] = _hex_to_rgb_triplet(str(colors[token_name]))
    for token_name, css_name in SURFACE_TO_VARIABLE.items():
        if token_name in surfaces and HEX_COLOR_RE.fullmatch(str(surfaces[token_name])):
            variables[css_name] = _hex_to_rgb_triplet(str(surfaces[token_name]))
    if typography.get("sans"):
        variables["--sd-font-sans"] = _assert_safe_font(typography["sans"], "tokens.typography.sans")
    if typography.get("story"):
        variables["--sd-font-story"] = _assert_safe_font(typography["story"], "tokens.typography.story")
    if shadows.get("glow"):
        variables["--sd-shadow-glow"] = _validate_css_token(shadows["glow"], "tokens.shadows.glow")
    if shadows.get("focus"):
        variables["--sd-shadow-focus"] = _validate_css_token(shadows["focus"], "tokens.shadows.focus")
    if texture.get("css"):
        variables["--sd-app-texture"] = _validate_gradient(texture["css"], "tokens.texture.css")
    if texture.get("size"):
        variables["--sd-app-texture-size"] = _validate_css_token(texture["size"], "tokens.texture.size", 160)
    if texture.get("opacity") is not None:
        opacity = float(texture["opacity"])
        if opacity < 0 or opacity > 1:
            raise PresetValidationError("tokens.texture.opacity must be between 0 and 1.")
        variables["--sd-app-texture-opacity"] = str(opacity)
    return variables


def _sanitize_css_variables(value: Any, tokens: dict[str, Any]) -> dict[str, str]:
    variables = _css_variables_from_tokens(tokens)
    if value is None:
        return variables
    if not isinstance(value, dict):
        raise PresetValidationError("cssVariables must be an object.")
    for name, raw_value in value.items():
        if name not in ALLOWED_CSS_VARIABLES:
            raise PresetValidationError(f"Unsupported CSS variable: {name}")
        field = f"cssVariables.{name}"
        if name in RGB_VARIABLES:
            variables[name] = _validate_rgb_triplet(raw_value, field)
        elif name in FONT_VARIABLES:
            variables[name] = _assert_safe_font(raw_value, field)
        elif name in GRADIENT_VARIABLES:
            variables[name] = _validate_gradient(raw_value, field)
        elif name in NUMBER_VARIABLES:
            numeric = float(raw_value)
            if numeric < 0 or numeric > 1:
                raise PresetValidationError(f"{field} must be between 0 and 1.")
            variables[name] = str(numeric)
        else:
            variables[name] = _validate_css_token(raw_value, field)
    return variables


def normalize_custom_preset(payload: dict[str, Any], *, mark_custom: bool = True) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PresetValidationError("Preset must be a JSON object.")
    _json_size_ok(payload)

    preset_id = str(payload.get("id") or "").strip().lower()
    if not SLUG_RE.fullmatch(preset_id):
        raise PresetValidationError("Preset id must be a slug using lowercase letters, numbers, and dashes.")

    display_name = _assert_safe_text(
        payload.get("displayName") or payload.get("name"),
        "displayName",
        80,
    )
    if not display_name:
        raise PresetValidationError("Preset displayName is required.")

    short_description = _assert_safe_text(
        payload.get("shortDescription") or payload.get("description"),
        "shortDescription",
        180,
    )
    category = _assert_safe_text(payload.get("category") or "Custom", "category", 40) or "Custom"
    version = _assert_safe_text(payload.get("version") or "1.0.0", "version", 32) or "1.0.0"
    author = _assert_safe_text(payload.get("author") or "Local custom preset", "author", 80) or "Local custom preset"
    source = _assert_safe_text(payload.get("source") or "Imported locally", "source", 120) or "Imported locally"
    compatibility_version = _assert_safe_text(
        payload.get("compatibilityVersion") or CURRENT_COMPATIBILITY_VERSION,
        "compatibilityVersion",
        16,
    ) or CURRENT_COMPATIBILITY_VERSION

    tags_raw = payload.get("tags") or payload.get("moodTags") or []
    if isinstance(tags_raw, str):
        tags_raw = [part.strip() for part in tags_raw.split(",") if part.strip()]
    if not isinstance(tags_raw, list):
        raise PresetValidationError("tags must be an array of short strings.")
    tags = [_assert_safe_text(tag, "tags", 32) for tag in tags_raw[:8]]
    tags = [tag for tag in tags if tag]

    tokens = _sanitize_tokens(payload.get("tokens"))
    css_variables = _sanitize_css_variables(payload.get("cssVariables"), tokens)
    thumbnail = _sanitize_token_value(payload.get("thumbnail") or {}, "thumbnail") if payload.get("thumbnail") else {}
    textures = _sanitize_token_value(payload.get("textures") or [], "textures") if payload.get("textures") else []
    component_variants = (
        _sanitize_token_value(payload.get("componentVariants") or {}, "componentVariants")
        if payload.get("componentVariants")
        else {}
    )

    return {
        "id": preset_id,
        "displayName": display_name,
        "name": display_name,
        "shortDescription": short_description,
        "description": short_description,
        "category": category,
        "tags": tags,
        "version": version,
        "author": author,
        "source": source,
        "compatibilityVersion": compatibility_version,
        "builtIn": False,
        "custom": bool(mark_custom),
        "disabled": False,
        "tokens": tokens,
        "cssVariables": css_variables,
        "thumbnail": thumbnail,
        "componentVariants": component_variants,
        "textures": textures,
        "updatedAt": _now_iso(),
    }


def list_custom_presets() -> list[dict[str, Any]]:
    store = _load_store()
    valid_presets: list[dict[str, Any]] = []
    dirty = False
    for preset in store["presets"]:
        try:
            normalized = normalize_custom_preset(preset)
            valid_presets.append(normalized)
            if normalized != preset:
                dirty = True
        except PresetValidationError as exc:
            disabled = deepcopy(preset) if isinstance(preset, dict) else {"id": "invalid-preset"}
            disabled["disabled"] = True
            disabled["validationError"] = str(exc)
            valid_presets.append(disabled)
            dirty = True
    if dirty:
        store["presets"] = valid_presets
        _save_store(store)
    return valid_presets


def import_custom_preset(payload: dict[str, Any], *, conflict_strategy: str = "error") -> dict[str, Any]:
    store = _load_store()
    presets = [preset for preset in store["presets"] if isinstance(preset, dict)]
    existing_ids = {str(preset.get("id") or "") for preset in presets}
    existing_ids.update(BUILT_IN_PRESET_IDS)
    preset = normalize_custom_preset(payload)
    incoming_id = preset["id"]

    strategy = (conflict_strategy or "error").strip().lower()
    if incoming_id in BUILT_IN_PRESET_IDS:
        if strategy in {"rename", "copy"}:
            preset["id"] = _unique_id(f"{incoming_id}-custom", existing_ids)
        else:
            raise PresetConflictError("Built-in preset ids are reserved. Import as a renamed copy instead.")
    elif incoming_id in existing_ids:
        if strategy == "overwrite":
            presets = [existing for existing in presets if existing.get("id") != incoming_id]
        elif strategy in {"rename", "copy"}:
            preset["id"] = _unique_id(incoming_id, existing_ids)
        else:
            raise PresetConflictError(f"Custom preset id '{incoming_id}' already exists.")

    preset["updatedAt"] = _now_iso()
    presets.append(preset)
    store["presets"] = sorted(presets, key=lambda item: str(item.get("displayName") or item.get("name") or item.get("id")).lower())
    _save_store(store)
    return {"preset": preset, "presets": list_custom_presets(), "storage_path": str(CUSTOM_PRESETS_FILE)}


def duplicate_preset(payload: dict[str, Any], *, display_name: str | None = None) -> dict[str, Any]:
    store = _load_store()
    presets = [preset for preset in store["presets"] if isinstance(preset, dict)]
    existing_ids = {str(preset.get("id") or "") for preset in presets}
    existing_ids.update(BUILT_IN_PRESET_IDS)

    source = deepcopy(payload)
    source_name = display_name or f"{source.get('displayName') or source.get('name') or source.get('id')} Custom"
    source["displayName"] = source_name
    source["name"] = source_name
    source["id"] = _unique_id(_slugify(source_name), existing_ids)
    source["category"] = source.get("category") or "Custom"
    source["source"] = f"Duplicated from {payload.get('displayName') or payload.get('name') or payload.get('id')}"
    source["author"] = "Local custom preset"
    source["builtIn"] = False
    source["custom"] = True
    return import_custom_preset(source, conflict_strategy="rename")


def export_preset(payload: dict[str, Any]) -> dict[str, Any]:
    preset = normalize_custom_preset(payload, mark_custom=bool(payload.get("custom")))
    _ensure_dirs()
    export_id = preset["id"]
    file_name = f"{export_id}.json"
    path = EXPORT_DIR / file_name
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = EXPORT_DIR / f"{export_id}_{stamp}.json"
    path.write_text(json.dumps(preset, indent=2, sort_keys=True), encoding="utf-8")
    return {"preset": preset, "export_path": str(path), "file_name": path.name}


def delete_custom_preset(preset_id: str) -> dict[str, Any]:
    if not SLUG_RE.fullmatch(preset_id or ""):
        raise PresetValidationError("Preset id must be a safe slug.")
    store = _load_store()
    presets = [preset for preset in store["presets"] if isinstance(preset, dict)]
    kept = [preset for preset in presets if preset.get("id") != preset_id]
    if len(kept) == len(presets):
        raise FileNotFoundError(preset_id)
    store["presets"] = kept
    _save_store(store)
    return {"deleted_id": preset_id, "presets": list_custom_presets()}
