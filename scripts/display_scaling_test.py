import json
import urllib.request
from pathlib import Path


root = Path(__file__).resolve().parents[1]
service = (root / "frontend/src/services/displaySettings.js").read_text(encoding="utf-8")
styles = (root / "frontend/src/styles.css").read_text(encoding="utf-8")
for needle in ("READING_WIDTHS", "COMPOSER_HEIGHTS", "--sd-ui-scale", "--sd-composer-min-height"):
    if needle not in service:
        raise AssertionError(f"display service missing {needle}")
for needle in ('data-mobile-scale="compact"', "font-size: 16px !important", "prefers-reduced-motion"):
    if needle not in styles:
        raise AssertionError(f"display CSS missing {needle}")
settings_url = "http://localhost:8001/settings/ui"
with urllib.request.urlopen(settings_url, timeout=5) as response:
    settings = json.load(response)
for key in ("reading_width", "composer_size", "prose_font_size", "ui_scale", "mobile_scale", "motion"):
    if key not in settings:
        raise AssertionError(f"live UI settings missing {key}")


def put_settings(payload: dict) -> dict:
    request = urllib.request.Request(
        settings_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


probe = dict(settings)
probe.update(
    {
        "reading_width": "wide" if settings["reading_width"] != "wide" else "focused",
        "composer_size": "tall" if settings["composer_size"] != "tall" else "compact",
        "ui_scale": 105 if settings["ui_scale"] != 105 else 100,
        "mobile_scale": "compact" if settings["mobile_scale"] != "compact" else "comfortable",
        "motion": "off" if settings["motion"] != "off" else "full",
    }
)
try:
    updated = put_settings(probe)
    for key in ("reading_width", "composer_size", "ui_scale", "mobile_scale", "motion"):
        if updated[key] != probe[key]:
            raise AssertionError(f"live UI settings did not persist {key}")
finally:
    restored = put_settings(settings)
    if restored != settings:
        raise AssertionError("live UI settings were not restored exactly")
print("display scaling contract: PASS")
