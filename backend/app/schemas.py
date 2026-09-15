from pydantic import BaseModel, Field, field_validator
from typing import Any
from typing import Literal

from app.diagnostics.privacy import validate_local_service_url


class HealthResponse(BaseModel):
    ok: bool
    app: str


class SessionCreate(BaseModel):
    title: str = Field(default="Untitled Story", min_length=1, max_length=120)


class SessionUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class SessionAutoTitleRequest(BaseModel):
    scene_text: str | None = Field(default=None, max_length=80000)
    director_note: str | None = Field(default=None, max_length=12000)


class SessionRead(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    archived_at: str | None = None
    title_source: str = "placeholder"
    auto_title_status: str = "skipped"
    auto_title_error: str | None = None
    last_auto_title_attempt_at: str | None = None
    auto_title_job_id: str | None = None
    auto_title_scene_id: str | None = None
    auto_title_version_id: str | None = None
    deletion_status: str = ""
    deletion_job_id: str | None = None
    deletion_started_at: str | None = None
    deletion_finished_at: str | None = None
    deletion_error: str | None = None


class SessionDeleteResponse(BaseModel):
    id: str
    title: str
    deleted_at: str
    permanent: bool = True
    status: str = "completed"
    job_id: str | None = None
    message: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    files_deleted: list[str] = Field(default_factory=list)
    files_skipped: list[str] = Field(default_factory=list)


class SessionDeleteJobRead(BaseModel):
    job_id: str
    session_id: str
    title: str
    permanent: bool = True
    status: str
    stage: str = "queued"
    message: str = ""
    created_at: str | None = None
    started_at: str
    finished_at: str | None = None
    updated_at: str | None = None
    error: str | None = None
    recoverable: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    deleted_counts: dict[str, int] = Field(default_factory=dict)
    files_deleted: list[str] = Field(default_factory=list)
    files_skipped: list[str] = Field(default_factory=list)
    deleted_file_bytes: int = 0
    current_table: str | None = None
    tables_completed: int = 0
    total_tables: int = 0
    rows_deleted: int = 0
    referenced_file_count: int = 0
    audit_path: str = ""


class GenerateSceneRequest(BaseModel):
    director_note: str = Field(default="", max_length=12000)
    mode: Literal["continue", "regenerate", "rewrite", "revise"] = "continue"
    target_scene_id: str | None = None
    client_submitted_at: str | None = Field(default=None, max_length=80)


class ProsePromptPreviewRequest(BaseModel):
    director_note: str = Field(default="", max_length=12000)
    mode: Literal["continue", "regenerate", "rewrite", "revise"] = "continue"
    target_scene_id: str | None = None
    system_prompt_override: str | None = None
    task_notes_override: str | None = Field(default=None, max_length=4000)
    prose_prompt_mode_override: Literal["standard", "direct"] | None = None
    writing_process_mode_override: Literal["fast_direct", "deliberate", "deep_chapter"] | None = None
    app_planning_enabled_override: bool | None = None
    chapter_extension_enabled_override: bool | None = None
    adherence_check_mode_override: Literal["off", "warn", "retry_once"] | None = None
    writing_length_mode_override: Literal["beat", "scene", "chapter", "custom"] | None = None
    custom_word_min_override: int | None = Field(default=None, ge=100, le=10000)
    custom_word_max_override: int | None = Field(default=None, ge=100, le=12000)
    max_tokens_override: int | None = Field(default=None, ge=1, le=64000)


class ProsePromptPreviewRead(BaseModel):
    session_id: str
    mode: str
    prompt_mode: str
    writing_path: str = "deliberate_pipeline"
    writing_process_mode: str = "deliberate"
    app_planning_enabled: bool = True
    chapter_extension_enabled: bool = True
    adherence_check_mode: str = "warn"
    scene_plan: dict[str, Any] | None = None
    genre_time_helper: dict[str, Any] = Field(default_factory=dict)
    system_prompt: str
    task_notes: str
    user_prompt: str
    director_note: str
    writing_length: dict[str, Any]
    parameters: dict[str, Any]
    prompt_diagnostics: dict[str, Any]
    task_profile: str
    task_label: str
    model: str = ""
    provider: str = "lm_studio"
    provider_url: str = ""
    lm_studio_url: str = ""
    inference_backend: str = "openai_compatible"
    reasoning_mode: str = "auto"
    uses_global_model: bool = True
    override_fields: list[str] = Field(default_factory=list)
    hidden_style_instructions: bool = False
    notes: list[str] = Field(default_factory=list)


class SceneVersionRead(BaseModel):
    id: str
    scene_id: str
    session_id: str
    director_note: str
    generated_text: str
    mode: str = "continue"
    version_index: int
    created_at: str
    generation_stats: dict[str, Any] = Field(default_factory=dict)


class SceneRead(BaseModel):
    id: str
    session_id: str
    director_note: str
    generated_text: str
    mode: str = "continue"
    created_at: str
    updated_at: str | None = None
    active_version_id: str | None = None
    active_version_index: int = 1
    version_count: int = 1
    versions: list[SceneVersionRead] = Field(default_factory=list)
    generation_stats: dict[str, Any] = Field(default_factory=dict)


class ModelRead(BaseModel):
    id: str
    name: str | None = None
    loaded: bool | None = None
    state: str = "available"
    source: str = "openai_compatible"


class ModelSettings(BaseModel):
    active_preset_id: str | None = None
    provider: Literal["llama_cpp", "openai_compatible", "lm_studio"] = "lm_studio"
    provider_url: str = "http://localhost:1234/v1"
    lm_studio_url: str = "http://localhost:1234/v1"
    model: str = ""
    model_path: str | None = Field(default=None, max_length=1200)
    allow_remote_provider: bool = False
    llama_context_length: int | None = Field(default=None, ge=2048, le=131072)
    llama_gpu_layers: int | None = Field(default=None, ge=-1, le=999)
    llama_threads: int | None = Field(default=None, ge=1, le=256)
    llama_batch_size: int | None = Field(default=None, ge=32, le=8192)
    llama_flash_attention: bool | None = None
    llama_parallel_slots: int | None = Field(default=None, ge=1, le=8)
    system_prompt: str
    temperature: float = Field(default=0.8, ge=0, le=2)
    top_p: float = Field(default=0.95, ge=0, le=1)
    max_tokens: int = Field(default=8000, ge=1)
    stop_strings: str = ""
    top_k: int = Field(default=40, ge=0)
    min_p: float = Field(default=0.05, ge=0, le=1)
    repeat_penalty: float = Field(default=1.1, ge=0, le=2)
    presence_penalty: float = Field(default=0, ge=-2, le=2)
    frequency_penalty: float = Field(default=0, ge=-2, le=2)
    seed: int | None = None
    streaming: bool = True
    writing_length_mode: Literal["beat", "scene", "chapter", "custom"] = "scene"
    custom_word_min: int | None = Field(default=None, ge=100, le=10000)
    custom_word_max: int | None = Field(default=None, ge=100, le=12000)
    prose_prompt_mode: Literal["standard", "direct"] = "standard"
    writing_process_mode: Literal["fast_direct", "deliberate", "deep_chapter"] = "deliberate"
    writing_path: Literal["direct_writer", "deliberate_pipeline"] = "deliberate_pipeline"
    app_planning_enabled: bool = True
    chapter_extension_enabled: bool = True
    adherence_check_mode: Literal["off", "warn", "retry_once"] = "warn"
    inference_backend: Literal["openai_compatible", "native_rest"] = "openai_compatible"
    reasoning_mode: Literal["auto", "off", "low", "medium", "high", "on"] = "auto"
    context_length: int | None = Field(default=None, ge=512, le=1048576)
    fallback_to_openai_compatible: bool = True

    @field_validator("lm_studio_url")
    @classmethod
    def validate_lm_studio_url(cls, value: str) -> str:
        return validate_local_service_url(value, "LM Studio URL")

    @field_validator("provider_url")
    @classmethod
    def validate_provider_url(cls, value: str) -> str:
        return validate_local_service_url(value, "Local model provider URL")


TaskModelType = Literal[
    "story_foundation_generation",
    "scene_planning",
    "prose_generation",
    "rewrite_revision",
    "scene_quality_review",
    "image_prompt_generation",
    "story_state_extraction",
    "summary_generation",
    "title_generation",
    "utility",
]


class TaskModelProfile(BaseModel):
    task_type: TaskModelType
    label: str = ""
    provider: Literal["llama_cpp", "openai_compatible", "lm_studio"] | None = None
    provider_url: str | None = Field(default=None, max_length=400)
    lm_studio_url: str | None = Field(default=None, max_length=400)
    model: str | None = Field(default=None, max_length=300)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, ge=1, le=64000)
    seed: int | None = None
    top_k: int | None = Field(default=None, ge=0, le=100000)
    min_p: float | None = Field(default=None, ge=0, le=1)
    repeat_penalty: float | None = Field(default=None, ge=0, le=4)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    timeout_seconds: float | None = Field(default=None, ge=5, le=900)
    streaming: bool | None = None
    inference_backend: Literal["openai_compatible", "native_rest"] | None = None
    reasoning_mode: Literal["auto", "off", "low", "medium", "high", "on"] | None = None
    context_length: int | None = Field(default=None, ge=512, le=1048576)
    fallback_to_openai_compatible: bool | None = None
    notes: str = Field(default="", max_length=4000)


class TaskModelProfileUpdate(BaseModel):
    provider: Literal["llama_cpp", "openai_compatible", "lm_studio"] | None = None
    provider_url: str | None = Field(default=None, max_length=400)
    lm_studio_url: str | None = Field(default=None, max_length=400)
    model: str | None = Field(default=None, max_length=300)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, ge=1, le=64000)
    seed: int | None = None
    top_k: int | None = Field(default=None, ge=0, le=100000)
    min_p: float | None = Field(default=None, ge=0, le=1)
    repeat_penalty: float | None = Field(default=None, ge=0, le=4)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    timeout_seconds: float | None = Field(default=None, ge=5, le=900)
    streaming: bool | None = None
    inference_backend: Literal["openai_compatible", "native_rest"] | None = None
    reasoning_mode: Literal["auto", "off", "low", "medium", "high", "on"] | None = None
    context_length: int | None = Field(default=None, ge=512, le=1048576)
    fallback_to_openai_compatible: bool | None = None
    notes: str | None = Field(default=None, max_length=4000)


class ResolvedTaskModelSettings(BaseModel):
    task_type: TaskModelType
    label: str
    provider: Literal["llama_cpp", "openai_compatible", "lm_studio"] = "lm_studio"
    provider_url: str = "http://localhost:1234/v1"
    lm_studio_url: str
    model: str = ""
    temperature: float
    top_p: float
    max_tokens: int
    seed: int | None = None
    top_k: int
    min_p: float
    repeat_penalty: float
    presence_penalty: float
    frequency_penalty: float
    timeout_seconds: float
    streaming: bool
    inference_backend: Literal["openai_compatible", "native_rest"] = "openai_compatible"
    reasoning_mode: Literal["auto", "off", "low", "medium", "high", "on"] = "auto"
    context_length: int | None = None
    fallback_to_openai_compatible: bool = True
    uses_global_model: bool = True
    override_fields: list[str] = Field(default_factory=list)
    notes: str = ""


class TaskModelProfilesResponse(BaseModel):
    task_types: list[dict[str, str]]
    profiles: dict[str, TaskModelProfile]
    resolved: dict[str, ResolvedTaskModelSettings]


class ModelPresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    system_prompt: str = ""
    settings: dict[str, Any] = Field(default_factory=dict)


class ModelPresetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    system_prompt: str | None = None
    settings: dict[str, Any] | None = None


class ModelPresetRead(BaseModel):
    id: str
    name: str
    system_prompt: str
    settings: dict[str, Any]
    created_at: str
    updated_at: str


class TTSVoiceProfile(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=120)
    provider: Literal["high_quality_local", "kokoro", "browser"] = "kokoro"
    voice_id: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=160)
    speed: float = Field(default=0.95, ge=0.25, le=4.0)
    style_notes: str = Field(default="", max_length=800)
    recommended_use: str = Field(default="", max_length=800)
    enabled: bool = True
    available: bool = True
    default: bool = False
    fallback_provider: Literal["kokoro", "browser"] | None = None
    quality_mode: Literal["fast", "balanced", "premium"] = "balanced"
    chunking_profile: Literal["fast", "natural", "audiobook"] = "natural"
    narration_pacing: Literal["fast", "natural", "slow"] = "natural"
    dialogue_pause_strength: Literal["low", "medium", "high"] = "medium"
    paragraph_pause_strength: Literal["low", "medium", "high"] = "medium"
    dialogue_narration_style: Literal["neutral", "slightly_dramatic", "minimal"] = "neutral"
    breathing_mode: Literal["off", "natural", "cinematic"] = "natural"
    voice_preferences: list[str] = Field(default_factory=list)
    pronunciation_profile: str | None = Field(default=None, max_length=160)
    authorized_reference_metadata: dict[str, Any] = Field(default_factory=dict)
    unavailable_reason: str = Field(default="", max_length=1200)
    benchmark_report: str | None = Field(default=None, max_length=260)


class TTSPronunciationEntry(BaseModel):
    id: str | None = Field(default=None, max_length=80)
    written_form: str = Field(min_length=1, max_length=120)
    spoken_form: str = Field(min_length=1, max_length=160)
    scope: Literal["global", "story"] = "global"
    story_id: str | None = None
    enabled: bool = True
    notes: str = Field(default="", max_length=400)


class TTSSettings(BaseModel):
    tts_provider: Literal["high_quality_local", "browser", "kokoro"] = "kokoro"
    tts_quality_mode: Literal["fast", "balanced", "premium"] = "balanced"
    tts_fallback_provider: Literal["kokoro", "browser"] = "kokoro"
    high_quality_local_enabled: bool = False
    tts_speed: float = 0.95
    tts_voice: str | None = None
    tts_voice_profile_id: str | None = "natural_female_narrator"
    tts_voice_profiles: list[TTSVoiceProfile] = Field(default_factory=list)
    auto_read_new_scenes: bool = False
    fast_reading_mode: bool = False
    pre_synthesize_new_scenes: bool = False
    pre_synthesize_mode: Literal["off", "selected_scene", "all_new_scenes"] = "selected_scene"
    chunked_narration_mode: Literal["off", "first_chunk_fast", "progressive_chunks", "full_scene_only"] = "progressive_chunks"
    tts_chunking_profile: Literal["fast", "natural", "audiobook"] = "natural"
    tts_chunk_size: int = Field(default=1200, ge=400, le=6000)
    tts_prebuffer_chunks: int = Field(default=2, ge=1, le=3)
    tts_cache_max_mb: int = Field(default=5120, ge=256, le=51200)
    kokoro_base_url: str = "http://localhost:8880"
    allow_browser_fallback: bool = False
    highlight_narration: bool = False
    tts_follow_mode: Literal["off", "sentence", "phrase", "word_estimate", "exact_word"] = "phrase"
    tts_follow_highlight: Literal["off", "sentence", "phrase", "word_estimate", "exact_word", "subtle", "strong"] = "phrase"
    narration_pacing: Literal["fast", "natural", "slow"] = "natural"
    dialogue_pause_strength: Literal["low", "medium", "high"] = "medium"
    paragraph_pause_strength: Literal["low", "medium", "high"] = "medium"
    dialogue_narration_style: Literal["neutral", "slightly_dramatic", "minimal"] = "neutral"
    breathing_mode: Literal["off", "natural", "cinematic"] = "natural"
    pronunciation_entries: list[TTSPronunciationEntry] = Field(default_factory=list)
    pronunciation_dictionary_version: str | None = None
    normalization_version: str | None = None
    kokoro_temperature: float | None = None
    kokoro_top_p: float | None = None
    kokoro_exaggeration: float | None = None
    kokoro_style: str | None = None
    kokoro_cfg: float | None = None

    @field_validator("kokoro_base_url")
    @classmethod
    def validate_kokoro_url(cls, value: str) -> str:
        return validate_local_service_url(value, "Kokoro URL")


class ImageSettings(BaseModel):
    image_generation_mode: Literal["enabled", "paused", "manual"] = "paused"
    comfyui_base_url: str = "http://localhost:8188"
    selected_workflow_id: str | None = None
    image_resource_mode: Literal["balanced", "image_priority", "manual", "experimental_auto_swap"] = "balanced"
    lm_unload_policy: Literal["selected", "all", "manual"] = "all"
    lm_reload_policy: Literal["prose", "previous_selected", "none", "preserve_manual", "all_previous"] = "prose"
    lm_reload_use_fast_profile: bool = True
    lm_reload_prefer_cli: bool = True
    lm_reload_parallel: int = Field(default=1, ge=1, le=16)
    lm_reload_context_length: int | None = Field(default=50749, ge=512, le=1048576)
    lm_reload_gpu_offload: Literal["default", "max", "off"] = "max"
    free_comfyui_memory_after_generation: bool = False
    comfyui_idle_cleanup_mode: Literal["off", "after_image", "idle_delay", "manual"] = "after_image"
    free_comfyui_before_writing: bool = False
    comfyui_free_throttle_seconds: int = Field(default=120, ge=10, le=3600)
    comfyui_regenerate_grace_seconds: int = Field(default=30, ge=0, le=600)
    fast_regenerate_window_enabled: bool = True
    regenerate_warm_window_seconds: int = Field(default=90, ge=0, le=900)
    free_comfyui_after_regenerate_window: bool = True
    reload_lm_after_regenerate_window: bool = True
    auto_reload_lm_after_image: bool = True
    generate_image_while_narrating: bool = True
    auto_image_on_narrate: bool = False

    @field_validator("comfyui_base_url")
    @classmethod
    def validate_comfyui_url(cls, value: str) -> str:
        return validate_local_service_url(value, "ComfyUI URL")


class StoryStateSettings(BaseModel):
    automatic_story_state: bool = True
    run_state_extraction_in_background: bool = True
    state_extraction_timeout_seconds: int = Field(default=120, ge=10, le=900)


class UISettings(BaseModel):
    sidebar_collapsed: bool = False
    reading_width: Literal["narrow", "comfortable", "wide", "full"] = "comfortable"
    composer_size: Literal["compact", "comfortable", "tall", "custom"] = "comfortable"
    composer_min_height: int = Field(default=80, ge=56, le=360)
    composer_max_height: int = Field(default=240, ge=120, le=640)
    composer_font_size: int = Field(default=16, ge=14, le=22)
    director_line_height: float = Field(default=1.5, ge=1.2, le=2.2)
    prose_font_size: int = Field(default=18, ge=14, le=28)
    prose_line_height: float = Field(default=1.8, ge=1.35, le=2.4)
    paragraph_spacing: float = Field(default=1.25, ge=0.5, le=2.5)
    prose_font: Literal["sans", "serif"] = "serif"
    ui_scale: int = Field(default=100, ge=90, le=125)
    mobile_scale: Literal["follow", "compact", "default", "large"] = "follow"
    motion: Literal["off", "subtle", "full"] = "subtle"
    easter_eggs_enabled: bool = True
    background_selected_ids: list[str] = Field(default_factory=list, max_length=100)
    background_opacity: float = Field(default=0.1, ge=0, le=0.6)
    background_blur: int = Field(default=0, ge=0, le=24)
    background_dim: float = Field(default=0.35, ge=0, le=0.9)
    background_saturation: float = Field(default=0.85, ge=0, le=1.5)
    background_position: Literal["center", "top", "bottom", "left", "right"] = "center"
    background_fit: Literal["cover", "contain", "fill"] = "cover"
    background_attachment: Literal["fixed", "scroll"] = "fixed"
    background_rotation_seconds: int = Field(default=0, ge=0, le=86400)
    background_rotation_order: Literal["ordered", "random"] = "ordered"
    background_fade_seconds: float = Field(default=1.2, ge=0, le=10)
    background_pause_while_writing: bool = True

    @field_validator("background_rotation_seconds")
    @classmethod
    def validate_background_rotation(cls, value: int) -> int:
        if value and value < 30:
            raise ValueError("Background rotation must be off or at least 30 seconds.")
        return value


class SessionImageSettings(BaseModel):
    session_id: str
    selected_workflow_id: str | None = None
    image_resource_mode: Literal["balanced", "image_priority", "manual", "experimental_auto_swap"] | None = None
    image_prompt_style: str = Field(default="", max_length=4000)
    preferred_visual_tone: str = Field(default="", max_length=4000)
    realism_notes: str = Field(default="", max_length=4000)
    lighting_camera_notes: str = Field(default="", max_length=4000)
    auto_open_preview: bool = True
    auto_attach_generated: bool = True
    generate_image_while_narrating: bool | None = None
    auto_image_on_narrate: bool | None = None
    default_negative_prompt: str = Field(default="", max_length=8000)
    updated_at: str | None = None


class SessionImageSettingsUpdate(BaseModel):
    selected_workflow_id: str | None = None
    image_resource_mode: Literal["balanced", "image_priority", "manual", "experimental_auto_swap"] | None = None
    image_prompt_style: str | None = Field(default=None, max_length=4000)
    preferred_visual_tone: str | None = Field(default=None, max_length=4000)
    realism_notes: str | None = Field(default=None, max_length=4000)
    lighting_camera_notes: str | None = Field(default=None, max_length=4000)
    auto_open_preview: bool | None = None
    auto_attach_generated: bool | None = None
    generate_image_while_narrating: bool | None = None
    auto_image_on_narrate: bool | None = None
    default_negative_prompt: str | None = Field(default=None, max_length=8000)


class SceneImageSettings(BaseModel):
    session_id: str
    scene_id: str
    version_id: str | None = None
    selected_workflow_id: str | None = None
    updated_at: str | None = None


class SceneImageSettingsUpdate(BaseModel):
    version_id: str | None = None
    selected_workflow_id: str | None = None


class ImageWorkflowConfig(BaseModel):
    name: str = Field(default="", max_length=160)
    workflow_file: str = Field(default="", max_length=260)
    workflow_type: Literal[
        "auto",
        "z_image_turbo",
        "z_image_base",
        "lonecat_zit",
        "flux_klein",
        "lonecat_flux_klein",
        "other",
    ] = "auto"
    resource_mode: Literal["auto", "balanced", "image_priority", "manual", "experimental_auto_swap"] = "auto"
    expected_time_seconds_min: int | None = Field(default=None, ge=1, le=3600)
    expected_time_seconds_max: int | None = Field(default=None, ge=1, le=3600)
    positive_prompt_node_id: str = Field(default="", max_length=80)
    positive_prompt_input: str = Field(default="text", max_length=80)
    negative_prompt_node_id: str = Field(default="", max_length=80)
    negative_prompt_input: str = Field(default="text", max_length=80)
    seed_node_id: str = Field(default="", max_length=80)
    seed_input: str = Field(default="seed", max_length=80)
    output_node_id: str = Field(default="", max_length=80)
    reference_image_node_id: str = Field(default="", max_length=80)
    reference_image_input: str = Field(default="image", max_length=80)
    img2img_input_node_id: str = Field(default="", max_length=80)
    img2img_input: str = Field(default="image", max_length=80)
    character_reference_node_id: str = Field(default="", max_length=80)
    character_reference_input: str = Field(default="image", max_length=80)
    denoise_node_id: str = Field(default="", max_length=80)
    denoise_input: str = Field(default="denoise", max_length=80)
    notes: str = Field(default="", max_length=4000)


class ImageWorkflowNodeCandidate(BaseModel):
    node_id: str
    class_type: str = ""
    input_name: str = ""
    input_names: list[str] = Field(default_factory=list)
    role: Literal["prompt", "positive", "negative", "seed", "output", "reference", "img2img", "denoise"] = "prompt"
    confidence: Literal["high", "medium", "low"] = "low"
    confidence_score: int = 0
    detected_by: str = ""
    current_text_preview: str = ""


class ImageWorkflowRead(BaseModel):
    id: str
    name: str
    workflow_file: str
    workflow_path: str
    config_file: str | None = None
    has_config: bool = False
    status: Literal["ready", "needs_setup", "invalid"] = "needs_setup"
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    config: ImageWorkflowConfig | None = None
    detected_config: ImageWorkflowConfig | None = None
    node_candidates: list[ImageWorkflowNodeCandidate] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ImageWorkflowListResponse(BaseModel):
    workflows: list[ImageWorkflowRead]
    selected_workflow_id: str | None = None
    workflow_folder: str
    image_settings: ImageSettings


class ImageWorkflowValidationResponse(BaseModel):
    workflow_id: str
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ImageWorkflowImportRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=260)
    content: str = Field(min_length=1)


class SystemOpenFolderRequest(BaseModel):
    target: Literal["comfy_workflows"]


class SystemOpenFolderResponse(BaseModel):
    ok: bool
    path: str
    opened: bool = False
    message: str = ""


class ImageGenerateRequest(BaseModel):
    session_id: str
    scene_id: str
    version_id: str | None = None
    workflow_id: str | None = None
    prompt_override: str | None = Field(default=None, max_length=12000)
    negative_prompt: str | None = Field(default=None, max_length=8000)
    seed: int | None = None
    open_preview: bool = True


class ImagePromptGenerateRequest(BaseModel):
    session_id: str
    scene_id: str
    version_id: str | None = None
    workflow_id: str | None = None
    negative_prompt: str | None = Field(default=None, max_length=8000)


class ImagePromptGenerateResponse(BaseModel):
    visual_beat: str = ""
    prompt: str
    negative_prompt: str = ""
    characters_included: list[str] = Field(default_factory=list)
    continuity_used: dict[str, list[str]] = Field(default_factory=dict)
    style_used: str = ""
    quality_warnings: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    workflow_id: str | None = None
    workflow_name: str = ""
    seed: int | None = None


class SceneImagePromptRead(BaseModel):
    id: str
    session_id: str
    scene_id: str
    version_id: str | None = None
    workflow_id: str = ""
    workflow_name: str = ""
    visual_beat: str = ""
    prompt: str
    negative_prompt: str = ""
    characters_included: list[str] = Field(default_factory=list)
    continuity_used: dict[str, list[str]] = Field(default_factory=dict)
    style_used: str = ""
    quality_warnings: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    style_prompt: str = ""
    workflow_notes: str = ""
    source_hash: str = ""
    status: Literal["ready", "failed"] = "ready"
    error: str | None = None
    created_at: str
    updated_at: str


class GeneratedImageRead(BaseModel):
    id: str
    session_id: str
    scene_id: str
    version_id: str | None = None
    workflow_id: str
    workflow_name: str
    prompt: str
    negative_prompt: str = ""
    seed: int | None = None
    image_path: str
    image_url: str
    status: Literal["pending", "accepted", "rejected", "discarded", "failed"]
    is_primary: bool = False
    created_at: str
    updated_at: str
    resource_mode: str = "balanced"
    resource_warnings: list[str] = Field(default_factory=list)
    resource_actions: dict[str, Any] = Field(default_factory=dict)
    characters_included: list[str] = Field(default_factory=list)
    continuity_used: dict[str, Any] = Field(default_factory=dict)
    reference_images_used: list[dict[str, Any]] = Field(default_factory=list)
    live_state_used: dict[str, Any] = Field(default_factory=dict)


class ImageJobStatus(BaseModel):
    active: bool = False
    stage: str = "ready"
    message: str = "Ready"
    percent: int | None = None
    prompt_id: str | None = None
    session_id: str | None = None
    scene_id: str | None = None
    version_id: str | None = None
    image_id: str | None = None
    resource_mode: str = "balanced"
    updated_at: str | None = None
    warnings: list[str] = Field(default_factory=list)
    actions: dict[str, Any] = Field(default_factory=dict)
    fast_regenerate_window: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    elapsed_seconds: float | None = None
    expected_time_seconds_min: int | None = None
    expected_time_seconds_max: int | None = None
    slow_warning: str | None = None


class TTSSynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=50000)
    voice: str | None = None
    speed: float = Field(default=1.0, ge=0.25, le=4.0)
    provider: Literal["high_quality_local", "browser", "kokoro"] = "browser"
    voice_profile_id: str | None = None
    session_id: str | None = None
    scene_id: str | None = None
    version_id: str | None = None
    narration_job_id: str | None = None
    chunk_index: int | None = Field(default=None, ge=0)
    chunk_count: int | None = Field(default=None, ge=1)
    text_hash: str | None = None
    text_start_offset: int | None = Field(default=None, ge=0)
    text_end_offset: int | None = Field(default=None, ge=0)
    follow_mode: Literal["off", "sentence", "phrase", "word_estimate", "exact_word"] | None = None
    normalized_tts_text: str | None = Field(default=None, max_length=50000)
    narration_pacing: Literal["fast", "natural", "slow"] | None = None
    dialogue_pause_strength: Literal["low", "medium", "high"] | None = None
    paragraph_pause_strength: Literal["low", "medium", "high"] | None = None
    dialogue_narration_style: Literal["neutral", "slightly_dramatic", "minimal"] | None = None
    tts_chunking_profile: Literal["fast", "natural", "audiobook"] | None = None
    pronunciation_dictionary_version: str | None = None
    normalization_version: str | None = None
    paragraph_break_after: bool = False
    scene_break_after: bool = False
    speaker_change_after: bool = False
    breathing_mode: Literal["off", "natural", "cinematic"] = "natural"
    temperature: float | None = None
    top_p: float | None = None
    exaggeration: float | None = None
    style: str | None = None
    cfg: float | None = None


class TTSSynthesizeResponse(BaseModel):
    provider: Literal["high_quality_local", "browser", "kokoro"]
    requested_provider: str | None = None
    requested_profile: str | None = None
    requested_voice: str | None = None
    effective_provider: str | None = None
    effective_profile: str | None = None
    effective_voice: str | None = None
    effective_model: str | None = None
    startup_timings: dict[str, Any] = Field(default_factory=dict)
    fallback_provider: str | None = None
    fallback_used: bool = False
    fallback_reason: str | None = None
    use_browser: bool = False
    audio_url: str | None = None
    duration: float | None = None
    cached: bool = False
    synthesis_seconds: float | None = None
    cache_key: str | None = None
    text_chars: int | None = None
    seconds_per_1000_chars: float | None = None
    chunk_index: int | None = None
    chunk_count: int | None = None
    narration_job_id: str | None = None
    voice_profile_id: str | None = None
    normalization_version: str | None = None
    pronunciation_dictionary_version: str | None = None
    custom_voice_id: str | None = None
    voice_prompt_revision: int | None = None
    style_reference: str | None = None
    narration_style: str | None = None
    narration_emotion: str | None = None
    narration_intensity: float | None = None
    narration_intensity_bucket: str | None = None
    pause_before_ms: int | None = None
    pause_after_ms: int | None = None
    source_cue: str | None = None
    generation_instruction: str | None = None
    breathing_mode: Literal["off", "natural", "cinematic"] | None = None
    breath_before: str | None = None
    breath_after: str | None = None
    breath_confidence: float | None = None
    breath_source_cue: str | None = None
    breath_reference_used: bool = False
    breath_reference_checksum: str | None = None
    breath_reference_revision: int | None = None
    breath_audio_url: str | None = None
    breath_duration: float | None = None


class TTSPreviewRequest(BaseModel):
    sample_text: str | None = Field(default=None, max_length=2000)
    voice: str | None = None
    voice_profile_id: str | None = None
    speed: float | None = Field(default=None, ge=0.25, le=4.0)


class TTSPreviewResponse(TTSSynthesizeResponse):
    sample_text: str
    voice: str
    speed: float
    profile: dict[str, Any] = Field(default_factory=dict)


class CustomVoiceReferenceUpload(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    audio_base64: str = Field(min_length=1, max_length=36_000_000)
    transcript: str = Field(min_length=1, max_length=4000)


class CustomVoiceCreateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    language: str = Field(default="English", min_length=1, max_length=80)
    authorization_confirmed: bool = False
    dominant_speaker_confirmed: bool = False
    user_notes: str = Field(default="", max_length=2000)
    references: dict[str, CustomVoiceReferenceUpload]
    build_prompt: bool = True


class CustomVoiceUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    language: str | None = Field(default=None, min_length=1, max_length=80)
    enabled: bool | None = None
    user_notes: str | None = Field(default=None, max_length=2000)


class CustomBreathUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    audio_base64: str = Field(min_length=1, max_length=18_000_000)
    authorization_confirmed: bool = False
    isolated_breath_confirmed: bool = False


class CharacterVisualProfileUpdate(BaseModel):
    base_visual_description: str | None = Field(default=None, max_length=12000)
    face_description: str | None = Field(default=None, max_length=8000)
    hair: str | None = Field(default=None, max_length=4000)
    body_build: str | None = Field(default=None, max_length=4000)
    age_marker: str | None = Field(default=None, max_length=1000)
    default_outfit: str | None = Field(default=None, max_length=8000)
    distinctive_marks: str | None = Field(default=None, max_length=8000)
    color_palette: str | None = Field(default=None, max_length=4000)
    negative_prompt: str | None = Field(default=None, max_length=8000)
    z_image_lora_trigger: str | None = Field(default=None, max_length=4000)
    alternate_lora_triggers: str | None = Field(default=None, max_length=8000)
    preferred_voice: str | None = Field(default=None, max_length=4000)
    visual_consistency_notes: str | None = Field(default=None, max_length=12000)
    used_in_image_prompts: bool | None = None


class CharacterVisualProfileRead(BaseModel):
    character_id: str
    base_visual_description: str = ""
    face_description: str = ""
    hair: str = ""
    body_build: str = ""
    age_marker: str = ""
    default_outfit: str = ""
    distinctive_marks: str = ""
    color_palette: str = ""
    negative_prompt: str = ""
    z_image_lora_trigger: str = ""
    alternate_lora_triggers: str = ""
    preferred_voice: str = ""
    visual_consistency_notes: str = ""
    used_in_image_prompts: bool = True
    auto_created_confidence: float = 0.0
    source_scene_id: str | None = None
    source_version_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class CharacterReferenceImageCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=260)
    content_base64: str = Field(min_length=1)
    source: str = Field(default="upload", max_length=4000)
    notes: str = Field(default="", max_length=8000)
    is_primary: bool = False


class CharacterReferenceImageUpdate(BaseModel):
    source: str | None = Field(default=None, max_length=4000)
    notes: str | None = Field(default=None, max_length=8000)
    is_primary: bool | None = None
    archived: bool | None = None


class CharacterReferenceImageRead(BaseModel):
    id: str
    character_id: str
    filename: str = ""
    image_path: str = ""
    image_url: str = ""
    source: str = ""
    notes: str = ""
    is_primary: bool = False
    archived: bool = False
    missing: bool = False
    created_at: str
    updated_at: str


class CharacterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(default="", max_length=4000)
    personality: str = Field(default="", max_length=8000)
    appearance: str = Field(default="", max_length=8000)
    relationships: str = Field(default="", max_length=8000)
    current_state: str = Field(default="", max_length=8000)
    voice: str = Field(default="", max_length=4000)
    image_prompt: str = Field(default="", max_length=12000)
    lora_trigger: str = Field(default="", max_length=4000)
    private_notes: str = Field(default="", max_length=12000)
    auto_created: bool = False
    visual_profile: CharacterVisualProfileUpdate | None = None
    attach_to_session: bool = False
    session_id: str | None = None
    is_active: bool = True


class CharacterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: str | None = Field(default=None, max_length=4000)
    personality: str | None = Field(default=None, max_length=8000)
    appearance: str | None = Field(default=None, max_length=8000)
    relationships: str | None = Field(default=None, max_length=8000)
    current_state: str | None = Field(default=None, max_length=8000)
    voice: str | None = Field(default=None, max_length=4000)
    image_prompt: str | None = Field(default=None, max_length=12000)
    lora_trigger: str | None = Field(default=None, max_length=4000)
    private_notes: str | None = Field(default=None, max_length=12000)
    auto_created: bool | None = None
    visual_profile: CharacterVisualProfileUpdate | None = None


class CharacterVisualRepairRequest(BaseModel):
    mode: Literal["repair", "extract", "clear_contaminated"] = "repair"
    session_id: str | None = None
    scene_id: str | None = None
    version_id: str | None = None
    apply_changes: bool = True


class CharacterRead(BaseModel):
    id: str
    name: str
    role: str = ""
    personality: str = ""
    appearance: str = ""
    relationships: str = ""
    current_state: str = ""
    voice: str = ""
    image_prompt: str = ""
    lora_trigger: str = ""
    private_notes: str = ""
    auto_created: bool = False
    visual_profile: CharacterVisualProfileRead | None = None
    reference_images: list[CharacterReferenceImageRead] = Field(default_factory=list)
    created_at: str
    updated_at: str


class CharacterVisualRepairResponse(BaseModel):
    character: CharacterRead
    changes: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    backup_path: str = ""
    source_scene_id: str | None = None
    source_version_id: str | None = None
    applied: bool = False


class SessionCharacterAttach(BaseModel):
    is_active: bool = True


class SessionCharacterUpdate(BaseModel):
    is_active: bool


class SessionCharacterRead(BaseModel):
    id: str
    session_id: str
    character_id: str
    is_active: bool
    created_at: str
    updated_at: str
    character: CharacterRead


class WorldNotesCreate(BaseModel):
    setting: str = Field(default="", max_length=12000)
    tone: str = Field(default="", max_length=8000)
    rules: str = Field(default="", max_length=12000)
    locations: str = Field(default="", max_length=12000)
    factions: str = Field(default="", max_length=12000)
    conflicts: str = Field(default="", max_length=12000)
    history: str = Field(default="", max_length=12000)


class WorldNotesUpdate(BaseModel):
    setting: str | None = Field(default=None, max_length=12000)
    tone: str | None = Field(default=None, max_length=8000)
    rules: str | None = Field(default=None, max_length=12000)
    locations: str | None = Field(default=None, max_length=12000)
    factions: str | None = Field(default=None, max_length=12000)
    conflicts: str | None = Field(default=None, max_length=12000)
    history: str | None = Field(default=None, max_length=12000)


class WorldNotesRead(BaseModel):
    id: str
    session_id: str
    setting: str = ""
    tone: str = ""
    rules: str = ""
    locations: str = ""
    factions: str = ""
    conflicts: str = ""
    history: str = ""
    created_at: str
    updated_at: str


class StoryFoundationRefreshRequest(BaseModel):
    director_note: str = Field(default="", max_length=12000)
    apply_to_cards: bool = True
    force: bool = True


class StoryFoundationUpdate(BaseModel):
    foundation: dict[str, Any] | None = None
    locked_paths: list[str] | None = None
    apply_to_cards: bool = True


class StoryFoundationRead(BaseModel):
    id: str
    session_id: str
    schema_version: str = "story_foundation_v1"
    status: str = "draft"
    source_director_note: str = ""
    foundation: dict[str, Any] = Field(default_factory=dict)
    source_map: dict[str, Any] = Field(default_factory=dict)
    locked_paths: list[str] = Field(default_factory=list)
    prompt_projection: str = ""
    generation_model: str = ""
    generation_task_type: str = ""
    generation_error: str | None = None
    foundation_revision: int = 1
    source_kind: str = "unknown"
    source_generation_id: str = ""
    source_opening_note_checksum: str = ""
    created_at: str
    updated_at: str
    refreshed_at: str | None = None


QualityNoteType = Literal["writing", "state", "character", "image", "tts", "ui", "speed", "other"]
QualitySeverity = Literal["low", "medium", "high"]
QualityStatus = Literal["open", "fixed", "ignored"]


class QualityNoteCreate(BaseModel):
    note_type: QualityNoteType = "other"
    severity: QualitySeverity = "medium"
    status: QualityStatus = "open"
    note_text: str = Field(min_length=1, max_length=12000)
    scene_id: str | None = None
    version_id: str | None = None


class QualityNoteUpdate(BaseModel):
    note_type: QualityNoteType | None = None
    severity: QualitySeverity | None = None
    status: QualityStatus | None = None
    note_text: str | None = Field(default=None, min_length=1, max_length=12000)
    scene_id: str | None = None
    version_id: str | None = None


class QualityNoteRead(BaseModel):
    id: str
    session_id: str
    scene_id: str | None = None
    version_id: str | None = None
    note_type: QualityNoteType = "other"
    severity: QualitySeverity = "medium"
    status: QualityStatus = "open"
    note_text: str = ""
    created_at: str
    updated_at: str


class SceneQualityChecklistUpdate(BaseModel):
    version_id: str | None = None
    director_note_followed: bool | None = None
    length_okay: bool | None = None
    no_assistant_tone: bool | None = None
    continuity_okay: bool | None = None
    state_extracted_okay: bool | None = None
    image_prompt_okay: bool | None = None
    narration_okay: bool | None = None
    image_generation_okay: bool | None = None
    notes: str | None = Field(default=None, max_length=12000)


class SceneQualityChecklistRead(BaseModel):
    id: str
    session_id: str
    scene_id: str
    version_id: str = ""
    director_note_followed: bool | None = None
    length_okay: bool | None = None
    no_assistant_tone: bool | None = None
    continuity_okay: bool | None = None
    state_extracted_okay: bool | None = None
    image_prompt_okay: bool | None = None
    narration_okay: bool | None = None
    image_generation_okay: bool | None = None
    notes: str = ""
    created_at: str
    updated_at: str


class QualityHintRead(BaseModel):
    id: str
    hint_type: QualityNoteType = "other"
    severity: QualitySeverity = "low"
    message: str
    scene_id: str | None = None
    version_id: str | None = None
    action_label: str = ""


class QualityHintsResponse(BaseModel):
    session_id: str
    hints: list[QualityHintRead] = Field(default_factory=list)
    open_issue_count: int = 0


class StoryMemoryRead(BaseModel):
    id: str
    session_id: str
    character_id: str | None = None
    memory_type: str
    title: str
    content: str
    importance: int
    keywords_json: str
    source_scene_id: str | None = None
    created_at: str
    updated_at: str


class SessionSummaryRead(BaseModel):
    id: str
    session_id: str
    summary_text: str
    from_scene_id: str | None = None
    to_scene_id: str | None = None
    created_at: str
    updated_at: str


class StoryStateRunRead(BaseModel):
    id: str
    session_id: str
    scene_id: str
    version_id: str | None = None
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class StoryStateItemUpdate(BaseModel):
    value: str | None = Field(default=None, max_length=12000)
    content: str | None = Field(default=None, max_length=12000)
    confidence: float | None = Field(default=None, ge=0, le=1)
    archived: bool | None = None
    disabled: bool | None = None
    manual_override: bool | None = None
    manually_pinned: bool | None = None
    review_status: str | None = Field(default=None, max_length=80)
    relationship_type: str | None = Field(default=None, max_length=80)
    closeness_weight: float | None = Field(default=None, ge=0, le=1)
    trust_weight: float | None = Field(default=None, ge=0, le=1)
    conflict_weight: float | None = Field(default=None, ge=0, le=1)
    protective_weight: float | None = Field(default=None, ge=0, le=1)
    grief_weight: float | None = Field(default=None, ge=0, le=1)
    romantic_weight: float | None = Field(default=None, ge=0, le=1)
    family_weight: float | None = Field(default=None, ge=0, le=1)
    betrayal_weight: float | None = Field(default=None, ge=0, le=1)
    respect_weight: float | None = Field(default=None, ge=0, le=1)
    fear_weight: float | None = Field(default=None, ge=0, le=1)
    emotional_importance: float | None = Field(default=None, ge=0, le=1)
    memory_type: str | None = Field(default=None, max_length=80)
    memory_text: str | None = Field(default=None, max_length=12000)
    emotional_weight: float | None = Field(default=None, ge=0, le=1)
    themes: list[str] | None = None
    related_characters: list[str] | None = None
    trigger_conditions: str | None = Field(default=None, max_length=4000)
    cooldown_scenes: int | None = Field(default=None, ge=0, le=50)
    owner_character_name: str | None = Field(default=None, max_length=180)
    holder_character_name: str | None = Field(default=None, max_length=180)
    current_location: str | None = Field(default=None, max_length=400)
    placement_state: Literal["held", "worn", "placed", "stored", "hidden", "lost", "destroyed", "unknown"] | None = None
    condition: str | None = Field(default=None, max_length=400)
    visibility: Literal["visible", "hidden", "concealed", "unknown"] | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    status: str | None = Field(default=None, max_length=80)
    title: str | None = Field(default=None, max_length=220)


class StoryStateItemActionResponse(BaseModel):
    ok: bool
    item_type: str | None = None
    item_id: str | None = None
    action: str
    session_id: str | None = None
    affected_count: int = 0


class StoryStateConflictRead(BaseModel):
    id: str
    type: str
    severity: Literal["low", "medium", "high"] = "medium"
    message: str
    item_ids: list[str] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)


class CharacterLiveStateRead(BaseModel):
    id: str
    session_id: str
    character_id: str | None = None
    character_name: str = ""
    state_type: str = "general"
    key: str
    value: str = ""
    previous_value: str | None = None
    confidence: float = 0.0
    is_tentative: bool = False
    archived: bool = False
    disabled: bool = False
    manual_override: bool = False
    review_status: str = "ok"
    source_scene_id: str | None = None
    source_version_id: str | None = None
    created_at: str
    updated_at: str


class RelationshipStateRead(BaseModel):
    id: str
    session_id: str
    character_a_name: str = ""
    character_b_name: str = ""
    relationship_type: str = "unknown"
    relationship_key: str = "relationship"
    content: str = ""
    closeness_weight: float = 0.0
    trust_weight: float = 0.0
    conflict_weight: float = 0.0
    protective_weight: float = 0.0
    grief_weight: float = 0.0
    romantic_weight: float = 0.0
    family_weight: float = 0.0
    betrayal_weight: float = 0.0
    respect_weight: float = 0.0
    fear_weight: float = 0.0
    emotional_importance: float = 0.0
    last_reinforced_scene_id: str | None = None
    confidence: float = 0.0
    is_tentative: bool = False
    archived: bool = False
    disabled: bool = False
    manual_override: bool = False
    manually_pinned: bool = False
    review_status: str = "ok"
    source_scene_id: str | None = None
    source_version_id: str | None = None
    updated_at: str


class EmotionalMemoryRead(BaseModel):
    id: str
    session_id: str
    memory_type: str = "emotional"
    memory_text: str = ""
    emotional_weight: float = 0.0
    themes: list[str] = Field(default_factory=list)
    related_characters: list[str] = Field(default_factory=list)
    trigger_conditions: str = ""
    last_used_in_prompt: str | None = None
    use_count: int = 0
    cooldown_scenes: int = 3
    confidence: float = 0.0
    is_tentative: bool = False
    archived: bool = False
    disabled: bool = False
    manual_override: bool = False
    manually_pinned: bool = False
    review_status: str = "ok"
    source_scene_id: str | None = None
    source_version_id: str | None = None
    updated_at: str


class WorldLiveStateRead(BaseModel):
    id: str
    session_id: str
    state_type: str = "world"
    key: str
    value: str = ""
    confidence: float = 0.0
    is_tentative: bool = False
    archived: bool = False
    disabled: bool = False
    manual_override: bool = False
    review_status: str = "ok"
    source_scene_id: str | None = None
    source_version_id: str | None = None
    updated_at: str


class SceneLiveStateRead(BaseModel):
    id: str
    session_id: str
    scene_id: str
    version_id: str | None = None
    state_type: str = "scene"
    key: str
    value: str = ""
    confidence: float = 0.0
    is_tentative: bool = False
    archived: bool = False
    disabled: bool = False
    manual_override: bool = False
    review_status: str = "ok"
    updated_at: str


class ObjectStateRead(BaseModel):
    id: str
    session_id: str
    object_key: str
    name: str = ""
    state_type: str = "object"
    value: str = ""
    owner_character_name: str = ""
    holder_character_name: str = ""
    current_location: str = ""
    placement_state: str = "unknown"
    condition: str = ""
    visibility: str = "unknown"
    importance: float = 0.0
    confidence: float = 0.0
    is_tentative: bool = False
    archived: bool = False
    disabled: bool = False
    manual_override: bool = False
    review_status: str = "ok"
    source_scene_id: str | None = None
    source_version_id: str | None = None
    updated_at: str


class PlotThreadRead(BaseModel):
    id: str
    session_id: str
    thread_key: str
    title: str = ""
    status: str = "active"
    content: str = ""
    confidence: float = 0.0
    is_tentative: bool = False
    disabled: bool = False
    manual_override: bool = False
    review_status: str = "ok"
    source_scene_id: str | None = None
    source_version_id: str | None = None
    resolved_scene_id: str | None = None
    resolved_version_id: str | None = None
    updated_at: str


class CharacterStateEventRead(BaseModel):
    id: str
    session_id: str
    character_id: str | None = None
    character_name: str = ""
    state_type: str = "general"
    key: str
    value: str = ""
    previous_value: str | None = None
    action: str = "update"
    confidence: float = 0.0
    source_scene_id: str | None = None
    source_version_id: str | None = None
    created_at: str


class StoryStateOverview(BaseModel):
    session_id: str
    settings: StoryStateSettings
    latest_run: StoryStateRunRead | None = None
    prompt_context: str = ""
    prompt_item_count: int = 0
    memory_pack_context: str = ""
    memory_pack_item_count: int = 0
    next_prompt_memory_pack: dict[str, Any] = Field(default_factory=dict)
    archived_item_count: int = 0
    disabled_item_count: int = 0
    manual_override_count: int = 0
    visual_prompt_context: str = ""
    summary_status: dict[str, Any] = Field(default_factory=dict)
    conflicts: list[StoryStateConflictRead] = Field(default_factory=list)
    active_characters: list[dict[str, Any]] = Field(default_factory=list)
    character_live_state: list[CharacterLiveStateRead] = Field(default_factory=list)
    relationships: list[RelationshipStateRead] = Field(default_factory=list)
    emotional_memories: list[EmotionalMemoryRead] = Field(default_factory=list)
    world_state: list[WorldLiveStateRead] = Field(default_factory=list)
    scene_state: list[SceneLiveStateRead] = Field(default_factory=list)
    objects: list[ObjectStateRead] = Field(default_factory=list)
    plot_threads: list[PlotThreadRead] = Field(default_factory=list)
    recent_events: list[CharacterStateEventRead] = Field(default_factory=list)
