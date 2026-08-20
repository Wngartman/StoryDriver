from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.database import db_session
from app.memory.foundation import get_story_foundation


MEMORY_PACK_SCHEMA_VERSION = "memory_character_place_v3"
MEMORY_PACK_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS next_prompt_memory_cache_v3 (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    context_kind TEXT NOT NULL DEFAULT 'writer',
    relevance_hash TEXT NOT NULL,
    pack_json TEXT NOT NULL DEFAULT '{}',
    rendered_context TEXT NOT NULL DEFAULT '',
    item_count INTEGER NOT NULL DEFAULT 0,
    source_counts_json TEXT NOT NULL DEFAULT '{}',
    prompt_chars INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(session_id, context_kind, relevance_hash),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
"""
MEMORY_PACK_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_next_prompt_memory_cache_v3_session
ON next_prompt_memory_cache_v3(session_id, context_kind, updated_at);
"""

CONTEXT_LIMITS = {
    "planner": 3600,
    "writer": 4600,
    "overview": 5200,
}

CHARACTER_KEY_PRIORITY = {
    "current_location": 0,
    "location": 0,
    "current_place": 0,
    "room_position": 1,
    "position": 1,
    "spatial_position": 1,
    "current_posture": 2,
    "posture": 2,
    "current_action": 3,
    "action": 3,
    "current_outfit": 4,
    "outfit": 4,
    "clothing": 4,
    "wearing": 4,
    "hair_style": 5,
    "visible_injuries": 6,
    "injury": 6,
    "wound": 6,
    "carried_objects": 7,
    "holding": 7,
    "inventory": 7,
    "known_secrets": 8,
    "knows": 8,
    "knowledge": 8,
    "emotional_state": 9,
    "current_goal": 10,
    "short_term_goal": 10,
    "long_term_goal": 11,
    "current_relationship_tension": 12,
}

BLOCKING_KEYS = {
    "current_location",
    "location",
    "current_place",
    "room_position",
    "position",
    "spatial_position",
    "blocking_position",
    "current_posture",
    "current_action",
    "posture",
    "action",
}
APPEARANCE_KEYS = {
    "current_outfit",
    "outfit",
    "clothing",
    "wearing",
    "hair_style",
    "visible_injuries",
    "dirt",
    "blood_on_clothes",
    "wet_cloak",
}
KNOWLEDGE_GOAL_KEYS = {
    "known_secrets",
    "knows",
    "knowledge",
    "secret_known",
    "current_goal",
    "short_term_goal",
    "long_term_goal",
    "emotional_state",
    "current_relationship_tension",
}
SCENE_BLOCKING_KEYS = {
    "current_location",
    "location",
    "present_characters",
    "layout",
    "active_zones",
    "doors_exits_windows",
    "sight_lines",
    "sightlines",
    "hearing_range",
    "entering",
    "leaving",
    "entries_exits",
}
RELATIONSHIP_WEIGHT_FIELDS = (
    "closeness_weight",
    "trust_weight",
    "conflict_weight",
    "protective_weight",
    "grief_weight",
    "romantic_weight",
    "family_weight",
    "betrayal_weight",
    "respect_weight",
    "fear_weight",
    "emotional_importance",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clean_text(value: Any, limit: int = 4000) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].strip()


def normalize_key(value: Any, fallback: str = "general") -> str:
    text = clean_text(value, 120).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or fallback


def json_array(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        try:
            parsed = json.loads(value or "[]")
            raw = parsed if isinstance(parsed, list) else []
        except Exception:
            raw = []
    result: list[str] = []
    for item in raw:
        text = clean_text(item, 120)
        if text and text.lower() not in {existing.lower() for existing in result}:
            result.append(text)
    return result[:12]


def row_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def relevance_words(text: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9_ ]+", " ", (text or "").lower())
    stop = {
        "scene",
        "story",
        "chapter",
        "their",
        "there",
        "where",
        "when",
        "what",
        "with",
        "from",
        "that",
        "this",
        "have",
        "will",
        "into",
        "they",
        "them",
        "then",
    }
    return {word for word in normalized.split() if len(word) >= 4 and word not in stop}


def score_text(text: str, words: set[str]) -> float:
    if not text or not words:
        return 0.0
    lower = text.lower()
    score = 0.0
    for word in words:
        if word in lower:
            score += 0.18
    return min(score, 1.5)


def confidence_value(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value or 0)))
    except Exception:
        return 0.0


def weight_value(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value or 0)))
    except Exception:
        return 0.0


def load_memory_context(session_id: str) -> dict[str, Any]:
    with db_session() as db:
        characters = [
            row_dict(row)
            for row in db.execute(
                """
                SELECT c.id, c.name, c.role, c.personality, c.appearance, c.relationships,
                       c.current_state, c.voice, c.image_prompt, c.private_notes, sc.is_active
                FROM session_characters sc
                JOIN characters c ON c.id = sc.character_id
                WHERE sc.session_id = ?
                ORDER BY sc.is_active DESC, lower(c.name) ASC
                LIMIT 16
                """,
                (session_id,),
            ).fetchall()
        ]
        character_state = [
            row_dict(row)
            for row in db.execute(
                """
                SELECT *
                FROM character_live_state
                WHERE session_id = ? AND archived = 0 AND disabled = 0
                ORDER BY manual_override DESC, character_name ASC, updated_at DESC
                LIMIT 180
                """,
                (session_id,),
            ).fetchall()
        ]
        relationships = [
            row_dict(row)
            for row in db.execute(
                """
                SELECT *
                FROM relationship_state
                WHERE session_id = ? AND archived = 0 AND disabled = 0
                ORDER BY manually_pinned DESC, emotional_importance DESC, updated_at DESC
                LIMIT 80
                """,
                (session_id,),
            ).fetchall()
        ]
        emotional = [
            row_dict(row)
            for row in db.execute(
                """
                SELECT *
                FROM emotional_memories
                WHERE session_id = ? AND archived = 0 AND disabled = 0
                ORDER BY manually_pinned DESC, emotional_weight DESC, updated_at DESC
                LIMIT 80
                """,
                (session_id,),
            ).fetchall()
        ]
        world_notes_row = db.execute(
            """
            SELECT setting, tone, rules, locations, factions, conflicts, history
            FROM world_notes
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        world_state = [
            row_dict(row)
            for row in db.execute(
                """
                SELECT *
                FROM world_live_state
                WHERE session_id = ? AND archived = 0 AND disabled = 0
                ORDER BY manual_override DESC, updated_at DESC
                LIMIT 80
                """,
                (session_id,),
            ).fetchall()
        ]
        scene_state = [
            row_dict(row)
            for row in db.execute(
                """
                SELECT *
                FROM scene_live_state
                WHERE session_id = ? AND archived = 0 AND disabled = 0
                ORDER BY manual_override DESC, updated_at DESC
                LIMIT 80
                """,
                (session_id,),
            ).fetchall()
        ]
        objects = [
            row_dict(row)
            for row in db.execute(
                """
                SELECT *
                FROM object_state
                WHERE session_id = ? AND archived = 0 AND disabled = 0
                ORDER BY manual_override DESC, updated_at DESC
                LIMIT 80
                """,
                (session_id,),
            ).fetchall()
        ]
        threads = [
            row_dict(row)
            for row in db.execute(
                """
                SELECT *
                FROM plot_threads
                WHERE session_id = ? AND status = 'active' AND disabled = 0
                ORDER BY manual_override DESC, updated_at DESC
                LIMIT 60
                """,
                (session_id,),
            ).fetchall()
        ]
    return {
        "foundation": get_story_foundation(session_id),
        "characters": characters,
        "character_state": character_state,
        "relationships": relationships,
        "emotional_memories": emotional,
        "world_notes": row_dict(world_notes_row) if world_notes_row else {},
        "world_state": world_state,
        "scene_state": scene_state,
        "objects": objects,
        "plot_threads": threads,
    }


def source_counts(context: dict[str, Any]) -> dict[str, int]:
    return {
        "foundation": 1 if context.get("foundation") else 0,
        "characters": len(context.get("characters") or []),
        "character_state": len(context.get("character_state") or []),
        "relationships": len(context.get("relationships") or []),
        "emotional_memories": len(context.get("emotional_memories") or []),
        "world_state": len(context.get("world_state") or []),
        "scene_state": len(context.get("scene_state") or []),
        "objects": len(context.get("objects") or []),
        "plot_threads": len(context.get("plot_threads") or []),
    }


def relationship_salience(row: dict[str, Any]) -> float:
    score = 0.0
    if row.get("manually_pinned"):
        score += 1.0
    for field in RELATIONSHIP_WEIGHT_FIELDS:
        score += weight_value(row.get(field)) * (0.45 if field == "emotional_importance" else 0.18)
    score += confidence_value(row.get("confidence")) * 0.25
    return score


def character_item_score(row: dict[str, Any], words: set[str], mentioned_names: set[str]) -> tuple[float, int, str]:
    key = normalize_key(row.get("key"))
    text = " ".join([clean_text(row.get("character_name"), 120), clean_text(row.get("key"), 120), clean_text(row.get("value"), 260)])
    score = confidence_value(row.get("confidence")) + score_text(text, words)
    if clean_text(row.get("character_name"), 120).lower() in mentioned_names:
        score += 1.0
    if row.get("manual_override"):
        score += 0.8
    if key in BLOCKING_KEYS or key in APPEARANCE_KEYS or key in KNOWLEDGE_GOAL_KEYS:
        score += 0.45
    if row.get("is_tentative"):
        score -= 0.2
    return (-score, CHARACTER_KEY_PRIORITY.get(key, 50), clean_text(row.get("updated_at"), 80))


def relationship_score(row: dict[str, Any], words: set[str], mentioned_names: set[str]) -> float:
    text = " ".join(
        [
            clean_text(row.get("character_a_name"), 120),
            clean_text(row.get("character_b_name"), 120),
            clean_text(row.get("relationship_type"), 80),
            clean_text(row.get("relationship_key"), 120),
            clean_text(row.get("content"), 420),
        ]
    )
    score = relationship_salience(row) + score_text(text, words)
    names = {clean_text(row.get("character_a_name"), 120).lower(), clean_text(row.get("character_b_name"), 120).lower()}
    if names & mentioned_names:
        score += 1.0
    return score


def object_score(row: dict[str, Any], words: set[str], mentioned_names: set[str]) -> float:
    text = " ".join(
        [
            clean_text(row.get("name") or row.get("object_key"), 120),
            clean_text(row.get("state_type"), 80),
            clean_text(row.get("value"), 320),
            clean_text(row.get("previous_value"), 320),
            clean_text(row.get("owner_character_name"), 120),
            clean_text(row.get("holder_character_name"), 120),
            clean_text(row.get("current_location"), 160),
            clean_text(row.get("condition"), 120),
        ]
    )
    score = confidence_value(row.get("confidence")) + score_text(text, words)
    if {
        clean_text(row.get("owner_character_name"), 120).lower(),
        clean_text(row.get("holder_character_name"), 120).lower(),
    } & mentioned_names:
        score += 0.8
    score += weight_value(row.get("importance")) * 0.5
    if normalize_key(row.get("state_type")) in {"ownership", "location", "visibility"}:
        score += 0.35
    if row.get("manual_override"):
        score += 0.8
    return score


def emotional_score(row: dict[str, Any], words: set[str], mentioned_names: set[str]) -> float:
    related = {name.lower() for name in json_array(row.get("related_characters_json"))}
    text = " ".join(
        [
            clean_text(row.get("memory_type"), 80),
            clean_text(row.get("memory_text"), 420),
            clean_text(row.get("trigger_conditions"), 260),
            " ".join(json_array(row.get("themes_json"))),
            " ".join(related),
        ]
    )
    score = weight_value(row.get("emotional_weight")) * 1.4 + confidence_value(row.get("confidence")) * 0.25 + score_text(text, words)
    if row.get("manually_pinned"):
        score += 1.2
    if related & mentioned_names:
        score += 0.8
    if int(row.get("use_count") or 0) > 0 and not row.get("manually_pinned"):
        score -= 0.25
    return score


def compact_join(parts: list[str], separator: str = "; ") -> str:
    return separator.join(part for part in parts if clean_text(part, 500))


def foundation_lines(foundation_row: dict[str, Any] | None, words: set[str], mentioned_names: set[str]) -> list[str]:
    if not foundation_row:
        return []
    data = foundation_row.get("foundation") or {}
    overview = data.get("overview") if isinstance(data.get("overview"), dict) else {}
    opening = data.get("opening_scope") if isinstance(data.get("opening_scope"), dict) else {}
    world = data.get("world") if isinstance(data.get("world"), dict) else {}
    lines: list[str] = []
    lines.append("STORY FOUNDATION / CHARACTER BIBLE summarized for the v3 memory pack.")
    overview_text = compact_join(
        [
            f"Premise: {clean_text(overview.get('premise'), 320)}" if overview.get("premise") else "",
            f"Genre/time: {clean_text(overview.get('genre'), 100)} / {clean_text(overview.get('time_period'), 120)}"
            if overview.get("genre") or overview.get("time_period")
            else "",
            f"Tone: {clean_text(overview.get('tone'), 140)}" if overview.get("tone") else "",
        ]
    )
    if overview_text:
        lines.append(overview_text)
    for rule in (overview.get("story_rules") or [])[:4]:
        rule_text = clean_text(rule, 180)
        if rule_text:
            lines.append(f"Rule: {rule_text}")
    opening_bits = compact_join(
        [
            f"Start: {clean_text(opening.get('start_time'), 100)} at {clean_text(opening.get('start_location'), 160)}"
            if opening.get("start_time") or opening.get("start_location")
            else "",
            f"Opening pressure: {clean_text(opening.get('initial_pressure'), 220)}" if opening.get("initial_pressure") else "",
        ]
    )
    if opening_bits:
        lines.append(opening_bits)
    for item in (opening.get("boundaries") or [])[:4]:
        text = clean_text(item, 180)
        if text:
            lines.append(f"Boundary: {text}")
    for item in (opening.get("must_not_occur_yet") or [])[:3]:
        text = clean_text(item, 180)
        if text:
            lines.append(f"Not yet: {text}")
    world_bits = []
    for key in ("rules", "locations", "conflicts"):
        for item in (world.get(key) or [])[:2]:
            text = clean_text(item, 160)
            if text:
                world_bits.append(f"{key[:-1]}: {text}")
    if world.get("technology_magic"):
        world_bits.append(f"tech/magic: {clean_text(world.get('technology_magic'), 180)}")
    lines.extend(world_bits[:5])

    character_rows: list[tuple[float, str]] = []
    for character in data.get("characters") or []:
        if not isinstance(character, dict):
            continue
        name = clean_text(character.get("name"), 120)
        if not name:
            continue
        char_text = " ".join(str(character.get(field) or "") for field in ("name", "role", "personality", "private_goal", "relationships"))
        relevance = score_text(char_text, words) + (1.0 if name.lower() in mentioned_names else 0.0)
        line = compact_join(
            [
                name,
                clean_text(character.get("role"), 150),
                f"personality: {clean_text(character.get('personality'), 180)}" if character.get("personality") else "",
                f"goal: {clean_text(character.get('private_goal'), 140)}" if character.get("private_goal") else "",
                f"intro: {clean_text(character.get('introduction_need'), 140)}" if character.get("introduction_need") else "",
            ]
        )
        if line:
            character_rows.append((relevance, line))
    character_rows.sort(key=lambda item: item[0], reverse=True)
    lines.extend([f"Base character: {line}" for _score, line in character_rows[:5]])
    return lines[:16]


def build_character_lines(context: dict[str, Any], words: set[str], mentioned_names: set[str]) -> list[str]:
    states_by_name: dict[str, list[dict[str, Any]]] = {}
    for state in context.get("character_state") or []:
        name = clean_text(state.get("character_name"), 120)
        if name:
            states_by_name.setdefault(name.lower(), []).append(state)
    lines: list[str] = []
    for character in (context.get("characters") or [])[:10]:
        if not character.get("is_active"):
            continue
        name = clean_text(character.get("name"), 120)
        if not name:
            continue
        live_rows = sorted(states_by_name.get(name.lower(), []), key=lambda row: character_item_score(row, words, mentioned_names))
        live_bits = []
        for row in live_rows[:8]:
            key = clean_text(row.get("key"), 80).replace("_", " ")
            value = clean_text(row.get("value"), 180)
            if key and value:
                marker = " tentative" if row.get("is_tentative") else ""
                live_bits.append(f"{key}: {value}{marker}")
        base = compact_join(
            [
                clean_text(character.get("role"), 140),
                clean_text(character.get("personality"), 160),
                clean_text(character.get("current_state"), 160),
            ]
        )
        line = f"{name}: "
        if base:
            line += f"base {base}"
        if live_bits:
            line += ("; " if base else "") + "live " + "; ".join(live_bits)
        if line.strip() != f"{name}:":
            lines.append(line)
    return lines[:8]


def build_blocking_lines(context: dict[str, Any], words: set[str], mentioned_names: set[str]) -> list[str]:
    del mentioned_names
    lines: list[str] = []
    for row in context.get("scene_state") or []:
        key = normalize_key(row.get("key"))
        if key not in SCENE_BLOCKING_KEYS:
            continue
        value = clean_text(row.get("value"), 220)
        if value:
            lines.append(f"{clean_text(row.get('key'), 80).replace('_', ' ')}: {value}")
    character_rows = [
        row
        for row in context.get("character_state") or []
        if normalize_key(row.get("key")) in BLOCKING_KEYS and clean_text(row.get("value"), 220)
    ]
    character_rows.sort(key=lambda row: (0 if score_text(clean_text(row.get("value"), 260), words) else 1, character_item_score(row, words, set())))
    for row in character_rows[:8]:
        lines.append(
            f"{clean_text(row.get('character_name'), 120)} {clean_text(row.get('key'), 80).replace('_', ' ')}: {clean_text(row.get('value'), 200)}"
        )
    return lines[:12]


def build_inventory_body_lines(context: dict[str, Any], words: set[str], mentioned_names: set[str]) -> list[str]:
    lines: list[str] = []
    object_rows = sorted(context.get("objects") or [], key=lambda row: object_score(row, words, mentioned_names), reverse=True)
    for row in object_rows[:10]:
        name = clean_text(row.get("name") or row.get("object_key"), 120)
        value = clean_text(row.get("value"), 220)
        owner = clean_text(row.get("owner_character_name"), 120)
        holder = clean_text(row.get("holder_character_name"), 120)
        location = clean_text(row.get("current_location"), 160)
        condition = clean_text(row.get("condition"), 120)
        previous = clean_text(row.get("previous_value"), 220)
        if name and value:
            details = [f"owner {owner}" if owner else "", f"holder {holder}" if holder else "", location, condition]
            if previous and previous.casefold() != value.casefold() and score_text(previous, words):
                details.append(f"previously {previous}")
            lines.append(f"{name}: {value}" + (f"; {compact_join(details)}" if any(details) else ""))
    appearance_rows = [
        row
        for row in context.get("character_state") or []
        if normalize_key(row.get("key")) in APPEARANCE_KEYS
        or normalize_key(row.get("key")).startswith("injury_")
        or "wound" in normalize_key(row.get("key"))
    ]
    appearance_rows.sort(key=lambda row: character_item_score(row, words, mentioned_names))
    for row in appearance_rows[:8]:
        lines.append(
            f"{clean_text(row.get('character_name'), 120)} {clean_text(row.get('key'), 80).replace('_', ' ')}: {clean_text(row.get('value'), 180)}"
        )
    return lines[:12]


def build_relationship_lines(context: dict[str, Any], words: set[str], mentioned_names: set[str]) -> list[str]:
    rows = sorted(context.get("relationships") or [], key=lambda row: relationship_score(row, words, mentioned_names), reverse=True)
    lines: list[str] = []
    for row in rows[:8]:
        pair = " / ".join(
            item
            for item in [clean_text(row.get("character_a_name"), 80), clean_text(row.get("character_b_name"), 80)]
            if item
        )
        content = clean_text(row.get("content"), 260)
        if not pair or not content:
            continue
        tags = []
        for field, label in (
            ("emotional_importance", "importance"),
            ("family_weight", "family"),
            ("trust_weight", "trust"),
            ("conflict_weight", "conflict"),
            ("romantic_weight", "romantic"),
            ("grief_weight", "grief"),
            ("betrayal_weight", "betrayal"),
        ):
            value = weight_value(row.get(field))
            if value >= 0.65:
                tags.append(f"{label} {value:.2f}")
        lines.append(f"{pair}: {content}" + (f" ({'; '.join(tags[:4])})" if tags else ""))
    return lines


def build_knowledge_agency_lines(context: dict[str, Any], words: set[str], mentioned_names: set[str]) -> list[str]:
    rows = [
        row
        for row in context.get("character_state") or []
        if normalize_key(row.get("key")) in KNOWLEDGE_GOAL_KEYS and clean_text(row.get("value"), 220)
    ]
    rows.sort(key=lambda row: character_item_score(row, words, mentioned_names))
    lines = []
    for row in rows[:10]:
        lines.append(
            f"{clean_text(row.get('character_name'), 120)} {clean_text(row.get('key'), 80).replace('_', ' ')}: {clean_text(row.get('value'), 220)}"
        )
    return lines


def build_emotional_lines(context: dict[str, Any], words: set[str], mentioned_names: set[str]) -> tuple[list[str], list[str]]:
    rows = sorted(context.get("emotional_memories") or [], key=lambda row: emotional_score(row, words, mentioned_names), reverse=True)
    lines: list[str] = []
    used_ids: list[str] = []
    for row in rows[:5]:
        text = clean_text(row.get("memory_text"), 260)
        if not text:
            continue
        score = emotional_score(row, words, mentioned_names)
        if score < 1.05 and not row.get("manually_pinned"):
            continue
        related = json_array(row.get("related_characters_json"))
        themes = json_array(row.get("themes_json"))
        tags = []
        if related:
            tags.append("related " + ", ".join(related[:4]))
        if themes:
            tags.append("themes " + ", ".join(themes[:4]))
        if row.get("manually_pinned"):
            tags.append("pinned")
        lines.append(f"{text}" + (f" ({'; '.join(tags[:3])})" if tags else ""))
        if row.get("id"):
            used_ids.append(str(row["id"]))
    return lines, used_ids


def build_world_thread_lines(context: dict[str, Any], words: set[str]) -> list[str]:
    lines: list[str] = []
    for key, value in (context.get("world_notes") or {}).items():
        text = clean_text(value, 220)
        if text:
            lines.append(f"{key.replace('_', ' ')}: {text}")
    world_rows = sorted(
        context.get("world_state") or [],
        key=lambda row: confidence_value(row.get("confidence")) + score_text(clean_text(row.get("value"), 260), words),
        reverse=True,
    )
    for row in world_rows[:5]:
        key = clean_text(row.get("key"), 80).replace("_", " ")
        value = clean_text(row.get("value"), 220)
        if key and value:
            lines.append(f"{key}: {value}")
    thread_rows = sorted(
        context.get("plot_threads") or [],
        key=lambda row: confidence_value(row.get("confidence")) + score_text(clean_text(row.get("content"), 260), words),
        reverse=True,
    )
    for row in thread_rows[:6]:
        title = clean_text(row.get("title") or row.get("thread_key"), 120)
        content = clean_text(row.get("content") or row.get("status"), 240)
        if title:
            lines.append(f"Thread - {title}: {content or 'active'}")
    return lines[:12]


def build_conflict_lines(context: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    owners_by_object: dict[str, set[str]] = {}
    for row in context.get("objects") or []:
        key = normalize_key(row.get("object_key") or row.get("name"))
        holder = clean_text(row.get("holder_character_name"), 120)
        if key and holder:
            owners_by_object.setdefault(key, set()).add(holder)
    for key, owners in owners_by_object.items():
        if len(owners) > 1:
            lines.append(f"Review object ownership: {key.replace('_', ' ')} has multiple active holders ({', '.join(sorted(owners))}).")
    locations = [
        clean_text(row.get("value"), 160)
        for row in context.get("scene_state") or []
        if normalize_key(row.get("key")) in {"current_location", "location"} and clean_text(row.get("value"), 160)
    ]
    if len({item.lower() for item in locations}) > 1:
        lines.append("Review current location: multiple active scene locations are present.")
    return lines[:4]


def render_pack(pack: dict[str, Any], limit: int) -> str:
    header = [
        "NEXT PROMPT MEMORY PACK v3:",
        "Use as factual continuity only. Director note, user system prompt, task notes, Scene Contract, and manual edits outrank automatic memory.",
        "Prefer these selected facts over duplicating older raw context.",
        "",
    ]
    sections: list[dict[str, Any]] = []
    for section in pack.get("sections", []):
        items = [clean_text(item, 420) for item in section.get("items") or [] if clean_text(item, 420)]
        if not items:
            continue
        title = clean_text(section.get("title"), 80).upper()
        sections.append({"title": title, "items": items, "selected": []})

    used = sum(len(line) + 1 for line in header)
    for section in sections:
        first = clean_text(section["items"][0], 320)
        cost = len(section["title"]) + len(first) + 7
        if used + cost <= limit:
            section["selected"].append(first)
            used += cost

    max_items = max((len(section["items"]) for section in sections), default=0)
    for item_index in range(1, max_items):
        for section in sections:
            if item_index >= len(section["items"]):
                continue
            item = section["items"][item_index]
            cost = len(item) + 3
            if used + cost <= limit:
                section["selected"].append(item)
                used += cost

    lines = list(header)
    for section in sections:
        if not section["selected"]:
            continue
        if section["title"]:
            lines.append(f"{section['title']}:")
        lines.extend(f"- {item}" for item in section["selected"])
        lines.append("")

    text = "\n".join(lines).strip()
    all_items = sum(len(section["items"]) for section in sections)
    selected_items = sum(len(section["selected"]) for section in sections)
    if selected_items < all_items:
        suffix = "\n\n[Memory pack trimmed to fit prompt budget.]"
        if len(text) + len(suffix) <= limit:
            text += suffix
    return text


def ensure_memory_cache_schema() -> None:
    with db_session() as db:
        db.execute(MEMORY_PACK_TABLE_SQL)
        db.execute(MEMORY_PACK_INDEX_SQL)


def cache_pack(session_id: str, context_kind: str, relevance_hash: str, pack: dict[str, Any], rendered: str) -> None:
    ensure_memory_cache_schema()
    source_counts_data = pack.get("source_counts") or {}
    with db_session() as db:
        existing = db.execute(
            """
            SELECT id
            FROM next_prompt_memory_cache_v3
            WHERE session_id = ? AND context_kind = ? AND relevance_hash = ?
            """,
            (session_id, context_kind, relevance_hash),
        ).fetchone()
        if existing:
            db.execute(
                """
                UPDATE next_prompt_memory_cache_v3
                SET pack_json = ?, rendered_context = ?, item_count = ?, source_counts_json = ?,
                    prompt_chars = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(pack, ensure_ascii=False),
                    rendered,
                    int(pack.get("item_count") or 0),
                    json.dumps(source_counts_data, ensure_ascii=False),
                    len(rendered),
                    utc_now(),
                    existing["id"],
                ),
            )
        else:
            db.execute(
                """
                INSERT INTO next_prompt_memory_cache_v3 (
                    id, session_id, context_kind, relevance_hash, pack_json, rendered_context,
                    item_count, source_counts_json, prompt_chars
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    session_id,
                    context_kind,
                    relevance_hash,
                    json.dumps(pack, ensure_ascii=False),
                    rendered,
                    int(pack.get("item_count") or 0),
                    json.dumps(source_counts_data, ensure_ascii=False),
                    len(rendered),
                ),
            )
        db.execute(
            """
            DELETE FROM next_prompt_memory_cache_v3
            WHERE session_id = ? AND context_kind = ?
              AND id NOT IN (
                  SELECT id
                  FROM next_prompt_memory_cache_v3
                  WHERE session_id = ? AND context_kind = ?
                  ORDER BY updated_at DESC
                  LIMIT 30
              )
            """,
            (session_id, context_kind, session_id, context_kind),
        )


def mark_emotional_memories_used(memory_ids: list[str]) -> None:
    if not memory_ids:
        return
    placeholders = ",".join("?" for _ in memory_ids)
    with db_session() as db:
        db.execute(
            f"""
            UPDATE emotional_memories
            SET last_used_in_prompt = ?, use_count = use_count + 1
            WHERE id IN ({placeholders})
            """,
            (utc_now(), *memory_ids),
        )


def build_next_prompt_memory_pack(
    session_id: str,
    *,
    relevance_text: str = "",
    context_kind: str = "writer",
    limit: int | None = None,
    mark_used: bool = False,
    cache: bool = False,
) -> dict[str, Any]:
    context_kind = normalize_key(context_kind, "writer")
    limit = int(limit or CONTEXT_LIMITS.get(context_kind, CONTEXT_LIMITS["writer"]))
    context = load_memory_context(session_id)
    words = relevance_words(relevance_text)
    active_names = {
        clean_text(character.get("name"), 120).lower()
        for character in context.get("characters") or []
        if clean_text(character.get("name"), 120)
    }
    mentioned_names = {name for name in active_names if name and re.search(rf"\b{re.escape(name)}\b", (relevance_text or "").lower())}

    emotional_lines, emotional_ids = build_emotional_lines(context, words, mentioned_names)
    sections = [
        {"id": "foundation_essentials", "title": "Foundation Essentials", "items": foundation_lines(context.get("foundation"), words, mentioned_names)},
        {"id": "character_bible_live_state", "title": "Character Bible + Live State", "items": build_character_lines(context, words, mentioned_names)},
        {"id": "place_blocking", "title": "Place / Room / Blocking", "items": build_blocking_lines(context, words, mentioned_names)},
        {"id": "inventory_body_state", "title": "Inventory / Clothing / Body State", "items": build_inventory_body_lines(context, words, mentioned_names)},
        {"id": "relationship_memory", "title": "Relationship Memory", "items": build_relationship_lines(context, words, mentioned_names)},
        {"id": "knowledge_agency", "title": "Knowledge / Goals / Agency", "items": build_knowledge_agency_lines(context, words, mentioned_names)},
        {"id": "emotional_memory", "title": "Emotional Memory", "items": emotional_lines},
        {"id": "world_threads", "title": "World Constraints / Threads", "items": build_world_thread_lines(context, words)},
        {"id": "review_flags", "title": "Review Flags", "items": build_conflict_lines(context)},
    ]
    sections = [{**section, "items": [clean_text(item, 420) for item in section["items"] if clean_text(item, 420)]} for section in sections]
    item_count = sum(len(section["items"]) for section in sections)
    relevance_hash = hashlib.sha1(
        json.dumps(
            {
                "session_id": session_id,
                "context_kind": context_kind,
                "relevance_text": clean_text(relevance_text, 8000),
                "source_counts": source_counts(context),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    pack = {
        "schema_version": MEMORY_PACK_SCHEMA_VERSION,
        "session_id": session_id,
        "context_kind": context_kind,
        "relevance_hash": relevance_hash,
        "source_counts": source_counts(context),
        "item_count": item_count,
        "sections": sections,
    }
    rendered = render_pack(pack, limit) if item_count else ""
    pack["rendered_context"] = rendered
    pack["prompt_chars"] = len(rendered)
    if mark_used:
        mark_emotional_memories_used(emotional_ids)
    if cache and rendered:
        cache_pack(session_id, context_kind, relevance_hash, pack, rendered)
    return pack


def format_next_prompt_memory_pack(
    session_id: str,
    *,
    relevance_text: str = "",
    context_kind: str = "writer",
    limit: int | None = None,
    mark_used: bool = False,
    cache: bool = False,
) -> tuple[str, int, dict[str, Any]]:
    pack = build_next_prompt_memory_pack(
        session_id,
        relevance_text=relevance_text,
        context_kind=context_kind,
        limit=limit,
        mark_used=mark_used,
        cache=cache,
    )
    return clean_text(pack.get("rendered_context"), limit or 8000), int(pack.get("item_count") or 0), pack
