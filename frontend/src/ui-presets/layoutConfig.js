export const BASE_LAYOUT_CONFIG = Object.freeze({
  version: 1,
  rightSidebarEnabled: false,
  rightSidebarOverrideAllowed: false,
  centerColumnWidth: "immersive",
  prosePanelMode: "framed-manuscript",
  composerPosition: "bottom",
  utilityTrayEnabled: false,
  utilityTrayOverrideAllowed: false,
  utilityCards: "none",
  narrationPresentation: "mini-player-only",
});

export const GLOBAL_UI_BEHAVIOR_CONFIG = Object.freeze({
  showInlineUtilityTray: false,
  showNarrationUtility: false,
  keepSceneNarrateButtons: true,
});

export function getResolvedLayoutConfig(preset = null) {
  return {
    ...BASE_LAYOUT_CONFIG,
    behavior: GLOBAL_UI_BEHAVIOR_CONFIG,
    presetId: preset?.id || "default",
  };
}

export default BASE_LAYOUT_CONFIG;
