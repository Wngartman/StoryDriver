export const DEFAULT_UI_SETTINGS = Object.freeze({
  sidebar_collapsed: false,
  reading_width: "comfortable",
  composer_size: "comfortable",
  composer_min_height: 80,
  composer_max_height: 240,
  composer_font_size: 16,
  director_line_height: 1.5,
  prose_font_size: 18,
  prose_line_height: 1.8,
  paragraph_spacing: 1.25,
  prose_font: "serif",
  ui_scale: 100,
  mobile_scale: "follow",
  motion: "subtle",
  easter_eggs_enabled: true,
  background_selected_ids: [],
  background_opacity: 0.1,
  background_blur: 0,
  background_dim: 0.35,
  background_saturation: 0.85,
  background_position: "center",
  background_fit: "cover",
  background_attachment: "fixed",
  background_rotation_seconds: 0,
  background_rotation_order: "ordered",
  background_fade_seconds: 1.2,
  background_pause_while_writing: true,
});

const READING_WIDTHS = {
  narrow: "740px",
  comfortable: "880px",
  wide: "1080px",
  full: "1280px",
};

const COMPOSER_HEIGHTS = {
  compact: [56, 160],
  comfortable: [80, 240],
  tall: [144, 420],
};

const PROSE_FONTS = {
  sans: "var(--sd-font-sans)",
  serif: "Georgia, Cambria, 'Times New Roman', serif",
};

export function normalizeUiSettings(value = {}) {
  return { ...DEFAULT_UI_SETTINGS, ...(value || {}) };
}

export function applyDisplaySettings(value = {}) {
  const settings = normalizeUiSettings(value);
  const root = document.documentElement;
  const composerHeights = COMPOSER_HEIGHTS[settings.composer_size] || [
    settings.composer_min_height,
    settings.composer_max_height,
  ];

  root.dataset.readingWidth = settings.reading_width;
  root.dataset.composerSize = settings.composer_size;
  root.dataset.mobileScale = settings.mobile_scale;
  root.dataset.motion = settings.motion;
  root.style.setProperty("--sd-reading-column-max", `min(${READING_WIDTHS[settings.reading_width] || READING_WIDTHS.comfortable}, calc(100vw - 2rem))`);
  root.style.setProperty("--sd-prose-size", `${settings.prose_font_size}px`);
  root.style.setProperty("--sd-prose-line-height", String(settings.prose_line_height));
  root.style.setProperty("--sd-paragraph-spacing", `${settings.paragraph_spacing}rem`);
  root.style.setProperty("--sd-font-story", PROSE_FONTS[settings.prose_font] || PROSE_FONTS.serif);
  root.style.setProperty("--sd-composer-min-height", `${composerHeights[0]}px`);
  root.style.setProperty("--sd-composer-max-height", `${composerHeights[1]}px`);
  root.style.setProperty("--sd-composer-font-size", `${settings.composer_font_size}px`);
  root.style.setProperty("--sd-director-line-height", String(settings.director_line_height));
  root.style.setProperty("--sd-ui-scale", String(settings.ui_scale / 100));
  return settings;
}
