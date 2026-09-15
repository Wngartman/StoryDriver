from __future__ import annotations

import json
import re
from pathlib import Path
from time import perf_counter, time
from typing import Any

from app.config import DATA_DIR
from app.schemas import ModelSettings, ResolvedTaskModelSettings
from app.generation.model_provider import LMStudioClient, LMStudioError, model_client_for_settings
from app.generation.router import resolve_task_model_settings
from app.memory.context import format_next_prompt_memory_pack
from app.generation.prompt_builder import (
    CHARACTER_PROMPT_FIELDS,
    WORLD_FIELDS,
    load_active_characters,
    load_world_notes,
)
from app.memory.foundation import render_story_foundation_for_prompt
from app.memory.engine import format_story_state_for_prompt


PIPELINE_SCHEMA_VERSION = "deliberate_generation_pipeline_v2"
PIPELINE_NAME = "structured_plan_write_review_repair"
PIPELINE_STAGE_ORDER = (
    "preparing_context",
    "scene_planning",
    "prose_generation",
    "scene_quality_review",
    "targeted_repair",
    "save_scene",
)
PIPELINE_TEMP_DIR = DATA_DIR / "temp" / "internal_generation"
PIPELINE_TEMP_RETENTION_SECONDS = 48 * 60 * 60
PIPELINE_TEMP_RETAIN_MAX = 20
PLANNER_TIMEOUT_CAP_SECONDS = 18.0
REVIEW_TIMEOUT_CAP_SECONDS = 18.0

PLAN_KEYS = (
    "scene_mode",
    "scene_purpose",
    "requested_scope",
    "scene_contract",
    "allowed_time_span",
    "must_not_advance_beyond",
    "required_director_facts",
    "characters_present",
    "viewpoint_strategy",
    "viewpoint_contract",
    "character_motives",
    "character_agency_matrix",
    "location_and_blocking",
    "blocking_map",
    "continuity_facts",
    "sensory_anchors",
    "world_grounding_anchors",
    "story_beats",
    "character_introduction_requirements",
    "forbidden_leaps_or_skips",
    "anti_fixation_checks",
    "ending_handoff",
    "continuity_risks",
)
PLAN_LIST_KEYS = {
    "required_director_facts",
    "characters_present",
    "continuity_facts",
    "sensory_anchors",
    "story_beats",
    "character_introduction_requirements",
    "forbidden_leaps_or_skips",
    "world_grounding_anchors",
    "anti_fixation_checks",
    "continuity_risks",
}
SCENE_CONTRACT_KEYS = (
    "start_time",
    "allowed_duration",
    "start_location",
    "end_boundary",
    "required_beats",
    "optional_beats",
    "must_not_occur_yet",
    "introduction_obligations",
    "backstory_to_dramatize",
    "time_skip_allowed",
    "scene_question",
    "emotional_progression",
    "ending_handoff",
    "cast_policy",
    "allowed_present_characters",
)
SCENE_CONTRACT_LIST_KEYS = {
    "required_beats",
    "optional_beats",
    "must_not_occur_yet",
    "introduction_obligations",
    "backstory_to_dramatize",
    "emotional_progression",
    "allowed_present_characters",
}
LOCATION_KEYS = (
    "location",
    "important_zones",
    "character_positions",
    "entrances_exits",
    "visible_objects",
)
BLOCKING_MAP_KEYS = (
    "location_zones",
    "doors_exits_windows",
    "furniture_terrain",
    "character_positions",
    "orientation_proximity",
    "carried_placed_objects",
    "sightlines_hearing",
    "entries_exits",
    "injuries_mobility_limits",
)
MOTIVE_KEYS = (
    "character",
    "surface_goal",
    "private_goal",
    "current_emotion",
    "relationship_pressures",
    "likely_behavior",
)
AGENCY_MATRIX_KEYS = (
    "character",
    "current_goal",
    "hidden_goal",
    "emotional_state",
    "relationship_pressure",
    "information_known",
    "information_hidden",
    "preferred_plan",
    "likely_objection",
    "action_they_may_initiate",
)
VIEWPOINT_CONTRACT_KEYS = (
    "strategy",
    "allowed_internal_access",
    "transition_rules",
    "forbidden_knowledge",
)
REVIEW_SEVERITIES = {"none", "minor", "major"}

PLANNING_CONTEXT_RECENT_REFERENCE_CHARS = 500
PLANNING_CONTEXT_TARGET_REFERENCE_CHARS = 700
PLANNING_WORLD_FIELD_CHARS = 320
PLANNING_FOUNDATION_CHARS = 2200
PLANNING_ACTIVE_CHARACTER_LIMIT = 6
PLANNING_CHARACTER_FIELD_CHARS = 260
PLANNING_SUMMARY_CHARS = 900
PLANNING_RECENT_SCENE_LIMIT = 3
PLANNING_RECENT_SCENE_CHARS = 460
PLANNING_RECENT_NOTE_CHARS = 180
PLANNING_TARGET_SCENE_CHARS = 800
PLANNING_TASK_NOTE_CHARS = 320
PLANNER_MAX_TOKENS = 760
REVIEW_MAX_TOKENS = 260
REVIEW_CONTEXT_CHARS = 2600
REVIEW_PLAN_CHARS = 3600
REVIEW_DRAFT_CHARS = 14000
REPAIR_CONTEXT_CHARS = 6000
REPAIR_PLAN_CHARS = 6500
REPAIR_DRAFT_CHARS = 45000


def compact_text(value: Any, limit: int = 700) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].strip() + "..."


def trim_middle_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    marker = "\n\n[Middle omitted for speed; summary and Story State preserve continuity.]\n\n"
    available = max(0, limit - len(marker))
    head_len = max(1000, available // 2)
    tail_len = max(1000, available - head_len)
    head = text[:head_len].rsplit("\n", 1)[0].strip() or text[:head_len].strip()
    tail = text[-tail_len:].lstrip()
    return f"{head}{marker}{tail}"


def compact_json(value: Any, *, limit: int | None = None) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return trim_middle_text(text, limit) if limit else text


def compact_list(value: Any, *, limit: int = 12, item_limit: int = 500) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    items: list[str] = []
    for item in value:
        if isinstance(item, dict):
            parts = [
                f"{compact_text(key, 80)}: {compact_text(child, 220)}"
                for key, child in item.items()
                if child not in (None, "", [], {}) and compact_text(child, 220)
            ]
            text = "; ".join(parts)
        else:
            text = compact_text(item, item_limit)
        if text:
            items.append(text[:item_limit])
        if len(items) >= limit:
            break
    return items


def compact_sentence_parts(text: str, limit: int = 8) -> list[str]:
    clean = " ".join((text or "").split())
    if not clean:
        return []
    parts = [
        compact_text(part.strip(" -"), 360)
        for part in re.split(r"(?<=[.!?;])\s+|\n+|,\s+(?=\w)", clean)
        if part.strip(" -")
    ]
    if len(parts) <= 1 and len(clean) > 240:
        parts = [compact_text(clean[index : index + 240], 260) for index in range(0, min(len(clean), 1440), 240)]
    return [part for part in parts if part][:limit]


def director_named_characters(director_note: str) -> list[str]:
    from app.memory.foundation import director_named_characters as extract_names
    return extract_names(director_note)


def director_closes_cast(director_note: str, named_characters: list[str]) -> bool:
    note = (director_note or "").lower()
    return bool(
        named_characters
        and (
            re.search(r"\b(?:scene|story|chapter)\s+between\b", note)
            or re.search(r"\b(?:only|just)\s+(?:the\s+)?(?:two|three)\s+(?:adults?|women|men|people|characters?|friends?|siblings?)\b", note)
            or re.search(r"\bno\s+(?:other|additional|new)\s+(?:people|persons|characters)\b", note)
        )
    )


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
                if not isinstance(parsed, dict):
                    raise ValueError("Top-level JSON was not an object.")
                return parsed
    raise ValueError("No complete JSON object found.")


def normalize_mode(mode: str) -> str:
    return mode if mode in {"continue", "regenerate", "rewrite", "revise"} else "continue"


def note_has_any(note_lower: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, note_lower) for pattern in patterns)


def explicit_time_skip_allowed(note_lower: str) -> bool:
    if re.search(r"\b(no|do not|don't|without)\s+(?:time[-\s]?skip|skip|jump ahead|fast[-\s]?forward)\b", note_lower):
        return False
    if re.search(
        r"\b(future|backstory|later)\b.{0,90}\b(must|should|stay|remain|keep|not\s+yet)\b|"
        r"\b(must|should|stay|remain|keep|not\s+yet)\b.{0,90}\b(future|backstory|later)\b",
        note_lower,
    ):
        return False
    return note_has_any(
        note_lower,
        (
            r"\btime[-\s]?skip\b",
            r"\bskip ahead\b",
            r"\bjump ahead\b",
            r"\bfast[-\s]?forward\b",
            r"\bmontage\b",
            r"\bdays?\s+later\b",
            r"\bweeks?\s+later\b",
            r"\bmonths?\s+later\b",
            r"\byears?\s+later\b",
        ),
    )


def start_location_from_context(
    director_note: str,
    recent_scenes: list[dict[str, Any]],
    world_notes: dict[str, Any] | None,
) -> str:
    note_lower = (director_note or "").lower()
    location_patterns = (
        (r"\bloading[-\s]?bay\b", "the loading bay"),
        (r"\bwarehouse\b", "the warehouse"),
        (r"\boffice\b", "the office"),
        (r"\bmaintenance bay\b", "the maintenance bay"),
        (r"\bapartment\b", "the apartment"),
        (r"\bkitchen\b", "the kitchen"),
        (r"\bfarmhouse\b", "the farmhouse kitchen"),
        (r"\bstove\b", "near the stove"),
        (r"\bporch\b", "the porch threshold"),
        (r"\bliving room\b", "the living room"),
        (r"\bhallway\b|\bcorridor\b", "the corridor or hallway"),
        (r"\bstarship\b|\bship\b|\bbridge\b", "the ship interior"),
        (r"\bengineering\b|\bengine room\b", "engineering"),
        (r"\bmedbay\b|\bmedical bay\b", "the medical bay"),
        (r"\bcastle\b|\bfortress\b|\bstronghold\b|\bthe keep\b|\bcastle keep\b", "the castle"),
        (r"\bstable\b", "the stable"),
        (r"\btavern\b|\binn\b", "the tavern"),
        (r"\bcamp\b", "the camp"),
        (r"\bbattlefield\b|\bfield\b", "the field"),
        (r"\bforest\b|\bwoods\b", "the woods"),
        (r"\broad\b|\bstreet\b", "the road or street"),
    )
    for pattern, location in location_patterns:
        if re.search(pattern, note_lower):
            return location

    for scene in reversed(recent_scenes[-3:]):
        recent_lower = f"{scene.get('director_note', '')}\n{scene.get('generated_text', '')[:900]}".lower()
        for pattern, location in location_patterns:
            if re.search(pattern, recent_lower):
                return location

    if world_notes:
        location_text = compact_text(world_notes.get("locations") or world_notes.get("setting"), 260)
        if location_text:
            return f"latest established place within: {location_text}"
    return "the latest established location, or the director-specified opening location"


def object_hints_from_text(text: str, limit: int = 8) -> list[str]:
    object_terms = (
        "badge",
        "bag",
        "blade",
        "bolt cutters",
        "book",
        "bottle",
        "card",
        "coin",
        "compass",
        "drive",
        "file",
        "folder",
        "gun",
        "key",
        "keycard",
        "knife",
        "lantern",
        "ledger",
        "letter",
        "map",
        "mask",
        "note",
        "pistol",
        "radio",
        "ring",
        "satchel",
        "sword",
        "tablet",
        "tool",
        "tool roll",
        "vial",
        "weapon",
    )
    found: list[str] = []
    lower = (text or "").lower()
    for term in object_terms:
        if re.search(rf"\b{re.escape(term)}s?\b", lower):
            found.append(term)
    if re.search(r"\b(give|gave|hand|handoff|pass|passed|transfer|take|took|carry|hold|holding|set down|put down)\b", lower):
        found.append("explicit ownership or placement change")
    deduped: list[str] = []
    for item in found:
        if item not in deduped:
            deduped.append(item)
    return deduped[:limit]


def location_zones_for_note(note_lower: str, start_location: str) -> list[str]:
    if "apartment" in note_lower:
        return ["entry door", "living area", "kitchen edge", "hallway or bedrooms if established"]
    if re.search(r"\bstarship\b|\bship\b|\bbridge\b|\bengineering\b", note_lower):
        return ["primary workstation", "bulkhead or hatch", "corridor access", "damaged or noisy system area"]
    if re.search(r"\bmedieval\b|\bcastle\b|\bstable\b|\btavern\b|\bcamp\b", note_lower):
        return ["main gathering space", "table or work surface", "door or gate", "nearby cover or waiting area"]
    if re.search(r"\btactical\b|\bmove|movement|across|zone\b", note_lower):
        return ["start zone", "cover or obstacle", "transition route", "destination boundary"]
    if start_location:
        return [f"near side of {start_location}", "central action area", "exit or threshold"]
    return []


def derive_scene_contract(
    *,
    director_note: str,
    writing_length: dict[str, Any],
    recent_scenes: list[dict[str, Any]],
    world_notes: dict[str, Any] | None,
    note_facts: list[str],
    character_names: list[str],
    mode: str,
) -> dict[str, Any]:
    note_lower = (director_note or "").lower()
    time_skip_allowed = explicit_time_skip_allowed(note_lower)
    is_intro = note_has_any(
        note_lower,
        (
            r"\bfirst\s+chapter\b",
            r"\bintroduc(?:e|tion|tions)\b",
            r"\bmeet(?:ing)?\b",
            r"\bopening\b",
        ),
    )
    is_preparation = note_has_any(
        note_lower,
        (
            r"\bprepar(?:e|ation|ing)\b",
            r"\bplan(?:s|ning)?\b",
            r"\bbrief(?:ing)?\b",
            r"\bdiscuss(?:ion|ing)?\b",
            r"\btalk(?:ing)?\b",
            r"\bconversation\b",
        ),
    )
    action_terms_present = note_has_any(
        note_lower,
        (
            r"\bbattle\b",
            r"\bfight\b",
            r"\brescue\b",
            r"\bassault\b",
            r"\battack\b",
            r"\bambush\b",
            r"\bmission\b",
            r"\bescape\b",
        ),
    )
    long_requested = str(writing_length.get("mode") or "").lower() == "chapter" or int(writing_length.get("max_words") or 0) >= 1800
    closed_cast = director_closes_cast(director_note, character_names)
    start_location = start_location_from_context(director_note, recent_scenes, world_notes)
    required_beats = note_facts[:6] or ["Open from current continuity", "Let present characters act", "End on a concrete handoff"]
    if is_intro:
        required_beats = [
            "Begin before the larger premise pays off.",
            "Introduce each major present character through action, voice, position, motive, and relationship pressure.",
            "Establish the immediate setting and practical situation in dramatized scene time.",
            *required_beats[:3],
        ]
    if is_preparation:
        required_beats = [
            "Dramatize the preparation, conversation, or tactical reasoning instead of summarizing it.",
            "Let each important present character press a goal, objection, or practical concern.",
            "End before execution begins unless the director explicitly asks to start it.",
            *required_beats[:3],
        ]

    must_not = [
        "Do not jump to aftermath, a later chapter, or a new major story beat.",
        "Do not compress hours or days into summary unless the director explicitly allowed a time skip.",
        "Do not repeat the director note as paraphrased exposition.",
    ]
    if is_intro:
        must_not.append("Do not skip introductions to reach the action premise.")
    if is_preparation or (action_terms_present and re.search(r"\b(before|planning|prepare|talk|discuss)\b", note_lower)):
        must_not.append("Do not reach the battle, rescue, attack, mission execution, or aftermath yet.")
    if long_requested and not time_skip_allowed:
        must_not.append("Long requested length deepens the same meaningful beats; it is not permission to fast-forward.")
    if closed_cast:
        must_not.append(f"Do not introduce additional present characters; the scene cast is limited to {', '.join(character_names[:8])}.")

    backstory = [
        fact
        for fact in note_facts
        if re.search(r"\b(backstory|history|years? ago|once|used to|before|past|future|later|eventually|will|would)\b", fact, re.I)
    ][:5]
    if re.search(r"\bbackstory\b|\bfuture\b|\blater\b|\beventually\b|\bwill\b", note_lower) and not backstory:
        backstory.append("Treat premise/backstory/future events as context to seed motive, not automatic first-scene events.")

    scene_question = note_facts[0] if note_facts else "What changes in this immediate scene?"
    if is_intro and character_names:
        scene_question = f"How do {', '.join(character_names[:3])} enter this story as people with wants, history, and pressure?"
    elif is_preparation:
        scene_question = "What plan, commitment, or unresolved objection emerges before action begins?"

    end_boundary = "Stop at the first meaningful handoff that preserves current continuity."
    if is_intro:
        end_boundary = "End after introductions and immediate tension are established, before skipping to the premise payoff."
    elif is_preparation:
        end_boundary = "End with the plan, disagreement, or commitment still inside the preparation scene; do not start the later action."
    elif not time_skip_allowed:
        end_boundary = "End within the same continuous scene-time envelope, not after an offscreen jump."

    return {
        "start_time": "Immediately after the latest saved passage." if recent_scenes and mode == "continue" else "At the moment requested by the director note.",
        "allowed_duration": "A continuous scene-length span; minutes or one uninterrupted chapter beat unless explicitly scoped otherwise.",
        "start_location": start_location,
        "end_boundary": end_boundary,
        "required_beats": required_beats[:8],
        "optional_beats": [
            "Add complications, reactions, sensory grounding, and micro-decisions that deepen the requested scope.",
            "Use backstory as pressure on current choices rather than dumping lore.",
        ],
        "must_not_occur_yet": must_not[:8],
        "introduction_obligations": [
            "Give each major present character a concrete position, voice, visible detail, current want, and relationship pressure."
        ]
        if is_intro or character_names
        else [],
        "backstory_to_dramatize": backstory,
        "time_skip_allowed": time_skip_allowed,
        "scene_question": scene_question,
        "emotional_progression": [
            "Open from the immediate current pressure.",
            "Let conflict, desire, fear, or practical limits force at least one choice or reveal.",
            "End with changed knowledge, commitment, relationship pressure, physical situation, risk, or emotional state.",
        ],
        "ending_handoff": end_boundary,
        "cast_policy": "closed" if closed_cast else "established-or-directly-implied",
        "allowed_present_characters": character_names[:8],
    }


def derive_blocking_map(
    *,
    director_note: str,
    start_location: str,
    character_names: list[str],
) -> dict[str, Any]:
    note_lower = (director_note or "").lower()
    zones = location_zones_for_note(note_lower, start_location)
    object_hints = object_hints_from_text(director_note)
    positions = [
        f"{name}: place relative to the start location and update only through explicit movement or exit."
        for name in character_names[:8]
    ]
    if not positions:
        positions = ["Identify who is physically present and keep them present until an explicit exit."]
    return {
        "location_zones": zones,
        "doors_exits_windows": ["Identify usable doors, exits, windows, hatches, gates, or routes only when they affect action or privacy."],
        "furniture_terrain": ["Use tables, counters, consoles, beds, cover, steps, mud, narrow passages, or terrain only when they shape movement or mood."],
        "character_positions": positions,
        "orientation_proximity": [
            "Track who faces whom, who is within reach, who hangs back, and who is separated by furniture, distance, or thresholds."
        ],
        "carried_placed_objects": object_hints
        or ["Track held, transferred, set-down, hidden, or visible objects when the scene makes them matter."],
        "sightlines_hearing": ["State who can see or hear whom when secrecy, privacy, distance, or obstruction matters."],
        "entries_exits": ["No one appears, vanishes, enters, or leaves without a dramatized cue."],
        "injuries_mobility_limits": ["Preserve wounds, exhaustion, restraints, clothing, load, fear, intoxication, or terrain that limits movement."],
    }


def derive_character_agency_matrix(
    *,
    director_note: str,
    character_names: list[str],
    active_characters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    note_lower = (director_note or "").lower()
    cards_by_name = {compact_text(character.get("name"), 120).lower(): character for character in active_characters}
    rows: list[dict[str, Any]] = []
    if not character_names:
        character_names = ["Primary present character"]
    for index, name in enumerate(character_names[:8]):
        card = cards_by_name.get(name.lower(), {})
        role = compact_text(card.get("role"), 220)
        personality = compact_text(card.get("personality"), 220)
        relationships = compact_text(card.get("relationships"), 260)
        current_state = compact_text(card.get("current_state"), 260)
        if "argument" in note_lower or "disagree" in note_lower or "different goal" in note_lower:
            preferred_plan = "Push a distinct, character-specific way to handle the conflict."
            objection_options = (
                "Object from urgency, protection, or fear that waiting will make things worse.",
                "Object from secrecy, shame, loyalty, or the cost of exposing the truth.",
                "Object from self-preservation, exhaustion, distrust, or the need to leave.",
            )
            likely_objection = objection_options[index % len(objection_options)]
        elif re.search(r"\bplan|planning|prepare|brief|discuss|talk\b", note_lower):
            preferred_plan = "Propose or refine a practical next step inside the planning scene."
            objection_options = (
                "Raise a grounded timing, route, or resource problem.",
                "Raise a loyalty, trust, or secrecy concern.",
                "Raise a cost, casualty, moral, or fallback-plan objection.",
            )
            likely_objection = objection_options[index % len(objection_options)]
        else:
            preferred_plan = "Take a plausible initiative that fits the immediate scene problem."
            objection_options = (
                "Resist choices that violate their knowledge or role.",
                "Resist choices that threaten a relationship or secret.",
                "Resist choices that ignore fear, desire, injury, or practical limits.",
            )
            likely_objection = objection_options[index % len(objection_options)]
        rows.append(
            {
                "character": name,
                "current_goal": current_state or role or "Pursue an immediate concrete goal shaped by the director note and Story State.",
                "hidden_goal": "Protect a private fear, loyalty, desire, secret, or self-image if established; otherwise keep hidden motives subtle.",
                "emotional_state": current_state or "Infer from recent events and current pressure.",
                "relationship_pressure": relationships or "Let relationships alter decisions, trust, objections, and who receives help.",
                "information_known": "Use only information this character knows from Story State, recent scenes, direct observation, or explicit scene framing.",
                "information_hidden": "Do not reveal secrets or offscreen facts to this character without dramatized evidence.",
                "preferred_plan": preferred_plan,
                "likely_objection": likely_objection,
                "action_they_may_initiate": (
                    "Ask the hard question, move to a meaningful position, handle an object, object to the plan, reveal a limited fact, or make a constrained offer."
                    if index % 2 == 0
                    else "Press for a different priority, protect someone, test a claim, refuse a shortcut, or create a practical complication."
                ),
            }
        )
    return rows


def derive_viewpoint_contract(director_note: str, character_count: int) -> dict[str, str]:
    note_lower = (director_note or "").lower()
    if re.search(r"\bfirst person\b|\b1st person\b", note_lower):
        strategy = "first person only because the director requested it"
        allowed = "Only the first-person narrator's direct perceptions, memories, and interpretations."
        transitions = "No viewpoint rotation unless the director explicitly asks."
    elif re.search(r"\bomnisci(?:ent|ence)\b", note_lower):
        strategy = "controlled omniscient"
        allowed = "Use clear omniscient framing for facts outside one character's knowledge; keep revelations intentional."
        transitions = "Signal broad-camera or narrator-level moves cleanly."
    elif character_count >= 3 or re.search(r"\bensemble\b|\bseveral\b|\bmultiple\b", note_lower):
        strategy = "rotating close third with explicit transitions"
        allowed = "A paragraph or beat may enter one character's interiority at a time."
        transitions = "Change internal access only at paragraph or beat boundaries with a clear physical or emotional cue."
    else:
        strategy = "close third"
        allowed = "Stay near the most scene-relevant viewpoint while using external cues for other characters."
        transitions = "Avoid rotation unless the scene clearly needs it."
    return {
        "strategy": strategy,
        "allowed_internal_access": allowed,
        "transition_rules": transitions,
        "forbidden_knowledge": "Do not head-hop inside a sentence or paragraph; do not reveal information unavailable to the viewpoint without explicit omniscient framing.",
    }


def derive_world_grounding_anchors(director_note: str) -> list[str]:
    note_lower = (director_note or "").lower()
    anchors = [
        "Physical environment detail that changes movement, privacy, comfort, or available choices.",
        "Sound, smell, light, weather, or body sensation that affects mood or attention.",
        "Clothing, injury, fatigue, carried weight, or body state that changes behavior.",
    ]
    if re.search(r"\bmodern|present[-\s]?day|apartment|phone|car|office\b", note_lower):
        anchors.append("Modern tool or social detail such as phone, keys, locks, neighbors, traffic, leases, work pressure, or building noise.")
    elif re.search(r"\bmedieval|fantasy|castle|sword|village|stable|tavern\b", note_lower):
        anchors.append("Practical period detail such as firelight, tack, mud, steel, cloth, coin, rank, rumor, or travel constraints.")
    elif re.search(r"\bsci[-\s]?fi|starship|ship|orbit|colony|android|AI|engineering\b", note_lower):
        anchors.append("Specific technical constraint that changes human dialogue or action, not generic technobabble.")
    elif re.search(r"\btactical|fight|battle|rescue|ambush|escape\b", note_lower):
        anchors.append("Practical tactical constraint: cover, angles, distance, visibility, noise, stamina, load, or egress.")
    anchors.append("One social or relationship detail that changes what someone can safely say or do.")
    return anchors[:6]


def deterministic_scene_plan(
    *,
    session_id: str,
    mode: str,
    director_note: str,
    writing_length: dict[str, Any],
    recent_scenes: list[dict[str, Any]],
    session_summary: str | None,
    target_scene: dict[str, Any] | None,
    world_notes: dict[str, Any] | None,
    active_characters: list[dict[str, Any]],
) -> dict[str, Any]:
    del session_id
    note_facts = compact_sentence_parts(director_note, limit=8)
    director_names = director_named_characters(director_note)
    if director_closes_cast(director_note, director_names):
        character_names = director_names
    else:
        character_names = [
            compact_text(character.get("name"), 120)
            for character in active_characters
            if compact_text(character.get("name"), 120)
        ]
        for name in director_names:
            if name not in character_names:
                character_names.append(name)
    latest_scene = compact_text((recent_scenes[-1] or {}).get("generated_text") if recent_scenes else "", 360)
    target_excerpt = compact_text((target_scene or {}).get("generated_text"), 360)
    continuity_facts = []
    if session_summary:
        continuity_facts.append(f"Summary: {compact_text(session_summary, 420)}")
    if latest_scene:
        continuity_facts.append(f"Latest passage: {latest_scene}")
    if target_excerpt:
        continuity_facts.append(f"Target passage: {target_excerpt}")
    if world_notes:
        for field, label in WORLD_FIELDS:
            value = compact_text(world_notes.get(field), 260)
            if value:
                continuity_facts.append(f"{label}: {value}")
    if not continuity_facts:
        continuity_facts.append("Preserve supplied Story State, world notes, active character cards, and recent passage facts.")

    note_lower = (director_note or "").lower()
    length_label = writing_length.get("label") or writing_length.get("mode") or "Scene"
    scope = f"{length_label}: target {writing_length.get('min_words')}-{writing_length.get('max_words')} words."
    scene_contract = derive_scene_contract(
        director_note=director_note,
        writing_length=writing_length,
        recent_scenes=recent_scenes,
        world_notes=world_notes,
        note_facts=note_facts,
        character_names=character_names,
        mode=mode,
    )
    blocking_map = derive_blocking_map(
        director_note=director_note,
        start_location=str(scene_contract.get("start_location") or ""),
        character_names=character_names,
    )
    agency_matrix = derive_character_agency_matrix(
        director_note=director_note,
        character_names=character_names,
        active_characters=active_characters,
    )
    viewpoint_contract = derive_viewpoint_contract(director_note, len(character_names))
    introduction_requirements: list[str] = []
    if "first chapter" in note_lower or "introduce" in note_lower:
        introduction_requirements.append("Introduce each requested major character through action, voice, visual grounding, and motive.")
    if re.search(r"\bmodern|present[-\s]?day|contemporary\b", note_lower):
        introduction_requirements.append("Keep diction modern and natural.")
    if re.search(r"\bmedieval|fantasy|kingdom|sword|village|bandit\b", note_lower):
        introduction_requirements.append("Keep diction grounded for fantasy/medieval context without fake archaic filler.")
    if re.search(r"\bsci[-\s]?fi|science fiction|starship|colony|android|orbit\b", note_lower):
        introduction_requirements.append("Keep technology precise and story-specific.")

    plan = {
        "scene_mode": normalize_mode(mode),
        "scene_purpose": note_facts[0] if note_facts else f"{normalize_mode(mode)} the current StoryDriver scene.",
        "requested_scope": scope,
        "scene_contract": scene_contract,
        "allowed_time_span": scene_contract["allowed_duration"],
        "must_not_advance_beyond": scene_contract["end_boundary"],
        "required_director_facts": note_facts,
        "characters_present": character_names[:10] or ["Use established characters who are present or directly implied."],
        "viewpoint_strategy": viewpoint_contract["strategy"],
        "viewpoint_contract": viewpoint_contract,
        "character_motives": [
            {
                "character": name,
                "surface_goal": "Act within the director's requested scene problem.",
                "private_goal": "Preserve personal agency, history, fears, desires, and relationship pressures.",
                "current_emotion": "Infer from recent scenes and current stakes.",
                "relationship_pressures": [],
                "likely_behavior": "Choose concrete, character-specific action rather than exposition-only compliance.",
            }
            for name in (character_names[:6] or ["Primary characters"])
        ],
        "character_agency_matrix": agency_matrix,
        "location_and_blocking": {
            "location": scene_contract["start_location"],
            "important_zones": blocking_map["location_zones"],
            "character_positions": blocking_map["character_positions"],
            "entrances_exits": [*blocking_map["doors_exits_windows"][:3], *blocking_map["entries_exits"][:3]],
            "visible_objects": blocking_map["carried_placed_objects"],
        },
        "blocking_map": blocking_map,
        "continuity_facts": continuity_facts[:10],
        "sensory_anchors": ["light", "sound", "body language", "tactile detail", "relevant objects"],
        "world_grounding_anchors": derive_world_grounding_anchors(director_note),
        "story_beats": note_facts[:6] or ["Open from current continuity", "Let characters act", "End on a concrete handoff"],
        "character_introduction_requirements": introduction_requirements,
        "forbidden_leaps_or_skips": [
            "No assistant-style framing.",
            "No hidden style steering beyond user system prompt, editable task notes, Story State, and this structured plan.",
            "No contradiction of explicit world rules.",
            *scene_contract["must_not_occur_yet"][:5],
        ],
        "anti_fixation_checks": [
            "Do not repeat the same argument, emotional note, or director-note phrase in paraphrase.",
            "Every several paragraphs should change knowledge, commitment, relationship pressure, physical situation, plan, risk, or emotional state.",
            "Avoid artificial cliffhanger endings that dodge the requested scene handoff.",
            "Limit atmosphere to details that affect action, mood, character choice, or continuity.",
        ],
        "ending_handoff": "End after a meaningful beat that supports continue/regenerate/rewrite/revise without asking the director a question.",
        "continuity_risks": [
            "Time skip or aftermath summary could violate requested scope.",
            "Object ownership, injuries, outfits, positions, secrets, or relationships may be easy to drop.",
        ],
    }
    return validate_scene_plan(plan, mode=mode, fallback=None)


def compact_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "y", "1", "allowed"}:
            return True
        if normalized in {"false", "no", "n", "0", "not allowed", "disallowed"}:
            return False
    return default


def nested_source(source: dict[str, Any], base: dict[str, Any], key: str) -> tuple[dict[str, Any], dict[str, Any]]:
    source_value = source.get(key) if isinstance(source.get(key), dict) else {}
    base_value = base.get(key) if isinstance(base.get(key), dict) else {}
    return source_value, base_value


def validate_scene_contract(source: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    contract_source, contract_base = nested_source(source, base, "scene_contract")
    if not contract_source and not contract_base:
        contract_base = {
            "start_time": "At the director-requested opening moment.",
            "allowed_duration": source.get("allowed_time_span") or base.get("allowed_time_span") or "One continuous scene span.",
            "start_location": "Latest established location or director-specified setting.",
            "end_boundary": source.get("must_not_advance_beyond") or base.get("must_not_advance_beyond") or "Do not jump beyond the requested scene.",
            "required_beats": source.get("story_beats") or base.get("story_beats") or [],
            "optional_beats": [],
            "must_not_occur_yet": source.get("forbidden_leaps_or_skips") or base.get("forbidden_leaps_or_skips") or [],
            "introduction_obligations": source.get("character_introduction_requirements")
            or base.get("character_introduction_requirements")
            or [],
            "backstory_to_dramatize": [],
            "time_skip_allowed": False,
            "scene_question": source.get("scene_purpose") or base.get("scene_purpose") or "",
            "emotional_progression": [],
            "ending_handoff": source.get("ending_handoff") or base.get("ending_handoff") or "",
        }
    contract: dict[str, Any] = {}
    for key in SCENE_CONTRACT_KEYS:
        value = contract_source.get(key) if key in contract_source else contract_base.get(key)
        if key in SCENE_CONTRACT_LIST_KEYS:
            contract[key] = compact_list(value, limit=7, item_limit=320)
        elif key == "time_skip_allowed":
            contract[key] = compact_bool(value, default=compact_bool(contract_base.get(key), False))
        else:
            contract[key] = compact_text(value, 520)
    return contract


def validate_blocking_map(source: dict[str, Any], base: dict[str, Any], scene_contract: dict[str, Any]) -> dict[str, list[str]]:
    blocking_source, blocking_base = nested_source(source, base, "blocking_map")
    if not blocking_source:
        legacy_location = source.get("location_and_blocking") if isinstance(source.get("location_and_blocking"), dict) else {}
        if legacy_location:
            blocking_source = {
                "location_zones": legacy_location.get("important_zones"),
                "doors_exits_windows": legacy_location.get("entrances_exits"),
                "character_positions": legacy_location.get("character_positions"),
                "carried_placed_objects": legacy_location.get("visible_objects"),
            }
    if not blocking_base:
        legacy_base = base.get("location_and_blocking") if isinstance(base.get("location_and_blocking"), dict) else {}
        if legacy_base:
            blocking_base = {
                "location_zones": legacy_base.get("important_zones"),
                "doors_exits_windows": legacy_base.get("entrances_exits"),
                "character_positions": legacy_base.get("character_positions"),
                "carried_placed_objects": legacy_base.get("visible_objects"),
            }
    result = {
        key: compact_list(blocking_source.get(key) if key in blocking_source else blocking_base.get(key), limit=7, item_limit=320)
        for key in BLOCKING_MAP_KEYS
    }
    if not result["location_zones"] and scene_contract.get("start_location"):
        result["location_zones"] = [str(scene_contract["start_location"])]
    return result


def validate_viewpoint_contract(source: dict[str, Any], base: dict[str, Any]) -> dict[str, str]:
    viewpoint_source, viewpoint_base = nested_source(source, base, "viewpoint_contract")
    if not viewpoint_source and isinstance(source.get("viewpoint_strategy"), str):
        viewpoint_source = {"strategy": source.get("viewpoint_strategy")}
    if not viewpoint_base and isinstance(base.get("viewpoint_strategy"), str):
        viewpoint_base = {"strategy": base.get("viewpoint_strategy")}
    return {
        key: compact_text(viewpoint_source.get(key) if key in viewpoint_source else viewpoint_base.get(key), 520)
        for key in VIEWPOINT_CONTRACT_KEYS
    }


def validate_agency_matrix(source: dict[str, Any], base: dict[str, Any]) -> list[dict[str, str]]:
    base_rows = base.get("character_agency_matrix", []) if isinstance(base.get("character_agency_matrix"), list) else []
    rows = source.get("character_agency_matrix") if isinstance(source.get("character_agency_matrix"), list) else base_rows
    if not rows and isinstance(source.get("character_motives"), list):
        rows = [
            {
                "character": row.get("character"),
                "current_goal": row.get("surface_goal"),
                "hidden_goal": row.get("private_goal"),
                "emotional_state": row.get("current_emotion"),
                "relationship_pressure": row.get("relationship_pressures"),
                "preferred_plan": row.get("likely_behavior"),
            }
            for row in source.get("character_motives", [])
            if isinstance(row, dict)
        ]
    base_by_character = {
        compact_text(row.get("character"), 120).casefold(): row
        for row in base_rows
        if isinstance(row, dict) and compact_text(row.get("character"), 120)
    }
    matrix: list[dict[str, str]] = []
    for index, row in enumerate(rows[:6] if isinstance(rows, list) else []):
        if not isinstance(row, dict):
            continue
        character = compact_text(row.get("character"), 120)
        fallback_row = base_by_character.get(character.casefold()) if character else None
        if fallback_row is None and index < len(base_rows) and isinstance(base_rows[index], dict):
            fallback_row = base_rows[index]
        fallback_row = fallback_row or {}
        matrix.append(
            {
                key: compact_text(
                    row.get(key) if row.get(key) not in (None, "", [], {}) else fallback_row.get(key),
                    280,
                )
                for key in AGENCY_MATRIX_KEYS
            }
        )
    return matrix


def validate_scene_plan(
    value: dict[str, Any],
    *,
    mode: str,
    fallback: dict[str, Any] | None,
) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    base = fallback or {}
    plan: dict[str, Any] = {}
    plan["scene_mode"] = normalize_mode(str(source.get("scene_mode") or base.get("scene_mode") or mode))
    plan["scene_purpose"] = compact_text(source.get("scene_purpose") or base.get("scene_purpose"), 480)
    plan["requested_scope"] = compact_text(source.get("requested_scope") or base.get("requested_scope"), 480)
    plan["scene_contract"] = validate_scene_contract(source, base)
    base_contract = base.get("scene_contract") if isinstance(base.get("scene_contract"), dict) else {}
    if base_contract.get("cast_policy") == "closed":
        plan["scene_contract"]["cast_policy"] = "closed"
        plan["scene_contract"]["allowed_present_characters"] = compact_list(
            base_contract.get("allowed_present_characters"), limit=8, item_limit=120
        )
        plan["scene_contract"]["must_not_occur_yet"] = compact_list(
            [
                *(base_contract.get("must_not_occur_yet") or []),
                *(plan["scene_contract"].get("must_not_occur_yet") or []),
            ],
            limit=8,
            item_limit=320,
        )
    if base_contract and not compact_bool(base_contract.get("time_skip_allowed"), False):
        plan["scene_contract"]["time_skip_allowed"] = False
    plan["allowed_time_span"] = compact_text(
        source.get("allowed_time_span") or base.get("allowed_time_span") or plan["scene_contract"].get("allowed_duration"),
        480,
    )
    plan["must_not_advance_beyond"] = compact_text(
        source.get("must_not_advance_beyond")
        or base.get("must_not_advance_beyond")
        or plan["scene_contract"].get("end_boundary"),
        480,
    )
    plan["viewpoint_contract"] = validate_viewpoint_contract(source, base)
    plan["viewpoint_strategy"] = compact_text(
        source.get("viewpoint_strategy")
        or base.get("viewpoint_strategy")
        or plan["viewpoint_contract"].get("strategy"),
        480,
    )
    plan["ending_handoff"] = compact_text(
        source.get("ending_handoff") or base.get("ending_handoff") or plan["scene_contract"].get("ending_handoff"),
        480,
    )
    for key in PLAN_LIST_KEYS:
        plan[key] = compact_list(source.get(key) if key in source else base.get(key), limit=7, item_limit=320)

    motive_rows = source.get("character_motives") if isinstance(source.get("character_motives"), list) else base.get("character_motives", [])
    motives: list[dict[str, Any]] = []
    for row in motive_rows[:6] if isinstance(motive_rows, list) else []:
        if not isinstance(row, dict):
            continue
        motives.append(
            {
                key: compact_list(row.get(key), limit=4, item_limit=220)
                if key == "relationship_pressures"
                else compact_text(row.get(key), 260)
                for key in MOTIVE_KEYS
            }
        )
    plan["character_motives"] = motives
    plan["character_agency_matrix"] = validate_agency_matrix(source, base)
    plan["blocking_map"] = validate_blocking_map(source, base, plan["scene_contract"])

    location_source = source.get("location_and_blocking") if isinstance(source.get("location_and_blocking"), dict) else {}
    location_base = base.get("location_and_blocking") if isinstance(base.get("location_and_blocking"), dict) else {}
    plan["location_and_blocking"] = {
        "location": compact_text(
            location_source.get("location")
            or location_base.get("location")
            or plan["scene_contract"].get("start_location"),
            360,
        ),
        "important_zones": compact_list(
            location_source.get("important_zones")
            if "important_zones" in location_source
            else location_base.get("important_zones") or plan["blocking_map"].get("location_zones"),
            limit=8,
            item_limit=300,
        ),
        "character_positions": compact_list(
            location_source.get("character_positions")
            if "character_positions" in location_source
            else location_base.get("character_positions") or plan["blocking_map"].get("character_positions"),
            limit=8,
            item_limit=300,
        ),
        "entrances_exits": compact_list(
            location_source.get("entrances_exits")
            if "entrances_exits" in location_source
            else location_base.get("entrances_exits")
            or [*plan["blocking_map"].get("doors_exits_windows", []), *plan["blocking_map"].get("entries_exits", [])],
            limit=8,
            item_limit=300,
        ),
        "visible_objects": compact_list(
            location_source.get("visible_objects")
            if "visible_objects" in location_source
            else location_base.get("visible_objects") or plan["blocking_map"].get("carried_placed_objects"),
            limit=8,
            item_limit=300,
        ),
    }

    if not plan["forbidden_leaps_or_skips"] and plan["scene_contract"].get("must_not_occur_yet"):
        plan["forbidden_leaps_or_skips"] = compact_list(plan["scene_contract"]["must_not_occur_yet"], limit=7, item_limit=320)
    if not plan["story_beats"] and plan["scene_contract"].get("required_beats"):
        plan["story_beats"] = compact_list(plan["scene_contract"]["required_beats"], limit=7, item_limit=320)
    if not plan["character_introduction_requirements"] and plan["scene_contract"].get("introduction_obligations"):
        plan["character_introduction_requirements"] = compact_list(
            plan["scene_contract"]["introduction_obligations"],
            limit=7,
            item_limit=320,
        )

    for key in PLAN_KEYS:
        if key not in plan:
            plan[key] = [] if key in PLAN_LIST_KEYS else ""
    return plan


def validate_quality_review(value: dict[str, Any]) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    severity = str(source.get("severity") or "none").strip().lower()
    if severity not in REVIEW_SEVERITIES:
        severity = "minor"
    issues = compact_list(source.get("issues"), limit=12, item_limit=500)
    repair_instructions = compact_list(source.get("repair_instructions"), limit=10, item_limit=600)
    passed = bool(source.get("pass"))
    if severity == "major":
        passed = False
        if issues and not repair_instructions:
            repair_instructions = [f"Correct issue: {issue}" for issue in issues[:5]]
    elif severity == "none" and not issues:
        passed = True
    return {
        "pass": passed,
        "severity": severity,
        "issues": issues,
        "repair_instructions": repair_instructions,
    }


QUALITY_REVIEW_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "storydriver_quality_review",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "pass": {"type": "boolean"},
                "severity": {"type": "string", "enum": ["none", "minor", "major"]},
                "issues": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                "repair_instructions": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
            },
            "required": ["pass", "severity", "issues", "repair_instructions"],
            "additionalProperties": False,
        },
    },
}


def deterministic_quality_review(
    *,
    scene_plan: dict[str, Any],
    draft_text: str,
    writing_length: dict[str, Any],
) -> dict[str, Any]:
    text = str(draft_text or "").strip()
    lowered = text.lower()
    issues: list[str] = []
    repairs: list[str] = []

    def major(issue: str, repair: str) -> None:
        if issue not in issues:
            issues.append(issue)
            repairs.append(repair)

    if not text:
        major("The draft is empty.", "Write the requested scene against the validated plan.")
    if re.search(r"(?:^|\n)\s*(?:as an ai|i (?:cannot|can't) (?:write|help)|here(?:'s| is) (?:the|your) (?:scene|story))\b", lowered):
        major("The draft contains assistant framing or a refusal.", "Return fiction prose only, without assistant framing or refusal text.")

    min_words = max(0, int(writing_length.get("min_words") or 0))
    max_words = max(0, int(writing_length.get("max_words") or 0))
    word_count = len(re.findall(r"\b[\w'-]+\b", text))
    if min_words and word_count < max(80, int(min_words * 0.5)):
        major(
            f"The draft is severely under length at {word_count} words for a {min_words}-word minimum.",
            "Deepen required beats, agency, blocking, interiority, and consequences without advancing beyond the contract.",
        )
    if max_words and word_count > max(max_words + 150, int(max_words * 1.18)):
        major(
            f"The draft materially exceeds the requested maximum at {word_count} words for a {max_words}-word maximum.",
            f"Tighten repetition, atmosphere, and redundant transitions so the complete passage lands between {min_words}-{max_words} words without dropping required beats.",
        )

    contract = scene_plan.get("scene_contract") if isinstance(scene_plan.get("scene_contract"), dict) else {}
    if not compact_bool(contract.get("time_skip_allowed"), False) and re.search(
        r"\b(?:several\s+)?(?:hours|days|weeks|months|years)\s+later\b|\bthe next (?:morning|day|week|month|year)\b",
        lowered,
    ):
        major("The draft contains an unauthorized time skip.", "Keep the scene inside the contract's continuous time span.")

    boundaries = " ".join(
        [
            str(contract.get("end_boundary") or ""),
            *(str(item) for item in contract.get("must_not_occur_yet") or []),
            *(str(item) for item in scene_plan.get("forbidden_leaps_or_skips") or []),
        ]
    ).lower()
    if re.search(r"(?:do not|must not|without) leave|same (?:room|location|apartment|workshop|basement)", boundaries) and re.search(
        r"\bleft (?:the|their|his|her) (?:apartment|room|building|workshop|basement|hall|ship)\b|"
        r"\bstepped (?:outside|into the hall|through the exit)\b|\bwent outside\b|\bcrossed the threshold\b|\bdeparted\b",
        lowered,
    ):
        major("The draft leaves the location despite a hard scene boundary.", "Keep every character inside the allowed location.")
    if re.search(r"(?:do not|must not).{0,40}(?:battle|fight|attack|combat)", boundaries) and re.search(
        r"\b(?:the battle began|combat erupted|the attack began|they charged|blades clashed)\b",
        lowered,
    ):
        major("The draft reaches forbidden battle or action early.", "End during preparation or planning before battle begins.")
    if re.search(r"(?:do not|must not).{0,50}(?:reveal|identify|solve|resolve)", boundaries) and re.search(
        r"\b(?:the culprit was|the saboteur was|the mystery was solved|the secret was that|everything was resolved)\b",
        lowered,
    ):
        major("The draft reveals or resolves a fact reserved for later.", "Keep the future reveal unresolved inside this scene.")
    if ("no magic" in boundaries or "without magic" in boundaries) and re.search(
        r"\b(?:cast a spell|summoned magic|magical flame|arcane power)\b", lowered
    ):
        major("The draft violates the no-magic world rule.", "Remove magic and solve the scene through grounded action.")

    if contract.get("introduction_obligations"):
        names = [
            str(name).strip()
            for name in scene_plan.get("characters_present") or []
            if re.fullmatch(r"[A-Z][A-Za-z'-]*(?:\s+[A-Z][A-Za-z'-]*)?", str(name).strip())
        ]
        missing_names = [name for name in names if not re.search(rf"\b{re.escape(name.split()[0])}\b", text)]
        if missing_names:
            major(
                f"Required present characters are missing from the introduction: {', '.join(missing_names)}.",
                "Dramatize each required present character through action, voice, grounding, and motive.",
            )

    if contract.get("cast_policy") == "closed":
        allowed_first_names = {
            str(name).strip().split()[0].casefold()
            for name in contract.get("allowed_present_characters") or scene_plan.get("characters_present") or []
            if str(name).strip()
        }
        non_name_subjects = {
            "a",
            "an",
            "as",
            "he",
            "her",
            "his",
            "it",
            "she",
            "that",
            "the",
            "their",
            "they",
            "this",
            "we",
            "when",
            "while",
            "you",
        }
        candidate_counts: dict[str, int] = {}
        full_name_introductions: set[str] = set()
        for match in re.finditer(
            r"\b([A-Z][a-z]{2,})(?:\s+([A-Z][a-z]{2,}))?\s+"
            r"(?:said|asked|replied|whispered|sat|stood|watched|moved|reached|closed|opened|turned|walked|waited|looked|leaned|nodded|remained)\b",
            text,
        ):
            candidate = match.group(1)
            if candidate.casefold() in allowed_first_names or candidate.casefold() in non_name_subjects:
                continue
            candidate_counts[candidate] = candidate_counts.get(candidate, 0) + 1
            if match.group(2):
                full_name_introductions.add(candidate)
        unexpected = sorted(
            name for name, count in candidate_counts.items() if count >= 2 or name in full_name_introductions
        )
        if unexpected:
            major(
                f"The draft introduces characters outside the closed scene cast: {', '.join(unexpected)}.",
                "Remove unrequested present characters and keep the scene limited to the allowed cast.",
            )

    paragraphs = [" ".join(part.split()).casefold() for part in re.split(r"\n\s*\n", text) if len(part.split()) >= 12]
    if {paragraph for paragraph in paragraphs if paragraphs.count(paragraph) > 1}:
        major("The draft repeats an entire paragraph without scene movement.", "Replace repetition with a new choice, reveal, action, or relationship change.")

    if re.search(r"\b(?:little did (?:they|she|he) know|everything was about to change|what happened next|to be continued)\b[.!?\s]*$", lowered[-320:]):
        major("The draft ends on an artificial cliffhanger.", "End on the contract's concrete handoff instead of generic suspense.")

    viewpoint = scene_plan.get("viewpoint_contract") if isinstance(scene_plan.get("viewpoint_contract"), dict) else {}
    if str(viewpoint.get("strategy") or "").lower().startswith("close third") and re.search(
        r"\b(?:unbeknownst to|could not know that|had no way to know that)\b", lowered
    ):
        major("The close-third draft reveals knowledge outside the viewpoint without clear framing.", "Keep interior access with the viewpoint character or explicitly change the viewpoint strategy.")

    holder_mentions: dict[str, set[str]] = {}
    for match in re.finditer(
        r"\b([A-Z][a-z]+)\s+(?:held|gripped|carried|kept)\s+(?:the\s+)?"
        r"(key|ledger|notebook|map|wafer|compass|knife|phone|letter|ring|book|coat|scarf)\b",
        text,
    ):
        holder_mentions.setdefault(match.group(2).lower(), set()).add(match.group(1))
    if not re.search(r"\b(?:handed|gave|passed|returned|transferred)\b", lowered):
        duplicate_objects = [name for name, holders in holder_mentions.items() if len(holders) > 1]
        if duplicate_objects:
            major(
                f"The draft gives multiple simultaneous holders to: {', '.join(duplicate_objects)}.",
                "Keep one holder at a time or dramatize the transfer explicitly.",
            )

    if issues:
        return {"pass": False, "severity": "major", "issues": issues[:6], "repair_instructions": repairs[:4]}
    if min_words and word_count < int(min_words * 0.78):
        return {
            "pass": True,
            "severity": "minor",
            "issues": [f"The draft is moderately under the requested minimum at {word_count} words."],
            "repair_instructions": [],
        }
    return {"pass": True, "severity": "none", "issues": [], "repair_instructions": []}


def merge_quality_reviews(deterministic: dict[str, Any], model_review: dict[str, Any]) -> dict[str, Any]:
    if deterministic.get("severity") == "major":
        return deterministic
    if model_review.get("severity") == "major":
        return model_review
    if deterministic.get("severity") == "minor" and model_review.get("severity") == "none":
        return deterministic
    return model_review


def build_generation_context_text(
    *,
    session_id: str,
    mode: str,
    director_note: str,
    writing_length: dict[str, Any],
    recent_scenes: list[dict[str, Any]],
    session_summary: str | None,
    target_scene: dict[str, Any] | None,
    world_notes: dict[str, Any] | None,
    active_characters: list[dict[str, Any]],
) -> str:
    relevance_text = "\n".join(
        [
            director_note or "",
            "\n".join(scene.get("generated_text", "")[:PLANNING_CONTEXT_RECENT_REFERENCE_CHARS] for scene in recent_scenes[-3:]),
            (target_scene or {}).get("generated_text", "")[:PLANNING_CONTEXT_TARGET_REFERENCE_CHARS],
        ]
    )
    memory_pack_context, _memory_pack_item_count, _memory_pack = format_next_prompt_memory_pack(
        session_id,
        relevance_text=relevance_text,
        context_kind="planner",
        limit=3600,
        mark_used=False,
        cache=True,
    )
    story_state_context = ""
    if not memory_pack_context:
        story_state_context, _count = format_story_state_for_prompt(
            session_id,
            relevance_text=relevance_text,
            mark_used=False,
        )
    lines = [
        f"Scene mode: {normalize_mode(mode)}",
        f"Requested length: {writing_length.get('label') or writing_length.get('mode')} "
        f"({writing_length.get('min_words')}-{writing_length.get('max_words')} words)",
        "",
        "Director note:",
        director_note.strip(),
        "",
    ]
    if not memory_pack_context and world_notes:
        world_lines = [
            f"{label}: {compact_text(world_notes.get(field), PLANNING_WORLD_FIELD_CHARS)}"
            for field, label in WORLD_FIELDS
            if compact_text(world_notes.get(field), PLANNING_WORLD_FIELD_CHARS)
        ]
        if world_lines:
            lines.extend(["World/story foundation:", *world_lines, ""])
    foundation_context = "" if memory_pack_context else render_story_foundation_for_prompt(session_id, limit=PLANNING_FOUNDATION_CHARS)
    if foundation_context:
        lines.extend([foundation_context, ""])
    if not memory_pack_context and active_characters:
        lines.append("Active character cards:")
        for character in active_characters[:PLANNING_ACTIVE_CHARACTER_LIMIT]:
            name = compact_text(character.get("name"), 160)
            if not name:
                continue
            block = [f"Name: {name}"]
            for field, label in CHARACTER_PROMPT_FIELDS:
                value = compact_text(character.get(field), PLANNING_CHARACTER_FIELD_CHARS)
                if value:
                    block.append(f"{label}: {value}")
            lines.extend(block + [""])
    if memory_pack_context:
        lines.extend([memory_pack_context, ""])
    elif story_state_context:
        lines.extend(["Factual Story State continuity:", story_state_context, ""])
    if session_summary and session_summary.strip():
        lines.extend(["Recent summary:", compact_text(session_summary, PLANNING_SUMMARY_CHARS), ""])
    if recent_scenes:
        lines.append("Recent scenes:")
        for index, scene in enumerate(recent_scenes[-PLANNING_RECENT_SCENE_LIMIT:], start=1):
            text = compact_text(scene.get("generated_text"), PLANNING_RECENT_SCENE_CHARS)
            note = compact_text(scene.get("director_note"), PLANNING_RECENT_NOTE_CHARS)
            lines.extend([f"[Recent {index}] Director note: {note}", text, ""])
    else:
        lines.extend(["Recent scenes:", "[No prior scenes]", ""])
    if target_scene:
        lines.extend(["Target scene/version for versioned mode:", compact_text(target_scene.get("generated_text"), PLANNING_TARGET_SCENE_CHARS), ""])
    return "\n".join(lines).strip()


def build_scene_planning_prompt(
    *,
    context_text: str,
    mode: str,
    task_notes: str,
) -> str:
    model_plan_shape = {
        "scene_purpose": "",
        "scene_contract": {
            "start_time": "",
            "allowed_duration": "",
            "start_location": "",
            "end_boundary": "",
            "required_beats": [],
            "must_not_occur_yet": [],
            "introduction_obligations": [],
            "backstory_to_dramatize": [],
            "time_skip_allowed": False,
            "scene_question": "",
            "emotional_progression": [],
            "ending_handoff": "",
        },
        "characters_present": [],
        "viewpoint_contract": {
            "strategy": "close third",
            "transition_rules": "",
        },
        "character_agency_matrix": [
            {
                "character": "",
                "current_goal": "",
                "hidden_goal": "",
                "emotional_state": "",
                "relationship_pressure": "",
                "preferred_plan": "",
                "likely_objection": "",
                "action_they_may_initiate": "",
            }
        ],
        "blocking_map": {
            "location_zones": [],
            "character_positions": [],
            "orientation_proximity": [],
            "carried_placed_objects": [],
            "sightlines_hearing": [],
            "entries_exits": [],
            "injuries_mobility_limits": [],
        },
        "story_beats": [],
    }
    compact_task_notes = compact_text(task_notes, PLANNING_TASK_NOTE_CHARS)
    return "\n".join(
        [
            "Create a concise structured StoryDriver scene plan.",
            compact_task_notes,
            "",
            "Rules:",
            "- Return strict JSON only. No markdown, labels, prose draft, analysis, or chain-of-thought.",
            "- Keep every string concrete and short, preferably under twelve words. Use at most three items per list.",
            "- Return only the compact refinement shape below. Deterministic code supplies and validates all omitted full-plan fields.",
            "- Explicit director scope is hard. Do not jump to later action, battle, rescue, aftermath, or location when setup, introduction, planning, or conversation is requested.",
            "- Treat premise/backstory/future events as context unless the note explicitly asks to dramatize them now.",
            "- Do not compress hours or days unless scene_contract.time_skip_allowed is true; long requests should deepen beats instead of advancing too far.",
            "- Preserve Story Foundation, character cards, Story State, relationships, objects, injuries, outfits, secrets, and unresolved threads.",
            "- scene_contract must set start_time, allowed_duration, start_location, end_boundary, required_beats, must_not_occur_yet, scene_question, emotional_progression, and ending_handoff.",
            "- blocking_map must use human-readable relative positions, sight/hearing, entries/exits, mobility limits, and carried/placed object ownership.",
            "- character_agency_matrix must give each present main character goals, pressure, known/hidden information, likely objection, and an action they may initiate.",
            "- viewpoint_contract must choose one clear strategy and prevent head-hopping.",
            "- world_grounding_anchors and anti_fixation_checks should be limited, relevant, and actionable.",
            "",
            "Required JSON shape:",
            compact_json(model_plan_shape),
            "",
            "Context:",
            context_text,
            "",
            "End of context. Return the required scene-plan JSON object now. Do not continue or imitate any story prose above.",
        ]
    )


def build_json_repair_prompt(raw_text: str, schema_hint: str) -> str:
    return "\n".join(
        [
            "Repair the following malformed structured output into valid strict JSON only.",
            "Do not add prose, markdown, explanation, or chain-of-thought.",
            schema_hint,
            "",
            "Malformed output:",
            raw_text[:12000],
            "",
            "End malformed output. Return the repaired JSON object only.",
        ]
    )


def build_quality_review_prompt(
    *,
    context_text: str,
    scene_plan: dict[str, Any],
    draft_text: str,
    task_notes: str,
    writing_length: dict[str, Any],
) -> str:
    review_context = trim_middle_text(context_text, REVIEW_CONTEXT_CHARS)
    review_plan = compact_json(scene_plan, limit=REVIEW_PLAN_CHARS)
    compact_task_notes = compact_text(task_notes, PLANNING_TASK_NOTE_CHARS)
    return "\n".join(
        [
            "Review this completed StoryDriver draft for quality and continuity.",
            compact_task_notes,
            "",
            "Return compact JSON only:",
            compact_json({"pass": True, "severity": "none", "issues": [], "repair_instructions": []}),
            "",
            "Severity rules:",
            "- none: no meaningful issue.",
            "- minor: useful QA hint, but the scene can be saved.",
            "- major: meaningful failure that warrants exactly one targeted repair.",
            "- Use minor, not major, for uncertain detector-style concerns, slight length misses, or stylistic preferences without concrete evidence.",
            "- If the only concern is that the draft could be richer, clearer, or longer but it respects hard scope, return minor or none.",
            "",
            "Check hard failures: director-note scope, scene_contract boundaries, unauthorized time skips, missing required beats, premature future/backstory/action, "
            "lost people/objects, duplicated object ownership, blocking/entry/exit contradictions, character-bible or Story State contradictions, viewpoint head-hopping, "
            "repetitive fixation, stagnant paragraphs, artificial cliffhanger, missing meaningful scene movement, assistant framing, and requested length.",
            "",
            "A draft should usually change at least one of: knowledge, commitment, relationship pressure, physical situation, plan, risk, or emotional state. "
            "Mark major if the scene violates hard scope, skips required setup/introduction/conversation, loses a present person/object, duplicates object ownership, "
            "or repeats the same idea instead of moving the scene.",
            "",
            f"Requested length: {writing_length.get('min_words')}-{writing_length.get('max_words')} words.",
            "",
            "Context:",
            review_context,
            "",
            "Validated scene plan:",
            review_plan,
            "",
            "Draft prose to review:",
            draft_text[:REVIEW_DRAFT_CHARS],
            "",
            "End of draft. Return only the compact review JSON object now. Do not continue, rewrite, or imitate the prose.",
        ]
    )


def build_targeted_repair_prompt(
    *,
    context_text: str,
    scene_plan: dict[str, Any],
    draft_text: str,
    quality_review: dict[str, Any],
    writing_length: dict[str, Any],
) -> str:
    repair_context = trim_middle_text(context_text, REPAIR_CONTEXT_CHARS)
    repair_plan = compact_json(scene_plan, limit=REPAIR_PLAN_CHARS)
    repair_review = compact_json(quality_review, limit=4000)
    current_word_count = len(re.findall(r"\b[\w'-]+\b", str(draft_text or "")))
    return "\n".join(
        [
            "Repair this StoryDriver draft once, targeting only the identified major issues.",
            "Output the complete repaired fiction passage only. No JSON, no analysis, no headings, no assistant framing.",
            "Preserve good prose, existing continuity, scene purpose, and all unaffected material where possible.",
            "Respect the scene_contract, blocking_map, character_agency_matrix, viewpoint_contract, and object ownership in the plan.",
            "Do not restart the story, do not add a new scene, and do not jump beyond the requested scope.",
            f"Current draft length: {current_word_count} words. Final requested length: {writing_length.get('min_words')}-{writing_length.get('max_words')} words.",
            "When length is an identified issue, the complete repaired passage must fit that final range; preserve required beats by removing repetition and compression-resistant filler first.",
            "",
            "Context:",
            repair_context,
            "",
            "Scene plan:",
            repair_plan,
            "",
            "Major review issues and repair instructions:",
            repair_review,
            "",
            "Draft prose:",
            draft_text[:REPAIR_DRAFT_CHARS],
        ]
    )


def structured_task_parameters(model_settings: ModelSettings, *, max_tokens: int, temperature: float) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "temperature": temperature,
        "top_p": min(float(model_settings.top_p or 0.9), 0.9),
        "max_tokens": max_tokens,
        "seed": model_settings.seed,
        "top_k": model_settings.top_k,
        "min_p": model_settings.min_p,
        "repeat_penalty": model_settings.repeat_penalty,
        "presence_penalty": 0.0,
        "frequency_penalty": 0.0,
    }
    if str(model_settings.reasoning_mode or "").strip().lower() == "off":
        parameters["reasoning_effort"] = "none"
    return parameters


def inherited_pipeline_settings(
    task_type: str,
    inherited_settings: ModelSettings,
    inherited_resolved: ResolvedTaskModelSettings,
) -> tuple[ModelSettings, ResolvedTaskModelSettings, bool]:
    task_settings, task_resolved = resolve_task_model_settings(task_type)
    inherited_fields = (
        "lm_studio_url",
        "model",
        "inference_backend",
        "reasoning_mode",
        "context_length",
        "fallback_to_openai_compatible",
    )
    updates: dict[str, Any] = {}
    resolved_updates: dict[str, Any] = {}
    inherited_any = False
    for field in inherited_fields:
        if field in task_resolved.override_fields:
            continue
        value = getattr(inherited_settings, field)
        updates[field] = value
        resolved_updates[field] = getattr(inherited_resolved, field)
        inherited_any = True
    if updates:
        task_settings = task_settings.model_copy(update=updates)
        task_resolved = task_resolved.model_copy(update=resolved_updates)
    return task_settings, task_resolved, inherited_any


async def resolve_pipeline_model(
    client: LMStudioClient,
    selected_model: str,
    inherited_model: str,
) -> str:
    selected = (selected_model or "").strip()
    if selected:
        return selected
    if inherited_model.strip():
        return inherited_model.strip()
    models = await client.list_models()
    if not models:
        raise LMStudioError("No models are loaded in LM Studio.")
    return models[0]["id"]


def pipeline_metadata_base(
    *,
    task_type: str,
    task_label: str,
    model: str,
    duration_seconds: float,
    inherited_route: bool,
    raw_response: str,
    json_repair_used: bool,
    deterministic_fallback_used: bool,
) -> dict[str, Any]:
    return {
        "task_profile": task_type,
        "task_label": task_label,
        "model": model,
        "duration_seconds": round(duration_seconds, 3),
        "inherited_prose_route": inherited_route,
        "raw_response_chars": len(raw_response or ""),
        "json_repair_used": json_repair_used,
        "deterministic_fallback_used": deterministic_fallback_used,
    }


async def generate_validated_scene_plan(
    *,
    client: Any,
    model: str,
    system_prompt: str,
    user_prompt: str,
    parameters: dict[str, Any],
    timeout: float,
    inference_backend: str,
    reasoning_mode: str,
    context_length: int | None,
    fallback_to_openai_compatible: bool,
    fallback_plan: dict[str, Any],
    mode: str,
) -> tuple[dict[str, Any], str, bool, bool]:
    raw_text = ""
    try:
        result = await client.generate_scene_routed(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            parameters=parameters,
            timeout=timeout,
            inference_backend=inference_backend,
            reasoning_mode=reasoning_mode,
            context_length=context_length,
            fallback_to_openai_compatible=fallback_to_openai_compatible,
        )
        raw_text = str(result.get("text") or "")
        parsed = extract_json_object(raw_text)
        return validate_scene_plan(parsed, mode=mode, fallback=fallback_plan), raw_text, False, False
    except Exception:
        return fallback_plan, raw_text, False, True


async def generate_validated_quality_review(
    *,
    client: Any,
    model: str,
    system_prompt: str,
    user_prompt: str,
    parameters: dict[str, Any],
    timeout: float,
    inference_backend: str,
    reasoning_mode: str,
    context_length: int | None,
    fallback_to_openai_compatible: bool,
) -> tuple[dict[str, Any], str, bool, bool]:
    raw_text = ""
    json_repair_used = False
    try:
        result = await client.generate_scene_routed(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            parameters=parameters,
            timeout=timeout,
            inference_backend=inference_backend,
            reasoning_mode=reasoning_mode,
            context_length=context_length,
            fallback_to_openai_compatible=fallback_to_openai_compatible,
        )
        raw_text = str(result.get("text") or "")
        return validate_quality_review(extract_json_object(raw_text)), raw_text, json_repair_used, False
    except Exception:
        json_repair_used = True
        if not raw_text.strip():
            return {
                "pass": False,
                "severity": "major",
                "issues": ["Quality review was unavailable, so hard Scene Contract compliance could not be verified."],
                "repair_instructions": ["Rewrite once against the validated Scene Contract, preserving required beats, people, blocking, object ownership, and end boundary."],
            }, raw_text, False, True

    try:
        repair_prompt = build_json_repair_prompt(
            raw_text,
            'The JSON must use exactly these top-level keys: "pass", "severity", "issues", "repair_instructions".',
        )
        result = await client.generate_scene_routed(
            model=model,
            system_prompt="Return valid strict JSON only. Do not include prose or analysis.",
            user_prompt=repair_prompt,
            parameters={**parameters, "temperature": 0.0, "max_tokens": min(int(parameters.get("max_tokens") or REVIEW_MAX_TOKENS), REVIEW_MAX_TOKENS)},
            timeout=timeout,
            inference_backend=inference_backend,
            reasoning_mode=reasoning_mode,
            context_length=context_length,
            fallback_to_openai_compatible=fallback_to_openai_compatible,
        )
        raw_text = str(result.get("text") or raw_text)
        return validate_quality_review(extract_json_object(raw_text)), raw_text, json_repair_used, False
    except Exception:
        return {
            "pass": False,
            "severity": "major",
            "issues": ["Quality review returned malformed JSON, so hard Scene Contract compliance could not be verified."],
            "repair_instructions": ["Rewrite once against the validated Scene Contract, preserving required beats, people, blocking, object ownership, and end boundary."],
        }, raw_text, json_repair_used, True


async def run_scene_planning_pass(
    *,
    session_id: str,
    mode: str,
    director_note: str,
    writing_length: dict[str, Any],
    recent_scenes: list[dict[str, Any]],
    session_summary: str | None,
    target_scene: dict[str, Any] | None,
    inherited_settings: ModelSettings,
    inherited_resolved: ResolvedTaskModelSettings,
    inherited_model: str,
) -> dict[str, Any]:
    started = perf_counter()
    world_notes = load_world_notes(session_id)
    active_characters = load_active_characters(session_id)
    fallback_plan = deterministic_scene_plan(
        session_id=session_id,
        mode=mode,
        director_note=director_note,
        writing_length=writing_length,
        recent_scenes=recent_scenes,
        session_summary=session_summary,
        target_scene=target_scene,
        world_notes=world_notes,
        active_characters=active_characters,
    )
    context_text = build_generation_context_text(
        session_id=session_id,
        mode=mode,
        director_note=director_note,
        writing_length=writing_length,
        recent_scenes=recent_scenes,
        session_summary=session_summary,
        target_scene=target_scene,
        world_notes=world_notes,
        active_characters=active_characters,
    )
    task_settings, task_resolved, inherited_route = inherited_pipeline_settings(
        "scene_planning",
        inherited_settings,
        inherited_resolved,
    )
    client = model_client_for_settings(task_settings)
    model = await resolve_pipeline_model(client, task_settings.model, inherited_model)
    user_prompt = build_scene_planning_prompt(
        context_text=context_text,
        mode=mode,
        task_notes=task_resolved.notes,
    )
    parameters = structured_task_parameters(task_settings, max_tokens=PLANNER_MAX_TOKENS, temperature=0.25)
    effective_timeout = min(float(task_resolved.timeout_seconds or PLANNER_TIMEOUT_CAP_SECONDS), PLANNER_TIMEOUT_CAP_SECONDS)
    plan, raw_text, json_repair_used, deterministic_fallback_used = await generate_validated_scene_plan(
        client=client,
        model=model,
        system_prompt="Plan one StoryDriver scene in compact strict JSON under 760 tokens. Never include prose or hidden reasoning.",
        user_prompt=user_prompt,
        parameters=parameters,
        timeout=effective_timeout,
        inference_backend=task_resolved.inference_backend,
        reasoning_mode=task_resolved.reasoning_mode,
        context_length=task_resolved.context_length,
        fallback_to_openai_compatible=task_resolved.fallback_to_openai_compatible,
        fallback_plan=fallback_plan,
        mode=mode,
    )
    duration = perf_counter() - started
    metadata = pipeline_metadata_base(
        task_type="scene_planning",
        task_label=task_resolved.label,
        model=model,
        duration_seconds=duration,
        inherited_route=inherited_route,
        raw_response=raw_text,
        json_repair_used=json_repair_used,
        deterministic_fallback_used=deterministic_fallback_used,
    )
    metadata.update(
        {
            "required_director_fact_count": len(plan.get("required_director_facts") or []),
            "scene_contract_required_beat_count": len((plan.get("scene_contract") or {}).get("required_beats") or []),
            "story_beat_count": len(plan.get("story_beats") or []),
            "continuity_risk_count": len(plan.get("continuity_risks") or []),
            "character_count": len(plan.get("characters_present") or []),
            "agency_row_count": len(plan.get("character_agency_matrix") or []),
            "world_grounding_anchor_count": len(plan.get("world_grounding_anchors") or []),
            "prompt_chars": len(user_prompt),
            "configured_timeout_seconds": task_resolved.timeout_seconds,
            "effective_timeout_seconds": effective_timeout,
        }
    )
    return {
        "plan": plan,
        "metadata": metadata,
        "context_text": context_text,
        "world_notes": world_notes,
        "active_characters": active_characters,
    }


async def run_quality_review_pass(
    *,
    context_text: str,
    scene_plan: dict[str, Any],
    draft_text: str,
    writing_length: dict[str, Any],
    inherited_settings: ModelSettings,
    inherited_resolved: ResolvedTaskModelSettings,
    inherited_model: str,
) -> dict[str, Any]:
    started = perf_counter()
    deterministic_review = deterministic_quality_review(
        scene_plan=scene_plan,
        draft_text=draft_text,
        writing_length=writing_length,
    )
    if deterministic_review.get("severity") == "major":
        duration = perf_counter() - started
        metadata = {
            "task_profile": "scene_quality_review",
            "task_label": "Scene Quality Review",
            "model": "deterministic_preflight",
            "duration_seconds": round(duration, 3),
            "inherited_prose_route": False,
            "raw_response_chars": 0,
            "json_repair_used": False,
            "deterministic_fallback_used": False,
            "deterministic_preflight": True,
            "model_review_ran": False,
            "pass": False,
            "severity": "major",
            "issue_count": len(deterministic_review.get("issues") or []),
            "repair_instruction_count": len(deterministic_review.get("repair_instructions") or []),
            "issues": (deterministic_review.get("issues") or [])[:8],
            "repair_instructions": (deterministic_review.get("repair_instructions") or [])[:6],
            "prompt_chars": 0,
        }
        return {"review": deterministic_review, "metadata": metadata}
    task_settings, task_resolved, inherited_route = inherited_pipeline_settings(
        "scene_quality_review",
        inherited_settings,
        inherited_resolved,
    )
    client = model_client_for_settings(task_settings)
    model = await resolve_pipeline_model(client, task_settings.model, inherited_model)
    user_prompt = build_quality_review_prompt(
        context_text=context_text,
        scene_plan=scene_plan,
        draft_text=draft_text,
        task_notes=task_resolved.notes,
        writing_length=writing_length,
    )
    parameters = structured_task_parameters(task_settings, max_tokens=REVIEW_MAX_TOKENS, temperature=0.1)
    parameters["response_format"] = QUALITY_REVIEW_RESPONSE_FORMAT
    model_review, raw_text, json_repair_used, deterministic_fallback_used = await generate_validated_quality_review(
        client=client,
        model=model,
        system_prompt="You review StoryDriver drafts. Return compact JSON only and never include hidden reasoning.",
        user_prompt=user_prompt,
        parameters=parameters,
        timeout=min(float(task_resolved.timeout_seconds or REVIEW_TIMEOUT_CAP_SECONDS), REVIEW_TIMEOUT_CAP_SECONDS),
        inference_backend=task_resolved.inference_backend,
        reasoning_mode=task_resolved.reasoning_mode,
        context_length=task_resolved.context_length,
        fallback_to_openai_compatible=task_resolved.fallback_to_openai_compatible,
    )
    review = merge_quality_reviews(deterministic_review, model_review)
    duration = perf_counter() - started
    metadata = pipeline_metadata_base(
        task_type="scene_quality_review",
        task_label=task_resolved.label,
        model=model,
        duration_seconds=duration,
        inherited_route=inherited_route,
        raw_response=raw_text,
        json_repair_used=json_repair_used,
        deterministic_fallback_used=deterministic_fallback_used,
    )
    metadata.update(
        {
            "pass": review["pass"],
            "severity": review["severity"],
            "issue_count": len(review["issues"]),
            "repair_instruction_count": len(review["repair_instructions"]),
            "issues": review["issues"][:8],
            "repair_instructions": review["repair_instructions"][:6],
            "prompt_chars": len(user_prompt),
            "deterministic_preflight": False,
            "deterministic_issue_count": len(deterministic_review.get("issues") or []),
            "model_review_ran": True,
            "effective_timeout_seconds": min(
                float(task_resolved.timeout_seconds or REVIEW_TIMEOUT_CAP_SECONDS),
                REVIEW_TIMEOUT_CAP_SECONDS,
            ),
        }
    )
    return {"review": review, "metadata": metadata}


async def run_targeted_repair_pass(
    *,
    context_text: str,
    scene_plan: dict[str, Any],
    draft_text: str,
    quality_review: dict[str, Any],
    writing_length: dict[str, Any],
    inherited_settings: ModelSettings,
    inherited_resolved: ResolvedTaskModelSettings,
    inherited_model: str,
    prose_parameters: dict[str, Any],
) -> dict[str, Any]:
    started = perf_counter()
    client = model_client_for_settings(inherited_settings)
    user_prompt = build_targeted_repair_prompt(
        context_text=context_text,
        scene_plan=scene_plan,
        draft_text=draft_text,
        quality_review=quality_review,
        writing_length=writing_length,
    )
    repair_parameters = dict(prose_parameters)
    repair_parameters["temperature"] = min(float(repair_parameters.get("temperature") or 0.6), 0.55)
    repair_parameters["max_tokens"] = max(int(repair_parameters.get("max_tokens") or 0), 1600)
    result = await client.generate_scene_routed(
        model=inherited_model,
        system_prompt=inherited_settings.system_prompt,
        user_prompt=user_prompt,
        parameters=repair_parameters,
        timeout=inherited_resolved.timeout_seconds,
        inference_backend=inherited_resolved.inference_backend,
        reasoning_mode=inherited_resolved.reasoning_mode,
        context_length=inherited_resolved.context_length,
        fallback_to_openai_compatible=inherited_resolved.fallback_to_openai_compatible,
    )
    repaired_text = str(result.get("text") or "").strip()
    if not repaired_text:
        raise LMStudioError("Targeted repair returned an empty scene.")
    duration = perf_counter() - started
    metadata = {
        "task_profile": inherited_resolved.task_type,
        "task_label": inherited_resolved.label,
        "model": inherited_model,
        "duration_seconds": round(duration, 3),
        "raw_response_chars": len(repaired_text),
        "reasoning_chars": result.get("reasoning_chars"),
        "finish_reason": result.get("finish_reason"),
        "fallback_used": bool(result.get("fallback_used")),
        "prompt_chars": len(user_prompt),
        "instruction_count": len(quality_review.get("repair_instructions") or []),
    }
    return {"text": repaired_text, "metadata": metadata}


def should_run_targeted_repair(review: dict[str, Any]) -> bool:
    if review.get("severity") != "major":
        return False
    items = [str(item) for item in [*(review.get("issues") or []), *(review.get("repair_instructions") or [])] if str(item).strip()]
    if not items:
        return False
    text = " ".join(items).lower()
    hard_markers = (
        "scope",
        "time skip",
        "jump",
        "missing",
        "required",
        "must",
        "forbidden",
        "boundary",
        "contradict",
        "lost",
        "duplicated",
        "duplicate",
        "ownership",
        "object",
        "head-hop",
        "assistant",
        "empty",
        "refusal",
        "under length",
        "too short",
        "repetitive",
        "fixation",
        "stagnant",
    )
    soft_markers = ("may", "might", "could", "possible", "possibly", "somewhat", "slight", "would benefit")
    if not any(marker in text for marker in hard_markers) and any(marker in text for marker in soft_markers):
        return False
    return True


def compact_pipeline_stats(
    *,
    planning_metadata: dict[str, Any],
    review_metadata: dict[str, Any] | None,
    repair_metadata: dict[str, Any] | None,
    repair_ran: bool,
) -> dict[str, Any]:
    planning_seconds = float(planning_metadata.get("duration_seconds") or 0)
    review_seconds = float((review_metadata or {}).get("duration_seconds") or 0)
    repair_seconds = float((repair_metadata or {}).get("duration_seconds") or 0)
    return {
        "name": PIPELINE_NAME,
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "mandatory": True,
        "stages": list(PIPELINE_STAGE_ORDER),
        "raw_chain_of_thought_saved": False,
        "raw_plans_reviews_persisted": False,
        "plan": planning_metadata,
        "review": review_metadata or {},
        "repair": repair_metadata or {},
        "repair_ran": repair_ran,
        "latency_seconds": {
            "planning": round(planning_seconds, 3),
            "review": round(review_seconds, 3),
            "repair": round(repair_seconds, 3),
            "added_total": round(planning_seconds + review_seconds + repair_seconds, 3),
        },
        "retention": {
            "raw_plan_review_storage": "rolling debug only; generation stats store compact metadata",
            "temp_dir": str(PIPELINE_TEMP_DIR),
            "temp_retention_seconds": PIPELINE_TEMP_RETENTION_SECONDS,
            "temp_retain_max": PIPELINE_TEMP_RETAIN_MAX,
        },
    }


def cleanup_internal_generation_artifacts() -> dict[str, Any]:
    PIPELINE_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    files = [path for path in PIPELINE_TEMP_DIR.iterdir() if path.is_file()]
    now = time()
    deleted: list[str] = []
    for path in files:
        try:
            age = now - path.stat().st_mtime
        except OSError:
            continue
        if age > PIPELINE_TEMP_RETENTION_SECONDS:
            try:
                path.unlink()
                deleted.append(path.name)
            except OSError:
                pass
    remaining = sorted(
        [path for path in PIPELINE_TEMP_DIR.iterdir() if path.is_file()],
        key=lambda item: item.stat().st_mtime if item.exists() else 0,
        reverse=True,
    )
    for path in remaining[PIPELINE_TEMP_RETAIN_MAX:]:
        try:
            path.unlink()
            deleted.append(path.name)
        except OSError:
            pass
    return {
        "temp_dir": str(PIPELINE_TEMP_DIR),
        "deleted_count": len(deleted),
        "deleted": deleted[:20],
        "retention_seconds": PIPELINE_TEMP_RETENTION_SECONDS,
        "retain_max": PIPELINE_TEMP_RETAIN_MAX,
    }


def write_pipeline_report(lines: list[str], path: Path | None = None) -> None:
    report_path = path or (DATA_DIR / "logs" / "DELIBERATE_GENERATION_PIPELINE_REPORT.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
