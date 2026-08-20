from dataclasses import dataclass


@dataclass(frozen=True)
class Session:
    id: str
    title: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Scene:
    id: str
    session_id: str
    director_note: str
    generated_text: str
    created_at: str


@dataclass(frozen=True)
class AppSetting:
    key: str
    value_json: str
    updated_at: str
