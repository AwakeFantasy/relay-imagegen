---
name: relay-imagegen
description: Generate or edit images through a local OpenAI-compatible relay or proxy endpoint, with saved prompt files and non-secret run metadata. Use when the user wants relay/proxy image generation, reusable or saved prompts, reuse of current Codex or ccswitch relay config, api_key.json/base_url config, or a repeatable local image workflow without persistent OPENAI_API_KEY environment variables.
---

# Relay Imagegen

## Fast Path

Use `scripts/relay_imagegen.py` directly. The defaults are tuned for low-thinking relay image runs:

- Relay/proxy config lookup without persistent system env vars.
- Saved prompts via `prompts/*.txt` and sidecar `prompt_snapshot`.
- Non-secret run metadata next to every successful output.
- Default size: `2560x1440`.
- High quality.
- Config lookup: `--config` first, then current Codex config/auth, then ccswitch, then private config files.
- Auto output path: `generated/<name>-YYYYMMDD-HHMMSS-2k.png`.
- Optional input downscaling with `--prepare-image` or `--max-input-edge`.

Agent rules:

- Do not run setup checks unless config lookup fails.
- Do not pass `--output-dir` unless the user asks for a custom directory.
- Do not pass `--size`, `--quality`, or `--timeout` unless the user asks; defaults are already useful.
- Use `prompts/<short-name>.txt` for saved prompts in the current workspace.
- Use `generated/` as the default output location.
- On Windows, avoid PowerShell ternary syntax; assign `$skill` with a plain path.
- For a user-requested 4K run, add only `--size 3840x2160` and name it accordingly.

Windows path setup:

```powershell
$skill = "$HOME/.codex/skills/relay-imagegen/scripts/relay_imagegen.py"
```

Minimal generation:

```powershell
python $skill generate --prompt-file prompts/prompt.txt --name output --force
```

Require current Codex config/auth instead of falling back:

```powershell
python $skill generate --from-codex --prompt-file prompts/prompt.txt --name output --force
```

Require the current ccswitch Codex provider instead of falling back:

```powershell
python $skill generate --from-ccswitch --prompt-file prompts/prompt.txt --name output --force
```

Minimal edit with references:

```powershell
python $skill edit --image C:/path/to/reference.jpg --prompt-file prompts/prompt.txt --name edit --prepare-image --force
```

Prefer `--prompt-file` over `--prompt` for saved/reusable prompts, long prompts, Chinese text, or prompts that should not appear in shell history. Successful runs copy the prompt text into the sidecar metadata as `prompt_snapshot`.

## Config

Use `scripts/relay_imagegen.py` for relay image generation or editing. It reads a private JSON config only at runtime, injects `OPENAI_API_KEY` and `OPENAI_BASE_URL` only into the child process that calls the bundled imagegen CLI, then verifies output dimensions when possible.

Default config lookup:

1. `--config <path>` if provided.
2. Current Codex config/auth from `~/.codex/config.toml` and `~/.codex/auth.json`, unless `--no-codex` is used.
3. Current ccswitch `codex` provider from `~/.cc-switch/cc-switch.db`, unless `--no-ccswitch` is used.
4. `RELAY_IMAGEGEN_CONFIG` if set.
5. `photo/api_key.json` under the current working directory.
6. `.secrets/image_api.json` under the current working directory.
7. `.secrets/relay_imagegen.json` under the current working directory.
8. This skill's private `.secrets/config.json`.
9. `%APPDATA%/relay-imagegen/config.json` on Windows.
10. `~/.config/relay-imagegen/config.json`.
11. `~/.relay-imagegen.json`.

Expected JSON fields:

```json
{
  "api_key": "...",
  "base_url": "https://relay.example/v1",
  "model": "gpt-image-2"
}
```

Accepted aliases:

- API key: `api_key`, `apiKey`, `key`, `token`, `openai_api_key`, `OPENAI_API_KEY`
- Base URL: `base_url`, `baseUrl`, `baseURL`, `api_base`, `endpoint`, `openai_base_url`, `OPENAI_BASE_URL`

Never print the API key or pass it as a command-line argument. Do not write it to user or system environment variables. Avoid committing the config file.

For the lowest-friction cross-project setup, create the skill-local private config:

```text
<skill>/relay-imagegen/.secrets/config.json
```

Use the setup helper when available:

```powershell
$skillDir = "$HOME/.codex/skills/relay-imagegen"
python (Join-Path $skillDir "scripts/setup.py") config --scope skill
python (Join-Path $skillDir "scripts/setup.py") --check
python (Join-Path $skillDir "scripts/setup.py") --check-codex
python (Join-Path $skillDir "scripts/setup.py") --check-ccswitch
```

For project-specific or shared setups, use one of these files instead:

```text
<project>/.secrets/image_api.json
%APPDATA%/relay-imagegen/config.json
~/.config/relay-imagegen/config.json
```

Add `.secrets/` to `.gitignore` when using project-local or skill-local config. Do not package or share a real config file with the skill.

## Common Commands

Generation with default relay settings:

```powershell
python $skill generate `
  --prompt-file prompts/prompt.txt `
  --name output-2k `
  --force
```

If `--out` is omitted, the script writes a timestamped file under `--output-dir`, `RELAY_IMAGEGEN_OUTPUT_DIR`, or finally `generated`. For example: `generated/output-2k-20260526-203000-2k.png`.

Use `prompts/` for reusable prompt files in a project. Do not create `photo/prompt.txt` just because the config example uses `photo/api_key.json`; if the current workspace is already named `photo`, that would produce awkward paths such as `photo/photo/prompt.txt`.

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

- `--config <path>`: Override config discovery for this run.
- `--from-codex`: Require current Codex config/auth from `~/.codex/config.toml` and `~/.codex/auth.json`; fail instead of falling back.
- `--no-codex`: Skip default Codex config/auth lookup.
- `--codex-config <path>`: Override the Codex `config.toml` path.
- `--codex-auth <path>`: Override the Codex `auth.json` path.
- `--from-ccswitch`: Require the current `codex` provider from `~/.cc-switch/cc-switch.db`; fail instead of falling back.
- `--no-ccswitch`: Skip default ccswitch lookup and use file config discovery.
- `--ccswitch-db <path>`: Override the ccswitch SQLite database path.
- `--timeout <seconds>`: Cap how long the relay call can run. Default is `600`.
- `--output-dir <path>`: Directory for auto-named outputs when `--out` is omitted. Default is `generated`, or `RELAY_IMAGEGEN_OUTPUT_DIR` if set.
- `--prepare-image`: Downscale edit input images before upload. Default edge is `2048`.
- `--max-input-edge <pixels>`: Downscale edit inputs to fit this max edge. Also enables image preparation.
- `--name <slug>`: Use this base name when `--out` is omitted.
- `--dry-run`: Print the non-secret command shape without calling the relay.

## Validation

After generation, report:

- Output path.
- Actual width and height.
- Whether the size matched the requested `--size`.
- Whether the call used `generate` or `edit`.
- Sidecar metadata path.

The wrapper filters the noisy `OPENAI_API_KEY is set.` line from child process output. For successful calls it writes a sibling sidecar file, for example `final-2k.meta.json`, containing non-secret run metadata: mode, model, size, quality, prompt file, prompt snapshot, input image paths, prepared image paths, output dimensions, elapsed seconds, config source/path, Codex or ccswitch provider name when used, and base URL. It must never include the API key.

If the relay rejects a model, size, or endpoint, report the exact non-secret error summary and suggest the smallest next adjustment, such as testing `generate` before `edit`, checking `base_url`, or switching model only if the user asks.
