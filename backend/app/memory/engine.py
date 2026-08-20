from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from app.database import db_session
from app.schemas import (
    CharacterLiveStateRead,
    CharacterStateEventRead,
    EmotionalMemoryRead,
    ObjectStateRead,
    PlotThreadRead,
    RelationshipStateRead,
    SceneLiveStateRead,
    StoryStateConflictRead,
    StoryStateItemActionResponse,
    StoryStateItemUpdate,
    StoryStateOverview,
    StoryStateRunRead,
    WorldLiveStateRead,
)
from app.generation.model_provider import LMStudioClient, LMStudioError, model_client_for_settings
from app.generation.router import resolve_task_model_settings, task_parameters
from app.memory.context import build_next_prompt_memory_pack
from app.memory.summaries import summary_status
from app.settings.store import load_story_state_settings
from app.memory.foundation import render_story_foundation_for_prompt


LOW_CONFIDENCE_SKIP = 0.45
TENTATIVE_CONFIDENCE = 0.7
MANUAL_OVERRIDE_REPLACE_CONFIDENCE = 0.9
CURRENT_SCENE_KEYS = {
    "current_location",
    "location",
    "current_scene",
    "current_place",
    "setting",
}
CURRENT_CHARACTER_LOCATION_KEYS = {"current_location", "location", "current_place"}
CURRENT_CHARACTER_POSITION_KEYS = {
    "room_position",
    "position",
    "spatial_position",
    "blocking_position",
    "where_standing",
    "near",
}
CURRENT_CHARACTER_OUTFIT_KEYS = {"current_outfit", "outfit", "clothing", "wearing"}
CURRENT_CHARACTER_POSTURE_KEYS = {"posture", "current_posture", "current_action", "action", "stance"}
CURRENT_CHARACTER_CARRIED_KEYS = {"carried_objects", "holding", "held_object", "inventory", "current_inventory"}
CURRENT_CHARACTER_KNOWLEDGE_KEYS = {"known_secrets", "knows", "knowledge", "secret_known"}
CURRENT_CHARACTER_EMOTION_KEYS = {"emotional_state", "current_emotional_state", "mood", "feeling"}
CURRENT_CHARACTER_GOAL_KEYS = {"short_term_goal", "current_goal", "long_term_goal", "motivation"}
CURRENT_CHARACTER_TENSION_KEYS = {"current_relationship_tension", "relationship_tension", "tension"}
GENERIC_RELATIONSHIP_CONTENT = {
    "active bond",
    "core cast",
    "current relationship pressure affects their choices in the scene",
    "relationship pressure affects choices",
    "useful mix of trust, pressure, and disagreement",
}
POSITION_REJECT_PATTERNS = (
    r"\bintroduced or present\b",
    r"\b(feels?|felt|afraid|angry|anxious|ashamed|hopeful|hurt|jealous|sad)\b",
    r"\b(straps?|sleeves?|collar|clothes?|pack)\b.*\b(hurt|tight|pressed|bit|ached)\b",
    r"\b(heart|chest|stomach|throat|pulse|breath)\b",
    r"\b(?:biting|cold|open|night|denver)?\s*air\b",
)
POSITION_LANDMARK_PATTERN = re.compile(
    r"\b(?:beside|near|at|by|in|inside|outside|behind|before|across from|against|under|between|on|"
    r"next to|within|beyond|through|toward|from|to)\b\s+.{2,120}",
    re.I,
)
POSITION_MOVEMENT_PATTERN = re.compile(
    r"\b(?:entered|exited|left|departed|crossed|moved|walked|ran|stepped|returned|waits?|stands?|sits?|"
    r"knelt|crouched|leans?|remains?)\b",
    re.I,
)
OUTFIT_LAYER_HINTS = ("cloak", "coat", "dress", "tunic", "shirt", "boots", "hood", "gloves", "belt", "armor")
INJURY_CONFLICT_HINTS = (
    ("fresh", "scar"),
    ("fresh", "healed"),
    ("open", "scar"),
    ("open", "healed"),
    ("bleeding", "scar"),
    ("bleeding", "healed"),
    ("bandaged", "scar"),
)
STATE_FALLBACK_NAME_EXCLUSIONS = {
    "The",
    "A",
    "An",
    "And",
    "But",
    "Or",
    "No",
    "She",
    "He",
    "They",
    "Her",
    "His",
    "Their",
    "This",
    "That",
    "When",
    "Where",
    "What",
    "There",
    "Not",
    "One",
    "Then",
    "You",
    "From",
    "Every",
    "Only",
    "Those",
    "Maybe",
    "Check",
    "Think",
    "Knowing",
    "Old",
    "Man",
    "Little",
    "Miller",
    "Blackwood",
    "Ridge",
    "Oakhaven",
    "StoryDriver",
    "Chapter",
    "Scene",
    "Magic",
}
PROMPT_STATE_LIMITS = {
    "character_live": 8,
    "relationships": 8,
    "emotional_memories": 4,
    "world": 8,
    "scene": 6,
    "objects": 10,
    "plot_threads": 8,
}
EXTRACTION_CONTEXT_LIMITS = {
    "characters": 8,
    "live_state": 24,
    "relationships": 14,
    "emotional_memories": 8,
    "objects": 16,
    "plot_threads": 14,
    "world_field": 260,
    "summary": 800,
    "scene_text": 4500,
}
RELATIONSHIP_TYPES = {
    "parent",
    "child",
    "sibling",
    "spouse",
    "lover",
    "close_friend",
    "mentor",
    "rival",
    "enemy",
    "betrayer",
    "ally",
    "acquaintance",
    "stranger",
    "unknown",
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
EMOTIONAL_MEMORY_TYPES = {
    "grief",
    "trauma",
    "promise",
    "betrayal",
    "conflict",
    "guilt",
    "shame",
    "fear",
    "protection",
    "loss",
    "secret",
    "emotional",
}
EMOTIONAL_RECALL_THEME_WORDS = {
    "family",
    "sister",
    "brother",
    "mother",
    "father",
    "parent",
    "child",
    "home",
    "rescue",
    "promise",
    "secret",
    "betrayal",
    "trust",
    "death",
    "dead",
    "grief",
    "grave",
    "wound",
    "injury",
    "fear",
    "shame",
    "guilt",
    "lover",
    "spouse",
    "mentor",
    "rival",
    "enemy",
    "protect",
    "protected",
}
CHARACTER_PROMPT_PRIORITY = {
    "current_location": 0,
    "room_position": 1,
    "current_posture": 2,
    "current_action": 3,
    "current_outfit": 4,
    "hair_style": 5,
    "visible_injuries": 6,
    "carried_objects": 7,
    "known_secrets": 8,
    "emotional_state": 9,
    "short_term_goal": 10,
    "long_term_goal": 11,
    "current_relationship_tension": 12,
}
TRANSIENT_MODEL_UNAVAILABLE_MARKERS = (
    "model unloaded",
    "model is unloaded",
    "no model loaded",
    "no loaded model",
)
LAST_STORY_STATE_EVENT: dict[str, Any] = {
    "session_id": None,
    "scene_id": None,
    "version_id": None,
    "status": None,
    "error": None,
    "task_type": None,
    "model": None,
    "timeout_seconds": None,
}
STALE_RUNNING_RUN_SECONDS = 30 * 60


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_utc_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def is_stale_running_state_run(row: Any) -> bool:
    try:
        if row["status"] != "running":
            return False
    except (KeyError, TypeError):
        return False
    timestamp = None
    for key in ("updated_at", "started_at", "created_at"):
        try:
            timestamp = parse_utc_timestamp(row[key])
        except (KeyError, TypeError):
            timestamp = None
        if timestamp:
            break
    if not timestamp:
        return False
    return (datetime.now(timezone.utc) - timestamp).total_seconds() > STALE_RUNNING_RUN_SECONDS


def stale_state_run_warning() -> str:
    return "State extraction appears stale. Run extraction again for the selected scene if you need fresh state."


def clean_text(value: Any, max_length: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return " ".join(text.strip().split())[:max_length]


def compact_value(value: Any, max_length: int = 240) -> str:
    return clean_text(value, max_length)


def compact_row(row: dict[str, Any], fields: tuple[str, ...], *, max_length: int = 240) -> dict[str, str]:
    compacted: dict[str, str] = {}
    for field in fields:
        value = compact_value(row.get(field), max_length)
        if value:
            compacted[field] = value
    return compacted


def compact_rows(
    rows: list[dict[str, Any]],
    fields: tuple[str, ...],
    *,
    limit: int,
    max_length: int = 240,
) -> list[dict[str, str]]:
    compacted: list[dict[str, str]] = []
    for row in rows[:limit]:
        item = compact_row(row, fields, max_length=max_length)
        if item:
            compacted.append(item)
    return compacted


def normalize_key(value: Any, fallback: str = "general") -> str:
    text = clean_text(value, 160).lower()
    text = re.sub(r"[^a-z0-9_ -]+", "", text).strip().replace(" ", "_").replace("-", "_")
    text = re.sub(r"_+", "_", text)
    return text or fallback


def normalize_object_identity(value: Any, limit: int = 180) -> str:
    name = clean_text(str(value or "").replace("_", " "), limit)
    name = re.sub(r"^(?:the|a|an|his|her|their)\s+", "", name, flags=re.I)
    name = re.split(r"\bfrom\b", name, maxsplit=1, flags=re.I)[0]
    name = re.split(
        r"\b(?:was|were|is|are|gripped|gripping|held|holding|carried|carrying|clutched|clutching|"
        r"rested|resting|lay|lying|sat|sitting|hung|hanging)\b",
        name,
        maxsplit=1,
        flags=re.I,
    )[0]
    name = re.sub(r"\s+(?:tight|tightly|close|closely|firm|firmly)\s*$", "", name, flags=re.I)
    return name.rstrip(" .,;:!?\"'()[]")


def confidence_value(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def confidence_with_default(value: Any, default: float) -> float:
    if value is None or value == "":
        return confidence_value(default)
    return confidence_value(value)


def calibrated_confidence(update: dict[str, Any], default: float) -> float:
    confidence = confidence_with_default(update.get("confidence"), default)
    evidence_text = " ".join(
        clean_text(update.get(field), 260).lower()
        for field in (
            "confidence_reason",
            "evidence",
            "evidence_strength",
            "source",
            "source_text",
            "support",
        )
        if clean_text(update.get(field), 260)
    )
    if any(marker in evidence_text for marker in ("manual", "user edited", "user-set", "user set")):
        return 1.0
    if any(marker in evidence_text for marker in ("repeated explicit", "repeated direct", "again states", "again directly")):
        return max(confidence, 0.9)
    if any(marker in evidence_text for marker in ("explicit", "directly states", "scene states", "literally states", "says ", "said ")):
        return max(confidence, 0.88)
    if any(marker in evidence_text for marker in ("strong implication", "strongly implies", "clearly implies")):
        return max(confidence, 0.72)
    if any(marker in evidence_text for marker in ("weak implication", "weakly implies", "guess", "speculative", "unclear")):
        return min(confidence, 0.64)
    return confidence


def weight_value(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return confidence_value(default)
    return confidence_value(value)


def relationship_type_value(value: Any) -> str:
    raw = normalize_key(value, "unknown")
    aliases = {
        "sister": "sibling",
        "sisters": "sibling",
        "brother": "sibling",
        "brothers": "sibling",
        "mother": "parent",
        "father": "parent",
        "parents": "parent",
        "son": "child",
        "daughter": "child",
        "wife": "spouse",
        "husband": "spouse",
        "friend": "close_friend",
        "close_friendship": "close_friend",
        "betrayed": "betrayer",
        "betrayal": "betrayer",
    }
    normalized = aliases.get(raw, raw)
    return normalized if normalized in RELATIONSHIP_TYPES else "unknown"


def safe_json_list(values: Any, *, limit: int = 12, item_limit: int = 80) -> list[str]:
    if isinstance(values, str):
        try:
            parsed = json.loads(values)
        except json.JSONDecodeError:
            parsed = [part.strip() for part in re.split(r"[,;|]", values)]
    elif isinstance(values, list):
        parsed = values
    else:
        parsed = []
    result: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        text = clean_text(item, item_limit)
        key = text.lower()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
        if len(result) >= limit:
            break
    return result


def json_list_text(values: Any, *, limit: int = 12, item_limit: int = 80) -> str:
    return json.dumps(safe_json_list(values, limit=limit, item_limit=item_limit), ensure_ascii=False)


def json_array(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [clean_text(item, 1200) for item in parsed if clean_text(item)]


def update_last_state_event(
    *,
    session_id: str,
    scene_id: str,
    version_id: str | None,
    status: str,
    error: str | None = None,
    task_type: str | None = None,
    model: str | None = None,
    timeout_seconds: float | None = None,
) -> None:
    payload = {
        "session_id": session_id,
        "scene_id": scene_id,
        "version_id": version_id,
        "status": status,
        "error": error,
        "task_type": task_type,
        "model": model,
        "timeout_seconds": timeout_seconds,
    }
    LAST_STORY_STATE_EVENT.update(payload)


def last_story_state_event() -> dict[str, Any]:
    return dict(LAST_STORY_STATE_EVENT)


def story_state_run_status(session_id: str | None = None) -> dict[str, Any]:
    event = last_story_state_event()
    event_applies = not session_id or event.get("session_id") == session_id
    with db_session() as db:
        if session_id:
            row = db.execute(
                """
                SELECT session_id, scene_id, version_id, status, error, completed_at, updated_at, started_at, created_at
                FROM story_state_runs
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        else:
            row = db.execute(
                """
                SELECT session_id, scene_id, version_id, status, error, completed_at, updated_at, started_at, created_at
                FROM story_state_runs
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
    stored = {key: row[key] for key in row.keys()} if row else None
    if stored and is_stale_running_state_run(stored):
        stored = {
            **stored,
            "status": "stale",
            "error": stored.get("error") or stale_state_run_warning(),
        }
    return {
        "session_id": event.get("session_id") if event_applies and event.get("session_id") else (stored or {}).get("session_id"),
        "scene_id": event.get("scene_id") if event_applies and event.get("scene_id") else (stored or {}).get("scene_id"),
        "version_id": event.get("version_id") if event_applies and event.get("version_id") else (stored or {}).get("version_id"),
        "status": event.get("status") if event_applies and event.get("status") else (stored or {}).get("status"),
        "error": event.get("error") if event_applies and event.get("error") else (stored or {}).get("error"),
        "task_type": event.get("task_type") if event_applies else None,
        "model": event.get("model") if event_applies else None,
        "timeout_seconds": event.get("timeout_seconds") if event_applies else None,
        "completed_at": (stored or {}).get("completed_at"),
        "updated_at": (stored or {}).get("updated_at"),
    }


def prompt_line(label: str, value: Any, limit: int = 420) -> str:
    text = clean_text(value, limit)
    return f"{label}: {text}" if text else ""


def active_prompt_state_count(session_id: str | None = None) -> int:
    where = "WHERE archived = 0 AND disabled = 0"
    args: tuple[Any, ...] = ()
    if session_id:
        where += " AND session_id = ?"
        args = (session_id,)
    with db_session() as db:
        character_count = db.execute(f"SELECT COUNT(*) AS count FROM character_live_state {where}", args).fetchone()["count"]
        relationship_count = db.execute(f"SELECT COUNT(*) AS count FROM relationship_state {where}", args).fetchone()["count"]
        world_count = db.execute(f"SELECT COUNT(*) AS count FROM world_live_state {where}", args).fetchone()["count"]
        scene_count = db.execute(f"SELECT COUNT(*) AS count FROM scene_live_state {where}", args).fetchone()["count"]
        object_count = db.execute(f"SELECT COUNT(*) AS count FROM object_state {where}", args).fetchone()["count"]
        if session_id:
            thread_count = db.execute(
                "SELECT COUNT(*) AS count FROM plot_threads WHERE session_id = ? AND status = 'active' AND disabled = 0",
                (session_id,),
            ).fetchone()["count"]
        else:
            thread_count = db.execute("SELECT COUNT(*) AS count FROM plot_threads WHERE status = 'active' AND disabled = 0").fetchone()["count"]
        emotional_count = db.execute(f"SELECT COUNT(*) AS count FROM emotional_memories {where}", args).fetchone()["count"]
    return int(character_count + relationship_count + emotional_count + world_count + scene_count + object_count + thread_count)


def archived_state_count(session_id: str | None = None) -> int:
    where = "WHERE (archived = 1 OR disabled = 1)"
    args: tuple[Any, ...] = ()
    if session_id:
        where += " AND session_id = ?"
        args = (session_id,)
    with db_session() as db:
        character_count = db.execute(f"SELECT COUNT(*) AS count FROM character_live_state {where}", args).fetchone()["count"]
        relationship_count = db.execute(f"SELECT COUNT(*) AS count FROM relationship_state {where}", args).fetchone()["count"]
        world_count = db.execute(f"SELECT COUNT(*) AS count FROM world_live_state {where}", args).fetchone()["count"]
        scene_count = db.execute(f"SELECT COUNT(*) AS count FROM scene_live_state {where}", args).fetchone()["count"]
        object_count = db.execute(f"SELECT COUNT(*) AS count FROM object_state {where}", args).fetchone()["count"]
        if session_id:
            thread_count = db.execute(
                "SELECT COUNT(*) AS count FROM plot_threads WHERE session_id = ? AND (status IN ('archived', 'resolved') OR disabled = 1)",
                (session_id,),
            ).fetchone()["count"]
        else:
            thread_count = db.execute("SELECT COUNT(*) AS count FROM plot_threads WHERE status IN ('archived', 'resolved') OR disabled = 1").fetchone()["count"]
        emotional_count = db.execute(f"SELECT COUNT(*) AS count FROM emotional_memories {where}", args).fetchone()["count"]
    return int(character_count + relationship_count + emotional_count + world_count + scene_count + object_count + thread_count)


def disabled_state_count(session_id: str | None = None) -> int:
    where = "WHERE disabled = 1"
    args: tuple[Any, ...] = ()
    if session_id:
        where += " AND session_id = ?"
        args = (session_id,)
    with db_session() as db:
        total = 0
        for table in ("character_live_state", "relationship_state", "emotional_memories", "world_live_state", "scene_live_state", "object_state", "plot_threads"):
            total += int(db.execute(f"SELECT COUNT(*) AS count FROM {table} {where}", args).fetchone()["count"])
    return total


def manual_override_count(session_id: str | None = None) -> int:
    where = "WHERE manual_override = 1"
    args: tuple[Any, ...] = ()
    if session_id:
        where += " AND session_id = ?"
        args = (session_id,)
    with db_session() as db:
        total = 0
        for table in ("character_live_state", "relationship_state", "emotional_memories", "world_live_state", "scene_live_state", "object_state", "plot_threads"):
            total += int(db.execute(f"SELECT COUNT(*) AS count FROM {table} {where}", args).fetchone()["count"])
    return total


def archive_superseded_scene_version_state(
    db,
    *,
    session_id: str,
    scene_id: str,
    version_id: str | None,
) -> int:
    if not version_id:
        return 0
    archived = 0
    for table in ("character_live_state", "relationship_state", "emotional_memories", "world_live_state", "scene_live_state", "object_state"):
        cursor = db.execute(
            f"""
            UPDATE {table}
            SET archived = 1
            WHERE session_id = ?
              AND source_scene_id = ?
              AND COALESCE(source_version_id, '') != ?
              AND archived = 0
              AND manual_override = 0
            """,
            (session_id, scene_id, version_id),
        )
        archived += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
    cursor = db.execute(
        """
        UPDATE plot_threads
        SET status = 'archived'
        WHERE session_id = ?
          AND source_scene_id = ?
          AND COALESCE(source_version_id, '') != ?
          AND status = 'active'
          AND manual_override = 0
        """,
        (session_id, scene_id, version_id),
    )
    archived += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
    return archived


def load_prompt_state_context(session_id: str) -> dict[str, Any]:
    with db_session() as db:
        character_rows = db.execute(
            """
            SELECT
                c.id, c.name, c.role, c.personality, c.appearance, c.current_state,
                c.image_prompt, c.lora_trigger, sc.is_active
            FROM session_characters sc
            JOIN characters c ON c.id = sc.character_id
            WHERE sc.session_id = ? AND sc.is_active = 1
            ORDER BY lower(c.name) ASC
            """,
            (session_id,),
        ).fetchall()
        live_rows = db.execute(
            """
            SELECT character_id, character_name, state_type, key, value, confidence, is_tentative,
                   source_scene_id, source_version_id, updated_at
            FROM character_live_state
            WHERE session_id = ? AND archived = 0 AND disabled = 0
            ORDER BY character_name ASC, updated_at DESC
            LIMIT 120
            """,
            (session_id,),
        ).fetchall()
        relationship_rows = db.execute(
            """
            SELECT character_a_name, character_b_name, relationship_type, relationship_key, content,
                   closeness_weight, trust_weight, conflict_weight, protective_weight, grief_weight,
                   romantic_weight, family_weight, betrayal_weight, respect_weight, fear_weight,
                   emotional_importance, manually_pinned, confidence, is_tentative,
                   source_scene_id, source_version_id, updated_at
            FROM relationship_state
            WHERE session_id = ? AND archived = 0 AND disabled = 0
            ORDER BY manually_pinned DESC, emotional_importance DESC, updated_at DESC
            LIMIT 40
            """,
            (session_id,),
        ).fetchall()
        emotional_rows = db.execute(
            """
            SELECT id, memory_type, memory_text, emotional_weight, themes_json,
                   related_characters_json, trigger_conditions, last_used_in_prompt,
                   use_count, cooldown_scenes, confidence, is_tentative, manually_pinned,
                   source_scene_id, source_version_id, updated_at
            FROM emotional_memories
            WHERE session_id = ? AND archived = 0 AND disabled = 0
            ORDER BY manually_pinned DESC, emotional_weight DESC, updated_at DESC
            LIMIT 40
            """,
            (session_id,),
        ).fetchall()
        world_notes = db.execute(
            """
            SELECT setting, tone, rules, locations, factions, conflicts, history
            FROM world_notes
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        world_rows = db.execute(
            """
            SELECT state_type, key, value, confidence, is_tentative, source_scene_id,
                   source_version_id, updated_at
            FROM world_live_state
            WHERE session_id = ? AND archived = 0 AND disabled = 0
            ORDER BY updated_at DESC
            LIMIT 40
            """,
            (session_id,),
        ).fetchall()
        scene_rows = db.execute(
            """
            SELECT scene_id, version_id, state_type, key, value, confidence, is_tentative, updated_at
            FROM scene_live_state
            WHERE session_id = ? AND archived = 0 AND disabled = 0
            ORDER BY updated_at DESC
            LIMIT 30
            """,
            (session_id,),
        ).fetchall()
        object_rows = db.execute(
            """
            SELECT object_key, name, state_type, value, owner_character_name, holder_character_name,
                   current_location, placement_state, condition, visibility, importance, confidence,
                   is_tentative, source_scene_id, source_version_id, updated_at
            FROM object_state
            WHERE session_id = ? AND archived = 0 AND disabled = 0
            ORDER BY updated_at DESC
            LIMIT 40
            """,
            (session_id,),
        ).fetchall()
        plot_rows = db.execute(
            """
            SELECT thread_key, title, status, content, confidence, is_tentative,
                   source_scene_id, source_version_id, updated_at
            FROM plot_threads
            WHERE session_id = ? AND status = 'active' AND disabled = 0
            ORDER BY updated_at DESC
            LIMIT 40
            """,
            (session_id,),
        ).fetchall()
    return {
        "active_characters": [{key: row[key] for key in row.keys()} for row in character_rows],
        "character_live_state": [{key: row[key] for key in row.keys()} for row in live_rows],
        "relationships": [{key: row[key] for key in row.keys()} for row in relationship_rows],
        "emotional_memories": [{key: row[key] for key in row.keys()} for row in emotional_rows],
        "world_notes": {key: world_notes[key] for key in world_notes.keys()} if world_notes else {},
        "world_state": [{key: row[key] for key in row.keys()} for row in world_rows],
        "scene_state": [{key: row[key] for key in row.keys()} for row in scene_rows],
        "objects": [{key: row[key] for key in row.keys()} for row in object_rows],
        "plot_threads": [{key: row[key] for key in row.keys()} for row in plot_rows],
    }


def relationship_salience_parts(item: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    relationship_type = relationship_type_value(item.get("relationship_type") or item.get("relationship_key"))
    if relationship_type != "unknown":
        parts.append(relationship_type.replace("_", " "))
    for field, label in (
        ("emotional_importance", "importance"),
        ("closeness_weight", "closeness"),
        ("family_weight", "family"),
        ("trust_weight", "trust"),
        ("protective_weight", "protective"),
        ("grief_weight", "grief"),
        ("betrayal_weight", "betrayal"),
        ("conflict_weight", "conflict"),
        ("romantic_weight", "romantic"),
        ("fear_weight", "fear"),
    ):
        value = weight_value(item.get(field))
        if value >= 0.65:
            parts.append(f"{label} {value:.2f}")
    if item.get("manually_pinned"):
        parts.append("pinned")
    return parts[:5]


def character_state_prompt_sort_key(item: dict[str, Any]) -> tuple[int, int, float, str]:
    key = normalize_key(item.get("key"))
    confidence = confidence_value(item.get("confidence"))
    priority = CHARACTER_PROMPT_PRIORITY.get(key, 50)
    tentative_penalty = 1 if item.get("is_tentative") else 0
    return (priority, tentative_penalty, -confidence, str(item.get("updated_at") or ""))


def character_state_prompt_label(item: dict[str, Any]) -> str:
    key = normalize_key(item.get("key"))
    labels = {
        "current_location": "location",
        "room_position": "position",
        "current_posture": "posture",
        "current_action": "action",
        "current_outfit": "outfit",
        "hair_style": "hair",
        "visible_injuries": "injuries",
        "carried_objects": "carrying",
        "known_secrets": "knows",
        "emotional_state": "emotion",
        "short_term_goal": "short-term goal",
        "long_term_goal": "long-term goal",
        "current_relationship_tension": "tension",
    }
    return labels.get(key, clean_text(item.get("key"), 80).replace("_", " "))


def relevance_words(text: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9_ ]+", " ", (text or "").lower())
    words = {word for word in normalized.split() if len(word) >= 4}
    return words | {word for word in EMOTIONAL_RECALL_THEME_WORDS if word in normalized}


def emotional_memory_score(item: dict[str, Any], *, relevance_text: str, active_names: list[str]) -> float:
    text = " ".join(
        [
            clean_text(item.get("memory_text"), 800),
            clean_text(item.get("trigger_conditions"), 300),
            " ".join(safe_json_list(item.get("themes_json"), limit=12)),
            " ".join(safe_json_list(item.get("related_characters_json"), limit=12)),
        ]
    )
    relevant = relevance_words(relevance_text)
    themes = {theme.lower() for theme in safe_json_list(item.get("themes_json"), limit=12)}
    related = {name.lower() for name in safe_json_list(item.get("related_characters_json"), limit=12)}
    active = {name.lower() for name in active_names if name}
    weight = weight_value(item.get("emotional_weight"))
    score = weight * 0.65
    if item.get("manually_pinned"):
        score += 1.0
    if themes & relevant:
        score += 0.7
    if related & active:
        score += 0.25
    if any(name and name in (relevance_text or "").lower() for name in related):
        score += 0.35
    if any(word in (relevance_text or "").lower() for word in ("promise", "secret", "betray", "death", "dead", "rescue", "family")):
        score += 0.2
    if weight >= 0.9 and (themes & relevant or any(name and name in (relevance_text or "").lower() for name in related)):
        score += 0.2
    relevance_lower = (relevance_text or "").lower()
    if any(word in relevance_lower for word in ("promise", "secret", "betray", "death", "dead", "rescue", "family", "grief")) and any(
        word in text.lower() for word in ("unresolved", "promise", "secret", "betray", "grief")
    ):
        score += 0.15
    return score


def select_emotional_memories(
    context: dict[str, Any],
    *,
    relevance_text: str = "",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    limit = limit or PROMPT_STATE_LIMITS["emotional_memories"]
    active_names = [
        clean_text(character.get("name"), 120).lower()
        for character in context.get("active_characters", [])
        if clean_text(character.get("name"), 120)
    ]
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in context.get("emotional_memories", []):
        memory_text = clean_text(item.get("memory_text"), 500)
        if not memory_text:
            continue
        score = emotional_memory_score(item, relevance_text=relevance_text, active_names=active_names)
        used_count = int(item.get("use_count") or 0)
        pinned = bool(item.get("manually_pinned"))
        if not pinned and score < 0.95:
            continue
        if not pinned and used_count > 0 and score < 1.25:
            continue
        scored.append((score, item))
    scored.sort(key=lambda pair: (pair[0], weight_value(pair[1].get("emotional_weight")), pair[1].get("updated_at") or ""), reverse=True)
    return [item for _score, item in scored[:limit]]


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


def inferred_relationship_weights(relationship_type: str, key: str, content: str) -> dict[str, float]:
    normalized = f"{relationship_type} {key} {content}".lower()
    weights = {field: 0.0 for field in RELATIONSHIP_WEIGHT_FIELDS}
    if relationship_type in {"parent", "child", "sibling", "spouse"}:
        weights["family_weight"] = 0.9
        weights["closeness_weight"] = 0.72
        weights["protective_weight"] = 0.55
        weights["emotional_importance"] = 0.8
    if relationship_type == "lover":
        weights["romantic_weight"] = 0.85
        weights["closeness_weight"] = 0.75
        weights["emotional_importance"] = 0.78
    if relationship_type == "close_friend":
        weights["closeness_weight"] = 0.72
        weights["trust_weight"] = 0.62
        weights["emotional_importance"] = 0.65
    if relationship_type == "mentor":
        weights["respect_weight"] = 0.72
        weights["trust_weight"] = 0.55
        weights["emotional_importance"] = 0.62
    if relationship_type in {"rival", "enemy"}:
        weights["conflict_weight"] = 0.75
        weights["fear_weight"] = 0.35
        weights["emotional_importance"] = 0.58
    if relationship_type == "betrayer":
        weights["betrayal_weight"] = 0.88
        weights["conflict_weight"] = 0.65
        weights["trust_weight"] = 0.1
        weights["emotional_importance"] = 0.82
    if relationship_type == "ally":
        weights["trust_weight"] = 0.55
        weights["respect_weight"] = 0.45
        weights["emotional_importance"] = 0.45
    if any(word in normalized for word in ("sister", "brother", "sibling")):
        weights["family_weight"] = max(weights["family_weight"], 0.92)
        weights["closeness_weight"] = max(weights["closeness_weight"], 0.72)
        weights["emotional_importance"] = max(weights["emotional_importance"], 0.78)
    if any(word in normalized for word in ("mother", "father", "parent", "child", "daughter", "son")):
        weights["family_weight"] = max(weights["family_weight"], 0.95)
        weights["protective_weight"] = max(weights["protective_weight"], 0.72)
        weights["emotional_importance"] = max(weights["emotional_importance"], 0.88)
    if any(word in normalized for word in ("dead", "death", "died", "killed", "lost", "grief", "mourning")):
        weights["grief_weight"] = max(weights["grief_weight"], 0.82)
        weights["emotional_importance"] = max(weights["emotional_importance"], 0.86)
    if any(word in normalized for word in ("betray", "betrayed", "treachery", "lied", "deceived")):
        weights["betrayal_weight"] = max(weights["betrayal_weight"], 0.8)
        weights["conflict_weight"] = max(weights["conflict_weight"], 0.55)
        weights["emotional_importance"] = max(weights["emotional_importance"], 0.78)
    if "protect" in normalized or "rescue" in normalized:
        weights["protective_weight"] = max(weights["protective_weight"], 0.65)
        weights["emotional_importance"] = max(weights["emotional_importance"], 0.68)
    explicit_peak = max(weights[field] for field in RELATIONSHIP_WEIGHT_FIELDS if field != "emotional_importance")
    weights["emotional_importance"] = max(weights["emotional_importance"], min(1.0, explicit_peak * 0.92))
    return weights


def format_story_state_for_prompt(
    session_id: str,
    *,
    relevance_text: str = "",
    mark_used: bool = False,
) -> tuple[str, int]:
    context = load_prompt_state_context(session_id)
    sections: list[str] = []
    item_count = 0

    live_by_character: dict[str, list[dict[str, Any]]] = {}
    for item in context["character_live_state"]:
        name = clean_text(item.get("character_name"), 160) or "Unknown"
        live_by_character.setdefault(name, []).append(item)

    character_blocks: list[str] = []
    for character in context["active_characters"]:
        name = clean_text(character.get("name"), 160)
        if not name:
            continue
        base_bits = [
            clean_text(character.get("role"), 180),
            clean_text(character.get("personality"), 220),
            clean_text(character.get("appearance"), 260),
        ]
        base = "; ".join(bit for bit in base_bits if bit)
        live_items = live_by_character.get(name, [])
        live_lines = []
        for item in sorted(live_items, key=character_state_prompt_sort_key)[: PROMPT_STATE_LIMITS["character_live"]]:
            key = character_state_prompt_label(item)
            value = clean_text(item.get("value"), 220)
            if key and value:
                marker = " (tentative)" if item.get("is_tentative") else ""
                live_lines.append(f"{key}: {value}{marker}")
        line_parts = [f"{name}:"]
        if base:
            line_parts.append(f"Base: {base}.")
        if live_lines:
            line_parts.append(f"Live: {'; '.join(live_lines)}.")
            item_count += len(live_lines)
        elif clean_text(character.get("current_state")):
            line_parts.append(f"Live: {clean_text(character.get('current_state'), 260)}.")
        character_blocks.append(" ".join(line_parts))
    if character_blocks:
        sections.extend(["ACTIVE CHARACTERS:", *character_blocks, ""])

    relationship_lines = []
    for item in context["relationships"][: PROMPT_STATE_LIMITS["relationships"]]:
        pair = " / ".join(
            part
            for part in [
                clean_text(item.get("character_a_name"), 80),
                clean_text(item.get("character_b_name"), 80),
            ]
            if part
        )
        content = clean_text(item.get("content"), 260)
        if pair and content:
            salience = relationship_salience_parts(item)
            suffix = f" ({'; '.join(salience)})" if salience else ""
            relationship_lines.append(f"- {pair}: {content}{suffix}")
    if relationship_lines:
        item_count += len(relationship_lines)
        sections.extend(["RELATIONSHIPS:", *relationship_lines, ""])

    emotional_lines = []
    selected_emotional = select_emotional_memories(context, relevance_text=relevance_text)
    for item in selected_emotional:
        memory_text = clean_text(item.get("memory_text"), 320)
        if not memory_text:
            continue
        related = safe_json_list(item.get("related_characters_json"), limit=6)
        themes = safe_json_list(item.get("themes_json"), limit=5)
        tags = []
        memory_type = normalize_key(item.get("memory_type"), "emotional").replace("_", " ")
        if memory_type:
            tags.append(memory_type)
        if related:
            tags.append("related: " + ", ".join(related))
        if themes:
            tags.append("themes: " + ", ".join(themes))
        if item.get("manually_pinned"):
            tags.append("pinned")
        suffix = f" ({'; '.join(tags[:4])})" if tags else ""
        emotional_lines.append(f"- {memory_text}{suffix}")
    if emotional_lines:
        item_count += len(emotional_lines)
        sections.extend(["EMOTIONAL CONTINUITY:", *emotional_lines, ""])
        if mark_used:
            mark_emotional_memories_used([item["id"] for item in selected_emotional if item.get("id")])

    scene_lines = []
    for item in context["scene_state"][: PROMPT_STATE_LIMITS["scene"]]:
        key = clean_text(item.get("key"), 80).replace("_", " ")
        value = clean_text(item.get("value"), 260)
        if key and value:
            scene_lines.append(f"- {key}: {value}")
    if scene_lines:
        item_count += len(scene_lines)
        sections.extend(["CURRENT SCENE:", *scene_lines, ""])

    world_lines = []
    for field, value in context["world_notes"].items():
        if clean_text(value):
            world_lines.append(f"- {field.replace('_', ' ')}: {clean_text(value, 260)}")
    for item in context["world_state"][: PROMPT_STATE_LIMITS["world"]]:
        key = clean_text(item.get("key"), 80).replace("_", " ")
        value = clean_text(item.get("value"), 260)
        if key and value:
            world_lines.append(f"- {key}: {value}")
    if world_lines:
        item_count += len(world_lines)
        sections.extend(["WORLD STATE:", *world_lines[:14], ""])

    object_lines = []
    for item in context["objects"][: PROMPT_STATE_LIMITS["objects"]]:
        name = clean_text(item.get("name") or item.get("object_key"), 120)
        value = clean_text(item.get("value"), 260)
        owner = clean_text(item.get("owner_character_name"), 120)
        holder = clean_text(item.get("holder_character_name"), 120)
        location = clean_text(item.get("current_location"), 160)
        if name and value:
            details = [f"owner: {owner}" if owner else "", f"holder: {holder}" if holder else "", f"at: {location}" if location else ""]
            suffix = f" ({'; '.join(part for part in details if part)})" if any(details) else ""
            object_lines.append(f"- {name}: {value}{suffix}")
    if object_lines:
        item_count += len(object_lines)
        sections.extend(["IMPORTANT OBJECTS:", *object_lines, ""])

    thread_lines = []
    for item in context["plot_threads"][: PROMPT_STATE_LIMITS["plot_threads"]]:
        title = clean_text(item.get("title") or item.get("thread_key"), 140)
        content = clean_text(item.get("content"), 300)
        if title:
            thread_lines.append(f"- {title}: {content or 'active'}")
    if thread_lines:
        item_count += len(thread_lines)
        sections.extend(["ACTIVE PLOT THREADS:", *thread_lines, ""])

    if not sections:
        return "", 0
    return "\n".join(["CURRENT STORY STATE:", "", *sections]).strip(), item_count


def format_visual_story_state_for_prompt(session_id: str, *, relevance_text: str = "") -> str:
    context = load_prompt_state_context(session_id)
    lines: list[str] = []
    live_by_character: dict[str, list[str]] = {}
    for item in context["character_live_state"]:
        key = clean_text(item.get("key"), 80).replace("_", " ")
        value = clean_text(item.get("value"), 220)
        if not key or not value:
            continue
        name = clean_text(item.get("character_name"), 120) or "Unknown"
        live_by_character.setdefault(name, []).append(f"{key}: {value}")
    for character in context["active_characters"]:
        name = clean_text(character.get("name"), 120)
        if not name:
            continue
        bits = [
            clean_text(character.get("appearance"), 240),
            clean_text(character.get("image_prompt"), 240),
            clean_text(character.get("lora_trigger"), 120),
        ]
        bits.extend(live_by_character.get(name, [])[:8])
        compact = "; ".join(bit for bit in bits if bit)
        if compact:
            lines.append(f"{name}: {compact}")
    for item in context["scene_state"][:6]:
        if clean_text(item.get("value")):
            lines.append(f"Scene {clean_text(item.get('key'), 80).replace('_', ' ')}: {clean_text(item.get('value'), 240)}")
    for item in context["objects"][:8]:
        name = clean_text(item.get("name") or item.get("object_key"), 120)
        value = clean_text(item.get("value"), 220)
        owner = clean_text(item.get("owner_character_name"), 80)
        if name and value:
            lines.append(f"Object {name}: {value}{f'; owner {owner}' if owner else ''}")
    world_bits = []
    for field in ("tone", "setting", "locations"):
        if clean_text(context["world_notes"].get(field)):
            world_bits.append(clean_text(context["world_notes"].get(field), 220))
    for item in context["world_state"][:5]:
        if clean_text(item.get("value")):
            world_bits.append(clean_text(item.get("value"), 220))
    if world_bits:
        lines.append(f"World visual tone/state: {'; '.join(world_bits[:8])}")
    emotional_visuals = []
    for item in select_emotional_memories(context, relevance_text=relevance_text, limit=2):
        memory_text = clean_text(item.get("memory_text"), 180)
        if memory_text:
            emotional_visuals.append(memory_text)
    if emotional_visuals:
        lines.append("Emotional visual context: " + "; ".join(emotional_visuals))
    return "\n".join(lines[:24])


def resolve_state_item_table(item_type: str) -> tuple[str, str]:
    normalized = normalize_key(item_type)
    table_map = {
        "character": "character_live_state",
        "character_live_state": "character_live_state",
        "relationship": "relationship_state",
        "relationship_state": "relationship_state",
        "emotional": "emotional_memories",
        "emotional_memory": "emotional_memories",
        "emotional_memories": "emotional_memories",
        "world": "world_live_state",
        "world_live_state": "world_live_state",
        "scene": "scene_live_state",
        "scene_live_state": "scene_live_state",
        "object": "object_state",
        "object_state": "object_state",
        "plot": "plot_threads",
        "plot_thread": "plot_threads",
        "plot_threads": "plot_threads",
    }
    table = table_map.get(normalized)
    if not table:
        raise HTTPException(status_code=400, detail="Unknown story-state item type.")
    return normalized, table


def state_row_label(table: str, row) -> tuple[str, str, str]:
    if table == "character_live_state":
        return row["session_id"], row["key"], row["value"] or ""
    if table == "relationship_state":
        return row["session_id"], row["relationship_key"], row["content"] or ""
    if table == "emotional_memories":
        return row["session_id"], row["memory_type"], row["memory_text"] or ""
    if table == "world_live_state":
        return row["session_id"], row["key"], row["value"] or ""
    if table == "scene_live_state":
        return row["session_id"], row["key"], row["value"] or ""
    if table == "object_state":
        return row["session_id"], row["object_key"], row["value"] or ""
    return row["session_id"], row["thread_key"], row["content"] or row["status"] or ""


def insert_manual_state_event(db, *, table: str, row, action: str, value: str, previous_value: str | None = None) -> None:
    session_id, key, _old_value = state_row_label(table, row)
    character_id = row["character_id"] if "character_id" in row.keys() else None
    character_name = row["character_name"] if "character_name" in row.keys() else ""
    if table == "relationship_state":
        character_name = " / ".join(
            part for part in [row["character_a_name"], row["character_b_name"]] if part
        )
    elif table == "emotional_memories":
        character_name = ", ".join(safe_json_list(row["related_characters_json"], limit=4)) or "Story"
    elif table == "object_state":
        character_name = row["name"] or row["object_key"]
    elif table == "plot_threads":
        character_name = row["title"] or row["thread_key"]
    db.execute(
        """
        INSERT INTO character_state_events (
            id, session_id, character_id, character_name, state_type, key, value,
            previous_value, action, confidence, run_id, source_scene_id, source_version_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
        """,
        (
            str(uuid4()),
            session_id,
            character_id,
            character_name or "Story State",
            "manual_review",
            key,
            value,
            previous_value,
            action,
            confidence_value(row["confidence"] if "confidence" in row.keys() else None),
            row["source_scene_id"] if "source_scene_id" in row.keys() else None,
            row["source_version_id"] if "source_version_id" in row.keys() else None,
        ),
    )


def set_story_state_item_status(item_type: str, item_id: str, status: str) -> StoryStateItemActionResponse:
    normalized, table = resolve_state_item_table(item_type)
    action = normalize_key(status)
    if action not in {"active", "disable", "disabled", "archive", "archived", "restore"}:
        raise HTTPException(status_code=400, detail="Unknown state item action.")
    with db_session() as db:
        row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Story-state item not found.")
        session_id = row["session_id"]
        previous = state_row_label(table, row)[2]
        if action in {"disable", "disabled"}:
            if table == "plot_threads":
                db.execute("UPDATE plot_threads SET disabled = 1, review_status = 'disabled' WHERE id = ?", (item_id,))
            else:
                db.execute(f"UPDATE {table} SET disabled = 1, review_status = 'disabled' WHERE id = ?", (item_id,))
            final_action = "disabled"
        elif action in {"archive", "archived"}:
            if table == "plot_threads":
                db.execute("UPDATE plot_threads SET status = 'archived', disabled = 0, review_status = 'archived' WHERE id = ?", (item_id,))
            else:
                db.execute(f"UPDATE {table} SET archived = 1, disabled = 0, review_status = 'archived' WHERE id = ?", (item_id,))
            final_action = "archived"
        else:
            if table == "plot_threads":
                db.execute("UPDATE plot_threads SET status = 'active', disabled = 0, review_status = 'ok' WHERE id = ?", (item_id,))
            else:
                db.execute(f"UPDATE {table} SET archived = 0, disabled = 0, review_status = 'ok' WHERE id = ?", (item_id,))
            final_action = "active"
        insert_manual_state_event(db, table=table, row=row, action=f"manual_{final_action}", value=previous, previous_value=previous)
    return StoryStateItemActionResponse(ok=True, item_type=normalized, item_id=item_id, action=final_action, session_id=session_id)


def archive_story_state_item(item_type: str, item_id: str) -> StoryStateItemActionResponse:
    return set_story_state_item_status(item_type, item_id, "archived")


def disable_story_state_item(item_type: str, item_id: str) -> StoryStateItemActionResponse:
    return set_story_state_item_status(item_type, item_id, "disabled")


def restore_story_state_item(item_type: str, item_id: str) -> StoryStateItemActionResponse:
    return set_story_state_item_status(item_type, item_id, "active")


def update_story_state_item(item_type: str, item_id: str, patch: StoryStateItemUpdate) -> StoryStateItemActionResponse:
    normalized, table = resolve_state_item_table(item_type)
    payload = patch.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail="No story-state changes were provided.")
    allowed_columns_by_table = {
        "character_live_state": {"value", "confidence", "archived", "disabled", "manual_override", "review_status"},
        "relationship_state": {
            "content",
            "confidence",
            "archived",
            "disabled",
            "manual_override",
            "manually_pinned",
            "review_status",
            "relationship_type",
            *RELATIONSHIP_WEIGHT_FIELDS,
        },
        "emotional_memories": {
            "memory_text",
            "content",
            "confidence",
            "archived",
            "disabled",
            "manual_override",
            "manually_pinned",
            "review_status",
            "memory_type",
            "emotional_weight",
            "themes_json",
            "related_characters_json",
            "trigger_conditions",
            "cooldown_scenes",
        },
        "world_live_state": {"value", "confidence", "archived", "disabled", "manual_override", "review_status"},
        "scene_live_state": {"value", "confidence", "archived", "disabled", "manual_override", "review_status"},
        "object_state": {
            "value", "owner_character_name", "holder_character_name", "current_location",
            "placement_state", "condition", "visibility", "importance", "confidence",
            "archived", "disabled", "manual_override", "review_status",
        },
        "plot_threads": {"content", "status", "title", "confidence", "disabled", "manual_override", "review_status"},
    }
    if "value" in payload and table in {"relationship_state", "plot_threads"} and "content" not in payload:
        payload["content"] = payload.pop("value")
    if "value" in payload and table == "emotional_memories" and "memory_text" not in payload:
        payload["memory_text"] = payload.pop("value")
    if "content" in payload and table not in {"relationship_state", "plot_threads"} and "value" not in payload:
        if table == "emotional_memories":
            payload["memory_text"] = payload.pop("content")
        else:
            payload["value"] = payload.pop("content")
    if "themes" in payload and table == "emotional_memories":
        payload["themes_json"] = json_list_text(payload.pop("themes"), limit=10)
    if "related_characters" in payload and table == "emotional_memories":
        payload["related_characters_json"] = json_list_text(payload.pop("related_characters"), limit=10)
    if "status" in payload and table != "plot_threads":
        payload.pop("status", None)
    if "title" in payload and table != "plot_threads":
        payload.pop("title", None)
    allowed = allowed_columns_by_table[table]
    updates: dict[str, Any] = {}
    content_changed = False
    for key, value in payload.items():
        if key not in allowed:
            continue
        if key in {"archived", "disabled", "manual_override", "manually_pinned"}:
            updates[key] = 1 if value else 0
        elif key in {"confidence", "emotional_weight", "importance", *RELATIONSHIP_WEIGHT_FIELDS} and value is not None:
            updates[key] = confidence_value(value)
        elif key == "relationship_type":
            updates[key] = relationship_type_value(value)
        elif key == "memory_type":
            updates[key] = normalize_key(value or "emotional", "emotional")[:80]
        elif key == "cooldown_scenes" and value is not None:
            updates[key] = max(0, min(50, int(value)))
        elif key == "review_status":
            updates[key] = normalize_key(value or "manual", "manual")[:80]
        elif key == "status":
            next_status = normalize_key(value or "active")
            updates[key] = next_status if next_status in {"active", "resolved", "archived"} else "active"
        else:
            updates[key] = clean_text(value, 12000)
        if key in {"value", "content", "memory_text", "owner_character_name", "status", "title"}:
            content_changed = True
    if content_changed and "manual_override" not in updates:
        updates["manual_override"] = 1
    if content_changed and "review_status" not in updates:
        updates["review_status"] = "manual"
    if not updates:
        raise HTTPException(status_code=400, detail="No supported story-state changes were provided.")
    with db_session() as db:
        row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Story-state item not found.")
        previous = state_row_label(table, row)[2]
        assignments = ", ".join(f"{key} = ?" for key in updates)
        db.execute(f"UPDATE {table} SET {assignments} WHERE id = ?", (*updates.values(), item_id))
        value = str(updates.get("value") or updates.get("content") or updates.get("memory_text") or previous)
        insert_manual_state_event(db, table=table, row=row, action="manual_edit", value=value, previous_value=previous)
        session_id = row["session_id"]
    return StoryStateItemActionResponse(ok=True, item_type=normalized, item_id=item_id, action="updated", session_id=session_id)


def undo_story_state_run(run_id: str) -> StoryStateItemActionResponse:
    with db_session() as db:
        run = db.execute("SELECT * FROM story_state_runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise HTTPException(status_code=404, detail="Story-state extraction run not found.")
        session_id = run["session_id"]
        scene_id = run["scene_id"]
        version_id = run["version_id"]
        affected = 0
        for table in ("character_live_state", "relationship_state", "emotional_memories", "world_live_state", "scene_live_state", "object_state"):
            cursor = db.execute(
                f"""
                UPDATE {table}
                SET archived = 1, disabled = 0, review_status = 'undone'
                WHERE session_id = ?
                  AND source_scene_id = ?
                  AND COALESCE(source_version_id, '') = COALESCE(?, '')
                  AND manual_override = 0
                  AND archived = 0
                """,
                (session_id, scene_id, version_id),
            )
            affected += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        cursor = db.execute(
            """
            UPDATE plot_threads
            SET status = 'archived', disabled = 0, review_status = 'undone'
            WHERE session_id = ?
              AND source_scene_id = ?
              AND COALESCE(source_version_id, '') = COALESCE(?, '')
              AND manual_override = 0
              AND status != 'archived'
            """,
            (session_id, scene_id, version_id),
        )
        affected += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        db.execute(
            """
            UPDATE story_state_runs
            SET status = 'undone',
                warnings_json = ?,
                completed_at = COALESCE(completed_at, ?)
            WHERE id = ?
            """,
            (json.dumps([f"Undo archived {affected} non-manual state item(s)."], ensure_ascii=False), utc_now(), run_id),
        )
        db.execute(
            """
            INSERT INTO character_state_events (
                id, session_id, character_name, state_type, key, value,
                previous_value, action, confidence, run_id, source_scene_id, source_version_id
            )
            VALUES (?, ?, 'Story State', 'manual_review', 'undo_extraction', ?, NULL, 'manual_undo_run', 1, ?, ?, ?)
            """,
            (str(uuid4()), session_id, f"Archived {affected} item(s) from extraction run.", run_id, scene_id, version_id),
        )
    return StoryStateItemActionResponse(ok=True, action="undone", session_id=session_id, affected_count=affected)


def split_ids(value: str | None) -> list[str]:
    if not value:
        return []
    return [item for item in value.split(",") if item]


def injury_body_key(key: str, value: str) -> str:
    text = normalize_key(f"{key} {value}")
    for token in ("injury", "wound", "bandage", "bandaged", "scar", "fresh", "healed", "healing", "open", "bleeding"):
        text = text.replace(token, "")
    text = re.sub(r"_+", "_", text).strip("_")
    for side in ("left", "right"):
        for part in ("forearm", "arm", "shoulder", "hand", "leg", "thigh", "cheek", "face", "rib", "side"):
            if side in text and part in text:
                return f"{side}_{part}"
    for part in ("forearm", "arm", "shoulder", "hand", "leg", "thigh", "cheek", "face", "rib", "side"):
        if part in text:
            return part
    return text or normalize_key(key)


def injury_values_conflict(values: list[str]) -> bool:
    normalized = " | ".join(value.lower() for value in values)
    for first, second in INJURY_CONFLICT_HINTS:
        if first in normalized and second in normalized:
            return True
    return False


def detect_story_state_conflicts(session_id: str) -> list[StoryStateConflictRead]:
    conflicts: list[StoryStateConflictRead] = []
    with db_session() as db:
        character_conflicts = db.execute(
            """
            SELECT
                lower(character_name) AS character_key,
                character_name,
                key,
                GROUP_CONCAT(id) AS ids,
                COUNT(DISTINCT lower(value)) AS value_count
            FROM character_live_state
            WHERE session_id = ?
              AND COALESCE(archived, 0) = 0
              AND COALESCE(disabled, 0) = 0
              AND lower(key) IN (
                'current_outfit', 'outfit', 'clothing',
                'current_location', 'location', 'current_place',
                'room_position', 'position', 'spatial_position', 'blocking_position',
                'current_posture', 'current_action'
              )
            GROUP BY lower(character_name), lower(key)
            HAVING value_count > 1
            """,
            (session_id,),
        ).fetchall()
        for row in character_conflicts:
            conflicts.append(
                StoryStateConflictRead(
                    id=f"character-{row['character_key']}-{row['key']}",
                    type="character",
                    severity="medium",
                    message=f"{row['character_name'] or 'A character'} has multiple active {str(row['key']).replace('_', ' ')} values.",
                    item_ids=split_ids(row["ids"]),
                    suggested_actions=["keep newest", "keep old", "edit manually", "archive one"],
                )
            )
        object_conflicts = db.execute(
            """
            SELECT
                COALESCE(NULLIF(lower(name), ''), lower(object_key)) AS object_identity,
                MAX(object_key) AS object_key,
                MAX(name) AS name,
                GROUP_CONCAT(id) AS ids,
                COUNT(DISTINCT lower(holder_character_name)) AS owner_count
            FROM object_state
            WHERE session_id = ?
              AND COALESCE(archived, 0) = 0
              AND COALESCE(disabled, 0) = 0
              AND holder_character_name != ''
            GROUP BY object_identity
            HAVING owner_count > 1
            """,
            (session_id,),
        ).fetchall()
        for row in object_conflicts:
            conflicts.append(
                StoryStateConflictRead(
                    id=f"object-owner-{row['object_key']}",
                    type="object",
                    severity="medium",
                    message=f"{row['name'] or row['object_key']} has multiple active holders.",
                    item_ids=split_ids(row["ids"]),
                    suggested_actions=["keep newest", "edit manually", "archive one"],
                )
            )
        object_location_conflicts = db.execute(
            """
            SELECT
                COALESCE(NULLIF(lower(name), ''), lower(object_key)) AS object_identity,
                MAX(object_key) AS object_key,
                MAX(name) AS name,
                GROUP_CONCAT(id) AS ids,
                COUNT(DISTINCT lower(value)) AS location_count
            FROM object_state
            WHERE session_id = ?
              AND COALESCE(archived, 0) = 0
              AND COALESCE(disabled, 0) = 0
              AND lower(state_type) IN ('location', 'visibility')
            GROUP BY object_identity, lower(state_type)
            HAVING location_count > 1
            """,
            (session_id,),
        ).fetchall()
        for row in object_location_conflicts:
            conflicts.append(
                StoryStateConflictRead(
                    id=f"object-location-{row['object_key']}",
                    type="object",
                    severity="medium",
                    message=f"{row['name'] or row['object_key']} has multiple active location/visibility values.",
                    item_ids=split_ids(row["ids"]),
                    suggested_actions=["keep newest", "edit manually", "archive stale location"],
                )
            )
        injury_rows = db.execute(
            """
            SELECT id, character_name, key, value
            FROM character_live_state
            WHERE session_id = ?
              AND COALESCE(archived, 0) = 0
              AND COALESCE(disabled, 0) = 0
              AND (
                lower(key) LIKE '%injury%'
                OR lower(key) LIKE '%wound%'
                OR lower(key) LIKE '%scar%'
                OR lower(value) LIKE '%injur%'
                OR lower(value) LIKE '%wound%'
                OR lower(value) LIKE '%scar%'
                OR lower(value) LIKE '%bandage%'
                OR lower(value) LIKE '%healed%'
              )
            ORDER BY character_name ASC, updated_at DESC
            LIMIT 120
            """,
            (session_id,),
        ).fetchall()
        injury_groups: dict[tuple[str, str], list[dict[str, str]]] = {}
        for row in injury_rows:
            character = clean_text(row["character_name"], 120) or "Unknown"
            body_key = injury_body_key(row["key"], row["value"])
            injury_groups.setdefault((character.lower(), body_key), []).append(
                {
                    "id": row["id"],
                    "character_name": character,
                    "key": row["key"],
                    "value": row["value"],
                }
            )
        for (_character_key, body_key), items in injury_groups.items():
            values = [item["value"] for item in items]
            if len(items) > 1 and injury_values_conflict(values):
                character_name = items[0]["character_name"]
                conflicts.append(
                    StoryStateConflictRead(
                        id=f"injury-{normalize_key(character_name)}-{body_key}",
                        type="character",
                        severity="medium",
                        message=f"{character_name} has conflicting injury/healing state for {body_key.replace('_', ' ')}.",
                        item_ids=[item["id"] for item in items],
                        suggested_actions=["keep newest", "edit manually", "archive stale injury"],
                    )
                )
        scene_conflict = db.execute(
            """
            SELECT GROUP_CONCAT(id) AS ids, COUNT(DISTINCT lower(value)) AS location_count
            FROM scene_live_state
            WHERE session_id = ?
              AND COALESCE(archived, 0) = 0
              AND COALESCE(disabled, 0) = 0
              AND lower(key) IN ('current_location', 'location', 'current_scene', 'current_place', 'setting')
            HAVING location_count > 1
            """,
            (session_id,),
        ).fetchone()
        if scene_conflict and scene_conflict["location_count"] and int(scene_conflict["location_count"]) > 1:
            conflicts.append(
                StoryStateConflictRead(
                    id="scene-current-location",
                    type="scene",
                    severity="medium",
                    message="The current scene/location has multiple active values.",
                    item_ids=split_ids(scene_conflict["ids"]),
                    suggested_actions=["keep newest", "edit manually", "archive one"],
                )
            )
        plot_conflicts = db.execute(
            """
            SELECT thread_key, title, GROUP_CONCAT(id) AS ids
            FROM plot_threads
            WHERE session_id = ?
              AND COALESCE(disabled, 0) = 0
              AND status IN ('active', 'resolved')
            GROUP BY lower(thread_key)
            HAVING SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) > 0
               AND SUM(CASE WHEN status = 'resolved' THEN 1 ELSE 0 END) > 0
            """,
            (session_id,),
        ).fetchall()
        for row in plot_conflicts:
            conflicts.append(
                StoryStateConflictRead(
                    id=f"plot-status-{row['thread_key']}",
                    type="plot_thread",
                    severity="medium",
                    message=f"{row['title'] or row['thread_key']} is both active and resolved.",
                    item_ids=split_ids(row["ids"]),
                    suggested_actions=["keep active", "keep resolved", "edit manually"],
                )
            )
    return conflicts[:40]


def row_to_run(row) -> StoryStateRunRead:
    status = row["status"]
    error = row["error"]
    warnings = json_array(row["warnings_json"])
    if is_stale_running_state_run(row):
        status = "stale"
        error = error or stale_state_run_warning()
        if stale_state_run_warning() not in warnings:
            warnings.append(stale_state_run_warning())
    return StoryStateRunRead(
        id=row["id"],
        session_id=row["session_id"],
        scene_id=row["scene_id"],
        version_id=row["version_id"],
        status=status,
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        error=error,
        warnings=warnings,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_character_state(row) -> CharacterLiveStateRead:
    return CharacterLiveStateRead(
        id=row["id"],
        session_id=row["session_id"],
        character_id=row["character_id"],
        character_name=row["character_name"] or "",
        state_type=row["state_type"] or "general",
        key=row["key"],
        value=row["value"] or "",
        previous_value=row["previous_value"],
        confidence=float(row["confidence"] or 0),
        is_tentative=bool(row["is_tentative"]),
        archived=bool(row["archived"]),
        disabled=bool(row["disabled"]),
        manual_override=bool(row["manual_override"]),
        review_status=row["review_status"] or "ok",
        source_scene_id=row["source_scene_id"],
        source_version_id=row["source_version_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_relationship(row) -> RelationshipStateRead:
    return RelationshipStateRead(
        id=row["id"],
        session_id=row["session_id"],
        character_a_name=row["character_a_name"] or "",
        character_b_name=row["character_b_name"] or "",
        relationship_type=row["relationship_type"] or "unknown",
        relationship_key=row["relationship_key"] or "relationship",
        content=row["content"] or "",
        closeness_weight=float(row["closeness_weight"] or 0),
        trust_weight=float(row["trust_weight"] or 0),
        conflict_weight=float(row["conflict_weight"] or 0),
        protective_weight=float(row["protective_weight"] or 0),
        grief_weight=float(row["grief_weight"] or 0),
        romantic_weight=float(row["romantic_weight"] or 0),
        family_weight=float(row["family_weight"] or 0),
        betrayal_weight=float(row["betrayal_weight"] or 0),
        respect_weight=float(row["respect_weight"] or 0),
        fear_weight=float(row["fear_weight"] or 0),
        emotional_importance=float(row["emotional_importance"] or 0),
        last_reinforced_scene_id=row["last_reinforced_scene_id"],
        confidence=float(row["confidence"] or 0),
        is_tentative=bool(row["is_tentative"]),
        archived=bool(row["archived"]),
        disabled=bool(row["disabled"]),
        manual_override=bool(row["manual_override"]),
        manually_pinned=bool(row["manually_pinned"]),
        review_status=row["review_status"] or "ok",
        source_scene_id=row["source_scene_id"],
        source_version_id=row["source_version_id"],
        updated_at=row["updated_at"],
    )


def row_to_emotional_memory(row) -> EmotionalMemoryRead:
    return EmotionalMemoryRead(
        id=row["id"],
        session_id=row["session_id"],
        memory_type=row["memory_type"] or "emotional",
        memory_text=row["memory_text"] or "",
        emotional_weight=float(row["emotional_weight"] or 0),
        themes=safe_json_list(row["themes_json"], limit=12),
        related_characters=safe_json_list(row["related_characters_json"], limit=12),
        trigger_conditions=row["trigger_conditions"] or "",
        last_used_in_prompt=row["last_used_in_prompt"],
        use_count=int(row["use_count"] or 0),
        cooldown_scenes=int(row["cooldown_scenes"] or 0),
        confidence=float(row["confidence"] or 0),
        is_tentative=bool(row["is_tentative"]),
        archived=bool(row["archived"]),
        disabled=bool(row["disabled"]),
        manual_override=bool(row["manual_override"]),
        manually_pinned=bool(row["manually_pinned"]),
        review_status=row["review_status"] or "ok",
        source_scene_id=row["source_scene_id"],
        source_version_id=row["source_version_id"],
        updated_at=row["updated_at"],
    )


def row_to_world_state(row) -> WorldLiveStateRead:
    return WorldLiveStateRead(
        id=row["id"],
        session_id=row["session_id"],
        state_type=row["state_type"] or "world",
        key=row["key"],
        value=row["value"] or "",
        confidence=float(row["confidence"] or 0),
        is_tentative=bool(row["is_tentative"]),
        archived=bool(row["archived"]),
        disabled=bool(row["disabled"]),
        manual_override=bool(row["manual_override"]),
        review_status=row["review_status"] or "ok",
        source_scene_id=row["source_scene_id"],
        source_version_id=row["source_version_id"],
        updated_at=row["updated_at"],
    )


def row_to_scene_state(row) -> SceneLiveStateRead:
    return SceneLiveStateRead(
        id=row["id"],
        session_id=row["session_id"],
        scene_id=row["scene_id"],
        version_id=row["version_id"],
        state_type=row["state_type"] or "scene",
        key=row["key"],
        value=row["value"] or "",
        confidence=float(row["confidence"] or 0),
        is_tentative=bool(row["is_tentative"]),
        archived=bool(row["archived"]),
        disabled=bool(row["disabled"]),
        manual_override=bool(row["manual_override"]),
        review_status=row["review_status"] or "ok",
        updated_at=row["updated_at"],
    )


def row_to_object_state(row) -> ObjectStateRead:
    return ObjectStateRead(
        id=row["id"],
        session_id=row["session_id"],
        object_key=row["object_key"],
        name=row["name"] or "",
        state_type=row["state_type"] or "object",
        value=row["value"] or "",
        owner_character_name=row["owner_character_name"] or "",
        holder_character_name=row["holder_character_name"] or "",
        current_location=row["current_location"] or "",
        placement_state=row["placement_state"] or "unknown",
        condition=row["condition"] or "",
        visibility=row["visibility"] or "unknown",
        importance=float(row["importance"] or 0),
        confidence=float(row["confidence"] or 0),
        is_tentative=bool(row["is_tentative"]),
        archived=bool(row["archived"]),
        disabled=bool(row["disabled"]),
        manual_override=bool(row["manual_override"]),
        review_status=row["review_status"] or "ok",
        source_scene_id=row["source_scene_id"],
        source_version_id=row["source_version_id"],
        updated_at=row["updated_at"],
    )


def row_to_plot_thread(row) -> PlotThreadRead:
    return PlotThreadRead(
        id=row["id"],
        session_id=row["session_id"],
        thread_key=row["thread_key"],
        title=row["title"] or "",
        status=row["status"] or "active",
        content=row["content"] or "",
        confidence=float(row["confidence"] or 0),
        is_tentative=bool(row["is_tentative"]),
        disabled=bool(row["disabled"]),
        manual_override=bool(row["manual_override"]),
        review_status=row["review_status"] or "ok",
        source_scene_id=row["source_scene_id"],
        source_version_id=row["source_version_id"],
        resolved_scene_id=row["resolved_scene_id"],
        resolved_version_id=row["resolved_version_id"],
        updated_at=row["updated_at"],
    )


def row_to_event(row) -> CharacterStateEventRead:
    return CharacterStateEventRead(
        id=row["id"],
        session_id=row["session_id"],
        character_id=row["character_id"],
        character_name=row["character_name"] or "",
        state_type=row["state_type"] or "general",
        key=row["key"],
        value=row["value"] or "",
        previous_value=row["previous_value"],
        action=row["action"] or "update",
        confidence=float(row["confidence"] or 0),
        source_scene_id=row["source_scene_id"],
        source_version_id=row["source_version_id"],
        created_at=row["created_at"],
    )


def load_scene_version_text(session_id: str, scene_id: str, version_id: str | None) -> tuple[str, str, str | None]:
    with db_session() as db:
        scene = db.execute(
            "SELECT id, director_note, generated_text FROM scenes WHERE id = ? AND session_id = ?",
            (scene_id, session_id),
        ).fetchone()
        if scene is None:
            raise HTTPException(status_code=404, detail="Scene not found.")
        if version_id:
            version = db.execute(
                """
                SELECT id, director_note, generated_text
                FROM scene_versions
                WHERE id = ? AND scene_id = ? AND session_id = ?
                """,
                (version_id, scene_id, session_id),
            ).fetchone()
            if version is None:
                raise HTTPException(status_code=404, detail="Scene version not found.")
            return version["generated_text"] or "", version["director_note"] or scene["director_note"] or "", version["id"]
        version = db.execute(
            """
            SELECT id, director_note, generated_text
            FROM scene_versions
            WHERE scene_id = ? AND session_id = ?
            ORDER BY version_index DESC
            LIMIT 1
            """,
            (scene_id, session_id),
        ).fetchone()
        if version:
            return version["generated_text"] or "", version["director_note"] or scene["director_note"] or "", version["id"]
        return scene["generated_text"] or "", scene["director_note"] or "", None


def load_extraction_context(session_id: str, scene_id: str, version_id: str | None) -> dict[str, Any]:
    scene_text, director_note, resolved_version_id = load_scene_version_text(session_id, scene_id, version_id)
    story_foundation = render_story_foundation_for_prompt(session_id, limit=3600)
    with db_session() as db:
        character_rows = db.execute(
            """
            SELECT
                c.id, c.name, c.role, c.personality, c.appearance, c.relationships,
                c.current_state, c.image_prompt, c.lora_trigger
            FROM session_characters sc
            JOIN characters c ON c.id = sc.character_id
            WHERE sc.session_id = ? AND sc.is_active = 1
            ORDER BY lower(c.name) ASC
            """,
            (session_id,),
        ).fetchall()
        world_notes = db.execute(
            """
            SELECT setting, tone, rules, locations, factions, conflicts, history
            FROM world_notes
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        summary = db.execute(
            """
            SELECT summary_text
            FROM session_summaries
            WHERE session_id = ?
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        live_rows = db.execute(
            """
            SELECT character_name, state_type, key, value, confidence
            FROM character_live_state
            WHERE session_id = ? AND archived = 0 AND disabled = 0
            ORDER BY updated_at DESC
            LIMIT 80
            """,
            (session_id,),
        ).fetchall()
        relationship_rows = db.execute(
            """
            SELECT character_a_name, character_b_name, relationship_type, relationship_key, content,
                   closeness_weight, trust_weight, conflict_weight, protective_weight, grief_weight,
                   romantic_weight, family_weight, betrayal_weight, respect_weight, fear_weight,
                   emotional_importance, manually_pinned, confidence
            FROM relationship_state
            WHERE session_id = ? AND archived = 0 AND disabled = 0
            ORDER BY emotional_importance DESC, updated_at DESC
            LIMIT 40
            """,
            (session_id,),
        ).fetchall()
        emotional_rows = db.execute(
            """
            SELECT memory_type, memory_text, emotional_weight, themes_json,
                   related_characters_json, trigger_conditions, confidence, manually_pinned
            FROM emotional_memories
            WHERE session_id = ? AND archived = 0 AND disabled = 0
            ORDER BY manually_pinned DESC, emotional_weight DESC, updated_at DESC
            LIMIT 20
            """,
            (session_id,),
        ).fetchall()
        object_rows = db.execute(
            """
            SELECT object_key, name, state_type, value, owner_character_name, holder_character_name,
                   current_location, placement_state, condition, visibility, importance, confidence
            FROM object_state
            WHERE session_id = ? AND archived = 0 AND disabled = 0
            ORDER BY updated_at DESC
            LIMIT 40
            """,
            (session_id,),
        ).fetchall()
        scene_state_rows = db.execute(
            """
            SELECT state_type, key, value, confidence
            FROM scene_live_state
            WHERE session_id = ? AND archived = 0 AND disabled = 0
            ORDER BY updated_at DESC
            LIMIT 30
            """,
            (session_id,),
        ).fetchall()
        plot_rows = db.execute(
            """
            SELECT thread_key, title, status, content, confidence
            FROM plot_threads
            WHERE session_id = ? AND status != 'archived' AND disabled = 0
            ORDER BY updated_at DESC
            LIMIT 40
            """,
            (session_id,),
        ).fetchall()

    return {
        "scene_text": scene_text,
        "director_note": director_note,
        "version_id": resolved_version_id,
        "story_foundation": story_foundation,
        "characters": [{key: row[key] for key in row.keys()} for row in character_rows],
        "world_notes": {key: world_notes[key] for key in world_notes.keys()} if world_notes else {},
        "summary": summary["summary_text"] if summary else "",
        "live_state": [{key: row[key] for key in row.keys()} for row in live_rows],
        "relationships": [{key: row[key] for key in row.keys()} for row in relationship_rows],
        "emotional_memories": [{key: row[key] for key in row.keys()} for row in emotional_rows],
        "objects": [{key: row[key] for key in row.keys()} for row in object_rows],
        "scene_state": [{key: row[key] for key in row.keys()} for row in scene_state_rows],
        "plot_threads": [{key: row[key] for key in row.keys()} for row in plot_rows],
    }


def build_extraction_prompt(context: dict[str, Any]) -> str:
    world_notes = context.get("world_notes") or {}
    compact = {
        "story_foundation_base_identity": compact_value(context.get("story_foundation"), 3600),
        "active_characters": compact_rows(
            context.get("characters") or [],
            ("name", "role", "personality", "appearance", "current_state"),
            limit=EXTRACTION_CONTEXT_LIMITS["characters"],
            max_length=220,
        ),
        "current_live_character_state": compact_rows(
            context.get("live_state") or [],
            ("character_name", "state_type", "key", "value", "confidence"),
            limit=EXTRACTION_CONTEXT_LIMITS["live_state"],
            max_length=220,
        ),
        "current_relationships": compact_rows(
            context.get("relationships") or [],
            (
                "character_a_name",
                "character_b_name",
                "relationship_type",
                "relationship_key",
                "content",
                "emotional_importance",
                "closeness_weight",
                "trust_weight",
                "conflict_weight",
                "grief_weight",
                "betrayal_weight",
                "confidence",
            ),
            limit=EXTRACTION_CONTEXT_LIMITS["relationships"],
            max_length=220,
        ),
        "current_emotional_memories": compact_rows(
            context.get("emotional_memories") or [],
            ("memory_type", "memory_text", "emotional_weight", "themes_json", "related_characters_json", "confidence"),
            limit=EXTRACTION_CONTEXT_LIMITS["emotional_memories"],
            max_length=220,
        ),
        "current_objects": compact_rows(
            context.get("objects") or [],
            (
                "object_key", "name", "state_type", "value", "owner_character_name",
                "holder_character_name", "current_location", "placement_state", "condition",
                "visibility", "importance", "confidence",
            ),
            limit=EXTRACTION_CONTEXT_LIMITS["objects"],
            max_length=220,
        ),
        "current_scene_state": compact_rows(
            context.get("scene_state") or [],
            ("state_type", "key", "value", "confidence"),
            limit=20,
            max_length=220,
        ),
        "current_plot_threads": compact_rows(
            context.get("plot_threads") or [],
            ("thread_key", "title", "status", "content", "confidence"),
            limit=EXTRACTION_CONTEXT_LIMITS["plot_threads"],
            max_length=220,
        ),
        "world_notes": {
            key: compact_value(value, EXTRACTION_CONTEXT_LIMITS["world_field"])
            for key, value in world_notes.items()
            if compact_value(value, EXTRACTION_CONTEXT_LIMITS["world_field"])
        },
        "recent_summary": compact_value(context.get("summary"), EXTRACTION_CONTEXT_LIMITS["summary"]),
    }
    schema = {
        "character_updates": [
            {
                "character_name": "Mara",
                "state_type": "appearance",
                "key": "current_outfit",
                "value": "wearing a red cloak",
                "action": "replace",
                "confidence": 0.92,
                "confidence_reason": "The scene directly states Mara wears it.",
            },
            {
                "character_name": "Mara",
                "state_type": "blocking",
                "key": "room_position",
                "value": "standing beside the west door, close enough to hear Iven",
                "action": "replace",
                "confidence": 0.9,
                "confidence_reason": "The scene explicitly places Mara there.",
            },
            {
                "character_name": "Iven",
                "state_type": "blocking",
                "key": "carried_objects",
                "value": "carrying the brass key in his left hand",
                "action": "replace",
                "confidence": 0.9,
                "confidence_reason": "The scene directly shows Iven holding the key after the handoff.",
            },
            {
                "character_name": "Iven",
                "state_type": "knowledge",
                "key": "known_secrets",
                "value": "knows the prince's true name but agrees to keep it secret",
                "action": "replace",
                "confidence": 0.88,
                "confidence_reason": "The scene directly states what Iven knows.",
            }
        ],
        "relationship_updates": [
            {
                "character_a_name": "Mara",
                "character_b_name": "Iven",
                "relationship_type": "ally",
                "relationship_key": "promise_secret",
                "content": "Mara and Iven share a promise to protect the prince's true name until dawn.",
                "trust_weight": 0.78,
                "protective_weight": 0.72,
                "emotional_importance": 0.82,
                "confidence": 0.9,
                "confidence_reason": "The scene explicitly states the promise.",
            }
        ],
        "emotional_memory_updates": [
            {
                "memory_type": "grief",
                "memory_text": "Elara still grieves Lyra's death, especially around family, rescue, and home.",
                "emotional_weight": 0.95,
                "themes": ["grief", "family", "rescue"],
                "related_characters": ["Elara", "Lyra"],
                "trigger_conditions": "Recall only when family, children, rescue, home, or Lyra are relevant.",
                "cooldown_scenes": 4,
                "confidence": 0.9,
            }
        ],
        "world_updates": [],
        "scene_updates": [
            {
                "state_type": "blocking",
                "key": "present_characters",
                "value": "Mara and Iven are present in the chapel nave; no one else is shown hearing them.",
                "confidence": 0.88,
                "confidence_reason": "The scene directly frames who is present.",
            },
            {
                "state_type": "blocking",
                "key": "layout",
                "value": "west door, nave aisle, altar steps, and vestry curtain define the active movement zones",
                "confidence": 0.82,
                "confidence_reason": "The scene uses these zones for movement and sightlines.",
            }
        ],
        "object_updates": [
            {
                "name": "brass key",
                "object_key": "brass_key",
                "state_type": "ownership",
                "value": "transferred from Mara to Iven and now carried by Iven",
                "owner_character_name": "Mara",
                "holder_character_name": "Iven",
                "current_location": "in Iven's left hand",
                "placement_state": "held",
                "condition": "intact",
                "visibility": "visible",
                "importance": 0.8,
                "confidence": 0.92,
                "confidence_reason": "The scene directly says Mara gave it to Iven.",
            },
            {
                "name": "oil lamp",
                "object_key": "oil_lamp",
                "state_type": "location",
                "value": "set on the altar steps where both characters can see it",
                "owner_character_name": "",
                "holder_character_name": "",
                "current_location": "on the altar steps",
                "placement_state": "placed",
                "visibility": "visible",
                "importance": 0.45,
                "confidence": 0.88,
                "confidence_reason": "The scene directly places the lamp there.",
            }
        ],
        "plot_thread_updates": [],
        "summary_notes": [],
        "warnings": [],
    }
    def strip_confidence_reasons(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: strip_confidence_reasons(item) for key, item in value.items() if key != "confidence_reason"}
        if isinstance(value, list):
            return [strip_confidence_reasons(item) for item in value]
        return value

    schema = strip_confidence_reasons(schema)
    return "\n".join(
        [
            "Extract only story-state changes supported by the newly completed scene.",
            "Do not invent, speculate, or continue the story.",
            "Do not rewrite base character cards. Stable identity belongs in the base cards; evolving facts belong in live state.",
            "Story Foundation / Character Bible context is durable base identity and contradiction context only; do not extract it as a live-state update unless the new scene itself supports a change.",
            "Base identity examples: face, stable hair, height/build, age marker, permanent scars, default style. Live state examples: current outfit, fresh wounds, bandages, healing wounds, dirt, blood, wetness, haircut changes, carried items, current location, posture in this scene.",
            "Track scene blocking when explicit: who is present, who enters/leaves, where each character stands/sits, who is near whom, who can see/hear what, what each character carries or sets down, and what each character is doing right now.",
            "Update blocking after the completed scene, not from the plan alone. If the prose leaves the final position or object owner ambiguous, keep the update out or add a warning.",
            "Use human-readable relative positions only: beside the west door, across the table from Mara, in the galley doorway, behind the overturned cart. Do not invent coordinates.",
            "Track character knowledge when explicit: known secrets, promises heard, lies believed, information hidden from them, and what they personally witnessed.",
            "Track character goals when explicit: short_term_goal for immediate intent and long_term_goal for durable motivation.",
            "Track appearance changes as live state when temporary/current: clothing, armor, hair changes, visible injuries, bandages, mud, blood, wetness, scars becoming visible. Stable base appearance remains on character cards.",
            "Prefer a few useful updates over exhaustive notes. Return empty arrays where nothing changed.",
            "Return minified JSON only. No markdown. Keep values under 180 characters when possible. Use no more than 24 total updates.",
            "Return strict JSON only with this exact top-level shape:",
            json.dumps(schema, ensure_ascii=False),
            "",
            "Supported update guidance:",
            "- current_outfit, current_location, emotional_state, current_goal: replace prior value.",
            "- room_position/posture/action/carried_objects: use character_updates with state_type blocking and keys room_position, current_posture, current_action, or carried_objects. Replace prior values when the scene explicitly moves a character or changes what they carry.",
            "- present characters and visibility/hearing: use scene_updates with keys present_characters, entering, leaving, sight_lines, hearing_range, layout, doors_exits_windows, or active_zones when the scene supports it.",
            "- character knowledge/secrets: use character_updates with state_type knowledge and key known_secrets or knows. Name who knows it and who does not if the scene makes that clear.",
            "- goals: use character_updates with keys short_term_goal or long_term_goal only when a goal is explicit or strongly implied by action/dialogue.",
            "- Clothing/fashion: use precise keys like current_outfit, outfit_cloak, outfit_coat, hair_style. If a cloak changes color, replace the current cloak/outfit live state; do not edit the base card.",
            "- Injuries/healing: use stable body-part keys like injury_left_forearm. Progress the value through fresh wound, bandaged wound, healing wound, scar when explicitly stated.",
            "- Dirt/blood/wetness/weather effects: live visual state only, with keys like dirt, blood_on_clothes, wet_cloak.",
            "- Inventory: owner_character_name is legal/personal ownership; holder_character_name is who physically carries it now. Track current_location, placement_state (held, worn, placed, stored, hidden, lost, destroyed, unknown), condition, visibility (visible, hidden, concealed, unknown), and importance 0.0-1.0 when supported.",
            "- If Lyra lends the compass to Elara, keep owner_character_name Lyra and set holder_character_name Elara. If she gives ownership permanently, both may become Elara.",
            "- Object visibility/location: if an object is visible, hidden, dropped, or placed, include state_type visibility or location with the exact supported location.",
            "- If an object is placed somewhere, set holder_character_name empty, current_location such as 'on the farmhouse table', and placement_state placed. Preserve owner_character_name when ownership is still known.",
            "- Character location/blocking: use character_updates current_location only when the character's location actually changes or matters.",
            "- Secrets/promises/plot threads: use plot_thread_updates with status active or resolved. Include known_by/hidden_from in content when the scene supports it.",
            "- relationships: use character_a_name, character_b_name, relationship_type, relationship_key, content, and only weights supported by the scene.",
            "- relationship history: include shared history, betrayal, promise, debt, family bond, romantic/sexual interest only when relevant and adult, and current relationship tension when the scene supports it.",
            "- relationship_type must be one of parent, child, sibling, spouse, lover, close_friend, mentor, rival, enemy, betrayer, ally, acquaintance, stranger, unknown.",
            "- relationship weights are 0.0-1.0: closeness_weight, trust_weight, conflict_weight, protective_weight, grief_weight, romantic_weight, family_weight, betrayal_weight, respect_weight, fear_weight, emotional_importance.",
            "- emotional_memory_updates are for high-impact grief, trauma, promises, betrayal, guilt, shame, fear, protection, secrets, death, or permanent separation. Use emotional_weight 0.0-1.0 and concise trigger_conditions.",
            "- If a character dies, add/update character status dead when supported, then create grief/trauma memories only for high-weight relationships.",
            "- Do not overweight random deaths or low-importance side characters.",
            "- scene/location state: use scene_updates with key current_location when the active location changes.",
            "- Confidence calibration: explicit direct scene statement = 0.85-0.98; repeated explicit fact = 0.90-1.0; strong implication = 0.70-0.85; weak implication = 0.45-0.65; guess/inference = skip or 0.25-0.45 with warning.",
            "- Do not put everything at 0.50. If the scene literally states the fact, use high confidence.",
            "- If an update contradicts active state, require explicit evidence and use confidence 0.85+ or add a warning instead of overwriting.",
            "- Keep output compact: include numeric confidence but omit confidence_reason. Put only state facts in value/content fields.",
            "",
            "Compact current context JSON:",
            json.dumps(compact, ensure_ascii=False),
            "",
            "Director note that created this scene (supporting evidence for concrete setup facts; saved prose is primary):",
            compact_value(context.get("director_note"), 1800),
            "",
            "New scene text:",
            compact_value(context.get("scene_text"), EXTRACTION_CONTEXT_LIMITS["scene_text"]),
        ]
    )


def parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("State extractor did not return a JSON object.")
    return parsed


def is_transient_model_unavailable_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in TRANSIENT_MODEL_UNAVAILABLE_MARKERS)


def fallback_character_names(context: dict[str, Any]) -> list[str]:
    scene_text = clean_text(context.get("scene_text"), EXTRACTION_CONTEXT_LIMITS["scene_text"])
    existing = [
        clean_text(character.get("name"), 120)
        for character in context.get("characters", [])
        if clean_text(character.get("name"), 120)
    ]
    if len(existing) >= 2:
        names: list[str] = []
        for name in existing:
            if name and name.lower() not in {item.lower() for item in names}:
                names.append(name)
        return names[:8]
    counts: dict[str, int] = {}
    for match in re.finditer(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?\b", scene_text):
        raw_name = clean_text(match.group(0), 120)
        first = raw_name.split()[0]
        if first in STATE_FALLBACK_NAME_EXCLUSIONS:
            continue
        counts[first] = counts.get(first, 0) + 1
    sister_context = bool(re.search(r"\bsisters?\b|\bthree women\b|\bolder sister\b|\bmiddle sister\b|\byoungest sister\b", scene_text, re.I))
    sorted_candidates = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    if sister_context:
        repeated = [item for item in sorted_candidates if item[1] >= 2]
        if len(repeated) >= 3:
            sorted_candidates = repeated[:3]
    detected = [name for name, count in sorted_candidates if count >= 2 or sister_context]
    names: list[str] = []
    for name in [*existing, *detected]:
        if name and name.lower() not in {item.lower() for item in names}:
            names.append(name)
    return names[:8]


def fallback_location_from_scene(scene_text: str) -> str:
    checks = [
        (r"\bapartment\b|\bliving room\b|\bkitchen\b|\bhallway\b|\bbedroom\b", "apartment interior"),
        (r"\bbridge\b|\bengineering\b|\bstarship\b|\bship\b|\bcorridor\b|\bhatch\b", "ship interior"),
        (r"\bfarmstead\b|\bfarm\b|\bcottage\b|\bbarn\b|\bfields?\b", "farm outside town"),
        (r"\bruined chapel\b|\bchapel\b", "ruined chapel"),
        (r"\bbandit camp\b|\bcamp\b", "camp"),
        (r"\btavern\b|\bcastle\b|\bcourtyard\b|\bstable\b|\bgate\b", "medieval gathering place"),
        (r"\btown\b|\bmarket\b|\broad\b", "near town"),
    ]
    for pattern, location in checks:
        if re.search(pattern, scene_text, re.I):
            return location
    return ""


def fallback_character_live_updates(scene_text: str, names: list[str]) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    if not scene_text or not names:
        return updates
    known_names = [clean_text(name, 120) for name in names if clean_text(name, 120)]
    seen: set[tuple[str, str]] = set()
    sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", scene_text) if sentence.strip()]

    def sentence_names(sentence: str) -> list[str]:
        matched: list[str] = []
        for name in known_names:
            first = name.split()[0]
            if re.search(rf"\b{re.escape(name)}\b|\b{re.escape(first)}\b", sentence):
                matched.append(name)
        return matched

    def add_update(name: str, state_type: str, key: str, value: str, confidence: float, reason: str) -> None:
        value = clean_text(value, 180)
        if not name or not key or not value:
            return
        pair = (name.lower(), normalize_key(key))
        if pair in seen:
            return
        seen.add(pair)
        updates.append(
            {
                "character_name": name,
                "state_type": state_type,
                "key": key,
                "value": value,
                "action": "replace",
                "confidence": confidence,
                "confidence_reason": reason,
            }
        )

    for sentence in sentences:
        matched_names = sentence_names(sentence)
        if not matched_names:
            continue
        for name in matched_names:
            outfit_match = re.search(
                r"\b(?:wearing|wears|wore|in)\s+(?:a|an|the|her|his|their)?\s*"
                r"(?P<outfit>(?:[a-z]+[- ]?){0,5}(?:coat|jacket|cloak|dress|tunic|armor|armour|shirt|boots|gown|suit|uniform|sweater|jeans|scrubs|raincoat))\b",
                sentence,
                re.I,
            )
            if outfit_match:
                add_update(
                    name,
                    "appearance",
                    "current_outfit",
                    outfit_match.group("outfit"),
                    0.82,
                    "Local fallback found an explicit clothing description.",
                )
            elif re.search(r"\b(bloodied|muddy|wet|torn|soaked|dusty|singed)\b", sentence, re.I) and re.search(
                r"\b(cloak|coat|jacket|dress|tunic|shirt|boots|armor|uniform|clothes|sleeve)\b",
                sentence,
                re.I,
            ):
                add_update(
                    name,
                    "appearance",
                    "current_outfit",
                    sentence,
                    0.74,
                    "Local fallback found explicit clothing/body condition in the sentence.",
                )

            position_match = re.search(
                r"\b(?:stood|sat|leaned|crouched|knelt|waited|hovered|stopped|remained|moved|shifted)\s+"
                r"(?P<position>(?:beside|near|at|by|in|inside|outside|behind|before|across from|against|under|between|on)\s+[^.;!?]{3,90})",
                sentence,
                re.I,
            )
            if position_match:
                add_update(
                    name,
                    "blocking",
                    "room_position",
                    position_match.group("position"),
                    0.8,
                    "Local fallback found an explicit character position.",
                )

            carried_match = re.search(
                r"\b(?:holding|held|carrying|carried|gripping|gripped|clutching|clutched|tucked|pocketed)\s+"
                r"(?:the|a|an|her|his|their)?\s*(?P<object>[a-z][a-z0-9' -]{2,60})",
                sentence,
                re.I,
            )
            if carried_match:
                obj = re.split(r"\b(?:while|when|as|and|but|with|near|beside|under|inside|outside|because)\b", carried_match.group("object"), maxsplit=1, flags=re.I)[0]
                add_update(
                    name,
                    "blocking",
                    "carried_objects",
                    clean_text(obj.rstrip(" .,;:"), 120),
                    0.82,
                    "Local fallback found an explicit carried or held object.",
                )

            goal_match = re.search(
                r"\b(?:wanted|needed|intended|planned|decided|meant|tried|trying)\s+to\s+(?P<goal>[^.;!?]{4,120})",
                sentence,
                re.I,
            )
            if goal_match:
                add_update(
                    name,
                    "goal",
                    "short_term_goal",
                    "to " + goal_match.group("goal"),
                    0.72,
                    "Local fallback found an explicit short-term intent.",
                )

            if re.search(r"\b(secret|knows|knew|learned|realized|understood|heard|overheard|saw|witnessed)\b", sentence, re.I):
                key = "known_secrets" if re.search(r"\bsecret\b", sentence, re.I) else "knows"
                add_update(
                    name,
                    "knowledge",
                    key,
                    sentence,
                    0.72,
                    "Local fallback found explicit character knowledge or witnessed information.",
                )

            if re.search(r"\b(bleeding|bloodied|bandaged|wounded|limped|limping|scar|scarred|bruise|bruised|broken|sprained)\b", sentence, re.I):
                add_update(
                    name,
                    "appearance",
                    "visible_injuries",
                    sentence,
                    0.76,
                    "Local fallback found explicit injury or body-state evidence.",
                )
        if len(updates) >= 12:
            break
    return updates[:12]


def fallback_scene_blocking_updates(scene_text: str, names: list[str], location: str = "") -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    if not scene_text:
        return updates

    def add_scene(state_type: str, key: str, value: str, confidence: float, reason: str) -> None:
        value = clean_text(value, 220)
        if not value:
            return
        normalized = normalize_key(key)
        if any(normalize_key(item.get("key")) == normalized for item in updates):
            return
        updates.append(
            {
                "state_type": state_type,
                "key": key,
                "value": value,
                "confidence": confidence,
                "confidence_reason": reason,
            }
        )

    present = [name for name in names[:8] if re.search(rf"\b{re.escape(name)}\b|\b{re.escape(name.split()[0])}\b", scene_text)]
    if present:
        add_scene("blocking", "present_characters", ", ".join(present), 0.72, "Local fallback identified named characters present in the scene.")
    if location:
        add_scene("scene", "current_location", location, 0.62, "Local fallback inferred a broad active location from scene nouns.")

    zone_terms = (
        "door",
        "window",
        "table",
        "counter",
        "kitchen",
        "hallway",
        "bedroom",
        "couch",
        "corridor",
        "hatch",
        "airlock",
        "console",
        "bridge",
        "engineering",
        "gate",
        "stable",
        "courtyard",
        "road",
        "altar",
        "nave",
        "campfire",
        "cart",
    )
    zones = []
    lower = scene_text.lower()
    for term in zone_terms:
        if re.search(rf"\b{re.escape(term)}s?\b", lower):
            zones.append(term)
    if zones:
        add_scene("blocking", "active_zones", ", ".join(zones[:8]), 0.68, "Local fallback found concrete movement zones in the scene.")

    entry_exit_sentences = [
        clean_text(sentence, 180)
        for sentence in re.split(r"(?<=[.!?])\s+", scene_text)
        if re.search(r"\b(entered|came in|stepped in|stepped inside|left|went out|exited|slipped out|crossed the threshold)\b", sentence, re.I)
    ]
    if entry_exit_sentences:
        add_scene("blocking", "entries_exits", entry_exit_sentences[0], 0.74, "Local fallback found an explicit entry or exit.")

    sight_sentences = [
        clean_text(sentence, 180)
        for sentence in re.split(r"(?<=[.!?])\s+", scene_text)
        if re.search(r"\b(can|could|cannot|couldn't|watched|saw|visible|blocked her view|blocked his view|line of sight)\b", sentence, re.I)
    ]
    if sight_sentences:
        add_scene("blocking", "sight_lines", sight_sentences[0], 0.7, "Local fallback found sightline or visibility evidence.")

    hearing_sentences = [
        clean_text(sentence, 180)
        for sentence in re.split(r"(?<=[.!?])\s+", scene_text)
        if re.search(r"\b(heard|hear|overheard|whisper|whispered|murmur|murmured|too loud|quiet enough)\b", sentence, re.I)
    ]
    if hearing_sentences:
        add_scene("blocking", "hearing_range", hearing_sentences[0], 0.68, "Local fallback found hearing or privacy evidence.")

    return updates[:8]


def fallback_object_updates(scene_text: str, names: list[str] | None = None) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    known_names = [clean_text(name, 120) for name in (names or []) if clean_text(name, 120)]
    full_by_first = {name.split()[0].lower(): name for name in known_names if name.split()}
    valid_owner_names = {name.lower() for name in known_names}
    valid_owner_names.update(full_by_first.keys())
    generic_owners = {
        "a",
        "an",
        "and",
        "her",
        "his",
        "inside",
        "it",
        "its",
        "she",
        "he",
        "the",
        "they",
        "their",
        "them",
        "this",
    }
    concrete_object_hints = {
        "badge",
        "bag",
        "blade",
        "book",
        "bottle",
        "blanket",
        "card",
        "coat",
        "coin",
        "compass",
        "cup",
        "cutters",
        "dress",
        "drive",
        "file",
        "folder",
        "gasket",
        "gun",
        "jacket",
        "key",
        "keycard",
        "knife",
        "lamp",
        "lantern",
        "ledger",
        "letter",
        "map",
        "mask",
        "notebook",
        "note",
        "pistol",
        "parchment",
        "pouch",
        "raincoat",
        "radio",
        "release",
        "revolver",
        "rifle",
        "ring",
        "roll",
        "satchel",
        "seal",
        "sword",
        "tablet",
        "tool",
        "towel",
        "vial",
        "vellum",
    }

    def resolve_owner(raw_owner: str) -> str:
        owner = clean_text(raw_owner, 120)
        if not owner:
            return ""
        owner_key = owner.lower()
        if owner_key in full_by_first:
            return full_by_first[owner_key]
        if owner_key in valid_owner_names:
            return owner
        if valid_owner_names:
            return ""
        if owner_key in generic_owners:
            return ""
        return owner

    def clean_object_name(raw_name: str) -> str:
        name = clean_text(raw_name, 120)
        name = re.sub(r"^(?:the|a|an|his|her|their)\s+", "", name, flags=re.I)
        name = re.split(
            r"\b(?:while|when|as|and|but|with|near|beside|under|inside|outside|before|after|because|that|which|who|from)\b",
            name,
            maxsplit=1,
            flags=re.I,
        )[0]
        name = name.rstrip(" .,;:!?\"'()[]")
        return name

    def object_name_is_plausible(name: str) -> bool:
        if not name or len(name.split()) > 6:
            return False
        normalized = normalize_key(name)
        if not normalized or len(normalized) < 3:
            return False
        rejected_starts = {
            "been",
            "begun",
            "forced",
            "grounding",
            "in",
            "made",
            "run",
            "stood",
            "tracked",
        }
        first_word = name.split()[0].lower()
        if first_word in rejected_starts:
            return False
        normalized_words = set(normalized.split("_"))
        if not (normalized_words & concrete_object_hints):
            return False
        return bool(re.search(r"[a-z]", name, re.I))

    def add_update(
        holder: str,
        name: str,
        confidence: float,
        reason: str,
        *,
        legal_owner: str = "",
    ) -> bool:
        holder = resolve_owner(holder)
        owner = resolve_owner(legal_owner) if legal_owner else ""
        name = clean_object_name(name)
        if not holder or not object_name_is_plausible(name):
            return False
        object_key = normalize_key(name)
        payload = {
            "name": name,
            "object_key": object_key,
            "state_type": "ownership" if owner else "possession",
            "value": f"carried by {holder}",
            "owner_character_name": owner,
            "holder_character_name": holder,
            "placement_state": "held",
            "visibility": "unknown",
            "importance": 0.5,
            "confidence": confidence,
            "confidence_reason": reason,
        }
        for index, item in enumerate(updates):
            if item.get("object_key") != object_key:
                continue
            if item.get("holder_character_name", "").lower() == holder.lower():
                return False
            existing_confidence = float(item.get("confidence") or 0.0)
            if "transfer" in reason.lower() or confidence >= existing_confidence:
                updates.pop(index)
                updates.append(payload)
                return True
            return False
        updates.append(payload)
        return True

    def add_location_update(name: str, location: str, confidence: float, reason: str) -> bool:
        name = clean_object_name(name)
        location = clean_text(location, 160).rstrip(" .,;:")
        if not object_name_is_plausible(name) or not location:
            return False
        object_key = normalize_key(name)
        payload = {
            "name": name,
            "object_key": object_key,
            "state_type": "location",
            "value": f"placed {location}",
            "owner_character_name": "",
            "holder_character_name": "",
            "current_location": location,
            "placement_state": "placed",
            "visibility": "unknown",
            "importance": 0.45,
            "confidence": confidence,
            "confidence_reason": reason,
        }
        for item in updates:
            if item.get("object_key") == object_key and item.get("state_type") == "location":
                return False
        updates.append(payload)
        return True

    for match in re.finditer(
        r"(?:\b(?:the|a|an)\s+)?(?P<object>[a-z][a-z0-9' -]{2,64}?)\s+belongs\s+to\s+(?P<owner>[A-Z][a-z]{2,})\b",
        scene_text,
        re.I,
    ):
        add_update(
            match.group("owner"),
            match.group("object"),
            0.92,
            "The scene explicitly states legal ownership.",
            legal_owner=match.group("owner"),
        )

    for match in re.finditer(
        r"\b(?P<from>[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)\s+(?P<verb>gave|handed|passed|transferred)\s+"
        r"(?:the|a|an)\s+(?P<object>[a-z][a-z0-9' -]{2,48}?)\s+to\s+"
        r"(?P<to>[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)\b",
        scene_text,
    ):
        giver = clean_text(match.group("from"), 120)
        holder = clean_text(match.group("to"), 120)
        name = clean_text(match.group("object"), 120).rstrip(" .,;:")
        if not holder or not name:
            continue
        legal_owner = holder if match.group("verb").lower() in {"gave", "transferred"} else giver
        if add_update(
            holder,
            name,
            0.9,
            "The scene explicitly describes the object transfer.",
            legal_owner=legal_owner,
        ):
            updates[-1]["value"] = f"transferred from {giver} to {holder}"

    for match in re.finditer(
        r"\b(?P<holder>[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)\s+"
        r"(?:carried|held|holds|gripped|clutched|pocketed|tucked)\s+"
        r"(?:the|a|an|his|her|their)?\s*(?P<object>[a-z][a-z0-9' -]{2,64}?)"
        r"(?=\s+(?:she|he|they)\s+had\s+given\b|[.,;!?]|$)",
        scene_text,
    ):
        holder = match.group("holder")
        legal_owner = ""
        tail = scene_text[match.end() : match.end() + 40]
        if re.match(r"\s+(?:she|he|they)\s+had\s+given\b", tail, re.I):
            prior_text = scene_text[: match.start()]
            prior_candidates = []
            holder_first = holder.split()[0].lower()
            for candidate in known_names:
                first = candidate.split()[0]
                if first.lower() == holder_first:
                    continue
                found = list(re.finditer(rf"\b{re.escape(first)}\b", prior_text, re.I))
                if found:
                    prior_candidates.append((found[-1].start(), candidate))
            if prior_candidates:
                legal_owner = max(prior_candidates)[1]
        add_update(
            holder,
            match.group("object"),
            0.88,
            "The scene directly names who carries the object.",
            legal_owner=legal_owner,
        )
    for match in re.finditer(
        r"\b(?P<owner>[A-Z][a-z]{2,})\b[^.\n]{0,120}?\b"
        r"(?:(?:carried|held|holds|holding|gripped|gripping|clutched|clutching|kept|hid|pocketed|took|tucked|has|had)\s+|reached\s+for\s+)"
        r"(?:the|a|an|his|her|their)?\s*(?P<object>[a-z][a-z0-9' -]{2,64})",
        scene_text,
    ):
        add_update(
            match.group("owner"),
            match.group("object"),
            0.86,
            "The scene explicitly describes who holds or carries the object.",
        )
    for match in re.finditer(
        r"\b(?:placed|set|left|laid|dropped|rested|propped|slid|put)\s+"
        r"(?:the|a|an|his|her|their)?\s*(?P<object>[a-z][a-z0-9' -]{2,64}?)\s+"
        r"(?P<location>(?:on|onto|upon|under|inside|in|beside|near|against|behind|by)\s+[^.\n,;]{3,80})",
        scene_text,
        re.I,
    ):
        add_location_update(
            match.group("object"),
            match.group("location"),
            0.82,
            "The scene explicitly places the object in a location rather than with a holder.",
        )
    for match in re.finditer(
        r"\b(?P<owner>[A-Z][a-z]{2,})\b[^.\n]{0,140}?\bwith\s+(?:the|a|an|his|her|their)?\s*"
        r"(?P<object>[a-z][a-z0-9' -]{2,64}?)\s+(?:in|under|inside|tucked|pressed|against)\b",
        scene_text,
    ):
        add_update(
            match.group("owner"),
            match.group("object"),
            0.82,
            "The scene explicitly places the object with the character.",
        )
    if known_names:
        recent_owner = ""
        sentences = re.split(r"(?<=[.!?])\s+", scene_text)
        for sentence in sentences:
            for name in known_names:
                if re.search(rf"\b{re.escape(name)}\b|\b{re.escape(name.split()[0])}\b", sentence):
                    recent_owner = name
            if not recent_owner:
                continue
            for match in re.finditer(
                r"\b(?:in|under|inside|beneath|against)\s+(?:her|his|their)\s+(?:left\s+|right\s+)?(?:hand|hands|coat|jacket|cloak|sleeve|pocket)\b"
                r"[^.\n]{0,80}?\b(?:gripped|held|clutched|carried|tucked)?\s*(?:the|a|an)?\s*"
                r"(?P<object>[a-z][a-z0-9' -]{2,64})",
                sentence,
                re.I,
            ):
                add_update(
                    recent_owner,
                    match.group("object"),
                    0.8,
                    "The scene places the object with a pronoun tied to the most recent named character.",
                )
            for match in re.finditer(
                r"\b(?:the|a|an)\s+(?P<object>[a-z][a-z0-9' -]{2,64}?)\s+"
                r"(?:gripped|held|clutched|carried|tucked|pressed)\s+(?:in|under|inside|against)\s+(?:her|his|their)\b",
                sentence,
                re.I,
            ):
                add_update(
                    recent_owner,
                    match.group("object"),
                    0.8,
                    "The scene places the object with a pronoun tied to the most recent named character.",
                )
            for match in re.finditer(
                r"\b(?P<object>"
                r"(?:blue\s+hospital\s+keycard|hospital\s+keycard|blue\s+keycard|keycard|"
                r"torn\s+road\s+map|road\s+map|map|"
                r"piece\s+of\s+vellum|vellum|parchment|"
                r"replacement\s+pressure\s+gasket|pressure\s+gasket|pressure\s+seal|jagged\s+seal|seal|gasket|"
                r"stolen\s+ledger|ledger|bolt\s+cutters|canvas\s+tool\s+roll|tool\s+roll)"
                r")\b",
                sentence,
                re.I,
            ):
                if re.search(r"\b(hold|held|holding|grip|gripped|gripping|clutch|clutched|carried|tucked|under|smoothed|smoothing|reached\s+for|in\s+(?:her|his|their)\s+hands?)\b", sentence, re.I):
                    add_update(
                        recent_owner,
                        match.group("object"),
                        0.82,
                        "The scene ties a concrete carried object to the most recent named character.",
                    )
            for match in re.finditer(
                r"\b(?P<object>"
                r"(?:blue\s+hospital\s+keycard|hospital\s+keycard|blue\s+keycard|keycard|"
                r"torn\s+road\s+map|road\s+map|map|"
                r"piece\s+of\s+vellum|vellum|parchment|"
                r"replacement\s+pressure\s+gasket|pressure\s+gasket|pressure\s+seal|jagged\s+seal|seal|gasket|"
                r"stolen\s+ledger|ledger|bolt\s+cutters|canvas\s+tool\s+roll|tool\s+roll)"
                r")\s+(?:in|under|inside|against)\s+"
                r"(?P<owner>[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)['’]s\s+(?:hand|hands|coat|jacket|cloak|sleeve|pocket)\b",
                sentence,
                re.I,
            ):
                add_update(
                    match.group("owner"),
                    match.group("object"),
                    0.84,
                    "The scene explicitly places a concrete object in a named character's hand or clothing.",
                )
    return updates[:4]


def fallback_relationship_updates(scene_text: str, names: list[str]) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    if len(names) < 2:
        return updates

    def add_pair(
        first: str,
        second: str,
        *,
        relationship_type: str,
        relationship_key: str,
        content: str,
        confidence: float,
        **weights: float,
    ) -> None:
        if not first or not second or first.lower() == second.lower():
            return
        sorted_key = tuple(sorted((first.lower(), second.lower()))) + (relationship_key,)
        for item in updates:
            item_key = tuple(sorted((item["character_a_name"].lower(), item["character_b_name"].lower()))) + (item["relationship_key"],)
            if item_key == sorted_key:
                return
        payload = {
            "character_a_name": first,
            "character_b_name": second,
            "relationship_type": relationship_type,
            "relationship_key": relationship_key,
            "content": content,
            "confidence": confidence,
            "confidence_reason": "The completed scene explicitly states or strongly supports this relationship fact.",
        }
        payload.update(weights)
        updates.append(payload)

    lower = scene_text.lower()
    first, second = names[0], names[1]
    if re.search(r"\b(older\s+)?sisters?\b|\bbrothers?\b|\bsiblings?\b", scene_text, re.I):
        for index, name in enumerate(names[:3]):
            for other in names[index + 1 : 3]:
                add_pair(
                    name,
                    other,
                    relationship_type="sibling",
                    relationship_key="siblings",
                    content="siblings with shared family history",
                    confidence=0.86,
                    closeness_weight=0.72,
                    family_weight=0.95,
                    emotional_importance=0.82,
                )
    if (
        "used to trust" in lower
        or "used to trust each other" in lower
        or "failed escort" in lower
        or "old trust" in lower
        or ("escort detail" in lower and "promise" in lower)
        or ("escort job" in lower and "promise" in lower)
        or ("trust was a luxury" in lower and ("caravan" in lower or "bled for" in lower))
        or ("promise" in lower and ("bled for" in lower or "blackwood" in lower or "uneasy silence" in lower))
        or "broken the trust" in lower
    ):
        add_pair(
            first,
            second,
            relationship_type="ally",
            relationship_key="damaged_trust",
            content="former trust is damaged by a past failure",
            confidence=0.78,
            trust_weight=0.34,
            conflict_weight=0.62,
            emotional_importance=0.72,
        )
    if (
        "wanted each other for months" in lower
        or ("consent" in lower and "wanted" in lower)
        or "first kiss" in lower
        or "months carefully" in lower
        or "colleagues, friends" in lower
        or ("months" in lower and "careful" in lower and ("want you" in lower or "desire" in lower or "consent" in lower))
        or ("professional distance" in lower and ("mutual longing" in lower or "want you" in lower or "consent" in lower))
        or ("relationship had evolved" in lower and "careful" in lower)
        or ("wanted to pull" in lower and ("desire" in lower or "entire world" in lower))
        or "long-denied desire" in lower
        or ("missed opportunity" in lower and "desire" in lower)
    ):
        add_pair(
            first,
            second,
            relationship_type="lover",
            relationship_key="mutual_desire_and_consent",
            content="mutual adult desire is tempered by care, consent, and shared work history",
            confidence=0.82,
            romantic_weight=0.82,
            trust_weight=0.68,
            emotional_importance=0.76,
        )
    if (
        "promised each other" in lower
        or "no one would get killed" in lower
        or "no one gets killed" in lower
        or "no one dies" in lower
        or "no blood" in lower
        or "made a pact" in lower
        or "made it a pact" in lower
        or "pact they had made" in lower
    ):
        add_pair(
            first,
            second,
            relationship_type="ally",
            relationship_key="shared_promise_no_killing",
            content="share a promise that no one will be killed tonight",
            confidence=0.84,
            trust_weight=0.7,
            protective_weight=0.7,
            emotional_importance=0.8,
        )
    if "mother disappeared" in lower or "mother had disappeared" in lower or "mother vanished" in lower or "mother had vanished" in lower:
        add_pair(
            first,
            second,
            relationship_type="sibling",
            relationship_key="shared_missing_mother",
            content="share the unresolved history of their mother's disappearance",
            confidence=0.86,
            family_weight=0.9,
            grief_weight=0.68,
            emotional_importance=0.86,
        )
    if (
        "falsified" in lower
        or "sabotage" in lower
        or "distrust" in lower
        or ("maintenance" in lower and ("trust" in lower or "blame" in lower))
    ):
        add_pair(
            first,
            second,
            relationship_type="ally",
            relationship_key="distrust_under_pressure",
            content="work together under pressure while distrust and possible sabotage shape their choices",
            confidence=0.72,
            trust_weight=0.32,
            conflict_weight=0.62,
            respect_weight=0.46,
            emotional_importance=0.68,
        )
    if "letter" in lower and re.search(r"\b(hurt|jealous|protect|consequence|truth|loyalty|forgive|sealed)\b", lower):
        add_pair(
            first,
            second,
            relationship_type="unknown",
            relationship_key="unresolved_letter_tension",
            content="an unresolved letter and old hurt create active tension between them",
            confidence=0.72,
            closeness_weight=0.42,
            trust_weight=0.36,
            conflict_weight=0.62,
            emotional_importance=0.74,
        )
        if len(names) >= 3 and re.search(r"\b(best friend|friend|loyal|protect|consequence|truth)\b", lower):
            add_pair(
                first,
                names[2],
                relationship_type="close_friend",
                relationship_key="protective_friendship_pressure",
                content="friendship and protective loyalty pressure the choice about the letter",
                confidence=0.68,
                closeness_weight=0.72,
                protective_weight=0.64,
                conflict_weight=0.36,
                emotional_importance=0.66,
            )
    return updates[:4]


def fallback_name_can_resolve(raw_name: Any, names: list[str]) -> bool:
    name = clean_text(raw_name, 120).lower()
    if not name:
        return False
    for candidate in names:
        candidate_clean = clean_text(candidate, 120).lower()
        if not candidate_clean:
            continue
        if name == candidate_clean or name == candidate_clean.split()[0] or candidate_clean.startswith(f"{name} "):
            return True
    return False


def payload_has_usable_relationship_updates(payload: dict[str, Any], names: list[str]) -> bool:
    for item in array_of_dicts(payload, "relationship_updates"):
        content = clean_text(item.get("content") or item.get("value") or item.get("description"), 300)
        first = item.get("character_a_name") or item.get("character_a")
        second = item.get("character_b_name") or item.get("character_b")
        if content and fallback_name_can_resolve(first, names) and fallback_name_can_resolve(second, names):
            return True
    return False


def payload_has_usable_object_updates(payload: dict[str, Any], names: list[str]) -> bool:
    for item in array_of_dicts(payload, "object_updates"):
        name = clean_text(item.get("name") or item.get("object_name") or item.get("object_key"), 120)
        value = clean_text(item.get("value") or item.get("content") or item.get("description") or item.get("state"), 240)
        owner = item.get("owner_character_name") or item.get("owner") or item.get("held_by")
        if name and value and (not owner or fallback_name_can_resolve(owner, names)):
            return True
    return False


def fallback_plot_threads(scene_text: str) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    if re.search(r"\bbandits?\b", scene_text, re.I) and re.search(r"\bcaptured\b|\bgirl\b|\bMiri\b", scene_text, re.I):
        updates.append(
            {
                "thread_key": "rescue_captured_girl",
                "title": "Rescue the captured girl",
                "status": "active",
                "content": "Bandits have captured a girl, and the protagonists are weighing a rescue.",
                "confidence": 0.64,
            }
        )
    promise_match = re.search(
        r"\b(?P<speaker>[A-Z][a-z]{2,})\s+promised\s+(?P<target>[A-Z][a-z]{2,})\s+[^.]{0,160}?\b(secret|true name|until dawn)\b",
        scene_text,
        re.I,
    )
    if promise_match:
        speaker = clean_text(promise_match.group("speaker"), 120)
        target = clean_text(promise_match.group("target"), 120)
        updates.append(
            {
                "thread_key": "promise_keep_secret",
                "title": "Promise to keep the secret",
                "status": "active",
                "content": f"{speaker} promised {target} to keep an important secret.",
                "confidence": 0.9,
            }
        )
    elif re.search(r"\bsecret\b", scene_text, re.I):
        updates.append(
            {
                "thread_key": "hidden_secret",
                "title": "Hidden secret",
                "status": "active",
                "content": "A secret remains important after the completed scene.",
                "confidence": 0.55,
            }
        )
    return updates[:4]


def fallback_state_payload(context: dict[str, Any], reason: str) -> dict[str, Any]:
    scene_text = clean_text(context.get("scene_text"), EXTRACTION_CONTEXT_LIMITS["scene_text"])
    support_text = "\n".join(
        item
        for item in (
            scene_text,
            clean_text(context.get("director_note"), 2200),
        )
        if item
    )
    names = fallback_character_names(context)
    location = fallback_location_from_scene(scene_text)
    character_updates = fallback_character_live_updates(scene_text, names)
    relationship_updates = fallback_relationship_updates(support_text or scene_text, names)
    scene_updates = fallback_scene_blocking_updates(scene_text, names, location)
    object_updates = fallback_object_updates(support_text or scene_text, names)
    plot_thread_updates = fallback_plot_threads(scene_text)
    return {
        "character_updates": character_updates,
        "relationship_updates": relationship_updates[:3],
        "emotional_memory_updates": [],
        "world_updates": [],
        "scene_updates": scene_updates,
        "object_updates": object_updates,
        "plot_thread_updates": plot_thread_updates,
        "summary_notes": ["Used conservative local fallback because the LM Studio state extractor returned no usable JSON."],
        "warnings": [f"State extraction fallback used: {clean_text(reason, 300)}"],
    }


def supplement_partial_state_payload(context: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    scene_text = clean_text(context.get("scene_text"), EXTRACTION_CONTEXT_LIMITS["scene_text"])
    if not scene_text:
        return payload
    support_text = "\n".join(
        item
        for item in (
            scene_text,
            clean_text(context.get("director_note"), 2200),
        )
        if item
    )
    supplemented = {**payload}
    warnings = [clean_text(item, 600) for item in supplemented.get("warnings", []) if clean_text(item)]
    names = fallback_character_names(context)

    existing_character_pairs = set()
    for item in array_of_dicts(supplemented, "character_updates"):
        item_name = clean_text(item.get("character_name") or item.get("name"), 120).lower()
        item_key_raw = clean_text(item.get("key") or item.get("field"), 120)
        if item_name and item_key_raw:
            existing_character_pairs.add((item_name, normalize_key(item_key_raw)))
    added_character_updates = []
    for item in fallback_character_live_updates(scene_text, names):
        pair = (clean_text(item.get("character_name"), 120).lower(), normalize_key(item.get("key")))
        if pair not in existing_character_pairs:
            existing_character_pairs.add(pair)
            added_character_updates.append(item)
    if added_character_updates:
        supplemented["character_updates"] = [*array_of_dicts(supplemented, "character_updates"), *added_character_updates[:8]]
        warnings.append("Local continuity supplement added explicit character position, outfit/body, knowledge, goal, or carried-object facts omitted by the model extractor.")

    existing_scene_keys = set()
    for item in array_of_dicts(supplemented, "scene_updates"):
        item_key_raw = clean_text(item.get("key") or item.get("field"), 120)
        if item_key_raw:
            existing_scene_keys.add(normalize_key(item_key_raw))
    added_scene_updates = []
    for item in fallback_scene_blocking_updates(scene_text, names, fallback_location_from_scene(scene_text)):
        key = normalize_key(item.get("key"))
        if key not in existing_scene_keys:
            existing_scene_keys.add(key)
            added_scene_updates.append(item)
    if added_scene_updates:
        supplemented["scene_updates"] = [*array_of_dicts(supplemented, "scene_updates"), *added_scene_updates[:5]]
        warnings.append("Local continuity supplement added explicit scene blocking, active-zone, entry/exit, sightline, or hearing facts omitted by the model extractor.")

    existing_object_pairs = set()
    for item in array_of_dicts(supplemented, "object_updates"):
        object_key_raw = clean_text(item.get("object_key") or item.get("name") or item.get("object_name"), 120)
        object_key = normalize_key(object_key_raw) if object_key_raw else ""
        state_type = normalize_key(item.get("state_type") or item.get("type") or "object")
        if object_key:
            existing_object_pairs.add((object_key, state_type))
    added_object_updates = []
    for item in fallback_object_updates(support_text or scene_text, names):
        object_key_raw = clean_text(item.get("object_key") or item.get("name"), 120)
        object_key = normalize_key(object_key_raw) if object_key_raw else ""
        state_type = normalize_key(item.get("state_type") or "object")
        pair = (object_key, state_type)
        if object_key and pair not in existing_object_pairs:
            existing_object_pairs.add(pair)
            added_object_updates.append(item)
    if added_object_updates:
        supplemented["object_updates"] = [*array_of_dicts(supplemented, "object_updates"), *added_object_updates[:6]]
        warnings.append("Local continuity supplement added explicit object ownership, carrying, transfer, or placement facts omitted by the model extractor.")
    elif not payload_has_usable_object_updates(supplemented, names):
        object_updates = fallback_object_updates(support_text or scene_text, names)
        if object_updates:
            supplemented["object_updates"] = object_updates
            warnings.append("Local continuity supplement added explicit object ownership/carrying facts omitted by the model extractor.")

    if not payload_has_usable_relationship_updates(supplemented, names):
        relationship_updates = fallback_relationship_updates(support_text or scene_text, names)
        if relationship_updates:
            supplemented["relationship_updates"] = relationship_updates
            warnings.append("Local continuity supplement added explicit relationship/history facts omitted by the model extractor.")

    existing_thread_keys = {
        normalize_key(item.get("thread_key") or item.get("title") or item.get("name"))
        for item in array_of_dicts(supplemented, "plot_thread_updates")
        if clean_text(item.get("thread_key") or item.get("title") or item.get("name"), 220)
    }
    added_plot_threads = []
    for item in fallback_plot_threads(scene_text):
        thread_key = normalize_key(item.get("thread_key") or item.get("title"))
        if thread_key and thread_key not in existing_thread_keys:
            existing_thread_keys.add(thread_key)
            added_plot_threads.append(item)
    if added_plot_threads:
        supplemented["plot_thread_updates"] = [
            *array_of_dicts(supplemented, "plot_thread_updates"),
            *added_plot_threads[:4],
        ]
        warnings.append("Local continuity supplement added explicit unresolved promises or threats omitted by the model extractor.")

    if warnings:
        supplemented["warnings"] = warnings[:12]
    return supplemented


async def extract_state_json(context: dict[str, Any]) -> tuple[dict[str, Any], str, Any]:
    model_settings, resolved_model = resolve_task_model_settings("story_state_extraction")
    state_settings = load_story_state_settings()
    timeout_seconds = (
        resolved_model.timeout_seconds
        if "timeout_seconds" in resolved_model.override_fields
        else float(state_settings.state_extraction_timeout_seconds)
    )
    client = model_client_for_settings(model_settings)
    model = model_settings.model.strip()
    if not model:
        models = await client.list_models()
        model = next((item.get("id") for item in models if item.get("id")), "")
    if not model:
        raise LMStudioError("No LM Studio model is selected or loaded for story-state extraction.")
    resolved_model.model = model
    resolved_model.timeout_seconds = timeout_seconds
    system_prompt = (
        "You are StoryDriver's local story-state extractor. "
        "Return strict JSON only. Extract supported facts from the provided scene; do not invent."
    )
    if clean_text(resolved_model.notes, 1200):
        system_prompt = f"{system_prompt}\n\nTask notes:\n{clean_text(resolved_model.notes, 1200)}"
    raw = ""
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            result = await client.generate_scene_routed(
                model=model,
                system_prompt=system_prompt,
                user_prompt=build_extraction_prompt(context),
                parameters=task_parameters(
                    resolved_model,
                    {
                        "temperature": 0.05 if attempt else 0.1,
                        "top_p": 0.8 if attempt else 0.85,
                        "max_tokens": 1200,
                        "presence_penalty": 0,
                        "frequency_penalty": 0,
                    },
                    max_tokens_min=800,
                    max_tokens_max=1400,
                ),
                timeout=timeout_seconds,
                inference_backend=resolved_model.inference_backend,
                reasoning_mode=resolved_model.reasoning_mode,
                context_length=resolved_model.context_length,
                fallback_to_openai_compatible=resolved_model.fallback_to_openai_compatible,
            )
            raw = result["text"]
            break
        except LMStudioError as error:
            last_error = error
            if is_transient_model_unavailable_error(error) and clean_text(context.get("scene_text"), 80):
                raw = f"LOCAL_FALLBACK_MODEL_UNAVAILABLE_STATE_JSON: {error}"
                return fallback_state_payload(context, str(error)), raw, resolved_model
            if "empty scene" in str(error).lower():
                if attempt:
                    break
                continue
            raise
    if not raw and last_error:
        if clean_text(context.get("scene_text"), 80) and "empty scene" in str(last_error).lower():
            raw = f"LOCAL_FALLBACK_EMPTY_STATE_JSON: {last_error}"
            return fallback_state_payload(context, str(last_error)), raw, resolved_model
        raise last_error
    try:
        return supplement_partial_state_payload(context, parse_json_object(raw)), raw, resolved_model
    except (json.JSONDecodeError, ValueError) as error:
        if clean_text(context.get("scene_text"), 80):
            if clean_text(raw, 80):
                raw = f"LOCAL_FALLBACK_MALFORMED_STATE_JSON: {error}\n\nRAW:\n{raw[:40000]}"
            else:
                raw = f"LOCAL_FALLBACK_EMPTY_STATE_JSON: {error}"
            return fallback_state_payload(context, str(error)), raw, resolved_model
        raise


def character_lookup(db, session_id: str) -> dict[str, dict[str, str]]:
    rows = db.execute(
        """
        SELECT c.id, c.name
        FROM session_characters sc
        JOIN characters c ON c.id = sc.character_id
        WHERE sc.session_id = ?
        """,
        (session_id,),
    ).fetchall()
    return {clean_text(row["name"]).lower(): {"id": row["id"], "name": row["name"]} for row in rows}


def resolve_character(lookup: dict[str, dict[str, str]], raw_name: Any) -> tuple[str | None, str]:
    name = clean_text(raw_name, 160)
    if not name:
        return None, ""
    hit = lookup.get(name.lower())
    if hit:
        return hit["id"], hit["name"]
    normalized = name.lower()
    for active_name, active in lookup.items():
        if active_name.startswith(f"{normalized} ") or active_name.split(" ", 1)[0] == normalized:
            return active["id"], active["name"]
    return None, name


def character_state_replace_group(key: str) -> str:
    normalized = normalize_key(key)
    if normalized in CURRENT_CHARACTER_LOCATION_KEYS:
        return "current_location"
    if normalized in CURRENT_CHARACTER_POSITION_KEYS:
        return "room_position"
    if normalized in CURRENT_CHARACTER_OUTFIT_KEYS:
        return "current_outfit"
    if normalized in CURRENT_CHARACTER_POSTURE_KEYS:
        return "current_posture"
    if normalized in CURRENT_CHARACTER_CARRIED_KEYS:
        return "carried_objects"
    if normalized in CURRENT_CHARACTER_KNOWLEDGE_KEYS:
        return "known_secrets"
    if normalized in CURRENT_CHARACTER_EMOTION_KEYS:
        return "emotional_state"
    if normalized in CURRENT_CHARACTER_GOAL_KEYS:
        return normalized
    if normalized in CURRENT_CHARACTER_TENSION_KEYS:
        return "current_relationship_tension"
    for layer in OUTFIT_LAYER_HINTS:
        if layer in normalized:
            return f"outfit_{layer}"
    return ""


def canonical_character_state_key(state_type: str, key: str, value: str) -> str:
    normalized_type = normalize_key(state_type)
    normalized_key = normalize_key(key)
    normalized_value = normalize_key(value)
    if (
        "injury" in normalized_type
        or "wound" in normalized_type
        or "injury" in normalized_key
        or "wound" in normalized_key
        or "scar" in normalized_key
        or any(hint in normalized_value for hint in ("injur", "wound", "scar", "bandage", "healed"))
    ):
        body_key = injury_body_key(normalized_key, normalized_value)
        if body_key and body_key not in {"general", "injury", "wound", "scar"}:
            return f"injury_{body_key}"
        return normalized_key if normalized_key not in {"general"} else "injury_general"
    if normalized_key in CURRENT_CHARACTER_LOCATION_KEYS or normalized_type in {"location", "place"}:
        return "current_location"
    if normalized_key in CURRENT_CHARACTER_POSITION_KEYS or normalized_type in {"position", "blocking", "scene_blocking", "spatial"}:
        return "room_position"
    if normalized_key in CURRENT_CHARACTER_POSTURE_KEYS or normalized_type in {"posture", "current_action", "action"}:
        return "current_posture" if "posture" in normalized_key or "stance" in normalized_value else "current_action"
    if normalized_key in CURRENT_CHARACTER_CARRIED_KEYS or normalized_type in {"inventory", "possession", "holding"}:
        return "carried_objects"
    if normalized_key in CURRENT_CHARACTER_KNOWLEDGE_KEYS or normalized_type in {"knowledge", "secret"}:
        return "known_secrets"
    if normalized_key in CURRENT_CHARACTER_EMOTION_KEYS or normalized_type in {"emotion", "emotional_state"}:
        return "emotional_state"
    if normalized_key in CURRENT_CHARACTER_TENSION_KEYS or normalized_type in {"relationship_tension", "tension"}:
        return "current_relationship_tension"
    return normalized_key


def physical_position_is_usable(value: str) -> bool:
    compact = clean_text(value, 240)
    if len(compact) < 5 or any(re.search(pattern, compact, re.I) for pattern in POSITION_REJECT_PATTERNS):
        return False
    return bool(POSITION_LANDMARK_PATTERN.search(compact) or POSITION_MOVEMENT_PATTERN.search(compact))


def location_value_is_usable(value: str) -> bool:
    compact = clean_text(value, 240)
    if len(compact) < 3 or any(re.search(pattern, compact, re.I) for pattern in POSITION_REJECT_PATTERNS):
        return False
    if compact.lower() in {"present", "here", "there", "somewhere", "unknown", "introduced"}:
        return False
    if POSITION_LANDMARK_PATTERN.search(compact):
        return True
    return len(compact.split()) <= 8 and not re.search(r"\b(feels?|thinks?|wants?|tries?|grips?|hurts?|says?|asks?)\b", compact, re.I)


def archive_replaced_character_state(
    db,
    *,
    session_id: str,
    character_id: str | None,
    character_name: str,
    state_type: str,
    key: str,
    keep_id: str | None = None,
) -> int:
    group = character_state_replace_group(key)
    if not group:
        return 0
    if group == "current_location":
        key_clause = "lower(key) IN ('current_location', 'location', 'current_place')"
    elif group == "room_position":
        key_clause = "lower(key) IN ('room_position', 'position', 'spatial_position', 'blocking_position', 'where_standing', 'near')"
    elif group == "current_outfit":
        key_clause = "lower(key) IN ('current_outfit', 'outfit', 'clothing', 'wearing')"
    elif group == "current_posture":
        key_clause = "lower(key) IN ('posture', 'current_posture', 'current_action', 'action', 'stance')"
    elif group == "carried_objects":
        key_clause = "lower(key) IN ('carried_objects', 'holding', 'held_object', 'inventory', 'current_inventory')"
    elif group == "known_secrets":
        key_clause = "lower(key) IN ('known_secrets', 'knows', 'knowledge', 'secret_known')"
    elif group == "emotional_state":
        key_clause = "lower(key) IN ('emotional_state', 'current_emotional_state', 'mood', 'feeling')"
    elif group in {"short_term_goal", "current_goal", "long_term_goal", "motivation"}:
        key_clause = "lower(key) IN ('short_term_goal', 'current_goal', 'long_term_goal', 'motivation')"
    elif group == "current_relationship_tension":
        key_clause = "lower(key) IN ('current_relationship_tension', 'relationship_tension', 'tension')"
    else:
        layer = group.replace("outfit_", "", 1)
        key_clause = f"lower(key) LIKE '%{layer}%'"
    sql = f"""
        UPDATE character_live_state
        SET archived = 1, review_status = 'superseded'
        WHERE session_id = ?
          AND COALESCE(character_id, '') = COALESCE(?, '')
          AND lower(character_name) = lower(?)
          AND archived = 0
          AND disabled = 0
          AND {key_clause}
          AND (? IS NULL OR id != ?)
    """
    cursor = db.execute(sql, (session_id, character_id, character_name, keep_id, keep_id))
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def insert_character_event(
    db,
    *,
    session_id: str,
    character_id: str | None,
    character_name: str,
    state_type: str,
    key: str,
    value: str,
    previous_value: str | None,
    action: str,
    confidence: float,
    run_id: str,
    scene_id: str,
    version_id: str | None,
) -> None:
    db.execute(
        """
        INSERT INTO character_state_events (
            id, session_id, character_id, character_name, state_type, key, value,
            previous_value, action, confidence, run_id, source_scene_id, source_version_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            session_id,
            character_id,
            character_name,
            state_type,
            key,
            value,
            previous_value,
            action,
            confidence,
            run_id,
            scene_id,
            version_id,
        ),
    )


def upsert_character_state(
    db,
    *,
    session_id: str,
    run_id: str,
    scene_id: str,
    version_id: str | None,
    lookup: dict[str, dict[str, str]],
    update: dict[str, Any],
) -> str | None:
    confidence = calibrated_confidence(update, 0.72)
    if confidence < LOW_CONFIDENCE_SKIP:
        return "Skipped low-confidence character update."
    character_id, character_name = resolve_character(
        lookup,
        update.get("character_name") or update.get("name") or update.get("character"),
    )
    if not character_name:
        return "Skipped character update with no character name."
    state_type = normalize_key(update.get("state_type") or update.get("type") or update.get("category"))
    key = normalize_key(update.get("key") or update.get("field") or update.get("body_area") or update.get("item"))
    value = clean_text(
        update.get("value")
        or update.get("content")
        or update.get("description")
        or update.get("state")
        or update.get("status")
        or update.get("action")
    )
    if not value:
        return "Skipped empty character state update."
    key = canonical_character_state_key(state_type, key, value)
    if key == "room_position" and not physical_position_is_usable(value):
        return "Skipped character position without concrete physical placement or movement."
    if key == "current_location" and not location_value_is_usable(value):
        return "Skipped character location without a concrete place."
    action = normalize_key(update.get("action") or update.get("merge_action") or "update")
    tentative = 1 if confidence < TENTATIVE_CONFIDENCE else 0
    archived = 1 if action in {"remove", "removed", "discard", "resolved"} else 0
    previous_value = None
    existing = db.execute(
        """
        SELECT id, value, manual_override
        FROM character_live_state
        WHERE session_id = ?
          AND COALESCE(character_id, '') = COALESCE(?, '')
          AND lower(character_name) = lower(?)
          AND key = ?
          AND archived = 0
          AND disabled = 0
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (session_id, character_id, character_name, key),
    ).fetchone()
    if existing:
        if existing["manual_override"] and confidence < MANUAL_OVERRIDE_REPLACE_CONFIDENCE:
            return f"Skipped low-confidence update for manual override: {character_name} {key}."
        if normalize_key(existing["value"]) == normalize_key(value) and not archived:
            return None
        previous_value = existing["value"]
        archive_replaced_character_state(
            db,
            session_id=session_id,
            character_id=character_id,
            character_name=character_name,
            state_type=state_type,
            key=key,
            keep_id=existing["id"],
        )
        db.execute(
            """
            UPDATE character_live_state
            SET value = ?, previous_value = ?, confidence = ?, is_tentative = ?,
                archived = ?, manual_override = 0, review_status = 'ok',
                source_scene_id = ?, source_version_id = ?
            WHERE id = ?
            """,
            (value, previous_value, confidence, tentative, archived, scene_id, version_id, existing["id"]),
        )
    else:
        archive_replaced_character_state(
            db,
            session_id=session_id,
            character_id=character_id,
            character_name=character_name,
            state_type=state_type,
            key=key,
        )
        db.execute(
            """
            INSERT INTO character_live_state (
                id, session_id, character_id, character_name, state_type, key, value,
                previous_value, confidence, is_tentative, archived, source_scene_id, source_version_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                session_id,
                character_id,
                character_name,
                state_type,
                key,
                value,
                None,
                confidence,
                tentative,
                archived,
                scene_id,
                version_id,
            ),
        )
    insert_character_event(
        db,
        session_id=session_id,
        character_id=character_id,
        character_name=character_name,
        state_type=state_type,
        key=key,
        value=value,
        previous_value=previous_value,
        action=action,
        confidence=confidence,
        run_id=run_id,
        scene_id=scene_id,
        version_id=version_id,
    )
    return None


def upsert_relationship(
    db,
    *,
    session_id: str,
    run_id: str,
    scene_id: str,
    version_id: str | None,
    lookup: dict[str, dict[str, str]],
    update: dict[str, Any],
) -> str | None:
    confidence = calibrated_confidence(update, 0.72)
    if confidence < LOW_CONFIDENCE_SKIP:
        return "Skipped low-confidence relationship update."
    a_id, a_name = resolve_character(lookup, update.get("character_a_name") or update.get("character_a"))
    b_id, b_name = resolve_character(lookup, update.get("character_b_name") or update.get("character_b"))
    if not a_name or not b_name or a_name.lower() == b_name.lower():
        return "Skipped relationship update without two characters."
    first = (a_name.lower(), a_id, a_name)
    second = (b_name.lower(), b_id, b_name)
    if first[0] > second[0]:
        first, second = second, first
    relationship_type = relationship_type_value(
        update.get("relationship_type")
        or update.get("relationship_kind")
        or update.get("relationship")
        or update.get("type")
    )
    key = normalize_key(update.get("relationship_key") or update.get("key") or update.get("type"), "relationship")
    content = clean_text(update.get("content") or update.get("value") or update.get("description"))
    if not content:
        return "Skipped empty relationship update."
    if normalize_key(content) in {normalize_key(item) for item in GENERIC_RELATIONSHIP_CONTENT}:
        return "Skipped generic relationship filler without a concrete history, want, conflict, or change."
    inferred_weights = inferred_relationship_weights(relationship_type, key, content)
    requested_weights = {
        field: weight_value(update.get(field), inferred_weights.get(field, 0.0))
        for field in RELATIONSHIP_WEIGHT_FIELDS
    }
    if "emotional_importance" not in update:
        requested_weights["emotional_importance"] = max(
            requested_weights["emotional_importance"],
            inferred_weights["emotional_importance"],
        )
    manually_pinned = 1 if update.get("manually_pinned") or update.get("pinned") else 0
    tentative = 1 if confidence < TENTATIVE_CONFIDENCE else 0
    existing = db.execute(
        """
        SELECT id, content, manual_override, relationship_type,
               closeness_weight, trust_weight, conflict_weight, protective_weight, grief_weight,
               romantic_weight, family_weight, betrayal_weight, respect_weight, fear_weight,
               emotional_importance, manually_pinned
        FROM relationship_state
        WHERE session_id = ?
          AND (
                (lower(character_a_name) = lower(?) AND lower(character_b_name) = lower(?))
                OR (lower(character_a_name) = lower(?) AND lower(character_b_name) = lower(?))
          )
          AND archived = 0
          AND disabled = 0
        ORDER BY manual_override DESC, manually_pinned DESC, emotional_importance DESC, updated_at DESC
        LIMIT 1
        """,
        (session_id, first[2], second[2], second[2], first[2]),
    ).fetchone()
    previous_content = None
    if existing:
        if existing["manual_override"] and confidence < MANUAL_OVERRIDE_REPLACE_CONFIDENCE:
            return f"Skipped low-confidence update for manual override relationship: {first[2]} / {second[2]}."
        merged_weights = {}
        for field, requested_value in requested_weights.items():
            if field in update:
                merged_weights[field] = requested_value
            else:
                merged_weights[field] = max(weight_value(existing[field]), requested_value)
        next_type = relationship_type
        if next_type == "unknown" and existing["relationship_type"]:
            next_type = existing["relationship_type"]
        unchanged_weights = all(
            abs(weight_value(existing[field]) - merged_weights[field]) < 0.0001
            for field in RELATIONSHIP_WEIGHT_FIELDS
        )
        if (
            normalize_key(existing["content"]) == normalize_key(content)
            and next_type == existing["relationship_type"]
            and unchanged_weights
        ):
            return None
        previous_content = existing["content"]
        db.execute(
            """
            UPDATE relationship_state
            SET character_a_id = ?, character_a_name = ?, character_b_id = ?, character_b_name = ?,
                relationship_type = ?, relationship_key = ?, content = ?, previous_content = ?, confidence = ?, is_tentative = ?,
                closeness_weight = ?, trust_weight = ?, conflict_weight = ?, protective_weight = ?,
                grief_weight = ?, romantic_weight = ?, family_weight = ?, betrayal_weight = ?,
                respect_weight = ?, fear_weight = ?, emotional_importance = ?,
                last_reinforced_scene_id = ?,
                manual_override = 0, manually_pinned = ?, review_status = 'ok',
                source_scene_id = ?, source_version_id = ?
            WHERE id = ?
            """,
            (
                first[1],
                first[2],
                second[1],
                second[2],
                next_type,
                key,
                content,
                existing["content"],
                confidence,
                tentative,
                merged_weights["closeness_weight"],
                merged_weights["trust_weight"],
                merged_weights["conflict_weight"],
                merged_weights["protective_weight"],
                merged_weights["grief_weight"],
                merged_weights["romantic_weight"],
                merged_weights["family_weight"],
                merged_weights["betrayal_weight"],
                merged_weights["respect_weight"],
                merged_weights["fear_weight"],
                merged_weights["emotional_importance"],
                scene_id,
                1 if manually_pinned or existing["manually_pinned"] else 0,
                scene_id,
                version_id,
                existing["id"],
            ),
        )
    else:
        db.execute(
            """
            INSERT INTO relationship_state (
                id, session_id, character_a_id, character_a_name, character_b_id,
                character_b_name, relationship_type, relationship_key, content,
                closeness_weight, trust_weight, conflict_weight, protective_weight,
                grief_weight, romantic_weight, family_weight, betrayal_weight,
                respect_weight, fear_weight, emotional_importance, last_reinforced_scene_id,
                confidence, is_tentative, manually_pinned, source_scene_id, source_version_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                session_id,
                first[1],
                first[2],
                second[1],
                second[2],
                relationship_type,
                key,
                content,
                requested_weights["closeness_weight"],
                requested_weights["trust_weight"],
                requested_weights["conflict_weight"],
                requested_weights["protective_weight"],
                requested_weights["grief_weight"],
                requested_weights["romantic_weight"],
                requested_weights["family_weight"],
                requested_weights["betrayal_weight"],
                requested_weights["respect_weight"],
                requested_weights["fear_weight"],
                requested_weights["emotional_importance"],
                scene_id,
                confidence,
                tentative,
                manually_pinned,
                scene_id,
                version_id,
            ),
        )
    db.execute(
        """
        INSERT INTO relationship_state_events (
            id, session_id, character_a_name, character_b_name, relationship_type,
            relationship_key, content, previous_content, confidence, run_id,
            source_scene_id, source_version_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            session_id,
            first[2],
            second[2],
            relationship_type,
            key,
            content,
            previous_content,
            confidence,
            run_id,
            scene_id,
            version_id,
        ),
    )
    return None


def upsert_emotional_memory(
    db,
    *,
    run_id: str,
    session_id: str,
    scene_id: str,
    version_id: str | None,
    update: dict[str, Any],
) -> str | None:
    confidence = calibrated_confidence(update, 0.72)
    if confidence < LOW_CONFIDENCE_SKIP:
        return "Skipped low-confidence emotional memory update."
    memory_type = normalize_key(update.get("memory_type") or update.get("type") or "emotional", "emotional")
    if memory_type not in EMOTIONAL_MEMORY_TYPES:
        memory_type = "emotional"
    memory_text = clean_text(
        update.get("memory_text")
        or update.get("content")
        or update.get("value")
        or update.get("description"),
        1200,
    )
    if not memory_text:
        return "Skipped empty emotional memory update."
    emotional_weight = weight_value(update.get("emotional_weight") or update.get("weight") or update.get("importance"))
    if emotional_weight <= 0:
        emotional_weight = max(0.45, confidence)
    themes_json = json_list_text(update.get("themes") or update.get("themes_json"), limit=10, item_limit=80)
    related_json = json_list_text(
        update.get("related_characters") or update.get("related_characters_json"),
        limit=10,
        item_limit=120,
    )
    trigger_conditions = clean_text(update.get("trigger_conditions") or update.get("triggers"), 800)
    cooldown_scenes = int(update.get("cooldown_scenes") or 3)
    cooldown_scenes = max(0, min(50, cooldown_scenes))
    tentative = 1 if confidence < TENTATIVE_CONFIDENCE else 0
    manually_pinned = 1 if update.get("manually_pinned") or update.get("pinned") else 0
    memory_key = clean_text(memory_text, 180).lower()
    existing = db.execute(
        """
        SELECT id, memory_text, emotional_weight, manual_override, manually_pinned
        FROM emotional_memories
        WHERE session_id = ?
          AND memory_type = ?
          AND lower(substr(memory_text, 1, 180)) = ?
          AND archived = 0
          AND disabled = 0
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (session_id, memory_type, memory_key),
    ).fetchone()
    if existing:
        if existing["manual_override"] and confidence < MANUAL_OVERRIDE_REPLACE_CONFIDENCE:
            return "Skipped low-confidence update for manual override emotional memory."
        db.execute(
            """
            UPDATE emotional_memories
            SET memory_text = ?, emotional_weight = ?, themes_json = ?,
                related_characters_json = ?, trigger_conditions = ?, cooldown_scenes = ?,
                confidence = ?, is_tentative = ?, manual_override = 0,
                manually_pinned = ?, review_status = 'ok',
                run_id = ?, source_scene_id = ?, source_version_id = ?
            WHERE id = ?
            """,
            (
                memory_text,
                max(weight_value(existing["emotional_weight"]), emotional_weight),
                themes_json,
                related_json,
                trigger_conditions,
                cooldown_scenes,
                confidence,
                tentative,
                1 if manually_pinned or existing["manually_pinned"] else 0,
                run_id,
                scene_id,
                version_id,
                existing["id"],
            ),
        )
        memory_id = existing["id"]
    else:
        memory_id = str(uuid4())
        db.execute(
            """
            INSERT INTO emotional_memories (
                id, session_id, memory_type, memory_text, emotional_weight, themes_json,
                related_characters_json, trigger_conditions, cooldown_scenes, confidence,
                is_tentative, manually_pinned, run_id, source_scene_id, source_version_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                session_id,
                memory_type,
                memory_text,
                emotional_weight,
                themes_json,
                related_json,
                trigger_conditions,
                cooldown_scenes,
                confidence,
                tentative,
                manually_pinned,
                run_id,
                scene_id,
                version_id,
            ),
        )
    db.execute(
        """
        INSERT INTO character_state_events (
            id, session_id, character_id, character_name, state_type, key, value,
            previous_value, action, confidence, run_id, source_scene_id, source_version_id
        )
        VALUES (?, ?, NULL, ?, 'emotional_memory', ?, ?, NULL, 'update', ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            session_id,
            ", ".join(safe_json_list(related_json, limit=4)) or "Story",
            memory_type,
            memory_text,
            confidence,
            run_id,
            scene_id,
            version_id,
        ),
    )
    return None


def upsert_keyed_state(
    db,
    *,
    table: str,
    session_id: str,
    scene_id: str,
    version_id: str | None,
    update: dict[str, Any],
) -> str | None:
    confidence = calibrated_confidence(update, 0.72)
    if confidence < LOW_CONFIDENCE_SKIP:
        return f"Skipped low-confidence {table} update."
    state_type = normalize_key(update.get("state_type") or update.get("type") or "world")
    key = normalize_key(update.get("key") or update.get("field") or update.get("name"))
    value = clean_text(update.get("value") or update.get("content") or update.get("description"))
    if not value:
        return f"Skipped empty {table} update."
    tentative = 1 if confidence < TENTATIVE_CONFIDENCE else 0
    existing = db.execute(
        f"""
        SELECT id, value, manual_override
        FROM {table}
        WHERE session_id = ? AND state_type = ? AND key = ? AND archived = 0 AND disabled = 0
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (session_id, state_type, key),
    ).fetchone()
    if existing:
        if existing["manual_override"] and confidence < MANUAL_OVERRIDE_REPLACE_CONFIDENCE:
            return f"Skipped low-confidence update for manual override: {key}."
        if normalize_key(existing["value"]) == normalize_key(value):
            return None
        db.execute(
            f"""
            UPDATE {table}
            SET value = ?, previous_value = ?, confidence = ?, is_tentative = ?,
                manual_override = 0, review_status = 'ok',
                source_scene_id = ?, source_version_id = ?
            WHERE id = ?
            """,
            (value, existing["value"], confidence, tentative, scene_id, version_id, existing["id"]),
        )
    else:
        db.execute(
            f"""
            INSERT INTO {table} (
                id, session_id, state_type, key, value, confidence, is_tentative,
                source_scene_id, source_version_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid4()), session_id, state_type, key, value, confidence, tentative, scene_id, version_id),
        )
    return None


def upsert_scene_state(
    db,
    *,
    session_id: str,
    scene_id: str,
    version_id: str | None,
    update: dict[str, Any],
) -> str | None:
    confidence = calibrated_confidence(update, 0.72)
    if confidence < LOW_CONFIDENCE_SKIP:
        return "Skipped low-confidence scene update."
    state_type = normalize_key(update.get("state_type") or update.get("type") or "scene")
    key = normalize_key(update.get("key") or update.get("field") or update.get("name"))
    value = clean_text(update.get("value") or update.get("content") or update.get("description"))
    if not value:
        return "Skipped empty scene update."
    if key in {"current_location", "location", "current_place"} and not location_value_is_usable(value):
        return "Skipped scene location without a concrete place."
    if key in {"present_characters", "entering", "leaving", "entries_exits"} and "introduced or present" in value.lower():
        return "Skipped generic presence filler without a concrete entry, exit, or blocking fact."
    tentative = 1 if confidence < TENTATIVE_CONFIDENCE else 0
    if key in CURRENT_SCENE_KEYS:
        existing = db.execute(
            """
            SELECT id, value, manual_override
            FROM scene_live_state
            WHERE session_id = ? AND key = ?
              AND archived = 0 AND disabled = 0
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (session_id, key),
        ).fetchone()
    else:
        existing = db.execute(
            """
            SELECT id, value, manual_override
            FROM scene_live_state
            WHERE session_id = ? AND scene_id = ? AND COALESCE(version_id, '') = COALESCE(?, '')
              AND state_type = ? AND key = ? AND archived = 0 AND disabled = 0
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (session_id, scene_id, version_id, state_type, key),
        ).fetchone()
    if existing and existing["manual_override"] and confidence < MANUAL_OVERRIDE_REPLACE_CONFIDENCE:
        return f"Skipped low-confidence update for manual override scene state: {key}."
    if existing and normalize_key(existing["value"]) == normalize_key(value):
        return None
    if key in CURRENT_SCENE_KEYS:
        db.execute(
            """
            UPDATE scene_live_state
            SET archived = 1
            WHERE session_id = ? AND key = ? AND archived = 0 AND disabled = 0
            """,
            (session_id, key),
        )
    else:
        db.execute(
            """
            UPDATE scene_live_state
            SET archived = 1
            WHERE session_id = ? AND scene_id = ? AND COALESCE(version_id, '') = COALESCE(?, '')
              AND state_type = ? AND key = ? AND archived = 0 AND disabled = 0
            """,
            (session_id, scene_id, version_id, state_type, key),
        )
    db.execute(
        """
        INSERT INTO scene_live_state (
            id, session_id, scene_id, version_id, state_type, key, value,
            confidence, is_tentative, archived, source_scene_id, source_version_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (str(uuid4()), session_id, scene_id, version_id, state_type, key, value, confidence, tentative, scene_id, version_id),
    )
    return None


def archive_replaced_object_holder(
    db,
    *,
    session_id: str,
    object_key: str,
    object_name: str = "",
    holder_character_name: str,
    keep_id: str | None = None,
) -> int:
    if not holder_character_name:
        return 0
    cursor = db.execute(
        """
        UPDATE object_state
        SET archived = 1, review_status = 'superseded'
        WHERE session_id = ?
          AND (
                lower(object_key) = lower(?)
                OR (? != '' AND lower(name) = lower(?))
          )
          AND holder_character_name != ''
          AND lower(holder_character_name) != lower(?)
          AND archived = 0
          AND disabled = 0
          AND (? IS NULL OR id != ?)
        """,
        (session_id, object_key, object_name, object_name, holder_character_name, keep_id, keep_id),
    )
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def upsert_object_state(
    db,
    *,
    session_id: str,
    scene_id: str,
    version_id: str | None,
    lookup: dict[str, dict[str, str]],
    update: dict[str, Any],
) -> str | None:
    confidence = calibrated_confidence(update, 0.72)
    if confidence < LOW_CONFIDENCE_SKIP:
        return "Skipped low-confidence object update."
    name = normalize_object_identity(update.get("name") or update.get("object_name") or update.get("object_key"))
    object_key = normalize_key(normalize_object_identity(update.get("object_key") or name))
    if not name or not object_key:
        return "Skipped malformed object identity."
    state_type = normalize_key(update.get("state_type") or update.get("type") or "object")
    if state_type in {"owner", "owned_by", "carried_by", "held_by", "possession"}:
        state_type = "ownership"
    value = clean_text(
        update.get("value")
        or update.get("content")
        or update.get("description")
        or update.get("state")
        or update.get("status")
        or update.get("action")
    )
    if not value:
        return "Skipped empty object update."
    owner_raw = update.get("owner_character_name") or update.get("owner")
    owner_supplied = bool(clean_text(owner_raw)) or bool(update.get("clear_owner"))
    holder_supplied = any(key in update for key in ("holder_character_name", "holder", "held_by", "carried_by"))
    owner_id, owner_name = resolve_character(lookup, owner_raw)
    holder_id, holder_name = resolve_character(
        lookup,
        update.get("holder_character_name") or update.get("holder") or update.get("held_by") or update.get("carried_by"),
    )
    if state_type == "location" and not holder_supplied:
        holder_id, holder_name = None, ""
        holder_supplied = True
    explicit_location = clean_text(update.get("current_location") or update.get("location"), 220)
    placement_supplied = any(key in update for key in ("placement_state", "placement"))
    placement_state = normalize_key(update.get("placement_state") or update.get("placement") or "")
    if not placement_state:
        placement_state = "placed" if state_type == "location" else "held" if holder_name else "unknown"
    if placement_state not in {"held", "worn", "placed", "stored", "hidden", "lost", "destroyed", "unknown"}:
        placement_state = "unknown"
    condition = clean_text(update.get("condition"), 180)
    visibility = normalize_key(update.get("visibility") or "unknown")
    if visibility not in {"visible", "hidden", "concealed", "unknown"}:
        visibility = "unknown"
    importance = weight_value(update.get("importance"), 0.0)
    tentative = 1 if confidence < TENTATIVE_CONFIDENCE else 0
    archived = 1 if normalize_key(update.get("action") or "update") in {"remove", "removed", "lost", "destroyed"} else 0
    existing = db.execute(
        """
        SELECT id, object_key, name, value, owner_character_id, owner_character_name,
               holder_character_id, holder_character_name, current_location, placement_state,
               condition, visibility, importance, manual_override
        FROM object_state
        WHERE session_id = ?
          AND archived = 0
          AND disabled = 0
          AND (
                lower(object_key) = lower(?)
                OR (? != '' AND lower(name) = lower(?))
          )
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (session_id, object_key, name, name),
    ).fetchone()
    if existing is None:
        sought_tokens = {token for token in object_key.split("_") if token}
        sought_core = object_key.split("_")[-1] if object_key else ""
        alias_candidates = []
        for row in db.execute(
            """
            SELECT id, object_key, name, value, owner_character_id, owner_character_name,
                   holder_character_id, holder_character_name, current_location, placement_state,
                   condition, visibility, importance, manual_override
            FROM object_state
            WHERE session_id = ? AND archived = 0 AND disabled = 0
            """,
            (session_id,),
        ).fetchall():
            candidate_key = normalize_key(row["object_key"] or row["name"])
            candidate_tokens = {token for token in candidate_key.split("_") if token}
            candidate_core = candidate_key.split("_")[-1] if candidate_key else ""
            if (
                sought_tokens
                and candidate_tokens
                and sought_core == candidate_core
                and (sought_tokens <= candidate_tokens or candidate_tokens <= sought_tokens)
            ):
                alias_candidates.append(row)
        if len(alias_candidates) == 1:
            existing = alias_candidates[0]
            object_key = existing["object_key"]
            name = existing["name"] or name
    if existing:
        if existing["manual_override"] and confidence < MANUAL_OVERRIDE_REPLACE_CONFIDENCE:
            return f"Skipped low-confidence update for manual override object: {name or object_key}."
        next_owner_id = owner_id if owner_supplied else existing["owner_character_id"]
        next_owner_name = owner_name if owner_supplied else existing["owner_character_name"]
        next_holder_id = holder_id if holder_supplied else existing["holder_character_id"]
        next_holder_name = holder_name if holder_supplied else existing["holder_character_name"]
        if explicit_location:
            next_location = explicit_location
        elif state_type == "location":
            next_location = value
        elif holder_supplied and holder_name and placement_state in {"held", "worn"}:
            next_location = f"with {holder_name}"
        else:
            next_location = existing["current_location"]
        previous_value = clean_text(existing["value"], 320)
        previous_location = clean_text(existing["current_location"], 180)
        if previous_location and previous_location.lower() not in previous_value.lower():
            previous_value = f"{previous_value}; previous location {previous_location}".strip("; ")
        next_condition = condition or existing["condition"]
        next_visibility = visibility if "visibility" in update else existing["visibility"]
        next_importance = importance if "importance" in update else float(existing["importance"] or 0)
        next_placement = (
            placement_state
            if placement_supplied or state_type in {"location", "ownership"}
            else existing["placement_state"]
        )
        archive_replaced_object_holder(
            db,
            session_id=session_id,
            object_key=object_key,
            object_name=name,
            holder_character_name=next_holder_name,
            keep_id=existing["id"],
        )
        db.execute(
            """
            UPDATE object_state
            SET name = ?, state_type = ?, value = ?, previous_value = ?, owner_character_id = ?,
                owner_character_name = ?, holder_character_id = ?, holder_character_name = ?,
                current_location = ?, placement_state = ?, condition = ?, visibility = ?, importance = ?,
                confidence = ?, is_tentative = ?, archived = ?,
                manual_override = 0, review_status = 'ok',
                source_scene_id = ?, source_version_id = ?
            WHERE id = ?
            """,
            (
                name,
                state_type,
                value,
                previous_value,
                next_owner_id,
                next_owner_name,
                next_holder_id,
                next_holder_name,
                next_location,
                next_placement,
                next_condition,
                next_visibility,
                next_importance,
                confidence,
                tentative,
                archived,
                scene_id,
                version_id,
                existing["id"],
            ),
        )
    else:
        archive_replaced_object_holder(
            db,
            session_id=session_id,
            object_key=object_key,
            object_name=name,
            holder_character_name=holder_name,
        )
        db.execute(
            """
            INSERT INTO object_state (
                id, session_id, object_key, name, state_type, value,
                owner_character_id, owner_character_name, holder_character_id, holder_character_name,
                current_location, placement_state, condition, visibility, importance, confidence, is_tentative,
                archived, source_scene_id, source_version_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                session_id,
                object_key,
                name,
                state_type,
                value,
                owner_id,
                owner_name,
                holder_id,
                holder_name,
                explicit_location
                or (value if state_type == "location" else f"with {holder_name}" if holder_name and placement_state in {"held", "worn"} else ""),
                placement_state,
                condition,
                visibility,
                importance,
                confidence,
                tentative,
                archived,
                scene_id,
                version_id,
            ),
        )
    return None


def upsert_plot_thread(
    db,
    *,
    session_id: str,
    scene_id: str,
    version_id: str | None,
    update: dict[str, Any],
) -> str | None:
    confidence = calibrated_confidence(update, 0.72)
    if confidence < LOW_CONFIDENCE_SKIP:
        return "Skipped low-confidence plot thread update."
    title = clean_text(update.get("title") or update.get("name") or update.get("thread_name") or update.get("thread_key"), 220)
    thread_key = normalize_key(update.get("thread_key") or title)
    status = normalize_key(update.get("status") or update.get("action") or "active")
    if status in {"resolve", "resolved", "complete", "completed"}:
        status = "resolved"
    elif status in {"archive", "archived"}:
        status = "archived"
    else:
        status = "active"
    content = clean_text(
        update.get("content")
        or update.get("value")
        or update.get("description")
        or update.get("expiration_condition")
        or update.get("status")
    )
    if not content and not title:
        return "Skipped empty plot thread update."
    tentative = 1 if confidence < TENTATIVE_CONFIDENCE else 0
    existing = db.execute(
        """
        SELECT id, content, manual_override
        FROM plot_threads
        WHERE session_id = ? AND thread_key = ? AND disabled = 0
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (session_id, thread_key),
    ).fetchone()
    if existing:
        if existing["manual_override"] and confidence < MANUAL_OVERRIDE_REPLACE_CONFIDENCE:
            return f"Skipped low-confidence update for manual override plot thread: {title or thread_key}."
        db.execute(
            """
            UPDATE plot_threads
            SET title = ?, status = ?, content = ?, previous_content = ?, confidence = ?,
                is_tentative = ?, manual_override = 0, review_status = 'ok',
                source_scene_id = ?, source_version_id = ?,
                resolved_scene_id = ?, resolved_version_id = ?
            WHERE id = ?
            """,
            (
                title,
                status,
                content or existing["content"],
                existing["content"],
                confidence,
                tentative,
                scene_id,
                version_id,
                scene_id if status == "resolved" else None,
                version_id if status == "resolved" else None,
                existing["id"],
            ),
        )
    else:
        db.execute(
            """
            INSERT INTO plot_threads (
                id, session_id, thread_key, title, status, content, confidence,
                is_tentative, source_scene_id, source_version_id,
                resolved_scene_id, resolved_version_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                session_id,
                thread_key,
                title,
                status,
                content,
                confidence,
                tentative,
                scene_id,
                version_id,
                scene_id if status == "resolved" else None,
                version_id if status == "resolved" else None,
            ),
        )
    return None


def array_of_dicts(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def merge_state_payload(
    *,
    run_id: str,
    session_id: str,
    scene_id: str,
    version_id: str | None,
    payload: dict[str, Any],
) -> list[str]:
    warnings: list[str] = [clean_text(item, 600) for item in payload.get("warnings", []) if clean_text(item)]
    summary_notes = [clean_text(item, 600) for item in payload.get("summary_notes", []) if clean_text(item)]
    warnings.extend([f"Summary note: {note}" for note in summary_notes[:8]])
    with db_session() as db:
        superseded_count = archive_superseded_scene_version_state(
            db,
            session_id=session_id,
            scene_id=scene_id,
            version_id=version_id,
        )
        if superseded_count:
            warnings.append(f"Archived {superseded_count} state item(s) from superseded versions of this scene.")
        lookup = character_lookup(db, session_id)
        for item in array_of_dicts(payload, "character_updates"):
            warning = upsert_character_state(
                db,
                session_id=session_id,
                run_id=run_id,
                scene_id=scene_id,
                version_id=version_id,
                lookup=lookup,
                update=item,
            )
            if warning:
                warnings.append(warning)
        for item in array_of_dicts(payload, "relationship_updates"):
            warning = upsert_relationship(
                db,
                session_id=session_id,
                run_id=run_id,
                scene_id=scene_id,
                version_id=version_id,
                lookup=lookup,
                update=item,
            )
            if warning:
                warnings.append(warning)
        for item in array_of_dicts(payload, "emotional_memory_updates"):
            warning = upsert_emotional_memory(
                db,
                run_id=run_id,
                session_id=session_id,
                scene_id=scene_id,
                version_id=version_id,
                update=item,
            )
            if warning:
                warnings.append(warning)
        for item in array_of_dicts(payload, "world_updates"):
            warning = upsert_keyed_state(
                db,
                table="world_live_state",
                session_id=session_id,
                scene_id=scene_id,
                version_id=version_id,
                update=item,
            )
            if warning:
                warnings.append(warning)
        for item in array_of_dicts(payload, "scene_updates"):
            warning = upsert_scene_state(
                db,
                session_id=session_id,
                scene_id=scene_id,
                version_id=version_id,
                update=item,
            )
            if warning:
                warnings.append(warning)
        for item in array_of_dicts(payload, "object_updates"):
            warning = upsert_object_state(
                db,
                session_id=session_id,
                scene_id=scene_id,
                version_id=version_id,
                lookup=lookup,
                update=item,
            )
            if warning:
                warnings.append(warning)
        for item in array_of_dicts(payload, "plot_thread_updates"):
            warning = upsert_plot_thread(
                db,
                session_id=session_id,
                scene_id=scene_id,
                version_id=version_id,
                update=item,
            )
            if warning:
                warnings.append(warning)
        snapshot = build_snapshot(db, session_id)
        db.execute(
            """
            INSERT INTO state_snapshots (id, session_id, run_id, snapshot_json)
            VALUES (?, ?, ?, ?)
            """,
            (str(uuid4()), session_id, run_id, json.dumps(snapshot, ensure_ascii=False)),
        )
    return warnings[:40]


def build_snapshot(db, session_id: str) -> dict[str, Any]:
    characters = db.execute(
        """
        SELECT character_name, state_type, key, value, confidence
        FROM character_live_state
        WHERE session_id = ? AND archived = 0 AND disabled = 0
        ORDER BY character_name ASC, state_type ASC, key ASC
        LIMIT 160
        """,
        (session_id,),
    ).fetchall()
    threads = db.execute(
        """
        SELECT title, status, content, confidence
        FROM plot_threads
        WHERE session_id = ? AND status = 'active' AND disabled = 0
        ORDER BY updated_at DESC
        LIMIT 40
        """,
        (session_id,),
    ).fetchall()
    objects = db.execute(
        """
        SELECT name, object_key, state_type, value, owner_character_name, holder_character_name,
               current_location, placement_state, condition, visibility, importance, confidence
        FROM object_state
        WHERE session_id = ? AND archived = 0 AND disabled = 0
        ORDER BY updated_at DESC
        LIMIT 60
        """,
        (session_id,),
    ).fetchall()
    relationships = db.execute(
        """
        SELECT character_a_name, character_b_name, relationship_type, relationship_key,
               content, emotional_importance, confidence
        FROM relationship_state
        WHERE session_id = ? AND archived = 0 AND disabled = 0
        ORDER BY emotional_importance DESC, updated_at DESC
        LIMIT 40
        """,
        (session_id,),
    ).fetchall()
    emotional = db.execute(
        """
        SELECT memory_type, memory_text, emotional_weight, themes_json,
               related_characters_json, confidence
        FROM emotional_memories
        WHERE session_id = ? AND archived = 0 AND disabled = 0
        ORDER BY emotional_weight DESC, updated_at DESC
        LIMIT 40
        """,
        (session_id,),
    ).fetchall()
    return {
        "characters": [{key: row[key] for key in row.keys()} for row in characters],
        "relationships": [{key: row[key] for key in row.keys()} for row in relationships],
        "emotional_memories": [{key: row[key] for key in row.keys()} for row in emotional],
        "plot_threads": [{key: row[key] for key in row.keys()} for row in threads],
        "objects": [{key: row[key] for key in row.keys()} for row in objects],
    }


def create_run(session_id: str, scene_id: str, version_id: str | None) -> str:
    run_id = str(uuid4())
    with db_session() as db:
        db.execute(
            """
            INSERT INTO story_state_runs (id, session_id, scene_id, version_id, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (run_id, session_id, scene_id, version_id),
        )
    return run_id


def update_run(run_id: str, **fields: Any) -> StoryStateRunRead | None:
    allowed = {"status", "started_at", "completed_at", "error", "raw_response", "warnings_json"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    with db_session() as db:
        if updates:
            assignments = ", ".join(f"{key} = ?" for key in updates)
            db.execute(f"UPDATE story_state_runs SET {assignments} WHERE id = ?", (*updates.values(), run_id))
        row = db.execute("SELECT * FROM story_state_runs WHERE id = ?", (run_id,)).fetchone()
    return row_to_run(row) if row else None


async def run_story_state_extraction(
    *,
    session_id: str,
    scene_id: str,
    version_id: str | None,
    force: bool = False,
) -> StoryStateRunRead:
    if not force and not load_story_state_settings().automatic_story_state:
        run_id = create_run(session_id, scene_id, version_id)
        update_last_state_event(session_id=session_id, scene_id=scene_id, version_id=version_id, status="skipped")
        return update_run(
            run_id,
            status="skipped",
            completed_at=utc_now(),
            warnings_json=json.dumps(["Automatic Story State is off."]),
        )

    run_id = create_run(session_id, scene_id, version_id)
    update_last_state_event(session_id=session_id, scene_id=scene_id, version_id=version_id, status="running")
    update_run(
        run_id,
        status="running",
        started_at=utc_now(),
    )
    try:
        context = load_extraction_context(session_id, scene_id, version_id)
        if not clean_text(context.get("scene_text"), 40):
            message = "State extraction skipped because the saved scene/version text was empty."
            update_last_state_event(
                session_id=session_id,
                scene_id=scene_id,
                version_id=context.get("version_id") or version_id,
                status="skipped",
                error=message,
            )
            return update_run(
                run_id,
                status="skipped",
                completed_at=utc_now(),
                error=message,
                warnings_json=json.dumps([message], ensure_ascii=False),
            )
        payload, raw_response, resolved_model = await extract_state_json(context)
        resolved_version_id = context.get("version_id") or version_id
        warnings = merge_state_payload(
            run_id=run_id,
            session_id=session_id,
            scene_id=scene_id,
            version_id=resolved_version_id,
            payload=payload,
        )
        update_last_state_event(
            session_id=session_id,
            scene_id=scene_id,
            version_id=resolved_version_id,
            status="completed",
            task_type=resolved_model.task_type,
            model=resolved_model.model or None,
            timeout_seconds=resolved_model.timeout_seconds,
        )
        return update_run(
            run_id,
            status="completed",
            completed_at=utc_now(),
            raw_response=raw_response[:50000],
            warnings_json=json.dumps(warnings, ensure_ascii=False),
        )
    except Exception as error:
        message = str(error)
        update_last_state_event(
            session_id=session_id,
            scene_id=scene_id,
            version_id=version_id,
            status="failed",
            error=message,
        )
        return update_run(
            run_id,
            status="failed",
            completed_at=utc_now(),
            error=message,
        )


def schedule_story_state_extraction(
    *,
    session_id: str,
    scene_id: str,
    version_id: str | None,
    delay_seconds: float = 0.0,
) -> None:
    state_settings = load_story_state_settings()
    if not state_settings.automatic_story_state:
        update_last_state_event(session_id=session_id, scene_id=scene_id, version_id=version_id, status="skipped")
        return
    if not state_settings.run_state_extraction_in_background:
        update_last_state_event(
            session_id=session_id,
            scene_id=scene_id,
            version_id=version_id,
            status="background_off",
        )
        return

    async def runner() -> None:
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        await run_story_state_extraction(
            session_id=session_id,
            scene_id=scene_id,
            version_id=version_id,
        )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(runner())

    def consume_exception(completed: asyncio.Task) -> None:
        try:
            completed.exception()
        except asyncio.CancelledError:
            return

    task.add_done_callback(consume_exception)


def get_story_state_overview(session_id: str) -> StoryStateOverview:
    settings = load_story_state_settings()
    prompt_context, prompt_item_count = format_story_state_for_prompt(session_id)
    next_prompt_memory_pack = build_next_prompt_memory_pack(
        session_id,
        context_kind="overview",
        limit=5200,
        cache=False,
    )
    with db_session() as db:
        session = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found.")
        run_row = db.execute(
            """
            SELECT *
            FROM story_state_runs
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        character_rows = db.execute(
            """
            SELECT
                c.id, c.name, c.role, c.appearance, c.current_state, sc.is_active
            FROM session_characters sc
            JOIN characters c ON c.id = sc.character_id
            WHERE sc.session_id = ?
            ORDER BY sc.is_active DESC, lower(c.name) ASC
            """,
            (session_id,),
        ).fetchall()
        live_rows = db.execute(
            """
            SELECT *
            FROM character_live_state
            WHERE session_id = ?
            ORDER BY archived ASC, disabled ASC, character_name ASC, state_type ASC, key ASC, updated_at DESC
            LIMIT 180
            """,
            (session_id,),
        ).fetchall()
        relationship_rows = db.execute(
            """
            SELECT *
            FROM relationship_state
            WHERE session_id = ?
            ORDER BY archived ASC, disabled ASC, manually_pinned DESC, emotional_importance DESC, updated_at DESC
            LIMIT 80
            """,
            (session_id,),
        ).fetchall()
        emotional_rows = db.execute(
            """
            SELECT *
            FROM emotional_memories
            WHERE session_id = ?
            ORDER BY archived ASC, disabled ASC, manually_pinned DESC, emotional_weight DESC, updated_at DESC
            LIMIT 80
            """,
            (session_id,),
        ).fetchall()
        world_rows = db.execute(
            """
            SELECT *
            FROM world_live_state
            WHERE session_id = ?
            ORDER BY archived ASC, disabled ASC, updated_at DESC
            LIMIT 80
            """,
            (session_id,),
        ).fetchall()
        scene_rows = db.execute(
            """
            SELECT *
            FROM scene_live_state
            WHERE session_id = ?
            ORDER BY archived ASC, disabled ASC, updated_at DESC
            LIMIT 80
            """,
            (session_id,),
        ).fetchall()
        object_rows = db.execute(
            """
            SELECT *
            FROM object_state
            WHERE session_id = ?
            ORDER BY archived ASC, disabled ASC, updated_at DESC
            LIMIT 80
            """,
            (session_id,),
        ).fetchall()
        plot_rows = db.execute(
            """
            SELECT *
            FROM plot_threads
            WHERE session_id = ?
            ORDER BY disabled ASC, status ASC, updated_at DESC
            LIMIT 80
            """,
            (session_id,),
        ).fetchall()
        event_rows = db.execute(
            """
            SELECT *
            FROM character_state_events
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT 30
            """,
            (session_id,),
        ).fetchall()
    return StoryStateOverview(
        session_id=session_id,
        settings=settings,
        latest_run=row_to_run(run_row) if run_row else None,
        prompt_context=prompt_context,
        prompt_item_count=prompt_item_count,
        memory_pack_context=next_prompt_memory_pack.get("rendered_context", ""),
        memory_pack_item_count=int(next_prompt_memory_pack.get("item_count") or 0),
        next_prompt_memory_pack=next_prompt_memory_pack,
        archived_item_count=archived_state_count(session_id),
        disabled_item_count=disabled_state_count(session_id),
        manual_override_count=manual_override_count(session_id),
        visual_prompt_context=format_visual_story_state_for_prompt(session_id),
        summary_status=summary_status(session_id),
        conflicts=detect_story_state_conflicts(session_id),
        active_characters=[{key: row[key] for key in row.keys()} for row in character_rows],
        character_live_state=[row_to_character_state(row) for row in live_rows],
        relationships=[row_to_relationship(row) for row in relationship_rows],
        emotional_memories=[row_to_emotional_memory(row) for row in emotional_rows],
        world_state=[row_to_world_state(row) for row in world_rows],
        scene_state=[row_to_scene_state(row) for row in scene_rows],
        objects=[row_to_object_state(row) for row in object_rows],
        plot_threads=[row_to_plot_thread(row) for row in plot_rows],
        recent_events=[row_to_event(row) for row in event_rows],
    )
