# StoryDriver brand assets

`storydriver-mark.svg` is the canonical color source. The mark combines a page silhouette with one routed path and is designed to remain legible at 16 px.

`storydriver-monochrome.svg` is the current-color one-stroke variant. `storydriver-wordmark.svg` is the full local wordmark. Web PNG/ICO/PWA derivatives are generated with:

```bat
D:\StoryDriver\scripts\generate_storydriver_brand_assets.bat
```

The generator uses only Python's standard library and writes to `frontend/public`. Command controls continue to use the bundled, tree-shaken Lucide icon set; StoryDriver-specific concepts use the local SVG set in `frontend/src/assets/icons`.
