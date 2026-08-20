import {
  DEFAULT_UI_PRESET_ID,
  UI_PRESET_STORAGE_KEY,
  getUiPreset,
} from "./registry.js";
import { getResolvedLayoutConfig } from "./layoutConfig.js";
import { isUiPresetIdFormat } from "./utils.js";

const canUseDom = () => typeof window !== "undefined" && typeof document !== "undefined";
const VARIANT_DATASETS = Object.freeze({
  proseCardVariant: "proseVariant",
  composerVariant: "composerVariant",
  buttonVariant: "buttonVariant",
  sidebarVariant: "sidebarVariant",
  storyCardVariant: "storyCardVariant",
  miniPlayerVariant: "miniPlayerVariant",
  drawerVariant: "drawerVariant",
  backgroundMotif: "backgroundMotif",
  typographyMode: "typographyMode",
  ornamentalDensity: "ornamentalDensity",
});

function safeVariantValue(value, fallback = "standard") {
  const normalized = String(value || fallback).toLowerCase().replace(/[^a-z0-9-]/g, "-");
  return normalized.replace(/-+/g, "-").replace(/^-|-$/g, "") || fallback;
}

export function readStoredUiPresetId() {
  if (!canUseDom()) return DEFAULT_UI_PRESET_ID;
  try {
    const stored = window.localStorage.getItem(UI_PRESET_STORAGE_KEY);
    return isUiPresetIdFormat(stored) ? stored : DEFAULT_UI_PRESET_ID;
  } catch {
    return DEFAULT_UI_PRESET_ID;
  }
}

export function persistUiPresetId(presetId) {
  if (!canUseDom()) return DEFAULT_UI_PRESET_ID;
  const normalized = isUiPresetIdFormat(presetId) ? String(presetId) : DEFAULT_UI_PRESET_ID;
  try {
    window.localStorage.setItem(UI_PRESET_STORAGE_KEY, normalized);
  } catch {
    // Preset persistence is a convenience; CSS fallbacks keep the app usable.
  }
  return normalized;
}

export function applyUiPreset(presetId = DEFAULT_UI_PRESET_ID) {
  if (!canUseDom()) return getUiPreset(presetId);
  const preset = getUiPreset(presetId);
  const fallbackPreset = getUiPreset(DEFAULT_UI_PRESET_ID);
  const root = document.documentElement;
  const layoutConfig = getResolvedLayoutConfig(preset);
  root.dataset.uiPreset = preset.id;
  root.dataset.rightSidebar = layoutConfig.rightSidebarEnabled ? "enabled" : "disabled";
  root.dataset.utilityCards = layoutConfig.utilityCards;
  root.dataset.utilityTray = layoutConfig.utilityTrayEnabled ? "enabled" : "disabled";
  root.dataset.prosePanelMode = layoutConfig.prosePanelMode;
  Object.entries(VARIANT_DATASETS).forEach(([presetKey, datasetKey]) => {
    root.dataset[datasetKey] = safeVariantValue(preset.componentVariants?.[presetKey]);
  });
  root.style.colorScheme = "dark";
  Object.entries(fallbackPreset.cssVariables || {}).forEach(([name, value]) => {
    root.style.setProperty(name, value);
  });
  Object.entries(preset.cssVariables || {}).forEach(([name, value]) => {
    root.style.setProperty(name, value);
  });
  return preset;
}

export function initializeUiPreset() {
  return applyUiPreset(readStoredUiPresetId());
}
