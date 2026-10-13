#!/usr/bin/env python3
"""Configure or check relay-imagegen private config files."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit


SKILL_DIR = Path(__file__).resolve().parents[1]
ROOT_CONFIG = SKILL_DIR / "config.json"
SKILL_CONFIG = SKILL_DIR / ".secrets" / "config.json"
CCSWITCH_DB = Path.home() / ".cc-switch" / "cc-switch.db"
CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
CODEX_CONFIG = CODEX_HOME / "config.toml"
CODEX_AUTH = CODEX_HOME / "auth.json"
DEFAULT_MODEL = "gpt-image-2.5-flare"
MODEL_PRESETS = {
    "Flare（推荐）": "gpt-image-2.5-flare",
    "Sunburst": "gpt-image-2.5-sunburst",
    "Image 2（兼容）": "gpt-image-2",
}


def user_config_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "relay-imagegen" / "config.json"
    return Path.home() / ".config" / "relay-imagegen" / "config.json"


def candidate_paths(include_skill_config: bool = True) -> list[Path]:
    paths = []
    env_path = os.environ.get("RELAY_IMAGEGEN_CONFIG")
    paths.append(ROOT_CONFIG)
    if env_path:
        paths.append(Path(env_path))
    paths.extend(
        [
            Path.cwd() / "photo" / "api_key.json",
            Path.cwd() / ".secrets" / "image_api.json",
            Path.cwd() / ".secrets" / "relay_imagegen.json",
            SKILL_CONFIG if include_skill_config else None,
            user_config_path(),
            Path.home() / ".config" / "relay-imagegen" / "config.json",
            Path.home() / ".relay-imagegen.json",
        ]
    )
    deduped = []
    seen = set()
    for path in paths:
        if path is None:
            continue
        key = str(path).lower()
        if key not in seen:
            deduped.append(path)
            seen.add(key)
    return deduped


def load_json(path: Path, quiet: bool = False) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        if not quiet:
            print(f"BROKEN_CONFIG={path} ({exc})")
        return None


def check_json_config(path: Path, quiet: bool = False) -> int:
    data = load_json(path, quiet=quiet)
    if data is None:
        return 1
    sources = data.get("sources") if isinstance(data.get("sources"), list) else None
    first = sources[0] if sources and isinstance(sources[0], dict) else data
    api_key = (
        first.get("api_key")
        or first.get("apiKey")
        or first.get("key")
        or first.get("token")
        or first.get("openai_api_key")
        or first.get("OPENAI_API_KEY")
        or data.get("api_key")
        or data.get("apiKey")
        or data.get("key")
        or data.get("token")
        or data.get("openai_api_key")
        or data.get("OPENAI_API_KEY")
    )
    base_url = (
        first.get("base_url")
        or first.get("baseUrl")
        or first.get("baseURL")
        or first.get("api_base")
        or first.get("endpoint")
        or first.get("openai_base_url")
        or first.get("OPENAI_BASE_URL")
        or data.get("base_url")
        or data.get("baseUrl")
        or data.get("baseURL")
        or data.get("api_base")
        or data.get("endpoint")
        or data.get("openai_base_url")
        or data.get("OPENAI_BASE_URL")
    )
    model = first.get("model") or data.get("model", DEFAULT_MODEL)
    if quiet:
        return 0 if api_key and base_url and (not sources or all(isinstance(item, dict) and item.get("api_key") and item.get("base_url") for item in sources)) else 2
    print(f"CONFIG={path}")
    if sources:
        print(f"SOURCES={len(sources)}")
    print(f"API_KEY={redact(str(api_key)) if api_key else 'missing'}")
    print(f"BASE_URL={base_url or 'missing'}")
    print(f"MODEL={model}")
    return 0 if api_key and base_url and (not sources or all(isinstance(item, dict) and item.get("api_key") and item.get("base_url") for item in sources)) else 2


def redact(value: str | None) -> str:
    if not value:
        return ""
    return value[:4] + "..." + value[-4:] if len(value) > 10 else "***"


def normalize_openai_base_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme and parsed.netloc and parsed.path in {"", "/"}:
        return urlunsplit((parsed.scheme, parsed.netloc, "/v1", parsed.query, parsed.fragment))
    return url


def extract_base_url_from_ccswitch_config(config: Any) -> str | None:
    if isinstance(config, dict):
        for key in ("base_url", "baseUrl", "baseURL", "api_base", "endpoint", "openai_base_url", "OPENAI_BASE_URL"):
            if config.get(key):
                return str(config[key])
        return None
    if not isinstance(config, str):
        return None
    for key in ("base_url", "baseUrl", "baseURL", "api_base", "endpoint", "openai_base_url", "OPENAI_BASE_URL"):
        match = re.search(rf"(?m)^\s*{re.escape(key)}\s*=\s*[\"']([^\"']+)[\"']", config)
        if match:
            return match.group(1)
    return None


def check() -> int:
    if ROOT_CONFIG.exists():
        return check_json_config(ROOT_CONFIG)
    if SKILL_CONFIG.exists():
        skill_status = check_json_config(SKILL_CONFIG, quiet=True)
        if skill_status == 0:
            check_json_config(SKILL_CONFIG)
            return 0

    codex_status = check_codex(quiet_missing=True)
    if codex_status == 0:
        return 0
    ccswitch_status = check_ccswitch(quiet_missing=True)
    if ccswitch_status == 0:
        return 0
    found = False
    for path in candidate_paths(include_skill_config=False):
        if not path.exists():
            continue
        found = True
        status = check_json_config(path)
        if status != 1:
            return status
    if not found:
        print("CONFIG=missing")
        print("If Codex already uses a relay, run: python scripts/setup.py --check-codex")
        print(f"Suggested skill-local path: {SKILL_CONFIG}")
        print(f"Suggested user path: {user_config_path()}")
        print("If you use ccswitch, run: python scripts/setup.py --check-ccswitch")
    return 1


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


def check_codex(
    config_path: Path = CODEX_CONFIG,
    auth_path: Path = CODEX_AUTH,
    quiet_missing: bool = False,
) -> int:
    if not config_path.exists() or not auth_path.exists():
        if not quiet_missing:
            print(f"CODEX_CONFIG={'missing' if not config_path.exists() else config_path}")
            print(f"CODEX_AUTH={'missing' if not auth_path.exists() else auth_path}")
        return 1
    try:
        config = parse_toml(config_path)
        auth = json.loads(auth_path.read_text(encoding="utf-8"))
    except Exception as exc:
        if not quiet_missing:
            print(f"CODEX_ERROR={exc}")
        return 1
    provider_name = config.get("model_provider")
    providers = config.get("model_providers") if isinstance(config.get("model_providers"), dict) else {}
    provider = providers.get(provider_name) if provider_name else None
    if not isinstance(provider, dict):
        if not quiet_missing:
            print("CODEX_PROVIDER=missing")
        return 1
    api_key = (
        auth.get("api_key")
        or auth.get("apiKey")
        or auth.get("key")
        or auth.get("token")
        or auth.get("openai_api_key")
        or auth.get("OPENAI_API_KEY")
    )
    base_url = (
        provider.get("base_url")
        or provider.get("baseUrl")
        or provider.get("baseURL")
        or provider.get("api_base")
        or provider.get("endpoint")
        or provider.get("openai_base_url")
        or provider.get("OPENAI_BASE_URL")
    )
    image_model = provider.get("image_model") or provider.get("imageModel") or DEFAULT_MODEL
    print(f"CODEX_CONFIG={config_path}")
    print(f"CODEX_AUTH={auth_path}")
    print(f"CODEX_PROVIDER={provider_name}")
    print(f"API_KEY={redact(str(api_key)) if api_key else 'missing'}")
    print(f"BASE_URL={normalize_openai_base_url(str(base_url)) if base_url else 'missing'}")
    print(f"MODEL={image_model}")
    return 0 if api_key and base_url else 2


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
            "select url from provider_endpoints where app_type=? and provider_id=? order by id asc limit 1",
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
    base_url = extract_base_url_from_ccswitch_config(settings.get("config"))
    if not base_url and endpoint:
        base_url = endpoint["url"]
    print(f"BASE_URL={normalize_openai_base_url(base_url) if base_url else 'missing'}")
    print(f"MODEL={DEFAULT_MODEL}")
    return 0 if api_key and base_url else 2


def write_config(path: Path, overwrite: bool) -> int:
    if path.exists() and not overwrite:
        print(f"Config already exists: {path}")
        print("Use --force to overwrite it.")
        return 2
    print(f"Writing private relay config to: {path}")
    base_url = input("Relay base_url: ").strip()
    model = input(f"Model [{DEFAULT_MODEL}]: ").strip() or DEFAULT_MODEL
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


def open_gui() -> int:
    """Open the local configuration window without exposing JSON to users."""
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except ImportError:
        print("Tkinter is not available in this Python installation.", file=sys.stderr)
        return 1

    loaded_config = load_json(ROOT_CONFIG, quiet=True)
    existing = loaded_config if isinstance(loaded_config, dict) else {}
    raw_sources = existing.get("sources") if isinstance(existing.get("sources"), list) else [existing]
    sources: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_sources):
        if not isinstance(raw, dict):
            continue
        api_key = raw.get("api_key") or raw.get("apiKey") or raw.get("key") or ""
        base_url = raw.get("base_url") or raw.get("baseUrl") or raw.get("endpoint") or ""
        if api_key or base_url:
            sources.append({
                "name": str(raw.get("name") or f"relay-{index + 1}"),
                "base_url": str(base_url),
                "api_key": str(api_key),
                "model": str(raw.get("model") or existing.get("model") or DEFAULT_MODEL),
                "enabled": raw.get("enabled", True) is not False,
            })
    window = tk.Tk()
    window.title("Relay Imagegen 配置")
    window.resizable(False, False)
    frame = ttk.Frame(window, padding=16)
    frame.grid(sticky="nsew")
    status = tk.StringVar(value="按顺序尝试启用的中转站；失败时自动切换到下一个。")
    name_var = tk.StringVar()
    url_var = tk.StringVar()
    key_var = tk.StringVar()
    enabled_var = tk.BooleanVar(value=True)
    custom_label = "自定义模型（高级）"
    preset_by_model = {model: label for label, model in MODEL_PRESETS.items()}
    model_label = tk.StringVar()
    listbox = tk.Listbox(frame, height=6, width=32, exportselection=False)
    listbox.grid(row=0, column=0, rowspan=8, padx=(0, 16), sticky="ns")

    ttk.Label(frame, text="中转站名称").grid(row=0, column=1, sticky="w")
    ttk.Entry(frame, textvariable=name_var, width=45).grid(row=0, column=2, pady=(0, 6))
    ttk.Label(frame, text="中转站地址").grid(row=1, column=1, sticky="w")
    ttk.Entry(frame, textvariable=url_var, width=45).grid(row=1, column=2, pady=6)
    ttk.Label(frame, text="访问密钥").grid(row=2, column=1, sticky="w")
    ttk.Entry(frame, textvariable=key_var, show="*", width=45).grid(row=2, column=2, pady=6)
    ttk.Label(frame, text="生成模型").grid(row=3, column=1, sticky="w")
    model_combo = ttk.Combobox(frame, textvariable=model_label, values=list(MODEL_PRESETS) + [custom_label], state="readonly", width=42)
    model_combo.grid(row=3, column=2, pady=6)
    custom_model = tk.StringVar()
    custom_entry = ttk.Entry(frame, textvariable=custom_model, width=45)
    ttk.Label(frame, text="模型 ID").grid(row=4, column=1, sticky="w")
    ttk.Checkbutton(frame, text="启用此源", variable=enabled_var).grid(row=5, column=1, sticky="w", pady=4)

    def refresh() -> None:
        listbox.delete(0, tk.END)
        for source in sources:
            marker = "启用" if source.get("enabled", True) else "停用"
            listbox.insert(tk.END, f"[{marker}] {source['name']}")

    def load_selected(_event: object = None) -> None:
        selection = listbox.curselection()
        if not selection:
            return
        source = sources[selection[0]]
        name_var.set(source["name"])
        url_var.set(source["base_url"])
        key_var.set(source["api_key"])
        enabled_var.set(source.get("enabled", True) is not False)
        model = source.get("model") or DEFAULT_MODEL
        label = preset_by_model.get(model, custom_label)
        model_label.set(label)
        custom_model.set(model if label == custom_label else "")
        if label == custom_label:
            custom_entry.grid(row=4, column=2, pady=6)
        else:
            custom_entry.grid_remove()

    def select_preset(_event: object = None) -> None:
        if model_label.get() == custom_label:
            custom_entry.grid(row=4, column=2, pady=6)
            custom_entry.focus_set()
        else:
            custom_entry.grid_remove()

    def form_source() -> dict[str, Any] | None:
        url = url_var.get().strip()
        key = key_var.get().strip()
        try:
            parsed_url = urlsplit(url)
            valid_url = parsed_url.scheme in {"http", "https"} and bool(parsed_url.hostname)
            if parsed_url.port is not None:
                valid_url = valid_url and 0 < parsed_url.port < 65536
        except ValueError:
            valid_url = False
        if not url or not key or not valid_url:
            messagebox.showerror("无法保存", "请填写有效的中转站地址和访问密钥。", parent=window)
            return None
        model = custom_model.get().strip() if model_label.get() == custom_label else MODEL_PRESETS.get(model_label.get(), DEFAULT_MODEL)
        if not model:
            messagebox.showerror("无法保存", "请输入模型 ID。", parent=window)
            return None
        return {"name": name_var.get().strip() or f"relay-{len(sources) + 1}", "base_url": normalize_openai_base_url(url), "api_key": key, "model": model, "enabled": enabled_var.get()}

    def add_or_update() -> None:
        source = form_source()
        if source is None:
            return
        # The explicit add action always appends; edits are committed by Save.
        sources.append(source)
        refresh()
        listbox.selection_clear(0, tk.END)
        listbox.selection_set(len(sources) - 1)
        load_selected()

    def delete_selected() -> None:
        selection = listbox.curselection()
        if selection and len(sources) > 1:
            sources.pop(selection[0])
            refresh()
            listbox.selection_set(min(selection[0], len(sources) - 1))
            load_selected()

    def move_selected(delta: int) -> None:
        selection = listbox.curselection()
        if not selection:
            return
        index = selection[0]
        target = index + delta
        if 0 <= target < len(sources):
            sources[index], sources[target] = sources[target], sources[index]
            refresh()
            listbox.selection_set(target)
            load_selected()

    def save() -> None:
        source = form_source()
        selection = listbox.curselection()
        if source is not None and selection:
            sources[selection[0]] = source
        elif source is not None:
            sources.append(source)
        if not sources or not any(item.get("enabled", True) for item in sources) or not all(item.get("base_url") and item.get("api_key") for item in sources):
            messagebox.showerror("无法保存", "请补全每个中转站的地址和访问密钥。", parent=window)
            return
        try:
            ROOT_CONFIG.write_text(json.dumps({"sources": sources, "model": sources[0].get("model", DEFAULT_MODEL)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("保存失败", f"无法保存配置文件：{exc}", parent=window)
            return
        refresh()
        status.set("配置已保存。生成时会按列表顺序自动尝试。")
        messagebox.showinfo("已保存", "配置已保存，之后生成图片时会自动使用。", parent=window)

    ttk.Button(frame, text="添加新源", command=add_or_update).grid(row=6, column=1, pady=(12, 4), sticky="w")
    ttk.Button(frame, text="删除选中", command=delete_selected).grid(row=6, column=2, pady=(12, 4), sticky="w")
    ttk.Button(frame, text="上移", command=lambda: move_selected(-1)).grid(row=7, column=1, pady=4, sticky="w")
    ttk.Button(frame, text="下移", command=lambda: move_selected(1)).grid(row=7, column=2, pady=4, sticky="w")
    ttk.Label(frame, textvariable=status, foreground="#666", wraplength=420).grid(row=8, column=1, columnspan=2, sticky="w", pady=(8, 12))
    buttons = ttk.Frame(frame)
    buttons.grid(row=9, column=1, columnspan=2, sticky="e")
    ttk.Button(buttons, text="保存配置", command=save).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(buttons, text="关闭", command=window.destroy).grid(row=0, column=1)
    listbox.bind("<<ListboxSelect>>", load_selected)
    model_combo.bind("<<ComboboxSelected>>", select_preset)
    refresh()
    if sources:
        listbox.selection_set(0)
        load_selected()
    else:
        name_var.set("relay-1")
        model_label.set(MODEL_PRESETS.get(DEFAULT_MODEL, list(MODEL_PRESETS)[0]))
    window.mainloop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up relay-imagegen private config.")
    parser.add_argument("command", nargs="?", choices=["config", "gui"], help="Create config or open the local configuration window.")
    parser.add_argument("--check", action="store_true", help="Check the first usable config without printing secrets.")
    parser.add_argument("--check-codex", action="store_true", help="Check the current Codex config/auth provider.")
    parser.add_argument("--codex-config", help="Override the Codex config.toml path.")
    parser.add_argument("--codex-auth", help="Override the Codex auth.json path.")
    parser.add_argument("--check-ccswitch", action="store_true", help="Check the current ccswitch Codex provider.")
    parser.add_argument("--ccswitch-db", help="Override the ccswitch SQLite database path.")
    parser.add_argument(
        "--scope",
        choices=["root", "skill", "user"],
        default="root",
        help="Where to write config with the config command. Default: root.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite an existing config.")
    args = parser.parse_args()

    if args.check_codex:
        return check_codex(
            Path(args.codex_config) if args.codex_config else CODEX_CONFIG,
            Path(args.codex_auth) if args.codex_auth else CODEX_AUTH,
        )
    if args.check_ccswitch:
        return check_ccswitch(Path(args.ccswitch_db) if args.ccswitch_db else CCSWITCH_DB)
    if args.check or not args.command:
        return check()
    if args.command == "gui":
        return open_gui()
    target = ROOT_CONFIG if args.scope == "root" else SKILL_CONFIG if args.scope == "skill" else user_config_path()
    return write_config(target, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
