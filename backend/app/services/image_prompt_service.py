from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.database import db_session
from app.generation.model_provider import LMStudioClient
from app.generation.router import resolve_task_model_settings, task_parameters
from app.memory.engine import format_visual_story_state_for_prompt


WORLD_FIELDS = ["setting", "tone", "rules", "locations", "factions", "conflicts", "history"]
PROFILE_FIELDS = [
    "base_visual_description",
    "face_description",
    "hair",
    "body_build",
    "age_marker",
    "default_outfit",
    "distinctive_marks",
    "color_palette",
    "negative_prompt",
    "z_image_lora_trigger",
    "alternate_lora_triggers",
    "preferred_voice",
    "visual_consistency_notes",
    "used_in_image_prompts",
]
VISUAL_STATE_KEY_HINTS = (
    "outfit",
    "cloak",
    "clothing",
    "wearing",
    "injury",
    "wound",
    "scar",
    "bandage",
    "blood",
    "dirt",
    "wet",
    "hair",
    "face",
    "expression",
    "location",
    "carrying",
    "inventory",
    "weapon",
    "object",
)
DEFAULT_IMAGE_PROMPT_STYLE_PRESET_ID = "gritty_dark_fantasy_realism"
IMAGE_PROMPT_STYLE_PRESETS: dict[str, dict[str, str]] = {
    "gritty_dark_fantasy_realism": {
        "label": "Gritty Dark Fantasy Realism",
        "image_prompt_style": (
            "gritty grounded fantasy realism, rough still from a dark fantasy film or old documentary footage, "
            "imperfect camera quality, slight grain, practical camera angle"
        ),
        "preferred_visual_tone": "somber, weathered, tense, restrained, lived-in, not glamorous",
        "realism_notes": "realistic faces, practical clothing, dirt, sweat, mud, blood, worn fabric, imperfect skin",
        "lighting_camera_notes": (
            "natural low light, bad weather, campfire, torchlight, window light, overcast daylight when appropriate; "
            "avoid glossy poster lighting"
        ),
        "negative_prompt": (
            "plastic skin, glossy airbrushed skin, glamour lighting, sterile studio lighting, shiny clean clothes, "
            "generic fantasy poster, overdramatic poster composition, anime, cartoon, extra fingers, extra limbs, "
            "modern objects unless present in scene"
        ),
    },
    "somber_fantasy_lora_style": {
        "label": "Somber Fantasy LoRA Style",
        "image_prompt_style": (
            "somber grounded fantasy realism, consistent character LoRA portrait language, worn costumes, natural faces, "
            "muted color palette"
        ),
        "preferred_visual_tone": "quiet, bleak, intimate, character-focused, low magic",
        "realism_notes": "stable facial identity, realistic wounds and grime, no beautified glamour treatment",
        "lighting_camera_notes": "soft natural shadow, candlelight, torchlight, campfire glow, practical medium shots",
        "negative_prompt": (
            "wrong face, plastic skin, glamour portrait, clean costume, anime, cartoon, extra fingers, extra limbs, "
            "generic armor, sterile studio look"
        ),
    },
    "rough_medieval_documentary": {
        "label": "Rough Medieval Documentary",
        "image_prompt_style": (
            "rough medieval documentary still, imperfect handheld camera feeling, naturalistic period texture, "
            "weather-beaten people and locations"
        ),
        "preferred_visual_tone": "plain, observational, grim, unsentimental, tactile",
        "realism_notes": "mud, sweat, uneven skin, worn wool and leather, practical tools, believable wounds",
        "lighting_camera_notes": "available light, overcast sky, firelight, torchlight, no polished cinematic glow",
        "negative_prompt": (
            "fantasy poster, heroic glamour pose, plastic skin, clean costume, modern objects, anime, cartoon, "
            "extra fingers, extra limbs, glossy studio lighting"
        ),
    },
    "cleaner_realistic": {
        "label": "Cleaner Realistic",
        "image_prompt_style": "grounded realistic fantasy still, clear subject, believable faces and clothing, restrained style",
        "preferred_visual_tone": "natural, readable, calm, realistic, lightly stylized",
        "realism_notes": "real skin texture, practical costumes, subtle dirt and wear when appropriate",
        "lighting_camera_notes": "natural cinematic light, clear framing, no exaggerated poster composition",
        "negative_prompt": (
            "plastic skin, overprocessed beauty filter, anime, cartoon, extra fingers, extra limbs, modern objects unless present"
        ),
    },
}
STYLE_PRESET_FIELD_KEYS = (
    "image_prompt_style",
    "preferred_visual_tone",
    "realism_notes",
    "lighting_camera_notes",
)
STRONG_DEFAULT_NEGATIVE_PROMPT = (
    "low quality, blurry, distorted anatomy, unreadable text, watermark, text, plastic skin, glossy airbrushed skin, "
    "glossy fantasy poster, overly clean clothing, clean perfect clothing, glamour lighting, sterile studio lighting, "
    "shiny fantasy armor, generic fantasy poster, overdramatic poster composition, anime, cartoon, extra fingers, "
    "extra limbs, extra hands, malformed hands, duplicate faces, modern objects unless present in scene"
)
VISUAL_BEAT_POSITIVE_HINTS = (
    "campfire",
    "torch",
    "lantern",
    "rain",
    "storm",
    "mud",
    "blood",
    "wound",
    "injur",
    "bandage",
    "scar",
    "cloak",
    "compass",
    "blade",
    "sword",
    "chapel",
    "ruin",
    "road",
    "forest",
    "gate",
    "door",
    "window",
    "fire",
    "ashes",
    "smoke",
    "kneel",
    "stand",
    "sits",
    "lean",
    "clutch",
    "hide",
    "draw",
    "enter",
    "return",
    "watch",
    "whisper",
)
VISUAL_BEAT_FUTURE_HINTS = (
    "tomorrow",
    "later",
    "would",
    "will",
    "planned to",
    "promised to",
    "meant to",
    "intended to",
    "next",
)
VISUAL_DESCRIPTOR_HINTS = (
    "adult",
    "age",
    "beard",
    "body",
    "boots",
    "broad",
    "build",
    "cloak",
    "clothes",
    "clothing",
    "coat",
    "color",
    "dress",
    "eyes",
    "face",
    "frame",
    "freckle",
    "glasses",
    "hair",
    "hands",
    "hat",
    "height",
    "jacket",
    "lean",
    "leather",
    "mark",
    "nose",
    "outfit",
    "robe",
    "scar",
    "shirt",
    "skin",
    "spectacles",
    "stubble",
    "tall",
    "tattoo",
    "tunic",
    "weathered",
    "wool",
    "wound",
)
PROSE_LEAK_HINTS = (
    " said ",
    " asked ",
    " whispered ",
    " replied ",
    " thought ",
    " remembered ",
    " wondered ",
    "chapter",
    "scene",
)
NARRATIVE_FRAGMENT_STARTERS = (
    "after",
    "although",
    "as",
    "because",
    "before",
    "though",
    "when",
    "while",
)
NARRATIVE_EXCERPT_HINTS = (
    "all sharp angles",
    "eyes fixed on",
    "sat in silence",
    "strength was in",
    "the winter won't wait",
    "won't wait for us",
)
DIALOGUE_QUOTE_MARKS = ('"', "“", "”", "„", "«", "»")
INTERNAL_PROMPT_TERMS = (
    "preferred storydriver workflow",
    "storydriver workflow",
    "workflow notes",
    "workflow mapping",
    "selected workflow",
    "workflow_id",
    "source_hash",
    "prompt diagnostics",
    "quality_warnings",
    "continuity_used",
    "characters_included",
    "positive prompt node",
    "negative prompt node",
    "positive node",
    "negative node",
    "seed node",
    "output node",
    "node id",
    "class_type",
    "comfyui",
    "detected automatically",
    "advanced mapping",
    "storydriver only replaces",
)
INTERNAL_LABEL_PREFIXES = (
    "name",
    "role",
    "visual profile",
    "plain prompt lora tokens",
    "lora trigger text",
    "current outfit",
    "current location",
    "world visual tone/state",
    "objects",
    "object",
    "setting",
    "tone",
    "locations",
)
CONCRETE_VISUAL_SUBJECT_HINTS = (
    "woman",
    "man",
    "girl",
    "boy",
    "sister",
    "traveler",
    "character",
    "farm",
    "cottage",
    "camp",
    "chapel",
    "road",
    "forest",
    "table",
    "fire",
    "campfire",
    "door",
    "window",
    "cloak",
    "wound",
    "bandage",
    "compass",
    "sword",
    "map",
    "rain",
    "mud",
    "torch",
    "lantern",
)


def clean_text(value: Any, limit: int | None = None) -> str:
    text = " ".join(str(value or "").split())
    if limit is not None and len(text) > limit:
        return text[:limit].rsplit(" ", 1)[0].strip()
    return text


def strip_prompt_label(value: str) -> str:
    text = clean_text(value)
    for prefix in INTERNAL_LABEL_PREFIXES:
        text = re.sub(rf"^\s*{re.escape(prefix)}\s*:\s*", "", text, flags=re.IGNORECASE)
    label_match = re.match(r"^\s*([a-z_ ]{3,48})\s*:\s+(.+)$", text, flags=re.IGNORECASE)
    if label_match:
        label = clean_text(label_match.group(1).replace("_", " "), 80)
        rest = clean_text(label_match.group(2))
        label_normalized = normalize_name(label)
        if any(hint in label_normalized for hint in VISUAL_STATE_KEY_HINTS):
            text = f"{label} {rest}"
        else:
            text = rest
    return clean_text(text)


def looks_internal_prompt_text(value: str) -> bool:
    text = clean_text(value)
    if not text:
        return False
    normalized = normalize_name(text)
    if any(term in normalized for term in INTERNAL_PROMPT_TERMS):
        return True
    if re.search(r"\b\d{1,5}\s*:\s*\d{1,5}\s*\.\s*(text|seed|positive|negative|prompt)\b", normalized):
        return True
    if re.search(r"\b(node|input|mapping)\s+\d{1,5}\b", normalized):
        return True
    if (text.startswith("{") or text.startswith("[")) and (":" in text or '"' in text):
        return True
    return False


def looks_like_narrative_visual_leak(value: str, *, allow_sentence: bool = False) -> bool:
    """Reject prose/dialogue excerpts before they become image-prompt fragments."""
    text = clean_text(value)
    if not text:
        return False
    normalized = f" {normalize_name(text)} "
    words = word_count(text)
    if any(mark in text for mark in DIALOGUE_QUOTE_MARKS):
        return True
    if any(hint in normalized for hint in NARRATIVE_EXCERPT_HINTS):
        return True
    if any(hint in normalized for hint in PROSE_LEAK_HINTS):
        return words > 8 or not allow_sentence
    first_word = normalized.strip().split(" ", 1)[0]
    if first_word in NARRATIVE_FRAGMENT_STARTERS and words > 8:
        return True
    if words > 24 and re.search(r"\b(she|he|they|her|his|their)\b.+\b(was|were|had|would|could)\b", normalized):
        return True
    return False


def sanitize_visual_fragment(
    value: Any,
    *,
    limit: int = 180,
    allow_sentence: bool = False,
    allow_internal: bool = False,
) -> str:
    text = strip_prompt_label(clean_text(value, limit * 2))
    if not text:
        return ""
    if not allow_internal and looks_internal_prompt_text(text):
        return ""
    normalized = normalize_name(text)
    if looks_like_narrative_visual_leak(text, allow_sentence=allow_sentence):
        return ""
    if not allow_sentence:
        if word_count(text) > 42:
            return ""
        if any(hint in normalized for hint in PROSE_LEAK_HINTS) and word_count(text) > 18:
            return ""
    text = text.replace(" | ", ", ")
    text = re.sub(r"\s*;\s*", ", ", text)
    text = re.sub(r"\s*:\s*", " ", text)
    return clean_text(text, limit)


def prompt_fragments(value: str | None) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[,;\n|]+", str(value))
    return [part for part in (sanitize_visual_fragment(part, limit=220) for part in parts) if part]


def style_context_fragments(value: str | None) -> list[str]:
    fragments: list[str] = []
    for fragment in prompt_fragments(value):
        normalized = normalize_name(fragment)
        if word_count(fragment) > 14:
            continue
        if any(hint in normalized for hint in PROSE_LEAK_HINTS):
            continue
        fragments.append(fragment)
    return dedupe(fragments)[:5]


def style_prompt_fragments(style_used: str = "", style_context: str = "") -> list[str]:
    label = normalize_name(style_used)
    if "rough medieval documentary" in label:
        base = [
            "rough medieval documentary still",
            "imperfect camera quality",
            "slight grain",
            "practical camera angle",
            "available natural light",
            "weathered period texture",
            "worn dirty clothing",
            "realistic faces",
        ]
    elif "cleaner realistic" in label:
        base = [
            "grounded realistic fantasy still",
            "clear practical framing",
            "natural light",
            "believable faces",
            "practical clothing",
        ]
    elif "somber fantasy" in label:
        base = [
            "somber grounded fantasy realism still",
            "consistent character portrait language",
            "muted color palette",
            "natural faces",
            "worn costumes",
            "soft practical shadows",
        ]
    else:
        base = [
            "rough grounded dark fantasy realism still",
            "rough medieval documentary feel",
            "imperfect camera quality",
            "slight grain",
            "practical camera angle",
            "natural low light",
            "worn dirty clothing",
            "realistic faces",
            "not glossy",
            "not overdramatic",
        ]
    custom = style_context_fragments(style_context)
    return dedupe([*base, *custom])


def visual_beat_fragment(value: str, scene_text: str = "") -> str:
    text = clean_text(value, 360)
    if not text:
        return ""
    text = re.sub(r'"[^"]{1,240}"', "quiet conversation", text)
    text = re.sub(r"\b(said|asked|whispered|replied)\b", "speaking", text, flags=re.IGNORECASE)
    fragment = sanitize_visual_fragment(text, limit=260, allow_sentence=True)
    if not fragment:
        return ""
    if scene_text and word_count(fragment) > 52 and fragment.lower() in scene_text.lower():
        return clean_text(fragment, 220)
    return fragment


def character_prompt_phrase(character: dict[str, Any]) -> str:
    name = sanitize_visual_fragment(character.get("name"), limit=120, allow_sentence=True)
    if not name:
        return ""
    profile = character.get("visual_profile") or {}
    has_profile_identity = any(
        clean_text(profile.get(field))
        for field in (
            "base_visual_description",
            "face_description",
            "hair",
            "body_build",
            "age_marker",
            "distinctive_marks",
        )
    )
    lora_tokens = [
        sanitize_visual_fragment(trigger, limit=120, allow_sentence=True)
        for trigger in [
            character.get("lora_trigger"),
            profile.get("z_image_lora_trigger"),
            profile.get("alternate_lora_triggers"),
        ]
        if clean_text(trigger)
    ]
    stable_bits = [
        compact_character_card_fragment(profile.get("base_visual_description"), limit=220),
        compact_character_card_fragment(profile.get("face_description"), limit=180),
        profile.get("hair"),
        profile.get("body_build"),
        profile.get("age_marker"),
        profile.get("distinctive_marks"),
    ]
    details: list[str] = []
    for bit in stable_bits:
        cleaned = sanitize_visual_fragment(bit, limit=170, allow_sentence=False)
        if not cleaned or normalize_name(cleaned) == normalize_name(name):
            continue
        details.append(cleaned)
        if len(details) >= 5:
            break
    for item in sorted(character.get("live_visual_state", []), key=visual_state_priority):
        key = clean_text(item.get("key"), 80).replace("_", " ")
        value = clean_text(item.get("value"), 190)
        if key and value and is_visual_state_key(key):
            cleaned = sanitize_visual_fragment(f"{key}: {value}", limit=190, allow_sentence=False)
            if cleaned:
                details.append(cleaned)
        if len(details) >= 12:
            break
    if not current_outfit_values(character):
        default_outfit = sanitize_visual_fragment(profile.get("default_outfit"), limit=170, allow_sentence=False)
        if default_outfit:
            details.append(default_outfit)
    fallback_bits = [profile.get("color_palette")]
    if not has_profile_identity:
        fallback_bits.append(compact_character_card_fragment(character.get("appearance"), limit=180))
    fallback_bits.append(compact_character_card_fragment(character.get("image_prompt"), limit=220))
    for bit in fallback_bits:
        cleaned = sanitize_visual_fragment(bit, limit=170, allow_sentence=False)
        if cleaned:
            details.append(cleaned)
        if len(details) >= 14:
            break
    return ", ".join(dedupe([*lora_tokens, name, *details]))


def sanitize_positive_prompt(prompt: str, *, scene_text: str = "", limit: int = 1450) -> str:
    fragments: list[str] = []
    for raw in re.split(r"[,;\n|]+", prompt):
        fragment = sanitize_visual_fragment(raw, limit=240, allow_sentence=True)
        if not fragment:
            continue
        if scene_text and word_count(fragment) > 58 and fragment.lower() in scene_text.lower():
            continue
        fragments.append(fragment)
    cleaned = dedupe(fragments)
    output: list[str] = []
    current_length = 0
    for fragment in cleaned:
        next_length = current_length + len(fragment) + (2 if output else 0)
        if next_length > limit:
            break
        output.append(fragment)
        current_length = next_length
    return ", ".join(output)


def sanitize_negative_prompt(prompt: str, *, limit: int = 1000) -> str:
    fragments: list[str] = []
    for raw in re.split(r"[,;\n|]+", prompt or ""):
        fragment = strip_prompt_label(clean_text(raw, 160))
        if not fragment or looks_internal_prompt_text(fragment):
            continue
        fragments.append(fragment)
    return ", ".join(dedupe(fragments))[:limit]


def scene_excerpt(text: str, limit: int = 900) -> str:
    return clean_text(text, limit)


def normalize_name(value: str) -> str:
    return clean_text(value).lower()


def name_is_in_scene(name: str, scene_text: str) -> bool:
    cleaned = clean_text(name)
    if not cleaned or not scene_text:
        return False
    parts = [cleaned]
    first = cleaned.split(" ", 1)[0]
    if len(first) >= 3 and first.lower() != cleaned.lower():
        parts.append(first)
    for part in parts:
        if re.search(rf"(?<![\w-]){re.escape(part)}(?![\w-])", scene_text, flags=re.IGNORECASE):
            return True
    return False


def row_dict(row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def split_prompt_parts(value: str | None) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[,;\n]+", str(value))
    return [clean_text(part, 260) for part in parts if clean_text(part)]


def word_count(value: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", value))


def compact_character_card_fragment(
    value: Any,
    *,
    limit: int = 220,
    max_words: int = 34,
    require_visual_hint: bool = True,
) -> str:
    """Keep character-card visual descriptors from becoming scene excerpts."""
    text = clean_text(value)
    if not text:
        return ""
    normalized = f" {normalize_name(text)} "
    if looks_like_narrative_visual_leak(text, allow_sentence=False):
        return ""
    if len(text) <= limit and word_count(text) <= max_words:
        if not require_visual_hint or any(hint in normalized for hint in VISUAL_DESCRIPTOR_HINTS):
            return text

    fragments = [
        clean_text(fragment, limit)
        for fragment in re.split(r"(?<=[.!?])\s+|[;\n]+", text)
        if clean_text(fragment)
    ]
    selected: list[str] = []
    for fragment in fragments:
        if word_count(fragment) > max_words:
            continue
        fragment_normalized = f" {normalize_name(fragment)} "
        if looks_like_narrative_visual_leak(fragment, allow_sentence=False):
            continue
        if any(hint in fragment_normalized for hint in PROSE_LEAK_HINTS):
            continue
        if require_visual_hint and not any(hint in fragment_normalized for hint in VISUAL_DESCRIPTOR_HINTS):
            continue
        selected.append(fragment)
        if len(selected) >= 2:
            break
    return clean_text("; ".join(selected), limit)


def resolve_image_prompt_style(
    image_prompt_style: str | None = "",
    preferred_visual_tone: str | None = "",
    realism_notes: str | None = "",
    lighting_camera_notes: str | None = "",
) -> dict[str, Any]:
    provided = {
        "image_prompt_style": clean_text(image_prompt_style),
        "preferred_visual_tone": clean_text(preferred_visual_tone),
        "realism_notes": clean_text(realism_notes),
        "lighting_camera_notes": clean_text(lighting_camera_notes),
    }
    if not any(provided.values()):
        preset = IMAGE_PROMPT_STYLE_PRESETS[DEFAULT_IMAGE_PROMPT_STYLE_PRESET_ID]
        return {
            "id": DEFAULT_IMAGE_PROMPT_STYLE_PRESET_ID,
            "label": preset["label"],
            "is_default": True,
            "text": "\n".join(preset[key] for key in STYLE_PRESET_FIELD_KEYS if preset.get(key)),
            "negative_prompt": preset["negative_prompt"],
            "fields": {key: preset[key] for key in STYLE_PRESET_FIELD_KEYS},
        }

    for preset_id, preset in IMAGE_PROMPT_STYLE_PRESETS.items():
        if all(provided[key] == clean_text(preset.get(key)) for key in STYLE_PRESET_FIELD_KEYS):
            return {
                "id": preset_id,
                "label": preset["label"],
                "is_default": False,
                "text": "\n".join(provided[key] for key in STYLE_PRESET_FIELD_KEYS if provided[key]),
                "negative_prompt": preset["negative_prompt"],
                "fields": provided,
            }

    return {
        "id": "custom",
        "label": "Custom",
        "is_default": False,
        "text": "\n".join(provided[key] for key in STYLE_PRESET_FIELD_KEYS if provided[key]),
        "negative_prompt": IMAGE_PROMPT_STYLE_PRESETS[DEFAULT_IMAGE_PROMPT_STYLE_PRESET_ID]["negative_prompt"],
        "fields": provided,
    }


def style_label_from_prompt(style_prompt: str | None) -> str:
    style_text = clean_text(style_prompt)
    if not style_text:
        return IMAGE_PROMPT_STYLE_PRESETS[DEFAULT_IMAGE_PROMPT_STYLE_PRESET_ID]["label"]
    normalized = normalize_name(style_text)
    for preset in IMAGE_PROMPT_STYLE_PRESETS.values():
        preset_text = "\n".join(preset[key] for key in STYLE_PRESET_FIELD_KEYS if preset.get(key))
        if normalized == normalize_name(preset_text):
            return preset["label"]
    return "Custom"


def load_visual_context(session_id: str, scene_text: str = "") -> dict[str, Any]:
    with db_session() as db:
        characters = db.execute(
            """
            SELECT
                c.id, c.name, c.role, c.appearance, c.current_state, c.image_prompt, c.lora_trigger,
                cvp.base_visual_description, cvp.face_description, cvp.hair, cvp.body_build,
                cvp.age_marker, cvp.default_outfit, cvp.distinctive_marks, cvp.color_palette,
                cvp.negative_prompt, cvp.z_image_lora_trigger, cvp.alternate_lora_triggers,
                cvp.preferred_voice, cvp.visual_consistency_notes,
                COALESCE(cvp.used_in_image_prompts, 1) AS used_in_image_prompts
            FROM session_characters sc
            JOIN characters c ON c.id = sc.character_id
            LEFT JOIN character_visual_profiles cvp ON cvp.character_id = c.id
            WHERE sc.session_id = ? AND sc.is_active = 1
            ORDER BY lower(c.name) ASC
            """,
            (session_id,),
        ).fetchall()
        live_rows = db.execute(
            """
            SELECT character_id, character_name, state_type, key, value, confidence, manual_override, updated_at
            FROM character_live_state
            WHERE session_id = ? AND archived = 0 AND disabled = 0
            ORDER BY manual_override DESC, updated_at DESC
            LIMIT 160
            """,
            (session_id,),
        ).fetchall()
        object_rows = db.execute(
            """
            SELECT object_key, name, state_type, value, owner_character_name, confidence, manual_override, updated_at
            FROM object_state
            WHERE session_id = ? AND archived = 0 AND disabled = 0
            ORDER BY manual_override DESC, updated_at DESC
            LIMIT 60
            """,
            (session_id,),
        ).fetchall()
        scene_rows = db.execute(
            """
            SELECT state_type, key, value, confidence, manual_override, updated_at
            FROM scene_live_state
            WHERE session_id = ? AND archived = 0 AND disabled = 0
            ORDER BY manual_override DESC, updated_at DESC
            LIMIT 40
            """,
            (session_id,),
        ).fetchall()
        world = db.execute(
            """
            SELECT setting, tone, rules, locations, factions, conflicts, history
            FROM world_notes
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        world_state_rows = db.execute(
            """
            SELECT state_type, key, value, confidence, manual_override, updated_at
            FROM world_live_state
            WHERE session_id = ? AND archived = 0 AND disabled = 0
            ORDER BY manual_override DESC, updated_at DESC
            LIMIT 40
            """,
            (session_id,),
        ).fetchall()

    live_by_character: dict[str, list[dict[str, Any]]] = {}
    for row in live_rows:
        item = row_dict(row)
        name = normalize_name(item.get("character_name"))
        if name:
            live_by_character.setdefault(name, []).append(item)

    normalized_characters: list[dict[str, Any]] = []
    for row in characters:
        item = row_dict(row)
        name = clean_text(item.get("name"))
        profile = {field: item.get(field) for field in PROFILE_FIELDS}
        character = {
            "id": item.get("id"),
            "name": name,
            "role": item.get("role") or "",
            "appearance": item.get("appearance") or "",
            "current_state": item.get("current_state") or "",
            "image_prompt": item.get("image_prompt") or "",
            "lora_trigger": item.get("lora_trigger") or "",
            "visual_profile": profile,
            "live_visual_state": live_by_character.get(normalize_name(name), [])[:12],
            "likely_present": name_is_in_scene(name, scene_text),
        }
        normalized_characters.append(character)

    return {
        "characters": normalized_characters,
        "objects": [row_dict(row) for row in object_rows],
        "scene_state": [row_dict(row) for row in scene_rows],
        "world": {field: world[field] for field in WORLD_FIELDS} if world else {},
        "world_state": [row_dict(row) for row in world_state_rows],
        "story_state": format_visual_story_state_for_prompt(session_id, relevance_text=scene_text),
    }


def is_visual_state_key(key: str) -> bool:
    normalized = normalize_name(key).replace("_", " ")
    return any(hint in normalized for hint in VISUAL_STATE_KEY_HINTS)


def current_outfit_values(character: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for item in character.get("live_visual_state", []):
        key = normalize_name(item.get("key"))
        if "outfit" in key or "cloak" in key or "clothing" in key or "wearing" in key:
            value = clean_text(item.get("value"), 220)
            if value:
                values.append(value)
    return values[:3]


def visual_state_priority(item: dict[str, Any]) -> int:
    key = normalize_name(item.get("key")).replace("_", " ")
    if any(hint in key for hint in ("outfit", "cloak", "clothing", "wearing")):
        return 0
    if any(hint in key for hint in ("injury", "wound", "bandage", "blood", "scar")):
        return 1
    if any(hint in key for hint in ("carrying", "inventory", "weapon", "object")):
        return 2
    if any(hint in key for hint in ("dirt", "mud", "wet")):
        return 3
    if "location" in key:
        return 4
    return 5


def character_visual_bits(character: dict[str, Any], *, include_lora: bool = True) -> list[str]:
    profile = character.get("visual_profile") or {}
    has_live_outfit = bool(current_outfit_values(character))
    has_profile_identity = any(
        clean_text(profile.get(field))
        for field in (
            "base_visual_description",
            "face_description",
            "hair",
            "body_build",
            "age_marker",
            "distinctive_marks",
        )
    )
    bits = [
        clean_text(character.get("name"), 120),
    ]
    if include_lora:
        for trigger in [
            clean_text(character.get("lora_trigger"), 120),
            clean_text(profile.get("z_image_lora_trigger"), 120),
            clean_text(profile.get("alternate_lora_triggers"), 220),
        ]:
            if trigger:
                bits.extend(split_prompt_parts(trigger))
    bits.extend(
        [
        compact_character_card_fragment(profile.get("base_visual_description"), limit=260),
        compact_character_card_fragment(profile.get("face_description"), limit=220),
        clean_text(profile.get("hair"), 180),
        clean_text(profile.get("body_build"), 180),
        clean_text(profile.get("age_marker"), 80),
        clean_text(profile.get("distinctive_marks"), 220),
        ]
    )
    for item in sorted(character.get("live_visual_state", []), key=visual_state_priority):
        key = clean_text(item.get("key"), 80).replace("_", " ")
        value = clean_text(item.get("value"), 220)
        if key and value and is_visual_state_key(key):
            bits.append(f"{key}: {value}")
    if not has_live_outfit:
        bits.append(clean_text(profile.get("default_outfit"), 220))
    bits.append(clean_text(profile.get("color_palette"), 140))
    if not has_profile_identity:
        bits.append(compact_character_card_fragment(character.get("appearance"), limit=220))
    bits.append(compact_character_card_fragment(character.get("image_prompt"), limit=260))
    notes = compact_character_card_fragment(profile.get("visual_consistency_notes"), limit=220, require_visual_hint=False)
    if notes:
        bits.append(notes)
    return dedupe([bit for bit in bits if bit])


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        normalized = normalize_name(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(value)
    return cleaned


def selected_characters_for_scene(visual_context: dict[str, Any]) -> list[dict[str, Any]]:
    eligible = [
        character
        for character in visual_context.get("characters", [])
        if (character.get("visual_profile") or {}).get("used_in_image_prompts", 1) != 0
    ]
    likely = [character for character in eligible if character.get("likely_present")]
    if likely:
        return likely[:5]
    return eligible[:2]


def continuity_for_characters(characters: list[dict[str, Any]]) -> dict[str, list[str]]:
    continuity: dict[str, list[str]] = {}
    for character in characters:
        name = clean_text(character.get("name"))
        if not name:
            continue
        bits = character_visual_bits(character, include_lora=True)
        continuity[name] = bits[:10]
    return continuity


def negative_prompt_for_characters(
    characters: list[dict[str, Any]],
    base_negative: str | None,
    style_negative_prompt: str | None = None,
) -> str:
    parts = [
        *split_prompt_parts(base_negative),
        *split_prompt_parts(style_negative_prompt),
    ]
    for character in characters:
        profile = character.get("visual_profile") or {}
        negative = clean_text(profile.get("negative_prompt"), 260)
        if negative:
            parts.extend(split_prompt_parts(negative))
    parts.extend(split_prompt_parts(STRONG_DEFAULT_NEGATIVE_PROMPT))
    return sanitize_negative_prompt(", ".join(dedupe([part for part in parts if part])), limit=1400)


def visual_world_bits(visual_context: dict[str, Any]) -> list[str]:
    bits = [
        clean_text(value, 260)
        for key, value in (visual_context.get("world") or {}).items()
        if key in {"setting", "tone", "locations"} and clean_text(value)
    ]
    for item in visual_context.get("world_state", [])[:8]:
        value = clean_text(item.get("value"), 240)
        if value:
            bits.append(value)
    for item in visual_context.get("scene_state", [])[:8]:
        key = clean_text(item.get("key"), 80).replace("_", " ")
        value = clean_text(item.get("value"), 240)
        if key and value:
            bits.append(f"{key}: {value}")
    return dedupe(bits)[:10]


def relevant_object_bits(visual_context: dict[str, Any], scene_text: str) -> list[str]:
    bits: list[str] = []
    lower_scene = scene_text.lower()
    for item in visual_context.get("objects", [])[:14]:
        name = clean_text(item.get("name") or item.get("object_key"), 120)
        value = clean_text(item.get("value"), 220)
        owner = clean_text(item.get("owner_character_name"), 100)
        if not name or not value:
            continue
        if name.lower() not in lower_scene and owner and owner.lower() not in lower_scene:
            continue
        owner_suffix = f"; owner {owner}" if owner else ""
        bits.append(f"{name}: {value}{owner_suffix}")
    return bits[:6]


def emotional_visual_bits(visual_context: dict[str, Any]) -> list[str]:
    bits: list[str] = []
    for line in str(visual_context.get("story_state") or "").splitlines():
        line = clean_text(line, 420)
        if not line.lower().startswith("emotional visual context:"):
            continue
        value = line.split(":", 1)[1] if ":" in line else line
        bits.extend(split_prompt_parts(value))
    return dedupe([sanitize_visual_fragment(bit, limit=220) for bit in bits if bit])[:3]


def scene_visual_units(scene_text: str) -> list[str]:
    text = clean_text(scene_text, 8000)
    if not text:
        return []
    sentences = [clean_text(part, 280) for part in re.split(r"(?<=[.!?])\s+", text) if clean_text(part)]
    units: list[str] = []
    for index, sentence in enumerate(sentences):
        units.append(sentence)
        if index + 1 < len(sentences):
            pair = clean_text(f"{sentence} {sentences[index + 1]}", 380)
            if word_count(pair) <= 72:
                units.append(pair)
    if units:
        return units[:80]
    paragraphs = [clean_text(part, 360) for part in re.split(r"\n\s*\n+", text) if clean_text(part)]
    return paragraphs or [scene_excerpt(text, 300)]


def visual_beat_score(unit: str, character_names: list[str]) -> int:
    normalized = normalize_name(unit)
    score = 0
    for hint in VISUAL_BEAT_POSITIVE_HINTS:
        if hint in normalized:
            score += 3
    for name in character_names:
        if name and re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", normalized):
            score += 4
    if '"' in unit or " said " in normalized or " asked " in normalized or "whisper" in normalized:
        score += 2
    if any(hint in normalized for hint in VISUAL_BEAT_FUTURE_HINTS):
        score -= 4
    if len(unit) < 50:
        score -= 1
    if len(unit) > 420:
        score -= 1
    return score


def select_visual_beat(scene_text: str, visual_context: dict[str, Any]) -> str:
    units = scene_visual_units(scene_text)
    if not units:
        return ""
    character_names = [
        normalize_name(character.get("name"))
        for character in visual_context.get("characters", [])
        if clean_text(character.get("name"))
    ]
    best = max(units, key=lambda unit: visual_beat_score(unit, character_names))
    return visual_beat_fragment(best, scene_text) or clean_text(best, 260)


def prompt_quality_warnings(
    *,
    prompt: str,
    visual_beat: str,
    characters_included: list[str],
    continuity_used: dict[str, list[str]],
    style_used: str,
    scene_text: str = "",
) -> list[str]:
    warnings: list[str] = []
    normalized_prompt = normalize_name(prompt)
    if not clean_text(visual_beat):
        warnings.append("No clear visual beat was selected.")
    if not characters_included:
        warnings.append("No named character was detected in the image prompt.")
    if not clean_text(style_used):
        warnings.append("No image style preset or visual direction was applied.")
    for name in characters_included:
        if name and name not in continuity_used:
            warnings.append(f"No continuity facts were recorded for {name}.")
    if looks_internal_prompt_text(prompt) or "node " in normalized_prompt or "positive:" in normalized_prompt or "negative:" in normalized_prompt:
        warnings.append("Prompt may contain workflow mapping/debug text.")
    if len(prompt) > 1500 or word_count(prompt) > 190:
        warnings.append("Prompt is longer than the recommended Z-Image Turbo prompt range.")
    fragments = [normalize_name(part) for part in re.split(r"[,;\n|]+", prompt) if clean_text(part)]
    repeated = len(fragments) - len(set(fragments))
    if repeated >= 2:
        warnings.append("Prompt contains repeated fragments.")
    if scene_text:
        for fragment in fragments:
            if len(fragment) > 180 and fragment in normalize_name(scene_text):
                warnings.append("Prompt may include a long raw scene excerpt.")
                break
    if not any(hint in normalized_prompt for hint in CONCRETE_VISUAL_SUBJECT_HINTS):
        warnings.append("Prompt may not have a concrete visual subject.")
    polished_flags = ("glamour lighting", "plastic skin", "clean fantasy poster", "poster composition")
    if any(flag in normalized_prompt for flag in polished_flags):
        warnings.append("Prompt contains polished poster/glamour language.")
    return dedupe(warnings)[:8]


def focused_visual_state_lines(
    *,
    included_characters: list[dict[str, Any]],
    object_lines: list[str],
    world_visual_lines: list[str],
    emotional_lines: list[str] | None = None,
) -> str:
    lines: list[str] = []
    for character in included_characters:
        name = clean_text(character.get("name"), 120)
        bits = character_visual_bits(character, include_lora=False)[:8]
        if name and bits:
            lines.append(f"{name}: {'; '.join(bits)}")
    if object_lines:
        lines.append("Objects: " + "; ".join(object_lines[:5]))
    if world_visual_lines:
        lines.append("World visual tone/state: " + "; ".join(world_visual_lines[:5]))
    if emotional_lines:
        lines.append("Visible emotional context, only if it fits the scene: " + "; ".join(emotional_lines[:3]))
    return "\n".join(lines)


def build_clean_positive_prompt(
    *,
    scene_text: str,
    visual_context: dict[str, Any],
    included_characters: list[dict[str, Any]],
    visual_beat: str,
    style_used: str,
    style_context: str = "",
) -> str:
    style_parts = style_prompt_fragments(style_used, style_context)
    beat = visual_beat_fragment(visual_beat, scene_text) or visual_beat_fragment(
        select_visual_beat(scene_text, visual_context),
        scene_text,
    )
    character_parts = [
        phrase
        for phrase in (character_prompt_phrase(character) for character in included_characters)
        if phrase
    ][:5]
    object_parts = [
        sanitize_visual_fragment(value, limit=180)
        for value in relevant_object_bits(visual_context, scene_text)
    ]
    world_parts = [
        sanitize_visual_fragment(value, limit=180)
        for value in visual_world_bits(visual_context)
    ]
    prompt = ", ".join(
        part
        for part in [
            *style_parts,
            beat,
            *character_parts,
            *[part for part in object_parts if part][:4],
            *[part for part in world_parts if part][:4],
        ]
        if part
    )
    return sanitize_positive_prompt(prompt, scene_text=scene_text)


def choose_characters_from_parsed(
    included_characters: list[dict[str, Any]],
    parsed_names: list[str] | None,
) -> list[dict[str, Any]]:
    names = {normalize_name(name) for name in (parsed_names or []) if clean_text(name)}
    if not names:
        return included_characters
    selected: list[dict[str, Any]] = []
    for character in included_characters:
        name = clean_text(character.get("name"))
        first = normalize_name(name.split(" ", 1)[0]) if name else ""
        if normalize_name(name) in names or first in names:
            selected.append(character)
    return selected or included_characters


def fallback_prompt(
    *,
    scene_text: str,
    negative_prompt: str | None,
    visual_context: dict[str, Any],
    workflow_name: str = "",
    workflow_notes: str = "",
    style_used: str = "",
    style_negative_prompt: str | None = None,
) -> dict[str, Any]:
    included_characters = selected_characters_for_scene(visual_context)
    visual_beat = select_visual_beat(scene_text, visual_context) or scene_excerpt(scene_text, limit=220)
    prompt = build_clean_positive_prompt(
        scene_text=scene_text,
        visual_context=visual_context,
        included_characters=included_characters,
        visual_beat=visual_beat,
        style_used=style_used,
        style_context=workflow_notes,
    )
    included_names = [clean_text(character.get("name")) for character in included_characters if clean_text(character.get("name"))]
    style_label = clean_text(style_used) or IMAGE_PROMPT_STYLE_PRESETS[DEFAULT_IMAGE_PROMPT_STYLE_PRESET_ID]["label"]
    continuity = continuity_for_characters(included_characters)
    result = {
        "visual_beat": visual_beat,
        "prompt": prompt,
        "negative_prompt": negative_prompt_for_characters(included_characters, negative_prompt, style_negative_prompt),
        "characters_included": included_names[:6],
        "continuity_used": continuity,
        "style_used": style_label,
        "confidence": 0.35,
    }
    result["quality_warnings"] = prompt_quality_warnings(
        prompt=result["prompt"],
        visual_beat=result["visual_beat"],
        characters_included=result["characters_included"],
        continuity_used=continuity,
        style_used=style_label,
        scene_text=scene_text,
    )
    return result


def parse_prompt_response(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    else:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None

    prompt = clean_text(str(data.get("prompt") or ""))
    negative = clean_text(str(data.get("negative_prompt") or data.get("negative") or ""))
    visual_beat = clean_text(str(data.get("visual_beat") or ""))
    style_used = clean_text(str(data.get("style_used") or ""))
    characters = data.get("characters_included") or []
    if not isinstance(characters, list):
        characters = []
    warnings = data.get("quality_warnings") or data.get("missing_continuity_warnings") or []
    if not isinstance(warnings, list):
        warnings = []
    continuity = data.get("continuity_used") or {}
    if not isinstance(continuity, dict):
        continuity = {}
    cleaned_continuity: dict[str, list[str]] = {}
    for key, values in continuity.items():
        name = clean_text(key, 120)
        if not name:
            continue
        if isinstance(values, list):
            cleaned_continuity[name] = [clean_text(item, 220) for item in values if clean_text(item)][:10]
        elif clean_text(values):
            cleaned_continuity[name] = [clean_text(values, 220)]
    try:
        confidence = float(data.get("confidence", 0.65))
    except (TypeError, ValueError):
        confidence = 0.65
    if not prompt:
        return None
    return {
        "visual_beat": visual_beat,
        "prompt": prompt,
        "negative_prompt": negative,
        "characters_included": [clean_text(str(item)) for item in characters if clean_text(str(item))][:8],
        "continuity_used": cleaned_continuity,
        "style_used": style_used,
        "quality_warnings": [clean_text(str(item), 220) for item in warnings if clean_text(str(item))][:8],
        "confidence": max(0.0, min(1.0, confidence)),
    }


async def resolve_model(client: LMStudioClient, selected_model: str) -> str:
    if selected_model.strip():
        return selected_model.strip()
    models = await client.list_models()
    first_model = next((model.get("id") for model in models if model.get("id")), None)
    if not first_model:
        raise RuntimeError("No model is selected and LM Studio did not return loaded models.")
    return first_model


def visual_context_source_hash(*, session_id: str, scene_text: str = "") -> str:
    context = load_visual_context(session_id, scene_text)
    payload = {
        "characters": [
            {
                "id": character.get("id"),
                "name": character.get("name"),
                "profile": character.get("visual_profile"),
                "image_prompt": character.get("image_prompt"),
                "lora_trigger": character.get("lora_trigger"),
                "live_visual_state": character.get("live_visual_state"),
                "likely_present": character.get("likely_present"),
            }
            for character in context.get("characters", [])
        ],
        "objects": context.get("objects", [])[:20],
        "scene_state": context.get("scene_state", [])[:20],
        "world": context.get("world", {}),
        "world_state": context.get("world_state", [])[:20],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()


async def generate_image_prompt(
    *,
    session_id: str,
    scene_text: str,
    workflow_name: str = "",
    workflow_notes: str = "",
    prompt_override: str | None = None,
    negative_prompt: str | None = None,
    style_used: str = "",
    style_negative_prompt: str | None = None,
) -> dict[str, Any]:
    visual_context = load_visual_context(session_id, scene_text)
    if prompt_override and prompt_override.strip():
        sanitized_prompt = sanitize_positive_prompt(prompt_override.strip(), scene_text=scene_text)
        style_label = clean_text(style_used) or IMAGE_PROMPT_STYLE_PRESETS[DEFAULT_IMAGE_PROMPT_STYLE_PRESET_ID]["label"]
        return {
            "visual_beat": select_visual_beat(scene_text, visual_context),
            "prompt": sanitized_prompt,
            "negative_prompt": sanitize_negative_prompt(negative_prompt or STRONG_DEFAULT_NEGATIVE_PROMPT),
            "characters_included": [],
            "continuity_used": {},
            "style_used": style_label,
            "quality_warnings": prompt_quality_warnings(
                prompt=sanitized_prompt,
                visual_beat=select_visual_beat(scene_text, visual_context),
                characters_included=[],
                continuity_used={},
                style_used=style_label,
                scene_text=scene_text,
            ),
        }

    included_characters = selected_characters_for_scene(visual_context)
    fallback = fallback_prompt(
        scene_text=scene_text,
        negative_prompt=negative_prompt,
        visual_context=visual_context,
        workflow_name=workflow_name,
        workflow_notes=workflow_notes,
        style_used=style_used,
        style_negative_prompt=style_negative_prompt,
    )

    model_settings, resolved_model = resolve_task_model_settings("image_prompt_generation")
    client = LMStudioClient(model_settings.lm_studio_url)

    included_lines: list[str] = []
    for character in included_characters:
        name = clean_text(character.get("name"))
        if not name:
            continue
        fields = [
            f"name: {name}",
            f"role: {clean_text(character.get('role'), 120)}",
            f"visual profile: {'; '.join(character_visual_bits(character, include_lora=False)[:10])}",
            f"plain prompt LoRA tokens: {'; '.join([clean_text(character.get('lora_trigger'), 120), clean_text((character.get('visual_profile') or {}).get('z_image_lora_trigger'), 120), clean_text((character.get('visual_profile') or {}).get('alternate_lora_triggers'), 220)]).strip('; ')}",
        ]
        included_lines.append("; ".join(field for field in fields if not field.endswith(": ")))

    available_names = [
        clean_text(character.get("name"))
        for character in visual_context.get("characters", [])
        if clean_text(character.get("name"))
    ]
    world_lines = [
        f"{field}: {clean_text(value, 260)}"
        for field, value in visual_context.get("world", {}).items()
        if clean_text(value)
    ]
    object_lines = relevant_object_bits(visual_context, scene_text)
    world_visual_lines = visual_world_bits(visual_context)
    emotional_lines = emotional_visual_bits(visual_context)
    story_state_lines = focused_visual_state_lines(
        included_characters=included_characters,
        object_lines=object_lines,
        world_visual_lines=world_visual_lines,
        emotional_lines=emotional_lines,
    )
    deterministic_visual_beat = select_visual_beat(scene_text, visual_context)
    style_label = clean_text(style_used) or IMAGE_PROMPT_STYLE_PRESETS[DEFAULT_IMAGE_PROMPT_STYLE_PRESET_ID]["label"]
    safe_style_context = ", ".join(style_prompt_fragments(style_label, workflow_notes))

    user_prompt = "\n".join(
        [
            "Choose the strongest single visual beat from the selected StoryDriver scene, then create a Z-Image Turbo-ready prompt for that exact beat.",
            "If the scene is dialogue, planning, tending wounds, or travel, illustrate that present moment. Do not jump ahead to an attack or future action.",
            "If the scene contains a clear visual peak, use the peak only if it actually happens in the selected scene.",
            "Prefer moments with characters, environment, objects, lighting/weather, and visible mood.",
            "Describe visible subjects, setting, lighting, weather, practical camera/framing, environment, and visible action.",
            "Use concise rich prompt fragments, not prose narration.",
            "Do not spoil beyond this scene. Do not invent extra major characters or events.",
            "Use the listed character visual profiles only when the character is present in the selected scene.",
            "Preserve current outfit, wounds, dirt/wetness, important objects, and location when supported by the scene or live state.",
            "Live state wins for temporary/current details. Base visual profile wins for stable identity.",
            "LoRA trigger text may be included as plain prompt tokens only; ComfyUI owns actual LoRA loading.",
            "Style target: gritty grounded fantasy realism, imperfect camera quality, slight grain, natural low-light or bad weather when appropriate, realistic faces and worn clothes.",
            "Avoid polished cinematic poster language unless the scene truly calls for it. Avoid glamour lighting, plastic skin, shiny clean clothing, generic AI fantasy armor, and modern objects not in the scene.",
            "Return strict JSON only with keys visual_beat, prompt, negative_prompt, characters_included, continuity_used, style_used, quality_warnings, confidence.",
            "continuity_used must map character names to the visual/live-state facts used in the prompt.",
            "quality_warnings should list missing or uncertain continuity facts, not workflow/debug details.",
            "confidence must be a number from 0.0 to 1.0.",
            "",
            "Task notes:",
            clean_text(resolved_model.notes, 1200) or "Use StoryDriver's default image prompt behavior.",
            "",
            f"Style preset/direction used: {style_label}",
            f"Clean style direction: {safe_style_context}",
            f"Suggested visual beat from deterministic scan: {deterministic_visual_beat or 'None'}",
            "",
            "Characters likely present in this scene:",
            "\n".join(included_lines) or "None detected. Use only characters visibly supported by scene text.",
            "",
            f"Other active character names for exclusion awareness: {', '.join(available_names) or 'None'}",
            "",
            "Automatic story state for visual continuity:",
            story_state_lines or "None",
            "",
            "Important visible objects if present in the scene:",
            "\n".join(object_lines) or "None",
            "",
            "World and scene visual state:",
            "\n".join(world_visual_lines) or "None",
            "",
            "World notes:",
            "\n".join(world_lines) or "None",
            "",
            "Scene text:",
            scene_text[:6000],
        ]
    )

    try:
        model = await resolve_model(client, model_settings.model)
        system_prompt = "You create concise visual prompts for local image generation. Return JSON only."
        if clean_text(resolved_model.notes, 1200):
            system_prompt = f"{system_prompt}\n\nTask notes:\n{clean_text(resolved_model.notes, 1200)}"
        result = await client.generate_scene_routed(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            parameters=task_parameters(
                resolved_model,
                {
                    "temperature": 0.5,
                    "top_p": 0.9,
                    "max_tokens": 520,
                },
                max_tokens_min=260,
                max_tokens_max=900,
            ),
            timeout=resolved_model.timeout_seconds,
            inference_backend=resolved_model.inference_backend,
            reasoning_mode=resolved_model.reasoning_mode,
            context_length=resolved_model.context_length,
            fallback_to_openai_compatible=resolved_model.fallback_to_openai_compatible,
        )
        raw = result["text"]
        parsed = parse_prompt_response(raw)
        if parsed and (parsed.get("prompt") or parsed.get("visual_beat")):
            parsed_characters = choose_characters_from_parsed(
                included_characters,
                parsed.get("characters_included") or [],
            )
            parsed_visual_beat = visual_beat_fragment(
                parsed.get("visual_beat") or deterministic_visual_beat,
                scene_text,
            ) or deterministic_visual_beat
            prompt = build_clean_positive_prompt(
                scene_text=scene_text,
                visual_context=visual_context,
                included_characters=parsed_characters,
                visual_beat=parsed_visual_beat,
                style_used=style_label,
                style_context=workflow_notes,
            )
            continuity = {
                **continuity_for_characters(parsed_characters),
                **(parsed.get("continuity_used") or {}),
            }
            parsed_style = clean_text(parsed.get("style_used")) or style_label
            parsed["prompt"] = prompt
            parsed["characters_included"] = [
                clean_text(character.get("name"))
                for character in parsed_characters
                if clean_text(character.get("name"))
            ][:6]
            parsed["continuity_used"] = {
                key: values for key, values in continuity.items() if key and values
            }
            parsed["negative_prompt"] = negative_prompt_for_characters(
                parsed_characters,
                negative_prompt if negative_prompt else parsed.get("negative_prompt"),
                style_negative_prompt,
            )
            parsed["style_used"] = parsed_style
            parsed["quality_warnings"] = dedupe(
                [
                    *(parsed.get("quality_warnings") or []),
                    *prompt_quality_warnings(
                        prompt=parsed["prompt"],
                        visual_beat=parsed_visual_beat,
                        characters_included=parsed.get("characters_included") or [],
                        continuity_used=parsed["continuity_used"],
                        style_used=parsed_style,
                        scene_text=scene_text,
                    ),
                ]
            )
            parsed["visual_beat"] = parsed_visual_beat
            return parsed
    except Exception:
        return fallback

    return fallback
