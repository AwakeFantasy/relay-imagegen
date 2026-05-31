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
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit


DEFAULT_SIZE = "2560x1440"
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
CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
CODEX_CONFIG = CODEX_HOME / "config.toml"
CODEX_AUTH = CODEX_HOME / "auth.json"
SKILL_PRIVATE_CONFIG = SKILL_DIR / ".secrets" / "config.json"


def die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def default_config_path(include_skill_private: bool = True) -> Path | None:
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
            SKILL_PRIVATE_CONFIG if include_skill_private else None,
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


def extract_base_url_from_ccswitch_config(config: Any) -> str | None:
    if isinstance(config, dict):
        value = first_present(config, BASE_URL_FIELDS)
        return str(value) if value else None
    if not isinstance(config, str):
        return None
    for field in BASE_URL_FIELDS:
        match = re.search(rf"(?m)^\s*{re.escape(field)}\s*=\s*[\"']([^\"']+)[\"']", config)
        if match:
            return match.group(1)
    return None


def parse_toml(path: Path) -> dict[str, Any]:
    try:
        import tomllib
    except ModuleNotFoundError:
        tomllib = None
    if tomllib is not None:
        with path.open("rb") as f:
            return tomllib.load(f)
    return parse_codex_toml_fallback(path.read_text(encoding="utf-8"))


def parse_codex_toml_fallback(text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    section: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = [part.strip().strip('"').strip("'") for part in line.strip("[]").split(".")]
            current = data
            for part in section:
                current = current.setdefault(part, {})
            continue
        if "=" not in line:
            continue
        key, raw_value = [part.strip() for part in line.split("=", 1)]
        value = raw_value.strip().strip('"').strip("'")
        current = data
        for part in section:
            current = current.setdefault(part, {})
        current[key] = value
    return data


def load_codex_config(
    config_path_arg: str | None = None,
    auth_path_arg: str | None = None,
) -> tuple[dict[str, Any], str]:
    config_path = Path(config_path_arg) if config_path_arg else CODEX_CONFIG
    auth_path = Path(auth_path_arg) if auth_path_arg else CODEX_AUTH
    if not config_path.exists():
        die(f"Codex config not found: {config_path}")
    if not auth_path.exists():
        die(f"Codex auth not found: {auth_path}")
    try:
        config = parse_toml(config_path)
    except Exception as exc:
        die(f"Could not parse Codex config TOML: {exc}")
    try:
        auth = json.loads(auth_path.read_text(encoding="utf-8"))
    except Exception as exc:
        die(f"Could not parse Codex auth JSON: {exc}")

    provider_name = config.get("model_provider")
    providers = config.get("model_providers") if isinstance(config.get("model_providers"), dict) else {}
    provider = providers.get(provider_name) if provider_name else None
    if not isinstance(provider, dict):
        die("Codex config is missing the current model provider settings.")
    api_key = first_present(auth, API_KEY_FIELDS)
    base_url = first_present(provider, BASE_URL_FIELDS)
    image_model = first_present(provider, ("image_model", "imageModel", "image-model"))
    if not api_key:
        die("Codex auth is missing an API key.")
    if not base_url:
        die("Codex current model provider is missing a base_url.")
    return (
        {
            "api_key": api_key,
            "base_url": normalize_openai_base_url(str(base_url)),
            "model": image_model or "gpt-image-2",
            "codex_provider_name": provider_name,
        },
        f"codex:{config_path}:{auth_path}:{provider_name}",
    )


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
            "select url from provider_endpoints where app_type=? and provider_id=? order by id asc limit 1",
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

    config = settings.get("config")
    base_url = extract_base_url_from_ccswitch_config(config)
    if not base_url and endpoint:
        base_url = endpoint["url"]
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


def load_file_config(path_arg: str | None, include_skill_private: bool = True) -> tuple[dict[str, Any], str]:
    path = Path(path_arg) if path_arg else default_config_path(include_skill_private=include_skill_private)
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


def load_optional_skill_private_config() -> tuple[dict[str, Any], str] | None:
    if not SKILL_PRIVATE_CONFIG.exists():
        return None
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            return load_file_config(str(SKILL_PRIVATE_CONFIG))
    except SystemExit:
        return None


def load_config(
    path_arg: str | None,
    from_ccswitch: bool = False,
    from_codex: bool = False,
    no_codex: bool = False,
    no_ccswitch: bool = False,
    ccswitch_db: str | None = None,
    codex_config: str | None = None,
    codex_auth: str | None = None,
) -> tuple[dict[str, Any], str]:
    if path_arg:
        return load_file_config(path_arg)
    if from_codex or os.environ.get("RELAY_IMAGEGEN_FROM_CODEX") == "1":
        return load_codex_config(codex_config, codex_auth)
    use_codex = not no_codex and os.environ.get("RELAY_IMAGEGEN_NO_CODEX") != "1"
    use_ccswitch = not no_ccswitch and os.environ.get("RELAY_IMAGEGEN_NO_CCSWITCH") != "1"
    if from_ccswitch or os.environ.get("RELAY_IMAGEGEN_FROM_CCSWITCH") == "1":
        return load_ccswitch_config(ccswitch_db)
    skill_private_config = load_optional_skill_private_config()
    if skill_private_config:
        return skill_private_config
    try:
        if use_codex:
            with contextlib.redirect_stderr(io.StringIO()):
                return load_codex_config(codex_config, codex_auth)
    except SystemExit:
        pass
    try:
        if use_ccswitch:
            with contextlib.redirect_stderr(io.StringIO()):
                return load_ccswitch_config(ccswitch_db)
    except SystemExit:
        pass
    return load_file_config(None, include_skill_private=False)


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
    size_label = "2k" if args.size == "2560x1440" else "4k" if args.size == "3840x2160" else args.size
    return out_dir / f"{sanitize_stem(base)}-{timestamp}-{size_label}.png"


def prepared_image_path(prep_dir: Path, out: Path, image: Path, index: int) -> Path:
    prep_dir.mkdir(parents=True, exist_ok=True)
    return prep_dir / f"{out.stem}-input{index}-{image.stem}.jpg"


def prepare_images(args: argparse.Namespace, out: Path, prep_dir: Path | None = None, prepared_temporary: bool = False) -> list[dict[str, str | int | bool]]:
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
        dst = prepared_image_path(prep_dir or out.parent / "relay_prepared", out, src, index)
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
                "prepared_temporary": prepared_temporary,
                "original_width": original_size[0],
                "original_height": original_size[1],
                "used_width": used_size[0],
                "used_height": used_size[1],
                "max_input_edge": max_edge,
            }
        )
    return prepared


def images_for_metadata(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metadata_images = []
    for image in images:
        item = dict(image)
        if item.get("prepared") and item.get("prepared_temporary"):
            item.pop("used", None)
            item["prepared_copy"] = "temporary_deleted_after_run"
        metadata_images.append(item)
    return metadata_images


def filter_process_output(text: str) -> str:
    filtered = []
    for line in text.splitlines():
        if line.strip() == "OPENAI_API_KEY is set.":
            continue
        filtered.append(line)
    return "\n".join(filtered)


def prompt_snapshot(args: argparse.Namespace) -> str | None:
    if args.prompt is not None:
        return args.prompt
    if not args.prompt_file:
        return None
    try:
        return Path(args.prompt_file).read_text(encoding="utf-8")
    except Exception as exc:
        return f"<could not read prompt file: {exc}>"


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
        "prompt_snapshot": prompt_snapshot(args),
        "images": images_for_metadata(images),
        "config_path": config_path,
        "config_source": "codex" if str(config_path).startswith("codex:") else "ccswitch" if str(config_path).startswith("ccswitch:") else "file",
        "codex_provider": cfg.get("codex_provider_name"),
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
    parser.add_argument("--from-codex", action="store_true", help="Require the current Codex config/auth provider.")
    parser.add_argument("--no-codex", action="store_true", help="Skip default Codex config/auth lookup.")
    parser.add_argument("--codex-config", help="Override the Codex config.toml path.")
    parser.add_argument("--codex-auth", help="Override the Codex auth.json path.")
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
    parser.add_argument("--keep-prepared", action="store_true", help="Keep prepared upload copies under generated/relay_prepared.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg, config_path = load_config(
        args.config,
        args.from_ccswitch,
        args.from_codex,
        args.no_codex,
        args.no_ccswitch,
        args.ccswitch_db,
        args.codex_config,
        args.codex_auth,
    )
    out = Path(args.out) if args.out else default_output_path(args)
    args.out = str(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    needs_prepared_images = args.mode == "edit" and args.image and (args.prepare_image or args.max_input_edge)
    temp_context = (
        contextlib.nullcontext(None)
        if not needs_prepared_images or args.keep_prepared
        else tempfile.TemporaryDirectory(prefix="relay-imagegen-")
    )
    with temp_context as temp_dir:
        prep_dir = out.parent / "relay_prepared" if args.keep_prepared and needs_prepared_images else Path(temp_dir) if temp_dir else None
        images = prepare_images(args, out, prep_dir, prepared_temporary=bool(needs_prepared_images and not args.keep_prepared))

        env = os.environ.copy()
        env["OPENAI_API_KEY"] = str(cfg["api_key"])
        env["OPENAI_BASE_URL"] = str(cfg["base_url"])

        print(f"MODE={args.mode}")
        print(f"MODEL={args.model or cfg.get('model', 'gpt-image-2')}")
        print(f"SIZE={args.size}")
        print(f"OUT={out}")
        print(f"TIMEOUT={args.timeout}")
        if cfg.get("codex_provider_name"):
            print(f"CODEX_PROVIDER={cfg['codex_provider_name']}")
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
