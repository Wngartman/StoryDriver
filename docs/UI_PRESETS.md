# StoryDriver UI Presets

StoryDriver UI presets are local, inert JSON/CSS-token texture packs. They change the visual finish of the existing writing workspace without changing story data, model settings, TTS settings, image workflow data, or app behavior.

## Built-In Presets

- StoryDriver Default: quiet charcoal baseline and safe fallback.
- Emberfall: dark fantasy study with parchment, brass, leather, and ember accents.
- Midnight Atelier: premium modern writing studio with obsidian/navy and steel-blue highlights.
- Chronicle Hall: fantasy archive with ledgers, worn wood, parchment, brass, and map-green accents.
- Writer's Workshop: tactile everyday writing desk with warm charcoal, walnut, paper grain, and craft accents.

## Where Presets Live

Built-in preset code lives in:

- `frontend/src/ui-presets/`
- `frontend/src/ui-presets/presets` may be used later if the built-in preset count grows.

The registry and shared helpers live in:

- `frontend/src/ui-presets/registry.js`
- `frontend/src/ui-presets/applyUiPreset.js`
- `frontend/src/ui-presets/utils.js`
- `frontend/src/ui-presets/types.js`

Custom presets are stored outside frontend source at runtime:

- `backend/data/ui_presets/custom_presets.json`
- `backend/data/ui_presets/assets/`

Preset exports are saved locally to:

- `backend/data/exports/ui_presets/`

## Import Schema

A custom preset JSON object may contain:

```json
{
  "id": "my-custom-preset",
  "displayName": "My Custom Preset",
  "shortDescription": "A short local-only theme description.",
  "category": "Custom",
  "tags": ["warm", "editorial"],
  "version": "1.0.0",
  "author": "Local",
  "source": "Imported locally",
  "compatibilityVersion": "1",
  "tokens": {
    "colors": {
      "ink": "#090a0c",
      "panel": "#111318",
      "panelSoft": "#171a21",
      "line": "#272a32",
      "story": "#edeae2",
      "muted": "#9ca3af",
      "moss": "#90c99a",
      "ember": "#f1b66d",
      "tide": "#8fc7d8"
    },
    "typography": {
      "sans": "Inter, ui-sans-serif, system-ui, sans-serif",
      "story": "Georgia, Charter, serif"
    },
    "surfaces": {
      "sunken": "#0d0e11",
      "hover": "#1d2028",
      "warning": "#17120c"
    }
  },
  "cssVariables": {}
}
```

`tokens.colors` is required. If `cssVariables` is omitted or partial, StoryDriver derives safe defaults from the token colors and typography.

## Safety Rules

Imported presets are JSON only. StoryDriver rejects:

- JavaScript and executable content
- remote URLs and external fonts
- `url(...)` CSS
- unsafe CSS characters such as braces, semicolons in CSS values, and angle brackets
- path traversal
- absolute asset paths
- preset files over 256 KB
- invalid preset IDs
- overwriting built-in preset IDs

Optional local texture assets must stay inside `backend/data/ui_presets/assets/`. The current system is optimized for CSS-generated texture and thumbnail previews, so image assets are not required.

## Using the Manager

Open Settings -> Appearance.

- Click a preset card to apply it instantly.
- Use Export active to save the active preset as JSON under `backend/data/exports/ui_presets/`.
- Use Duplicate as custom to copy the active built-in or custom preset into `backend/data/ui_presets/custom_presets.json`.
- Use Import custom preset JSON to choose a local `.json` file or paste JSON.
- If an imported ID conflicts with a custom preset, import as a renamed copy or overwrite the custom preset.
- Built-in presets cannot be overwritten; duplicate or import a renamed copy instead.

## Adding a Fifth Built-In Preset

1. Add a preset module in `frontend/src/ui-presets/`.
2. Include metadata: `id`, `displayName`, `shortDescription`, `category`, `tags`, `version`, `author`, `source`, and `compatibilityVersion`.
3. Include `tokens` and `cssVariables`.
4. Add the module to `BUILT_IN_UI_PRESETS` in `frontend/src/ui-presets/registry.js`.
5. Keep new styling token-driven; avoid one-off inline styles.
6. Run `npm.cmd run build` from `frontend`.

## Known Limitations

- The custom editor is intentionally minimal; import/export/duplicate are the supported customization path for now.
- Exact asset packaging for custom texture images is reserved for a later pass.
- Imported preset validation is strict by design, so some valid CSS will be rejected unless it is represented through approved tokens or variables.

