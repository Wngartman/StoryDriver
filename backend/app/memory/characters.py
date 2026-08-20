from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from uuid import uuid4

from app.database import db_session
from app.generation.model_provider import LMStudioClient, LMStudioError, model_client_for_settings
from app.generation.router import resolve_task_model_settings, task_parameters


AUTO_CHARACTER_LIMIT = 6
MIN_MAJOR_CHARACTER_CONFIDENCE = 0.58
NAME_EXCLUSIONS = {
    "The",
    "A",
    "An",
    "And",
    "Are",
    "As",
    "At",
    "Before",
    "Behind",
    "Being",
    "Bring",
    "Because",
    "Between",
    "But",
    "By",
    "Can",
    "Could",
    "Or",
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
    "These",
    "Not",
    "One",
    "Then",
    "You",
    "From",
    "Every",
    "Only",
    "Those",
    "Though",
    "Through",
    "Under",
    "Until",
    "Maybe",
    "Must",
    "Check",
    "Think",
    "Knowing",
    "Alone",
    "Did",
    "Everything",
    "Gather",
    "Grey",
    "How",
    "Like",
    "Short",
    "While",
    "Twelve",
    "Thieves",
    "Old",
    "Man",
    "Woman",
    "Women",
    "Sister",
    "Sisters",
    "Girl",
    "Child",
    "Children",
    "Mother",
    "Father",
    "Parents",
    "Town",
    "Farm",
    "Camp",
    "Bandit",
    "Bandits",
    "Street",
    "Streets",
    "Chapter",
    "Scene",
    "Little",
    "Miller",
    "Blackwood",
    "Ridge",
    "Oakhaven",
    "StoryDriver",
}

NAME_EXCLUSION_LOWER = {item.lower() for item in NAME_EXCLUSIONS}
CHARACTER_CONTEXT_RE = re.compile(
    r"\b("
    r"said|asked|answered|replied|whispered|muttered|called|told|warned|nodded|shook|looked|watched|stood|sat|"
    r"walked|ran|moved|held|carried|took|gave|kept|hid|opened|closed|pressed|drew|wore|wearing|bandaged|"
    r"sister|brother|daughter|son|mother|father|woman|girl|scout|thief|scholar|mercenary|farmer|outcast"
    r")\b",
    re.I,
)
ACTION_LIKE_NAME_WORDS = {
    "bring",
    "check",
    "close",
    "come",
    "gather",
    "go",
    "hold",
    "leave",
    "listen",
    "look",
    "move",
    "open",
    "remember",
    "run",
    "stand",
    "stay",
    "stop",
    "think",
    "turn",
    "wait",
    "watch",
}
CHARACTER_SUBJECT_ACTIONS = (
    r"said|asked|answered|replied|whispered|muttered|called|told|warned|nodded|shook|"
    r"looked|watched|stood|sat|walked|ran|moved|held|carried|took|gave|kept|hid|opened|"
    r"closed|pressed|drew|wore|smiled|laughed|sighed|turned|stepped|leaned|reached|"
    r"followed|entered|left|paused|waited|checked|remembered|thought|decided|refused"
)
IMPERATIVE_FOLLOWERS = re.compile(
    r"^\s*(?:[!,.?:;]|(?:here|there|until|for|while|a|one|just|now|then|before|after|"
    r"and|or|please|outside|inside|up|down|back|away|still|longer|again|do\s+not|don't)\b)",
    re.I,
)
VISUAL_HINT_RE = re.compile(
    r"\b("
    r"adult|young|old|older|younger|eldest|middle|youngest|age|hair|braid|braided|blonde|black|brown|"
    r"red|grey|gray|white|pale|dark|face|eyes|sharp-eyed|scar|freckle|tattoo|mark|shoulder|forearm|"
    r"lean|slight|broad|tall|short|stout|thin|strong|wiry|build|height|cloak|coat|dress|tunic|shirt|"
    r"boots|hood|leather|wool|linen|sleeves|hem|belt|gloves|wore|wearing|dressed|carried|holding|"
    r"posture|limp|stride|movement|grime|mud|blood|dirt|weathered"
    r")\b",
    re.I,
)
VISUAL_PROSE_LEAK_RE = re.compile(
    r"\b(said|asked|answered|replied|remarked|whispered|muttered|thought|remembered|wondered|promised)\b",
    re.I,
)
VISUAL_PROFILE_FIELDS = (
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
)


def clean_text(value: Any, max_length: int = 4000) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split())[:max_length]


def plausible_character_name(name: str) -> bool:
    cleaned = clean_text(name, 120)
    if not cleaned:
        return False
    parts = cleaned.split()
    if len(parts) > 2:
        return False
    for part in parts:
        if not re.fullmatch(r"[A-Z][a-z][A-Za-z'-]{1,40}", part):
            return False
        if part.lower() in NAME_EXCLUSION_LOWER:
            return False
    first = parts[0]
    if first.endswith(("ing", "ed")) and first.lower() in NAME_EXCLUSION_LOWER:
        return False
    return True


def sentences_for_name(scene_text: str, name: str) -> list[str]:
    first = re.escape(name.split()[0])
    return [
        sentence
        for sentence in re.split(r"(?<=[.!?])\s+", scene_text)
        if re.search(rf"\b{first}\b", sentence)
    ]


def character_name_evidence(scene_text: str, name: str) -> dict[str, int]:
    """Classify name uses without treating capitalization or quotes as identity proof."""
    first = clean_text(name, 120).split()[0]
    escaped = re.escape(first)
    mentions = list(re.finditer(rf"\b{escaped}\b", scene_text, re.I))
    explicit_patterns = (
        rf"\b(?:named|called)\s+{escaped}\b",
        rf"\b(?:Mr|Mrs|Ms|Miss|Dr|Captain|Sergeant|Commander|Lady|Lord|Sir|Dame)\.?\s+{escaped}\b",
        rf"\b{escaped}(?:'s|\u2019s)\b",
        rf"\b{escaped}\s*,\s+(?:an?|the)\s+[a-z][a-z'-]+",
        rf"\b{escaped}\s+(?:said|asked|answered|replied|whispered|muttered|called|told|warned)\b",
    )
    explicit = sum(len(re.findall(pattern, scene_text, re.I)) for pattern in explicit_patterns)
    subject_actions = len(
        re.findall(
            rf"\b{escaped}\s+(?:(?:quietly|softly|sharply|slowly|carefully|finally|then)\s+)?(?:{CHARACTER_SUBJECT_ACTIONS})\b",
            scene_text,
            re.I,
        )
    )
    dialogue_addresses = len(
        re.findall(rf"[\"\u201c][^\"\u201d\n]{{0,120}}\b{escaped}\s*[,!?]", scene_text, re.I)
    )
    imperatives = 0
    for mention in mentions:
        prefix = scene_text[max(0, mention.start() - 160) : mention.start()]
        boundary = max(prefix.rfind(mark) for mark in (".", "!", "?", "\n", '"', "\u201c", "\u201d"))
        clause_prefix = prefix[boundary + 1 :].strip(" \t\r\n\"'\u201c\u201d")
        suffix = scene_text[mention.end() : mention.end() + 60]
        if not clause_prefix and IMPERATIVE_FOLLOWERS.search(suffix):
            imperatives += 1
    return {
        "mentions": len(mentions),
        "explicit": explicit,
        "subject_actions": subject_actions,
        "dialogue_addresses": dialogue_addresses,
        "imperatives": imperatives,
    }


def has_character_evidence(scene_text: str, name: str) -> bool:
    evidence = character_name_evidence(scene_text, name)
    if evidence["explicit"] or evidence["subject_actions"]:
        return True
    if name.split()[0].lower() in ACTION_LIKE_NAME_WORDS:
        return False
    if evidence["dialogue_addresses"] and evidence["imperatives"] < evidence["mentions"]:
        return True
    evidence_sentences = sentences_for_name(scene_text, name)
    if not evidence_sentences:
        return False
    joined = " ".join(evidence_sentences[:5])
    return bool(CHARACTER_CONTEXT_RE.search(joined))


def visual_hint_count(value: str) -> int:
    return len(VISUAL_HINT_RE.findall(value or ""))


def word_count(value: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", value or ""))


def sentence_count(value: str) -> int:
    return len([part for part in re.split(r"(?<=[.!?])\s+", value or "") if part.strip()])


def looks_contaminated_visual_text(value: Any, *, require_visual_hint: bool = False) -> bool:
    raw = "" if value is None else str(value).strip()
    text = clean_text(raw, 20000)
    if not text:
        return False
    words = word_count(text)
    hints = visual_hint_count(text)
    if len(text) > 500 or words > 90:
        return True
    if raw.count("\n") >= 2:
        return True
    if any(mark in raw for mark in ('"', "'", "“", "”", "‘", "’")) and (words > 24 or VISUAL_PROSE_LEAK_RE.search(text)):
        return True
    if VISUAL_PROSE_LEAK_RE.search(text) and words > 22:
        return True
    if words > 10 and re.search(r"\b(voice|there was no heat|possess(?:ed|ing)?)\b", text, re.I):
        return True
    if sentence_count(text) > 3 and hints < 3:
        return True
    if words > 38 and re.search(r"\b(sun|storm|road|room|door|window|table|farmhouse|campfire|silhouette|sky)\b", text, re.I):
        return True
    if require_visual_hint and hints == 0:
        return True
    return False


def compact_visual_fragment(
    value: Any,
    max_length: int = 220,
    *,
    require_visual_hint: bool = False,
    max_words: int = 42,
) -> str:
    raw = "" if value is None else str(value).strip()
    text = clean_text(raw, max_length * 4)
    if not text:
        return ""
    text = text.strip(" \"'")
    if looks_contaminated_visual_text(text, require_visual_hint=require_visual_hint):
        return ""
    words = text.split()
    if len(words) > max_words:
        fragments: list[str] = []
        for fragment in re.split(r"(?<=[.!?])\s+|[;\n]+", text):
            cleaned = clean_text(fragment, max_length)
            if not cleaned or word_count(cleaned) > max_words:
                continue
            if looks_contaminated_visual_text(cleaned, require_visual_hint=require_visual_hint):
                continue
            fragments.append(cleaned.rstrip(" ,;:."))
            if len(fragments) >= 2:
                break
        return "; ".join(fragments)[:max_length]
    if require_visual_hint and visual_hint_count(text) == 0:
        return ""
    return text.rstrip(" ,;:.")


def visual_sentences_for_name(scene_text: str, name: str) -> list[str]:
    return [
        compact_visual_fragment(sentence, 260)
        for sentence in sentences_for_name(scene_text, name)
        if VISUAL_HINT_RE.search(sentence)
    ][:5]


def first_match_text(pattern: str, text: str, *, max_length: int = 160) -> str:
    match = re.search(pattern, text, re.I)
    if not match:
        return ""
    for group in match.groups():
        if group:
            return compact_visual_fragment(group, max_length)
    return compact_visual_fragment(match.group(0), max_length)


def fallback_visual_profile(scene_text: str, name: str) -> dict[str, str]:
    visual_text = " ".join(visual_sentences_for_name(scene_text, name))
    hair = first_match_text(
        r"((?:short|long|cropped|braided|dark|black|brown|blonde|red|fair|grey|gray|white|loose|tangled|shorn|curly|straight)[^.;,]{0,60}\bhair\b)",
        visual_text,
    )
    face = first_match_text(
        r"(\b(?:sharp|round|thin|weathered|pale|dark|freckled|scarred|tired|plain|narrow|broad)[^.;,]{0,80}\b(?:face|features|eyes)\b)",
        visual_text,
    )
    build = first_match_text(
        r"((?:lean|slight|broad|tall|stout|thin|strong|wiry|compact)[^.;,]{0,80}\b(?:build|frame|body|shoulders|height)\b|(?:broad|strong|narrow|heavy)[^.;,]{0,40}\bshoulders\b)",
        visual_text,
    )
    marks = first_match_text(r"(\b(?:scar|freckle|tattoo|birthmark|mark)\b[^.;]{0,90})", visual_text)
    outfit = first_match_text(
        r"(?:wore|wearing|dressed in|wrapped in|in)\s+([^.;]{0,120}\b(?:cloak|coat|dress|tunic|shirt|boots|hood|leather|wool|linen|sleeves|belt|gloves)\b[^.;]{0,80})",
        visual_text,
        max_length=200,
    )
    age_marker = ""
    if re.search(r"\badult women?\b|\bthree women\b|\byoung women?\b", scene_text, re.I):
        age_marker = "adult woman"
    elif re.search(r"\badult\b", visual_text, re.I):
        age_marker = "adult"
    base_bits = [hair, face, build, marks]
    if not any(base_bits):
        base_bits = visual_sentences_for_name(scene_text, name)[:2] or [age_marker]
    needs_detail = not any([hair, face, build, outfit, marks])
    return {
        "base_visual_description": "; ".join(bit for bit in base_bits if bit)[:420],
        "face_description": face,
        "hair": hair,
        "body_build": build,
        "age_marker": age_marker,
        "default_outfit": outfit,
        "distinctive_marks": marks,
        "color_palette": "",
        "negative_prompt": "",
        "z_image_lora_trigger": "",
        "alternate_lora_triggers": "",
        "preferred_voice": "",
        "visual_consistency_notes": "Needs detail: no specific hair, face, build, outfit, or mark was supported by the source scene."
        if needs_detail
        else "",
    }


def fallback_relationships(scene_text: str, name: str, sister_names: list[str]) -> str:
    if sister_names and re.search(r"\bsisters?\b|\bthree women\b", scene_text, re.I):
        others = [item for item in sister_names if item.lower() != name.lower()]
        if others:
            return f"sister of {', '.join(others)}"
        return "one of three sisters"
    return ""


def fallback_personality(scene_text: str, name: str) -> str:
    joined = " ".join(sentences_for_name(scene_text, name)[:4]).lower()
    traits: list[str] = []
    for needle, label in (
        ("tactical", "tactical"),
        ("smart", "smart"),
        ("careful", "careful"),
        ("quiet", "quiet"),
        ("restless", "restless"),
        ("sharp", "sharp-eyed"),
        ("protect", "protective"),
        ("fear", "afraid but trying"),
        ("angry", "angry"),
        ("calculat", "calculating"),
        ("silent", "silent"),
        ("steady", "steady"),
    ):
        if needle in joined and label not in traits:
            traits.append(label)
    if traits:
        return ", ".join(traits[:3])
    if re.search(r"\bsmart\b|\btactical\b", scene_text, re.I):
        return "smart and tactical"
    return ""


def fallback_current_state(scene_text: str) -> str:
    bits: list[str] = []
    if re.search(r"\bfarm\b", scene_text, re.I):
        bits.append("living on a farm outside town")
    if re.search(r"\bbandit camp\b|\bcaptured\b|\blittle girl\b|\brescue\b", scene_text, re.I):
        bits.append("deciding whether to rescue a captured girl")
    if re.search(r"\bnever truly killed\b|\bnever truly fought\b", scene_text, re.I):
        bits.append("has not truly fought or killed before")
    return "; ".join(bits[:3]) or "introduced in the current scene"


def normalized_visual_profile_from_item(scene_text: str, item: dict[str, Any], name: str) -> dict[str, str]:
    nested = item.get("visual_profile") if isinstance(item.get("visual_profile"), dict) else {}
    fallback = fallback_visual_profile(scene_text, name)
    profile: dict[str, str] = {}
    aliases = {
        "base_visual_description": (
            "base_visual_description",
            "base_appearance_summary",
            "stable_appearance",
            "base_appearance",
            "visual_description",
            "appearance",
        ),
        "face_description": ("face_description", "face"),
        "hair": ("hair",),
        "body_build": ("body_build", "build", "body", "height_impression"),
        "age_marker": ("age_adult_marker", "age_marker", "adult_marker", "age"),
        "default_outfit": ("default_outfit", "default_clothing", "clothing", "outfit"),
        "distinctive_marks": ("distinctive_marks", "marks", "distinguishing_marks"),
        "color_palette": ("color_palette", "palette"),
        "negative_prompt": ("character_negative_prompt", "negative_prompt"),
        "z_image_lora_trigger": ("z_image_lora_trigger", "lora_trigger"),
        "alternate_lora_triggers": ("alternate_lora_triggers",),
        "preferred_voice": ("preferred_voice",),
        "visual_consistency_notes": ("visual_consistency_notes", "visual_notes"),
    }
    for field, keys in aliases.items():
        raw = ""
        for key in keys:
            raw = nested.get(key)
            if not clean_text(raw):
                raw = item.get(key) or ""
            if clean_text(raw):
                break
        if field in {"negative_prompt", "z_image_lora_trigger", "alternate_lora_triggers", "preferred_voice"}:
            cleaned = clean_text(raw, 420 if field != "negative_prompt" else 800)
        else:
            cleaned = compact_visual_fragment(
                raw,
                420 if field == "base_visual_description" else 240,
                require_visual_hint=field
                in {
                    "base_visual_description",
                    "face_description",
                    "hair",
                    "body_build",
                    "age_marker",
                    "default_outfit",
                    "distinctive_marks",
                    "color_palette",
                },
                max_words=50 if field == "base_visual_description" else 34,
            )
        profile[field] = cleaned or fallback[field]
    if not profile["base_visual_description"]:
        stable_bits = [
            profile["hair"],
            profile["face_description"],
            profile["body_build"],
            profile["distinctive_marks"],
        ]
        profile["base_visual_description"] = "; ".join(bit for bit in stable_bits if bit)[:420]
    return profile


def concise_image_prompt_for_character(name: str, profile: dict[str, str]) -> str:
    parts = [
        name,
        profile.get("base_visual_description", ""),
        profile.get("default_outfit", ""),
        profile.get("distinctive_marks", ""),
    ]
    return ", ".join(dict.fromkeys(part for part in (compact_visual_fragment(part, 180) for part in parts) if part))[:520]


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
    return parsed if isinstance(parsed, dict) else {}


def load_scene_text(session_id: str, scene_id: str, version_id: str | None) -> tuple[str, str | None]:
    with db_session() as db:
        if version_id:
            row = db.execute(
                """
                SELECT generated_text
                FROM scene_versions
                WHERE session_id = ? AND scene_id = ? AND id = ?
                """,
                (session_id, scene_id, version_id),
            ).fetchone()
            return (row["generated_text"] if row else "", version_id)
        row = db.execute(
            """
            SELECT id, generated_text
            FROM scene_versions
            WHERE session_id = ? AND scene_id = ?
            ORDER BY version_index DESC
            LIMIT 1
            """,
            (session_id, scene_id),
        ).fetchone()
        if row:
            return row["generated_text"] or "", row["id"]
        scene = db.execute(
            "SELECT generated_text FROM scenes WHERE session_id = ? AND id = ?",
            (session_id, scene_id),
        ).fetchone()
        return (scene["generated_text"] if scene else "", None)


def load_existing_character_names(session_id: str) -> dict[str, dict[str, Any]]:
    with db_session() as db:
        rows = db.execute(
            """
            SELECT c.id, c.name, 1 AS attached
            FROM session_characters sc
            JOIN characters c ON c.id = sc.character_id
            WHERE sc.session_id = ?
            """,
            (session_id,),
        ).fetchall()
    existing = {
        clean_text(row["name"], 160).lower(): {
            "id": row["id"],
            "name": row["name"],
            "attached": bool(row["attached"]),
        }
        for row in rows
        if clean_text(row["name"])
    }
    first_name_hits: dict[str, list[dict[str, Any]]] = {}
    for item in existing.values():
        first = clean_text(item["name"], 160).split()[0].lower()
        first_name_hits.setdefault(first, []).append(item)
    for first, matches in first_name_hits.items():
        if len(matches) == 1:
            existing.setdefault(first, matches[0])
    return existing


def build_character_detection_prompt(scene_text: str, existing_names: list[str], task_notes: str) -> str:
    schema = {
        "characters": [
            {
                "name": "Mara",
                "role": "thief/scout",
                "personality": "guarded, tactical, dryly funny",
                "relationships": "older sister of Elias",
                "current_state": "living on a farm outside town",
                "base_appearance_summary": "wiry adult scout with short black hair and a scar over her left eyebrow",
                "visual_profile": {
                    "base_visual_description": "short black hair; scar over left eyebrow; wiry adult scout",
                    "face_description": "scar over left eyebrow",
                    "hair": "short black hair",
                    "body_build": "wiry adult scout",
                    "age_adult_marker": "adult woman",
                    "default_outfit": "red cloak",
                    "distinctive_marks": "scar over left eyebrow",
                    "color_palette": "red cloak, dark hair, weathered leather",
                    "visual_consistency_notes": "Keep the scar and wiry scout build consistent.",
                },
                "image_prompt": "Mara, short black hair, scar over left eyebrow, wiry adult scout, red cloak",
                "character_negative_prompt": "",
                "z_image_lora_trigger": "",
                "alternate_lora_triggers": "",
                "preferred_voice": "",
                "major": True,
                "confidence": 0.8,
                "reason": "Introduced with dialogue, history, and scene importance.",
            }
        ],
        "warnings": [],
    }
    lines = [
        "Detect named major/recurring characters introduced in the newly generated StoryDriver scene.",
        "Return strict JSON only. Do not invent names. Do not create cards for minor guards, nameless villagers, background people, titles, places, or organizations.",
        "A character is major if they receive meaningful introduction, dialogue, family relationship, POV focus, current goal, or likely recurring story role.",
        "If a character already exists, do not return them unless they need to be attached to this story.",
        "Fill concise draft-card fields from the scene only. Unknown fields should be empty strings.",
        "For visual_profile, extract stable visual identity only: face, hair, build, age/adult marker, lasting marks/scars, default clothing style, posture/movement, and color palette.",
        "Use base_appearance_summary for a brief general appearance summary only.",
        "Do not paste whole scene paragraphs, dialogue, biography, relationship history, weather/location prose, or action summaries into appearance, visual_profile, or image_prompt.",
        "Temporary changes like wounds, dirt, blood, wetness, damaged clothing, and carried objects belong in Story State later, not in base visual identity unless the scene establishes them as lasting marks.",
        "Use no more than six characters.",
        "Return this shape exactly:",
        json.dumps(schema, ensure_ascii=False),
        "",
        f"Existing character names: {', '.join(existing_names[:80]) or 'None'}",
    ]
    if clean_text(task_notes):
        lines.extend(["", "Task notes:", clean_text(task_notes, 1200)])
    lines.extend(["", "Scene text:", scene_text[:12000]])
    return "\n".join(lines)


async def detect_characters(scene_text: str, existing_names: list[str]) -> tuple[list[dict[str, Any]], str]:
    model_settings, resolved_model = resolve_task_model_settings("story_state_extraction")
    client = model_client_for_settings(model_settings)
    model = model_settings.model.strip()
    if not model:
        models = await client.list_models()
        model = next((item.get("id") for item in models if item.get("id")), "")
    if not model:
        raise LMStudioError("No LM Studio model is selected or loaded for automatic character creation.")
    result = await client.generate_scene_routed(
        model=model,
        system_prompt=(
            "You draft concise character cards for StoryDriver from completed scenes. "
            "Return JSON only and only use facts in the scene."
        ),
        user_prompt=build_character_detection_prompt(scene_text, existing_names, resolved_model.notes),
        parameters=task_parameters(
            resolved_model,
            {
                "temperature": 0.15,
                "top_p": 0.85,
                "max_tokens": 1200,
                "presence_penalty": 0,
                "frequency_penalty": 0,
            },
            max_tokens_min=400,
            max_tokens_max=1800,
        ),
        timeout=min(max(float(resolved_model.timeout_seconds), 30.0), 120.0),
        inference_backend=resolved_model.inference_backend,
        reasoning_mode=resolved_model.reasoning_mode,
        context_length=resolved_model.context_length,
        fallback_to_openai_compatible=resolved_model.fallback_to_openai_compatible,
    )
    raw = result["text"]
    parsed = parse_json_object(raw)
    raw_characters = parsed.get("characters") if isinstance(parsed, dict) else []
    if not isinstance(raw_characters, list):
        return [], raw
    return [item for item in raw_characters if isinstance(item, dict)], raw


def fallback_detect_characters(scene_text: str, existing_names: list[str]) -> list[dict[str, Any]]:
    existing = {name.lower() for name in existing_names}
    candidates: dict[str, int] = {}
    for match in re.finditer(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?\b", scene_text):
        name = clean_text(match.group(0), 120)
        first = name.split()[0]
        if not plausible_character_name(first) or name.lower() in existing:
            continue
        candidates[first] = candidates.get(first, 0) + 1
    context_mentions_sisters = bool(re.search(r"\bsisters?\b|\bthree women\b|\bolder sister\b|\bmiddle sister\b|\bthird sister\b", scene_text, re.I))
    results: list[dict[str, Any]] = []
    sorted_candidates = sorted(candidates.items(), key=lambda item: (-item[1], item[0]))
    if context_mentions_sisters:
        repeated = [item for item in sorted_candidates if item[1] >= 2]
        if len(repeated) >= 3:
            sorted_candidates = repeated[:3]
    sister_names = [name for name, _count in sorted_candidates[:3]] if context_mentions_sisters else []
    for name, count in sorted_candidates[:AUTO_CHARACTER_LIMIT]:
        if not plausible_character_name(name) or not has_character_evidence(scene_text, name):
            continue
        if count < 2 and not context_mentions_sisters:
            continue
        visual_profile = fallback_visual_profile(scene_text, name)
        image_prompt = concise_image_prompt_for_character(name, visual_profile)
        role = "major character"
        if context_mentions_sisters:
            role = "one of the three sisters"
        results.append(
            {
                "name": name,
                "role": role,
                "personality": fallback_personality(scene_text, name),
                "appearance": visual_profile.get("base_visual_description", ""),
                "relationships": fallback_relationships(scene_text, name, sister_names),
                "current_state": fallback_current_state(scene_text),
                "image_prompt": image_prompt,
                "visual_profile": visual_profile,
                "major": True,
                "confidence": 0.74 if count >= 2 and image_prompt else 0.62,
                "reason": "Local fallback from repeated proper-name mentions after the structured detector returned blank.",
            }
        )
    return results


def count_candidate_names(scene_text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for match in re.finditer(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?\b", scene_text):
        name = clean_text(match.group(0), 120)
        first = name.split()[0]
        if not plausible_character_name(first):
            continue
        counts[first] = counts.get(first, 0) + 1
    return counts


def filter_detected_characters(
    scene_text: str,
    detected: list[dict[str, Any]],
    existing_names: list[str],
) -> list[dict[str, Any]]:
    counts = count_candidate_names(scene_text)
    existing = {name.lower() for name in existing_names}
    sister_context = bool(re.search(r"\bsisters?\b|\bthree women\b|\bolder sister\b|\bmiddle sister\b|\bthird sister\b", scene_text, re.I))
    repeated = [item for item in sorted(counts.items(), key=lambda item: (-item[1], item[0])) if item[1] >= 2]
    likely_sister_names = {name.lower() for name, _count in repeated[:3]} if sister_context and len(repeated) >= 3 else set()
    filtered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in detected:
        name = clean_text(item.get("name"), 120)
        if not name:
            continue
        first = name.split()[0]
        key = first.lower()
        if not plausible_character_name(first) or key in seen:
            continue
        if likely_sister_names and key not in likely_sister_names and name.lower() not in existing:
            continue
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if name.lower() not in existing:
            evidence = has_character_evidence(scene_text, first)
            if not evidence:
                continue
            if counts.get(first, 0) < 2 and confidence < 0.78:
                continue
            if confidence < MIN_MAJOR_CHARACTER_CONFIDENCE:
                continue
        if item.get("major") is False:
            continue
        copy = dict(item)
        copy["name"] = first
        filtered.append(copy)
        seen.add(key)
    return filtered[:AUTO_CHARACTER_LIMIT]


def upsert_detected_characters(
    *,
    session_id: str,
    scene_id: str,
    version_id: str | None,
    scene_text: str,
    detected: list[dict[str, Any]],
    existing: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    created: list[str] = []
    attached: list[str] = []
    skipped: list[str] = []
    with db_session() as db:
        for item in detected[:AUTO_CHARACTER_LIMIT]:
            name = clean_text(item.get("name"), 120)
            normalized_name = name.lower()
            if not plausible_character_name(name) or normalized_name in {"mother", "father", "girl", "little girl", "parents"}:
                skipped.append(name or "unnamed")
                continue
            try:
                confidence = float(item.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            if item.get("major") is False or confidence < MIN_MAJOR_CHARACTER_CONFIDENCE:
                skipped.append(name)
                continue
            existing_hit = existing.get(normalized_name)
            if existing_hit:
                db.execute(
                    """
                    INSERT OR IGNORE INTO session_characters (id, session_id, character_id, is_active)
                    VALUES (?, ?, ?, 1)
                    """,
                    (str(uuid4()), session_id, existing_hit["id"]),
                )
                attached.append(name)
                continue

            if not has_character_evidence(scene_text, name):
                skipped.append(name)
                continue

            character_id = str(uuid4())
            visual_profile = normalized_visual_profile_from_item(scene_text=scene_text, item=item, name=name)
            image_prompt = compact_visual_fragment(
                item.get("image_prompt"),
                520,
                require_visual_hint=True,
                max_words=52,
            ) or concise_image_prompt_for_character(
                name,
                visual_profile,
            )
            appearance = compact_visual_fragment(
                item.get("base_appearance_summary") or item.get("appearance"),
                420,
                require_visual_hint=True,
                max_words=48,
            )
            if not appearance or looks_contaminated_visual_text(appearance, require_visual_hint=True):
                appearance = visual_profile.get("base_visual_description", "")
            profile_notes = "; ".join(
                part
                for part in [
                    "Auto-created visual profile from generated scene",
                    f"confidence {confidence:.2f}",
                    f"source scene {scene_id}",
                    f"source version {version_id or 'latest'}",
                ]
                if part
            )
            private_notes = "\n".join(
                part
                for part in [
                    "Auto-created by StoryDriver from a generated scene.",
                    f"Source scene: {scene_id}",
                    f"Source version: {version_id or 'latest'}",
                    f"Reason: {clean_text(item.get('reason'), 800)}" if clean_text(item.get("reason")) else "",
                ]
                if part
            )
            db.execute(
                """
                INSERT INTO characters (
                    id, name, role, personality, appearance, relationships, current_state,
                    voice, image_prompt, lora_trigger, private_notes, auto_created
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, '', ?, 1)
                """,
                (
                    character_id,
                    name,
                    clean_text(item.get("role"), 4000),
                    clean_text(item.get("personality"), 8000),
                    appearance,
                    clean_text(item.get("relationships"), 8000),
                    clean_text(item.get("current_state"), 8000),
                    image_prompt,
                    private_notes,
                ),
            )
            db.execute(
                """
                INSERT INTO character_visual_profiles (
                    character_id, base_visual_description, face_description, hair, body_build,
                    age_marker, default_outfit, distinctive_marks, color_palette,
                    negative_prompt, z_image_lora_trigger, alternate_lora_triggers,
                    preferred_voice, visual_consistency_notes, used_in_image_prompts,
                    auto_created_confidence, source_scene_id, source_version_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(character_id) DO UPDATE SET
                    base_visual_description = CASE
                        WHEN character_visual_profiles.base_visual_description = '' THEN excluded.base_visual_description
                        ELSE character_visual_profiles.base_visual_description
                    END,
                    face_description = CASE
                        WHEN character_visual_profiles.face_description = '' THEN excluded.face_description
                        ELSE character_visual_profiles.face_description
                    END,
                    hair = CASE
                        WHEN character_visual_profiles.hair = '' THEN excluded.hair
                        ELSE character_visual_profiles.hair
                    END,
                    body_build = CASE
                        WHEN character_visual_profiles.body_build = '' THEN excluded.body_build
                        ELSE character_visual_profiles.body_build
                    END,
                    age_marker = CASE
                        WHEN character_visual_profiles.age_marker = '' THEN excluded.age_marker
                        ELSE character_visual_profiles.age_marker
                    END,
                    default_outfit = CASE
                        WHEN character_visual_profiles.default_outfit = '' THEN excluded.default_outfit
                        ELSE character_visual_profiles.default_outfit
                    END,
                    distinctive_marks = CASE
                        WHEN character_visual_profiles.distinctive_marks = '' THEN excluded.distinctive_marks
                        ELSE character_visual_profiles.distinctive_marks
                    END,
                    color_palette = CASE
                        WHEN character_visual_profiles.color_palette = '' THEN excluded.color_palette
                        ELSE character_visual_profiles.color_palette
                    END,
                    negative_prompt = CASE
                        WHEN character_visual_profiles.negative_prompt = '' THEN excluded.negative_prompt
                        ELSE character_visual_profiles.negative_prompt
                    END,
                    z_image_lora_trigger = CASE
                        WHEN character_visual_profiles.z_image_lora_trigger = '' THEN excluded.z_image_lora_trigger
                        ELSE character_visual_profiles.z_image_lora_trigger
                    END,
                    alternate_lora_triggers = CASE
                        WHEN character_visual_profiles.alternate_lora_triggers = '' THEN excluded.alternate_lora_triggers
                        ELSE character_visual_profiles.alternate_lora_triggers
                    END,
                    preferred_voice = CASE
                        WHEN character_visual_profiles.preferred_voice = '' THEN excluded.preferred_voice
                        ELSE character_visual_profiles.preferred_voice
                    END,
                    visual_consistency_notes = CASE
                        WHEN character_visual_profiles.visual_consistency_notes = '' THEN excluded.visual_consistency_notes
                        ELSE character_visual_profiles.visual_consistency_notes
                    END,
                    auto_created_confidence = MAX(character_visual_profiles.auto_created_confidence, excluded.auto_created_confidence),
                    source_scene_id = COALESCE(character_visual_profiles.source_scene_id, excluded.source_scene_id),
                    source_version_id = COALESCE(character_visual_profiles.source_version_id, excluded.source_version_id)
                """,
                (
                    character_id,
                    visual_profile.get("base_visual_description", ""),
                    visual_profile.get("face_description", ""),
                    visual_profile.get("hair", ""),
                    visual_profile.get("body_build", ""),
                    visual_profile.get("age_marker", ""),
                    visual_profile.get("default_outfit", ""),
                    visual_profile.get("distinctive_marks", ""),
                    visual_profile.get("color_palette", ""),
                    visual_profile.get("negative_prompt", ""),
                    visual_profile.get("z_image_lora_trigger", ""),
                    visual_profile.get("alternate_lora_triggers", ""),
                    visual_profile.get("preferred_voice", ""),
                    "; ".join(part for part in [visual_profile.get("visual_consistency_notes", ""), profile_notes] if part),
                    confidence,
                    scene_id,
                    version_id,
                ),
            )
            db.execute(
                """
                INSERT INTO session_characters (id, session_id, character_id, is_active)
                VALUES (?, ?, ?, 1)
                """,
                (str(uuid4()), session_id, character_id),
            )
            created.append(name)
    return {"created": created, "attached": attached, "skipped": skipped}


def create_fallback_characters_for_scene(
    *,
    session_id: str,
    scene_id: str,
    version_id: str | None,
) -> dict[str, Any]:
    scene_text, resolved_version_id = load_scene_text(session_id, scene_id, version_id)
    if not clean_text(scene_text):
        return {"created": [], "attached": [], "skipped": ["empty_scene_text"]}
    existing = load_existing_character_names(session_id)
    attached_keys = {item["name"].lower() for item in existing.values() if item.get("attached")}
    detected = [
        item
        for item in fallback_detect_characters(scene_text, [])
        if clean_text(item.get("name"), 120).lower() not in attached_keys
    ]
    if not detected:
        return {"created": [], "attached": [], "skipped": ["no_local_character_candidates"]}
    return upsert_detected_characters(
        session_id=session_id,
        scene_id=scene_id,
        version_id=resolved_version_id or version_id,
        scene_text=scene_text,
        detected=detected,
        existing=existing,
    )


async def run_auto_character_detection(
    *,
    session_id: str,
    scene_id: str,
    version_id: str | None,
) -> dict[str, Any]:
    scene_text, resolved_version_id = load_scene_text(session_id, scene_id, version_id)
    if not clean_text(scene_text):
        return {"status": "skipped", "reason": "empty_scene_text"}
    existing = load_existing_character_names(session_id)
    try:
        detected, raw = await detect_characters(scene_text, [item["name"] for item in existing.values()])
    except Exception as error:
        detected = []
        raw = f"LOCAL_FALLBACK_AFTER_ERROR: {error}"
    existing_names = [item["name"] for item in existing.values()]
    attached_keys = {item["name"].lower() for item in existing.values() if item.get("attached")}
    fallback = [
        item
        for item in fallback_detect_characters(scene_text, [])
        if clean_text(item.get("name"), 120).lower() not in attached_keys
    ]
    detected = filter_detected_characters(scene_text, detected, existing_names)
    if not detected and fallback:
        detected = filter_detected_characters(scene_text, fallback[:AUTO_CHARACTER_LIMIT], existing_names)
        raw = raw or "LOCAL_FALLBACK_EMPTY_DETECTOR"
    result = upsert_detected_characters(
        session_id=session_id,
        scene_id=scene_id,
        version_id=resolved_version_id or version_id,
        scene_text=scene_text,
        detected=detected,
        existing=existing,
    )
    return {
        "status": "completed",
        "detected_count": len(detected),
        "raw_response_length": len(raw),
        **result,
    }


def schedule_auto_character_detection(
    *,
    session_id: str,
    scene_id: str,
    version_id: str | None,
) -> None:
    async def runner() -> None:
        try:
            await asyncio.sleep(1.0)
            await run_auto_character_detection(session_id=session_id, scene_id=scene_id, version_id=version_id)
        except Exception:
            return

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
