"""EXP36 Stage 13 / Astra E18: encounter knowledge in strict shadow mode.

This module is intentionally incapable of issuing combat actions.  It observes
verified map/name/skill facts and logs the response that *would* be considered
in a later A/B stage.  No target selection, skill request, movement or shared
claim is changed here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from Py4GWCoreLib.Skill import Skill

_BASE = Path(__file__).resolve().parents[1] / "Knowledge"
_PROFILE_PATH = _BASE / "encounter_profiles.json"
_LOADED = False
_PROFILES: dict[int, dict[str, Any]] = {}
_RESOLVED_SKILLS: dict[int, dict[int, dict[str, Any]]] = {}
_LAST_SESSION_EPOCH = 0
_LOGGED_ACTIVE: set[tuple[int, int]] = set()
_LOGGED_ENTITY: set[tuple[int, int, int, str]] = set()
_LOGGED_UNKNOWN_MODEL: set[tuple[int, int, int]] = set()
_ACTIVE_CASTS: dict[tuple[int, int], int] = {}
_LOGGED_ADVISORY: set[tuple[int, str]] = set()


def _load() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        data = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    for key, value in data.get("profiles", {}).items():
        try:
            _PROFILES[int(key)] = dict(value)
        except Exception:
            continue


def _session():
    global _LAST_SESSION_EPOCH
    try:
        from Py4GWCoreLib.Builds.Skills import CombatSession
        snap = CombatSession.refresh()
    except Exception:
        return None
    epoch = int(getattr(snap, "epoch", 0) or 0)
    if epoch > 0 and epoch != int(_LAST_SESSION_EPOCH or 0):
        _LAST_SESSION_EPOCH = epoch
        _LOGGED_ACTIVE.clear()
        _LOGGED_ENTITY.clear()
        _LOGGED_UNKNOWN_MODEL.clear()
        _ACTIVE_CASTS.clear()
        _LOGGED_ADVISORY.clear()
    return snap


def _log(event: str, **fields) -> None:
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(str(event), **fields)
    except Exception:
        pass


def _profile_for_session():
    _load()
    snap = _session()
    if snap is None or not bool(getattr(snap, "map_ready", False)):
        return None, None
    profile = _PROFILES.get(int(getattr(snap, "map_id", 0) or 0))
    if not profile or not bool(profile.get("shadow_only", True)):
        return snap, None
    active_key = (int(getattr(snap, "epoch", 0) or 0), int(getattr(snap, "map_id", 0) or 0))
    if active_key not in _LOGGED_ACTIVE:
        _LOGGED_ACTIVE.add(active_key)
        resolved = _resolved_watch_skills(int(getattr(snap, "map_id", 0) or 0), profile)
        _log(
            "ENCOUNTER_SHADOW_ACTIVE",
            session_epoch=int(getattr(snap, "epoch", 0) or 0),
            session_key=str(getattr(snap, "key", "")),
            map_id=int(getattr(snap, "map_id", 0) or 0),
            encounter=str(profile.get("name", "unknown")),
            watch_skill_ids=",".join(map(str, sorted(resolved.keys()))),
            unresolved_watch_skills=",".join(_unresolved_watch_skill_names(int(getattr(snap, "map_id", 0) or 0), profile)),
            policy="exp36_stage13_e18_shadow_only_no_gameplay_actions",
        )
    return snap, profile


def _resolved_watch_skills(map_id: int, profile: dict[str, Any]) -> dict[int, dict[str, Any]]:
    cached = _RESOLVED_SKILLS.get(int(map_id))
    if cached is not None:
        return cached
    resolved: dict[int, dict[str, Any]] = {}
    for name, raw in profile.get("watch_skills", {}).items():
        row = dict(raw)
        try:
            sid = int(row.get("skill_id", 0) or 0)
        except Exception:
            sid = 0
        if sid <= 0:
            try:
                sid = int(Skill.GetID(str(name)) or 0)
            except Exception:
                sid = 0
        if sid > 0:
            row["name"] = str(row.get("display_name") or name)
            resolved[sid] = row
    _RESOLVED_SKILLS[int(map_id)] = resolved
    return resolved


def _unresolved_watch_skill_names(map_id: int, profile: dict[str, Any]) -> list[str]:
    unresolved: list[str] = []
    resolved_ids = set(_resolved_watch_skills(int(map_id), profile).keys())
    for name, raw in profile.get("watch_skills", {}).items():
        try:
            explicit = int(dict(raw).get("skill_id", 0) or 0)
        except Exception:
            explicit = 0
        if explicit > 0 and explicit in resolved_ids:
            continue
        try:
            sid = int(Skill.GetID(str(name)) or 0)
        except Exception:
            sid = 0
        if sid <= 0 or sid not in resolved_ids:
            unresolved.append(str(name))
    return sorted(unresolved)


def observe_enemy(agent_id: int, *, model_id: int = 0, name: str = "", casting_skill_id: int = 0) -> None:
    """Observe catalog identity only.  Never influences EnemyKnowledge scores."""
    snap, profile = _profile_for_session()
    if snap is None or profile is None:
        return
    aid = int(agent_id or 0)
    mid = int(model_id or 0)
    lname = str(name or "").casefold()
    epoch = int(getattr(snap, "epoch", 0) or 0)

    matched = False
    for token, raw in profile.get("named_tokens", {}).items():
        if str(token).casefold() and str(token).casefold() in lname:
            key = (epoch, aid, mid, str(token).casefold())
            if key not in _LOGGED_ENTITY:
                _LOGGED_ENTITY.add(key)
                _log(
                    "ENCOUNTER_SHADOW_ENTITY_MATCH",
                    session_epoch=epoch, map_id=int(getattr(snap, "map_id", 0) or 0),
                    encounter=str(profile.get("name", "unknown")), agent_id=aid,
                    model_id=mid, name=str(name or "?"), token=str(token),
                    category=str(raw.get("category", "unknown")),
                    suggested_response=str(raw.get("suggested_response", "observe_only")),
                    policy="exp36_stage13_e18_shadow_only_no_gameplay_actions",
                )
            matched = True
            break

    verified_models = profile.get("verified_model_ids", {}) or {}
    model_row = verified_models.get(str(mid)) if mid > 0 else None
    if model_row:
        key = (epoch, aid, mid, "verified_model")
        if key not in _LOGGED_ENTITY:
            _LOGGED_ENTITY.add(key)
            _log(
                "ENCOUNTER_SHADOW_MODEL_MATCH",
                session_epoch=epoch, map_id=int(getattr(snap, "map_id", 0) or 0),
                encounter=str(profile.get("name", "unknown")), agent_id=aid,
                model_id=mid, name=str(name or model_row.get("name", "?")),
                category=str(model_row.get("category", "enemy")),
                source=str(model_row.get("source", "verified_catalog")),
                policy="exp36_stage16_e18_verified_live_model_catalog_no_action",
            )
        matched = True

    # Unknown model inventory is intentionally low-volume and observation-only;
    # it gives later manual review concrete model ids instead of guessing from names.
    if mid > 0 and not matched and bool(profile.get("log_unknown_models", True)):
        key = (epoch, int(getattr(snap, "map_id", 0) or 0), mid)
        if key not in _LOGGED_UNKNOWN_MODEL and len(_LOGGED_UNKNOWN_MODEL) < 64:
            _LOGGED_UNKNOWN_MODEL.add(key)
            _log(
                "ENCOUNTER_SHADOW_UNKNOWN_MODEL",
                session_epoch=epoch, map_id=int(getattr(snap, "map_id", 0) or 0),
                encounter=str(profile.get("name", "unknown")), model_id=mid,
                sample_agent_id=aid, sample_name=str(name or "?"),
                policy="exp36_stage13_e18_catalog_build_observation_only",
            )


def observe_current_casts(casts: tuple[tuple[int, int], ...] | list[tuple[int, int]]) -> None:
    """Log verified encounter skills once per real cast sequence."""
    snap, profile = _profile_for_session()
    if snap is None or profile is None:
        return
    map_id = int(getattr(snap, "map_id", 0) or 0)
    epoch = int(getattr(snap, "epoch", 0) or 0)
    watched = _resolved_watch_skills(map_id, profile)
    current: dict[tuple[int, int], int] = {}
    for aid_raw, sid_raw in casts:
        aid, sid = int(aid_raw or 0), int(sid_raw or 0)
        if aid <= 0 or sid <= 0:
            continue
        try:
            from Py4GWCoreLib.Builds.Skills import ReforgedSupport
            start_tick = int(ReforgedSupport.get_cast_start_tick(aid, sid) or 0)
        except Exception:
            start_tick = 0
        current[(aid, sid)] = start_tick
        if sid not in watched:
            continue
        previous = _ACTIVE_CASTS.get((aid, sid))
        if previous is not None and (start_tick <= 0 or int(previous or 0) == start_tick):
            continue
        row = watched[sid]
        _log(
            "ENCOUNTER_SHADOW_SKILL",
            session_epoch=epoch, session_key=str(getattr(snap, "key", "")),
            map_id=map_id, encounter=str(profile.get("name", "unknown")),
            caster_id=aid, skill_id=sid, skill_name=str(row.get("name", "?")),
            category=str(row.get("category", "unknown")),
            observation=str(row.get("observation", "")),
            suggested_response=str(row.get("suggested_response", "observe_only")),
            cast_start_tick=int(start_tick),
            policy="exp36_stage13_e18_shadow_only_no_gameplay_actions",
        )
    _ACTIVE_CASTS.clear()
    _ACTIVE_CASTS.update(current)



def local_signet_advisory() -> dict[str, object]:
    """Return verified local anti-signet constraints; no combat action is issued.

    Stage 16 deliberately keeps ``active_rules_enabled`` false until one of these
    mechanics is actually observed in a live run.  This function is the typed
    bridge later consumers can A/B-test without reparsing names or guessing IDs.
    """
    snap, profile = _profile_for_session()
    result: dict[str, object] = {
        "active_rules_enabled": False,
        "ignorance": False,
        "rust": False,
        "primal_echoes": False,
        "signets_blocked": False,
        "activation_multiplier": 1.0,
        "minimum_signet_energy": 0.0,
    }
    if snap is None or profile is None:
        return result
    result["active_rules_enabled"] = bool(profile.get("active_rules_enabled", False))
    watched = _resolved_watch_skills(int(getattr(snap, "map_id", 0) or 0), profile)
    by_name = {str(row.get("name", "")): int(sid) for sid, row in watched.items()}
    try:
        from Py4GWCoreLib import Routines
        from Py4GWCoreLib.Player import Player
        player_id = int(Player.GetAgentID() or 0)
        for name, key in (("Ignorance", "ignorance"), ("Rust", "rust"), ("Primal_Echoes", "primal_echoes")):
            sid = int(by_name.get(name, 0) or 0)
            if sid > 0 and bool(Routines.Checks.Agents.HasEffect(player_id, sid)):
                result[key] = True
    except Exception:
        pass
    if bool(result.get("ignorance")):
        result["signets_blocked"] = True
    if bool(result.get("rust")):
        result["activation_multiplier"] = 2.0
    if bool(result.get("primal_echoes")):
        result["minimum_signet_energy"] = 10.0

    # Observation only until the profile explicitly enables active rules after
    # live validation.  Log the typed advisory once per session/constraint.
    for key in ("ignorance", "rust", "primal_echoes"):
        if not bool(result.get(key)):
            continue
        log_key = (int(getattr(snap, "epoch", 0) or 0), str(key))
        if log_key in _LOGGED_ADVISORY:
            continue
        _LOGGED_ADVISORY.add(log_key)
        _log(
            "ENCOUNTER_ADVISORY_CONSTRAINT",
            session_epoch=int(getattr(snap, "epoch", 0) or 0),
            map_id=int(getattr(snap, "map_id", 0) or 0), constraint=str(key),
            active_rules_enabled=bool(result.get("active_rules_enabled", False)),
            signets_blocked=bool(result.get("signets_blocked", False)),
            activation_multiplier=float(result.get("activation_multiplier", 1.0) or 1.0),
            minimum_signet_energy=float(result.get("minimum_signet_energy", 0.0) or 0.0),
            policy="exp36_stage16_e18_activation_bridge_disabled_until_live_trigger_validation",
        )
    return result
