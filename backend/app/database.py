from contextlib import closing, contextmanager
import sqlite3
from typing import Iterator

from app.config import DB_PATH
from app.utils.paths import ensure_runtime_paths


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    archived_at TEXT,
    title_source TEXT NOT NULL DEFAULT 'placeholder',
    auto_title_status TEXT NOT NULL DEFAULT 'skipped',
    auto_title_error TEXT,
    last_auto_title_attempt_at TEXT,
    auto_title_job_id TEXT,
    auto_title_scene_id TEXT,
    auto_title_version_id TEXT,
    deletion_status TEXT NOT NULL DEFAULT '',
    deletion_job_id TEXT,
    deletion_started_at TEXT,
    deletion_finished_at TEXT,
    deletion_error TEXT
);

CREATE TABLE IF NOT EXISTS story_delete_jobs (
    job_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    permanent INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT NOT NULL DEFAULT 'queued',
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    error TEXT,
    counts_json TEXT NOT NULL DEFAULT '{}',
    deleted_counts_json TEXT NOT NULL DEFAULT '{}',
    referenced_files_json TEXT NOT NULL DEFAULT '[]',
    files_deleted_json TEXT NOT NULL DEFAULT '[]',
    files_skipped_json TEXT NOT NULL DEFAULT '[]',
    deleted_file_bytes INTEGER NOT NULL DEFAULT 0,
    current_table TEXT,
    tables_completed INTEGER NOT NULL DEFAULT 0,
    total_tables INTEGER NOT NULL DEFAULT 0,
    rows_deleted INTEGER NOT NULL DEFAULT 0,
    referenced_file_count INTEGER NOT NULL DEFAULT 0,
    audit_path TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS scenes (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    director_note TEXT NOT NULL DEFAULT '',
    generated_text TEXT NOT NULL DEFAULT '',
    generation_stats_json TEXT NOT NULL DEFAULT '{}',
    mode TEXT NOT NULL DEFAULT 'continue',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS characters (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    personality TEXT NOT NULL DEFAULT '',
    appearance TEXT NOT NULL DEFAULT '',
    relationships TEXT NOT NULL DEFAULT '',
    current_state TEXT NOT NULL DEFAULT '',
    voice TEXT NOT NULL DEFAULT '',
    image_prompt TEXT NOT NULL DEFAULT '',
    lora_trigger TEXT NOT NULL DEFAULT '',
    private_notes TEXT NOT NULL DEFAULT '',
    auto_created INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS character_visual_profiles (
    character_id TEXT PRIMARY KEY,
    base_visual_description TEXT NOT NULL DEFAULT '',
    face_description TEXT NOT NULL DEFAULT '',
    hair TEXT NOT NULL DEFAULT '',
    body_build TEXT NOT NULL DEFAULT '',
    age_marker TEXT NOT NULL DEFAULT '',
    default_outfit TEXT NOT NULL DEFAULT '',
    distinctive_marks TEXT NOT NULL DEFAULT '',
    color_palette TEXT NOT NULL DEFAULT '',
    negative_prompt TEXT NOT NULL DEFAULT '',
    z_image_lora_trigger TEXT NOT NULL DEFAULT '',
    alternate_lora_triggers TEXT NOT NULL DEFAULT '',
    preferred_voice TEXT NOT NULL DEFAULT '',
    visual_consistency_notes TEXT NOT NULL DEFAULT '',
    used_in_image_prompts INTEGER NOT NULL DEFAULT 1,
    auto_created_confidence REAL NOT NULL DEFAULT 0,
    source_scene_id TEXT,
    source_version_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS character_reference_images (
    id TEXT PRIMARY KEY,
    character_id TEXT NOT NULL,
    filename TEXT NOT NULL DEFAULT '',
    image_path TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    is_primary INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS session_characters (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE,
    UNIQUE(session_id, character_id)
);

CREATE TABLE IF NOT EXISTS world_notes (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    setting TEXT NOT NULL DEFAULT '',
    tone TEXT NOT NULL DEFAULT '',
    rules TEXT NOT NULL DEFAULT '',
    locations TEXT NOT NULL DEFAULT '',
    factions TEXT NOT NULL DEFAULT '',
    conflicts TEXT NOT NULL DEFAULT '',
    history TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    UNIQUE(session_id)
);

CREATE TABLE IF NOT EXISTS story_foundations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE,
    schema_version TEXT NOT NULL DEFAULT 'story_foundation_v1',
    status TEXT NOT NULL DEFAULT 'draft',
    source_director_note TEXT NOT NULL DEFAULT '',
    foundation_json TEXT NOT NULL DEFAULT '{}',
    source_map_json TEXT NOT NULL DEFAULT '{}',
    locked_paths_json TEXT NOT NULL DEFAULT '[]',
    generation_model TEXT NOT NULL DEFAULT '',
    generation_task_type TEXT NOT NULL DEFAULT '',
    generation_error TEXT,
    foundation_revision INTEGER NOT NULL DEFAULT 1,
    source_kind TEXT NOT NULL DEFAULT 'unknown',
    source_generation_id TEXT NOT NULL DEFAULT '',
    source_opening_note_checksum TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    refreshed_at TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scene_versions (
    id TEXT PRIMARY KEY,
    scene_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    director_note TEXT NOT NULL DEFAULT '',
    generated_text TEXT NOT NULL DEFAULT '',
    generation_stats_json TEXT NOT NULL DEFAULT '{}',
    mode TEXT NOT NULL DEFAULT 'continue',
    version_index INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    UNIQUE(scene_id, version_index)
);

CREATE TABLE IF NOT EXISTS session_summaries (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    summary_text TEXT NOT NULL DEFAULT '',
    from_scene_id TEXT,
    to_scene_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (from_scene_id) REFERENCES scenes(id) ON DELETE SET NULL,
    FOREIGN KEY (to_scene_id) REFERENCES scenes(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS story_memories (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    character_id TEXT,
    memory_type TEXT NOT NULL DEFAULT 'note',
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    importance INTEGER NOT NULL DEFAULT 1,
    keywords_json TEXT NOT NULL DEFAULT '[]',
    source_scene_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE SET NULL,
    FOREIGN KEY (source_scene_id) REFERENCES scenes(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS generated_images (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    scene_id TEXT NOT NULL,
    version_id TEXT,
    workflow_id TEXT NOT NULL,
    workflow_name TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL DEFAULT '',
    negative_prompt TEXT NOT NULL DEFAULT '',
    seed INTEGER,
    image_path TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    is_primary INTEGER NOT NULL DEFAULT 0,
    characters_included_json TEXT NOT NULL DEFAULT '[]',
    continuity_used_json TEXT NOT NULL DEFAULT '{}',
    reference_images_used_json TEXT NOT NULL DEFAULT '[]',
    live_state_used_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS session_image_settings (
    session_id TEXT PRIMARY KEY,
    selected_workflow_id TEXT,
    image_resource_mode TEXT,
    image_prompt_style TEXT NOT NULL DEFAULT '',
    preferred_visual_tone TEXT NOT NULL DEFAULT '',
    realism_notes TEXT NOT NULL DEFAULT '',
    lighting_camera_notes TEXT NOT NULL DEFAULT '',
    auto_open_preview INTEGER NOT NULL DEFAULT 1,
    auto_attach_generated INTEGER NOT NULL DEFAULT 1,
    generate_image_while_narrating INTEGER,
    auto_image_on_narrate INTEGER,
    default_negative_prompt TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scene_image_settings (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    scene_id TEXT NOT NULL,
    version_id TEXT NOT NULL DEFAULT '',
    selected_workflow_id TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(session_id, scene_id, version_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scene_image_prompts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    scene_id TEXT NOT NULL,
    version_id TEXT,
    workflow_id TEXT NOT NULL DEFAULT '',
    workflow_name TEXT NOT NULL DEFAULT '',
    visual_beat TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL DEFAULT '',
    negative_prompt TEXT NOT NULL DEFAULT '',
    characters_included_json TEXT NOT NULL DEFAULT '[]',
    continuity_used_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL DEFAULT 0,
    style_prompt TEXT NOT NULL DEFAULT '',
    workflow_notes TEXT NOT NULL DEFAULT '',
    source_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ready',
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS session_quality_notes (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    scene_id TEXT,
    version_id TEXT,
    note_type TEXT NOT NULL DEFAULT 'other',
    severity TEXT NOT NULL DEFAULT 'medium',
    status TEXT NOT NULL DEFAULT 'open',
    note_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS scene_quality_checklists (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    scene_id TEXT NOT NULL,
    version_id TEXT NOT NULL DEFAULT '',
    director_note_followed INTEGER,
    length_okay INTEGER,
    no_assistant_tone INTEGER,
    continuity_okay INTEGER,
    state_extracted_okay INTEGER,
    image_prompt_okay INTEGER,
    narration_okay INTEGER,
    image_generation_okay INTEGER,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(session_id, scene_id, version_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS story_state_runs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    scene_id TEXT NOT NULL,
    version_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    started_at TEXT,
    completed_at TEXT,
    error TEXT,
    raw_response TEXT NOT NULL DEFAULT '',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS character_live_state (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    character_id TEXT,
    character_name TEXT NOT NULL DEFAULT '',
    state_type TEXT NOT NULL DEFAULT 'general',
    key TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    previous_value TEXT,
    confidence REAL NOT NULL DEFAULT 0,
    is_tentative INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    manual_override INTEGER NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'ok',
    source_scene_id TEXT,
    source_version_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE SET NULL,
    FOREIGN KEY (source_scene_id) REFERENCES scenes(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS character_state_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    character_id TEXT,
    character_name TEXT NOT NULL DEFAULT '',
    state_type TEXT NOT NULL DEFAULT 'general',
    key TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    previous_value TEXT,
    action TEXT NOT NULL DEFAULT 'update',
    confidence REAL NOT NULL DEFAULT 0,
    run_id TEXT,
    source_scene_id TEXT,
    source_version_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE SET NULL,
    FOREIGN KEY (run_id) REFERENCES story_state_runs(id) ON DELETE SET NULL,
    FOREIGN KEY (source_scene_id) REFERENCES scenes(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS relationship_state (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    character_a_id TEXT,
    character_a_name TEXT NOT NULL DEFAULT '',
    character_b_id TEXT,
    character_b_name TEXT NOT NULL DEFAULT '',
    relationship_type TEXT NOT NULL DEFAULT 'unknown',
    relationship_key TEXT NOT NULL DEFAULT 'relationship',
    content TEXT NOT NULL DEFAULT '',
    previous_content TEXT,
    closeness_weight REAL NOT NULL DEFAULT 0,
    trust_weight REAL NOT NULL DEFAULT 0,
    conflict_weight REAL NOT NULL DEFAULT 0,
    protective_weight REAL NOT NULL DEFAULT 0,
    grief_weight REAL NOT NULL DEFAULT 0,
    romantic_weight REAL NOT NULL DEFAULT 0,
    family_weight REAL NOT NULL DEFAULT 0,
    betrayal_weight REAL NOT NULL DEFAULT 0,
    respect_weight REAL NOT NULL DEFAULT 0,
    fear_weight REAL NOT NULL DEFAULT 0,
    emotional_importance REAL NOT NULL DEFAULT 0,
    last_reinforced_scene_id TEXT,
    confidence REAL NOT NULL DEFAULT 0,
    is_tentative INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    manual_override INTEGER NOT NULL DEFAULT 0,
    manually_pinned INTEGER NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'ok',
    source_scene_id TEXT,
    source_version_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (character_a_id) REFERENCES characters(id) ON DELETE SET NULL,
    FOREIGN KEY (character_b_id) REFERENCES characters(id) ON DELETE SET NULL,
    FOREIGN KEY (source_scene_id) REFERENCES scenes(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS relationship_state_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    character_a_name TEXT NOT NULL DEFAULT '',
    character_b_name TEXT NOT NULL DEFAULT '',
    relationship_type TEXT NOT NULL DEFAULT 'unknown',
    relationship_key TEXT NOT NULL DEFAULT 'relationship',
    content TEXT NOT NULL DEFAULT '',
    previous_content TEXT,
    confidence REAL NOT NULL DEFAULT 0,
    run_id TEXT,
    source_scene_id TEXT,
    source_version_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (run_id) REFERENCES story_state_runs(id) ON DELETE SET NULL,
    FOREIGN KEY (source_scene_id) REFERENCES scenes(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS emotional_memories (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    memory_type TEXT NOT NULL DEFAULT 'emotional',
    memory_text TEXT NOT NULL DEFAULT '',
    emotional_weight REAL NOT NULL DEFAULT 0,
    themes_json TEXT NOT NULL DEFAULT '[]',
    related_characters_json TEXT NOT NULL DEFAULT '[]',
    trigger_conditions TEXT NOT NULL DEFAULT '',
    last_used_in_prompt TEXT,
    use_count INTEGER NOT NULL DEFAULT 0,
    cooldown_scenes INTEGER NOT NULL DEFAULT 3,
    confidence REAL NOT NULL DEFAULT 0,
    is_tentative INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    manual_override INTEGER NOT NULL DEFAULT 0,
    manually_pinned INTEGER NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'ok',
    run_id TEXT,
    source_scene_id TEXT,
    source_version_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (run_id) REFERENCES story_state_runs(id) ON DELETE SET NULL,
    FOREIGN KEY (source_scene_id) REFERENCES scenes(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS world_live_state (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    state_type TEXT NOT NULL DEFAULT 'world',
    key TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    previous_value TEXT,
    confidence REAL NOT NULL DEFAULT 0,
    is_tentative INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    manual_override INTEGER NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'ok',
    source_scene_id TEXT,
    source_version_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (source_scene_id) REFERENCES scenes(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS scene_live_state (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    scene_id TEXT NOT NULL,
    version_id TEXT,
    state_type TEXT NOT NULL DEFAULT 'scene',
    key TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    previous_value TEXT,
    confidence REAL NOT NULL DEFAULT 0,
    is_tentative INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    manual_override INTEGER NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'ok',
    source_scene_id TEXT,
    source_version_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE CASCADE,
    FOREIGN KEY (source_scene_id) REFERENCES scenes(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS object_state (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    object_key TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    state_type TEXT NOT NULL DEFAULT 'object',
    value TEXT NOT NULL DEFAULT '',
    previous_value TEXT,
    owner_character_id TEXT,
    owner_character_name TEXT NOT NULL DEFAULT '',
    holder_character_id TEXT,
    holder_character_name TEXT NOT NULL DEFAULT '',
    current_location TEXT NOT NULL DEFAULT '',
    placement_state TEXT NOT NULL DEFAULT 'unknown',
    condition TEXT NOT NULL DEFAULT '',
    visibility TEXT NOT NULL DEFAULT 'unknown',
    importance REAL NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    is_tentative INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    manual_override INTEGER NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'ok',
    source_scene_id TEXT,
    source_version_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (owner_character_id) REFERENCES characters(id) ON DELETE SET NULL,
    FOREIGN KEY (source_scene_id) REFERENCES scenes(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS plot_threads (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    thread_key TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    content TEXT NOT NULL DEFAULT '',
    previous_content TEXT,
    confidence REAL NOT NULL DEFAULT 0,
    is_tentative INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    manual_override INTEGER NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'ok',
    source_scene_id TEXT,
    source_version_id TEXT,
    resolved_scene_id TEXT,
    resolved_version_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (source_scene_id) REFERENCES scenes(id) ON DELETE SET NULL,
    FOREIGN KEY (resolved_scene_id) REFERENCES scenes(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS state_snapshots (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    run_id TEXT,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (run_id) REFERENCES story_state_runs(id) ON DELETE SET NULL
);

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

CREATE TABLE IF NOT EXISTS generation_runs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    scene_id TEXT NOT NULL,
    version_id TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL CHECK(mode IN ('continue', 'regenerate', 'rewrite', 'revise')),
    task_profile TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'completed' CHECK(status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    stage_timings_json TEXT NOT NULL DEFAULT '{}',
    prompt_chars INTEGER NOT NULL DEFAULT 0 CHECK(prompt_chars >= 0),
    output_chars INTEGER NOT NULL DEFAULT 0 CHECK(output_chars >= 0),
    word_count INTEGER NOT NULL DEFAULT 0 CHECK(word_count >= 0),
    repair_ran INTEGER NOT NULL DEFAULT 0 CHECK(repair_ran IN (0, 1)),
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE CASCADE,
    FOREIGN KEY (version_id) REFERENCES scene_versions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS narration_jobs (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    scene_id TEXT,
    version_id TEXT,
    provider TEXT NOT NULL DEFAULT 'kokoro',
    voice_profile_id TEXT NOT NULL DEFAULT '',
    requested_provider TEXT NOT NULL DEFAULT '',
    requested_profile TEXT NOT NULL DEFAULT '',
    requested_voice TEXT NOT NULL DEFAULT '',
    effective_provider TEXT NOT NULL DEFAULT '',
    effective_profile TEXT NOT NULL DEFAULT '',
    effective_voice TEXT NOT NULL DEFAULT '',
    effective_model TEXT NOT NULL DEFAULT '',
    fallback_used INTEGER NOT NULL DEFAULT 0 CHECK(fallback_used IN (0, 1)),
    fallback_reason TEXT NOT NULL DEFAULT '',
    prose_checksum TEXT NOT NULL DEFAULT '',
    pronunciation_revision TEXT NOT NULL DEFAULT '',
    prosody_revision TEXT NOT NULL DEFAULT '',
    breathing_revision TEXT NOT NULL DEFAULT '',
    custom_voice_revision INTEGER,
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued', 'running', 'ready', 'playing', 'paused', 'completed', 'failed', 'cancelled')),
    cursor_json TEXT NOT NULL DEFAULT '{}',
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE CASCADE,
    FOREIGN KEY (version_id) REFERENCES scene_versions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS narration_chunks (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL CHECK(chunk_index >= 0),
    text_hash TEXT NOT NULL CHECK(length(text_hash) <= 128),
    cache_key TEXT NOT NULL DEFAULT '' CHECK(length(cache_key) <= 128),
    audio_path TEXT NOT NULL DEFAULT '',
    duration_seconds REAL,
    synthesis_seconds REAL,
    text_start_offset INTEGER,
    text_end_offset INTEGER,
    requested_provider TEXT NOT NULL DEFAULT '',
    requested_profile TEXT NOT NULL DEFAULT '',
    requested_voice TEXT NOT NULL DEFAULT '',
    effective_provider TEXT NOT NULL DEFAULT '',
    effective_profile TEXT NOT NULL DEFAULT '',
    effective_voice TEXT NOT NULL DEFAULT '',
    fallback_used INTEGER NOT NULL DEFAULT 0 CHECK(fallback_used IN (0, 1)),
    fallback_reason TEXT NOT NULL DEFAULT '',
    custom_voice_id TEXT NOT NULL DEFAULT '',
    voice_prompt_revision INTEGER,
    style_reference TEXT NOT NULL DEFAULT 'normal',
    narration_style TEXT NOT NULL DEFAULT 'normal',
    narration_emotion TEXT NOT NULL DEFAULT 'neutral',
    narration_intensity REAL NOT NULL DEFAULT 0.25,
    narration_intensity_bucket TEXT NOT NULL DEFAULT 'low',
    pause_before_ms INTEGER NOT NULL DEFAULT 0,
    pause_after_ms INTEGER NOT NULL DEFAULT 0,
    source_cue TEXT NOT NULL DEFAULT '',
    generation_instruction TEXT NOT NULL DEFAULT '',
    breathing_mode TEXT NOT NULL DEFAULT 'natural',
    breath_before TEXT NOT NULL DEFAULT '',
    breath_after TEXT NOT NULL DEFAULT '',
    breath_confidence REAL NOT NULL DEFAULT 0,
    breath_source_cue TEXT NOT NULL DEFAULT '',
    breath_reference_checksum TEXT NOT NULL DEFAULT '',
    breath_reference_revision INTEGER,
    breath_audio_path TEXT NOT NULL DEFAULT '',
    breath_duration_seconds REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ready' CHECK(status IN ('queued', 'running', 'ready', 'failed', 'cancelled')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(job_id, chunk_index),
    FOREIGN KEY (job_id) REFERENCES narration_jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS custom_voices (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL COLLATE NOCASE,
    provider TEXT NOT NULL DEFAULT 'high_quality_local',
    model TEXT NOT NULL DEFAULT 'Qwen3-TTS-12Hz-0.6B-Base',
    language TEXT NOT NULL DEFAULT 'English',
    authorization_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(authorization_confirmed IN (0, 1)),
    dominant_speaker_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(dominant_speaker_confirmed IN (0, 1)),
    normal_reference_path TEXT NOT NULL DEFAULT '',
    normal_transcript TEXT NOT NULL DEFAULT '',
    soft_reference_path TEXT NOT NULL DEFAULT '',
    soft_transcript TEXT NOT NULL DEFAULT '',
    whisper_reference_path TEXT NOT NULL DEFAULT '',
    whisper_transcript TEXT NOT NULL DEFAULT '',
    heightened_reference_path TEXT NOT NULL DEFAULT '',
    heightened_transcript TEXT NOT NULL DEFAULT '',
    normalized_audio_checksum TEXT NOT NULL DEFAULT '',
    cached_voice_prompt_path TEXT NOT NULL DEFAULT '',
    cached_voice_prompt_checksum TEXT NOT NULL DEFAULT '',
    prompt_paths_json TEXT NOT NULL DEFAULT '{}',
    prompt_checksums_json TEXT NOT NULL DEFAULT '{}',
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    preview_path TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    last_used TEXT,
    user_notes TEXT NOT NULL DEFAULT '',
    validation_status TEXT NOT NULL DEFAULT 'pending',
    validation_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(display_name)
);

CREATE TABLE IF NOT EXISTS custom_voice_breaths (
    voice_id TEXT NOT NULL,
    breath_type TEXT NOT NULL CHECK(breath_type IN (
        'soft_inhale', 'normal_inhale', 'shaky_inhale', 'quiet_exhale', 'recovering_breath', 'gasp'
    )),
    audio_path TEXT NOT NULL,
    checksum TEXT NOT NULL CHECK(length(checksum) <= 128),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    duration_seconds REAL NOT NULL CHECK(duration_seconds > 0 AND duration_seconds <= 2.5),
    sample_rate INTEGER NOT NULL DEFAULT 24000,
    channels INTEGER NOT NULL DEFAULT 1,
    normalization_version TEXT NOT NULL DEFAULT 'storydriver-breath-v1',
    authorization_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(authorization_confirmed IN (0, 1)),
    isolated_breath_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(isolated_breath_confirmed IN (0, 1)),
    validation_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (voice_id, breath_type),
    FOREIGN KEY (voice_id) REFERENCES custom_voices(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pronunciation_aliases (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    written_form TEXT NOT NULL CHECK(length(written_form) BETWEEN 1 AND 120),
    spoken_form TEXT NOT NULL CHECK(length(spoken_form) BETWEEN 1 AND 160),
    source TEXT NOT NULL DEFAULT 'manual',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(session_id, written_form),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS model_presets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    system_prompt TEXT NOT NULL DEFAULT '',
    settings_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TRIGGER IF NOT EXISTS sessions_set_updated_at
AFTER UPDATE ON sessions
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE sessions
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS app_settings_set_updated_at
AFTER UPDATE ON app_settings
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE app_settings
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE key = OLD.key;
END;

CREATE TRIGGER IF NOT EXISTS model_presets_set_updated_at
AFTER UPDATE ON model_presets
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE model_presets
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS scenes_set_updated_at
AFTER UPDATE ON scenes
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE scenes
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE INDEX IF NOT EXISTS idx_scene_versions_scene_id ON scene_versions(scene_id);
CREATE INDEX IF NOT EXISTS idx_scenes_session_id ON scenes(session_id);
CREATE INDEX IF NOT EXISTS idx_scene_versions_session_id ON scene_versions(session_id);
CREATE INDEX IF NOT EXISTS idx_character_visual_profiles_character_id ON character_visual_profiles(character_id);
CREATE INDEX IF NOT EXISTS idx_character_reference_images_character_id
ON character_reference_images(character_id, archived, is_primary);
CREATE INDEX IF NOT EXISTS idx_session_characters_session_id ON session_characters(session_id);
CREATE INDEX IF NOT EXISTS idx_session_characters_character_id ON session_characters(character_id);
CREATE INDEX IF NOT EXISTS idx_world_notes_session_id ON world_notes(session_id);
CREATE INDEX IF NOT EXISTS idx_story_foundations_session_id ON story_foundations(session_id);
CREATE INDEX IF NOT EXISTS idx_story_memories_session_id ON story_memories(session_id);
CREATE INDEX IF NOT EXISTS idx_session_summaries_session_id ON session_summaries(session_id);
CREATE INDEX IF NOT EXISTS idx_generated_images_scene_id ON generated_images(scene_id);
CREATE INDEX IF NOT EXISTS idx_generated_images_session_id ON generated_images(session_id);
CREATE INDEX IF NOT EXISTS idx_generated_images_version_id ON generated_images(version_id);
CREATE INDEX IF NOT EXISTS idx_scene_image_prompts_scene_version
ON scene_image_prompts(session_id, scene_id, version_id, workflow_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_scene_image_settings_scene_version
ON scene_image_settings(session_id, scene_id, version_id);
CREATE INDEX IF NOT EXISTS idx_session_quality_notes_session
ON session_quality_notes(session_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_session_quality_notes_target
ON session_quality_notes(session_id, scene_id, version_id);
CREATE INDEX IF NOT EXISTS idx_scene_quality_checklists_target
ON scene_quality_checklists(session_id, scene_id, version_id);
CREATE INDEX IF NOT EXISTS idx_story_state_runs_session ON story_state_runs(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_story_state_runs_source ON story_state_runs(scene_id, version_id);
CREATE INDEX IF NOT EXISTS idx_character_live_state_session ON character_live_state(session_id, character_id, archived);
CREATE INDEX IF NOT EXISTS idx_character_state_events_session ON character_state_events(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_relationship_state_session ON relationship_state(session_id, archived);
CREATE INDEX IF NOT EXISTS idx_emotional_memories_session ON emotional_memories(session_id, archived, disabled);
CREATE INDEX IF NOT EXISTS idx_emotional_memories_run ON emotional_memories(run_id);
CREATE INDEX IF NOT EXISTS idx_world_live_state_session ON world_live_state(session_id, archived);
CREATE INDEX IF NOT EXISTS idx_scene_live_state_session ON scene_live_state(session_id, scene_id, version_id, archived);
CREATE INDEX IF NOT EXISTS idx_object_state_session ON object_state(session_id, archived);
CREATE INDEX IF NOT EXISTS idx_plot_threads_session ON plot_threads(session_id, status);
CREATE INDEX IF NOT EXISTS idx_state_snapshots_session ON state_snapshots(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_next_prompt_memory_cache_v3_session
ON next_prompt_memory_cache_v3(session_id, context_kind, updated_at);
CREATE INDEX IF NOT EXISTS idx_generation_runs_story ON generation_runs(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_generation_runs_scene ON generation_runs(scene_id, version_id);
CREATE INDEX IF NOT EXISTS idx_narration_jobs_story ON narration_jobs(session_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_narration_jobs_target
ON narration_jobs(session_id, scene_id, version_id, requested_provider, requested_voice, updated_at);
CREATE INDEX IF NOT EXISTS idx_narration_chunks_job ON narration_chunks(job_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_narration_chunks_provider_voice
ON narration_chunks(job_id, requested_provider, requested_voice, chunk_index);
CREATE INDEX IF NOT EXISTS idx_custom_voices_enabled ON custom_voices(enabled, display_name);
CREATE INDEX IF NOT EXISTS idx_custom_voice_breaths_voice ON custom_voice_breaths(voice_id, breath_type);
CREATE INDEX IF NOT EXISTS idx_pronunciation_aliases_story ON pronunciation_aliases(session_id, enabled);

CREATE TRIGGER IF NOT EXISTS characters_set_updated_at
AFTER UPDATE ON characters
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE characters
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS character_visual_profiles_set_updated_at
AFTER UPDATE ON character_visual_profiles
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE character_visual_profiles
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE character_id = OLD.character_id;
END;

CREATE TRIGGER IF NOT EXISTS character_reference_images_set_updated_at
AFTER UPDATE ON character_reference_images
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE character_reference_images
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS session_characters_set_updated_at
AFTER UPDATE ON session_characters
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE session_characters
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS world_notes_set_updated_at
AFTER UPDATE ON world_notes
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE world_notes
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS story_foundations_set_updated_at
AFTER UPDATE ON story_foundations
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE story_foundations
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS session_summaries_set_updated_at
AFTER UPDATE ON session_summaries
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE session_summaries
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS story_memories_set_updated_at
AFTER UPDATE ON story_memories
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE story_memories
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS generated_images_set_updated_at
AFTER UPDATE ON generated_images
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE generated_images
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS session_image_settings_set_updated_at
AFTER UPDATE ON session_image_settings
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE session_image_settings
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE session_id = OLD.session_id;
END;

CREATE TRIGGER IF NOT EXISTS scene_image_settings_set_updated_at
AFTER UPDATE ON scene_image_settings
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE scene_image_settings
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS scene_image_prompts_set_updated_at
AFTER UPDATE ON scene_image_prompts
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE scene_image_prompts
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS story_state_runs_set_updated_at
AFTER UPDATE ON story_state_runs
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE story_state_runs
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS session_quality_notes_set_updated_at
AFTER UPDATE ON session_quality_notes
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE session_quality_notes
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS scene_quality_checklists_set_updated_at
AFTER UPDATE ON scene_quality_checklists
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE scene_quality_checklists
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS character_live_state_set_updated_at
AFTER UPDATE ON character_live_state
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE character_live_state
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS relationship_state_set_updated_at
AFTER UPDATE ON relationship_state
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE relationship_state
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS emotional_memories_set_updated_at
AFTER UPDATE ON emotional_memories
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE emotional_memories
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS world_live_state_set_updated_at
AFTER UPDATE ON world_live_state
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE world_live_state
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS scene_live_state_set_updated_at
AFTER UPDATE ON scene_live_state
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE scene_live_state
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS object_state_set_updated_at
AFTER UPDATE ON object_state
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE object_state
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS plot_threads_set_updated_at
AFTER UPDATE ON plot_threads
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE plot_threads
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;
"""


MIGRATION_SQL = """
PRAGMA foreign_keys = OFF;

ALTER TABLE scenes ADD COLUMN mode TEXT NOT NULL DEFAULT 'continue';
ALTER TABLE scenes ADD COLUMN updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

PRAGMA foreign_keys = ON;
"""


def get_connection() -> sqlite3.Connection:
    ensure_runtime_paths()
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 15000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def db_session() -> Iterator[sqlite3.Connection]:
    connection = get_connection()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    ensure_runtime_paths()
    with closing(get_connection()) as connection, connection:
        connection.executescript(SCHEMA_SQL)
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (4, 'complete_storydriver_revamp')"
        )
        narration_job_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(narration_jobs)").fetchall()
        }
        for column_name, column_sql in (
            ("requested_provider", "TEXT NOT NULL DEFAULT ''"),
            ("requested_profile", "TEXT NOT NULL DEFAULT ''"),
            ("requested_voice", "TEXT NOT NULL DEFAULT ''"),
            ("effective_provider", "TEXT NOT NULL DEFAULT ''"),
            ("effective_profile", "TEXT NOT NULL DEFAULT ''"),
            ("effective_voice", "TEXT NOT NULL DEFAULT ''"),
            ("effective_model", "TEXT NOT NULL DEFAULT ''"),
            ("fallback_used", "INTEGER NOT NULL DEFAULT 0 CHECK(fallback_used IN (0, 1))"),
            ("fallback_reason", "TEXT NOT NULL DEFAULT ''"),
            ("prose_checksum", "TEXT NOT NULL DEFAULT ''"),
            ("pronunciation_revision", "TEXT NOT NULL DEFAULT ''"),
            ("prosody_revision", "TEXT NOT NULL DEFAULT ''"),
            ("breathing_revision", "TEXT NOT NULL DEFAULT ''"),
            ("custom_voice_revision", "INTEGER"),
        ):
            if column_name not in narration_job_columns:
                connection.execute(f"ALTER TABLE narration_jobs ADD COLUMN {column_name} {column_sql}")
        narration_chunk_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(narration_chunks)").fetchall()
        }
        for column_name, column_sql in (
            ("requested_provider", "TEXT NOT NULL DEFAULT ''"),
            ("requested_profile", "TEXT NOT NULL DEFAULT ''"),
            ("requested_voice", "TEXT NOT NULL DEFAULT ''"),
            ("effective_provider", "TEXT NOT NULL DEFAULT ''"),
            ("effective_profile", "TEXT NOT NULL DEFAULT ''"),
            ("effective_voice", "TEXT NOT NULL DEFAULT ''"),
            ("fallback_used", "INTEGER NOT NULL DEFAULT 0 CHECK(fallback_used IN (0, 1))"),
            ("fallback_reason", "TEXT NOT NULL DEFAULT ''"),
            ("custom_voice_id", "TEXT NOT NULL DEFAULT ''"),
            ("voice_prompt_revision", "INTEGER"),
            ("style_reference", "TEXT NOT NULL DEFAULT 'normal'"),
            ("narration_style", "TEXT NOT NULL DEFAULT 'normal'"),
            ("narration_emotion", "TEXT NOT NULL DEFAULT 'neutral'"),
            ("narration_intensity", "REAL NOT NULL DEFAULT 0.25"),
            ("narration_intensity_bucket", "TEXT NOT NULL DEFAULT 'low'"),
            ("pause_before_ms", "INTEGER NOT NULL DEFAULT 0"),
            ("pause_after_ms", "INTEGER NOT NULL DEFAULT 0"),
            ("source_cue", "TEXT NOT NULL DEFAULT ''"),
            ("generation_instruction", "TEXT NOT NULL DEFAULT ''"),
            ("breathing_mode", "TEXT NOT NULL DEFAULT 'natural'"),
            ("breath_before", "TEXT NOT NULL DEFAULT ''"),
            ("breath_after", "TEXT NOT NULL DEFAULT ''"),
            ("breath_confidence", "REAL NOT NULL DEFAULT 0"),
            ("breath_source_cue", "TEXT NOT NULL DEFAULT ''"),
            ("breath_reference_checksum", "TEXT NOT NULL DEFAULT ''"),
            ("breath_reference_revision", "INTEGER"),
            ("breath_audio_path", "TEXT NOT NULL DEFAULT ''"),
            ("breath_duration_seconds", "REAL NOT NULL DEFAULT 0"),
        ):
            if column_name not in narration_chunk_columns:
                connection.execute(f"ALTER TABLE narration_chunks ADD COLUMN {column_name} {column_sql}")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_narration_chunks_custom_voice "
            "ON narration_chunks(custom_voice_id, created_at)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (5, 'qwen_custom_voice_and_chunk_truth')"
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (6, 'automatic_narration_direction')"
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (7, 'automatic_breath_performance')"
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(scenes)").fetchall()}
        if "mode" not in columns:
            connection.execute("ALTER TABLE scenes ADD COLUMN mode TEXT NOT NULL DEFAULT 'continue'")
        if "updated_at" not in columns:
            connection.execute("ALTER TABLE scenes ADD COLUMN updated_at TEXT")
            connection.execute(
                """
                UPDATE scenes
                SET updated_at = COALESCE(updated_at, created_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
        else:
            connection.execute(
                """
                UPDATE scenes
                SET updated_at = COALESCE(created_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                WHERE updated_at IS NULL
                """
            )
        if "generation_stats_json" not in columns:
            connection.execute("ALTER TABLE scenes ADD COLUMN generation_stats_json TEXT NOT NULL DEFAULT '{}'")
        session_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if "archived_at" not in session_columns:
            connection.execute("ALTER TABLE sessions ADD COLUMN archived_at TEXT")
        for column_name, column_sql in (
            ("title_source", "TEXT NOT NULL DEFAULT 'placeholder'"),
            ("auto_title_status", "TEXT NOT NULL DEFAULT 'skipped'"),
            ("auto_title_error", "TEXT"),
            ("last_auto_title_attempt_at", "TEXT"),
            ("auto_title_job_id", "TEXT"),
            ("auto_title_scene_id", "TEXT"),
            ("auto_title_version_id", "TEXT"),
            ("deletion_status", "TEXT NOT NULL DEFAULT ''"),
            ("deletion_job_id", "TEXT"),
            ("deletion_started_at", "TEXT"),
            ("deletion_finished_at", "TEXT"),
            ("deletion_error", "TEXT"),
        ):
            if column_name not in session_columns:
                connection.execute(f"ALTER TABLE sessions ADD COLUMN {column_name} {column_sql}")
        foundation_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(story_foundations)").fetchall()
        }
        for column_name, column_sql in (
            ("foundation_revision", "INTEGER NOT NULL DEFAULT 1"),
            ("source_kind", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("source_generation_id", "TEXT NOT NULL DEFAULT ''"),
            ("source_opening_note_checksum", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column_name not in foundation_columns:
                connection.execute(f"ALTER TABLE story_foundations ADD COLUMN {column_name} {column_sql}")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_scenes_session_id ON scenes(session_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_session_summaries_session_id ON session_summaries(session_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_story_foundations_session_id ON story_foundations(session_id)")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_story_foundations_provenance "
            "ON story_foundations(session_id, foundation_revision, source_generation_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_auto_title_job "
            "ON sessions(auto_title_status, auto_title_job_id, updated_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_narration_jobs_target "
            "ON narration_jobs(session_id, scene_id, version_id, requested_provider, requested_voice, updated_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_narration_chunks_provider_voice "
            "ON narration_chunks(job_id, requested_provider, requested_voice, chunk_index)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, name) "
            "VALUES (8, 'story_isolation_title_qwen_runtime')"
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_sessions_deletion_status ON sessions(deletion_status, updated_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_story_delete_jobs_session ON story_delete_jobs(session_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_story_delete_jobs_status ON story_delete_jobs(status, updated_at)")
        version_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(scene_versions)").fetchall()
        }
        if "generation_stats_json" not in version_columns:
            connection.execute("ALTER TABLE scene_versions ADD COLUMN generation_stats_json TEXT NOT NULL DEFAULT '{}'")
        memory_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(story_memories)").fetchall()
        }
        if "character_id" not in memory_columns:
            connection.execute("ALTER TABLE story_memories ADD COLUMN character_id TEXT")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_story_memories_character_id ON story_memories(character_id)"
        )
        character_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(characters)").fetchall()
        }
        if "auto_created" not in character_columns:
            connection.execute("ALTER TABLE characters ADD COLUMN auto_created INTEGER NOT NULL DEFAULT 0")
        image_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(generated_images)").fetchall()
        }
        if image_columns and "is_primary" not in image_columns:
            connection.execute("ALTER TABLE generated_images ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0")
        for column_name, column_sql in (
            ("characters_included_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("continuity_used_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("reference_images_used_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("live_state_used_json", "TEXT NOT NULL DEFAULT '{}'"),
        ):
            if image_columns and column_name not in image_columns:
                connection.execute(f"ALTER TABLE generated_images ADD COLUMN {column_name} {column_sql}")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_generated_images_primary ON generated_images(session_id, scene_id, version_id, is_primary)"
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_character_reference_images_character_id
            ON character_reference_images(character_id, archived, is_primary)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_scene_image_prompts_scene_version
            ON scene_image_prompts(session_id, scene_id, version_id, workflow_id, updated_at)
            """
        )
        session_image_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(session_image_settings)").fetchall()
        }
        for column_name in (
            "preferred_visual_tone",
            "realism_notes",
            "lighting_camera_notes",
        ):
            if column_name not in session_image_columns:
                connection.execute(
                    f"ALTER TABLE session_image_settings ADD COLUMN {column_name} TEXT NOT NULL DEFAULT ''"
                )
        for column_name, column_sql in (
            ("image_resource_mode", "TEXT"),
            ("generate_image_while_narrating", "INTEGER"),
            ("auto_image_on_narrate", "INTEGER"),
        ):
            if column_name not in session_image_columns:
                connection.execute(
                    f"ALTER TABLE session_image_settings ADD COLUMN {column_name} {column_sql}"
                )
        scene_prompt_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(scene_image_prompts)").fetchall()
        }
        if "continuity_used_json" not in scene_prompt_columns:
            connection.execute(
                "ALTER TABLE scene_image_prompts ADD COLUMN continuity_used_json TEXT NOT NULL DEFAULT '{}'"
            )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_character_visual_profiles_character_id ON character_visual_profiles(character_id)"
        )
        visual_profile_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(character_visual_profiles)").fetchall()
        }
        for column_name, column_sql in (
            ("auto_created_confidence", "REAL NOT NULL DEFAULT 0"),
            ("source_scene_id", "TEXT"),
            ("source_version_id", "TEXT"),
        ):
            if column_name not in visual_profile_columns:
                connection.execute(f"ALTER TABLE character_visual_profiles ADD COLUMN {column_name} {column_sql}")
        review_columns = [
            ("disabled", "INTEGER NOT NULL DEFAULT 0"),
            ("manual_override", "INTEGER NOT NULL DEFAULT 0"),
            ("review_status", "TEXT NOT NULL DEFAULT 'ok'"),
        ]
        for table in (
            "character_live_state",
            "relationship_state",
            "world_live_state",
            "scene_live_state",
            "object_state",
            "plot_threads",
        ):
            existing_columns = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for column_name, column_sql in review_columns:
                if column_name not in existing_columns:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column_name} {column_sql}")
        relationship_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(relationship_state)").fetchall()
        }
        for column_name, column_sql in (
            ("relationship_type", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("closeness_weight", "REAL NOT NULL DEFAULT 0"),
            ("trust_weight", "REAL NOT NULL DEFAULT 0"),
            ("conflict_weight", "REAL NOT NULL DEFAULT 0"),
            ("protective_weight", "REAL NOT NULL DEFAULT 0"),
            ("grief_weight", "REAL NOT NULL DEFAULT 0"),
            ("romantic_weight", "REAL NOT NULL DEFAULT 0"),
            ("family_weight", "REAL NOT NULL DEFAULT 0"),
            ("betrayal_weight", "REAL NOT NULL DEFAULT 0"),
            ("respect_weight", "REAL NOT NULL DEFAULT 0"),
            ("fear_weight", "REAL NOT NULL DEFAULT 0"),
            ("emotional_importance", "REAL NOT NULL DEFAULT 0"),
            ("last_reinforced_scene_id", "TEXT"),
            ("manually_pinned", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column_name not in relationship_columns:
                connection.execute(f"ALTER TABLE relationship_state ADD COLUMN {column_name} {column_sql}")
        object_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(object_state)").fetchall()
        }
        for column_name, column_sql in (
            ("holder_character_id", "TEXT"),
            ("holder_character_name", "TEXT NOT NULL DEFAULT ''"),
            ("current_location", "TEXT NOT NULL DEFAULT ''"),
            ("placement_state", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("condition", "TEXT NOT NULL DEFAULT ''"),
            ("visibility", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("importance", "REAL NOT NULL DEFAULT 0"),
        ):
            if column_name not in object_columns:
                connection.execute(f"ALTER TABLE object_state ADD COLUMN {column_name} {column_sql}")
        connection.execute(
            "UPDATE object_state SET holder_character_id = owner_character_id, holder_character_name = owner_character_name "
            "WHERE holder_character_name = '' AND lower(state_type) IN ('ownership', 'possession', 'held_by', 'carried_by')"
        )
        connection.execute(
            "UPDATE object_state SET current_location = value, placement_state = 'placed' "
            "WHERE current_location = '' AND lower(state_type) = 'location'"
        )
        for index_sql in (
            "CREATE INDEX IF NOT EXISTS idx_relationship_pair_active ON relationship_state(session_id, character_a_name, character_b_name, archived, disabled)",
            "CREATE INDEX IF NOT EXISTS idx_relationship_source_version ON relationship_state(session_id, source_scene_id, source_version_id)",
            "CREATE INDEX IF NOT EXISTS idx_relationship_salience ON relationship_state(session_id, emotional_importance DESC, confidence DESC, updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_relationship_events_source ON relationship_state_events(session_id, source_scene_id, source_version_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_character_state_active_key ON character_live_state(session_id, character_name, key, archived, disabled, updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_character_state_source_version ON character_live_state(session_id, source_scene_id, source_version_id)",
            "CREATE INDEX IF NOT EXISTS idx_scene_state_location_active ON scene_live_state(session_id, key, archived, disabled, updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_object_holder_active ON object_state(session_id, holder_character_name, archived, disabled, updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_object_source_version ON object_state(session_id, source_scene_id, source_version_id)",
            "CREATE INDEX IF NOT EXISTS idx_object_salience ON object_state(session_id, importance DESC, confidence DESC, updated_at DESC)",
        ):
            connection.execute(index_sql)
        connection.execute(
            """
            UPDATE relationship_state
            SET character_a_id = CASE WHEN lower(character_a_name) > lower(character_b_name) THEN character_b_id ELSE character_a_id END,
                character_b_id = CASE WHEN lower(character_a_name) > lower(character_b_name) THEN character_a_id ELSE character_b_id END,
                character_a_name = CASE WHEN lower(character_a_name) > lower(character_b_name) THEN character_b_name ELSE character_a_name END,
                character_b_name = CASE WHEN lower(character_a_name) > lower(character_b_name) THEN character_a_name ELSE character_b_name END
            WHERE lower(character_a_name) > lower(character_b_name)
            """
        )
        connection.execute(
            """
            UPDATE relationship_state
            SET archived = 1, review_status = 'superseded'
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY session_id, lower(character_a_name), lower(character_b_name)
                               ORDER BY manual_override DESC, manually_pinned DESC,
                                        emotional_importance DESC, confidence DESC, updated_at DESC, id DESC
                           ) AS row_number
                    FROM relationship_state
                    WHERE archived = 0 AND disabled = 0
                )
                WHERE row_number > 1
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_relationship_one_active_pair
            ON relationship_state(session_id, lower(character_a_name), lower(character_b_name))
            WHERE archived = 0 AND disabled = 0
            """
        )
        emotional_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(emotional_memories)").fetchall()
        }
        for column_name, column_sql in (
            ("memory_type", "TEXT NOT NULL DEFAULT 'emotional'"),
            ("memory_text", "TEXT NOT NULL DEFAULT ''"),
            ("emotional_weight", "REAL NOT NULL DEFAULT 0"),
            ("themes_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("related_characters_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("trigger_conditions", "TEXT NOT NULL DEFAULT ''"),
            ("last_used_in_prompt", "TEXT"),
            ("use_count", "INTEGER NOT NULL DEFAULT 0"),
            ("cooldown_scenes", "INTEGER NOT NULL DEFAULT 3"),
            ("confidence", "REAL NOT NULL DEFAULT 0"),
            ("is_tentative", "INTEGER NOT NULL DEFAULT 0"),
            ("archived", "INTEGER NOT NULL DEFAULT 0"),
            ("disabled", "INTEGER NOT NULL DEFAULT 0"),
            ("manual_override", "INTEGER NOT NULL DEFAULT 0"),
            ("manually_pinned", "INTEGER NOT NULL DEFAULT 0"),
            ("review_status", "TEXT NOT NULL DEFAULT 'ok'"),
            ("run_id", "TEXT"),
            ("source_scene_id", "TEXT"),
            ("source_version_id", "TEXT"),
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
        ):
            if column_name not in emotional_columns:
                connection.execute(f"ALTER TABLE emotional_memories ADD COLUMN {column_name} {column_sql}")
        connection.execute(
            """
            UPDATE emotional_memories
            SET created_at = COALESCE(created_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at = COALESCE(updated_at, created_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            WHERE created_at IS NULL OR updated_at IS NULL
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_emotional_memories_session ON emotional_memories(session_id, archived, disabled)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_emotional_memories_run ON emotional_memories(run_id)"
        )
        connection.execute(
            """
            UPDATE generated_images
            SET is_primary = 0
            WHERE status != 'accepted' AND is_primary != 0
            """
        )
        connection.execute(
            """
            UPDATE generated_images
            SET is_primary = 0
            WHERE id IN (
                SELECT id
                FROM (
                  SELECT
                    id,
                    ROW_NUMBER() OVER (
                      PARTITION BY session_id, scene_id, COALESCE(version_id, '')
                      ORDER BY updated_at DESC, created_at DESC, id DESC
                    ) AS row_number
                  FROM generated_images
                  WHERE status = 'accepted' AND is_primary = 1
                )
                WHERE row_number > 1
              )
            """
        )
        connection.execute(
            """
            UPDATE generated_images
            SET is_primary = 1
            WHERE status = 'accepted'
              AND id IN (
                SELECT id
                FROM (
                  SELECT
                    candidate.id,
                    ROW_NUMBER() OVER (
                      PARTITION BY candidate.session_id, candidate.scene_id, COALESCE(candidate.version_id, '')
                      ORDER BY candidate.created_at DESC, candidate.updated_at DESC, candidate.id DESC
                    ) AS row_number
                  FROM generated_images AS candidate
                  WHERE candidate.status = 'accepted'
                    AND NOT EXISTS (
                      SELECT 1
                      FROM generated_images AS existing
                      WHERE existing.session_id = candidate.session_id
                        AND existing.scene_id = candidate.scene_id
                        AND COALESCE(existing.version_id, '') = COALESCE(candidate.version_id, '')
                        AND existing.status = 'accepted'
                        AND existing.is_primary = 1
                    )
                )
                WHERE row_number = 1
              )
            """
        )
        connection.execute(
            """
            INSERT INTO scene_versions (
                id, scene_id, session_id, director_note, generated_text, generation_stats_json, mode, version_index, created_at
            )
            SELECT
                id || '-v1',
                id,
                session_id,
                director_note,
                generated_text,
                COALESCE(generation_stats_json, '{}'),
                mode,
                1,
                created_at
            FROM scenes
            WHERE NOT EXISTS (
                SELECT 1 FROM scene_versions WHERE scene_versions.scene_id = scenes.id
            )
            """
        )
