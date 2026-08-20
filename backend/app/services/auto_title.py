from __future__ import annotations

import re


GENERIC_TITLES = {"", "untitled", "untitled story", "new story", "blank story"}
GENERIC_TITLE_PATTERNS = (
    re.compile(r"^new story(?:\s+\d+)?$", re.I),
    re.compile(r"^untitled(?:\s+\d+)?$", re.I),
)
REJECTED_GENERATED_TITLE_WORDS = {
    "untitled",
    "new story",
    "blank story",
    "the beginning",
    "beginning",
    "chapter one",
    "chapter 1",
    "first chapter",
    "opening scene",
    "the opening",
    "story",
}
OVERUSED_AI_TITLE_OPENINGS = {
    "shadows",
    "echoes",
    "whispers",
    "chronicles",
    "legacy",
    "secrets",
    "veil",
}
TITLE_STOP_WORDS = {"a", "an", "and", "at", "for", "from", "in", "of", "on", "the", "to", "with"}
DIRECTOR_COMMAND_WORDS = {"open", "create", "write", "continue", "rewrite", "revise", "regenerate"}


def is_generic_title(title: str | None) -> bool:
    normalized = (title or "").strip().lower()
    return normalized in GENERIC_TITLES or any(pattern.match(normalized) for pattern in GENERIC_TITLE_PATTERNS)


def clean_generated_title(raw_title: str) -> str:
    title = raw_title.strip().splitlines()[0].strip(" \"'`#:-")
    title = re.sub(r"^(?:title|story title)\s*:\s*", "", title, flags=re.I).strip(" \"'`#:-")
    title = " ".join(title.split())
    if len(title) > 80:
        title = title[:80].rsplit(" ", 1)[0].strip()
    return title


def title_words(title: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (title or "").lower())


def title_structure(title: str) -> str:
    words = title_words(title)
    if "of" in words and words.index("of") not in {0, len(words) - 1}:
        return "x_of_y"
    if words and words[0] in {"the", "a", "an"}:
        return "article_phrase"
    return "plain_phrase"


def title_lexical_similarity(first: str, second: str) -> float:
    first_words = {word for word in title_words(first) if word not in TITLE_STOP_WORDS}
    second_words = {word for word in title_words(second) if word not in TITLE_STOP_WORDS}
    if not first_words or not second_words:
        return 0.0
    return len(first_words & second_words) / len(first_words | second_words)


def generated_title_is_usable(title: str, recent_titles: list[str] | tuple[str, ...] = ()) -> bool:
    normalized = re.sub(r"\s+", " ", (title or "").strip().lower())
    if not normalized or normalized in REJECTED_GENERATED_TITLE_WORDS or is_generic_title(normalized):
        return False
    if len(title) < 3 or len(title) > 80:
        return False
    if len(re.findall(r"[A-Za-z0-9]", title)) < 3:
        return False
    if len(title.split()) > 8:
        return False
    if any(phrase in normalized for phrase in ("here is", "i would", "possible title", "suggested title")):
        return False
    words = title_words(title)
    first_word = words[0] if words else ""
    if first_word in DIRECTOR_COMMAND_WORDS or re.search(r"\b(?:for|and the) open\b", normalized):
        return False
    recent = [clean_generated_title(item) for item in recent_titles if clean_generated_title(item)]
    recent_first_words = {title_words(item)[0] for item in recent if title_words(item)}
    if first_word in OVERUSED_AI_TITLE_OPENINGS and (first_word in recent_first_words or len(recent) >= 3):
        return False
    if first_word and first_word in recent_first_words:
        return False
    if any(title_lexical_similarity(title, item) >= 0.6 for item in recent):
        return False
    if title_structure(title) == "x_of_y" and sum(title_structure(item) == "x_of_y" for item in recent[:8]) >= 2:
        return False
    return True


def deterministic_title_fallback(
    director_note: str,
    scene_text: str,
    *,
    character_names: list[str] | tuple[str, ...] = (),
    recent_titles: list[str] | tuple[str, ...] = (),
) -> str:
    source = f"{director_note}\n{scene_text[:2600]}"
    proper_nouns = []
    for candidate in [*character_names, *re.findall(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?\b", source)]:
        cleaned = clean_generated_title(candidate)
        if cleaned and cleaned.lower() not in {
            "the", "when", "after", "before", "storydriver", *DIRECTOR_COMMAND_WORDS
        }:
            if cleaned.lower() not in {item.lower() for item in proper_nouns}:
                proper_nouns.append(cleaned)
    event_nouns = []
    preferred = (
        "letter", "key", "compass", "promise", "bridge", "apartment", "station", "ship", "garden",
        "trial", "door", "map", "ring", "storm", "heist", "wedding", "signal", "forest", "ledger",
    )
    lower = source.lower()
    event_nouns.extend(word.title() for word in preferred if re.search(rf"\b{re.escape(word)}\b", lower))
    candidates: list[str] = []
    if proper_nouns and event_nouns:
        candidates.extend([f"{proper_nouns[0]} and the {event_nouns[0]}", f"{event_nouns[0]} for {proper_nouns[0]}"])
    if len(proper_nouns) >= 2:
        candidates.append(f"{proper_nouns[0]} Meets {proper_nouns[1]}")
    if event_nouns:
        candidates.extend([f"The {event_nouns[0]}", f"After the {event_nouns[0]}"])
    if proper_nouns:
        candidates.append(f"{proper_nouns[0]}'s First Choice")
    for candidate in candidates:
        candidate = clean_generated_title(candidate)
        if generated_title_is_usable(candidate, recent_titles):
            return candidate
    return "A Choice Before Dawn"


def title_is_user_set(row) -> bool:
    keys = row.keys()
    return (
        ("title_source" in keys and row["title_source"] == "user_set")
        or ("auto_title_status" in keys and row["auto_title_status"] == "user_set")
    )


def can_auto_title_row(row) -> bool:
    return bool(row) and is_generic_title(row["title"]) and not title_is_user_set(row)
