"""Lightweight file logging and native name-tag colors for Simple-Power HeroAI.

This module deliberately has no ImGui panel.  It is controlled by static values
in :mod:`SimplePowerSettings` and is safe to leave loaded during normal play.
Only meaningful combat events are written, one file per account, so eight
multibox clients do not fight over the same log file.

Native colors use Reforged's ``PyAgentTagColor`` hook.  If the hook/module is
unavailable, logging continues and combat behavior is unaffected.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Final

from Py4GWCoreLib.Agent import Agent
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.Skill import Skill

# ARGB (0xAARRGGBB), as required by PyAgentTagColor.
COLOR_FOCUS: Final[int] = 0xFFFFFF00       # yellow
COLOR_DANGEROUS: Final[int] = 0xFFFF3030   # red
COLOR_CLAIMED: Final[int] = 0xFFFF00FF     # magenta/purple
COLOR_SUCCESS: Final[int] = 0xFF30FF60     # green
COLOR_ESCAPE: Final[int] = 0xFFFF9A20      # orange

PRIORITY_FOCUS: Final[int] = 100
PRIORITY_DANGEROUS: Final[int] = 300
PRIORITY_CLAIMED: Final[int] = 400
PRIORITY_SUCCESS: Final[int] = 500
PRIORITY_ESCAPE: Final[int] = 600

_DEFAULT_LOG_MAX_BYTES: Final[int] = 5 * 1024 * 1024


@dataclass
class _Marker:
    kind: str
    color: int
    priority: int
    expires_tick: int


@dataclass
class _InterruptAttempt:
    attempt_id: int
    target_id: int
    enemy_skill_id: int
    our_skill_id: int
    fired_tick: int
    expected_finish_tick: int
    last_seen_same_cast_tick: int


_markers: dict[int, dict[str, _Marker]] = {}
_applied_color: dict[int, int] = {}
_original_agent_rules: dict[int, int | None] = {}
_interrupt_attempts: dict[tuple[int, int, int], _InterruptAttempt] = {}
_interrupt_attempt_serial = 0
_color_module = None
_color_checked = False
_last_color_check_tick = 0
_color_failure_logged = False
_last_rotation_check_tick = 0
_last_focus_log: tuple[int, int] = (0, 0)
_last_danger_log: dict[tuple[int, int], int] = {}
_state_event_log: dict[str, tuple[tuple[tuple[str, str], ...], int]] = {}
_keyed_state_event_log: dict[tuple[str, str], tuple[tuple[tuple[str, str], ...], int]] = {}
_guarded_exception_log: dict[str, tuple[str, int, int]] = {}
_pending_log_lines: list[str] = []
_last_log_flush_tick = 0
_last_debug_tick = 0
_LOG_FLUSH_INTERVAL_MS: Final[int] = 1500
_LOG_FLUSH_MAX_LINES: Final[int] = 256
_DEBUG_TICK_INTERVAL_MS: Final[int] = 150
_KEYED_STATE_EVENT_MAX_ENTRIES: Final[int] = 256
_KEYED_STATE_EVENT_RETENTION_MS: Final[int] = 15000
_GUARDED_EXCEPTION_MAX_SITES: Final[int] = 64
_GUARDED_EXCEPTION_INTERVAL_MS: Final[int] = 5000
_SETTINGS_CACHE_MS: Final[int] = 1000
_AGENT_NAME_CACHE_MS: Final[int] = 1000
_ACCOUNT_SLUG_CACHE_MS: Final[int] = 5000
_settings_cache: dict[str, tuple[object, int]] = {}
_skill_name_cache: dict[int, str] = {}
_agent_name_cache: dict[int, tuple[str, int]] = {}
_account_slug_cache: tuple[str, int] = ("", 0)


def _settings_enabled(name: str, default: bool) -> bool:
    # Diagnostics can call this dozens of times per frame. Feature switches are
    # human-scale settings, so a 1 s local cache removes repeated settings/import
    # work without affecting combat decisions. Live setting changes still apply
    # within one second.
    now = _now_ms()
    key = f"enabled:{name}"
    cached = _settings_cache.get(key)
    if cached is not None and now > 0 and now - int(cached[1]) < int(_SETTINGS_CACHE_MS):
        return bool(cached[0])
    try:
        from Py4GWCoreLib.Builds.Skills import SimplePowerSettings
        value = bool(SimplePowerSettings.is_feature_enabled(name, default))
    except Exception:
        value = bool(default)
    if now > 0:
        _settings_cache[key] = (value, int(now))
    return value


def _settings_value(name: str, default):
    now = _now_ms()
    key = f"value:{name}"
    cached = _settings_cache.get(key)
    if cached is not None and now > 0 and now - int(cached[1]) < int(_SETTINGS_CACHE_MS):
        return cached[0]
    try:
        from Py4GWCoreLib.Builds.Skills import SimplePowerSettings
        value = SimplePowerSettings.get_value(name, default)
    except Exception:
        value = default
    if now > 0:
        _settings_cache[key] = (value, int(now))
    return value


def _now_ms() -> int:
    try:
        import PySystem
        return int(PySystem.get_tick_count64() or 0)
    except Exception:
        return 0


def _safe_skill_name(skill_id: int) -> str:
    sid = int(skill_id or 0)
    if sid in _skill_name_cache:
        return _skill_name_cache[sid]
    try:
        value = str(Skill.GetName(sid) or "?").strip() or "?"
    except Exception:
        value = "?"
    if sid > 0 and value != "?":
        _skill_name_cache[sid] = value
    return value


def _safe_agent_name(agent_id: int) -> str:
    aid = int(agent_id or 0)
    if aid <= 0:
        return "?"
    now = _now_ms()
    cached = _agent_name_cache.get(aid)
    if cached is not None and now > 0 and now - int(cached[1]) < int(_AGENT_NAME_CACHE_MS):
        return str(cached[0])
    try:
        if not Agent.IsValid(aid):
            return "?"
        value = str(Agent.GetNameByID(aid) or "?").strip() or "?"
    except Exception:
        value = "?"
    if now > 0 and value != "?":
        _agent_name_cache[aid] = (value, int(now))
    return value


def _account_slug() -> str:
    global _account_slug_cache
    now = _now_ms()
    cached_slug, cached_tick = _account_slug_cache
    if cached_slug and now > 0 and now - int(cached_tick) < int(_ACCOUNT_SLUG_CACHE_MS):
        return str(cached_slug)
    try:
        raw = str(Player.GetAccountEmail() or Player.GetName() or "unknown")
    except Exception:
        raw = "unknown"
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._")[:80] or "unknown"
    # Do not pin the early startup 'unknown' identity; retry until the account
    # has actually been published, then refresh only every few seconds.
    if now > 0 and slug != "unknown":
        _account_slug_cache = (slug, int(now))
    return slug


def get_log_path() -> Path:
    root = Path(__file__).resolve().parents[3]
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"HeroAI_Combat_Debug_{_account_slug()}.log"


def _rotate_if_needed(now: int) -> None:
    global _last_rotation_check_tick
    if now <= 0 or now - int(_last_rotation_check_tick) < 5000:
        return
    _last_rotation_check_tick = int(now)
    path = get_log_path()
    try:
        max_bytes = max(256 * 1024, int(_settings_value("combat_debug_log_max_bytes", _DEFAULT_LOG_MAX_BYTES)))
        if path.exists() and path.stat().st_size >= max_bytes:
            old = path.with_suffix(path.suffix + ".old")
            try:
                old.unlink(missing_ok=True)
            except Exception:
                pass
            path.replace(old)
    except Exception:
        pass


def flush_pending(*, force: bool = False) -> None:
    """Write queued combat lines in one batch instead of opening the file per event."""
    global _last_log_flush_tick
    if not _pending_log_lines:
        return
    now = _now_ms()
    if (
        not force
        and len(_pending_log_lines) < int(_LOG_FLUSH_MAX_LINES)
        and int(now) - int(_last_log_flush_tick) < int(_LOG_FLUSH_INTERVAL_MS)
    ):
        return
    lines = list(_pending_log_lines)
    _pending_log_lines.clear()
    _last_log_flush_tick = int(now)
    _rotate_if_needed(now)
    try:
        with get_log_path().open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except Exception:
        # Keep diagnostics best-effort; never put file I/O in the combat path.
        pass


def log_event(event: str, **fields: object) -> None:
    global _last_log_flush_tick
    if not _settings_enabled("combat_debug_file_log", True):
        return
    now = _now_ms()
    if int(_last_log_flush_tick) <= 0:
        _last_log_flush_tick = int(now)
    pieces = [f"[{int(now):012d}]", str(event)]
    for key, value in fields.items():
        text = str(value).replace("\n", " ").replace("\r", " ")
        pieces.append(f"{key}={text}")
    _pending_log_lines.append(" | ".join(pieces))
    flush_pending(force=len(_pending_log_lines) >= int(_LOG_FLUSH_MAX_LINES))


def log_state_event(
    event: str,
    *,
    signature_fields: tuple[str, ...],
    min_interval_ms: int = 80,
    heartbeat_ms: int = 500,
    **fields,
) -> None:
    """Log high-frequency state only when it changes or on a heartbeat.

    Focus/cluster resolvers are queried by several consumers every frame.  The
    full diagnostic line is useful, but writing the same state hundreds of
    times per second is not.  This helper keeps state transitions visible,
    caps pathological oscillation, and emits a periodic heartbeat while the
    state remains unchanged.
    """
    if not _settings_enabled("combat_debug_file_log", True):
        return
    now = _now_ms()
    signature = tuple(
        (str(name), str(fields.get(str(name), "")))
        for name in signature_fields
    )
    previous = _state_event_log.get(str(event))
    if previous is not None:
        previous_signature, previous_tick = previous
        elapsed = int(now) - int(previous_tick)
        if signature == previous_signature and elapsed < max(100, int(heartbeat_ms)):
            return
        if signature != previous_signature and elapsed < max(0, int(min_interval_ms)):
            return
    _state_event_log[str(event)] = (signature, int(now))
    log_event(str(event), **fields)


def log_keyed_state_event(
    event: str,
    scope_key,
    *,
    signature_fields: tuple[str, ...],
    min_interval_ms: int = 80,
    heartbeat_ms: int = 500,
    **fields,
) -> None:
    """Like :func:`log_state_event`, but throttled independently per scope.

    This is intended for concurrent entities/casts where a single event-wide
    throttle would hide valid parallel observations.  Entries are locally
    bounded and expire; the helper is diagnostic-only and never feeds combat
    decisions.
    """
    if not _settings_enabled("combat_debug_file_log", True):
        return
    now = _now_ms()
    key = (str(event), str(scope_key))
    signature = tuple(
        (str(name), str(fields.get(str(name), "")))
        for name in signature_fields
    )
    previous = _keyed_state_event_log.get(key)
    if previous is not None:
        previous_signature, previous_tick = previous
        elapsed = int(now) - int(previous_tick)
        if signature == previous_signature and elapsed < max(100, int(heartbeat_ms)):
            return
        if signature != previous_signature and elapsed < max(0, int(min_interval_ms)):
            return

    # A cast start can create a new key, so keep this cache hard-bounded.
    if key not in _keyed_state_event_log and len(_keyed_state_event_log) >= int(_KEYED_STATE_EVENT_MAX_ENTRIES):
        cutoff = int(now) - int(_KEYED_STATE_EVENT_RETENTION_MS)
        for stale_key, (_sig, stale_tick) in list(_keyed_state_event_log.items()):
            if int(stale_tick) < cutoff:
                _keyed_state_event_log.pop(stale_key, None)
        if len(_keyed_state_event_log) >= int(_KEYED_STATE_EVENT_MAX_ENTRIES):
            oldest_key = min(_keyed_state_event_log, key=lambda item: int(_keyed_state_event_log[item][1]))
            _keyed_state_event_log.pop(oldest_key, None)

    _keyed_state_event_log[key] = (signature, int(now))
    log_event(str(event), **fields)


def log_guarded_exception(
    site: str,
    exc: BaseException,
    *,
    min_interval_ms: int = _GUARDED_EXCEPTION_INTERVAL_MS,
    **fields,
) -> None:
    """Expose swallowed exceptions without creating log spam.

    The first occurrence for a decision site is logged immediately. Repeats of
    the same exception are counted and only emitted again after the interval.
    This remains best-effort diagnostics and cannot alter the caller's fallback.
    """
    if not _settings_enabled("combat_debug_file_log", True):
        return
    now = _now_ms()
    site_key = str(site or "unknown")
    error_text = f"{type(exc).__name__}: {exc}"
    if len(error_text) > 240:
        error_text = error_text[:237] + "..."

    previous = _guarded_exception_log.get(site_key)
    if previous is None or str(previous[0]) != error_text:
        count = 1
        should_log = True
        last_log_tick = int(now)
    else:
        count = int(previous[1]) + 1
        last_log_tick = int(previous[2])
        should_log = int(now) - int(last_log_tick) >= max(250, int(min_interval_ms))
        if should_log:
            last_log_tick = int(now)

    if site_key not in _guarded_exception_log and len(_guarded_exception_log) >= int(_GUARDED_EXCEPTION_MAX_SITES):
        oldest_site = min(_guarded_exception_log, key=lambda key: int(_guarded_exception_log[key][2]))
        _guarded_exception_log.pop(oldest_site, None)
    _guarded_exception_log[site_key] = (error_text, int(count), int(last_log_tick))

    if should_log:
        log_event(
            "GUARDED_EXCEPTION",
            site=site_key,
            error=error_text,
            repeat_count=int(count),
            **fields,
        )


def _get_color_module():
    global _color_module, _color_checked, _last_color_check_tick, _color_failure_logged
    if not _settings_enabled("combat_debug_colors", True):
        return None
    now = _now_ms()
    if _color_module is not None:
        return _color_module
    # Native modules can become available only after the newest Reforged DLL
    # has been injected. Retry occasionally instead of permanently disabling
    # colors after one early import failure.
    if _color_checked and now > 0 and now - int(_last_color_check_tick) < 3000:
        return None
    _color_checked = True
    _last_color_check_tick = int(now)
    try:
        import PyAgentTagColor
        if not bool(PyAgentTagColor.is_hook_installed()):
            if not _color_failure_logged:
                log_event("DEBUG_COLOR_UNAVAILABLE", reason="hook_not_installed")
                _color_failure_logged = True
            return None
        PyAgentTagColor.enable()
        _color_module = PyAgentTagColor
        _color_failure_logged = False
        try:
            diag = dict(PyAgentTagColor.get_diagnostics() or {})
        except Exception:
            diag = {}
        log_event("DEBUG_COLOR_READY", diagnostics=diag)
    except Exception as exc:
        if not _color_failure_logged:
            log_event("DEBUG_COLOR_UNAVAILABLE", reason=repr(exc))
            _color_failure_logged = True
        _color_module = None
    return _color_module

def _valid_living_agent(agent_id: int) -> bool:
    try:
        return int(agent_id or 0) > 0 and Agent.IsValid(int(agent_id)) and Agent.IsAlive(int(agent_id))
    except Exception:
        return False


def _save_original_rule(agent_id: int, module) -> None:
    if int(agent_id) in _original_agent_rules:
        return
    try:
        rules = dict(module.get_agent_rules() or {})
        _original_agent_rules[int(agent_id)] = int(rules[int(agent_id)]) if int(agent_id) in rules else None
    except Exception:
        _original_agent_rules[int(agent_id)] = None


def _restore_agent(agent_id: int, module) -> None:
    previous = _original_agent_rules.pop(int(agent_id), None)
    try:
        if previous is None:
            module.remove_agent_color(int(agent_id))
        else:
            module.set_agent_color(int(agent_id), int(previous) & 0xFFFFFFFF)
    except Exception:
        pass
    _applied_color.pop(int(agent_id), None)


def _apply_best_marker(agent_id: int, now: int) -> None:
    module = _get_color_module()
    if module is None:
        return
    states = _markers.get(int(agent_id), {})
    for kind, marker in list(states.items()):
        if int(marker.expires_tick) <= int(now):
            states.pop(kind, None)
    if not states or not _valid_living_agent(int(agent_id)):
        _markers.pop(int(agent_id), None)
        if int(agent_id) in _applied_color:
            _restore_agent(int(agent_id), module)
        return
    best = max(states.values(), key=lambda marker: (int(marker.priority), int(marker.expires_tick)))
    if int(_applied_color.get(int(agent_id), -1)) == int(best.color):
        return
    try:
        _save_original_rule(int(agent_id), module)
        module.set_agent_color(int(agent_id), int(best.color) & 0xFFFFFFFF)
        _applied_color[int(agent_id)] = int(best.color)
    except Exception:
        pass


def mark_agent(agent_id: int, kind: str, color: int, priority: int, duration_ms: int) -> None:
    if not _settings_enabled("combat_debug_colors", True):
        return
    now = _now_ms()
    if now <= 0 or not _valid_living_agent(int(agent_id)):
        return
    states = _markers.setdefault(int(agent_id), {})
    states[str(kind)] = _Marker(
        kind=str(kind),
        color=int(color) & 0xFFFFFFFF,
        priority=int(priority),
        expires_tick=int(now) + max(100, int(duration_ms)),
    )
    _apply_best_marker(int(agent_id), now)


def mark_focus(agent_id: int, *, reason: str = "cluster") -> None:
    global _last_focus_log
    now = _now_ms()
    mark_agent(agent_id, "focus", COLOR_FOCUS, PRIORITY_FOCUS, 700)
    # Stage 1.70 compact diagnostics: TEAM_FOCUS_DECISION / PLAN_SWITCH already
    # preserve the actual combat decision. FOCUS_TARGET is therefore a visual
    # breadcrumb only. Sample it every few seconds instead of serialising every
    # transient oscillation from multiple consumers. Exact plan transitions are
    # still preserved by TEAM_COMBAT_PLAN_SWITCH. This cannot affect target
    # selection because the cache below is diagnostics-only.
    aid = int(agent_id or 0)
    last_aid, last_tick = _last_focus_log
    elapsed = int(now) - int(last_tick)
    should_log = bool(aid > 0 and elapsed >= 5000)
    if should_log:
        _last_focus_log = (aid, int(now))
        log_event("FOCUS_TARGET", target_id=aid, target=_safe_agent_name(aid), reason=reason)


def mark_dangerous_cast(agent_id: int, skill_id: int, *, score: int | None = None) -> None:
    now = _now_ms()
    mark_agent(agent_id, "dangerous_cast", COLOR_DANGEROUS, PRIORITY_DANGEROUS, 500)
    key = (int(agent_id), int(skill_id))
    if now - int(_last_danger_log.get(key, 0)) >= 1500:
        _last_danger_log[key] = int(now)
        log_event(
            "DANGEROUS_CAST",
            caster_id=int(agent_id),
            caster=_safe_agent_name(agent_id),
            skill_id=int(skill_id),
            skill=_safe_skill_name(skill_id),
            score="?" if score is None else int(score),
        )


def mark_interrupt_claim(agent_id: int, enemy_skill_id: int, our_skill_id: int) -> None:
    mark_agent(agent_id, "interrupt_claim", COLOR_CLAIMED, PRIORITY_CLAIMED, 1400)
    log_event(
        "INTERRUPT_CLAIMED",
        caster_id=int(agent_id),
        caster=_safe_agent_name(agent_id),
        enemy_skill_id=int(enemy_skill_id),
        enemy_skill=_safe_skill_name(enemy_skill_id),
        our_skill_id=int(our_skill_id),
        our_skill=_safe_skill_name(our_skill_id),
    )


def register_interrupt_fired(target_id: int, enemy_skill_id: int, our_skill_id: int) -> None:
    global _interrupt_attempt_serial
    now = _now_ms()
    if now <= 0:
        return
    try:
        from Py4GWCoreLib.Builds.Skills import CombatSense
        activation_ms = int(CombatSense.get_cast_activation_ms(int(enemy_skill_id), fallback_ms=1000))
        seen_ms = int(CombatSense.get_cast_seen_ms(int(target_id), int(enemy_skill_id)) or 0)
    except Exception:
        activation_ms, seen_ms = 1000, 0
    remaining = max(150, int(activation_ms) - max(0, int(seen_ms)))

    # Stage 2.56: the next interrupt event is an opportunity to observe older
    # pending attempts *before* recording a new one. This does not assert that
    # the interrupt succeeded, and it avoids a separate high-frequency thread.
    # The normal interval guard in tick() still bounds native/API work.
    tick()

    # EXP36 Stage 4 / T02.1: more than one of our skills can legitimately fire
    # while the same enemy is still on the same long activation.  Stage 3 keyed
    # pending diagnostics only by (target, enemy skill), so the later attempt
    # overwrote the earlier one and produced N INTERRUPT_FIRED but N-1 results.
    # Keep every fired attempt independent.  The serial also disambiguates two
    # callbacks landing in the same game-clock millisecond.  This table remains
    # diagnostics-only and is not consulted by combat selection/dispatch.
    _interrupt_attempt_serial = int(_interrupt_attempt_serial) + 1
    attempt_id = int(_interrupt_attempt_serial)
    key = (int(target_id), int(enemy_skill_id), int(attempt_id))
    _interrupt_attempts[key] = _InterruptAttempt(
        attempt_id=int(attempt_id),
        target_id=int(target_id),
        enemy_skill_id=int(enemy_skill_id),
        our_skill_id=int(our_skill_id),
        fired_tick=int(now),
        expected_finish_tick=int(now) + int(remaining),
        last_seen_same_cast_tick=int(now),
    )
    mark_agent(target_id, "interrupt_claim", COLOR_CLAIMED, PRIORITY_CLAIMED, min(1800, remaining + 500))
    log_event(
        "INTERRUPT_FIRED",
        attempt_id=int(attempt_id),
        caster_id=int(target_id),
        caster=_safe_agent_name(target_id),
        enemy_skill_id=int(enemy_skill_id),
        enemy_skill=_safe_skill_name(enemy_skill_id),
        our_skill_id=int(our_skill_id),
        our_skill=_safe_skill_name(our_skill_id),
        estimated_remaining_ms=int(remaining),
    )


def log_aoe_pending(skill_id: int, caster_id: int, center: tuple[float, float], confidence: str, commit_ms: int) -> None:
    log_event(
        "AOE_PENDING", caster_id=int(caster_id), caster=_safe_agent_name(caster_id),
        skill_id=int(skill_id), skill=_safe_skill_name(skill_id),
        center=f"{float(center[0]):.1f},{float(center[1]):.1f}",
        confidence=str(confidence), commit_ms=int(commit_ms),
    )


def log_aoe_cancelled(skill_id: int, caster_id: int, elapsed_ms: int, reason: str) -> None:
    log_event(
        "AOE_CAST_CANCELLED", caster_id=int(caster_id), caster=_safe_agent_name(caster_id),
        skill_id=int(skill_id), skill=_safe_skill_name(skill_id),
        elapsed_ms=int(elapsed_ms), reason=str(reason),
    )


def log_aoe_escape_skipped(skill_id: int, role: str, confidence: str, reason: str) -> None:
    log_event(
        "AOE_ESCAPE_SKIPPED", account=_account_slug(), role=str(role),
        skill_id=int(skill_id), skill=_safe_skill_name(skill_id),
        confidence=str(confidence), reason=str(reason),
    )


def log_aoe_escape_failed(skill_id: int, role: str, confidence: str, reason: str) -> None:
    log_event(
        "AOE_ESCAPE_FAILED", account=_account_slug(), role=str(role),
        skill_id=int(skill_id), skill=_safe_skill_name(skill_id),
        confidence=str(confidence), reason=str(reason),
    )


def log_aoe_zone(
    skill_id: int,
    caster_id: int,
    center: tuple[float, float],
    radius: float,
    confidence: str,
    critical: bool,
    *,
    provisional: bool = False,
    cast_started_tick: int = 0,
) -> None:
    log_event(
        "AOE_ZONE_ARMED",
        caster_id=int(caster_id),
        caster=_safe_agent_name(caster_id),
        skill_id=int(skill_id),
        skill=_safe_skill_name(skill_id),
        center=f"{float(center[0]):.1f},{float(center[1]):.1f}",
        radius=f"{float(radius):.1f}",
        confidence=str(confidence),
        critical=bool(critical),
        provisional=bool(provisional),
        cast_started_tick=int(cast_started_tick or 0),
    )


def mark_aoe_escape(skill_id: int, destination: tuple[float, float], role: str, confidence: str) -> None:
    try:
        player_id = int(Player.GetAgentID() or 0)
    except Exception:
        player_id = 0
    if player_id > 0:
        mark_agent(player_id, "aoe_escape", COLOR_ESCAPE, PRIORITY_ESCAPE, 1500)
    log_event(
        "AOE_ESCAPE",
        account=_account_slug(),
        role=str(role),
        skill_id=int(skill_id),
        skill=_safe_skill_name(skill_id),
        confidence=str(confidence),
        destination=f"{float(destination[0]):.1f},{float(destination[1]):.1f}",
    )


def log_aoe_return(role: str, destination: tuple[float, float]) -> None:
    log_event(
        "AOE_RETURN",
        account=_account_slug(),
        role=str(role),
        destination=f"{float(destination[0]):.1f},{float(destination[1]):.1f}",
    )


def tick() -> None:
    """Expire colors and infer interrupt outcomes from observed cast state.

    A true native interrupt event is not guaranteed by Reforged yet.  Results
    are therefore labelled ``likely_interrupted`` or ``completed_or_late`` and
    never presented as perfect ground truth.
    """
    global _last_debug_tick
    now = _now_ms()
    if now <= 0:
        return
    if int(now) - int(_last_debug_tick) < int(_DEBUG_TICK_INTERVAL_MS):
        return
    _last_debug_tick = int(now)

    for agent_id in list(_markers):
        _apply_best_marker(int(agent_id), now)

    for key, attempt in list(_interrupt_attempts.items()):
        target_id = int(attempt.target_id)
        enemy_skill_id = int(attempt.enemy_skill_id)
        attempt_id = int(attempt.attempt_id)
        native_outcome = None
        try:
            from Py4GWCoreLib.Builds.Skills.ReforgedSupport import get_cast_outcome
            native_outcome = get_cast_outcome(target_id, enemy_skill_id, attempt.fired_tick)
        except Exception:
            native_outcome = None
        if native_outcome is not None:
            result = f"native_{native_outcome}"
            log_event(
                "INTERRUPT_RESULT", result=result, attempt_id=int(attempt_id), caster_id=target_id,
                caster=_safe_agent_name(target_id), enemy_skill_id=enemy_skill_id,
                enemy_skill=_safe_skill_name(enemy_skill_id),
                our_skill=_safe_skill_name(attempt.our_skill_id),
                elapsed_ms=int(now) - int(attempt.fired_tick),
                expected_finish_tick=int(attempt.expected_finish_tick),
                resolution_lag_ms=max(0, int(now) - int(attempt.expected_finish_tick)),
            )
            if native_outcome == "interrupted":
                mark_agent(target_id, "interrupt_success", COLOR_SUCCESS, PRIORITY_SUCCESS, 900)
            _interrupt_attempts.pop(key, None)
            continue
        # Poll-based inference becomes misleading when this diagnostic path was
        # not serviced near the cast window.  Do not call a cast completed,
        # interrupted or target-gone tens of seconds later; mark it unknown.
        resolution_lag = max(0, int(now) - int(attempt.expected_finish_tick))
        if resolution_lag > 2200:
            log_event(
                "INTERRUPT_RESULT", result="unknown_observed_too_late",
                attempt_id=int(attempt_id), caster_id=target_id, caster=_safe_agent_name(target_id),
                enemy_skill_id=enemy_skill_id, enemy_skill=_safe_skill_name(enemy_skill_id),
                our_skill=_safe_skill_name(attempt.our_skill_id),
                elapsed_ms=int(now) - int(attempt.fired_tick),
                expected_finish_tick=int(attempt.expected_finish_tick),
                resolution_lag_ms=int(resolution_lag),
                observation_quality="late_poll_cannot_infer_interrupt_success",
                policy="stage2_55_observability_gap_not_verified_failure",
            )
            _interrupt_attempts.pop(key, None)
            continue
        if not _valid_living_agent(target_id):
            log_event(
                "INTERRUPT_RESULT",
                result="target_gone",
                attempt_id=int(attempt_id),
                caster_id=target_id,
                enemy_skill_id=enemy_skill_id,
                enemy_skill=_safe_skill_name(enemy_skill_id),
                our_skill=_safe_skill_name(attempt.our_skill_id),
                elapsed_ms=int(now) - int(attempt.fired_tick),
                expected_finish_tick=int(attempt.expected_finish_tick),
                resolution_lag_ms=max(0, int(now) - int(attempt.expected_finish_tick)),
            )
            _interrupt_attempts.pop(key, None)
            continue
        try:
            is_same = bool(Agent.IsCasting(target_id)) and int(Agent.GetCastingSkillID(target_id) or 0) == int(enemy_skill_id)
        except Exception:
            is_same = False
        if is_same:
            attempt.last_seen_same_cast_tick = int(now)
            if now <= int(attempt.expected_finish_tick) + 650:
                continue
            log_event(
                "INTERRUPT_RESULT",
                result="missed_or_too_late",
                attempt_id=int(attempt_id),
                caster_id=target_id,
                caster=_safe_agent_name(target_id),
                enemy_skill_id=enemy_skill_id,
                enemy_skill=_safe_skill_name(enemy_skill_id),
                our_skill=_safe_skill_name(attempt.our_skill_id),
                elapsed_ms=int(now) - int(attempt.fired_tick),
                expected_finish_tick=int(attempt.expected_finish_tick),
                resolution_lag_ms=max(0, int(now) - int(attempt.expected_finish_tick)),
            )
            _interrupt_attempts.pop(key, None)
            continue

        stopped_early = int(now) < int(attempt.expected_finish_tick) - 80
        result = "likely_interrupted" if stopped_early else "completed_or_late"
        log_event(
            "INTERRUPT_RESULT",
            result=result,
            attempt_id=int(attempt_id),
            caster_id=target_id,
            caster=_safe_agent_name(target_id),
            enemy_skill_id=enemy_skill_id,
            enemy_skill=_safe_skill_name(enemy_skill_id),
            our_skill=_safe_skill_name(attempt.our_skill_id),
            elapsed_ms=int(now) - int(attempt.fired_tick),
            expected_finish_tick=int(attempt.expected_finish_tick),
            resolution_lag_ms=max(0, int(now) - int(attempt.expected_finish_tick)),
        )
        if stopped_early:
            mark_agent(target_id, "interrupt_success", COLOR_SUCCESS, PRIORITY_SUCCESS, 900)
        _interrupt_attempts.pop(key, None)


def clear_debug_colors() -> None:
    module = _get_color_module()
    if module is not None:
        for agent_id in list(_applied_color):
            _restore_agent(int(agent_id), module)
    _markers.clear()


# --- Compatibility: explicit interrupt outcome verification ------------------

def verify_interrupt_outcomes() -> None:
    """Run the single interrupt diagnostic pipeline immediately.

    ``register_interrupt_fired`` above records one _InterruptAttempt using the
    module's monotonic game clock.  Older EXP callers still invoke this helper,
    so keep the public name as a thin wrapper instead of maintaining a second,
    conflicting implementation with different timing/API semantics.
    """
    tick()
