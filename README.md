# Relay Imagegen

Relay Imagegen is a Codex skill for generating or editing images through an
OpenAI-compatible relay or proxy endpoint without storing `OPENAI_API_KEY` as a
persistent system environment variable.

It is designed for low-friction 4K image runs:

- Defaults to native 4K output: `3840x2160`.
- Defaults to `gpt-image-2` and `quality=high`.
- Writes results to `generated/`.
- Writes a non-secret `.meta.json` sidecar next to each output.
- Can read the current ccswitch Codex provider automatically.
- Can also use private JSON config files for users without ccswitch.
- Can downscale reference images before upload.

## Requirements

- Codex with the bundled `imagegen` system skill installed.
- Python 3.10+.
- An OpenAI-compatible image relay/proxy that supports the image model you use.
- Optional: ccswitch, if you want Relay Imagegen to read your current Codex
  provider automatically.
- Optional: Pillow, for input image preparation and output dimension checks.

Relay Imagegen is currently a wrapper around Codex's bundled image generation
CLI:

```text
~/.codex/skills/.system/imagegen/scripts/image_gen.py
```

## Install

Copy this folder into your Codex skills directory:

```text
~/.codex/skills/relay-imagegen
```

On Windows this is usually:

```text
C:\Users\<you>\.codex\skills\relay-imagegen
```

After installation, Codex can trigger the skill when you mention relay image
generation, ccswitch, `api_key.json`, 4K image output, or the skill name
`relay-imagegen`.

## Quick Start

Create a prompt file in your project:

```text
prompts/test.txt
```

Then run:

```powershell
$skill = "$HOME/.codex/skills/relay-imagegen/scripts/relay_imagegen.py"
python $skill generate --prompt-file prompts/test.txt --name test --force
```

By default, output is written to:

```text
generated/test-YYYYMMDD-HHMMSS-4k.png
generated/test-YYYYMMDD-HHMMSS-4k.meta.json
```

Use `--dry-run` first if you only want to check the command shape:

```powershell
python $skill generate --prompt-file prompts/test.txt --name test --dry-run
```

## Configuration

Relay Imagegen supports two configuration styles:

1. ccswitch provider discovery.
2. Private JSON config files.

### Default Lookup Order

When `--config` is not provided, `relay_imagegen.py` checks:

1. Current ccswitch `codex` provider from `~/.cc-switch/cc-switch.db`.
2. `RELAY_IMAGEGEN_CONFIG`.
3. `photo/api_key.json` under the current project.
4. `.secrets/image_api.json` under the current project.
5. `.secrets/relay_imagegen.json` under the current project.
6. This skill's private `.secrets/config.json`.
7. `%APPDATA%/relay-imagegen/config.json` on Windows.
8. `~/.config/relay-imagegen/config.json`.
9. `~/.relay-imagegen.json`.

Use `--config <path>` to override all default lookup.

Use `--no-ccswitch` to skip ccswitch and use only file config discovery.

Use `--from-ccswitch` to require ccswitch; if ccswitch cannot be read, the
command fails instead of falling back to a file.

### ccswitch Setup

If you already use ccswitch for Codex, Relay Imagegen can read the current Codex
provider directly.

Check what will be used:

```powershell
python ~/.codex/skills/relay-imagegen/scripts/setup.py --check
```

or explicitly check ccswitch:

```powershell
python ~/.codex/skills/relay-imagegen/scripts/setup.py --check-ccswitch
```

Expected output looks like this:

```text
CCSWITCH_DB=C:\Users\you\.cc-switch\cc-switch.db
CCSWITCH_PROVIDER=Your Provider
API_KEY=sk-...xxxx
BASE_URL=https://relay.example/v1
MODEL=gpt-image-2
```

The key is always redacted.

By default the script reads:

```text
~/.cc-switch/cc-switch.db
```

You can override it:

```powershell
python ~/.codex/skills/relay-imagegen/scripts/setup.py --check-ccswitch --ccswitch-db C:/path/to/cc-switch.db
python ~/.codex/skills/relay-imagegen/scripts/relay_imagegen.py generate --ccswitch-db C:/path/to/cc-switch.db --prompt-file prompts/test.txt
```

### JSON Config Setup

If you do not use ccswitch, create a private config file.

Recommended user-level setup:

```powershell
python ~/.codex/skills/relay-imagegen/scripts/setup.py config --scope user
```

This writes:

```text
%APPDATA%/relay-imagegen/config.json
```

For a skill-local setup:

```powershell
python ~/.codex/skills/relay-imagegen/scripts/setup.py config --scope skill
```

This writes:

```text
~/.codex/skills/relay-imagegen/.secrets/config.json
```

The setup helper asks for:

```text
Relay base_url:
Model [gpt-image-2]:
API key (hidden):
```

You can also create the JSON manually:

```json
{
  "api_key": "sk-...",
  "base_url": "https://relay.example/v1",
  "model": "gpt-image-2"
}
```

Accepted API key field aliases:

```text
api_key, apiKey, key, token, openai_api_key, OPENAI_API_KEY
```

Accepted base URL field aliases:

```text
base_url, baseUrl, baseURL, api_base, endpoint, openai_base_url, OPENAI_BASE_URL
```

Never commit real config files. `.secrets/` is ignored by this repository.

## Usage

### Generate an Image

```powershell
$skill = "$HOME/.codex/skills/relay-imagegen/scripts/relay_imagegen.py"
python $skill generate --prompt-file prompts/test.txt --name test --force
```

### Edit with Reference Images

```powershell
$skill = "$HOME/.codex/skills/relay-imagegen/scripts/relay_imagegen.py"
python $skill edit `
  --image C:/path/to/reference.jpg `
  --prompt-file prompts/edit.txt `
  --name edit-test `
  --prepare-image `
  --force
```

### Require ccswitch

```powershell
python $skill generate --from-ccswitch --prompt-file prompts/test.txt --name test --force
```

### Skip ccswitch

```powershell
python $skill generate --no-ccswitch --prompt-file prompts/test.txt --name test --force
```

### Use an Explicit Config File

```powershell
python $skill generate --config .secrets/image_api.json --prompt-file prompts/test.txt --name test --force
```

### Choose Output Directory

```powershell
python $skill generate --prompt-file prompts/test.txt --output-dir my-images --name test --force
```

### Set Timeout

```powershell
python $skill generate --prompt-file prompts/test.txt --timeout 900 --name slow-test --force
```

### Downscale Reference Images

```powershell
python $skill edit `
  --image C:/path/to/large-reference.jpg `
  --prompt-file prompts/edit.txt `
  --max-input-edge 2048 `
  --name prepared-edit `
  --force
```

`--max-input-edge` also enables image preparation.

## Important Options

```text
mode                 generate or edit
--prompt-file        prompt text file, recommended for long prompts
--prompt             inline prompt, useful for quick tests
--image              reference image for edit mode, repeatable
--name               output filename stem
--out                exact output path
--output-dir         output directory when --out is omitted, default generated
--size               output size, default 3840x2160
--quality            image quality, default high
--timeout            process timeout in seconds, default 600
--prepare-image      downscale edit inputs before upload
--max-input-edge     max reference image edge, default 2048 when preparing
--dry-run            print the non-secret command shape and exit
--force              pass overwrite behavior to the bundled imagegen CLI
--config             explicit JSON config path
--from-ccswitch      require ccswitch current Codex provider
--no-ccswitch        skip ccswitch lookup
--ccswitch-db        override ccswitch SQLite path
```

## Output and Metadata

If `--out` is omitted, Relay Imagegen creates a timestamped output:

```text
generated/<name>-YYYYMMDD-HHMMSS-4k.png
```

It also writes:

```text
generated/<name>-YYYYMMDD-HHMMSS-4k.meta.json
```

The sidecar includes non-secret metadata:

- mode
- model
- size
- quality
- prompt file
- input image paths
- prepared image paths
- output dimensions when Pillow is available
- elapsed seconds
- config source
- ccswitch provider name when used
- base URL without query string

It must not include API keys.

## Security Notes

- Do not pass API keys on the command line.
- Do not store keys in persistent system environment variables if that triggers
  warnings in your local tooling.
- Prefer ccswitch discovery or private JSON config files.
- Keep `.secrets/` out of Git.
- The wrapper injects `OPENAI_API_KEY` and `OPENAI_BASE_URL` only into the child
  process that runs the bundled imagegen CLI.
- The wrapper filters the bundled CLI's noisy `OPENAI_API_KEY is set.` line.

## Troubleshooting

### `CONFIG=missing`

Run:

```powershell
python ~/.codex/skills/relay-imagegen/scripts/setup.py --check-ccswitch
```

If ccswitch is unavailable, create a file config:

```powershell
python ~/.codex/skills/relay-imagegen/scripts/setup.py config --scope user
```

### ccswitch Finds a Base URL Without `/v1`

Relay Imagegen normalizes root endpoints automatically. For example:

```text
https://relay.example
```

becomes:

```text
https://relay.example/v1
```

If your relay needs a different path, use a JSON config with the exact
`base_url`.

### Output Size Mismatch

The default requested size is:

```text
3840x2160
```

If the relay or model does not support that size, the command may fail or the
dimension check may reject the output. Try a supported size only after checking
your relay's model capabilities.

### Large Reference Images

Use:

```powershell
--prepare-image
```

or:

```powershell
--max-input-edge 2048
```

to create prepared upload copies under `generated/relay_prepared/`.

## Repository Layout

```text
relay-imagegen/
  SKILL.md
  README.md
  agents/
    openai.yaml
  scripts/
    relay_imagegen.py
    setup.py
```

Private files such as `.secrets/config.json` are intentionally not part of the
repository.
