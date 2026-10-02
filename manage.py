#!/usr/bin/env python3
"""Safe, transactional installer for the agent-toolkit checkout.

The source checkout contains only skills today.  Manifests are deliberately
strict: adding another asset kind requires an implementation here before it can
be accepted by this program.
"""
from __future__ import annotations

import argparse
import copy
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


SCHEMA_VERSION = 1
AGENTS = ("codex", "omp", "claude")
COMPAT_AGENTS = ("omp", "claude")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
STATE_RELATIVE = Path(".local") / "state" / "agent-toolkit"
STATE_FILENAME = "installations.json"
LOCK_FILENAME = "install.lock"


class ToolkitError(Exception):
    """A user-actionable validation or transaction error."""


class Source:
    def __init__(self, root: Path, groups: Dict[str, dict], skills: Dict[str, Path],
                 skill_groups: Dict[str, str], revision: Optional[str]):
        self.root = root
        self.groups = groups
        self.skills = skills
        self.skill_groups = skill_groups
        self.revision = revision

    @property
    def group_names(self) -> List[str]:
        return sorted(self.groups)


class Transaction:
    """Filesystem transaction based on rename-to-backup operations.

    Every destination is backed up before replacement/removal.  New files and
    directories are tracked separately.  Rollback therefore restores matching
    adopted directories and leaves unrelated paths untouched.
    """

    def __init__(self, home: Path):
        self.home = home
        self.parent = state_dir(home)
        self.temp: Optional[Path] = None
        self.backups: List[Tuple[Path, Path]] = []
        self.backed_up: Set[Path] = set()
        self.created: List[Path] = []
        self.created_dirs: List[Path] = []
        self.committed = False

    def __enter__(self) -> "Transaction":
        # The state directory already passed safety checks in the caller.
        self.temp = Path(tempfile.mkdtemp(prefix=".txn-", dir=str(self.parent)))
        return self

    def _backup(self, path: Path) -> None:
        path = path.absolute()
        if path in self.backed_up:
            return
        try:
            os.lstat(path)
        except FileNotFoundError:
            return
        if self.temp is None:
            raise ToolkitError("internal transaction was not started")
        backup = self.temp / ("backup-%d" % len(self.backups))
        os.replace(str(path), str(backup))
        self.backups.append((path, backup))
        self.backed_up.add(path)

    def note_created(self, path: Path) -> None:
        path = path.absolute()
        if path not in self.created:
            self.created.append(path)

    def ensure_parent(self, path: Path) -> None:
        """Create missing parents, recording only directories we create."""
        parent = path.absolute().parent
        chain: List[Path] = []
        cursor = parent
        while not cursor.exists():
            chain.append(cursor)
            cursor = cursor.parent
        # Existing ancestors were checked before the transaction.  Recheck
        # lstat here in case a concurrent actor introduced a symlink.
        for item in reversed(chain):
            try:
                os.mkdir(str(item), 0o755)
            except FileExistsError:
                if not item.is_dir() or item.is_symlink():
                    raise ToolkitError("destination parent is not a directory: %s" % item)
            self.created_dirs.append(item)

    def replace_with_tree(self, destination: Path, source: Path, expected_hash: Optional[str] = None) -> None:
        self.ensure_parent(destination)
        if destination.exists() or destination.is_symlink():
            self._backup(destination)
        stage = self.temp / ("tree-%d" % len(self.created)) if self.temp else None
        if stage is None:
            raise ToolkitError("internal transaction was not started")
        copy_tree(source, stage)
        if expected_hash is not None:
            staged_hash = _hash_tree(stage)
            current_source_hash = _hash_tree(source)
            if staged_hash != expected_hash or current_source_hash != expected_hash:
                raise ToolkitError("source asset changed during transaction: %s" % source)
        os.replace(str(stage), str(destination))
        self.note_created(destination)

    def replace_with_link(self, destination: Path, target: Path) -> None:
        self.ensure_parent(destination)
        if destination.exists() or destination.is_symlink():
            self._backup(destination)
        # An absolute link is unambiguous and still points within HOME.
        os.symlink(str(target), str(destination))
        self.note_created(destination)

    def remove(self, path: Path) -> None:
        if path.exists() or path.is_symlink():
            self._backup(path)

    def write_state(self, path: Path, data: dict) -> None:
        self.ensure_parent(path)
        if path.exists() or path.is_symlink():
            self._backup(path)
        if self.temp is None:
            raise ToolkitError("internal transaction was not started")
        temporary = self.temp / ("state-%d.json" % len(self.created))
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(str(temporary), 0o600)
        os.replace(str(temporary), str(path))
        self.note_created(path)

    def _remove_path(self, path: Path) -> None:
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return
        if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
            shutil.rmtree(str(path))
        else:
            os.unlink(str(path))

    def rollback(self) -> None:
        # Remove current replacements first.  A path may be both noted as
        # created and backed up only when a replacement was attempted; the
        # backup itself is authoritative in that case.
        for path in reversed(self.created):
            if path not in self.backed_up:
                self._remove_path(path)
        for original, backup in reversed(self.backups):
            self._remove_path(original)
            if backup.exists() or backup.is_symlink():
                os.replace(str(backup), str(original))
        for directory in sorted(set(self.created_dirs), key=lambda p: len(p.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        if self.temp is not None:
            shutil.rmtree(str(self.temp), ignore_errors=True)
            self.temp = None

    def commit(self) -> None:
        self.committed = True
        if self.temp is not None:
            shutil.rmtree(str(self.temp), ignore_errors=True)
            self.temp = None

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if exc_type is not None or not self.committed:
            self.rollback()
        return False


class ProcessLock:
    def __init__(self, path: Path):
        self.path = path
        self.fd: Optional[int] = None

    def __enter__(self) -> "ProcessLock":
        self.fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        except Exception:
            os.close(self.fd)
            self.fd = None
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None
        return False


def _safe_name(value: object, kind: str) -> str:
    if not isinstance(value, str) or not value or not SAFE_NAME.fullmatch(value):
        raise ToolkitError("unsafe %s name: %r" % (kind, value))
    return value


def _json_object(path: Path, label: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        raise ToolkitError("missing %s: %s" % (label, path))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ToolkitError("invalid %s %s: %s" % (label, path, exc))
    if not isinstance(value, dict):
        raise ToolkitError("%s must be a JSON object: %s" % (label, path))
    return value


def _regular_nonsymlink(path: Path, label: str) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise ToolkitError("missing %s: %s" % (label, path))
    if stat.S_ISLNK(info.st_mode):
        raise ToolkitError("symlinked %s is not allowed: %s" % (label, path))
    if not stat.S_ISREG(info.st_mode):
        raise ToolkitError("%s must be a regular file: %s" % (label, path))


def _directory_nonsymlink(path: Path, label: str) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise ToolkitError("missing %s: %s" % (label, path))
    if stat.S_ISLNK(info.st_mode):
        raise ToolkitError("symlinked %s is not allowed: %s" % (label, path))
    if not stat.S_ISDIR(info.st_mode):
        raise ToolkitError("%s must be a directory: %s" % (label, path))


def _reject_source_symlinks(path: Path) -> None:
    """Reject symlinks anywhere below a skill, including broken links."""
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise ToolkitError("cannot inspect source asset %s: %s" % (path, exc))
    if stat.S_ISLNK(info.st_mode):
        raise ToolkitError("source symlinks are not supported: %s" % path)
    if not stat.S_ISDIR(info.st_mode):
        raise ToolkitError("source skill must be a directory: %s" % path)
    try:
        entries = sorted(os.scandir(path), key=lambda item: item.name)
    except OSError as exc:
        raise ToolkitError("cannot inspect source asset %s: %s" % (path, exc))
    for entry in entries:
        entry_path = Path(entry.path)
        entry_info = entry.stat(follow_symlinks=False)
        if stat.S_ISLNK(entry_info.st_mode):
            raise ToolkitError("source symlinks are not supported: %s" % entry_path)
        if stat.S_ISDIR(entry_info.st_mode):
            _reject_source_symlinks(entry_path)
        elif not stat.S_ISREG(entry_info.st_mode):
            raise ToolkitError("unsupported source asset type: %s" % entry_path)


def _git_revision(root: Path) -> Optional[str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = completed.stdout.strip()
    return revision or None


def load_source(root: Optional[Path] = None) -> Source:
    root = (root or Path(__file__).resolve().parent).absolute()
    _directory_nonsymlink(root, "source root")
    manifest_path = root / "toolkit.json"
    _regular_nonsymlink(manifest_path, "root manifest")
    manifest = _json_object(manifest_path, "root manifest")
    expected_keys = {"schema_version", "name", "groups"}
    extra = set(manifest) - expected_keys
    missing = expected_keys - set(manifest)
    if extra:
        raise ToolkitError("unsupported root manifest key(s): %s" % ", ".join(sorted(extra)))
    if missing:
        raise ToolkitError("missing root manifest key(s): %s" % ", ".join(sorted(missing)))
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ToolkitError("unsupported root manifest schema_version: %r" % manifest["schema_version"])
    if manifest["name"] != "agent-toolkit":
        raise ToolkitError("root manifest name must be 'agent-toolkit'")
    group_values = manifest["groups"]
    if not isinstance(group_values, list) or not group_values:
        raise ToolkitError("root manifest groups must be a non-empty list")
    group_names = [_safe_name(value, "group") for value in group_values]
    if len(set(group_names)) != len(group_names):
        raise ToolkitError("root manifest groups contain duplicates")
    for unsupported_name in ("commands", "hooks"):
        unsupported_path = root / unsupported_name
        if os.path.lexists(str(unsupported_path)):
            raise ToolkitError("unsupported root asset: %s" % unsupported_path)


    groups_root = root / "groups"
    _directory_nonsymlink(groups_root, "groups directory")
    try:
        actual_groups = {entry.name for entry in os.scandir(groups_root)}
    except OSError as exc:
        raise ToolkitError("cannot inspect groups directory: %s" % exc)
    unknown_groups = actual_groups - set(group_names)
    if unknown_groups:
        raise ToolkitError("unsupported group asset(s): %s" % ", ".join(sorted(unknown_groups)))
    missing_groups = set(group_names) - actual_groups
    if missing_groups:
        raise ToolkitError("missing group directory(ies): %s" % ", ".join(sorted(missing_groups)))

    groups: Dict[str, dict] = {}
    skills: Dict[str, Path] = {}
    skill_groups: Dict[str, str] = {}
    for group_name in group_names:
        group_dir = groups_root / group_name
        _directory_nonsymlink(group_dir, "group directory")
        group_manifest_path = group_dir / "group.json"
        _regular_nonsymlink(group_manifest_path, "group manifest")
        group_manifest = _json_object(group_manifest_path, "group manifest")
        expected_group_keys = {"name", "description", "skills"}
        extra_group = set(group_manifest) - expected_group_keys
        missing_group = expected_group_keys - set(group_manifest)
        if extra_group:
            raise ToolkitError("unsupported key(s) in %s: %s" % (group_manifest_path, ", ".join(sorted(extra_group))))
        if missing_group:
            raise ToolkitError("missing key(s) in %s: %s" % (group_manifest_path, ", ".join(sorted(missing_group))))
        if group_manifest["name"] != group_name:
            raise ToolkitError("group manifest name does not match directory: %s" % group_manifest_path)
        _safe_name(group_manifest["name"], "group")
        if not isinstance(group_manifest["description"], str) or not group_manifest["description"].strip():
            raise ToolkitError("group description must be a non-empty string: %s" % group_manifest_path)
        group_skills_value = group_manifest["skills"]
        if not isinstance(group_skills_value, list) or not group_skills_value:
            raise ToolkitError("group skills must be a non-empty list: %s" % group_manifest_path)
        group_skills = [_safe_name(value, "skill") for value in group_skills_value]
        if len(set(group_skills)) != len(group_skills):
            raise ToolkitError("duplicate skills in %s" % group_manifest_path)

        try:
            group_entries = {entry.name for entry in os.scandir(group_dir)}
        except OSError as exc:
            raise ToolkitError("cannot inspect group directory %s: %s" % (group_dir, exc))
        unsupported_group_entries = group_entries - {"group.json", "skills"}
        if unsupported_group_entries:
            raise ToolkitError("unsupported asset(s) in group %s: %s" % (group_name, ", ".join(sorted(unsupported_group_entries))))
        skill_root = group_dir / "skills"
        _directory_nonsymlink(skill_root, "skills directory")
        actual_skills = {entry.name for entry in os.scandir(skill_root)}
        unknown_skills = actual_skills - set(group_skills)
        if unknown_skills:
            raise ToolkitError("unsupported skill asset(s) in group %s: %s" % (group_name, ", ".join(sorted(unknown_skills))))
        missing_skills = set(group_skills) - actual_skills
        if missing_skills:
            raise ToolkitError("missing skill directory(ies) in group %s: %s" % (group_name, ", ".join(sorted(missing_skills))))
        for skill_name in group_skills:
            if skill_name in skill_groups:
                raise ToolkitError("skill name is not globally unique: %s" % skill_name)
            skill_dir = skill_root / skill_name
            _directory_nonsymlink(skill_dir, "skill directory")
            skill_file = skill_dir / "SKILL.md"
            _regular_nonsymlink(skill_file, "skill SKILL.md")
            _reject_source_symlinks(skill_dir)
            skills[skill_name] = skill_dir
            skill_groups[skill_name] = group_name
        groups[group_name] = {
            "name": group_name,
            "description": group_manifest["description"],
            "skills": group_skills,
        }

    return Source(root, groups, skills, skill_groups, _git_revision(root))


def state_dir(home: Path) -> Path:
    return home / STATE_RELATIVE


def state_path(home: Path) -> Path:
    return state_dir(home) / STATE_FILENAME


def lock_path(home: Path) -> Path:
    return state_dir(home) / LOCK_FILENAME


def _absolute_home(value: Optional[str]) -> Path:
    raw = value if value is not None else os.environ.get("HOME")
    if not raw:
        raw = str(Path.home())
    # abspath removes lexical '..' without following a user-controlled link.
    home = Path(os.path.abspath(os.path.expanduser(raw)))
    if home == Path("/"):
        raise ToolkitError("refusing to use filesystem root as HOME")
    _check_existing_ancestors(home, allow_final_missing=True)
    if home.exists() and (home.is_symlink() or not home.is_dir()):
        raise ToolkitError("home must be a real directory, not a symlink or file: %s" % home)
    return home


def _check_existing_ancestors(path: Path, allow_final_missing: bool = True) -> None:
    path = path.absolute()
    parts = path.parts
    cursor = Path(parts[0])
    for part in parts[1:]:
        cursor = cursor / part
        try:
            info = os.lstat(cursor)
        except FileNotFoundError:
            if allow_final_missing:
                # A later component cannot exist while this one is missing.
                return
            raise ToolkitError("missing path component: %s" % cursor)
        except OSError as exc:
            raise ToolkitError("cannot inspect path component %s: %s" % (cursor, exc))
        if stat.S_ISLNK(info.st_mode):
            raise ToolkitError("symlinked parent is not allowed: %s" % cursor)
        if not stat.S_ISDIR(info.st_mode) and cursor != path:
            raise ToolkitError("path component is not a directory: %s" % cursor)


def _check_home_path(home: Path, path: Path, allow_final_missing: bool = True) -> None:
    home = home.absolute()
    path = path.absolute()
    try:
        if os.path.commonpath([str(home), str(path)]) != str(home):
            raise ToolkitError("destination escapes home: %s" % path)
    except ValueError:
        raise ToolkitError("destination escapes home: %s" % path)
    relative = path.relative_to(home)
    cursor = home
    try:
        home_info = os.lstat(home)
        if stat.S_ISLNK(home_info.st_mode):
            raise ToolkitError("home is a symlink: %s" % home)
    except FileNotFoundError:
        pass
    for index, part in enumerate(relative.parts):
        cursor = cursor / part
        is_final = index == len(relative.parts) - 1
        try:
            info = os.lstat(cursor)
        except FileNotFoundError:
            if allow_final_missing:
                return
            raise ToolkitError("missing destination component: %s" % cursor)
        if stat.S_ISLNK(info.st_mode) and not is_final:
            raise ToolkitError("symlinked destination parent is not allowed: %s" % cursor)
        if not is_final and not stat.S_ISDIR(info.st_mode):
            raise ToolkitError("destination parent is not a directory: %s" % cursor)


def _validate_home_layout(home: Path, paths: Iterable[Path]) -> None:
    _check_existing_ancestors(home, allow_final_missing=True)
    for path in paths:
        _check_home_path(home, path, allow_final_missing=True)


def read_state(home: Path, allow_missing: bool = True) -> dict:
    path = state_path(home)
    _validate_home_layout(home, [path])
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if allow_missing:
            return empty_state()
        raise ToolkitError("missing installation state: %s" % path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ToolkitError("installation state must be a regular file: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ToolkitError("invalid installation state %s: %s" % (path, exc))
    validate_state(value)
    return value


def empty_state() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_root": None,
        "revision": None,
        "selections": [],
        "assets": {},
    }


def validate_state(value: object) -> None:
    if not isinstance(value, dict):
        raise ToolkitError("installation state must be a JSON object")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ToolkitError("unsupported installation state schema_version")
    selections = value.get("selections")
    if not isinstance(selections, list):
        raise ToolkitError("installation state selections must be a list")
    seen: Set[Tuple[str, str]] = set()
    for item in selections:
        if not isinstance(item, dict) or set(item) != {"group", "agent"}:
            raise ToolkitError("invalid selection entry in installation state")
        group = _safe_name(item["group"], "group")
        agent = item["agent"]
        if agent not in AGENTS:
            raise ToolkitError("invalid agent in installation state: %r" % agent)
        pair = (group, agent)
        if pair in seen:
            raise ToolkitError("duplicate selection in installation state: %s/%s" % pair)
        seen.add(pair)
    assets = value.get("assets")
    if not isinstance(assets, dict):
        raise ToolkitError("installation state assets must be an object")
    for skill, metadata in assets.items():
        _safe_name(skill, "skill")
        if not isinstance(metadata, dict):
            raise ToolkitError("invalid asset metadata for %s" % skill)
        expected = {"owners", "hash", "sourcepath", "revision"}
        if set(metadata) != expected:
            raise ToolkitError("invalid asset metadata keys for %s" % skill)
        digest = metadata["hash"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ToolkitError("invalid asset hash for %s" % skill)
        if not isinstance(metadata["sourcepath"], str) or not metadata["sourcepath"]:
            raise ToolkitError("invalid asset sourcepath for %s" % skill)
        if metadata["revision"] is not None and not isinstance(metadata["revision"], str):
            raise ToolkitError("invalid asset revision for %s" % skill)
        owners = metadata["owners"]
        if not isinstance(owners, list) or not owners:
            raise ToolkitError("asset %s must have at least one owner" % skill)
        owner_seen: Set[Tuple[str, str]] = set()
        for owner in owners:
            if not isinstance(owner, dict) or set(owner) != {"group", "agent"}:
                raise ToolkitError("invalid owner for asset %s" % skill)
            pair = (_safe_name(owner["group"], "group"), owner["agent"])
            if pair[1] not in AGENTS or pair in owner_seen:
                raise ToolkitError("invalid or duplicate owner for asset %s" % skill)
            owner_seen.add(pair)
    if "source_root" in value and value["source_root"] is not None and not isinstance(value["source_root"], str):
        raise ToolkitError("invalid installation state source_root")
    if "revision" in value and value["revision"] is not None and not isinstance(value["revision"], str):
        raise ToolkitError("invalid installation state revision")


def _hash_tree(root: Path) -> str:
    """Hash relative names, types, permission bits and file bytes."""
    digest = hashlib.sha256()
    root_info = os.lstat(root)
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise ToolkitError("asset is not a real directory: %s" % root)

    def visit(path: Path, relative: str) -> None:
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode):
            raise ToolkitError("symlink in asset: %s" % path)
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISDIR(info.st_mode):
            kind = "D"
        elif stat.S_ISREG(info.st_mode):
            kind = "F"
        else:
            raise ToolkitError("unsupported asset type: %s" % path)
        digest.update((kind + "\0" + relative + "\0" + oct(mode) + "\0").encode("utf-8"))
        if kind == "F":
            with open(path, "rb") as handle:
                while True:
                    block = handle.read(1024 * 1024)
                    if not block:
                        break
                    digest.update(block)
        else:
            entries = sorted(os.scandir(path), key=lambda item: item.name)
            for entry in entries:
                child = Path(entry.path)
                child_rel = entry.name if not relative else relative + "/" + entry.name
                visit(child, child_rel)

    visit(root, ".")
    return digest.hexdigest()


def copy_tree(source: Path, destination: Path) -> None:
    """Copy a validated skill tree while preserving permission bits."""
    source_info = os.lstat(source)
    if stat.S_ISLNK(source_info.st_mode) or not stat.S_ISDIR(source_info.st_mode):
        raise ToolkitError("cannot copy non-directory skill: %s" % source)
    os.mkdir(str(destination), stat.S_IMODE(source_info.st_mode))
    for entry in sorted(os.scandir(source), key=lambda item: item.name):
        source_child = Path(entry.path)
        destination_child = destination / entry.name
        info = entry.stat(follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            raise ToolkitError("source symlinks are not supported: %s" % source_child)
        if stat.S_ISDIR(info.st_mode):
            copy_tree(source_child, destination_child)
        elif stat.S_ISREG(info.st_mode):
            shutil.copyfile(str(source_child), str(destination_child))
            os.chmod(str(destination_child), stat.S_IMODE(info.st_mode))
        else:
            raise ToolkitError("unsupported source asset type: %s" % source_child)
    os.chmod(str(destination), stat.S_IMODE(source_info.st_mode))


def _selection_pairs(value: Iterable[Tuple[str, str]]) -> List[dict]:
    return [{"group": group, "agent": agent} for group, agent in sorted(set(value))]


def _state_pairs(state: dict) -> Set[Tuple[str, str]]:
    return {(item["group"], item["agent"]) for item in state["selections"]}


def _owners_for(source: Source, selections: Set[Tuple[str, str]]) -> Dict[str, Set[Tuple[str, str]]]:
    owners: Dict[str, Set[Tuple[str, str]]] = {}
    for group, agent in selections:
        if group not in source.groups:
            raise ToolkitError("selected group is missing from source: %s" % group)
        for skill in source.groups[group]["skills"]:
            owners.setdefault(skill, set()).add((group, agent))
    return owners


def _check_source_removals(source: Source, state: dict) -> None:
    """A previous owner disappearing from its current group is never implicit."""
    for skill, metadata in state["assets"].items():
        for owner in metadata["owners"]:
            group = owner["group"]
            if group not in source.groups:
                raise ToolkitError("source group removed for installed asset %s: %s" % (skill, group))
            if skill not in source.groups[group]["skills"]:
                raise ToolkitError("source asset removed from group %s: %s" % (group, skill))
    for item in state["selections"]:
        if item["group"] not in source.groups:
            raise ToolkitError("source group removed for recorded selection: %s" % item["group"])


def _canonical(home: Path, skill: str) -> Path:
    return home / ".agents" / "skills" / skill


def _compat(home: Path, agent: str, skill: str) -> Path:
    if agent == "omp":
        return home / ".omp" / "agent" / "skills" / skill
    if agent == "claude":
        return home / ".claude" / "skills" / skill
    raise ToolkitError("codex has no compatibility link")


def _link_points_to(path: Path, target: Path) -> bool:
    if not path.is_symlink():
        return False
    try:
        return Path(os.path.realpath(str(path))) == Path(os.path.realpath(str(target)))
    except OSError:
        return False


def _kind(path: Path) -> str:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return "missing"
    if stat.S_ISLNK(info.st_mode):
        return "symlink"
    if stat.S_ISDIR(info.st_mode):
        return "directory"
    if stat.S_ISREG(info.st_mode):
        return "file"
    return "other"


def _preflight_link(path: Path, target: Path, expected_hashes: Set[str], action: str) -> str:
    kind = _kind(path)
    if kind == "missing":
        return "create"
    if kind == "symlink":
        if _link_points_to(path, target):
            return "keep"
        raise ToolkitError("%s symlink points elsewhere: %s" % (action, path))
    if kind == "directory":
        try:
            actual = _hash_tree(path)
        except ToolkitError:
            raise
        if actual in expected_hashes:
            return "replace"
        raise ToolkitError("%s directory is modified or unsafe: %s" % (action, path))
    raise ToolkitError("%s destination is not a directory or link: %s" % (action, path))


def _preflight_canonical(path: Path, source_path: Path, source_hash: str,
                         metadata: Optional[dict], explicit_update: bool) -> str:
    kind = _kind(path)
    if kind == "missing":
        if metadata is not None and metadata["hash"] != source_hash and not explicit_update:
            raise ToolkitError("managed asset is missing while source changed; run update: %s" % path.name)
        return "copy"
    if kind != "directory":
        raise ToolkitError("canonical asset is not a real directory: %s" % path)
    actual = _hash_tree(path)
    if metadata is None:
        if actual != source_hash:
            raise ToolkitError("existing canonical asset conflicts with source: %s" % path)
        return "adopt"
    if actual != metadata["hash"]:
        raise ToolkitError("managed asset was modified: %s" % path)
    if explicit_update and source_hash != metadata["hash"]:
        return "replace"
    return "keep"


def _asset_metadata(source: Source, skill: str, owners: Set[Tuple[str, str]],
                    digest: str, sourcepath: str, revision: Optional[str]) -> dict:
    return {
        "owners": [{"group": group, "agent": agent} for group, agent in sorted(owners)],
        "hash": digest,
        "sourcepath": sourcepath,
        "revision": revision,
    }


def _state_for(source: Source, home: Path, selections: Set[Tuple[str, str]], assets: Dict[str, dict]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_root": str(source.root),
        "revision": source.revision,
        "selections": _selection_pairs(selections),
        "assets": {skill: assets[skill] for skill in sorted(assets)},
    }


def _all_expected_paths(home: Path, skills: Iterable[str]) -> List[Path]:
    paths: List[Path] = [state_path(home), lock_path(home)]
    for skill in skills:
        paths.append(_canonical(home, skill))
        for agent in COMPAT_AGENTS:
            paths.append(_compat(home, agent, skill))
    return paths


def _build_plan(source: Source, home: Path, state: dict, operation: str,
                requested_pairs: Optional[Set[Tuple[str, str]]] = None,
                all_groups: bool = False,
                explicit_update: bool = False) -> Tuple[dict, dict]:
    """Return (new state, plan), without mutating the filesystem."""
    old_pairs = _state_pairs(state)
    if operation in ("install", "update"):
        _check_source_removals(source, state)
    if operation == "install":
        if not requested_pairs:
            raise ToolkitError("install requires at least one explicit agent")
        desired_pairs = old_pairs | requested_pairs
    elif operation == "update":
        desired_pairs = set(old_pairs)
        if not desired_pairs:
            return _state_for(source, home, desired_pairs, {}), {"copy": [], "links": [], "remove": [], "state": True, "changed": False}
    elif operation == "uninstall":
        if all_groups:
            remove_pairs = set(old_pairs)
            if requested_pairs:
                remove_pairs = {pair for pair in remove_pairs if pair[1] in {agent for _, agent in requested_pairs}}
        else:
            remove_pairs = set(requested_pairs or set())
        desired_pairs = old_pairs - remove_pairs
    else:
        raise ToolkitError("unknown operation: %s" % operation)

    # Validate all requested groups before deriving ownership.  Uninstall can
    # remove a source group that is still recorded, but source validation has
    # already happened and update/install explicitly reject removed groups.
    for group, agent in desired_pairs:
        if group not in source.groups:
            if operation == "uninstall" and group in {p[0] for p in old_pairs}:
                continue
            raise ToolkitError("unknown group: %s" % group)
        if agent not in AGENTS:
            raise ToolkitError("unknown agent: %s" % agent)

    old_assets = state["assets"]
    owners = _owners_for(source, desired_pairs) if desired_pairs else {}
    all_skills = set(old_assets) | set(owners)
    _validate_home_layout(home, _all_expected_paths(home, all_skills))

    plan = {"copy": [], "links": [], "remove": [], "state": True, "changed": True}
    new_assets: Dict[str, dict] = {}
    installed_hashes: Dict[str, str] = {}

    # Canonical copies and removals.  A stale managed asset with no remaining
    # owners is removed only after its old bytes are verified unchanged.
    for skill in sorted(all_skills):
        metadata = old_assets.get(skill)
        active_owners = owners.get(skill, set())
        if not active_owners:
            if metadata is not None:
                canonical_path = _canonical(home, skill)
                if _kind(canonical_path) == "directory":
                    if _hash_tree(canonical_path) != metadata["hash"]:
                        raise ToolkitError("managed asset was modified: %s" % canonical_path)
                elif _kind(canonical_path) not in ("missing",):
                    raise ToolkitError("managed asset is unsafe: %s" % canonical_path)
                plan["remove"].append(canonical_path)
                # Remove only links that point to this canonical asset.  A
                # different symlink is unrelated and is rejected if it occupies
                # the destination below.
                for agent in COMPAT_AGENTS:
                    link_path = _compat(home, agent, skill)
                    if _kind(link_path) == "symlink":
                        if _link_points_to(link_path, canonical_path):
                            plan["remove"].append(link_path)
                        else:
                            raise ToolkitError("managed compatibility link points elsewhere: %s" % link_path)
                    elif _kind(link_path) not in ("missing",):
                        raise ToolkitError("managed compatibility destination is not a link: %s" % link_path)
            continue

        if skill not in source.skills:
            raise ToolkitError("source asset is missing for selected group: %s" % skill)
        source_path = source.skills[skill]
        source_hash = _hash_tree(source_path)
        canonical_path = _canonical(home, skill)
        action = _preflight_canonical(canonical_path, source_path, source_hash, metadata, explicit_update)
        if action in ("copy", "replace"):
            plan["copy"].append((canonical_path, source_path, source_hash))
            installed_hashes[skill] = source_hash
        elif metadata is not None:
            installed_hashes[skill] = metadata["hash"]
        else:
            installed_hashes[skill] = source_hash

        if metadata is not None and action not in ("replace",) and metadata["hash"] != installed_hashes[skill]:
            raise ToolkitError("cannot infer unchanged managed asset: %s" % skill)

        if metadata is not None and action != "replace":
            asset_sourcepath = metadata["sourcepath"]
            asset_revision = metadata["revision"]
        else:
            asset_sourcepath = str(source_path)
            asset_revision = source.revision
        new_assets[skill] = _asset_metadata(
            source, skill, active_owners, installed_hashes[skill], asset_sourcepath, asset_revision
        )

    # Compatibility links are determined by the resulting selection union.
    desired_link_agents: Dict[str, Set[str]] = {skill: set() for skill in owners}
    for group, agent in desired_pairs:
        for skill in source.groups[group]["skills"]:
            if agent in COMPAT_AGENTS:
                desired_link_agents.setdefault(skill, set()).add(agent)
    # Existing managed links not needed by the new selection are removed.
    for skill in sorted(all_skills):
        canonical_path = _canonical(home, skill)
        for agent in COMPAT_AGENTS:
            link_path = _compat(home, agent, skill)
            kind = _kind(link_path)
            should_exist = agent in desired_link_agents.get(skill, set())
            if should_exist:
                expected_hashes = {installed_hashes[skill]}
                if metadata := old_assets.get(skill):
                    expected_hashes.add(metadata["hash"])
                action = _preflight_link(link_path, canonical_path, expected_hashes, "compatibility")
                if action == "replace":
                    plan["links"].append((link_path, canonical_path))
                elif action == "create":
                    plan["links"].append((link_path, canonical_path))
            elif kind == "symlink":
                if _link_points_to(link_path, canonical_path):
                    plan["remove"].append(link_path)
                elif skill in old_assets:
                    raise ToolkitError("managed compatibility link points elsewhere: %s" % link_path)
            elif kind == "directory" and skill in old_assets:
                # Do not recursively delete a directory just because a link is
                # no longer desired.  It may be unrelated user data.
                raise ToolkitError("managed compatibility destination is not a link: %s" % link_path)
            elif kind not in ("missing",):
                raise ToolkitError("compatibility destination is unsafe: %s" % link_path)

    # A state file is written even for an empty selection so that uninstall is
    # explicit and subsequent status/doctor can explain the empty installation.
    new_state = _state_for(source, home, desired_pairs, new_assets)
    return new_state, plan


def _execute_plan(home: Path, new_state: dict, plan: dict) -> None:
    with Transaction(home) as transaction:
        # Remove compatibility links before canonical trees.  remove() merely
        # backs up, making rollback deterministic.
        for path in plan["remove"]:
            transaction.remove(path)
        for destination, source, expected_hash in plan["copy"]:
            transaction.replace_with_tree(destination, source, expected_hash)
        for destination, target in plan["links"]:
            transaction.replace_with_link(destination, target)
        transaction.write_state(state_path(home), new_state)
        transaction.commit()


def _acquire_mutation_lock(home: Path) -> ProcessLock:
    _validate_home_layout(home, [state_dir(home), lock_path(home), state_path(home)])
    parent = state_dir(home)
    if not parent.exists():
        # This is the only normal control-directory creation.  Dry-run never
        # calls this function.  The lock inode remains persistent so a waiter
        # cannot be separated from a failed transaction by unlinking it.
        parent.mkdir(parents=True, mode=0o755)
        _check_home_path(home, parent, allow_final_missing=False)
    return ProcessLock(lock_path(home))


def _run_mutation(source: Source, home: Path, operation: str,
                  requested_pairs: Optional[Set[Tuple[str, str]]] = None,
                  all_groups: bool = False, dry_run: bool = False) -> int:
    state = read_state(home)
    new_state, plan = _build_plan(
        source, home, state, operation, requested_pairs, all_groups,
        explicit_update=(operation == "update"),
    )
    if dry_run:
        _print_plan(operation, state, new_state, plan, dry_run=True)
        return 0

    # Preflight above avoids creating even the persistent lock on ordinary
    # validation/conflict failures.  Re-read and rebuild once locked so a
    # concurrent toolkit invocation cannot publish a stale plan.
    lock = _acquire_mutation_lock(home)
    with lock:
        state = read_state(home)
        new_state, plan = _build_plan(
            source, home, state, operation, requested_pairs, all_groups,
            explicit_update=(operation == "update"),
        )
        _execute_plan(home, new_state, plan)
        _print_plan(operation, state, new_state, plan, dry_run=False)
    return 0


def _print_plan(operation: str, old_state: dict, new_state: dict, plan: dict, dry_run: bool) -> None:
    prefix = "would " if dry_run else ""
    old_pairs = _state_pairs(old_state)
    new_pairs = _state_pairs(new_state)
    added = sorted(new_pairs - old_pairs)
    removed = sorted(old_pairs - new_pairs)
    print("%s%s:" % (prefix.capitalize() if prefix else "", operation))
    if added:
        print("  %sselections added: %s" % (prefix, ", ".join("%s/%s" % pair for pair in added)))
    if removed:
        print("  %sselections removed: %s" % (prefix, ", ".join("%s/%s" % pair for pair in removed)))
    if plan["copy"]:
        print("  %sassets copied: %s" % (prefix, ", ".join(item[0].name for item in plan["copy"])))
    if plan["links"]:
        print("  %slinks created/replaced: %d" % (prefix, len(plan["links"])))
    if plan["remove"]:
        print("  %spaths removed: %d" % (prefix, len(plan["remove"])))
    if not (added or removed or plan["copy"] or plan["links"] or plan["remove"]):
        print("  no changes")


def _status_details(source: Source, home: Path, state: dict) -> List[str]:
    details: List[str] = []
    try:
        _check_source_removals(source, state)
    except ToolkitError as exc:
        details.append(str(exc))
    selections = _state_pairs(state)
    try:
        owners = _owners_for(source, selections)
    except ToolkitError as exc:
        details.append(str(exc))
        owners = {}
    for skill in sorted(set(owners) - set(state["assets"])):
        details.append("missing installed asset state: %s" % skill)
        canonical_path = _canonical(home, skill)
        if _kind(canonical_path) != "directory":
            details.append("missing canonical asset: %s" % skill)
    for skill, metadata in state["assets"].items():
        expected = owners.get(skill, set())
        actual_owners = {(item["group"], item["agent"]) for item in metadata["owners"]}
        if actual_owners != expected:
            details.append("asset ownership drift: %s" % skill)
        canonical_path = _canonical(home, skill)
        kind = _kind(canonical_path)
        if kind == "missing":
            details.append("missing canonical asset: %s" % skill)
        elif kind != "directory":
            details.append("unsafe canonical asset: %s" % skill)
        else:
            try:
                if _hash_tree(canonical_path) != metadata["hash"]:
                    details.append("modified canonical asset: %s" % skill)
            except ToolkitError as exc:
                details.append(str(exc))
        source_path = source.skills.get(skill)
        if source_path is None:
            details.append("missing source asset: %s" % skill)
        else:
            try:
                if _hash_tree(source_path) != metadata["hash"]:
                    details.append("source changed (run update): %s" % skill)
            except ToolkitError as exc:
                details.append(str(exc))

    desired_links: Set[Tuple[str, str]] = set()
    for group, agent in selections:
        if group not in source.groups:
            continue
        for skill in source.groups[group]["skills"]:
            if agent in COMPAT_AGENTS:
                desired_links.add((agent, skill))
    known_skills = set(state["assets"]) | set(owners) | {skill for _, skill in desired_links}
    for skill in sorted(known_skills):
        target = _canonical(home, skill)
        for agent in COMPAT_AGENTS:
            path = _compat(home, agent, skill)
            should_exist = (agent, skill) in desired_links
            kind = _kind(path)
            if should_exist:
                if kind != "symlink" or not _link_points_to(path, target):
                    details.append("broken compatibility link: %s" % path)
            elif kind == "symlink" and _link_points_to(path, target):
                details.append("stale compatibility link: %s" % path)
            elif kind not in ("missing", "symlink"):
                details.append("unexpected compatibility destination: %s" % path)
            elif kind == "symlink" and skill in state["assets"]:
                details.append("compatibility link points elsewhere: %s" % path)
    return details


def command_list(source: Source) -> int:
    print("%s groups:" % source.root.name)
    for group_name in source.group_names:
        group = source.groups[group_name]
        print("%s - %s" % (group_name, group["description"]))
        for skill in group["skills"]:
            print("  - %s" % skill)
    return 0


def command_status(source: Source, home: Path) -> int:
    state = read_state(home)
    selections = _state_pairs(state)
    if not selections:
        print("No installed selections.")
        return 0
    print("Selections: %s" % ", ".join("%s/%s" % pair for pair in sorted(selections)))
    details = _status_details(source, home, state)
    if details:
        print("Drift detected:")
        for detail in sorted(set(details)):
            print("  - %s" % detail)
        return 1
    print("Assets and compatibility links are in sync.")
    return 0


def command_doctor(source: Source, home: Path) -> int:
    issues: List[str] = []
    try:
        state = read_state(home)
    except ToolkitError as exc:
        print("Doctor:")
        print("  - %s" % exc)
        return 1
    issues.extend(_status_details(source, home, state))
    if issues:
        print("Doctor found issues:")
        for issue in sorted(set(issues)):
            print("  - %s" % issue)
        return 1
    print("Doctor: no manifest, source, state, or managed-link issues found.")
    return 0


def _normalise_home_args(argv: Sequence[str]) -> Tuple[Optional[str], List[str]]:
    """Accept --home before or after a subcommand without argparse quirks."""
    home: Optional[str] = None
    result: List[str] = []
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--home":
            if index + 1 >= len(argv):
                raise ToolkitError("--home requires a path")
            home = argv[index + 1]
            index += 2
            continue
        if argument.startswith("--home="):
            home = argument.split("=", 1)[1]
            if not home:
                raise ToolkitError("--home requires a path")
            index += 1
            continue
        result.append(argument)
        index += 1
    return home, result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manage.py",
        description="Safely install the agent-toolkit skills into an isolated agent HOME.",
    )
    parser.add_argument("--home", help="target HOME (accepted before or after the command)")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list", help="list source groups and skills")

    install = commands.add_parser("install", help="add group/agent bindings and install skill copies")
    install.add_argument("--group", action="append", default=[], help="group to enable (repeatable)")
    install.add_argument("--all", dest="all_groups", action="store_true", help="enable every source group")
    install.add_argument("--agent", action="append", choices=AGENTS, default=[], help="agent binding (repeatable; required)")
    install.add_argument("--dry-run", action="store_true", help="show changes without creating locks or files")

    update = commands.add_parser("update", help="update copies for recorded selections only")
    update.add_argument("--dry-run", action="store_true", help="show changes without creating locks or files")

    status = commands.add_parser("status", help="report selections, source drift, and managed-link drift")
    status.set_defaults()

    doctor = commands.add_parser("doctor", help="validate manifests, state, sources, copies, and links")
    doctor.set_defaults()

    uninstall = commands.add_parser("uninstall", help="remove group/agent bindings and unneeded skills")
    uninstall.add_argument("--group", action="append", default=[], help="group to remove (repeatable)")
    uninstall.add_argument("--all", dest="all_groups", action="store_true", help="remove all recorded groups")
    uninstall.add_argument("--agent", action="append", choices=AGENTS, default=[], help="limit removal to these agents (repeatable)")
    uninstall.add_argument("--dry-run", action="store_true", help="show changes without creating locks or files")
    return parser


def _requested_install_pairs(source: Source, groups: List[str], agents: List[str], all_groups: bool) -> Set[Tuple[str, str]]:
    if not agents:
        raise ToolkitError("install requires at least one explicit --agent")
    if all_groups:
        selected_groups = set(source.groups)
    else:
        if not groups:
            raise ToolkitError("install requires --group or --all")
        selected_groups = set(groups)
    for group in selected_groups:
        if group not in source.groups:
            raise ToolkitError("unknown group: %s" % group)
    return {(group, agent) for group in selected_groups for agent in agents}


def _requested_uninstall_pairs(source: Source, state: dict, groups: List[str], agents: List[str], all_groups: bool) -> Tuple[Set[Tuple[str, str]], bool]:
    old_pairs = _state_pairs(state)
    if all_groups:
        if groups:
            raise ToolkitError("uninstall cannot combine --all and --group")
        # An omitted --agent means every recorded agent.  With --agent, remove
        # only those agent bindings while preserving other agents.
        if agents:
            return {(group, agent) for group, agent in old_pairs if agent in set(agents)}, False
        return set(old_pairs), False
    if not groups:
        raise ToolkitError("uninstall requires --group or --all")
    unknown = set(groups) - {group for group, _ in old_pairs}
    if unknown:
        raise ToolkitError("group is not recorded: %s" % ", ".join(sorted(unknown)))
    if agents:
        return {(group, agent) for group, agent in old_pairs if group in set(groups) and agent in set(agents)}, False
    # The _build_plan API uses requested pairs as removals.  Omission of
    # --agent removes the selected group from every recorded agent.
    return {(group, agent) for group, agent in old_pairs if group in set(groups)}, False


def main(argv: Optional[Sequence[str]] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        home_override, normalised = _normalise_home_args(raw)
        parser = build_parser()
        arguments = parser.parse_args(normalised)
        home = _absolute_home(home_override or arguments.home)
        source = load_source()

        if arguments.command == "list":
            return command_list(source)
        if arguments.command == "status":
            return command_status(source, home)
        if arguments.command == "doctor":
            return command_doctor(source, home)
        if arguments.command == "install":
            groups = arguments.group
            if arguments.all_groups and groups:
                raise ToolkitError("install cannot combine --all and --group")
            requested = _requested_install_pairs(source, groups, arguments.agent, arguments.all_groups)
            return _run_mutation(source, home, "install", requested, False, arguments.dry_run)
        if arguments.command == "update":
            return _run_mutation(source, home, "update", None, False, arguments.dry_run)
        if arguments.command == "uninstall":
            # Uninstall is allowed to operate on an empty state, but still
            # validates source manifests so unsupported assets are explicit.
            state = read_state(home)
            requested, _ = _requested_uninstall_pairs(source, state, arguments.group, arguments.agent, arguments.all_groups)
            return _run_mutation(source, home, "uninstall", requested, False, arguments.dry_run)
        raise ToolkitError("unknown command")
    except ToolkitError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
