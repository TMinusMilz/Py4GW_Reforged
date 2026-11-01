"""Small indexed offline knowledge layer for HeroAI.

The data is advisory and fails open. JSON is loaded once, indexed in memory, and
never scanned linearly during combat.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from Py4GWCoreLib.Skill import Skill

_BASE = Path(__file__).resolve().parent
_LOADED = False
_AREA: dict[int, dict[str, Any]] = {}
_SKILL_BY_ID: dict[int, dict[str, Any]] = {}
_ENEMY_MODELS: dict[int, dict[str, Any]] = {}
_ENEMY_MAP_MODELS: dict[tuple[int, int], dict[str, Any]] = {}
_NAME_TOKENS: dict[str, dict[str, Any]] = {}


def _load_json(name: str) -> dict[str, Any]:
    try:
        return json.loads((_BASE / name).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _ensure_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True

    area = _load_json("area_profiles.json").get("profiles", {})
    for key, value in area.items():
        try:
            _AREA[int(key)] = dict(value)
        except Exception:
            continue

    skills = _load_json("skill_profiles.json").get("profiles", {})
    for skill_name, value in skills.items():
        profile = dict(value)
        try:
            skill_id = int(profile.get("skill_id", 0) or 0)
        except Exception:
            skill_id = 0
        if skill_id <= 0:
            try:
                skill_id = int(Skill.GetID(str(skill_name)) or 0)
            except Exception:
                skill_id = 0
        if skill_id > 0:
            profile["name"] = str(skill_name)
            _SKILL_BY_ID[skill_id] = profile

    enemies = _load_json("enemy_profiles.json")
    for key, value in enemies.get("models", {}).items():
        try:
            _ENEMY_MODELS[int(key)] = dict(value)
        except Exception:
            continue
    for key, value in enemies.get("map_models", {}).items():
        try:
            map_raw, model_raw = str(key).split(":", 1)
            _ENEMY_MAP_MODELS[(int(map_raw), int(model_raw))] = dict(value)
        except Exception:
            continue
    for token, value in enemies.get("named_tokens", {}).items():
        _NAME_TOKENS[str(token).casefold()] = dict(value)


def area_profile(map_id: int) -> dict[str, Any]:
    _ensure_loaded()
    return dict(_AREA.get(int(map_id or 0), {}))


def skill_profile(skill_id: int) -> dict[str, Any]:
    _ensure_loaded()
    return dict(_SKILL_BY_ID.get(int(skill_id or 0), {}))


def enemy_profile(model_id: int, name: str = "", map_id: int = 0) -> dict[str, Any]:
    _ensure_loaded()
    merged = dict(_ENEMY_MODELS.get(int(model_id or 0), {}))
    scoped = _ENEMY_MAP_MODELS.get((int(map_id or 0), int(model_id or 0)))
    if scoped:
        merged.update(dict(scoped))
    lname = str(name or "").casefold()
    for token, profile in _NAME_TOKENS.items():
        if token and token in lname:
            for key, value in profile.items():
                if key == "roles":
                    roles = set(merged.get("roles", ()))
                    roles.update(value or ())
                    merged["roles"] = sorted(roles)
                elif key == "priority_bonus":
                    merged[key] = max(int(merged.get(key, 0) or 0), int(value or 0))
                else:
                    merged.setdefault(key, value)
    return merged

def has_exact_enemy_profile(model_id: int, map_id: int = 0) -> bool:
    """Return whether an exact indexed profile exists for this identity.

    When a map id is supplied, require the map-scoped (map, model) entry.
    This keeps first-contact proof markers from being emitted from a weaker
    global model-only match.  Callers without a map may still query the global
    model index explicitly.
    """
    _ensure_loaded()
    mid = int(model_id or 0)
    mp = int(map_id or 0)
    if mid <= 0:
        return False
    if mp > 0:
        return (mp, mid) in _ENEMY_MAP_MODELS
    return mid in _ENEMY_MODELS


def registered_interrupt_skill_ids(min_score: int = 0) -> frozenset[int]:
    """Return offline skills that are explicitly interruptible at/above threshold.

    This is built from the already-indexed skill table and is used only by the
    central DangerousSkillPriorities owner.  It performs no game queries.
    """
    _ensure_loaded()
    threshold = int(min_score)
    result: set[int] = set()
    for sid, profile in _SKILL_BY_ID.items():
        if profile.get("interruptible", True) is False:
            continue
        try:
            score = int(profile.get("interrupt_priority", 0) or 0)
        except Exception:
            score = 0
        if score >= threshold:
            result.add(int(sid))
    return frozenset(result)

