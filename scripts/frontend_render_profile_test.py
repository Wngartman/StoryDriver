from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src" / "components"
DIST = ROOT / "frontend" / "dist" / "assets"
REPORT = ROOT / "backend" / "data" / "logs" / "product_polish" / "frontend_render_profile.json"


def read(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    hot_files = ["AppShell.jsx", "Sidebar.jsx", "StoryFeed.jsx", "StoryInput.jsx"]
    sources = {name: read(name) for name in hot_files}
    whole_store_hot = [name for name, source in sources.items() if re.search(r"useAppStore\(\s*\)", source)]
    require(not whole_store_hot, f"hot components still subscribe to the entire store: {whole_store_hot}")

    story_feed = sources["StoryFeed.jsx"]
    app_shell = sources["AppShell.jsx"]
    require("state.narration.currentNarrationSceneId === scene.id" in story_feed, "narration updates are not scoped per scene")
    require("const SceneCard = memo(" in story_feed, "scene cards are not memoized")
    require("export default memo(StoryFeed)" in story_feed, "story feed is not isolated from shell rerenders")
    require("export default memo(StoryInput)" in sources["StoryInput.jsx"], "composer is not memoized")
    require("30000" in app_shell, "health refresh interval is shorter than the 30-second efficiency target")
    require("visibilitychange" in app_shell, "health refresh does not pause while the page is hidden")
    require("setInterval(checkHealth" not in app_shell, "legacy permanent health polling remains")

    background = read("WorkspaceBackground.jsx")
    require(background.count("window.setInterval") == 1, "workspace background must own exactly one rotation timer")
    require("document.visibilityState" in background, "background rotation does not pause in hidden tabs")
    require("[current, next]" in background, "background preloading is not limited to current and next")

    js_files = list(DIST.glob("*.js"))
    css_files = list(DIST.glob("*.css"))
    require(js_files and css_files, "frontend build assets are missing")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "passed": True,
        "hot_whole_store_subscriptions": whole_store_hot,
        "scoped_narration_updates": True,
        "memoized_story_feed": True,
        "health_refresh_seconds": 30,
        "health_pauses_when_hidden": True,
        "background_rotation_timers": 1,
        "javascript_bytes": sum(path.stat().st_size for path in js_files),
        "css_bytes": sum(path.stat().st_size for path in css_files),
        "largest_javascript_asset_bytes": max(path.stat().st_size for path in js_files),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("frontend render profile contract: PASS")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
