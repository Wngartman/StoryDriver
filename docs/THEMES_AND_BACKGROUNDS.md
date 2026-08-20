# Themes And Backgrounds

## Shared Theme Contract

Every retained theme uses the same layout and display settings. Themes change tokens for surfaces, text, borders, accents, focus, status, shadows, radius, and workspace overlay; they do not change feature availability or pipeline behavior. Reading width, prose typography, composer sizing, UI/mobile scale, and motion are shared backend UI settings.

## Local Background Library

The canonical folder is:

```text
D:\StoryDriver\backend\data\assets\backgrounds
```

Use Settings -> Appearance -> Workspace Backgrounds to upload, rename, remove, select, or refresh images. Files may also be dropped directly into the folder before Refresh Backgrounds. PNG, JPG/JPEG, and WebP are accepted after header, file-size, and dimension validation. Executables, malformed images, unsupported formats, and files over 25 MB are rejected.

Background settings include multiple selection, opacity, blur, dim overlay, saturation, Cover/Contain/Fill, position, fixed/scroll attachment, ordered/random rotation, a safe custom interval, fade duration, and pause while writing. Rotation is off by default. `None` clears selection without deleting library files.

## Rendering And Performance

The image layer is clipped to the crosshatched workspace behind the prose feed and composer. It is never rendered over the sidebar, scene surface, settings, or narration controls. The frontend preloads current and next images only, uses one rotation timer, pauses the timer while the document is hidden, cancels stale transitions, and removes fades when motion is Off or reduced motion is requested.

Deletion removes only the selected library file and its local metadata. Missing files disappear after refresh and stale selected IDs are ignored. User backgrounds are data: back them up before deleting or restoring a database/UI-settings snapshot.

## Rollback

The implementation checkpoint is tag `polish_theme_backgrounds_20260715`. Revert its code with `git -c safe.directory=D:/StoryDriver revert 81c9f17`. Restore UI settings from the product-polish backup only when the saved selection itself must also be reverted.
