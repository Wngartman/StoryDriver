import re
from typing import Any

from app.database import db_session
from app.memory.context import format_next_prompt_memory_pack
from app.memory.foundation import render_story_foundation_for_prompt
from app.memory.engine import format_story_state_for_prompt


WORLD_FIELDS = [
    ("setting", "Setting"),
    ("tone", "Tone"),
    ("rules", "Rules"),
    ("locations", "Locations"),
    ("factions", "Factions"),
    ("conflicts", "Conflicts"),
    ("history", "History"),
]

CHARACTER_PROMPT_FIELDS = [
    ("role", "Role"),
    ("personality", "Personality"),
    ("appearance", "Appearance"),
    ("relationships", "Relationships"),
    ("current_state", "Current state"),
]

WRITING_LENGTH_MODES: dict[str, dict[str, Any]] = {
    "beat": {
        "label": "Beat",
        "min_words": 300,
        "max_words": 700,
        "description": "short, focused story beat",
    },
    "scene": {
        "label": "Scene",
        "min_words": 800,
        "max_words": 1400,
        "description": "medium scene with developed pacing",
    },
    "chapter": {
        "label": "Chapter",
        "min_words": 1800,
        "max_words": 2600,
        "description": "long readable chapter with room for setup, character voice, and tension",
    },
}

WRITER_TASK_NOTE_CHARS = 720
WRITER_FOUNDATION_CHARS = 3000
WRITER_WORLD_FIELD_CHARS = 420
WRITER_CHARACTER_LIMIT = 6
WRITER_CHARACTER_FIELD_CHARS = 320
WRITER_SUMMARY_CHARS = 1300
WRITER_MEMORY_LIMIT = 5
WRITER_MEMORY_CHARS = 420
WRITER_RECENT_SCENE_LIMIT = 3
WRITER_RECENT_SCENE_CHARS = 800
WRITER_TARGET_SCENE_CHARS = 1800

CHAPTER_NOTE_PATTERNS = (
    r"\bfirst\s+chapter\b",
    r"\bfull\s+chapter\b",
    r"\bchapter\b",
    r"\blong\s+first\s+chapter\b",
    r"\blong-form\b",
    r"\b8\s*-\s*12\s*(?:minute|min)",
    r"\b[78]\s*[\-\u2013]\s*12\s*(?:minute|min)",
    r"\b7\s*[-–]\s*12\s*(?:minute|min)",
    r"\b7\s+to\s+12\s*(?:minute|min)",
    r"\b8\s+to\s+12\s*(?:minute|min)",
    r"\bseven\s*[-–]\s*twelve\s*(?:minute|min)",
    r"\bseven\s+to\s+twelve\s+minutes\b",
    r"\beight\s+to\s+twelve\s+minutes\b",
    r"\breading\s+time\b",
)

WORD_RANGE_PATTERNS = (
    re.compile(r"\b(?:between\s+)?(\d{2,5})\s*(?:-|\u2013|to|and)\s*(\d{2,5})\s+words?\b", re.I),
    re.compile(r"\b(\d{2,5})\s*(?:-|\u2013)word\b", re.I),
)
WORD_COUNT_PATTERN = re.compile(r"\b(?:exactly|about|around|roughly|approximately)?\s*(\d{2,5})\s+words?\b", re.I)
BRIEF_NOTE_PATTERN = re.compile(r"\b(?:brief|short|quick)\s+(?:moment|beat|passage|scene)\b", re.I)


def requested_chapter_length(director_note: str) -> bool:
    normalized = director_note.lower()
    return any(re.search(pattern, normalized) for pattern in CHAPTER_NOTE_PATTERNS)


def explicit_word_target(director_note: str) -> tuple[int, int] | None:
    for pattern in WORD_RANGE_PATTERNS:
        match = pattern.search(director_note)
        if not match:
            continue
        if match.lastindex == 1:
            target = int(match.group(1))
            return max(100, int(target * 0.9)), min(12000, max(100, int(target * 1.1)))
        low, high = sorted((int(match.group(1)), int(match.group(2))))
        return max(100, low), min(12000, max(100, high))
    match = WORD_COUNT_PATTERN.search(director_note)
    if not match:
        return None
    target = int(match.group(1))
    return max(100, int(target * 0.9)), min(12000, max(100, int(target * 1.1)))


def resolve_writing_length(
    *,
    director_note: str,
    configured_mode: str = "scene",
    custom_word_min: int | None = None,
    custom_word_max: int | None = None,
) -> dict[str, Any]:
    mode = (configured_mode or "scene").strip().lower()
    detected_reason = ""
    explicit_target = explicit_word_target(director_note)
    if explicit_target:
        min_words, max_words = explicit_target
        return {
            "mode": "custom",
            "label": "Custom",
            "min_words": min_words,
            "max_words": max_words,
            "description": "explicit director-requested passage length",
            "detected_reason": "Director note supplied an explicit word-count target.",
        }
    if requested_chapter_length(director_note):
        mode = "chapter"
        detected_reason = "Director note requested a chapter/long reading-length passage."
    elif BRIEF_NOTE_PATTERN.search(director_note):
        mode = "beat"
        detected_reason = "Director note requested a brief focused passage."
    if mode == "custom":
        min_words = int(custom_word_min or WRITING_LENGTH_MODES["scene"]["min_words"])
        max_words = int(custom_word_max or max(min_words, WRITING_LENGTH_MODES["scene"]["max_words"]))
        if max_words < min_words:
            max_words = min_words
        return {
            "mode": "custom",
            "label": "Custom",
            "min_words": min_words,
            "max_words": max_words,
            "description": "custom passage length",
            "detected_reason": detected_reason,
        }
    data = WRITING_LENGTH_MODES.get(mode, WRITING_LENGTH_MODES["scene"])
    return {
        "mode": mode if mode in WRITING_LENGTH_MODES else "scene",
        **data,
        "detected_reason": detected_reason,
    }


def estimated_reading_minutes(min_words: int, max_words: int) -> str:
    # A comfortable narration pace varies by voice and speed; 190 wpm is a useful local planning estimate.
    low = max(1, round(min_words / 190))
    high = max(low, round(max_words / 190))
    return f"{low}-{high} min"


def recommended_max_tokens_for_length(length: dict[str, Any]) -> int:
    max_words = int(length.get("max_words") or WRITING_LENGTH_MODES["scene"]["max_words"])
    mode = str(length.get("mode") or "").lower()
    if mode == "chapter":
        return max(5600, int(max_words * 1.9) + 700)
    return max(768, int(max_words * 1.65) + 420)


def director_requirement_lines(director_note: str, length: dict[str, Any]) -> list[str]:
    note = director_note.lower()
    lines = [
        f"Target format: {length['label']} ({length['description']}).",
        f"Target length: {length['min_words']}-{length['max_words']} words, roughly {estimated_reading_minutes(length['min_words'], length['max_words'])} at narration pace.",
        "Treat the director note as creative direction with concrete requirements, not wording to mechanically repeat.",
        "Use requested topics appropriately, then let the scene dynamics advance instead of looping on one idea.",
        "Do not rush through setup, decisions, character introductions, or tactical discussion in compressed recap.",
        "Treat the structured Scene Contract as binding: stay inside its start time, start location, allowed duration, end boundary, required beats, and must-not-yet list.",
        "Premise, backstory, and future events in the director note are not automatically first-scene events; dramatize them now only when explicitly requested.",
        "If the requested word count is long, deepen meaningful beats, relationship pressure, interiority, blocking, and world grounding instead of fast-forwarding.",
    ]
    if length.get("detected_reason"):
        lines.append(length["detected_reason"])
    if "no magic" in note or "without magic" in note:
        lines.append("Constraint: no magic exists in this world; do not introduce magic, spells, prophecy-as-magic, enchanted objects, or supernatural powers.")
    if "first chapter" in note:
        lines.append("First-chapter requirement: establish the characters, setting, history, immediate tension, and scene purpose through dramatized prose.")
    if length.get("mode") == "chapter":
        lines.append(
            "Chapter pacing requirement: write sustained scene prose with full beats, dialogue, sensory grounding, and transitions; do not end early after only a summary of the premise."
        )
    if "introduce" in note:
        lines.append("Introduction requirement: give each major requested character enough presence, voice, and grounding to be memorable.")
    if "talk" in note or "discussion" in note or "talking through" in note:
        lines.append("Discussion requirement: dramatize the conversation and tactical reasoning; do not skip directly to the decision or aftermath.")
    if any(term in note for term in ("prepare", "preparation", "planning", "plan before", "briefing")):
        lines.append("Preparation/planning requirement: stay before the later action unless the director explicitly asks the scene to begin that action.")
    return lines


def has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def compact_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].strip() + "..."


def load_world_notes(session_id: str) -> dict[str, Any] | None:
    with db_session() as db:
        row = db.execute(
            """
            SELECT setting, tone, rules, locations, factions, conflicts, history
            FROM world_notes
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    notes = {field: row[field] for field, _ in WORLD_FIELDS}
    return notes if any(has_text(value) for value in notes.values()) else None


def load_active_characters(session_id: str) -> list[dict[str, Any]]:
    with db_session() as db:
        rows = db.execute(
            """
            SELECT c.name, c.role, c.personality, c.appearance, c.relationships, c.current_state
            FROM session_characters sc
            JOIN characters c ON c.id = sc.character_id
            WHERE sc.session_id = ? AND sc.is_active = 1
            ORDER BY lower(c.name) ASC
            """,
            (session_id,),
        ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


GENRE_TIME_RULES: tuple[dict[str, Any], ...] = (
    {
        "id": "modern_present_day",
        "label": "Modern/present day",
        "patterns": (
            r"\bmodern\b",
            r"\bpresent[-\s]?day\b",
            r"\bcontemporary\b",
            r"\bapartment\b",
            r"\bphone\b",
            r"\btext(?:ing| message)?\b",
            r"\bcar\b",
            r"\boffice\b",
            r"\bsuburb\b",
        ),
        "guidance": "Use natural contemporary prose and dialogue. Avoid forced archaic phrasing or fantasy diction.",
    },
    {
        "id": "medieval_fantasy",
        "label": "Medieval/fantasy",
        "patterns": (
            r"\bfantasy\b",
            r"\bmedieval\b",
            r"\bkingdom\b",
            r"\bcastle\b",
            r"\bsword\b",
            r"\bvillage\b",
            r"\bbandit\b",
            r"\bknight\b",
            r"\btavern\b",
            r"\bno magic\b",
        ),
        "guidance": "Use grounded elevated prose and concrete period texture. Avoid fake old-timey words unless the user's style asks for them.",
    },
    {
        "id": "science_fiction",
        "label": "Science fiction",
        "patterns": (
            r"\bsci[-\s]?fi\b",
            r"\bscience fiction\b",
            r"\bspace\b",
            r"\bstarship\b",
            r"\borbit\b",
            r"\bcolony\b",
            r"\bandroid\b",
            r"\bcyber\b",
            r"\bAI\b",
        ),
        "guidance": "Keep technology precise but human. Avoid generic technobabble and do not import fantasy diction.",
    },
    {
        "id": "horror_thriller",
        "label": "Horror/thriller",
        "patterns": (
            r"\bhorror\b",
            r"\bthriller\b",
            r"\bstalker\b",
            r"\bmurder\b",
            r"\bserial\b",
            r"\bhaunted\b",
            r"\bdread\b",
            r"\bsuspense\b",
        ),
        "guidance": "Build tension through sensory detail, uncertainty, blocking, and consequence. Keep action clear.",
    },
    {
        "id": "romance_intimacy",
        "label": "Romance/intimacy",
        "patterns": (
            r"\bromance\b",
            r"\bintimacy\b",
            r"\bintimate\b",
            r"\bsexual\b",
            r"\bsex\b",
            r"\bdesire\b",
            r"\bchemistry\b",
            r"\blovers?\b",
        ),
        "guidance": "Prioritize character psychology, consent, chemistry, pacing, and grounded physical and emotional detail.",
    },
    {
        "id": "action_adventure",
        "label": "Action/adventure",
        "patterns": (
            r"\baction\b",
            r"\badventure\b",
            r"\bfight\b",
            r"\brescue\b",
            r"\bchase\b",
            r"\bbattle\b",
            r"\bambush\b",
            r"\bescape\b",
        ),
        "guidance": "Use clear blocking, spatial continuity, physical costs, and tactical cause-and-effect.",
    },
)


def build_genre_time_helper(
    *,
    director_note: str,
    world_notes: dict[str, Any] | None = None,
    session_summary: str | None = None,
    recent_scenes: list[dict[str, Any]] | None = None,
    target_scene: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text_parts = [director_note or "", session_summary or ""]
    if world_notes:
        text_parts.extend(str(world_notes.get(field, "") or "") for field, _label in WORLD_FIELDS)
    for scene in (recent_scenes or [])[-3:]:
        text_parts.append(str(scene.get("director_note", "") or ""))
        text_parts.append(str(scene.get("generated_text", "") or "")[:900])
    if target_scene:
        text_parts.append(str(target_scene.get("director_note", "") or ""))
        text_parts.append(str(target_scene.get("generated_text", "") or "")[:900])

    haystack = "\n".join(text_parts)
    matches: list[dict[str, str]] = []
    for rule in GENRE_TIME_RULES:
        for pattern in rule["patterns"]:
            if re.search(pattern, haystack, flags=re.IGNORECASE):
                matches.append(
                    {
                        "id": str(rule["id"]),
                        "label": str(rule["label"]),
                        "guidance": str(rule["guidance"]),
                    }
                )
                break
    if not matches:
        matches.append(
            {
                "id": "story_specific",
                "label": "Story-specific",
                "guidance": "Infer diction from the supplied story, world notes, recent scenes, and director note. Do not force a genre voice that the content does not support.",
            }
        )
    return {
        "active": True,
        "matches": matches[:3],
        "note": "This guide is derived from visible story/world/director-note context and is included in the prompt preview.",
    }


def render_genre_time_helper(helper: dict[str, Any] | None) -> str:
    if not helper or not helper.get("matches"):
        return ""
    lines = [str(helper.get("note") or "Use story-specific diction.")]
    for item in helper.get("matches", []):
        label = str(item.get("label") or "Story-specific").strip()
        guidance = str(item.get("guidance") or "").strip()
        if label and guidance:
            lines.append(f"- {label}: {guidance}")
    return "\n".join(lines).strip()


def get_relevant_memories(session_id: str, director_note: str, recent_scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    with db_session() as db:
        rows = db.execute(
            """
            SELECT memory_type, title, content, importance
            FROM story_memories
            WHERE session_id = ? AND TRIM(content) != ''
            ORDER BY importance DESC, updated_at DESC, created_at DESC
            LIMIT 8
            """,
            (session_id,),
        ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def mode_instruction(mode: str) -> str:
    if mode == "regenerate":
        return (
            "Generate a fresh alternate version of the target/latest story passage. Use the same director intent, "
            "preserve continuity, core story purpose, and approximate scene scope/length unless the director explicitly asks shorter, "
            "but vary the prose, pacing, and moment-to-moment details."
        )
    if mode == "rewrite":
        return (
            "Rewrite the latest story passage as a fresh version. Preserve the same underlying story events "
            "and approximate scene scope/length unless the director note explicitly changes them. Do not compress into summary. "
            "Output only the rewritten passage."
        )
    if mode == "revise":
        return (
            "Revise the target/latest story passage according to the director note. Keep continuity with the "
            "surrounding story and preserve the target scene's approximate scope/length unless the director asks shorter. "
            "Output only the revised passage."
        )
    return (
        "Continue the story after the recent passages. Follow the director note and output only the next passage."
    )


def _compact_plan_value(value: Any) -> list[str]:
    if value is None or value == "" or value == [] or value == {}:
        return []
    if isinstance(value, list):
        lines: list[str] = []
        for item in value[:6]:
            if isinstance(item, dict):
                parts = [
                    f"{key}: {str(child).strip()[:220]}"
                    for key, child in item.items()
                    if child not in (None, "", [], {}) and str(child).strip()
                ]
                if parts:
                    lines.append("; ".join(parts)[:300])
            elif str(item).strip():
                lines.append(str(item).strip()[:300])
        return lines
    if isinstance(value, dict):
        lines = []
        for key, child in value.items():
            child_lines = _compact_plan_value(child)
            if not child_lines:
                continue
            joined = "; ".join(child_lines)
            lines.append(f"{key}: {joined}"[:300])
        return lines
    return [str(value).strip()[:300]] if str(value).strip() else []


def render_writing_process_plan(plan: dict[str, Any] | None) -> str:
    if not plan:
        return ""
    field_labels = [
        ("scene_contract", "Scene Contract"),
        ("required_director_facts", "Required director facts"),
        ("characters_present", "Characters present"),
        ("viewpoint_contract", "Viewpoint Contract"),
        ("character_agency_matrix", "Character Agency Matrix"),
        ("blocking_map", "Blocking Map"),
        ("continuity_facts", "Continuity facts"),
        ("sensory_anchors", "Sensory anchors"),
        ("world_grounding_anchors", "World grounding anchors"),
        ("story_beats", "Story beats"),
        ("character_introduction_requirements", "Character introduction requirements"),
        ("forbidden_leaps_or_skips", "Forbidden leaps or skips"),
        ("anti_fixation_checks", "Anti-fixation checks"),
        ("ending_handoff", "Ending handoff"),
        ("continuity_risks", "Continuity risks"),
        ("required_director_note_details", "Required director-note details"),
        ("continuity_facts_to_preserve", "Continuity facts to preserve"),
        ("pacing_target", "Pacing target"),
        ("likely_ending_beat", "Likely ending beat"),
        ("key_detail_checklist", "Key-detail checklist"),
    ]
    lines: list[str] = []
    label = str(plan.get("label") or plan.get("mode") or "Visible writing plan").strip()
    if label:
        lines.append(f"Mode: {label}")
    for key, field_label in field_labels:
        value_lines = _compact_plan_value(plan.get(key))
        if not value_lines:
            continue
        lines.append(f"{field_label}:")
        lines.extend(f"- {line}" for line in value_lines)
    return "\n".join(lines).strip()


def build_scene_prompt(
    *,
    session_id: str,
    director_note: str,
    mode: str,
    recent_scenes: list[dict[str, Any]],
    session_summary: str | None = None,
    memories: list[dict[str, Any]] | None = None,
    world_notes: dict[str, Any] | None = None,
    active_characters: list[dict[str, Any]] | None = None,
    target_scene: dict[str, Any] | None = None,
    task_notes: str = "",
    writing_length: dict[str, Any] | None = None,
    writing_process_plan: dict[str, Any] | None = None,
    genre_time_helper: dict[str, Any] | None = None,
    prompt_mode: str = "standard",
    mark_state_used: bool = True,
) -> str:
    prompt_mode = (prompt_mode or "standard").strip().lower()
    memories = memories if memories is not None else get_relevant_memories(session_id, director_note, recent_scenes)
    world_notes = world_notes if world_notes is not None else load_world_notes(session_id)
    active_characters = active_characters if active_characters is not None else load_active_characters(session_id)
    relevance_text = "\n".join(
        [
            director_note or "",
            "\n".join(scene.get("generated_text", "")[:500] for scene in recent_scenes[-3:]),
        ]
    )
    memory_pack_context, _memory_pack_item_count, _memory_pack = format_next_prompt_memory_pack(
        session_id,
        relevance_text=relevance_text,
        context_kind="writer",
        limit=4600,
        mark_used=mark_state_used,
        cache=True,
    )
    story_state_context = ""
    if not memory_pack_context:
        story_state_context, _state_item_count = format_story_state_for_prompt(
            session_id,
            relevance_text=relevance_text,
            mark_used=mark_state_used,
        )
    writing_length = writing_length or resolve_writing_length(director_note=director_note)
    genre_time_helper = genre_time_helper or build_genre_time_helper(
        director_note=director_note,
        world_notes=world_notes,
        session_summary=session_summary,
        recent_scenes=recent_scenes,
        target_scene=target_scene,
    )

    sections = [
        "StoryDriver generation request",
        "",
    ]

    if task_notes.strip():
        sections.extend(["TASK NOTES:", compact_text(task_notes, WRITER_TASK_NOTE_CHARS), ""])

    genre_helper_text = render_genre_time_helper(genre_time_helper)
    if genre_helper_text:
        sections.extend(["GENRE / TIME-PERIOD DICTION GUIDE:", genre_helper_text, ""])

    foundation_context = "" if memory_pack_context else render_story_foundation_for_prompt(session_id, limit=WRITER_FOUNDATION_CHARS)
    if foundation_context:
        sections.extend([foundation_context, ""])

    requirements = director_requirement_lines(director_note, writing_length)
    if requirements:
        sections.extend(["DIRECTOR NOTE REQUIREMENTS:", *[f"- {line}" for line in requirements], ""])

    plan_text = render_writing_process_plan(writing_process_plan)
    if plan_text:
        sections.extend(
            [
                "STRUCTURED SCENE PLAN:",
                plan_text,
                "",
            ]
        )

    if memory_pack_context:
        sections.extend([memory_pack_context, ""])
    elif story_state_context:
        sections.extend([story_state_context, ""])
    elif world_notes:
        world_lines = [
            f"{label}: {compact_text(world_notes.get(field), WRITER_WORLD_FIELD_CHARS)}"
            for field, label in WORLD_FIELDS
            if has_text(world_notes.get(field))
        ]
        if world_lines:
            sections.extend(["WORLD NOTES:", *world_lines, ""])

    if active_characters and not story_state_context:
        character_blocks: list[str] = []
        for character in active_characters[:WRITER_CHARACTER_LIMIT]:
            if not has_text(character.get("name")):
                continue
            lines = [f"Name: {compact_text(character['name'], 120)}"]
            lines.extend(
                f"{label}: {compact_text(character.get(field), WRITER_CHARACTER_FIELD_CHARS)}"
                for field, label in CHARACTER_PROMPT_FIELDS
                if has_text(character.get(field))
            )
            character_blocks.append("\n".join(lines))
        if character_blocks:
            sections.extend(["ACTIVE CHARACTERS:", "\n\n".join(character_blocks), ""])

    if session_summary and session_summary.strip():
        sections.extend(["SESSION SUMMARY:", compact_text(session_summary, WRITER_SUMMARY_CHARS), ""])

    if memories:
        sections.append("RELEVANT STORY MEMORY:")
        for memory in memories[:WRITER_MEMORY_LIMIT]:
            title = memory.get("title") or memory.get("memory_type") or "Memory"
            content = memory.get("content") or ""
            if has_text(content):
                sections.append(f"- {compact_text(title, 120)}: {compact_text(content, WRITER_MEMORY_CHARS)}")
        sections.append("")

    sections.extend(["RECENT STORY PASSAGES:"])
    if recent_scenes:
        for index, scene in enumerate(recent_scenes[-WRITER_RECENT_SCENE_LIMIT:], start=1):
            generated_text = compact_text(scene.get("generated_text"), WRITER_RECENT_SCENE_CHARS)
            if generated_text:
                sections.extend([f"[Passage {index}]", generated_text, ""])
    else:
        sections.extend(["[No prior passages yet]", ""])

    if target_scene:
        sections.extend(
            [
                "TARGET PASSAGE FOR REWRITE/REVISION:",
                compact_text(target_scene.get("generated_text"), WRITER_TARGET_SCENE_CHARS) or "[Target passage is empty]",
                "",
            ]
        )

    if prompt_mode == "direct":
        final_instructions = [
            "System prompt is the creative authority. Treat Story State as factual continuity only.",
            "Treat the structured Scene Contract as binding for time span, start/end boundary, required beats, and must-not-yet constraints.",
            "Use the Blocking Map and Character Agency Matrix to keep people, objects, sightlines, goals, objections, and relationship pressure active.",
            "Follow the Viewpoint Contract: no sentence/paragraph head-hopping, and no unavailable knowledge unless the plan explicitly allows omniscient framing.",
            "Write narrated fiction prose only. Do not include labels, markdown headings, analysis, or chatty framing.",
            "Do not end with assistant-style offers, questions to the director, or summaries of what you wrote.",
        ]
    else:
        final_instructions = [
            "Use story details for continuity and prose quality only. Do not become a roleplay chat partner or speak as a character.",
            "Treat the structured Scene Contract as binding for time span, start/end boundary, required beats, and must-not-yet constraints.",
            "Use the Blocking Map and Character Agency Matrix to keep people, objects, sightlines, goals, objections, and relationship pressure active.",
            "Follow the Viewpoint Contract: no sentence/paragraph head-hopping, and no unavailable knowledge unless the plan explicitly allows omniscient framing.",
            "Write narrated fiction prose only. Do not include labels, markdown headings, analysis, or chatty framing.",
            "Do not end with assistant-style offers, questions to the director, or summaries of what you wrote.",
        ]

    sections.extend(
        [
            "DIRECTOR NOTE (strongest immediate instruction):",
            director_note.strip(),
            "",
            "MODE INSTRUCTION:",
            f"Mode: {mode}",
            mode_instruction(mode),
            "",
            *final_instructions,
        ]
    )

    return "\n".join(sections)
