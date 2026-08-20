import chronicleHallPreset from "./chronicleHallPreset.js";
import defaultPreset from "./defaultPreset.js";
import emberfallPreset from "./emberfallPreset.js";
import midnightAtelierPreset from "./midnightAtelierPreset.js";
import writersWorkshopPreset from "./writersWorkshopPreset.js";
import { isUiPresetIdFormat } from "./utils.js";

export const DEFAULT_UI_PRESET_ID = "default";
export const UI_PRESET_STORAGE_KEY = "storydriver.uiPresetId";

export const BUILT_IN_UI_PRESETS = [
  defaultPreset,
  emberfallPreset,
  midnightAtelierPreset,
  chronicleHallPreset,
  writersWorkshopPreset,
];

let customUiPresets = [];

export const UI_PRESETS = BUILT_IN_UI_PRESETS;

export function getCustomUiPresets() {
  return customUiPresets;
}

export function setCustomUiPresets(presets = []) {
  customUiPresets = Array.isArray(presets)
    ? presets.filter((preset) => preset?.id && isUiPresetIdFormat(preset.id) && preset.cssVariables && !preset.disabled)
    : [];
  return customUiPresets;
}

export function getAllUiPresets() {
  return [...BUILT_IN_UI_PRESETS, ...customUiPresets];
}

export function getUiPreset(presetId = DEFAULT_UI_PRESET_ID) {
  return getAllUiPresets().find((preset) => preset.id === presetId) || defaultPreset;
}

export function normalizeUiPresetId(presetId) {
  return isUiPresetIdFormat(presetId) ? String(presetId) : DEFAULT_UI_PRESET_ID;
}
