"""EXP36 Stage 15 / Astra E16: one conservative movement ownership contract.

Manual flags/leader control remain external and therefore highest priority.  This
module owns only confirmed AoE danger movement for followers.  Stage 15 enables
ST plus exactly one additional backline role (PI); the HR leader is never moved.
"""
from __future__ import annotations

_LOCAL_ROLE = "generic"
_ROLE_SESSION_EPOCH = 0
_LAST_OWNER = ""
_LAST_LOG_MS = 0


def _now_ms() -> int:
    try:
        import PySystem
        return int(PySystem.get_tick_count64() or 0)
    except Exception:
        return 0


def register_local_role(role: str) -> None:
    global _LOCAL_ROLE, _ROLE_SESSION_EPOCH
    value = str(role or "generic").strip().lower()
    try:
        from Py4GWCoreLib.Builds.Skills import CombatSession
        _ROLE_SESSION_EPOCH = int(CombatSession.refresh().epoch or 0)
    except Exception:
        _ROLE_SESSION_EPOCH = 0
    if value in {"st", "soul_twisting", "ritualist"}:
        _LOCAL_ROLE = "st"
    elif value in {"pi", "pi_support", "psychic_instability"}:
        _LOCAL_ROLE = "pi_support"


def local_role() -> str:
    try:
        from Py4GWCoreLib.Builds.Skills import CombatSession
        epoch = int(CombatSession.refresh().epoch or 0)
        if int(_ROLE_SESSION_EPOCH or 0) > 0 and epoch > 0 and epoch != int(_ROLE_SESSION_EPOCH):
            return "generic"
    except Exception:
        pass
    return str(_LOCAL_ROLE or "generic")


def _log_owner(owner: str, **fields) -> None:
    global _LAST_OWNER, _LAST_LOG_MS
    now = _now_ms()
    if str(owner) == str(_LAST_OWNER) and now - int(_LAST_LOG_MS or 0) < 1500:
        return
    _LAST_OWNER = str(owner)
    _LAST_LOG_MS = int(now)
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "TEAM_MOVEMENT_OWNER", owner=str(owner), role=local_role(),
            policy="exp36_stage15_e16_manual_flag_then_danger_then_recovery_follow",
            **fields,
        )
    except Exception:
        pass


def service_danger_movement(*, manual_flag_active: bool = False) -> bool:
    """Return True while verified AoE danger owns follower movement.

    Manual/party flags always win.  ST accepts existing medium-confidence critical
    zones; PI is intentionally stricter and moves only for high-confidence critical
    zones.  Other roles never gain automatic movement in Stage 15.
    """
    if bool(manual_flag_active):
        return False
    role = local_role()
    if role not in {"st", "pi_support"}:
        return False
    try:
        from Py4GWCoreLib.Builds.Skills.AoEDangerPrediction import (
            refresh_aoe_danger_zones, avoid_active_aoe_if_needed,
        )
        refresh_aoe_danger_zones()
        owned = bool(avoid_active_aoe_if_needed(
            role=role, allow_noncritical=False, allow_actions_at_safe_hold=False
        ))
    except Exception:
        owned = False
    if owned:
        _log_owner("confirmed_aoe_danger")
    return bool(owned)
