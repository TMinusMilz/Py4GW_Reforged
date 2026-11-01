"""Optional Keystone support-guard TEST extension (not part of frozen baseline).

No new engagement: only confirmed current attacks against ST/Panic can request
short, obstacle-agnostic retreat waypoints. Every known enemy is checked along
an entire straight-line corridor. If coverage is unavailable, HOLD position.
This is NOT navmesh/pathfinding validation: the game engine may detour. Keep
steps short and do not advertise an absolute no-pull guarantee.
"""
from __future__ import annotations

import math
from Py4GWCoreLib import AgentArray, GLOBAL_CACHE, Player, Range, Skill, Utils
from Py4GWCoreLib.Agent import Agent

_ROLE_IDS = {
    "st": int(Skill.GetID("Soul_Twisting") or 0),
    "panic": int(Skill.GetID("Panic") or 0),
    "keystone": int(Skill.GetID("Keystone_Signet") or 0),
}
_LAST_RETREAT_AT = 0
_RECENT_PANIC_FOCUS = (0, 0)
_LAST_LOG_AT = {}
_LAST_UNSAFE_PATH_UNTIL_MS = 0


def _now() -> int:
    try:
        import PySystem
        return int(PySystem.get_tick_count64() or 0)
    except Exception:
        return 0


def _log(event, **fields):
    now = _now()
    if now - int(_LAST_LOG_AT.get(event, -100000) or -100000) < 1000:
        return
    _LAST_LOG_AT[event] = now
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(event, policy="keystone_support_guard_test_fail_closed", **fields)
    except Exception:
        pass


def local_role() -> str:
    # Always read equipped skillbar, not a session-global role that may survive
    # a build change or map transition.
    try:
        skills = {int(GLOBAL_CACHE.SkillBar.GetSkillIDBySlot(slot) or 0) for slot in range(1, 9)}
    except Exception:
        return "unknown"
    for role in ("st", "panic", "keystone"):
        if _ROLE_IDS[role] > 0 and _ROLE_IDS[role] in skills:
            return role
    return "other"


def suppress_idle_weapon_attacks() -> bool:
    return local_role() in {"st", "panic", "keystone"}


def _party_accounts():
    # Reuse existing ST shared-party filter and its map/session restrictions.
    try:
        from Py4GWCoreLib.Builds.Skills.STDefensePriority import _iter_party_accounts
        return tuple(_iter_party_accounts())
    except Exception:
        return ()


def panic_agent_id() -> int:
    panic = _ROLE_IDS["panic"]
    if panic <= 0:
        return 0
    try:
        for account in _party_accounts():
            skillbar = getattr(getattr(account, "AgentData", None), "Skillbar", None)
            ids = {int(getattr(s, "Id", 0) or 0) for s in (getattr(skillbar, "Skills", ()) or ())}
            if panic not in ids:
                continue
            aid = int(account.AgentData.AgentID or 0)
            if aid > 0 and Agent.IsValid(aid) and Agent.IsAlive(aid):
                return aid
    except Exception:
        pass
    return 0


def panic_attacker_priority(enemies) -> int:
    global _RECENT_PANIC_FOCUS
    pid = panic_agent_id()
    if pid <= 0:
        _RECENT_PANIC_FOCUS = (0, 0)
        return 0
    target = 0
    best = None
    try:
        center = Agent.GetXY(pid)
        if not center:
            return 0
        for e in enemies or ():
            eid = int(getattr(e, "agent_id", 0) or 0)
            if eid <= 0 or not Agent.IsValid(eid) or not Agent.IsAlive(eid):
                continue
            if int(Agent.GetTarget(eid) or 0) != pid:
                continue
            if not (Agent.IsAttacking(eid) or Agent.IsCasting(eid)):
                continue
            dist = float(Utils.Distance(center, Agent.GetXY(eid)))
            if dist > float(Range.Earshot.value):
                continue
            # prefer attacker actually adjacent/nearest, then high threat
            key = (dist, -int(getattr(e, "threat_score", 0)), eid)
            if best is None or key < best:
                best, target = key, eid
    except Exception:
        return 0
    now = _now()
    if target > 0:
        _RECENT_PANIC_FOCUS = (target, now + 850)
        _log("SUPPORT_PANIC_UNDER_DIRECT_ATTACK", target_id=target, panic_id=pid)
    return target


def is_recent_panic_priority(target_id: int) -> bool:
    return (int(target_id) > 0 and int(target_id) == _RECENT_PANIC_FOCUS[0]
            and _now() <= _RECENT_PANIC_FOCUS[1])


def _enemies_fresh():
    """Return complete local enemy snapshot or None if unreliable."""
    try:
        ids = AgentArray.GetEnemyArray()
        if ids is None:
            return None
        out = []
        for raw in ids:
            aid = int(raw or 0)
            if aid <= 0 or not Agent.IsValid(aid) or Agent.IsDead(aid):
                continue
            xy = Agent.GetXY(aid)
            if xy is None or len(xy) < 2:
                return None
            point = (float(xy[0]), float(xy[1]))
            if not all(math.isfinite(x) for x in point):
                return None
            out.append((aid, point, int(Agent.GetTarget(aid) or 0), bool(Agent.IsAttacking(aid))))
        return out
    except Exception:
        return None


def _segment_distance(a, b, p):
    dx, dy = b[0] - a[0], b[1] - a[1]
    denom = dx * dx + dy * dy
    t = min(1.0, max(0.0, ((p[0]-a[0])*dx+(p[1]-a[1])*dy)/denom)) if denom else 0.0
    return math.hypot(p[0]-(a[0]+dx*t), p[1]-(a[1]+dy*t))


def _route_screen(origin, destination, *, enemy_snapshot=None, role=None) -> bool:
    """Conservatively screen *entire* segment, including endpoint.

    Existing foes explicitly attacking support are considered engaged, but we
    still never move CLOSER to them. Everything else must remain outside a
    padded aggro radius throughout the corridor. Unknown data => no movement.
    """
    role = role or local_role()
    if role not in ("st", "panic"):
        return True  # never change unrelated character movement
    enemies = _enemies_fresh() if enemy_snapshot is None else enemy_snapshot
    if enemies is None or origin is None or destination is None:
        return False
    try:
        a = (float(origin[0]), float(origin[1]))
        b = (float(destination[0]), float(destination[1]))
        if not all(math.isfinite(x) for x in a+b):
            return False
        distance = math.dist(a, b)
        if not (35.0 <= distance <= 300.0):
            return False  # engine path could make unobserved detours
        me = int(Player.GetAgentID() or 0)
        # Large enough to avoid accidentally aggroing new nearby packs.
        forbidden = float(getattr(Range, "Aggro", Range.Spellcast).value) + 130.0
        for aid, xy, target, is_attacking in enemies:
            current = math.dist(a, xy)
            final = math.dist(b, xy)
            if target == me and is_attacking:
                # An endpoint beyond the attacker can be farther away while
                # the STRAIGHT PATH runs directly through them. Require the
                # entire movement vector to point away from this attacker.
                vx, vy = b[0] - a[0], b[1] - a[1]
                away_x, away_y = a[0] - xy[0], a[1] - xy[1]
                if vx * away_x + vy * away_y < -0.01:
                    return False
                if final + 10.0 < current:
                    return False
                continue
            if _segment_distance(a, b, xy) < forbidden:
                return False
        from Py4GWCoreLib.Builds.Skills.AoEDangerPrediction import is_position_in_active_aoe
        # Check all along the corridor rather than just the destination.
        samples = max(3, int(distance / 45) + 1)
        for i in range(samples + 1):
            t = i / samples
            pt = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            if is_position_in_active_aoe(pt, padding=90.0, critical_only=False):
                return False
        return True
    except Exception:
        return False


def support_route_allowed(origin, destination, *, enemy_snapshot=None, role=None) -> bool:
    """Fail closed, and keep the follower from overwriting a rejected escape."""
    global _LAST_UNSAFE_PATH_UNTIL_MS
    role = role or local_role()
    result = _route_screen(origin, destination, enemy_snapshot=enemy_snapshot, role=role)
    if not result and role in {"st", "panic"}:
        _LAST_UNSAFE_PATH_UNTIL_MS = _now() + 750
        _log("SUPPORT_UNSAFE_MOVEMENT_BLOCKED", role=role)
    return bool(result)


def recent_unsafe_path_hold() -> bool:
    return local_role() in {"st", "panic"} and _now() < _LAST_UNSAFE_PATH_UNTIL_MS


def service_guarded_retreat() -> str:
    """none / move / hold. Only act on a VERIFIED incoming ST/Panic attack."""
    global _LAST_RETREAT_AT
    role = local_role()
    if role not in {"st", "panic"}:
        return "none"
    try:
        me = int(Player.GetAgentID() or 0)
        origin = Player.GetXY()
        if me <= 0 or origin is None or not Agent.IsAlive(me):
            return "none"
        snapshot = _enemies_fresh()
        if snapshot is None:
            return "hold"  # incomplete scan: do not command flee
        attackers = [(aid, xy) for aid, xy, target, active in snapshot
                     if target == me and active and math.dist(origin, xy) <= float(Range.Earshot.value)]
        if not attackers:
            return "none"
        if _now() - _LAST_RETREAT_AT < 1200:
            return "hold"  # don't send competing movement commands
        # Retreat away from the average of immediate attacking foes only.
        ax = sum(xy[0] for _, xy in attackers)/len(attackers)
        ay = sum(xy[1] for _, xy in attackers)/len(attackers)
        ux, uy = float(origin[0])-ax, float(origin[1])-ay
        dist = math.hypot(ux, uy)
        if dist < 25:
            return "hold"
        ux, uy = ux / dist, uy / dist
        for step in (200.0, 120.0):
            target = (float(origin[0])+ux*step, float(origin[1])+uy*step)
            if not support_route_allowed(origin, target, enemy_snapshot=snapshot, role=role):
                continue
            Player.Move(*target)
            _LAST_RETREAT_AT = _now()
            _log("SUPPORT_SAFE_RETREAT_MOVE", role=role, attackers=len(attackers),
                 destination=f"{target[0]:.0f},{target[1]:.0f}", step=step)
            return "move"
        _log("SUPPORT_RETREAT_BLOCKED_NEW_CLUSTER_RISK", role=role,
             attackers=len(attackers))
        return "hold"  # keep healing/casting, don't let follower override
    except Exception:
        return "hold"
