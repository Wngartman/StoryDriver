# UI Accessibility

## Responsive Layout

Desktop uses a 280 px sidebar or 68 px rail. Mobile uses a closable drawer and never renders the desktop rail. The writing column and composer reflow to the selected reading width. Automated acceptance covers 430x932, 390x844, 844x390, 1024x768, 1440x1000, and 1920x1080 with no horizontal overflow.

## Keyboard And Touch

Interactive icon controls have accessible names. Search, stories, settings, drawers, dialogs, and help are keyboard reachable. Escape clears/closes search, help, and overlays in context. Touch targets are approximately 40-44 px where space permits; mobile form controls stay at 16 px to avoid focus zoom. Browser pinch zoom remains enabled. Safe-area padding protects bottom controls.

Settings help is not hover-only: focus and touch open the same portal tooltip used by delayed hover. It remains within the viewport and closes on Escape or scroll. Destructive story deletion requires an explicit confirmation; there is no keyboard bypass.

## Motion

Motion supports Off, Subtle, and Full. Subtle is the default. Explicit Off wins, and browser `prefers-reduced-motion` suppresses nonessential transitions. Prose does not animate while reading. Background rotation has one visibility-aware timer and Easter eggs are disabled when motion is unavailable.

## Visual Contract

Themes share focus, text, surface, border, danger, and success tokens. Backgrounds remain below opaque/semitransparent reading and control surfaces. Compact headings and controls retain stable dimensions so status, hover, and loading changes do not shift the workspace.

## Remaining Manual Checks

Automation cannot prove physical iPhone lock-screen/background-audio continuity, trusted media-gesture recovery, subjective contrast preference, or long-form narrator pleasantness. Test those on the intended device and voice profile before treating them as human acceptance.
