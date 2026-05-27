#!/usr/bin/env python3
"""Configure or check relay-imagegen private config files."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit


SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL_CONFIG = SKILL_DIR / ".secrets" / "config.json"
CCSWITCH_DB = Path.home() / ".cc-switch" / "cc-switch.db"


def user_config_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "relay-imagegen" / "config.json"
    return Path.home() / ".config" / "relay-imagegen" / "config.json"


def candidate_paths() -> list[Path]:
    paths = []
    env_path = os.environ.get("RELAY_IMAGEGEN_CONFIG")
    if env_path:
        paths.append(Path(env_path))
    paths.extend(
        [
            Path.cwd() / "photo" / "api_key.json",
            Path.cwd() / ".secrets" / "image_api.json",
            Path.cwd() / ".secrets" / "relay_imagegen.json",
            SKILL_CONFIG,
            user_config_path(),
            Path.home() / ".config" / "relay-imagegen" / "config.json",
            Path.home() / ".relay-imagegen.json",
        ]
    )
    deduped = []
    seen = set()
    for path in paths:
        key = str(path).lower()
        if key not in seen:
            deduped.append(path)
            seen.add(key)
    return deduped


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"BROKEN_CONFIG={path} ({exc})")
        return None


def redact(value: str | None) -> str:
    if not value:
        return ""
    return value[:4] + "..." + value[-4:] if len(value) > 10 else "***"


def normalize_openai_base_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme and parsed.netloc and parsed.path in {"", "/"}:
        return urlunsplit((parsed.scheme, parsed.netloc, "/v1", parsed.query, parsed.fragment))
    return url


def check() -> int:
    ccswitch_status = check_ccswitch(quiet_missing=True)
    if ccswitch_status == 0:
        return 0
    found = False
    for path in candidate_paths():
        if not path.exists():
            continue
        found = True
        data = load_json(path)
        if data is None:
            continue
        api_key = (
            data.get("api_key")
            or data.get("apiKey")
            or data.get("key")
            or data.get("token")
            or data.get("openai_api_key")
            or data.get("OPENAI_API_KEY")
        )
        base_url = (
            data.get("base_url")
            or data.get("baseUrl")
            or data.get("baseURL")
            or data.get("api_base")
            or data.get("endpoint")
            or data.get("openai_base_url")
            or data.get("OPENAI_BASE_URL")
        )
        model = data.get("model", "gpt-image-2")
        print(f"CONFIG={path}")
        print(f"API_KEY={redact(str(api_key)) if api_key else 'missing'}")
        print(f"BASE_URL={base_url or 'missing'}")
        print(f"MODEL={model}")
        return 0 if api_key and base_url else 2
    if not found:
        print("CONFIG=missing")
        print(f"Suggested skill-local path: {SKILL_CONFIG}")
        print(f"Suggested user path: {user_config_path()}")
        print("If you use ccswitch, run: python scripts/setup.py --check-ccswitch")
    return 1


def check_ccswitch(db_path: Path = CCSWITCH_DB, app_type: str = "codex", quiet_missing: bool = False) -> int:
    if not db_path.exists():
        if not quiet_missing:
            print(f"CCSWITCH_DB=missing ({db_path})")
        return 1
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        provider = con.execute(
            "select id, name, settings_config from providers where app_type=? and is_current=1 limit 1",
            (app_type,),
        ).fetchone()
        if provider is None:
            if not quiet_missing:
                print(f"CCSWITCH_PROVIDER=missing app_type={app_type}")
            return 1
        settings = json.loads(provider["settings_config"] or "{}")
        auth = settings.get("auth") if isinstance(settings.get("auth"), dict) else {}
        api_key = (
            auth.get("api_key")
            or auth.get("apiKey")
            or auth.get("key")
            or auth.get("token")
            or auth.get("openai_api_key")
            or auth.get("OPENAI_API_KEY")
        )
        endpoint = con.execute(
            "select url from provider_endpoints where app_type=? and provider_id=? order by id desc limit 1",
            (app_type, provider["id"]),
        ).fetchone()
    except sqlite3.Error as exc:
        if not quiet_missing:
            print(f"CCSWITCH_ERROR={exc}")
        return 1
    except json.JSONDecodeError as exc:
        if not quiet_missing:
            print(f"CCSWITCH_SETTINGS_ERROR={exc}")
        return 1
    finally:
        try:
            con.close()
        except Exception:
            pass
    print(f"CCSWITCH_DB={db_path}")
    print(f"CCSWITCH_PROVIDER={provider['name']} ({provider['id']})")
    print(f"API_KEY={redact(str(api_key)) if api_key else 'missing'}")
    print(f"BASE_URL={normalize_openai_base_url(endpoint['url']) if endpoint else 'missing'}")
    print("MODEL=gpt-image-2")
    return 0 if api_key and endpoint else 2


def write_config(path: Path, overwrite: bool) -> int:
    if path.exists() and not overwrite:
        print(f"Config already exists: {path}")
        print("Use --force to overwrite it.")
        return 2
    print(f"Writing private relay config to: {path}")
    base_url = input("Relay base_url: ").strip()
    model = input("Model [gpt-image-2]: ").strip() or "gpt-image-2"
    api_key = getpass.getpass("API key (hidden): ").strip()
    if not api_key or not base_url:
        print("api_key and base_url are required.", file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"api_key": api_key, "base_url": base_url, "model": model}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("WROTE_CONFIG=1")
    print(f"CONFIG={path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up relay-imagegen private config.")
    parser.add_argument("command", nargs="?", choices=["config"], help="Create or update a config file.")
    parser.add_argument("--check", action="store_true", help="Check the first usable config without printing secrets.")
    parser.add_argument("--check-ccswitch", action="store_true", help="Check the current ccswitch Codex provider.")
    parser.add_argument("--ccswitch-db", help="Override the ccswitch SQLite database path.")
    parser.add_argument(
        "--scope",
        choices=["skill", "user"],
        default="skill",
        help="Where to write config with the config command. Default: skill.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite an existing config.")
    args = parser.parse_args()

    if args.check_ccswitch:
        return check_ccswitch(Path(args.ccswitch_db) if args.ccswitch_db else CCSWITCH_DB)
    if args.check or not args.command:
        return check()
    target = SKILL_CONFIG if args.scope == "skill" else user_config_path()
    return write_config(target, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
