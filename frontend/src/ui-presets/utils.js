export const UI_PRESET_ID_RE = /^[a-z0-9][a-z0-9-]{1,63}$/;

export function isUiPresetIdFormat(presetId) {
  return UI_PRESET_ID_RE.test(String(presetId || ""));
}

export function presetName(preset) {
  return preset?.displayName || preset?.name || preset?.id || "Unknown preset";
}

export function presetDescription(preset) {
  return preset?.shortDescription || preset?.description || "Local StoryDriver UI preset.";
}

export function presetTags(preset) {
  return Array.isArray(preset?.tags) ? preset.tags.filter(Boolean).slice(0, 4) : [];
}

export function presetColors(preset) {
  return preset?.tokens?.colors || {};
}

export function normalizePresetForExport(preset) {
  if (!preset) return null;
  return {
    id: preset.id,
    displayName: presetName(preset),
    shortDescription: presetDescription(preset),
    category: preset.category || (preset.custom ? "Custom" : "Default"),
    tags: presetTags(preset),
    version: preset.version || "1.0.0",
    author: preset.author || "StoryDriver",
    source: preset.source || "StoryDriver local UI preset",
    compatibilityVersion: preset.compatibilityVersion || "1",
    tokens: preset.tokens || {},
    cssVariables: preset.cssVariables || {},
    thumbnail: preset.thumbnail || {},
    componentVariants: preset.componentVariants || {},
    custom: Boolean(preset.custom),
    builtIn: Boolean(preset.builtIn),
  };
}
