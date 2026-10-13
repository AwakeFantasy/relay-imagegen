#!/usr/bin/env python3
"""Generate or edit images through an OpenAI-compatible relay."""

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
import sys
import tempfile
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    from scripts.image_client import ImageClient
except ModuleNotFoundError:
    from image_client import ImageClient


DEFAULT_SIZE = "2560x1440"
DEFAULT_QUALITY = "high"
DEFAULT_OUT_DIR = Path("generated")
DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_PREPARED_EDGE = 2048
DEFAULT_MODEL = "gpt-image-2.5-flare"
SKILL_DIR = Path(__file__).resolve().parents[1]
ROOT_CONFIG = SKILL_DIR / "config.json"

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
    appdata = os.environ.get("APPDATA")
    candidates.extend(
        [
            ROOT_CONFIG,
            Path.cwd() / "photo" / "api_key.json",
            Path.cwd() / ".secrets" / "image_api.json",
            Path.cwd() / ".secrets" / "relay_imagegen.json",
            SKILL_PRIVATE_CONFIG if include_skill_private else None,
            Path(appdata) / "relay-imagegen" / "config.json" if appdata else None,
            Path.home() / ".config" / "relay-imagegen" / "config.json",
            Path.home() / ".relay-imagegen.json",
            Path(env_path) if env_path else None,
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


def source_name(source: dict[str, Any], index: int) -> str:
    return str(source.get("name") or f"relay-{index + 1}").strip() or f"relay-{index + 1}"


def normalize_sources(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize legacy single-source and new ordered-source configs."""
    raw_sources = data.get("sources")
    if raw_sources is None:
        raw_sources = [data]
    if not isinstance(raw_sources, list) or not raw_sources:
        die("Config 'sources' must be a non-empty array.")

    sources = []
    for index, raw in enumerate(raw_sources):
        if not isinstance(raw, dict):
            die(f"Config source #{index + 1} must be an object.")
        api_key = first_present(raw, API_KEY_FIELDS) or first_present(data, API_KEY_FIELDS)
        base_url = first_present(raw, BASE_URL_FIELDS) or first_present(data, BASE_URL_FIELDS)
        if not api_key:
            die(f"Config source #{index + 1} is missing an API key.")
        if not base_url:
            die(f"Config source #{index + 1} is missing a base URL.")
        source = {
            "name": source_name(raw, index),
            "api_key": str(api_key),
            "base_url": normalize_openai_base_url(str(base_url)),
            "enabled": raw.get("enabled", True) is not False,
        }
        model = first_present(raw, ("model", "image_model", "imageModel")) or data.get("model")
        if model:
            source["model"] = str(model)
        sources.append(source)
    return sources


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
            "model": image_model or "gpt-image-2.5-flare",
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
            "model": settings.get("model") or "gpt-image-2.5-flare",
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

    sources = normalize_sources(data)
    data["sources"] = sources
    data["api_key"] = sources[0]["api_key"]
    data["base_url"] = sources[0]["base_url"]
    data.setdefault("model", DEFAULT_MODEL)
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
    if ROOT_CONFIG.exists():
        return load_file_config(str(ROOT_CONFIG))
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
    extension = {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}[args.output_format or "png"]
    return out_dir / f"{sanitize_stem(base)}-{timestamp}-{size_label}{extension}"


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
    route: dict[str, Any] | None = None,
) -> Path:
    sidecar = out.with_suffix(".meta.json")
    meta = {
        "mode": args.mode,
        "model": args.model or cfg.get("model", DEFAULT_MODEL),
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
        "route": route or {},
        "elapsed_seconds": round(elapsed, 2),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    sidecar.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"META={sidecar}")
    return sidecar


def is_retryable_relay_error(exc: BaseException) -> bool:
    """Only fail over on transport, rate-limit, or server-side failures."""
    if isinstance(exc, (FileNotFoundError, PermissionError, IsADirectoryError)):
        return False
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None) or getattr(exc, "code", None)
    if isinstance(status, int):
        return status == 429 or 500 <= status < 600
    message = str(exc).lower()
    return any(token in message for token in ("timed out", "timeout", "connection", "connect", "dns"))


def safe_error_summary(exc: BaseException, source: dict[str, Any]) -> str:
    message = str(exc)[:300]
    api_key = str(source.get("api_key") or "")
    if api_key:
        message = message.replace(api_key, "<redacted>")
    return message


def redact_error_text(text: str, sources: list[dict[str, Any]]) -> str:
    for source in sources:
        api_key = str(source.get("api_key") or "")
        if api_key:
            text = text.replace(api_key, "<redacted>")
    return text[:500]


def request_with_failover(
    *,
    sources: list[dict[str, Any]],
    preferred_source: str | None,
    explicit_model: str | None,
    mode: str,
    prompt: str,
    size: str,
    quality: str,
    image_paths: list[str],
    output_format: str | None,
    timeout: int,
    stream: bool = False,
    partial_images: int = 1,
    on_partial: Any = None,
) -> tuple[list[bytes], dict[str, Any], dict[str, Any]]:
    enabled = [source for source in sources if source.get("enabled", True)]
    if not enabled:
        die("No enabled relay sources are configured.")
    if preferred_source:
        selected_index = next((i for i, source in enumerate(enabled) if source_name(source, i) == preferred_source), None)
        if selected_index is None:
            die(f"Configured relay source not found or disabled: {preferred_source}")
        enabled = enabled[selected_index:] + enabled[:selected_index]
    if stream:
        enabled = enabled[:1]
    attempts = []
    for index, source in enumerate(enabled):
        name = source_name(source, index)
        model = explicit_model or source.get("model") or DEFAULT_MODEL
        started = time.time()
        attempt = {"source": name, "model": model, "base_url": str(source["base_url"]).split("?")[0]}
        print(f"ATTEMPT_SOURCE={name} ATTEMPT_MODEL={model}", flush=True)
        try:
            client = ImageClient(str(source["api_key"]), str(source["base_url"]), timeout=timeout)
            if mode == "generate":
                if stream:
                    generated = client.generate_stream(
                        model=model, prompt=prompt, size=size, quality=quality,
                        output_format=output_format, partial_images=partial_images, on_partial=on_partial,
                    )
                else:
                    generated = client.generate(model=model, prompt=prompt, size=size, quality=quality, output_format=output_format)
            else:
                if stream:
                    generated = client.edit_stream(
                        model=model, prompt=prompt, size=size, quality=quality, image_paths=image_paths,
                        output_format=output_format, partial_images=partial_images, on_partial=on_partial,
                    )
                else:
                    generated = client.edit(model=model, prompt=prompt, size=size, quality=quality, image_paths=image_paths, output_format=output_format)
            attempt.update({"status": "success", "elapsed_seconds": round(time.time() - started, 2)})
            attempts.append(attempt)
            selected = dict(source)
            selected["model"] = model
            return generated, selected, {"selected_source": name, "attempts": attempts}
        except Exception as exc:
            retryable = is_retryable_relay_error(exc)
            attempt.update({"status": "failed", "retryable": retryable, "error": safe_error_summary(exc, source), "elapsed_seconds": round(time.time() - started, 2)})
            attempts.append(attempt)
            if not retryable or index == len(enabled) - 1:
                raise
    raise RuntimeError("No relay source produced an image.")


def write_output_images(images: list[bytes], out: Path, output_format: str | None, force: bool) -> None:
    if not images:
        die("Image API returned no images.")
    if out.exists() and not force:
        die(f"Output already exists: {out}. Use --force to overwrite.")
    out.parent.mkdir(parents=True, exist_ok=True)
    images[0] and out.write_bytes(images[0])
    if len(images) > 1:
        suffix = out.suffix or ".png"
        for index, data in enumerate(images[1:], start=2):
            extra = out.with_name(f"{out.stem}-{index}{suffix}")
            if extra.exists() and not force:
                die(f"Output already exists: {extra}. Use --force to overwrite.")
            extra.write_bytes(data)


def main() -> int:
    parser = argparse.ArgumentParser(description="Relay image generation client.")
    parser.add_argument("mode", nargs="?", choices=["generate", "edit"])
    parser.add_argument("--list-sources", action="store_true", help="List configured relay names and models without showing credentials or URLs.")
    parser.add_argument("--config")
    parser.add_argument("--from-codex", action="store_true")
    parser.add_argument("--no-codex", action="store_true")
    parser.add_argument("--codex-config")
    parser.add_argument("--codex-auth")
    parser.add_argument("--from-ccswitch", action="store_true")
    parser.add_argument("--no-ccswitch", action="store_true")
    parser.add_argument("--ccswitch-db")
    parser.add_argument("--model")
    parser.add_argument("--source", help="Prefer a configured relay source by name.")
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    parser.add_argument("--image", action="append")
    parser.add_argument("--size", default=DEFAULT_SIZE)
    parser.add_argument("--quality", default=DEFAULT_QUALITY)
    parser.add_argument("--output-format", choices=["png", "jpeg", "webp"])
    parser.add_argument("--stream", action="store_true", help="Save image-generation partial previews as local files.")
    parser.add_argument("--partial-images", type=int, choices=range(0, 4), default=1, help="Number of partial previews when --stream is enabled (0-3).")
    parser.add_argument("--out")
    parser.add_argument("--output-dir")
    parser.add_argument("--name")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--prepare-image", action="store_true")
    parser.add_argument("--max-input-edge", type=int)
    parser.add_argument("--keep-prepared", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg, config_path = load_config(args.config, args.from_ccswitch, args.from_codex, args.no_codex, args.no_ccswitch, args.ccswitch_db, args.codex_config, args.codex_auth)
    sources = normalize_sources(cfg)
    if args.list_sources:
        for index, source in enumerate(sources):
            model_name = source.get("model") or cfg.get("model") or DEFAULT_MODEL
            enabled = "enabled" if source.get("enabled", True) else "disabled"
            print(f"SOURCE={source_name(source, index)} MODEL={model_name} STATUS={enabled}")
        return 0
    if args.mode is None:
        parser.error("mode is required unless --list-sources is used")
    out = Path(args.out) if args.out else default_output_path(args)
    args.out = str(out)
    prompt = args.prompt if args.prompt is not None else (Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else None)
    if not prompt:
        die("Use --prompt-file or --prompt.")
    needs_prepared = args.mode == "edit" and args.image and (args.prepare_image or args.max_input_edge)
    temp_context = contextlib.nullcontext(None) if not needs_prepared or args.keep_prepared else tempfile.TemporaryDirectory(prefix="relay-imagegen-")
    with temp_context as temp_dir:
        prep_dir = out.parent / "relay_prepared" if args.keep_prepared and needs_prepared else Path(temp_dir) if temp_dir else None
        images = prepare_images(args, out, prep_dir, prepared_temporary=bool(needs_prepared and not args.keep_prepared))
        used_paths = [item["used"] for item in images] if images else []
        source_order = [source for source in sources if source.get("enabled", True)]
        if args.source:
            source_index = next((i for i, source in enumerate(source_order) if source_name(source, i) == args.source), None)
            if source_index is None:
                die(f"Configured relay source not found or disabled: {args.source}")
            source_order = source_order[source_index:] + source_order[:source_index]
        if not source_order:
            die("No enabled relay sources are configured.")
        model = args.model or source_order[0].get("model") or DEFAULT_MODEL
        if args.stream:
            partial_paths = [out.with_name(f"{out.stem}.partial-{i + 1}{out.suffix or '.png'}") for i in range(args.partial_images)]
            conflicts = [path for path in partial_paths if path.exists() and not args.force]
            if conflicts:
                die(f"Partial preview already exists: {conflicts[0]}. Use --force to overwrite.")
        else:
            partial_paths = []
        partial_files_written: list[str] = []
        print(f"MODE={args.mode}")
        print(f"PLANNED_MODEL={model}")
        print(f"SIZE={args.size}")
        print(f"OUT={out}")
        if args.dry_run:
            print("DIRECT_SDK=1")
            print(f"SOURCES={len([source for source in sources if source.get('enabled', True)])}")
            print(f"STREAM={int(args.stream)}")
            if args.source:
                print(f"PREFERRED_SOURCE={args.source}")
            for index, source in enumerate(sources):
                if source.get("enabled", True):
                    source_model = args.model or source.get("model") or cfg.get("model") or DEFAULT_MODEL
                    print(f"SOURCE_{index + 1}={source_name(source, index)} MODEL={source_model}")
            return 0
        started = time.time()
        if args.mode == "edit" and not used_paths:
            die("edit mode requires at least one --image.")
        try:
            def save_partial(index: int, data: bytes) -> None:
                if index < len(partial_paths):
                    path = partial_paths[index]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                    partial_files_written.append(str(path))
                    print(f"PARTIAL={path}", flush=True)

            generated, selected_source, route = request_with_failover(
                sources=sources,
                preferred_source=args.source,
                explicit_model=args.model,
                mode=args.mode,
                prompt=prompt,
                size=args.size,
                quality=args.quality,
                image_paths=[str(path) for path in used_paths],
                output_format=args.output_format,
                timeout=args.timeout,
                stream=args.stream,
                partial_images=args.partial_images,
                on_partial=save_partial,
            )
        except Exception as exc:
            if args.stream:
                die(f"Image stream failed; the relay may have accepted the request, and no retry was attempted: {redact_error_text(str(exc), sources)}")
            die(f"All configured relay sources failed: {redact_error_text(str(exc), sources)}")
        cfg = dict(cfg)
        cfg.update(selected_source)
        route["stream"] = bool(args.stream)
        route["partial_images"] = args.partial_images if args.stream else 0
        route["partial_files"] = partial_files_written
        print(f"SOURCE={route['selected_source']}")
        print(f"MODEL={selected_source['model']}")
        write_output_images(generated, out, args.output_format, args.force)
        dimensions = verify_dimensions(out, args.size)
        write_sidecar(out, args, cfg, config_path, images, dimensions, time.time() - started, route)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
