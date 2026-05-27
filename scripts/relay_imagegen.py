#!/usr/bin/env python3
"""Run the bundled imagegen CLI through a relay config without persistent env vars."""

from __future__ import annotations

import argparse
import contextlib
from datetime import datetime
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit


DEFAULT_SIZE = "3840x2160"
DEFAULT_QUALITY = "high"
DEFAULT_OUT_DIR = Path("generated")
DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_PREPARED_EDGE = 2048
SKILL_DIR = Path(__file__).resolve().parents[1]

API_KEY_FIELDS = ("api_key", "apiKey", "key", "token", "openai_api_key", "OPENAI_API_KEY")
BASE_URL_FIELDS = (
    "base_url",
    "baseUrl",
    "baseURL",
    "api_base",
    "endpoint",
    "openai_base_url",
    "OPENAI_BASE_URL",
)
CCSWITCH_DB = Path.home() / ".cc-switch" / "cc-switch.db"


def die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def default_config_path() -> Path | None:
    env_path = os.environ.get("RELAY_IMAGEGEN_CONFIG")
    candidates = []
    if env_path:
        candidates.append(Path(env_path))
    appdata = os.environ.get("APPDATA")
    candidates.extend(
        [
            Path.cwd() / "photo" / "api_key.json",
            Path.cwd() / ".secrets" / "image_api.json",
            Path.cwd() / ".secrets" / "relay_imagegen.json",
            SKILL_DIR / ".secrets" / "config.json",
            Path(appdata) / "relay-imagegen" / "config.json" if appdata else None,
            Path.home() / ".config" / "relay-imagegen" / "config.json",
            Path.home() / ".relay-imagegen.json",
        ]
    )
    for path in candidates:
        if path and path.exists():
            return path
    return None


def first_present(data: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if data.get(name):
            return data[name]
    return None


def normalize_openai_base_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme and parsed.netloc and parsed.path in {"", "/"}:
        return urlunsplit((parsed.scheme, parsed.netloc, "/v1", parsed.query, parsed.fragment))
    return url


def load_ccswitch_config(db_path_arg: str | None = None, app_type: str = "codex") -> tuple[dict[str, Any], str]:
    db_path = Path(db_path_arg) if db_path_arg else CCSWITCH_DB
    if not db_path.exists():
        die(f"ccswitch database not found: {db_path}")
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        provider = con.execute(
            "select id, name, settings_config from providers where app_type=? and is_current=1 limit 1",
            (app_type,),
        ).fetchone()
        if provider is None:
            die(f"No current ccswitch provider found for app_type={app_type!r}.")
        settings = json.loads(provider["settings_config"] or "{}")
        auth = settings.get("auth") if isinstance(settings.get("auth"), dict) else {}
        api_key = first_present(auth, API_KEY_FIELDS)
        endpoint = con.execute(
            "select url from provider_endpoints where app_type=? and provider_id=? order by id desc limit 1",
            (app_type, provider["id"]),
        ).fetchone()
    except sqlite3.Error as exc:
        die(f"Could not read ccswitch database: {exc}")
    except json.JSONDecodeError as exc:
        die(f"Could not parse ccswitch provider settings: {exc}")
    finally:
        try:
            con.close()
        except Exception:
            pass

    base_url = endpoint["url"] if endpoint else None
    config = settings.get("config")
    if not base_url and isinstance(config, dict):
        base_url = first_present(config, BASE_URL_FIELDS)
    if not api_key:
        die("Current ccswitch provider is missing an API key in settings_config.auth.")
    if not base_url:
        die("Current ccswitch provider is missing a usable endpoint URL.")
    base_url = normalize_openai_base_url(str(base_url))
    return (
        {
            "api_key": api_key,
            "base_url": base_url,
            "model": settings.get("model") or "gpt-image-2",
            "ccswitch_provider_id": provider["id"],
            "ccswitch_provider_name": provider["name"],
        },
        f"ccswitch:{db_path}:{app_type}:{provider['id']}",
    )


def load_file_config(path_arg: str | None) -> tuple[dict[str, Any], str]:
    path = Path(path_arg) if path_arg else default_config_path()
    if path is None:
        die("No config found. Pass --config, create a private config, or use ccswitch.")
    if not path.exists():
        die(f"Config not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        die(f"Could not parse config JSON: {exc}")

    api_key = first_present(data, API_KEY_FIELDS)
    base_url = first_present(data, BASE_URL_FIELDS)
    if not api_key:
        die(f"Config JSON is missing an API key field. Supported: {', '.join(API_KEY_FIELDS)}.")
    if not base_url:
        die(f"Config JSON is missing a base URL field. Supported: {', '.join(BASE_URL_FIELDS)}.")
    data["api_key"] = api_key
    data["base_url"] = base_url
    data.setdefault("model", "gpt-image-2")
    return data, str(path)


def load_config(
    path_arg: str | None,
    from_ccswitch: bool = False,
    no_ccswitch: bool = False,
    ccswitch_db: str | None = None,
) -> tuple[dict[str, Any], str]:
    if path_arg:
        return load_file_config(path_arg)
    use_ccswitch = not no_ccswitch and os.environ.get("RELAY_IMAGEGEN_NO_CCSWITCH") != "1"
    if from_ccswitch or os.environ.get("RELAY_IMAGEGEN_FROM_CCSWITCH") == "1":
        return load_ccswitch_config(ccswitch_db)
    try:
        if use_ccswitch:
            with contextlib.redirect_stderr(io.StringIO()):
                return load_ccswitch_config(ccswitch_db)
    except SystemExit:
        pass
    return load_file_config(None)


def bundled_cli_path() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    cli = codex_home / "skills" / ".system" / "imagegen" / "scripts" / "image_gen.py"
    if not cli.exists():
        die(f"Bundled imagegen CLI not found: {cli}")
    return cli


def parse_size(size: str) -> tuple[int, int] | None:
    if "x" not in size:
        return None
    left, right = size.lower().split("x", 1)
    if not left.isdigit() or not right.isdigit():
        return None
    return int(left), int(right)


def verify_dimensions(path: Path, requested_size: str) -> dict[str, Any]:
    requested = parse_size(requested_size)
    if not path.exists():
        die(f"Expected output was not written: {path}")
    try:
        from PIL import Image
    except Exception:
        print(f"Wrote {path}")
        print("Dimension check skipped: Pillow is not installed.")
        return {"output": str(path), "bytes": path.stat().st_size, "size_ok": None}

    with Image.open(path) as img:
        width, height = img.size
    size_ok = requested is None or (width, height) == requested
    print(f"OUTPUT={path}")
    print(f"WIDTH={width}")
    print(f"HEIGHT={height}")
    print(f"BYTES={path.stat().st_size}")
    if not size_ok:
        die(f"Output dimensions {width}x{height} did not match requested {requested_size}.")
    if requested:
        print(f"SIZE_OK={requested_size}")
    return {
        "output": str(path),
        "width": width,
        "height": height,
        "bytes": path.stat().st_size,
        "requested_size": requested_size,
        "size_ok": size_ok,
    }


def sanitize_stem(text: str) -> str:
    allowed = []
    for char in text.lower():
        if char.isalnum():
            allowed.append(char)
        elif char in {"-", "_", " ", "."}:
            allowed.append("-")
    stem = "".join(allowed).strip("-")
    while "--" in stem:
        stem = stem.replace("--", "-")
    return stem[:48] or "relay-image"


def default_output_path(args: argparse.Namespace) -> Path:
    out_dir = Path(args.output_dir or os.environ.get("RELAY_IMAGEGEN_OUTPUT_DIR", DEFAULT_OUT_DIR))
    if args.name:
        base = args.name
    elif args.prompt_file:
        base = Path(args.prompt_file).stem
    else:
        base = f"relay-{args.mode}"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    size_label = "4k" if args.size == "3840x2160" else args.size
    return out_dir / f"{sanitize_stem(base)}-{timestamp}-{size_label}.png"


def prepared_image_path(out: Path, image: Path, index: int) -> Path:
    prep_dir = out.parent / "relay_prepared"
    prep_dir.mkdir(parents=True, exist_ok=True)
    return prep_dir / f"{out.stem}-input{index}-{image.stem}.jpg"


def prepare_images(args: argparse.Namespace, out: Path) -> list[dict[str, str | int]]:
    if args.mode != "edit" or not args.image:
        return []
    if not args.prepare_image and not args.max_input_edge:
        return [{"original": image, "used": image, "prepared": False} for image in args.image]

    max_edge = args.max_input_edge or DEFAULT_PREPARED_EDGE
    try:
        from PIL import Image
    except Exception:
        die("Image preparation requires Pillow. Install Pillow or omit --prepare-image/--max-input-edge.")

    prepared = []
    for index, raw in enumerate(args.image, start=1):
        src = Path(raw)
        if not src.exists():
            die(f"Image file not found: {src}")
        dst = prepared_image_path(out, src, index)
        with Image.open(src) as img:
            original_size = img.size
            img = img.convert("RGB")
            img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            img.save(dst, "JPEG", quality=92, optimize=True)
            used_size = img.size
        prepared.append(
            {
                "original": str(src),
                "used": str(dst),
                "prepared": True,
                "original_width": original_size[0],
                "original_height": original_size[1],
                "used_width": used_size[0],
                "used_height": used_size[1],
                "max_input_edge": max_edge,
            }
        )
    return prepared


def filter_process_output(text: str) -> str:
    filtered = []
    for line in text.splitlines():
        if line.strip() == "OPENAI_API_KEY is set.":
            continue
        filtered.append(line)
    return "\n".join(filtered)


def write_sidecar(
    out: Path,
    args: argparse.Namespace,
    cfg: dict[str, Any],
    config_path: str,
    images: list[dict[str, Any]],
    dimensions: dict[str, Any],
    elapsed: float,
) -> Path:
    sidecar = out.with_suffix(".meta.json")
    meta = {
        "mode": args.mode,
        "model": args.model or cfg.get("model", "gpt-image-2"),
        "size": args.size,
        "quality": args.quality,
        "output_format": args.output_format or "png",
        "output": str(out),
        "dimensions": dimensions,
        "prompt": args.prompt,
        "prompt_file": args.prompt_file,
        "images": images,
        "config_path": config_path,
        "config_source": "ccswitch" if str(config_path).startswith("ccswitch:") else "file",
        "ccswitch_provider": cfg.get("ccswitch_provider_name"),
        "base_url": str(cfg.get("base_url", "")).split("?")[0],
        "elapsed_seconds": round(elapsed, 2),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    sidecar.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"META={sidecar}")
    return sidecar


def build_command(args: argparse.Namespace, cfg: dict[str, Any], images: list[dict[str, Any]]) -> list[str]:
    cmd = [
        sys.executable,
        str(bundled_cli_path()),
        args.mode,
        "--model",
        str(args.model or cfg.get("model", "gpt-image-2")),
        "--size",
        args.size,
        "--quality",
        args.quality,
        "--out",
        str(args.out),
    ]
    if args.prompt_file:
        cmd.extend(["--prompt-file", args.prompt_file])
    elif args.prompt:
        cmd.extend(["--prompt", args.prompt])
    else:
        die("Use --prompt-file or --prompt.")

    if args.output_format:
        cmd.extend(["--output-format", args.output_format])
    if args.force:
        cmd.append("--force")
    if args.mode == "edit":
        if not images:
            die("edit mode requires at least one --image.")
        for image in images:
            cmd.extend(["--image", str(image["used"])])
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(description="Relay wrapper for bundled image generation.")
    parser.add_argument("mode", choices=["generate", "edit"])
    parser.add_argument("--config")
    parser.add_argument("--from-ccswitch", action="store_true", help="Require the current Codex provider from ccswitch.")
    parser.add_argument("--no-ccswitch", action="store_true", help="Skip default ccswitch lookup and use file config.")
    parser.add_argument("--ccswitch-db", help="Override the ccswitch SQLite database path.")
    parser.add_argument("--model")
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    parser.add_argument("--image", action="append")
    parser.add_argument("--size", default=DEFAULT_SIZE)
    parser.add_argument("--quality", default=DEFAULT_QUALITY)
    parser.add_argument("--output-format")
    parser.add_argument("--out")
    parser.add_argument("--output-dir")
    parser.add_argument("--name")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--prepare-image", action="store_true")
    parser.add_argument("--max-input-edge", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg, config_path = load_config(args.config, args.from_ccswitch, args.no_ccswitch, args.ccswitch_db)
    out = Path(args.out) if args.out else default_output_path(args)
    args.out = str(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    images = prepare_images(args, out)

    env = os.environ.copy()
    env["OPENAI_API_KEY"] = str(cfg["api_key"])
    env["OPENAI_BASE_URL"] = str(cfg["base_url"])

    print(f"MODE={args.mode}")
    print(f"MODEL={args.model or cfg.get('model', 'gpt-image-2')}")
    print(f"SIZE={args.size}")
    print(f"OUT={out}")
    print(f"TIMEOUT={args.timeout}")
    if cfg.get("ccswitch_provider_name"):
        print(f"CCSWITCH_PROVIDER={cfg['ccswitch_provider_name']}")

    cmd = build_command(args, cfg, images)
    if args.dry_run:
        redacted = ["<python>", *cmd[1:]]
        print("DRY_RUN=1")
        print("COMMAND=" + " ".join(redacted))
        return 0

    started = time.time()
    try:
        result = subprocess.run(
            cmd,
            env=env,
            text=True,
            capture_output=True,
            timeout=args.timeout,
        )
    except subprocess.TimeoutExpired:
        die(f"Image generation timed out after {args.timeout}s.", code=124)

    stdout = filter_process_output(result.stdout or "")
    stderr = filter_process_output(result.stderr or "")
    if stdout:
        print(stdout)
    if stderr:
        print(stderr, file=sys.stderr)
    if result.returncode != 0:
        return result.returncode
    dimensions = verify_dimensions(out, args.size)
    write_sidecar(out, args, cfg, config_path, images, dimensions, time.time() - started)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
