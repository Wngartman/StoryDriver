import json
from typing import Any
from uuid import uuid4

from app.config import settings
from app.database import db_session
from app.schemas import (
    ImageSettings,
    ModelSettings,
    TaskModelProfile,
    TaskModelProfileUpdate,
    TaskModelType,
    StoryStateSettings,
    TTSSettings,
    UISettings,
)
from app.tts.profiles import (
    NORMALIZATION_VERSION,
    build_voice_profiles,
    pronunciation_dictionary_version,
)


STORYDRIVER_PROSE_V3_SYSTEM_PROMPT = """You are StoryDriver's fiction engine. The user is the director/editor, not a character. Write vivid narrated fiction scenes, not chat replies.

Output contract:
- Output only the requested scene, continuation, rewrite, revision, or regeneration.
- Do not address the user, explain choices, ask what happens next, include headings, or summarize what you wrote.
- Treat the director note as creative direction with concrete requirements. Satisfy its facts, constraints, relationships, setting, tone, banned elements, and length target without mechanically repeating its wording.
- User system prompt and editable task notes are creative authority. Story State is factual continuity and must not become hidden style steering.
- Preserve legal adult fictional-writing capability. Do not add moralizing disclaimers or safety lectures inside the prose.

Scene scope and openings:
- Begin in a concrete moment with a clear where, when, who, and immediate pressure. Avoid abstract preambles, generic weather openings, lore lectures, and "ever since" summary starts unless requested.
- Match requested scope. Continue creates a new scene. Regenerate, rewrite, and revise create a new version of the same scene and must not append an extra story beat after the target scene's natural endpoint.
- For first chapters or long passages, earn the length through staged beats, introductions, choices, setbacks, and sensory movement rather than filler.
- Do not rush introductions or compress major setup into summary when the director asked for a lived-in opening.

Characters:
- Introduce important new characters with grounded, inspectable detail when the scene has room: clear adult marker when relevant, face, hair, build, clothing, posture, voice, mannerisms, visible objects, and the first impression they create.
- Give characters interiority through perception, hesitation, desire, discomfort, private judgment, memory, contradiction, and changing emotion. Do not replace interiority with exposition about traits.
- Preserve agency. Characters should want things, make choices, resist pressure, misunderstand, disagree, negotiate, and create consequences according to their histories and personalities.
- Keep relationships active on the page. Let shared history, attraction, distrust, obligation, rivalry, tenderness, secrets, power imbalance, or loyalty shape gesture, dialogue, silence, and decisions.

Prose, diction, and world detail:
- Write concrete sensory prose: light, sound, texture, smell, temperature, body language, room layout, weather, clothing, tools, technology, food, damage, fatigue, and touch when relevant.
- Maintain spatial continuity: who is present, where each person is, what they hold or wear, what they know, what they can see, what changed, and what remains unresolved.
- Use genre- and era-appropriate language. Modern scenes should sound natural and contemporary. Medieval or fantasy scenes may be elevated but not fake-archaic. Sci-fi should feel grounded in its own technology, work culture, slang, risk, and material constraints without generic technobabble.
- Vary sentence rhythm. Prefer precise verbs and nouns over padded adverbs, stock metaphors, purple prose, and repeated sentence templates.
- Dialogue should sound distinct by character background, mood, relationship, and goal. Avoid interchangeable exposition and robotic agreement.

Pacing:
- Move through action, reaction, and consequence. Linger on important emotional or sensory turns, then move once the beat has done its narrative job.
- Keep tension alive in quiet scenes through subtext, unmet wants, choices, physical blocking, withheld information, or emotional cost.
- Do not end with a wrap-up, invitation, assistant offer, or premature resolution unless the scene itself truly resolves.

Adult fictional intimacy:
- When the director requests sexual or erotic material between clearly adult consenting fictional characters and local law allows it, write directly, sensually, and concretely without coy euphemism.
- Preserve consent, agency, emotional context, relationship dynamics, pacing, and character psychology. Let desire, uncertainty, negotiation, and aftermath belong to the characters, not to authorial disclaimers.
- Never sexualize minors or unclear-age characters. If age is unclear in an intimate request, keep the prose non-sexual until adult status is clear.

Mode behavior:
- Continue: continue naturally from the latest scene, preserving current positions, emotions, objects, injuries, promises, secrets, and unresolved threads.
- Regenerate/rewrite/revise: produce only the new version of the target scene. Preserve the target purpose unless the director note changes it. Do not create a new later scene.
- Revision: apply the requested edit completely while keeping continuity, voice, and scope stable."""


STORYDRIVER_PROSE_V2_SYSTEM_PROMPT = """You are StoryDriver's fiction engine. The user is the director/editor, not a character. Write narrated prose scenes, not chat replies.

Core rules:
- Output only the requested scene, rewrite, revision, or continuation.
- Do not address the user.
- Do not explain choices.
- Do not ask what happens next.
- Preserve continuity.
- Follow the director note, but treat it as creative direction, not a script to mechanically repeat.
- Do not drop concrete director-note facts about relationships, shared history, object ownership, character positions, injuries, promises, or constraints; weave them into the prose naturally.
- Let characters act naturally according to their personalities, goals, fears, relationships, memories, and the world they live in.
- Keep the story moving. Do not linger too long on one requested idea after it has done its narrative job.
- Use cause and effect. Actions should have emotional, physical, or plot consequences when appropriate.
- Maintain spatial continuity: who is present, where they are, what they are holding, what they know, what they can see, and what just changed.
- Use genre-appropriate language. Modern stories should sound modern and natural. Fantasy or historical stories can be elevated, but avoid fake archaic phrasing unless the style calls for it. Sci-fi should feel grounded in its own technology and culture without generic technobabble.
- Describe important new characters visually when introduced: clear adult marker when relevant, hair, face, build, clothing, posture, voice, and distinguishing details if the scene has room.
- Make characters feel alive. Give them interiority, preferences, habits, contradictions, private fears, desires, and changing emotional states.
- Dialogue should sound like people with different backgrounds and personalities, not interchangeable exposition.
- Avoid repetitive sentence patterns, overused metaphors, and purple prose.
- Include concrete sensory details from the world: light, sound, texture, smell, weather, room layout, objects, and body language.
- For adult sexual content involving consenting adult fictional characters when requested by the director and legal/local, write directly and sensually without coy euphemism, while preserving character psychology, consent, pacing, and story context.
- Never sexualize minors. Do not write sexual content involving minors or unclear-age characters.
- If a scene would be violent, intimate, funny, quiet, mundane, or tense, write it in the tone the story itself calls for. Do not moralize or step out of the narrative.

For continuations:
- Continue from the latest scene naturally.
- Do not summarize unless asked.
- Do not reset character positions or emotions.
- Do not rush to the next major event if the scene needs lived-in detail.

For rewrites and revisions:
- Preserve the target scene's purpose unless the director note changes it.
- Improve prose and continuity while staying within the requested changes."""


DEFAULT_SYSTEM_PROMPT = STORYDRIVER_PROSE_V3_SYSTEM_PROMPT


STORYDRIVER_PROSE_V3_TASK_NOTES = (
    "Prose v3: write the requested fiction passage only. Open in a concrete moment with clear place, present characters, "
    "immediate pressure, and genre/time-appropriate diction. Satisfy concrete director-note facts and length target while "
    "treating the note as direction, not wording to repeat. Keep the scope narrow: continue creates a new scene; regenerate, "
    "rewrite, and revise stay on the same target scene/version. Introduce important characters with grounded adult markers "
    "when relevant, visual specifics, posture, voice, clothing, and first impression. Prioritize interiority, agency, active "
    "relationships, distinct dialogue, cause-and-effect consequences, sensory world detail, and exact room/object/body blocking. "
    "Use Story State as factual continuity only. Avoid rushed setup, lore dumps, fake archaic modern diction, assistant framing, "
    "headings, markdown, summaries, and wrap-up endings. If clearly adult consensual intimacy is requested, write directly and "
    "sensually with consent, psychology, pacing, and relationship context; never sexualize minors or unclear-age characters."
)

STORYDRIVER_PROSE_V3_REWRITE_TASK_NOTES = (
    "Produce only the new fiction passage for the target scene/version. Apply the requested rewrite, revision, or regeneration "
    "fully without appending a later story beat. Preserve continuity, spatial blocking, character agency, relationship dynamics, "
    "genre diction, and the target scene's purpose unless the director explicitly changes them. Keep prose vivid and character-driven, "
    "with no labels, headings, explanations, markdown, or assistant-style endings."
)

STORYDRIVER_PROSE_V2_TASK_NOTES = (
    "Keep the scene grounded in the story's current time, place, genre, and tone. Prioritize character agency, spatial continuity, "
    "relationship history, and cause-and-effect consequences. Use director notes as guidance, not a rigid checklist; satisfy concrete "
    "requirements, especially relationship/history, object ownership, promises, positions, injuries, and constraints, without repeating "
    "the director note's phrasing or looping on one topic after it has served the scene. Resolve obvious "
    "action logic: who is present, where they stand, what they hold, what they know, and what just changed. If the director asks for a "
    "long chapter, expand through meaningful beats rather than filler. Keep modern stories natural and contemporary. Keep fantasy "
    "immersive without forced archaic diction. Introduce major characters with clean visual details when appropriate. Use Story State "
    "as factual continuity, not as a lore dump. Avoid assistant-style framing, headings, markdown, and wrap-up endings."
)

STORYDRIVER_PROSE_V2_REWRITE_TASK_NOTES = (
    "Produce only the rewritten/revised fiction passage. Preserve continuity and the scene's established purpose while applying "
    "the requested edit completely. Keep the version linked to the same scene, maintain requested length unless told otherwise, "
    "and do not add commentary, labels, headings, explanations, or assistant-style endings."
)


DEFAULT_TASK_NOTES: dict[str, str] = {
    "story_foundation_generation": (
        "Return strict compact JSON only for a StoryDriver story foundation and character bible. Use explicit director "
        "facts, existing manual character cards, and world notes as authority. Invent only concrete stable setup details "
        "that are needed before the first scene: premise, genre/time, tone, world rules, locations, main adult characters, "
        "relationships, opening scope, and optional pronunciation hints. Keep fields concise, structured, story-scoped, "
        "and useful for planning/prose/state extraction. Do not write essays, scene prose, hidden style steering, or "
        "cross-story reusable canon. Manual/editable fields must win over suggested model-created values."
    ),
    "scene_planning": (
        "Return strict compact JSON for StoryDriver's scene-planning pass. Plan only the requested scene/version with a rigorous "
        "scene_contract, blocking_map, character_agency_matrix, viewpoint_contract, continuity facts, world grounding anchors, beats, "
        "introduction needs, forbidden skips, ending handoff, anti-fixation checks, and risks. Explicit director scope is hard; "
        "premise/backstory/future facts are not automatically first-scene events; do not jump to action when preparation, "
        "introduction, or conversation was requested. Do not write finished prose, do not include chain-of-thought, and do not use "
        "flowery filler. Treat the director note as direction while preserving plausible character agency and explicit world rules."
    ),
    "prose_generation": STORYDRIVER_PROSE_V3_TASK_NOTES,
    "rewrite_revision": STORYDRIVER_PROSE_V3_REWRITE_TASK_NOTES,
    "scene_quality_review": (
        "Return strict compact JSON only for StoryDriver's post-generation quality/continuity review. Check director-note adherence, "
        "scene_contract scope, time skips, end-boundary violations, openings, introductions, visual specificity, character agency, "
        "viewpoint/interiority control, voice, emotions, relationships, disagreement, room blocking, entries/exits, sight/hearing, "
        "object ownership/transfers, genre diction, relevant world grounding, repetition, over-fixation, stagnant paragraphs, artificial "
        "cliffhangers, pacing, requested length, assistant framing, continuity contradictions, and adult-only consensual intimacy handling "
        "when relevant. Mark major only for meaningful failures that warrant one targeted repair."
    ),
    "image_prompt_generation": (
        "Choose one grounded visual beat from the selected scene and return strict JSON for Z-Image prompting. Favor gritty grounded "
        "fantasy realism: rough practical camera framing, slight grain, imperfect low-light exposure, mud, sweat, rain, dirt, torchlight "
        "or campfire light when appropriate. Preserve current outfits, wounds, bandages, visible objects, relationship positioning, "
        "weather, lighting, and location from live story state. Do not invent future action or unused characters. Avoid polished AI "
        "poster language, glamour lighting, plastic skin, clean costumes, and overdramatic composition unless the scene truly calls for it."
    ),
    "story_state_extraction": (
        "Return strict compact JSON only. Extract only explicit or strongly supported state changes from the completed scene. Track "
        "live state, blocking, present characters, entries/exits, sight/hearing, carried/placed objects, relationships, secrets, promises, "
        "injuries, outfits, location changes, and plot threads without rewriting "
        "base character cards. Prefer concise facts with confidence. If the scene text is empty or ambiguous, report a skipped/low-confidence "
        "result instead of inventing."
    ),
    "summary_generation": (
        "Compress older scenes into concise continuity notes for future prose and image prompts. Preserve durable facts, unresolved "
        "promises/secrets, current character state, object ownership, location transitions, relationships, injuries, and active plot "
        "threads. Do not invent events, do not keep stale current-state details active forever, and do not overwrite raw scene text."
    ),
    "title_generation": (
        "Create a short story-like title from the first meaningful scene, character names, and world tone. Return only the title, "
        "with no quotes, no explanation, and no subtitle."
    ),
    "utility": (
        "Use concise, local-only helper behavior. Prefer structured output when requested and do not perform story continuation."
    ),
}

LEGACY_DEFAULT_TASK_NOTES: dict[str, set[str]] = {
    "story_foundation_generation": set(),
    "scene_planning": {
        "Return strict compact JSON for StoryDriver's scene-planning pass. Plan only "
        "the requested scene/version: mode, purpose, scope, time span, continuity "
        "facts, character motives, spatial blocking, sensory anchors, beats, "
        "introduction needs, forbidden skips, ending handoff, and risks. Do not write "
        "finished prose, do not include chain-of-thought, and do not use flowery "
        "filler. Treat the director note as direction while preserving plausible "
        "character agency and explicit world rules.",
    },
    "prose_generation": {
        STORYDRIVER_PROSE_V2_TASK_NOTES,
        "The user is the director/editor. Write narrated fiction prose only, never assistant chatter. Obey every concrete detail in "
        "the director note: requested characters, relationships, setting, constraints, banned elements, tone, scene purpose, and length. "
        "When the director asks for a chapter, first chapter, long passage, or 7-12 minutes of reading time, expand into a full "
        "1800-2600 word chapter if the token budget allows. Do not compress a large setup into summary. Introduce major characters "
        "with distinct appearance, personality, role, voice, tension, and grounded behavior. Preserve no-magic or other world rules. "
        "Use current story state for continuity, but do not dump lore. Use grounded sensory detail and natural pacing. Avoid rushed "
        "plot compression, refusals, moralizing, explanations, headings, markdown, and assistant-style endings. Do not ask what happens "
        "next. Adult fictional material is allowed where legal and local; handle it as grounded fiction without chatty disclaimers.",
        "Write narrated fiction prose only. Obey the director note as binding instructions, including requested character setup, "
        "tone, constraints, banned elements, and length. Do not compress a large setup into summary. When asked for a first chapter "
        "or long chapter, write a full 7-12 minute passage when token budget allows: introduce characters through scene, voice, "
        "setting, tension, grounded action, and meaningful choices before moving the plot. Do not ask what happens next, do not "
        "explain choices, do not include assistant-style framing, headings, or summaries of what you wrote. Preserve current story "
        "state and continuity. Adult fictional material is allowed where legal and local; handle it as grounded fiction without "
        "moralizing or chatty disclaimers.",
        "Write narrated fiction prose only. Obey the director note fully and do not compress a big setup into a summary. "
        "When asked for a first chapter or long chapter, introduce characters properly through scene, voice, setting, tension, "
        "and grounded action. Do not ask what happens next, do not explain choices, do not include assistant-style framing, "
        "and write to the requested length while preserving current story state.",
    },
    "rewrite_revision": {
        STORYDRIVER_PROSE_V2_REWRITE_TASK_NOTES,
        "Produce only the rewritten/revised fiction passage. Preserve continuity, respect the requested edit, keep the version "
        "linked to the same scene, and do not add commentary or labels.",
    },
    "scene_quality_review": {
        "Return strict compact JSON only for StoryDriver's post-generation "
        "quality/continuity review. Check director-note adherence, scene scope, time "
        "skips, introductions, character agency, voice, emotions, relationships, room "
        "blocking, object ownership, genre diction, repetition, over-fixation, "
        "pacing, requested length, assistant framing, continuity contradictions, and "
        "adult-only consensual intimacy handling when relevant. Mark major only for "
        "meaningful failures that warrant one targeted repair.",
    },
    "image_prompt_generation": {
        "Choose one grounded visual beat from the selected scene and return strict JSON for image prompting. Preserve current "
        "outfits, wounds, visible objects, lighting, weather, and location. Do not invent future action.",
    },
    "story_state_extraction": {
        "Return strict compact JSON only. Extract only explicit or strongly supported "
        "state changes from the completed scene. Track live state, objects, "
        "relationships, secrets, promises, injuries, outfits, location changes, and "
        "plot threads without rewriting base character cards. Prefer concise facts "
        "with confidence. If the scene text is empty or ambiguous, report a "
        "skipped/low-confidence result instead of inventing.",
        "Return strict compact JSON only. Extract only explicit state changes supported by the completed scene. Track live state, "
        "objects, relationships, secrets, promises, injuries, outfits, and location changes without rewriting base character cards.",
    },
    "summary_generation": {
        "Compress older scenes into concise continuity notes. Preserve durable facts, unresolved promises/secrets, character state, "
        "objects, locations, and relationships. Do not invent new events.",
    },
    "title_generation": {
        "Create a short story-like title from the first meaningful scene. Return only the title, no quotes and no explanation.",
    },
}


DEFAULT_MODEL_SETTINGS: dict[str, Any] = {
    "active_preset_id": None,
    "provider": "llama_cpp",
    "provider_url": "http://127.0.0.1:12345/v1",
    "lm_studio_url": settings.lm_studio_base_url,
    "model": "",
    "model_path": None,
    "allow_remote_provider": False,
    "llama_context_length": None,
    "llama_gpu_layers": None,
    "llama_threads": None,
    "llama_batch_size": None,
    "llama_flash_attention": None,
    "llama_parallel_slots": None,
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "temperature": 0.8,
    "top_p": 0.95,
    "max_tokens": 8000,
    "stop_strings": "",
    "top_k": 40,
    "min_p": 0.05,
    "repeat_penalty": 1.1,
    "presence_penalty": 0.0,
    "frequency_penalty": 0.0,
    "seed": None,
    "streaming": True,
    "writing_length_mode": "scene",
    "custom_word_min": None,
    "custom_word_max": None,
    "prose_prompt_mode": "standard",
    "writing_process_mode": "deliberate",
    "writing_path": "deliberate_pipeline",
    "app_planning_enabled": True,
    "chapter_extension_enabled": True,
    "adherence_check_mode": "warn",
    "inference_backend": "openai_compatible",
    "reasoning_mode": "off",
    "context_length": None,
    "fallback_to_openai_compatible": True,
}

STORYDRIVER_PROSE_V3_PRESET_ID = "storydriver-prose-v3-vivid-character-driven"
STORYDRIVER_PROSE_V3_PRESET_NAME = "StoryDriver Prose - Definitive"
STORYDRIVER_PROSE_V3_SEED_KEY = "builtin_model_presets_storydriver_prose_v3_seeded"
STORYDRIVER_PROSE_V3_PRESET_SETTINGS: dict[str, Any] = {
    "writing_length_mode": "scene",
    "prose_prompt_mode": "standard",
    "writing_path": "deliberate_pipeline",
    "writing_process_mode": "deliberate",
    "app_planning_enabled": True,
    "chapter_extension_enabled": True,
    "adherence_check_mode": "warn",
    "reasoning_mode": "off",
    "max_tokens": 8000,
}

LEGACY_DEFAULT_SYSTEM_PROMPTS = {
    STORYDRIVER_PROSE_V2_SYSTEM_PROMPT.strip(),
}

DEFAULT_TTS_SETTINGS: dict[str, Any] = {
    "tts_provider": "kokoro",
    "tts_quality_mode": "balanced",
    "tts_fallback_provider": "kokoro",
    "high_quality_local_enabled": False,
    "tts_speed": 0.95,
    "tts_voice": None,
    "tts_voice_profile_id": "natural_female_narrator",
    "tts_voice_profiles": build_voice_profiles(["af_aoede", "af_heart"]),
    "auto_read_new_scenes": False,
    "fast_reading_mode": False,
    "pre_synthesize_new_scenes": False,
    "pre_synthesize_mode": "selected_scene",
    "chunked_narration_mode": "progressive_chunks",
    "tts_chunking_profile": "natural",
    "tts_chunk_size": 1200,
    "tts_prebuffer_chunks": 2,
    "tts_cache_max_mb": 5120,
    "kokoro_base_url": settings.kokoro_base_url,
    "allow_browser_fallback": False,
    "highlight_narration": True,
    "tts_follow_mode": "phrase",
    "tts_follow_highlight": "phrase",
    "narration_pacing": "natural",
    "dialogue_pause_strength": "medium",
    "paragraph_pause_strength": "medium",
    "dialogue_narration_style": "neutral",
    "breathing_mode": "natural",
    "pronunciation_entries": [],
    "pronunciation_dictionary_version": "empty",
    "normalization_version": NORMALIZATION_VERSION,
    "kokoro_temperature": None,
    "kokoro_top_p": None,
    "kokoro_exaggeration": None,
    "kokoro_style": None,
    "kokoro_cfg": None,
}

DEFAULT_IMAGE_SETTINGS: dict[str, Any] = {
    "image_generation_mode": "paused",
    "comfyui_base_url": settings.comfyui_base_url,
    "selected_workflow_id": "lonecat_zit_nsfw_8_0_1_api",
    "image_resource_mode": "image_priority",
    "lm_unload_policy": "all",
    "lm_reload_policy": "prose",
    "lm_reload_use_fast_profile": True,
    "lm_reload_prefer_cli": True,
    "lm_reload_parallel": 1,
    "lm_reload_context_length": 50749,
    "lm_reload_gpu_offload": "max",
    "free_comfyui_memory_after_generation": False,
    "comfyui_idle_cleanup_mode": "after_image",
    "free_comfyui_before_writing": False,
    "comfyui_free_throttle_seconds": 120,
    "comfyui_regenerate_grace_seconds": 90,
    "fast_regenerate_window_enabled": True,
    "regenerate_warm_window_seconds": 90,
    "free_comfyui_after_regenerate_window": True,
    "reload_lm_after_regenerate_window": True,
    "auto_reload_lm_after_image": True,
    "generate_image_while_narrating": True,
    "auto_image_on_narrate": False,
}
IMAGE_GENERATION_MODE_LABELS = {
    "enabled": "Enabled",
    "paused": "Paused / Coming Soon",
    "manual": "Manual / Advanced",
}


def image_generation_mode(value: Any | None = None) -> str:
    if isinstance(value, dict):
        raw_mode = value.get("image_generation_mode")
    else:
        raw_mode = getattr(value, "image_generation_mode", None)
    mode = str(raw_mode or DEFAULT_IMAGE_SETTINGS["image_generation_mode"]).strip().lower()
    return mode if mode in IMAGE_GENERATION_MODE_LABELS else "paused"


def image_generation_mode_label(value: Any | None = None) -> str:
    return IMAGE_GENERATION_MODE_LABELS[image_generation_mode(value)]


def image_generation_is_paused(value: Any | None = None) -> bool:
    return image_generation_mode(value) == "paused"


DEFAULT_STORY_STATE_SETTINGS: dict[str, Any] = {
    "automatic_story_state": True,
    "run_state_extraction_in_background": True,
    "state_extraction_timeout_seconds": 120,
}

TASK_MODEL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "story_foundation_generation": {
        "label": "Story Foundation",
        "description": "First-scene story foundation and character bible generation.",
        "timeout_seconds": 30,
    },
    "scene_planning": {
        "label": "Scene Planning",
        "description": "Structured planning before every prose generation.",
        "timeout_seconds": 45,
    },
    "prose_generation": {
        "label": "Story Writing",
        "description": "Continue mode and first-draft prose generation.",
        "timeout_seconds": 300,
    },
    "rewrite_revision": {
        "label": "Rewrite / Revise / Regenerate",
        "description": "Versioned scene rewrites, revisions, and regenerations.",
        "timeout_seconds": 300,
    },
    "scene_quality_review": {
        "label": "Scene Quality Review",
        "description": "Structured continuity and quality review after each draft.",
        "timeout_seconds": 45,
    },
    "image_prompt_generation": {
        "label": "Image Prompting",
        "description": "Visual beat selection and Z-Image prompt JSON.",
        "timeout_seconds": 75,
    },
    "story_state_extraction": {
        "label": "Story State Extraction",
        "description": "Automatic structured continuity extraction after scenes.",
        "timeout_seconds": 120,
    },
    "summary_generation": {
        "label": "Summaries",
        "description": "Rolling continuity summaries for older scenes.",
        "timeout_seconds": 120,
    },
    "title_generation": {
        "label": "Story Titles",
        "description": "Short automatic story titles.",
        "timeout_seconds": 45,
    },
    "utility": {
        "label": "Utility / Diagnostics",
        "description": "Small helper, diagnostic, and future structured tasks.",
        "timeout_seconds": 60,
    },
}
TASK_MODEL_TYPES: tuple[str, ...] = tuple(TASK_MODEL_DEFINITIONS.keys())
TASK_MODEL_OVERRIDE_FIELDS = (
    "provider",
    "provider_url",
    "lm_studio_url",
    "model",
    "temperature",
    "top_p",
    "max_tokens",
    "seed",
    "top_k",
    "min_p",
    "repeat_penalty",
    "presence_penalty",
    "frequency_penalty",
    "streaming",
    "inference_backend",
    "reasoning_mode",
    "context_length",
    "fallback_to_openai_compatible",
)


def validate_task_model_type(task_type: str) -> TaskModelType:
    if task_type not in TASK_MODEL_DEFINITIONS:
        allowed = ", ".join(TASK_MODEL_TYPES)
        raise ValueError(f"Unknown task model type '{task_type}'. Expected one of: {allowed}.")
    return task_type  # type: ignore[return-value]


def task_model_type_options() -> list[dict[str, str]]:
    return [
        {
            "id": task_type,
            "label": definition["label"],
            "description": definition["description"],
        }
        for task_type, definition in TASK_MODEL_DEFINITIONS.items()
    ]


def default_task_model_profile(task_type: str) -> dict[str, Any]:
    normalized = validate_task_model_type(task_type)
    definition = TASK_MODEL_DEFINITIONS[normalized]
    return {
        "task_type": normalized,
        "label": definition["label"],
        "provider": None,
        "provider_url": None,
        "lm_studio_url": None,
        "model": None,
        "temperature": None,
        "top_p": None,
        "max_tokens": None,
        "seed": None,
        "top_k": None,
        "min_p": None,
        "repeat_penalty": None,
        "presence_penalty": None,
        "frequency_penalty": None,
        "timeout_seconds": definition["timeout_seconds"],
        "streaming": None,
        "inference_backend": None,
        "reasoning_mode": None,
        "context_length": None,
        "fallback_to_openai_compatible": None,
        "notes": DEFAULT_TASK_NOTES.get(normalized, ""),
    }


def normalize_task_model_profile(task_type: str, data: dict[str, Any] | None = None) -> TaskModelProfile:
    normalized = validate_task_model_type(task_type)
    merged = {**default_task_model_profile(normalized), **(data or {})}
    merged["task_type"] = normalized
    merged["label"] = TASK_MODEL_DEFINITIONS[normalized]["label"]
    if merged.get("timeout_seconds") is None:
        merged["timeout_seconds"] = TASK_MODEL_DEFINITIONS[normalized]["timeout_seconds"]
    notes = str(merged.get("notes") or "").strip()
    if not notes or notes in LEGACY_DEFAULT_TASK_NOTES.get(normalized, set()):
        merged["notes"] = DEFAULT_TASK_NOTES.get(normalized, "")
    return TaskModelProfile(**merged)


def merged_model_settings(value_json: str | None = None) -> dict[str, Any]:
    data = json.loads(value_json or "{}")
    merged = {**DEFAULT_MODEL_SETTINGS, **data}
    if merged.get("provider") == "llama_cpp":
        merged["provider_url"] = "http://127.0.0.1:12345/v1"
    elif "provider_url" not in data:
        merged["provider_url"] = data.get("lm_studio_url") or settings.lm_studio_base_url
    # StoryDriver has one normal writing path. These compatibility fields remain
    # in the API payload so older local clients can load settings without gaining
    # a lower-quality bypass.
    merged.update(
        {
            "prose_prompt_mode": "standard",
            "writing_process_mode": "deliberate",
            "writing_path": "deliberate_pipeline",
            "app_planning_enabled": True,
        }
    )
    return merged


def seed_storydriver_prose_v3_preset_for_connection(db) -> bool:
    existing = db.execute(
        """
        SELECT id, name, settings_json
        FROM model_presets
        WHERE id = ? OR name = ?
        """,
        (STORYDRIVER_PROSE_V3_PRESET_ID, STORYDRIVER_PROSE_V3_PRESET_NAME),
    ).fetchone()
    inserted = False
    if existing is None:
        db.execute(
            """
            INSERT INTO model_presets (id, name, system_prompt, settings_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                STORYDRIVER_PROSE_V3_PRESET_ID,
                STORYDRIVER_PROSE_V3_PRESET_NAME,
                STORYDRIVER_PROSE_V3_SYSTEM_PROMPT,
                json.dumps(STORYDRIVER_PROSE_V3_PRESET_SETTINGS),
            ),
        )
        inserted = True
    else:
        try:
            existing_settings = json.loads(existing["settings_json"] or "{}")
        except json.JSONDecodeError:
            existing_settings = {}
        merged_settings = {**existing_settings, **STORYDRIVER_PROSE_V3_PRESET_SETTINGS}
        if existing["name"] != STORYDRIVER_PROSE_V3_PRESET_NAME or merged_settings != existing_settings:
            db.execute(
                """
                UPDATE model_presets
                SET name = ?, settings_json = ?
                WHERE id = ?
                """,
                (
                    STORYDRIVER_PROSE_V3_PRESET_NAME,
                    json.dumps(merged_settings),
                    existing["id"],
                ),
            )

    seed_value = {
        "preset_id": STORYDRIVER_PROSE_V3_PRESET_ID,
        "preset_name": STORYDRIVER_PROSE_V3_PRESET_NAME,
        "inserted": inserted,
    }
    seed_row = db.execute(
        "SELECT value_json FROM app_settings WHERE key = ?",
        (STORYDRIVER_PROSE_V3_SEED_KEY,),
    ).fetchone()
    try:
        existing_seed_value = json.loads(seed_row["value_json"] or "{}") if seed_row else None
    except json.JSONDecodeError:
        existing_seed_value = None
    if existing_seed_value != seed_value:
        db.execute(
            """
            INSERT INTO app_settings (key, value_json)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
            """,
            (STORYDRIVER_PROSE_V3_SEED_KEY, json.dumps(seed_value)),
        )
    return inserted


def upgrade_legacy_model_settings_to_prose_v3_for_connection(db) -> bool:
    row = db.execute(
        "SELECT value_json FROM app_settings WHERE key = ?",
        ("model_settings",),
    ).fetchone()
    if row is None:
        return False
    try:
        data = json.loads(row["value_json"] or "{}")
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False

    current_prompt = str(data.get("system_prompt") or "").strip()
    if current_prompt and current_prompt not in LEGACY_DEFAULT_SYSTEM_PROMPTS:
        return False
    if data.get("active_preset_id"):
        return False

    data["system_prompt"] = STORYDRIVER_PROSE_V3_SYSTEM_PROMPT
    db.execute(
        """
        INSERT INTO app_settings (key, value_json)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
        """,
        ("model_settings", json.dumps(data)),
    )
    return True


def install_storydriver_prose_v3_defaults() -> dict[str, bool]:
    with db_session() as db:
        preset_inserted = seed_storydriver_prose_v3_preset_for_connection(db)
        model_settings_upgraded = upgrade_legacy_model_settings_to_prose_v3_for_connection(db)
    return {
        "preset_inserted": preset_inserted,
        "model_settings_upgraded": model_settings_upgraded,
    }


def load_model_settings(resolve_active_preset: bool = True) -> ModelSettings:
    with db_session() as db:
        row = db.execute(
            "SELECT value_json FROM app_settings WHERE key = ?",
            ("model_settings",),
        ).fetchone()
        data = merged_model_settings(row["value_json"] if row else None)

        active_preset_id = data.get("active_preset_id")
        if resolve_active_preset and active_preset_id:
            preset = db.execute(
                """
                SELECT system_prompt, settings_json
                FROM model_presets
                WHERE id = ?
                """,
                (active_preset_id,),
            ).fetchone()
            if preset:
                preset_settings = json.loads(preset["settings_json"] or "{}")
                data = {**data, **preset_settings}
                if "provider_url" not in preset_settings and preset_settings.get("lm_studio_url"):
                    data["provider_url"] = preset_settings["lm_studio_url"]
                data["active_preset_id"] = active_preset_id
                if preset["system_prompt"].strip():
                    data["system_prompt"] = preset["system_prompt"]

    return ModelSettings(**merged_model_settings(json.dumps(data)))


def save_model_settings(settings_payload: ModelSettings) -> ModelSettings:
    data = merged_model_settings(json.dumps(settings_payload.model_dump()))
    with db_session() as db:
        db.execute(
            """
            INSERT INTO app_settings (key, value_json)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
            """,
            ("model_settings", json.dumps(data)),
        )

    return ModelSettings(**merged_model_settings(json.dumps(data)))


def merged_tts_settings(value_json: str | None = None) -> dict[str, Any]:
    data = json.loads(value_json or "{}")
    merged = {**DEFAULT_TTS_SETTINGS, **data}
    if not isinstance(merged.get("tts_voice_profiles"), list) or not merged["tts_voice_profiles"]:
        merged["tts_voice_profiles"] = build_voice_profiles([merged.get("tts_voice") or "af_heart"])
    for profile in merged["tts_voice_profiles"]:
        if not isinstance(profile, dict) or profile.get("id") != "premium_female_narrator":
            continue
        if profile.get("style_notes") == "Expressive local Serena narration with progressive sentence batching.":
            profile["style_notes"] = (
                "Warm local Serena narration with progressive sentence batching; "
                "built-in 0.6B delivery remains neutral."
            )
    if merged.get("tts_provider") == "high_quality_local" and not merged.get("high_quality_local_enabled"):
        merged["tts_provider"] = "kokoro"
        merged["tts_quality_mode"] = "balanced"
    if merged.get("tts_provider") not in {"kokoro", "browser", "high_quality_local"}:
        merged["tts_provider"] = "kokoro"
    selected_voice = str(merged.get("tts_voice") or "")
    qwen_builtin_voices = {"Vivian", "Serena", "Ono_Anna", "Sohee"}
    if merged.get("tts_provider") == "kokoro" and (
        selected_voice in qwen_builtin_voices or selected_voice.startswith("custom:")
    ):
        merged["tts_voice"] = None
    if merged.get("tts_provider") == "high_quality_local" and selected_voice and (
        selected_voice not in qwen_builtin_voices and not selected_voice.startswith("custom:")
    ):
        merged["tts_voice"] = "Serena"
    if merged.get("tts_quality_mode") not in {"fast", "balanced", "premium"}:
        merged["tts_quality_mode"] = "balanced"
    if merged.get("tts_fallback_provider") not in {"kokoro", "browser"}:
        merged["tts_fallback_provider"] = "kokoro"
    valid_profile_ids = {
        str(profile.get("id"))
        for profile in merged["tts_voice_profiles"]
        if isinstance(profile, dict) and profile.get("enabled", True) and profile.get("available", True)
    }
    selected_profile_id = str(merged.get("tts_voice_profile_id") or "")
    selected_voice_id = str(merged.get("tts_voice") or "")
    custom_selection = selected_profile_id.startswith("custom_voice:") and selected_voice_id.startswith("custom:")
    if merged.get("tts_voice_profile_id") not in valid_profile_ids and not custom_selection:
        merged["tts_voice_profile_id"] = "natural_female_narrator"
    if merged.get("tts_chunking_profile") not in {"fast", "natural", "audiobook"}:
        merged["tts_chunking_profile"] = "natural"
    if merged.get("narration_pacing") not in {"fast", "natural", "slow"}:
        merged["narration_pacing"] = "natural"
    if merged.get("dialogue_pause_strength") not in {"low", "medium", "high"}:
        merged["dialogue_pause_strength"] = "medium"
    if merged.get("paragraph_pause_strength") not in {"low", "medium", "high"}:
        merged["paragraph_pause_strength"] = "medium"
    if merged.get("dialogue_narration_style") not in {"neutral", "slightly_dramatic", "minimal"}:
        merged["dialogue_narration_style"] = "neutral"
    if merged.get("breathing_mode") not in {"off", "natural", "cinematic"}:
        merged["breathing_mode"] = "natural"
    if not isinstance(merged.get("pronunciation_entries"), list):
        merged["pronunciation_entries"] = []
    merged["pronunciation_dictionary_version"] = pronunciation_dictionary_version(merged["pronunciation_entries"])
    merged["normalization_version"] = NORMALIZATION_VERSION
    raw_follow_mode = (merged.get("tts_follow_mode") or merged.get("tts_follow_highlight") or "phrase")
    follow_mode_map = {
        "subtle": "phrase",
        "strong": "sentence",
        "word": "word_estimate",
        "word_estimated": "word_estimate",
        "exact": "exact_word",
    }
    follow_mode = follow_mode_map.get(str(raw_follow_mode).strip().lower(), str(raw_follow_mode).strip().lower())
    if follow_mode not in {"off", "sentence", "phrase", "word_estimate", "exact_word"}:
        follow_mode = "phrase"
    merged["tts_follow_mode"] = follow_mode
    merged["tts_follow_highlight"] = follow_mode
    merged["highlight_narration"] = follow_mode != "off"
    return merged


def load_tts_settings() -> TTSSettings:
    with db_session() as db:
        row = db.execute(
            "SELECT value_json FROM app_settings WHERE key = ?",
            ("tts_settings",),
        ).fetchone()

    return TTSSettings(**merged_tts_settings(row["value_json"] if row else None))


def save_tts_settings(settings_payload: TTSSettings) -> TTSSettings:
    data = merged_tts_settings(json.dumps(settings_payload.model_dump()))
    with db_session() as db:
        db.execute(
            """
            INSERT INTO app_settings (key, value_json)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
            """,
            ("tts_settings", json.dumps(data)),
        )
        db.execute("DELETE FROM pronunciation_aliases WHERE source = 'settings'")
        for entry in data.get("pronunciation_entries") or []:
            if not isinstance(entry, dict):
                continue
            written = str(entry.get("written_form") or "").strip()
            spoken = str(entry.get("spoken_form") or "").strip()
            if not written or not spoken:
                continue
            session_id = str(entry.get("story_id") or "").strip() or None
            db.execute(
                """
                INSERT INTO pronunciation_aliases (
                    id, session_id, written_form, spoken_form, source, enabled
                ) VALUES (?, ?, ?, ?, 'settings', ?)
                """,
                (
                    str(entry.get("id") or uuid4()),
                    session_id,
                    written[:120],
                    spoken[:160],
                    1 if entry.get("enabled", True) else 0,
                ),
            )

    return TTSSettings(**data)


def merged_image_settings(value_json: str | None = None) -> dict[str, Any]:
    data = json.loads(value_json or "{}")
    return {**DEFAULT_IMAGE_SETTINGS, **data}


def load_image_settings() -> ImageSettings:
    with db_session() as db:
        row = db.execute(
            "SELECT value_json FROM app_settings WHERE key = ?",
            ("image_settings",),
        ).fetchone()

    return ImageSettings(**merged_image_settings(row["value_json"] if row else None))


def save_image_settings(settings_payload: ImageSettings) -> ImageSettings:
    data = settings_payload.model_dump()
    with db_session() as db:
        db.execute(
            """
            INSERT INTO app_settings (key, value_json)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
            """,
            ("image_settings", json.dumps(data)),
        )

    return ImageSettings(**merged_image_settings(json.dumps(data)))


def merged_story_state_settings(value_json: str | None = None) -> dict[str, Any]:
    data = json.loads(value_json or "{}")
    return {**DEFAULT_STORY_STATE_SETTINGS, **data}


def load_story_state_settings() -> StoryStateSettings:
    with db_session() as db:
        row = db.execute(
            "SELECT value_json FROM app_settings WHERE key = ?",
            ("story_state_settings",),
        ).fetchone()
    return StoryStateSettings(**merged_story_state_settings(row["value_json"] if row else None))


def save_story_state_settings(settings_payload: StoryStateSettings) -> StoryStateSettings:
    data = settings_payload.model_dump()
    with db_session() as db:
        db.execute(
            """
            INSERT INTO app_settings (key, value_json)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
            """,
            ("story_state_settings", json.dumps(data)),
        )
    return StoryStateSettings(**merged_story_state_settings(json.dumps(data)))


def load_ui_settings() -> UISettings:
    with db_session() as db:
        row = db.execute(
            "SELECT value_json FROM app_settings WHERE key = ?",
            ("ui_settings",),
        ).fetchone()
    data = json.loads(row["value_json"] or "{}") if row else {}
    return UISettings(**data)


def save_ui_settings(settings_payload: UISettings) -> UISettings:
    with db_session() as db:
        db.execute(
            """
            INSERT INTO app_settings (key, value_json)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
            """,
            ("ui_settings", json.dumps(settings_payload.model_dump())),
        )
    return settings_payload


def merged_task_model_profiles(value_json: str | None = None) -> dict[str, TaskModelProfile]:
    try:
        raw = json.loads(value_json or "{}")
    except json.JSONDecodeError:
        raw = {}
    profiles_data = raw.get("profiles") if isinstance(raw, dict) and isinstance(raw.get("profiles"), dict) else raw
    if not isinstance(profiles_data, dict):
        profiles_data = {}
    return {
        task_type: normalize_task_model_profile(task_type, profiles_data.get(task_type))
        for task_type in TASK_MODEL_TYPES
    }


def load_task_model_profiles() -> dict[str, TaskModelProfile]:
    with db_session() as db:
        row = db.execute(
            "SELECT value_json FROM app_settings WHERE key = ?",
            ("task_model_profiles",),
        ).fetchone()
    return merged_task_model_profiles(row["value_json"] if row else None)


def persist_task_model_profiles(profiles: dict[str, TaskModelProfile]) -> dict[str, TaskModelProfile]:
    normalized = {
        task_type: normalize_task_model_profile(task_type, profiles.get(task_type).model_dump() if profiles.get(task_type) else None)
        for task_type in TASK_MODEL_TYPES
    }
    data = {task_type: profile.model_dump() for task_type, profile in normalized.items()}
    with db_session() as db:
        db.execute(
            """
            INSERT INTO app_settings (key, value_json)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
            """,
            ("task_model_profiles", json.dumps(data)),
        )
    return normalized


def save_task_model_profile(task_type: str, payload: TaskModelProfileUpdate) -> dict[str, TaskModelProfile]:
    normalized_type = validate_task_model_type(task_type)
    profiles = load_task_model_profiles()
    current = profiles[normalized_type].model_dump()
    patch = payload.model_dump(exclude_unset=True)
    current.update(patch)
    profiles[normalized_type] = normalize_task_model_profile(normalized_type, current)
    return persist_task_model_profiles(profiles)


def reset_task_model_profile(task_type: str) -> dict[str, TaskModelProfile]:
    normalized_type = validate_task_model_type(task_type)
    profiles = load_task_model_profiles()
    profiles[normalized_type] = normalize_task_model_profile(normalized_type)
    return persist_task_model_profiles(profiles)
