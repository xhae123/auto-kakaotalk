#!/usr/bin/env python3
"""KakaoTalk Mac auth + read adapter (self-contained).

Cherry-picked from the kakaotalk-mac skill. Keeps only what auto-kakaotalk
actually uses: auth resolution, and kakaocli passthrough for `chats` /
`messages`. No `search`, no `schema`, no shell-export format.

The real work is auth: KakaoTalk Mac stores its DB encrypted with a key
derived from the device UUID and the logged-in user_id. The plist usually
lists the user_id as a candidate, but on some accounts it's only present
as a SHA-512 of the user_id. We iterate candidates first, then fall back
to a bounded preimage search. Once a working (db_path, key) pair is
found, it's cached at ~/.cache/k-skill/auto-kakaotalk-auth.json.

Query execution delegates to the `kakaocli` brew binary — we only handle
auth and cache.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import multiprocessing as mp
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


EMPTY_ACCOUNT_HASH = (
    "31bca02094eb78126a517b206a88c73cfa9ec6f704c7030d18212cace820f025"
    "f00bf0ea68dbf3f3a5436ca63b53bf7bf80ad8d5de7d8359d0b7fed9dbc3ab99"
)
HEX_DATABASE_PATTERN = re.compile(r"^[0-9a-f]{78}(?:\.db)?$")
DIRECT_USER_ID_KEYS = ("userId", "user_id", "KAKAO_USER_ID", "userID")
DEFAULT_MAX_USER_ID = 1_000_000_000
DEFAULT_CHUNK_SIZE = 500_000
DEFAULT_CACHE_PATH = Path.home() / ".cache" / "k-skill" / "auto-kakaotalk-auth.json"
PASSTHROUGH_COMMANDS = ("chats", "messages")


class AuthError(RuntimeError):
    pass


@dataclass
class DetectionState:
    uuid: str
    candidate_user_ids: list[int]
    active_account_hash: str | None
    database_files: list[Path]


@dataclass
class ResolvedAuth:
    user_id: int
    uuid: str
    database_path: Path
    database_name: str
    key: str
    source: str


# ---------- plist XML parsing ----------


def parse_plist_xml(xml_text: str) -> Any:
    tokens = tokenize_plist_xml(xml_text)
    if not tokens:
        raise AuthError("plist XML was empty")
    if tokens[0] != ("start", "plist"):
        raise AuthError("plist XML did not start with <plist>")
    value, index = _parse_plist_tokens(tokens, 1)
    if tokens[index] != ("end", "plist"):
        raise AuthError("plist XML did not end with </plist>")
    return value


def tokenize_plist_xml(xml_text: str) -> list[tuple[str, str]]:
    normalized = re.sub(r"<\?xml[^>]*\?>", "", xml_text)
    normalized = re.sub(r"<!DOCTYPE[^>]*>", "", normalized)
    normalized = re.sub(r"<([A-Za-z0-9]+)\s*/>", r"<\1></\1>", normalized)
    normalized = normalized.replace("\r", "")
    tokens: list[tuple[str, str]] = []
    position = 0
    for match in re.finditer(r"<(/?)([A-Za-z0-9]+)(?: [^>]*)?>", normalized):
        text = normalized[position : match.start()]
        stripped = _unescape_xml(text).strip()
        if stripped:
            tokens.append(("text", stripped))
        token_type = "end" if match.group(1) else "start"
        tokens.append((token_type, match.group(2)))
        position = match.end()
    trailing = _unescape_xml(normalized[position:]).strip()
    if trailing:
        tokens.append(("text", trailing))
    return [token for token in tokens if token[0] != "text" or token[1]]


def _parse_plist_tokens(tokens: list[tuple[str, str]], index: int) -> tuple[Any, int]:
    token_type, tag = tokens[index]
    if token_type != "start":
        raise AuthError(f"Unexpected token {tokens[index]!r}")

    if tag == "dict":
        result: dict[str, Any] = {}
        index += 1
        while tokens[index] != ("end", "dict"):
            if tokens[index] != ("start", "key"):
                raise AuthError(f"Expected dict key, got {tokens[index]!r}")
            key, index = _parse_scalar(tokens, index, "key", lambda v: v)
            value, index = _parse_plist_tokens(tokens, index)
            result[key] = value
        return result, index + 1

    if tag == "array":
        items: list[Any] = []
        index += 1
        while tokens[index] != ("end", "array"):
            value, index = _parse_plist_tokens(tokens, index)
            items.append(value)
        return items, index + 1

    if tag == "integer":
        return _parse_scalar(tokens, index, "integer", int)
    if tag == "real":
        return _parse_scalar(tokens, index, "real", float)
    if tag == "string":
        return _parse_scalar(tokens, index, "string", lambda v: v)
    if tag == "date":
        return _parse_scalar(tokens, index, "date", lambda v: v)
    if tag == "data":
        return _parse_scalar(tokens, index, "data", lambda v: v)
    if tag == "true":
        return True, index + 2
    if tag == "false":
        return False, index + 2
    raise AuthError(f"Unsupported plist tag: {tag}")


def _parse_scalar(tokens, index, tag, caster):
    if tokens[index] != ("start", tag):
        raise AuthError(f"Expected <{tag}>, got {tokens[index]!r}")
    text = ""
    index += 1
    if tokens[index][0] == "text":
        text = tokens[index][1]
        index += 1
    if tokens[index] != ("end", tag):
        raise AuthError(f"Expected </{tag}>, got {tokens[index]!r}")
    return caster(text), index + 1


def _unescape_xml(text: str) -> str:
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
    )


# ---------- candidate/hash detection ----------


def unique_ints(values: Iterable[int]) -> list[int]:
    seen: set[int] = set()
    ordered: list[int] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def collect_candidate_user_ids(plist_data: dict[str, Any]) -> list[int]:
    candidates: list[int] = []
    for key in DIRECT_USER_ID_KEYS:
        value = plist_data.get(key)
        if isinstance(value, int) and value > 0:
            candidates.append(value)
        elif isinstance(value, str) and value.isdigit():
            candidates.append(int(value))

    alert_ids = plist_data.get("AlertKakaoIDsList", [])
    if isinstance(alert_ids, list):
        for item in alert_ids:
            if isinstance(item, int) and item > 0:
                candidates.append(item)
            elif isinstance(item, str) and item.isdigit():
                numeric = int(item)
                if numeric > 0:
                    candidates.append(numeric)
    return unique_ints(candidates)


def find_active_account_hash(plist_data: dict[str, Any]) -> str | None:
    prefix = "DESIGNATEDFRIENDSREVISION:"
    for key, value in plist_data.items():
        if not key.startswith(prefix):
            continue
        hash_hex = key[len(prefix):]
        if hash_hex == EMPTY_ACCOUNT_HASH:
            continue
        if not re.fullmatch(r"[0-9a-f]{128}", hash_hex):
            continue
        numeric_value = 0
        if isinstance(value, (int, float)):
            numeric_value = int(value)
        elif isinstance(value, str) and value.isdigit():
            numeric_value = int(value)
        if numeric_value != 0:
            return hash_hex
    return None


def discover_database_files(container: Path) -> list[Path]:
    if not container.exists():
        return []
    return sorted(
        [p for p in container.iterdir() if p.is_file() and HEX_DATABASE_PATTERN.fullmatch(p.name)],
        key=lambda item: item.name,
    )


# ---------- key derivation (ported from upstream) ----------


def hashed_device_uuid(uuid: str) -> str:
    uuid_bytes = uuid.encode("utf-8")
    combined = hashlib.sha1(uuid_bytes).digest() + hashlib.sha256(uuid_bytes).digest()
    return base64.b64encode(combined).decode("ascii")


def database_name(user_id: int, uuid: str) -> str:
    hawawa = ".".join([".", "F", str(user_id), "A", "F", uuid[::-1], ".", "|"])
    salt = hashed_device_uuid(uuid)[::-1].encode("utf-8")
    derived = hashlib.pbkdf2_hmac("sha256", hawawa.encode("utf-8"), salt, 100_000, 128)
    return derived.hex()[28 : 28 + 78]


def secure_key(user_id: int, uuid: str) -> str:
    hashed = hashed_device_uuid(uuid)
    parts = ["A", hashed, "|", "F", uuid[:5], "H", str(user_id), "|", uuid[7:]]
    hawawa = "F".join(parts)[::-1].encode("utf-8")
    salt = uuid[int(len(uuid) * 0.3):].encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", hawawa, salt, 100_000, 128).hex()


# ---------- user_id recovery via SHA-512 preimage search ----------


def recover_user_id_from_sha512(
    hex_hash: str,
    *,
    max_user_id: int = DEFAULT_MAX_USER_ID,
    workers: int | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> int | None:
    if not re.fullmatch(r"[0-9a-f]{128}", hex_hash):
        raise ValueError("expected 128-char lowercase sha512 hex digest")
    if max_user_id < 0:
        raise ValueError("max_user_id must be non-negative")

    normalized_workers = max(1, workers or (os.cpu_count() or 1))
    if normalized_workers == 1:
        return _scan_user_id_range((0, max_user_id + 1, hex_hash))

    start_method = "fork" if "fork" in mp.get_all_start_methods() else mp.get_start_method()
    ctx = mp.get_context(start_method)
    jobs = (
        (start, min(start + chunk_size, max_user_id + 1), hex_hash)
        for start in range(0, max_user_id + 1, chunk_size)
    )
    with ctx.Pool(processes=normalized_workers) as pool:
        for result in pool.imap_unordered(_scan_user_id_range, jobs, chunksize=1):
            if result is not None:
                pool.terminate()
                return result
    return None


def _scan_user_id_range(job: tuple[int, int, str]) -> int | None:
    start, end, hex_hash = job
    target = hex_hash.encode("ascii")
    for user_id in range(start, end):
        if hashlib.sha512(str(user_id).encode("utf-8")).hexdigest().encode("ascii") == target:
            return user_id
    return None


# ---------- platform helpers ----------


def platform_uuid() -> str:
    result = run(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], check=True)
    match = re.search(r'"IOPlatformUUID" = "([0-9A-F-]+)"', result.stdout)
    if not match:
        raise AuthError("Could not read IOPlatformUUID from ioreg output.")
    return match.group(1)


def container_path() -> Path:
    return (
        Path.home()
        / "Library/Containers/com.kakao.KakaoTalkMac/Data"
        / "Library/Application Support/com.kakao.KakaoTalkMac"
    )


def preference_paths() -> list[Path]:
    pref_dir = (
        Path.home()
        / "Library/Containers/com.kakao.KakaoTalkMac/Data"
        / "Library/Preferences"
    )
    paths = sorted(pref_dir.glob("com.kakao.KakaoTalkMac*.plist"))
    global_pref = Path.home() / "Library/Preferences/com.kakao.KakaoTalkMac.plist"
    if global_pref.exists():
        paths.append(global_pref)
    seen: set[Path] = set()
    deduped: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            deduped.append(path)
    return deduped


def run(args: Sequence[str], *, check: bool) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise AuthError(result.stderr.strip() or result.stdout.strip() or f"command failed: {' '.join(args)}")
    return result


def convert_plist_to_xml(plist_path: Path) -> str:
    return run(["/usr/bin/plutil", "-convert", "xml1", "-o", "-", str(plist_path)], check=True).stdout


def read_plist(plist_path: Path) -> dict[str, Any]:
    return parse_plist_xml(convert_plist_to_xml(plist_path))


def collect_detection_state(uuid_override: str | None = None) -> DetectionState:
    uuid = uuid_override or platform_uuid()
    snapshots = [read_plist(p) for p in preference_paths() if p.exists()]
    candidate_user_ids: list[int] = []
    active_hash: str | None = None
    for snapshot in snapshots:
        candidate_user_ids.extend(collect_candidate_user_ids(snapshot))
        if active_hash is None:
            active_hash = find_active_account_hash(snapshot)
    return DetectionState(
        uuid=uuid,
        candidate_user_ids=unique_ints(candidate_user_ids),
        active_account_hash=active_hash,
        database_files=discover_database_files(container_path()),
    )


# ---------- resolve + cache ----------


def verify_database_access(resolved: ResolvedAuth) -> bool:
    result = run(
        [
            "kakaocli", "query",
            "SELECT count(*) FROM sqlite_master",
            "--db", str(resolved.database_path),
            "--key", resolved.key,
        ],
        check=False,
    )
    return result.returncode == 0


def prioritized_database_paths(database_files: Sequence[Path], derived_name: str) -> list[Path]:
    preferred_names = {derived_name, f"{derived_name}.db"}
    preferred = [p for p in database_files if p.name in preferred_names]
    fallback = [p for p in database_files if p.name not in preferred_names]
    return [*preferred, *fallback]


def _try_resolved(user_id: int, source: str, state: DetectionState) -> ResolvedAuth | None:
    derived_name = database_name(user_id, state.uuid)
    key = secure_key(user_id, state.uuid)
    for db_path in prioritized_database_paths(state.database_files, derived_name):
        resolved = ResolvedAuth(
            user_id=user_id, uuid=state.uuid, database_path=db_path,
            database_name=derived_name, key=key, source=source,
        )
        if verify_database_access(resolved):
            return resolved
    return None


def load_cached(cache_path: Path) -> ResolvedAuth | None:
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        db_path = Path(payload["database_path"]).expanduser()
        user_id = int(payload["user_id"])
        uuid = str(payload["uuid"])
        db_name = str(payload["database_name"])
        key = str(payload["key"])
        source = str(payload.get("source", "cache"))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    if user_id <= 0 or not uuid or not db_name or not key or not db_path.exists():
        return None
    return ResolvedAuth(user_id, uuid, db_path, db_name, key, source)


def persist_cache(resolved: ResolvedAuth, cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "user_id": resolved.user_id,
        "uuid": resolved.uuid,
        "database_path": str(resolved.database_path),
        "database_name": resolved.database_name,
        "key": resolved.key,
        "source": resolved.source,
    }
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(cache_path, 0o600)
    except OSError:
        pass


def resolve_auth(
    *, refresh: bool, cache_path: Path, user_id_override: int | None = None,
    uuid_override: str | None = None, max_user_id: int = DEFAULT_MAX_USER_ID,
    workers: int | None = None, chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> ResolvedAuth:
    if not refresh and user_id_override is None and uuid_override is None:
        cached = load_cached(cache_path)
        if cached is not None:
            return cached

    state = collect_detection_state(uuid_override)
    if not state.database_files:
        raise AuthError("No KakaoTalk database files discovered. Is KakaoTalk installed and logged in?")

    candidates = list(state.candidate_user_ids)
    if user_id_override is not None:
        candidates = unique_ints([user_id_override, *candidates])

    for user_id in candidates:
        resolved = _try_resolved(user_id, "candidate", state)
        if resolved is not None:
            persist_cache(resolved, cache_path)
            return resolved

    if state.active_account_hash:
        recovered = recover_user_id_from_sha512(
            state.active_account_hash,
            max_user_id=max_user_id, workers=workers, chunk_size=chunk_size,
        )
        if recovered is not None and recovered not in candidates:
            resolved = _try_resolved(recovered, "hash-recovery", state)
            if resolved is not None:
                persist_cache(resolved, cache_path)
                return resolved

    raise AuthError(
        "Failed to resolve KakaoTalk auth. Try a larger --max-user-id or pass --user-id explicitly."
    )


# ---------- CLI ----------


def render_auth_text(resolved: ResolvedAuth, cache_path: Path) -> str:
    return "\n".join([
        "KakaoTalk auth resolved",
        f"- user_id: {resolved.user_id}",
        f"- uuid: {resolved.uuid}",
        f"- database: {resolved.database_path}",
        f"- source: {resolved.source}",
        f"- cache: {cache_path}",
    ])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="KakaoTalk Mac auth + read adapter for auto-kakaotalk."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        p.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
        p.add_argument("--user-id", type=int)
        p.add_argument("--uuid")
        p.add_argument("--max-user-id", type=int, default=DEFAULT_MAX_USER_ID)
        p.add_argument("--workers", type=int, default=None)
        p.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)

    auth_p = sub.add_parser("auth")
    add_common(auth_p)
    auth_p.add_argument("--refresh", action="store_true")
    auth_p.add_argument("--format", choices=("text", "json"), default="text")

    for cmd in PASSTHROUGH_COMMANDS:
        p = sub.add_parser(cmd)
        add_common(p)
        p.add_argument("--refresh-auth", action="store_true")

    args, forwarded = parser.parse_known_args(argv)
    cache_path = Path(args.cache_path).expanduser()

    try:
        if args.command == "auth":
            if forwarded:
                raise AuthError(f"Unexpected auth arguments: {' '.join(forwarded)}")
            resolved = resolve_auth(
                refresh=args.refresh, cache_path=cache_path,
                user_id_override=args.user_id, uuid_override=args.uuid,
                max_user_id=args.max_user_id, workers=args.workers, chunk_size=args.chunk_size,
            )
            if args.format == "json":
                print(json.dumps({
                    "user_id": resolved.user_id, "uuid": resolved.uuid,
                    "database_path": str(resolved.database_path),
                    "database_name": resolved.database_name,
                    "key": resolved.key, "source": resolved.source,
                    "cache_path": str(cache_path),
                }, ensure_ascii=False, indent=2))
            else:
                print(render_auth_text(resolved, cache_path))
            return 0

        resolved = resolve_auth(
            refresh=args.refresh_auth, cache_path=cache_path,
            user_id_override=args.user_id, uuid_override=args.uuid,
            max_user_id=args.max_user_id, workers=args.workers, chunk_size=args.chunk_size,
        )
        result = subprocess.run(
            ["kakaocli", args.command, *forwarded, "--db", str(resolved.database_path), "--key", resolved.key]
        )
        return result.returncode
    except (AuthError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
