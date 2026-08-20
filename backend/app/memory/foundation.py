from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from hashlib import sha1, sha256
from typing import Any
from uuid import uuid4

from app.database import db_session
from app.generation.model_provider import LMStudioClient, model_client_for_settings
from app.generation.router import resolve_task_model_settings, task_parameters


FOUNDATION_SCHEMA_VERSION = "story_foundation_v4"
FOUNDATION_TASK_TYPE = "story_foundation_generation"
FOUNDATION_PROMPT_LIMIT = 5200
FOUNDATION_JSON_LIMIT = 24000
FOUNDATION_TASK_NOTE_CHARS = 500
FOUNDATION_GENERATION_TIMEOUT_CAP_SECONDS = 10.0

WORLD_FIELDS = ("setting", "tone", "rules", "locations", "factions", "conflicts", "history")
CHARACTER_FIELDS = (
    "name",
    "role",
    "personality",
    "appearance",
    "relationships",
    "current_state",
    "voice",
    "private_notes",
)
PROFILE_FIELDS = (
    "base_visual_description",
    "face_description",
    "hair",
    "body_build",
    "age_marker",
    "default_outfit",
    "distinctive_marks",
    "color_palette",
    "preferred_voice",
    "visual_consistency_notes",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def compact_text(value: Any, limit: int = 700) -> str:
    text = " ".join(str(value or "").replace("\r", " ").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].strip()


def clean_list(value: Any, *, limit: int = 10, item_limit: int = 260) -> list[str]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, str):
        items = re.split(r"\n+|;\s*|,\s+(?=\w)", value)
    elif isinstance(value, list):
        items = value
    else:
        items = [value]
    cleaned: list[str] = []
    for item in items:
        text = compact_text(item, item_limit)
        if text and text not in cleaned:
            cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def extract_json_object(text: str) -> dict[str, Any]:
    clean = (text or "").strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        parsed = json.loads(clean)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = clean.find("{")
    if start < 0:
        raise ValueError("No JSON object found.")
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(clean)):
        char = clean[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                parsed = json.loads(clean[start : index + 1])
                if isinstance(parsed, dict):
                    return parsed
                raise ValueError("Top-level JSON was not an object.")
    raise ValueError("No complete JSON object found.")


def json_loads_dict(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def json_loads_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def source_map_paths(source_map: dict[str, Any], key: str) -> set[str]:
    value = source_map.get(key)
    return {str(item) for item in value} if isinstance(value, list) else set()


def flatten_paths(value: Any, prefix: str = "") -> set[str]:
    if isinstance(value, dict):
        paths: set[str] = set()
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            paths |= flatten_paths(child, child_prefix)
        return paths
    if isinstance(value, list):
        paths = set()
        for index, child in enumerate(value):
            paths |= flatten_paths(child, f"{prefix}[{index}]")
        return paths or ({prefix} if prefix else set())
    return {prefix} if prefix else set()


def changed_leaf_paths(before: Any, after: Any, prefix: str = "") -> set[str]:
    if before == after:
        return set()
    if isinstance(before, dict) and isinstance(after, dict):
        paths: set[str] = set()
        for key in set(before) | set(after):
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            paths |= changed_leaf_paths(before.get(key), after.get(key), child_prefix)
        return paths
    if isinstance(before, list) and isinstance(after, list):
        paths = set()
        for index in range(max(len(before), len(after))):
            left = before[index] if index < len(before) else None
            right = after[index] if index < len(after) else None
            paths |= changed_leaf_paths(left, right, f"{prefix}[{index}]")
        return paths
    return {prefix} if prefix else set()


def path_is_protected(path: str, protected: set[str]) -> bool:
    return path in protected or any(path.startswith(f"{item}.") or path.startswith(f"{item}[") for item in protected)


def merge_preserving_paths(old: Any, new: Any, protected: set[str], prefix: str = "") -> Any:
    if prefix and path_is_protected(prefix, protected):
        return old
    if isinstance(old, dict) and isinstance(new, dict):
        merged = dict(new)
        for key, old_child in old.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            if key in new:
                merged[key] = merge_preserving_paths(old_child, new[key], protected, child_prefix)
            elif path_is_protected(child_prefix, protected):
                merged[key] = old_child
        return merged
    if isinstance(old, list) and isinstance(new, list):
        merged = list(new)
        for index, old_child in enumerate(old):
            child_prefix = f"{prefix}[{index}]"
            if index < len(merged):
                merged[index] = merge_preserving_paths(old_child, merged[index], protected, child_prefix)
        return merged
    return new


def detect_genre(director_note: str, world_notes: dict[str, Any] | None = None) -> tuple[str, str, str]:
    text = "\n".join([director_note or "", *(str((world_notes or {}).get(field) or "") for field in WORLD_FIELDS)]).lower()
    if re.search(r"\bsci[-\s]?fi|science fiction|starship|orbit|colony|android|cyber|space\b", text):
        return "Science fiction", "near-future or far-future story-specific era", "precise, practical, human under technological pressure"
    if re.search(r"\bfantasy|medieval|kingdom|castle|sword|village|tavern|dragon|mage|magic\b", text):
        return "Fantasy", "pre-industrial or story-specific secondary world", "grounded elevated, sensory, not fake archaic"
    if re.search(r"\bhorror|thriller|stalker|murder|haunted|dread\b", text):
        return "Horror/thriller", "contemporary unless the director note says otherwise", "tense, concrete, withholding information carefully"
    if re.search(r"\bromance|intimacy|lovers?|desire|chemistry\b", text):
        return "Romance/intimacy", "story-specific present", "character-psychological, direct, emotionally specific"
    if re.search(r"\bmodern|present[-\s]?day|contemporary|phone|apartment|car|office\b", text):
        return "Modern", "present day", "natural contemporary"
    return "Story-specific fiction", "as implied by the director note", "concrete, character-driven"


def desired_character_count(director_note: str, named_count: int) -> int:
    note = (director_note or "").lower()
    descriptor = r"(?:adult\s+|main\s+|major\s+|young\s+|older\s+|younger\s+|middle\s+)*"
    if re.search(rf"\b(?:three|3)\s+{descriptor}(?:sisters|women|girls|main characters|characters|friends)\b", note):
        return max(3, named_count)
    if re.search(rf"\b(?:two|2)\s+{descriptor}(?:sisters|women|men|main characters|characters|friends)\b", note):
        return max(2, named_count)
    if re.search(rf"\b(?:four|4)\s+{descriptor}(?:main characters|characters|friends|siblings)\b", note):
        return max(4, named_count)
    if re.search(rf"\b(?:one|1)\s+{descriptor}(?:man|woman|person|character|protagonist)\b", note):
        return max(1, named_count)
    if re.search(r"\b(?:man|woman|person|character|protagonist)\s+alone\b|\balone\s+(?:man|woman|person|character|protagonist)\b", note):
        return max(1, named_count)
    return max(named_count, 1)


def director_named_characters(director_note: str) -> list[str]:
    exclusions = {
        "Add",
        "Adult",
        "After",
        "Before",
        "Chapter",
        "Continue",
        "Cover",
        "Create",
        "Director",
        "Each",
        "End",
        "Establish",
        "Fantasy",
        "First",
        "Focus",
        "Introduce",
        "Keep",
        "Let",
        "Make",
        "Modern",
        "No",
        "Opening",
        "Regenerate",
        "Relationship",
        "Rewrite",
        "Revise",
        "Scene",
        "Sci",
        "Science",
        "Show",
        "Story",
        "The",
        "They",
        "Their",
        "Three",
        "Two",
        "Use",
        "Women",
        "Write",
        "Both",
    }
    names: list[str] = []
    note = director_note or ""
    between_match = re.search(
        r"\bbetween\s+([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)"
        r"(?:,\s*[^,.]{0,60},)?\s+and\s+([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)\b",
        note,
    )
    if between_match:
        names.extend([between_match.group(1), between_match.group(2)])
    for match in re.finditer(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?\b", note):
        name = match.group(0).strip()
        name = re.sub(r"^(?:Adult|Young|Older|Younger|Main|Major|Primary)\s+", "", name).strip()
        first = name.split()[0]
        if first in exclusions:
            continue
        sentence_start = max(note.rfind(".", 0, match.start()), note.rfind("!", 0, match.start()), note.rfind("?", 0, match.start())) + 1
        sentence_end_candidates = [position for position in (note.find(".", match.end()), note.find("!", match.end()), note.find("?", match.end())) if position >= 0]
        sentence_end = min(sentence_end_candidates) if sentence_end_candidates else len(note)
        sentence = note[sentence_start:sentence_end]
        relative_start = match.start() - sentence_start
        introduce_at = sentence.lower().find("introduce")
        after_name = note[match.end():match.end() + 90]
        first_pattern = re.escape(first)
        has_appositive = bool(re.match(r"\s*,\s+(?:an?|the|[A-Z][a-z]+['’]s)\b", after_name))
        has_character_action = bool(
            re.search(
                rf"\b{first_pattern}\b(?:'s)?\s+(?:wants|needs|holds|starts|waits|stands|sits|kneels|"
                r"is|was|has|works|argues|objects|asks|says|gives|takes|opens|keeps|begins|initiates|can|may|must)\b",
                note,
                re.I,
            )
        )
        introduced = introduce_at >= 0 and relative_start > introduce_at
        group_match = re.search(
            r"\b(adults?|women|men|people|characters?|mid-thirties|thirties)\b",
            sentence,
            re.I,
        )
        adult_group_context = (
            introduce_at < 0
            and group_match is not None
            and relative_start > group_match.start()
        )
        if not (introduced or adult_group_context or has_appositive or has_character_action):
            continue
        existing_index = next(
            (index for index, existing in enumerate(names) if existing.split()[0].casefold() == first.casefold()),
            None,
        )
        if existing_index is None:
            names.append(name)
        elif len(name.split()) > len(names[existing_index].split()):
            names[existing_index] = name
    return names[:8]


def note_window_for_name(director_note: str, name: str, radius: int = 90) -> str:
    note = director_note or ""
    index = note.lower().find(name.lower())
    if index < 0:
        return ""
    return note[max(0, index - radius) : min(len(note), index + len(name) + radius)]


def explicit_traits_for_name(director_note: str, name: str) -> dict[str, str]:
    window = note_window_for_name(director_note, name).lower()
    appearance: list[str] = []
    role: list[str] = []
    for trait in (
        "red-haired",
        "red haired",
        "black-haired",
        "black haired",
        "blonde",
        "scarred",
        "tall",
        "short",
        "older",
        "younger",
        "athletic",
    ):
        if trait in window:
            appearance.append(trait.replace(" haired", "-haired"))
    for job in ("mechanic", "doctor", "detective", "soldier", "pilot", "mage", "knight", "engineer", "teacher"):
        if re.search(rf"\b{job}\b", window):
            role.append(job)
    return {
        "appearance": ", ".join(dict.fromkeys(appearance)),
        "role": ", ".join(dict.fromkeys(role)),
    }


def generated_names_for_genre(genre: str, director_note: str = "") -> list[str]:
    genre_lower = genre.lower()
    note = director_note.lower()
    male_cast = bool(re.search(r"\b(?:one|a|the|young|older|medieval)\s+man\b|\bmale protagonist\b", note))
    female_cast = bool(re.search(r"\b(?:one|a|the|young|older|medieval)\s+woman\b|\bfemale protagonist\b", note))
    if "science" in genre_lower:
        if male_cast:
            return ["Elias Venn", "Jon Kepler", "Tarin Rook", "Soren Vale"]
        return ["Mara Venn", "Iris Kepler", "Talia Rook", "Sena Vale"]
    if "fantasy" in genre_lower:
        if male_cast:
            return ["Alden Voss", "Garran Thorn", "Rowan Vale", "Corin Wren"]
        if female_cast:
            return ["Elara Voss", "Mira Thorn", "Sable Wren", "Talia Vale"]
        return ["Elara Voss", "Mira Thorn", "Rowan Vale", "Sable Wren"]
    if "horror" in genre_lower or "thriller" in genre_lower:
        if male_cast:
            return ["Nolan Vale", "Jude Mercer", "Theo Calder", "Marek Holt"]
        return ["Nora Vale", "June Mercer", "Tess Calder", "Mara Holt"]
    if "romance" in genre_lower:
        if male_cast:
            return ["Ari Vale", "Leon Hart", "Marek Quinn", "Ira Lane"]
        return ["Ari Vale", "Lena Hart", "Mara Quinn", "Iris Lane"]
    if male_cast:
        return ["Elias Ward", "Jon Avery", "Theo Vale", "Calder Reed"]
    if female_cast:
        return ["Lena Ward", "June Avery", "Tessa Vale", "Iris Calder"]
    return ["Mara Ellis", "June Avery", "Tessa Vale", "Iris Calder"]


def character_seed(
    *,
    index: int,
    name: str,
    genre: str,
    director_note: str,
    related_names: list[str],
) -> dict[str, Any]:
    role_options = [
        "primary viewpoint anchor",
        "pragmatic pressure source",
        "emotionally guarded counterweight",
        "volatile catalyst",
    ]
    personality_options = [
        "observant, controlled, protective under stress",
        "direct, practical, impatient with evasions",
        "warm but private, notices emotional shifts quickly",
        "bold, restless, masks fear with humor",
    ]
    appearance_options = [
        "adult; steady posture, sharp eyes, practical clothes suited to the setting",
        "adult; compact build, expressive face, work-worn hands, no ornamental excess",
        "adult; composed bearing, distinct silhouette, careful grooming shaped by the world",
        "adult; restless movement, weathered clothing, visible tells when lying",
    ]
    voice_options = [
        "measured, dry, rarely wastes a word",
        "plainspoken, quick to challenge weak plans",
        "soft-spoken until a line is crossed",
        "fast, wry, deflects before admitting concern",
    ]
    traits = explicit_traits_for_name(director_note, name)
    is_sister_story = bool(re.search(r"\bsisters?\b", director_note or "", flags=re.IGNORECASE))
    relationship_lines = []
    for other in related_names:
        if other == name:
            continue
        relationship_lines.append(
            f"{other}: {'sister with shared history and different coping habits' if is_sister_story else 'important connection with active tension and loyalty'}"
        )
    role = traits["role"] or role_options[index % len(role_options)]
    appearance = "; ".join(
        item
        for item in [
            appearance_options[index % len(appearance_options)],
            traits["appearance"],
            "genre grounding: " + genre,
        ]
        if item
    )
    return {
        "name": name,
        "role": role,
        "age_marker": "adult",
        "appearance": appearance,
        "face": "distinct face and readable expression established on first introduction",
        "hair": traits["appearance"] if "haired" in traits["appearance"] else "",
        "body_build": "adult build specific enough to recognize in later scenes",
        "default_outfit": "practical outfit for the opening location and social role",
        "distinctive_marks": "",
        "personality": personality_options[index % len(personality_options)],
        "private_goal": "get through the opening pressure without losing agency or exposing the wrong vulnerability",
        "fear": "being forced into a role they did not choose",
        "agency": "makes choices from personal goals, not simple compliance with the plot",
        "voice": voice_options[index % len(voice_options)],
        "relationships": relationship_lines[:4],
        "introduction_need": "Introduce through action, visual grounding, voice, and a motive before relying on exposition.",
        "pronunciation": "",
        "source": "fallback",
    }


def foundation_from_manual_inputs(
    *,
    director_note: str,
    manual_characters: list[dict[str, Any]],
    world_notes: dict[str, Any] | None,
) -> dict[str, Any]:
    genre, time_period, tone = detect_genre(director_note, world_notes)
    named = director_named_characters(director_note)
    manual_names = [compact_text(character.get("name"), 120) for character in manual_characters if character.get("name")]
    names = [name for name in [*manual_names, *named] if name]
    for generated in generated_names_for_genre(genre, director_note):
        if len(names) >= desired_character_count(director_note, len(names)):
            break
        if generated not in names:
            names.append(generated)
    count = desired_character_count(director_note, len(names))
    names = names[:count]
    character_by_name = {str(character.get("name") or "").casefold(): character for character in manual_characters}
    characters: list[dict[str, Any]] = []
    for index, name in enumerate(names):
        manual = character_by_name.get(name.casefold())
        if manual:
            relationships = clean_list(manual.get("relationships"), limit=8)
            characters.append(
                {
                    "name": name,
                    "role": compact_text(manual.get("role") or "manual story character", 300),
                    "age_marker": "adult",
                    "appearance": compact_text(manual.get("appearance"), 700),
                    "face": "",
                    "hair": "",
                    "body_build": "",
                    "default_outfit": "",
                    "distinctive_marks": "",
                    "personality": compact_text(manual.get("personality"), 700),
                    "private_goal": "",
                    "fear": "",
                    "agency": "Manual card is authoritative; infer choices from role, personality, and relationships.",
                    "voice": compact_text(manual.get("voice"), 260),
                    "relationships": relationships,
                    "introduction_need": "If new to the prose, introduce through action, visual grounding, voice, and motive.",
                    "pronunciation": "",
                    "source": "manual",
                }
            )
        else:
            characters.append(
                character_seed(
                    index=index,
                    name=name,
                    genre=genre,
                    director_note=director_note,
                    related_names=names,
                )
            )
    is_sister_story = bool(re.search(r"\bsisters?\b", director_note or "", flags=re.IGNORECASE))
    relationships: list[dict[str, Any]] = []
    for left_index, left in enumerate(names):
        for right in names[left_index + 1 :]:
            relationships.append(
                {
                    "characters": [left, right],
                    "type": "family" if is_sister_story else "core cast",
                    "dynamic": "shared history with affection, friction, and different survival habits"
                    if is_sister_story
                    else "active bond with a useful mix of trust, pressure, and disagreement",
                    "tension": "what each person wants now may not align with what the other needs",
                    "source": "fallback",
                }
            )
    world = {
        "rules": clean_list((world_notes or {}).get("rules"), limit=6)
        or [f"World behavior must fit {genre.lower()} logic and explicit director constraints."],
        "locations": clean_list((world_notes or {}).get("locations"), limit=6)
        or ["Opening location should be concrete, navigable, and tied to immediate pressure."],
        "factions": clean_list((world_notes or {}).get("factions"), limit=6),
        "conflicts": clean_list((world_notes or {}).get("conflicts"), limit=6)
        or ["The opening conflict should pressure the main cast before the plot broadens."],
        "technology_magic": "No magic" if re.search(r"\bno magic|without magic\b", director_note or "", re.I) else "",
        "source": "manual" if world_notes and any(world_notes.get(field) for field in WORLD_FIELDS) else "fallback",
    }
    return {
        "overview": {
            "premise": compact_text(director_note, 520)
            or "A character-driven story opening with immediate pressure and durable continuity.",
            "genre": genre,
            "time_period": time_period,
            "tone": tone,
            "story_rules": clean_list(director_note, limit=4, item_limit=220),
        },
        "world": world,
        "characters": characters,
        "relationships": relationships[:10],
        "narrative_contract": {
            "viewpoint_default": "close third unless the director requests otherwise",
            "prose_priorities": [
                "character agency",
                "relationship pressure",
                "spatial continuity",
                "specific sensory detail that affects action or mood",
            ],
            "time_policy": "continuous scene time by default; do not compress hours or days unless the director asks",
        },
        "continuity_seed": {
            "initial_objects": [],
            "active_locations": ["the opening location and its practical zones"],
            "not_yet_events": [
                "future premise events are context, not automatic first-scene action",
                "backstory should pressure current choices instead of replacing the requested scene",
            ],
            "relationship_pressures": [
                "each main relationship should create a different choice, objection, or vulnerability",
            ],
            "secrets_or_hidden_information": [],
        },
        "opening_scope": {
            "start_location": "the first concrete location implied by the director note",
            "start_time": "the immediate story present",
            "initial_pressure": compact_text(director_note, 320)
            or "a pressure that forces character choice before exposition",
            "first_scene_jobs": [
                "establish where and when the story starts",
                "introduce the main characters distinctly",
                "make relationships visible through choices and subtext",
                "leave an actionable handoff for the next scene",
            ],
            "boundaries": [
                "do not skip to aftermath",
                "do not append a new story beat in versioned modes",
                "do not contradict explicit director constraints",
            ],
            "introduction_obligations": [
                "introduce present main characters through action, voice, visual grounding, current want, and relationship pressure",
            ],
            "must_not_occur_yet": [
                "do not jump to later action, battle, rescue, aftermath, or payoff unless explicitly requested",
                "do not turn future backstory into first-scene events by default",
            ],
            "time_skip_policy": "time skips are not allowed unless the director explicitly permits them",
        },
    }


def normalize_character(value: Any, index: int, director_note: str, genre: str, all_names: list[str]) -> dict[str, Any]:
    data = as_dict(value)
    name = compact_text(data.get("name"), 120) or generated_names_for_genre(genre, director_note)[index % 4]
    seeded = character_seed(index=index, name=name, genre=genre, director_note=director_note, related_names=all_names)
    relationships = data.get("relationships")
    if isinstance(relationships, list):
        rel_lines = [
            compact_text(item if not isinstance(item, dict) else "; ".join(f"{k}: {v}" for k, v in item.items()), 260)
            for item in relationships
        ]
    else:
        rel_lines = clean_list(relationships, limit=8)
    return {
        "name": name,
        "role": compact_text(data.get("role") or seeded["role"], 320),
        "age_marker": compact_text(data.get("age_marker") or data.get("age") or seeded["age_marker"], 120),
        "appearance": compact_text(data.get("appearance") or seeded["appearance"], 900),
        "face": compact_text(data.get("face") or data.get("face_description") or seeded["face"], 500),
        "hair": compact_text(data.get("hair") or seeded["hair"], 260),
        "body_build": compact_text(data.get("body_build") or data.get("build") or seeded["body_build"], 260),
        "default_outfit": compact_text(data.get("default_outfit") or data.get("outfit") or seeded["default_outfit"], 520),
        "distinctive_marks": compact_text(data.get("distinctive_marks") or data.get("marks") or "", 360),
        "personality": compact_text(data.get("personality") or seeded["personality"], 900),
        "private_goal": compact_text(data.get("private_goal") or data.get("goal") or seeded["private_goal"], 420),
        "fear": compact_text(data.get("fear") or seeded["fear"], 360),
        "agency": compact_text(data.get("agency") or seeded["agency"], 520),
        "voice": compact_text(data.get("voice") or seeded["voice"], 420),
        "relationships": [line for line in rel_lines if line][:8],
        "introduction_need": compact_text(data.get("introduction_need") or seeded["introduction_need"], 420),
        "pronunciation": compact_text(data.get("pronunciation") or data.get("spoken_name") or "", 160),
        "source": compact_text(data.get("source") or "model", 80),
    }


def normalize_foundation(
    raw: dict[str, Any],
    *,
    director_note: str,
    manual_characters: list[dict[str, Any]] | None = None,
    world_notes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fallback = foundation_from_manual_inputs(
        director_note=director_note,
        manual_characters=manual_characters or [],
        world_notes=world_notes,
    )
    source = as_dict(raw)
    overview_source = as_dict(source.get("overview") or source.get("story") or source)
    genre = compact_text(overview_source.get("genre") or fallback["overview"]["genre"], 160)
    raw_characters = source.get("characters") if isinstance(source.get("characters"), list) else fallback["characters"]
    explicit_names = [
        *[compact_text(character.get("name"), 120) for character in manual_characters or [] if character.get("name")],
        *director_named_characters(director_note),
    ]
    explicit_names = list(dict.fromkeys(name for name in explicit_names if name))
    closed_cast = bool(
        explicit_names
        and (
            re.search(r"\b(?:scene|story|chapter)\s+between\b", director_note or "", re.I)
            or re.search(r"\b(?:only|just)\s+(?:the\s+)?(?:two|three)\b", director_note or "", re.I)
            or re.search(r"\bno\s+(?:other|additional|new)\s+(?:people|persons|characters)\b", director_note or "", re.I)
        )
    )
    if closed_cast:
        allowed_first_names = {name.split()[0].casefold() for name in explicit_names}
        filtered = [
            item
            for item in raw_characters
            if compact_text(as_dict(item).get("name"), 120).split()[0].casefold() in allowed_first_names
        ]
        present = {
            compact_text(as_dict(item).get("name"), 120).split()[0].casefold()
            for item in filtered
            if compact_text(as_dict(item).get("name"), 120)
        }
        filtered.extend(
            item
            for item in fallback["characters"]
            if compact_text(as_dict(item).get("name"), 120).split()[0].casefold() not in present
        )
        raw_characters = filtered
        all_names = explicit_names
    else:
        all_names = [compact_text(as_dict(item).get("name"), 120) for item in raw_characters]
        if not all_names:
            all_names = [character["name"] for character in fallback["characters"]]
    characters = [
        normalize_character(item, index, director_note, genre, all_names)
        for index, item in enumerate(raw_characters[:8])
    ]
    if not characters:
        characters = fallback["characters"]
    world_source = as_dict(source.get("world") or source.get("world_foundation"))
    narrative_source = as_dict(source.get("narrative_contract") or source.get("prose_contract"))
    continuity_source = as_dict(source.get("continuity_seed") or source.get("continuity"))
    relationships_source = source.get("relationships") if isinstance(source.get("relationships"), list) else fallback["relationships"]
    relationships: list[dict[str, Any]] = []
    for item in relationships_source[:12]:
        row = as_dict(item)
        chars = row.get("characters")
        if not isinstance(chars, list):
            chars = [row.get("character_a") or row.get("left"), row.get("character_b") or row.get("right")]
        clean_chars = [compact_text(char, 120) for char in chars if compact_text(char, 120)]
        if len(clean_chars) < 2:
            continue
        if closed_cast and any(char.split()[0].casefold() not in allowed_first_names for char in clean_chars):
            continue
        relationships.append(
            {
                "characters": clean_chars[:3],
                "type": compact_text(row.get("type") or row.get("relationship_type") or "relationship", 160),
                "dynamic": compact_text(row.get("dynamic") or row.get("content") or "", 520),
                "tension": compact_text(row.get("tension") or "", 360),
                "source": compact_text(row.get("source") or "model", 80),
            }
        )
    opening_source = as_dict(source.get("opening_scope") or source.get("opening"))
    normalized = {
        "overview": {
            "premise": compact_text(overview_source.get("premise") or fallback["overview"]["premise"], 700),
            "genre": genre,
            "time_period": compact_text(overview_source.get("time_period") or fallback["overview"]["time_period"], 220),
            "tone": compact_text(overview_source.get("tone") or fallback["overview"]["tone"], 260),
            "story_rules": clean_list(overview_source.get("story_rules") or overview_source.get("rules") or fallback["overview"]["story_rules"], limit=8),
        },
        "world": {
            "rules": clean_list(world_source.get("rules") or fallback["world"]["rules"], limit=8),
            "locations": clean_list(world_source.get("locations") or fallback["world"]["locations"], limit=8),
            "factions": clean_list(world_source.get("factions") or fallback["world"]["factions"], limit=8),
            "conflicts": clean_list(world_source.get("conflicts") or fallback["world"]["conflicts"], limit=8),
            "technology_magic": compact_text(world_source.get("technology_magic") or fallback["world"]["technology_magic"], 520),
            "source": compact_text(world_source.get("source") or fallback["world"]["source"], 80),
        },
        "characters": characters[:8],
        "relationships": relationships[:12],
        "narrative_contract": {
            "viewpoint_default": compact_text(
                narrative_source.get("viewpoint_default") or fallback["narrative_contract"]["viewpoint_default"],
                180,
            ),
            "prose_priorities": clean_list(
                narrative_source.get("prose_priorities") or fallback["narrative_contract"]["prose_priorities"],
                limit=8,
            ),
            "time_policy": compact_text(narrative_source.get("time_policy") or fallback["narrative_contract"]["time_policy"], 260),
        },
        "continuity_seed": {
            "initial_objects": clean_list(continuity_source.get("initial_objects") or fallback["continuity_seed"]["initial_objects"], limit=8),
            "active_locations": clean_list(continuity_source.get("active_locations") or fallback["continuity_seed"]["active_locations"], limit=8),
            "not_yet_events": clean_list(continuity_source.get("not_yet_events") or fallback["continuity_seed"]["not_yet_events"], limit=8),
            "relationship_pressures": clean_list(
                continuity_source.get("relationship_pressures") or fallback["continuity_seed"]["relationship_pressures"],
                limit=8,
            ),
            "secrets_or_hidden_information": clean_list(
                continuity_source.get("secrets_or_hidden_information") or fallback["continuity_seed"]["secrets_or_hidden_information"],
                limit=8,
            ),
        },
        "opening_scope": {
            "start_location": compact_text(opening_source.get("start_location") or fallback["opening_scope"]["start_location"], 360),
            "start_time": compact_text(opening_source.get("start_time") or fallback["opening_scope"]["start_time"], 220),
            "initial_pressure": compact_text(opening_source.get("initial_pressure") or fallback["opening_scope"]["initial_pressure"], 420),
            "first_scene_jobs": clean_list(opening_source.get("first_scene_jobs") or fallback["opening_scope"]["first_scene_jobs"], limit=8),
            "boundaries": clean_list(opening_source.get("boundaries") or fallback["opening_scope"]["boundaries"], limit=8),
            "introduction_obligations": clean_list(
                opening_source.get("introduction_obligations") or fallback["opening_scope"]["introduction_obligations"],
                limit=8,
            ),
            "must_not_occur_yet": clean_list(opening_source.get("must_not_occur_yet") or fallback["opening_scope"]["must_not_occur_yet"], limit=8),
            "time_skip_policy": compact_text(opening_source.get("time_skip_policy") or fallback["opening_scope"]["time_skip_policy"], 260),
        },
    }
    if len(json.dumps(normalized, ensure_ascii=False)) > FOUNDATION_JSON_LIMIT:
        normalized["overview"]["premise"] = compact_text(normalized["overview"]["premise"], 420)
        normalized["characters"] = normalized["characters"][:6]
        normalized["relationships"] = normalized["relationships"][:8]
    return normalized


def load_attached_characters_for_foundation(session_id: str) -> list[dict[str, Any]]:
    with db_session() as db:
        rows = db.execute(
            """
            SELECT c.id, c.name, c.role, c.personality, c.appearance, c.relationships,
                   c.current_state, c.voice, c.private_notes, c.auto_created
            FROM session_characters sc
            JOIN characters c ON c.id = sc.character_id
            WHERE sc.session_id = ? AND sc.is_active = 1
            ORDER BY lower(c.name) ASC
            """,
            (session_id,),
        ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def load_world_notes_for_foundation(session_id: str) -> dict[str, Any]:
    with db_session() as db:
        row = db.execute(
            """
            SELECT setting, tone, rules, locations, factions, conflicts, history
            FROM world_notes
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
    return {key: row[key] for key in row.keys()} if row else {}


def build_foundation_prompt(
    *,
    director_note: str,
    manual_characters: list[dict[str, Any]],
    world_notes: dict[str, Any],
    writing_length: dict[str, Any] | None = None,
    task_notes: str = "",
) -> str:
    shape = foundation_from_manual_inputs(
        director_note=director_note,
        manual_characters=manual_characters,
        world_notes=world_notes,
    )
    compact_manual_characters = json.dumps(manual_characters[:6], ensure_ascii=False, separators=(",", ":"))
    compact_world_notes = json.dumps(world_notes, ensure_ascii=False, separators=(",", ":"))
    compact_shape = json.dumps(shape, ensure_ascii=False, separators=(",", ":"))
    explicit_names = director_named_characters(director_note)
    closed_cast_rule = (
        f"- Closed opening cast: use only {', '.join(explicit_names)}; do not invent another present or viewpoint character."
        if explicit_names
        and (
            re.search(r"\b(?:scene|story|chapter)\s+between\b", director_note or "", re.I)
            or re.search(r"\b(?:only|just)\s+(?:the\s+)?(?:two|three)\b", director_note or "", re.I)
            or re.search(r"\bno\s+(?:other|additional|new)\s+(?:people|persons|characters)\b", director_note or "", re.I)
        )
        else ""
    )
    return "\n".join(
        [
            "Create a compact StoryDriver story foundation and character bible before the first scene.",
            compact_text(task_notes, FOUNDATION_TASK_NOTE_CHARS),
            "",
            "Rules:",
            "- Return strict JSON only. No markdown, prose scene, analysis, or commentary.",
            "- Director note, existing manual character cards, and manual world notes are authoritative.",
            "- Fill only stable setup needed for distinct characters, relationships, world rules, opening scope, and continuity.",
            "- Keep every field concise; no long biographies or lore dumps.",
            "- Separate base identity from live Story State.",
            closed_cast_rule,
            "",
            f"Target length mode: {compact_text((writing_length or {}).get('label') or '', 80)}",
            "",
            "Existing manual character cards:",
            compact_manual_characters,
            "",
            "Existing manual world notes:",
            compact_world_notes,
            "",
            "Director note:",
            director_note.strip(),
            "",
            "Required JSON shape:",
            compact_shape,
        ]
    )


async def generate_foundation_payload(
    *,
    session_id: str,
    director_note: str,
    writing_length: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manual_characters = load_attached_characters_for_foundation(session_id)
    world_notes = load_world_notes_for_foundation(session_id)
    model_settings, resolved = resolve_task_model_settings(FOUNDATION_TASK_TYPE)
    client = model_client_for_settings(model_settings)
    model = model_settings.model.strip()
    if not model:
        models = await client.list_models()
        model = next((item.get("id") for item in models if item.get("id")), "")
    raw_text = ""
    prompt = ""
    effective_timeout = min(float(resolved.timeout_seconds or 30.0), FOUNDATION_GENERATION_TIMEOUT_CAP_SECONDS)
    try:
        if not model:
            raise RuntimeError("No LM Studio model selected or loaded for story foundation.")
        prompt = build_foundation_prompt(
            director_note=director_note,
            manual_characters=manual_characters,
            world_notes=world_notes,
            writing_length=writing_length,
            task_notes=resolved.notes,
        )
        result = await client.generate_scene_routed(
            model=model,
            system_prompt="You create compact structured StoryDriver story foundations. Return strict JSON only.",
            user_prompt=prompt,
            parameters=task_parameters(
                resolved,
                {
                    "temperature": 0.25,
                    "top_p": 0.85,
                    "max_tokens": 620,
                },
                max_tokens_min=400,
                max_tokens_max=760,
            ),
            timeout=effective_timeout,
            inference_backend=resolved.inference_backend,
            reasoning_mode=resolved.reasoning_mode,
            context_length=resolved.context_length,
            fallback_to_openai_compatible=resolved.fallback_to_openai_compatible,
        )
        raw_text = str(result.get("text") or "")
        parsed = extract_json_object(raw_text)
        return normalize_foundation(
            parsed,
            director_note=director_note,
            manual_characters=manual_characters,
            world_notes=world_notes,
        ), {
            "status": "generated",
            "model": model,
            "task_type": FOUNDATION_TASK_TYPE,
            "raw_response_chars": len(raw_text),
            "prompt_chars": len(prompt),
            "fallback_used": False,
            "configured_timeout_seconds": resolved.timeout_seconds,
            "effective_timeout_seconds": effective_timeout,
            "error": None,
        }
    except Exception as error:
        fallback = foundation_from_manual_inputs(
            director_note=director_note,
            manual_characters=manual_characters,
            world_notes=world_notes,
        )
        return normalize_foundation(
            fallback,
            director_note=director_note,
            manual_characters=manual_characters,
            world_notes=world_notes,
        ), {
            "status": "fallback",
            "model": model,
            "task_type": FOUNDATION_TASK_TYPE,
            "raw_response_chars": len(raw_text),
            "prompt_chars": len(prompt),
            "fallback_used": True,
            "configured_timeout_seconds": resolved.timeout_seconds,
            "effective_timeout_seconds": effective_timeout,
            "error": str(error),
        }


def foundation_row_to_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    foundation = json_loads_dict(row["foundation_json"])
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "schema_version": row["schema_version"],
        "status": row["status"],
        "source_director_note": row["source_director_note"],
        "foundation": foundation,
        "source_map": json_loads_dict(row["source_map_json"]),
        "locked_paths": json_loads_list(row["locked_paths_json"]),
        "prompt_projection": render_foundation_projection(foundation),
        "generation_model": row["generation_model"],
        "generation_task_type": row["generation_task_type"],
        "generation_error": row["generation_error"],
        "foundation_revision": int(row["foundation_revision"] or 1)
        if "foundation_revision" in row.keys()
        else 1,
        "source_kind": row["source_kind"] if "source_kind" in row.keys() else "unknown",
        "source_generation_id": (
            row["source_generation_id"] if "source_generation_id" in row.keys() else ""
        ),
        "source_opening_note_checksum": (
            row["source_opening_note_checksum"]
            if "source_opening_note_checksum" in row.keys()
            else ""
        ),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "refreshed_at": row["refreshed_at"],
    }


def load_story_foundation_row(session_id: str) -> Any:
    with db_session() as db:
        return db.execute(
            """
            SELECT *
            FROM story_foundations
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()


def get_story_foundation(session_id: str) -> dict[str, Any] | None:
    return foundation_row_to_dict(load_story_foundation_row(session_id))


def save_story_foundation(
    *,
    session_id: str,
    foundation: dict[str, Any],
    source_director_note: str,
    source_map: dict[str, Any] | None = None,
    locked_paths: list[str] | None = None,
    status: str = "ready",
    generation_model: str = "",
    generation_task_type: str = FOUNDATION_TASK_TYPE,
    generation_error: str | None = None,
    source_kind: str = "unknown",
    source_generation_id: str = "",
) -> dict[str, Any]:
    row_id = str(uuid4())
    foundation_json = json.dumps(foundation, ensure_ascii=False)
    source_map_json = json.dumps(source_map or {}, ensure_ascii=False)
    locked_paths_json = json.dumps(locked_paths or [], ensure_ascii=False)
    now = utc_now()
    with db_session() as db:
        existing = db.execute(
            "SELECT id, foundation_revision FROM story_foundations WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        foundation_revision = int(existing["foundation_revision"] or 1) + 1 if existing else 1
        source_opening_note_checksum = sha256(source_director_note.encode("utf-8")).hexdigest()
        if existing:
            db.execute(
                """
                UPDATE story_foundations
                SET schema_version = ?, status = ?, source_director_note = ?,
                    foundation_json = ?, source_map_json = ?, locked_paths_json = ?,
                    generation_model = ?, generation_task_type = ?, generation_error = ?,
                    foundation_revision = ?, source_kind = ?, source_generation_id = ?,
                    source_opening_note_checksum = ?, refreshed_at = ?
                WHERE session_id = ?
                """,
                (
                    FOUNDATION_SCHEMA_VERSION,
                    status,
                    source_director_note,
                    foundation_json,
                    source_map_json,
                    locked_paths_json,
                    generation_model,
                    generation_task_type,
                    generation_error,
                    foundation_revision,
                    source_kind,
                    source_generation_id,
                    source_opening_note_checksum,
                    now,
                    session_id,
                ),
            )
        else:
            db.execute(
                """
                INSERT INTO story_foundations (
                    id, session_id, schema_version, status, source_director_note,
                    foundation_json, source_map_json, locked_paths_json,
                    generation_model, generation_task_type, generation_error, refreshed_at
                    , foundation_revision, source_kind, source_generation_id, source_opening_note_checksum
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    session_id,
                    FOUNDATION_SCHEMA_VERSION,
                    status,
                    source_director_note,
                    foundation_json,
                    source_map_json,
                    locked_paths_json,
                    generation_model,
                    generation_task_type,
                    generation_error,
                    now,
                    foundation_revision,
                    source_kind,
                    source_generation_id,
                    source_opening_note_checksum,
                ),
            )
    result = get_story_foundation(session_id)
    if result is None:
        raise RuntimeError("Story foundation could not be loaded after save.")
    return result


def join_parts(parts: list[str], separator: str = "; ") -> str:
    return separator.join(part for part in parts if compact_text(part))


def character_relationship_text(character: dict[str, Any], foundation: dict[str, Any]) -> str:
    lines = list(character.get("relationships") or [])
    name = compact_text(character.get("name"), 120)
    for relation in foundation.get("relationships") or []:
        row = as_dict(relation)
        chars = row.get("characters") if isinstance(row.get("characters"), list) else []
        if name and name in chars:
            others = [item for item in chars if item != name]
            lines.append(
                join_parts(
                    [
                        "/".join(others),
                        row.get("type"),
                        row.get("dynamic"),
                        row.get("tension"),
                    ],
                    " - ",
                )
            )
    return "\n".join(dict.fromkeys(compact_text(line, 400) for line in lines if compact_text(line)))


def character_card_from_foundation(character: dict[str, Any], foundation: dict[str, Any]) -> dict[str, str]:
    return {
        "name": compact_text(character.get("name"), 120),
        "role": compact_text(character.get("role"), 1200),
        "personality": join_parts(
            [
                compact_text(character.get("personality"), 800),
                f"Goal: {compact_text(character.get('private_goal'), 360)}" if character.get("private_goal") else "",
                f"Fear: {compact_text(character.get('fear'), 300)}" if character.get("fear") else "",
                f"Agency: {compact_text(character.get('agency'), 360)}" if character.get("agency") else "",
            ]
        ),
        "appearance": join_parts(
            [
                compact_text(character.get("age_marker"), 120),
                compact_text(character.get("appearance"), 800),
                f"Face: {compact_text(character.get('face'), 320)}" if character.get("face") else "",
                f"Hair: {compact_text(character.get('hair'), 220)}" if character.get("hair") else "",
                f"Build: {compact_text(character.get('body_build'), 220)}" if character.get("body_build") else "",
                f"Outfit: {compact_text(character.get('default_outfit'), 360)}" if character.get("default_outfit") else "",
                f"Marks: {compact_text(character.get('distinctive_marks'), 240)}" if character.get("distinctive_marks") else "",
            ]
        ),
        "relationships": character_relationship_text(character, foundation),
        "current_state": "",
        "voice": compact_text(character.get("voice"), 800),
        "private_notes": "Model-created story foundation detail. Manual edits and live Story State override generated suggestions.",
    }


def visual_profile_from_foundation(character: dict[str, Any]) -> dict[str, str | int | float]:
    return {
        "base_visual_description": compact_text(character.get("appearance"), 1200),
        "face_description": compact_text(character.get("face"), 800),
        "hair": compact_text(character.get("hair"), 400),
        "body_build": compact_text(character.get("body_build"), 400),
        "age_marker": compact_text(character.get("age_marker") or "adult", 240),
        "default_outfit": compact_text(character.get("default_outfit"), 800),
        "distinctive_marks": compact_text(character.get("distinctive_marks"), 800),
        "color_palette": "",
        "preferred_voice": compact_text(character.get("voice"), 400),
        "visual_consistency_notes": compact_text(character.get("introduction_need"), 900),
        "used_in_image_prompts": 1,
        "auto_created_confidence": 0.72,
    }


def update_empty_character_fields(db: Any, character_id: str, card: dict[str, str]) -> list[str]:
    row = db.execute(
        """
        SELECT role, personality, appearance, relationships, current_state, voice, private_notes
        FROM characters
        WHERE id = ?
        """,
        (character_id,),
    ).fetchone()
    if row is None:
        return []
    updates: dict[str, str] = {}
    for field in ("role", "personality", "appearance", "relationships", "current_state", "voice", "private_notes"):
        if compact_text(row[field]):
            continue
        value = compact_text(card.get(field), 12000)
        if value:
            updates[field] = value
    if updates:
        assignments = ", ".join(f"{field} = ?" for field in updates)
        db.execute(f"UPDATE characters SET {assignments} WHERE id = ?", (*updates.values(), character_id))
    return list(updates)


def update_empty_visual_profile_fields(db: Any, character_id: str, profile: dict[str, Any]) -> list[str]:
    existing = db.execute("SELECT * FROM character_visual_profiles WHERE character_id = ?", (character_id,)).fetchone()
    if existing is None:
        db.execute(
            """
            INSERT INTO character_visual_profiles (
                character_id, base_visual_description, face_description, hair, body_build,
                age_marker, default_outfit, distinctive_marks, color_palette, preferred_voice,
                visual_consistency_notes, used_in_image_prompts, auto_created_confidence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                character_id,
                profile["base_visual_description"],
                profile["face_description"],
                profile["hair"],
                profile["body_build"],
                profile["age_marker"],
                profile["default_outfit"],
                profile["distinctive_marks"],
                profile["color_palette"],
                profile["preferred_voice"],
                profile["visual_consistency_notes"],
                1,
                profile["auto_created_confidence"],
            ),
        )
        return [field for field in PROFILE_FIELDS if compact_text(profile.get(field))]
    updates: dict[str, Any] = {}
    for field in PROFILE_FIELDS:
        if compact_text(existing[field]):
            continue
        value = profile.get(field)
        if compact_text(value):
            updates[field] = value
    if updates:
        assignments = ", ".join(f"{field} = ?" for field in updates)
        db.execute(f"UPDATE character_visual_profiles SET {assignments} WHERE character_id = ?", (*updates.values(), character_id))
    return list(updates)


def ensure_world_notes_row(db: Any, session_id: str) -> Any:
    row = db.execute("SELECT * FROM world_notes WHERE session_id = ?", (session_id,)).fetchone()
    if row is None:
        db.execute("INSERT INTO world_notes (id, session_id) VALUES (?, ?)", (str(uuid4()), session_id))
        row = db.execute("SELECT * FROM world_notes WHERE session_id = ?", (session_id,)).fetchone()
    return row


def apply_foundation_to_story(session_id: str, foundation: dict[str, Any]) -> dict[str, Any]:
    applied = {
        "created_characters": [],
        "updated_characters": [],
        "updated_visual_profiles": [],
        "updated_world_fields": [],
    }
    with db_session() as db:
        existing_rows = db.execute(
            """
            SELECT c.id, c.name
            FROM session_characters sc
            JOIN characters c ON c.id = sc.character_id
            WHERE sc.session_id = ?
            """,
            (session_id,),
        ).fetchall()
        attached_by_name = {str(row["name"]).casefold(): row["id"] for row in existing_rows}
        for character in foundation.get("characters") or []:
            if not isinstance(character, dict):
                continue
            card = character_card_from_foundation(character, foundation)
            name = card["name"]
            if not name:
                continue
            profile = visual_profile_from_foundation(character)
            character_id = attached_by_name.get(name.casefold())
            if character_id:
                changed = update_empty_character_fields(db, character_id, card)
                profile_changed = update_empty_visual_profile_fields(db, character_id, profile)
                if changed:
                    applied["updated_characters"].append({"id": character_id, "name": name, "fields": changed})
                if profile_changed:
                    applied["updated_visual_profiles"].append({"id": character_id, "name": name, "fields": profile_changed})
                continue
            character_id = str(uuid4())
            db.execute(
                """
                INSERT INTO characters (
                    id, name, role, personality, appearance, relationships,
                    current_state, voice, private_notes, auto_created
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    character_id,
                    name,
                    card["role"],
                    card["personality"],
                    card["appearance"],
                    card["relationships"],
                    card["current_state"],
                    card["voice"],
                    card["private_notes"],
                ),
            )
            db.execute(
                """
                INSERT INTO session_characters (id, session_id, character_id, is_active)
                VALUES (?, ?, ?, 1)
                """,
                (str(uuid4()), session_id, character_id),
            )
            update_empty_visual_profile_fields(db, character_id, profile)
            attached_by_name[name.casefold()] = character_id
            applied["created_characters"].append({"id": character_id, "name": name})

        world = as_dict(foundation.get("world"))
        overview = as_dict(foundation.get("overview"))
        opening = as_dict(foundation.get("opening_scope"))
        world_values = {
            "setting": join_parts(
                [
                    overview.get("premise"),
                    f"Time: {overview.get('time_period')}" if overview.get("time_period") else "",
                    f"Opening: {opening.get('start_location')}" if opening.get("start_location") else "",
                ],
                "\n",
            ),
            "tone": join_parts([overview.get("genre"), overview.get("tone")]),
            "rules": "\n".join([*clean_list(overview.get("story_rules"), limit=8), *clean_list(world.get("rules"), limit=8), compact_text(world.get("technology_magic"), 520)]).strip(),
            "locations": "\n".join(clean_list(world.get("locations"), limit=10)),
            "factions": "\n".join(clean_list(world.get("factions"), limit=10)),
            "conflicts": "\n".join([*clean_list(world.get("conflicts"), limit=8), compact_text(opening.get("initial_pressure"), 420)]).strip(),
            "history": "",
        }
        row = ensure_world_notes_row(db, session_id)
        updates: dict[str, str] = {}
        for field in WORLD_FIELDS:
            if compact_text(row[field]):
                continue
            value = compact_text(world_values.get(field), 12000)
            if value:
                updates[field] = value
        if updates:
            assignments = ", ".join(f"{field} = ?" for field in updates)
            db.execute(f"UPDATE world_notes SET {assignments} WHERE session_id = ?", (*updates.values(), session_id))
            applied["updated_world_fields"] = list(updates)
    return applied


async def ensure_story_foundation(
    *,
    session_id: str,
    director_note: str,
    writing_length: dict[str, Any] | None = None,
    force_refresh: bool = False,
    apply_to_cards: bool = True,
) -> dict[str, Any]:
    existing = get_story_foundation(session_id)
    if existing and not force_refresh:
        applied = apply_foundation_to_story(session_id, existing["foundation"]) if apply_to_cards else {}
        return {"status": "existing", "foundation": existing, "applied": applied, "generation": {"fallback_used": False}}

    source_generation_id = str(uuid4())
    source_director_note = director_note or (existing or {}).get("source_director_note", "")
    starting_revision = int((existing or {}).get("foundation_revision") or 0)
    foundation, metadata = await generate_foundation_payload(
        session_id=session_id,
        director_note=source_director_note,
        writing_length=writing_length,
    )
    with db_session() as db:
        session_exists = db.execute(
            "SELECT 1 FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    if session_exists is None:
        return {
            "status": "discarded_deleted_story",
            "foundation": None,
            "applied": {},
            "generation": {**metadata, "source_generation_id": source_generation_id},
        }

    latest = get_story_foundation(session_id)
    latest_revision = int((latest or {}).get("foundation_revision") or 0)
    if latest_revision != starting_revision:
        return {
            "status": "discarded_stale_generation",
            "foundation": latest,
            "applied": {},
            "generation": {**metadata, "source_generation_id": source_generation_id},
        }

    source_kind = "fallback" if metadata.get("fallback_used") else "model"
    source_paths_key = "fallback_paths" if source_kind == "fallback" else "model_paths"
    source_map = {source_paths_key: sorted(flatten_paths(foundation)), "manual_paths": []}
    locked_paths = list((existing or {}).get("locked_paths") or [])
    if existing:
        protected = set(locked_paths) | source_map_paths(existing.get("source_map") or {}, "manual_paths")
        foundation = merge_preserving_paths(existing["foundation"], foundation, protected)
        source_map = {
            source_paths_key: sorted(flatten_paths(foundation) - protected),
            "manual_paths": sorted(protected),
        }
    saved = save_story_foundation(
        session_id=session_id,
        foundation=foundation,
        source_director_note=source_director_note,
        source_map=source_map,
        locked_paths=locked_paths,
        status="fallback" if source_kind == "fallback" else "ready",
        generation_model=metadata.get("model") or "",
        generation_task_type=metadata.get("task_type") or FOUNDATION_TASK_TYPE,
        generation_error=metadata.get("error"),
        source_kind=source_kind,
        source_generation_id=source_generation_id,
    )
    applied = apply_foundation_to_story(session_id, foundation) if apply_to_cards else {}
    return {
        "status": "created" if not existing else "refreshed",
        "foundation": saved,
        "applied": applied,
        "generation": {**metadata, "source_generation_id": source_generation_id},
    }


def update_story_foundation(
    *,
    session_id: str,
    foundation: dict[str, Any] | None = None,
    locked_paths: list[str] | None = None,
    apply_to_cards: bool = True,
) -> dict[str, Any]:
    existing = get_story_foundation(session_id)
    if existing is None:
        base = foundation_from_manual_inputs(
            director_note="",
            manual_characters=load_attached_characters_for_foundation(session_id),
            world_notes=load_world_notes_for_foundation(session_id),
        )
        existing = save_story_foundation(
            session_id=session_id,
            foundation=base,
            source_director_note="",
            source_map={"model_paths": sorted(flatten_paths(base)), "manual_paths": []},
            locked_paths=[],
            status="draft",
            source_kind="manual",
            source_generation_id=str(uuid4()),
        )
    previous = existing["foundation"]
    next_foundation = previous
    if foundation is not None:
        next_foundation = normalize_foundation(
            foundation,
            director_note=existing.get("source_director_note") or "",
            manual_characters=load_attached_characters_for_foundation(session_id),
            world_notes=load_world_notes_for_foundation(session_id),
        )
    manual_paths = source_map_paths(existing.get("source_map") or {}, "manual_paths")
    if foundation is not None:
        manual_paths |= changed_leaf_paths(previous, next_foundation)
    next_locked = locked_paths if locked_paths is not None else existing.get("locked_paths", [])
    source_map = {
        "manual_paths": sorted(manual_paths),
        "model_paths": sorted(flatten_paths(next_foundation) - manual_paths),
    }
    saved = save_story_foundation(
        session_id=session_id,
        foundation=next_foundation,
        source_director_note=existing.get("source_director_note") or "",
        source_map=source_map,
        locked_paths=next_locked,
        status="ready",
        generation_model=existing.get("generation_model") or "",
        generation_task_type=existing.get("generation_task_type") or FOUNDATION_TASK_TYPE,
        generation_error=existing.get("generation_error"),
        source_kind="manual",
        source_generation_id=str(uuid4()),
    )
    applied = apply_foundation_to_story(session_id, next_foundation) if apply_to_cards else {}
    return {"foundation": saved, "applied": applied}


def render_foundation_projection(foundation: dict[str, Any] | None, limit: int = FOUNDATION_PROMPT_LIMIT) -> str:
    data = as_dict(foundation)
    if not data:
        return ""
    overview = as_dict(data.get("overview"))
    world = as_dict(data.get("world"))
    opening = as_dict(data.get("opening_scope"))
    narrative = as_dict(data.get("narrative_contract"))
    continuity = as_dict(data.get("continuity_seed"))
    lines = [
        "STORY FOUNDATION / CHARACTER BIBLE:",
        "Use this as durable base identity and world setup. Manual edits override generated suggestions; live Story State tracks temporary changes.",
    ]
    if overview:
        lines.append(
            "Overview: "
            + join_parts(
                [
                    f"Premise: {overview.get('premise')}" if overview.get("premise") else "",
                    f"Genre: {overview.get('genre')}" if overview.get("genre") else "",
                    f"Time: {overview.get('time_period')}" if overview.get("time_period") else "",
                    f"Tone: {overview.get('tone')}" if overview.get("tone") else "",
                ]
            )
        )
        for rule in clean_list(overview.get("story_rules"), limit=6):
            lines.append(f"- Rule: {rule}")
    character_lines: list[str] = []
    for character in data.get("characters") or []:
        if not isinstance(character, dict) or not compact_text(character.get("name")):
            continue
        character_lines.append(
            "- "
            + join_parts(
                [
                    compact_text(character.get("name"), 120),
                    compact_text(character.get("role"), 180),
                    compact_text(character.get("age_marker"), 80),
                    f"Appearance: {compact_text(character.get('appearance'), 260)}" if character.get("appearance") else "",
                    f"Personality: {compact_text(character.get('personality'), 240)}" if character.get("personality") else "",
                    f"Goal: {compact_text(character.get('private_goal'), 180)}" if character.get("private_goal") else "",
                    f"Voice: {compact_text(character.get('voice'), 160)}" if character.get("voice") else "",
                    f"Intro: {compact_text(character.get('introduction_need'), 180)}" if character.get("introduction_need") else "",
                ]
            )
        )
    if character_lines:
        lines.extend(["Main characters:", *character_lines[:8]])
    relationship_lines = []
    for relation in data.get("relationships") or []:
        row = as_dict(relation)
        chars = " / ".join(clean_list(row.get("characters"), limit=3, item_limit=80))
        text = join_parts([chars, row.get("type"), row.get("dynamic"), row.get("tension")], " - ")
        if text:
            relationship_lines.append(f"- {text}")
    if relationship_lines:
        lines.extend(["Relationships:", *relationship_lines[:8]])
    world_lines = [
        *(f"- Rule: {item}" for item in clean_list(world.get("rules"), limit=6)),
        *(f"- Location: {item}" for item in clean_list(world.get("locations"), limit=6)),
        *(f"- Conflict: {item}" for item in clean_list(world.get("conflicts"), limit=6)),
    ]
    if world.get("technology_magic"):
        world_lines.append(f"- Technology/magic: {compact_text(world.get('technology_magic'), 360)}")
    if world_lines:
        lines.extend(["World foundation:", *world_lines[:12]])
    narrative_lines = [
        f"- Viewpoint default: {narrative.get('viewpoint_default')}" if narrative.get("viewpoint_default") else "",
        f"- Time policy: {narrative.get('time_policy')}" if narrative.get("time_policy") else "",
        *(f"- Prose priority: {item}" for item in clean_list(narrative.get("prose_priorities"), limit=4)),
    ]
    narrative_lines = [compact_text(line, 260) for line in narrative_lines if compact_text(line)]
    if narrative_lines:
        lines.extend(["Narrative contract:", *narrative_lines[:6]])
    continuity_lines = [
        *(f"- Initial object: {item}" for item in clean_list(continuity.get("initial_objects"), limit=4)),
        *(f"- Active location: {item}" for item in clean_list(continuity.get("active_locations"), limit=4)),
        *(f"- Not yet: {item}" for item in clean_list(continuity.get("not_yet_events"), limit=4)),
        *(f"- Relationship pressure: {item}" for item in clean_list(continuity.get("relationship_pressures"), limit=4)),
        *(f"- Hidden info: {item}" for item in clean_list(continuity.get("secrets_or_hidden_information"), limit=3)),
    ]
    continuity_lines = [compact_text(line, 260) for line in continuity_lines if compact_text(line)]
    if continuity_lines:
        lines.extend(["Continuity seed:", *continuity_lines[:8]])
    opening_lines = [
        f"Start location: {opening.get('start_location')}" if opening.get("start_location") else "",
        f"Start time: {opening.get('start_time')}" if opening.get("start_time") else "",
        f"Initial pressure: {opening.get('initial_pressure')}" if opening.get("initial_pressure") else "",
        *(f"First-scene job: {item}" for item in clean_list(opening.get("first_scene_jobs"), limit=5)),
        *(f"Boundary: {item}" for item in clean_list(opening.get("boundaries"), limit=5)),
        *(f"Intro obligation: {item}" for item in clean_list(opening.get("introduction_obligations"), limit=4)),
        *(f"Not yet: {item}" for item in clean_list(opening.get("must_not_occur_yet"), limit=4)),
        f"Time skip policy: {opening.get('time_skip_policy')}" if opening.get("time_skip_policy") else "",
    ]
    opening_lines = [compact_text(line, 360) for line in opening_lines if compact_text(line)]
    if opening_lines:
        lines.extend(["Opening scope:", *opening_lines])
    projection = "\n".join(line for line in lines if compact_text(line))
    if len(projection) <= limit:
        return projection
    return projection[:limit].rsplit("\n", 1)[0].strip()


def render_story_foundation_for_prompt(session_id: str, limit: int = FOUNDATION_PROMPT_LIMIT) -> str:
    foundation = get_story_foundation(session_id)
    if not foundation:
        return ""
    return render_foundation_projection(foundation.get("foundation"), limit=limit)


def foundation_pronunciation_entries(session_id: str) -> list[dict[str, Any]]:
    foundation = get_story_foundation(session_id)
    if not foundation:
        return []
    entries: list[dict[str, Any]] = []
    for character in foundation.get("foundation", {}).get("characters") or []:
        if not isinstance(character, dict):
            continue
        written = compact_text(character.get("name"), 120)
        spoken = compact_text(character.get("pronunciation"), 160)
        if not written or not spoken or written.casefold() == spoken.casefold():
            continue
        digest = sha1(f"{session_id}:{written}:{spoken}".encode("utf-8")).hexdigest()[:12]
        entries.append(
            {
                "id": f"foundation-{digest}",
                "written_form": written,
                "spoken_form": spoken,
                "scope": "story",
                "story_id": session_id,
                "enabled": True,
                "notes": "Story foundation pronunciation hint.",
            }
        )
    return entries
