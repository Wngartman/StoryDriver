# ComfyUI API Workflows

Place exported ComfyUI API workflow JSON files in this folder:

`D:\StoryDriver\backend\data\comfy_workflows`

StoryDriver scans this folder and shows the workflows in Image Settings. For many exported API workflows, StoryDriver can auto-detect:

- positive prompt node ID and input
- negative prompt node ID and input, if used
- seed node ID and input, if used
- output node ID, if needed

If auto-detection is confident, Image Settings shows an "Auto-detected mapping" section and lets you use it with one click. If detection is uncertain, choose the prompt node from the dropdowns. Use the collapsed Advanced mapping fields only when the dropdowns cannot identify the right node.

Negative prompts are optional. Some Z-Image and Z-Image Turbo workflows use only one positive prompt input; in that case set the negative prompt node to "None / not used."

ComfyUI owns the model, LoRAs, sampler, size, style, upscalers, and workflow complexity. StoryDriver only injects the selected scene image prompt and saves generated image history.

Real image generation requires ComfyUI to be reachable at the configured `COMFYUI_BASE_URL`, normally `http://localhost:8188`.

Current preferred workflow: `lonecat_zit_nsfw_8_0_1_api.json`.

- This file was extracted from a successful ComfyUI `/history` prompt for Lonecat's "ZIT NSFW 8.0.1" workflow.
- StoryDriver injects the positive scene prompt into node `1342` input `positive`.
- StoryDriver can inject a negative prompt into node `1317` input `negative`.
- StoryDriver can set the seed on node `1307` input `seed`.
- StoryDriver reads the final saved output from node `1704`.
- ComfyUI still owns models, LoRAs, sampler, size, detailers, upscalers, and save settings.

`image_z_image_turbo.json` is also exported API JSON, but it is a smaller direct Z-Image Turbo graph and is not the current preferred Lonecat workflow. ComfyUI Desktop's `Lonecat's ZIT NSFW 8.0.1.json` file is a normal UI workflow graph, not an API workflow by itself. StoryDriver cannot queue that UI graph directly.

The current ComfyUI Desktop workflow `Flux 2D & Klein_9b ver 5.0.3.json` was found in the Desktop workflow folder as a normal UI workflow graph. StoryDriver is ready to label and persist `Flux 2D / Klein` workflows, but it still needs an exported API workflow JSON before it can queue the graph through `/prompt`.

ComfyUI Desktop's `C:\Users\wngar\Documents\ComfyUI\user\default\workflows` folder stores Desktop UI workflow files. StoryDriver does not read those directly. Open the workflow in ComfyUI, export it in API workflow JSON format, then place the exported JSON in this folder.

Typical export flow:

1. Start ComfyUI Desktop and open the target workflow, such as `Flux 2D & Klein_9b ver 5.0.3`.
2. Use ComfyUI's API/dev export option for the currently loaded workflow. Depending on the Desktop build this may appear as "Export API", "Save API Format", or a developer-mode workflow export.
3. Save the exported API JSON into this folder.
4. Open StoryDriver Image Settings, refresh workflows, select the file, and click Auto-detect nodes.
5. If StoryDriver shows "Detected automatically," use the detected mapping and save config. Otherwise choose the prompt node from the dropdown and validate.

If Image Settings reports "Needs setup", the file was found but the StoryDriver sidecar config still needs prompt node IDs. If it reports "Invalid", the file is likely not API format or the configured node/input names do not exist.
