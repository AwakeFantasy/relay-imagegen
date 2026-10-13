---
name: relay-imagegen
description: Generate or edit images through the configured OpenAI-compatible relay, with saved prompt files and non-secret run metadata. Use when the user wants relay image generation, reusable prompts, or repeatable local image output.
---

# Relay Imagegen

## Fast Path

Use the installed Skill's `scripts/relay_imagegen.py` for image generation and editing:

- Configuration comes from the Skill-root `config.json`.
- Saved prompts via `prompts/*.txt` and sidecar `prompt_snapshot`.
- Non-secret run metadata next to every successful output.
- Default size: `2560x1440`.
- High quality.
- Default model: `gpt-image-2.5-flare`; use the configured model unless the user requests another.
- Auto output path: `generated/<name>-YYYYMMDD-HHMMSS-2k.png`.
- Optional input downscaling with `--prepare-image` or `--max-input-edge`.

Agent rules:

- Do not open the configuration GUI during normal image generation. Open it only when the user asks to configure the relay or the Skill-root config is missing or invalid; continue after the user saves it.
- When opening the GUI, call `python $setup gui` using the installed Skill path below, never a path relative to the user's project directory.
- Use the Skill-root `config.json` as the only automatic config source.
- Keep setup to the GUI and generation to the CLI. Do not ask the user to edit JSON or expose relay implementation details during normal use.
- Invoke the installed Skill's `scripts/relay_imagegen.py` directly for image generation and editing.
- Before choosing a source, run `python $skill --list-sources`; it lists only source names, models, and enabled status.
- Honor an explicit source or model request. Otherwise prefer Sunburst when edit precision matters and Flare for fast, high-quality everyday generation; use the first enabled source when no better fit is configured.
- Pass the selected source with `--source <name>`. Never expose keys or relay URLs in user-facing text.
- Non-streaming requests may fail over in GUI order after transport, rate-limit, or 5xx failures. Do not retry ordinary 4xx errors.
- Use streaming only when the user asks to see previews during generation. On a stream failure, report the uncertain outcome and do not automatically retry another source.
- Do not pass `--output-dir` unless the user asks for a custom directory.
- Default size is `2560x1440` landscape. Omit `--size` only when the user does not request a different resolution or aspect ratio.
- If the user asks for 4K, 2K, horizontal/landscape, vertical/portrait, square, 16:9, 9:16, 1:1, wallpaper, avatar, or any explicit framing/aspect ratio, pass `--size` explicitly.
- Do not pass `--quality` or `--timeout` unless the user asks; defaults are already useful.
- Use `prompts/<short-name>.txt` for saved prompts in the current workspace.
- Use `generated/` as the default output location.
- Common sizes: 2K landscape `2560x1440`; 4K landscape `3840x2160`; 2K portrait `1440x2560`; 4K portrait `2160x3840`; square `2048x2048`.

Windows path setup:

```powershell
$skillDir = "$HOME/.codex/skills/relay-imagegen"
$setup = "$skillDir/scripts/setup.py"
$skill = "$skillDir/scripts/relay_imagegen.py"
```

Minimal generation:

```powershell
python $skill generate --prompt-file prompts/prompt.txt --name output --force
```

Minimal edit with references:

```powershell
python $skill edit --image C:/path/to/reference.jpg --prompt-file prompts/prompt.txt --name edit --prepare-image --force
```

Prefer `--prompt-file` over `--prompt` for saved/reusable prompts, long prompts, Chinese text, or prompts that should not appear in shell history. Successful runs copy the prompt text into the sidecar metadata as `prompt_snapshot`.

## Common Commands

Generation with default relay settings:

```powershell
python $skill generate `
  --prompt-file prompts/prompt.txt `
  --name output-2k `
  --force
```

If `--out` is omitted, the script writes a timestamped file under `--output-dir`, `RELAY_IMAGEGEN_OUTPUT_DIR`, or finally `generated`. For example: `generated/output-2k-20260526-203000-2k.png`.

Use `prompts/` for reusable prompt files in a project.

Edit with reference images:

```powershell
python $skill edit `
  --image C:/path/to/composition.png `
  --image C:/path/to/character.jpg `
  --prompt-file prompts/prompt.txt `
  --name final-2k `
  --force
```

For heavy reference images, prefer:

```powershell
python $skill edit `
  --image C:/path/to/reference.jpg `
  --prompt-file prompts/prompt.txt `
  --prepare-image `
  --max-input-edge 2048 `
  --timeout 900
```

## Options

- `--timeout <seconds>`: Cap how long the relay call can run. Default is `600`.
- `--output-dir <path>`: Directory for auto-named outputs when `--out` is omitted. Default is `generated`, or `RELAY_IMAGEGEN_OUTPUT_DIR` if set.
- `--prepare-image`: Downscale edit input images before upload. Default edge is `2048`; prepared copies are temporary and deleted after the run.
- `--max-input-edge <pixels>`: Downscale edit inputs to fit this max edge. Also enables image preparation.
- `--keep-prepared`: Keep prepared upload copies under `generated/relay_prepared/` for debugging.
- `--name <slug>`: Use this base name when `--out` is omitted.
- `--model <id>`: Override the configured model when requested. Presets include `gpt-image-2.5-flare`, `gpt-image-2.5-sunburst`, and `gpt-image-2`.
- `--source <name>`: Prefer a configured relay by name; list choices with `--list-sources`.
- `--stream`: Save streaming partial previews beside the final image; use only when requested.
- `--partial-images <0..3>`: Number of local previews in streaming mode; defaults to `1`.
- `--list-sources`: Print configured source names and models without credentials or URLs.
- `--dry-run`: Print the non-secret command shape without calling the relay.

The GUI can store multiple ordered relay sources. A compatible root config looks like this (users normally do not need to edit it):

```json
{
  "sources": [
    {"name": "primary", "base_url": "https://relay-a.example/v1", "api_key": "...", "model": "gpt-image-2.5-flare"},
    {"name": "backup", "base_url": "https://relay-b.example/v1", "api_key": "...", "model": "gpt-image-2.5-sunburst"}
  ]
}
```

The older single-source `{ "api_key": "...", "base_url": "...", "model": "..." }` shape remains supported. Failover is intentionally conservative: a timeout can happen after a relay has accepted a request, so switching sources may create a duplicate request or charge.

Image streaming is opt-in. Partial previews are saved as `*.partial-N.<format>` beside the final image, and the completed image keeps the requested output path. The relay must forward the image SSE events; a stream error may occur after generation has started, so the client does not automatically retry it through another source.

## Validation

After generation, report:

- Output path.
- Actual width and height.
- Whether the size matched the requested `--size`.
- Whether the call used `generate` or `edit`.
- Sidecar metadata path.

For successful calls the script writes a sibling sidecar file, for example `final-2k.meta.json`, containing non-secret run metadata such as model, size, prompt snapshot, input image paths, output dimensions, and elapsed time. It never includes the API key.

If the relay rejects a model, size, or endpoint, report the exact non-secret error summary and suggest the smallest next adjustment, such as testing `generate` before `edit`, checking `base_url`, or switching model only if the user asks.
