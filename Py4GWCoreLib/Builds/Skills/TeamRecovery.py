"""EXP36 Stage 16 / Astra E20: conservative team recovery state machine.

Fight -> Stabilize -> Retreat -> Regroup -> Recover -> Ready.

Only follower accounts can receive automatic retreat movement.  The HR/party
leader is never moved.  Retreat uses a previously observed safe formation point
that was recorded while the party was healthy, out of local aggro and outside
active AoE.  If no such point exists the policy stabilizes in place instead of
guessing a dangerous destination.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from Py4GWCoreLib import AgentArray, GLOBAL_CACHE, Range, Skill, Utils
from Py4GWCoreLib.Agent import Agent
from Py4GWCoreLib.Party import Party
from Py4GWCoreLib.Player import Player


@dataclass(slots=True)
class RecoverySnapshot:
    state: str = "FIGHT"
    alive: int = 0
    dead: int = 0
    party_size: int = 0
    observed_members: int = 0
    expected_members: int = 0
    snapshot_valid: bool = False
    low35: int = 0
    low55: int = 0
    lowest_hp: float = 1.0
    catastrophic: bool = False
    severe: bool = False


_STATE = "FIGHT"
_STATE_SINCE_MS = 0
_LAST_LOG_SIGNATURE = None
_LAST_LOG_MS = 0
_LAST_SAFE_ANCHOR: tuple[float, float] | None = None
_LAST_SAFE_ANCHOR_MS = 0
_LAST_SESSION_EPOCH = 0
_HEALTHY_SINCE_MS = 0
_LAST_MOVE_MS = 0
_CATASTROPHIC_CONFIRM_COUNT = 0
_LAST_CATASTROPHIC_SAMPLE_MS = 0
_RECOVERY_CORE_REQUEST_UNTIL_MS = 0
_RECOVERY_CORE_REQUEST_EPOCH = 0
_RECOVERY_CORE_REQUEST_ANCHOR: tuple[float, float] | None = None
_RECOVERY_CORE_REQUEST_REASON = ""
_RECOVERY_DEAD_SINCE_MS = 0
_RECOVERY_LAST_DEAD_COUNT = 0
_RECOVERY_LAST_DEAD_PROGRESS_MS = 0
_RECOVERY_CORE_WAIT_SINCE_MS = 0
_LAST_CORE_GATE_LOG_MS = 0

RECOVERY_MOVE_REISSUE_MS = 850
RECOVERY_ARRIVAL_DISTANCE = 170.0
RECOVERY_SAFE_ANCHOR_MAX_AGE_MS = 120000
RECOVERY_READY_HYSTERESIS_MS = 3500
RECOVERY_SAFE_ENEMY_RADIUS = Range.Earshot.value
RECOVERY_SNAPSHOT_CONFIRM_MIN_MS = 150
RECOVERY_SNAPSHOT_CONFIRM_MAX_MS = 1600
# Stage 2.19 / Astra P1-A/C: bounded, session-local support coordination.
RECOVERY_CORE_REQUEST_TTL_MS = 2600
# Stage 2.24: core coverage may improve recovery, but must never deadlock it.
# Healthy recovery clients wait a short bounded window while the ST receives an
# explicit precast request; after that, health hysteresis is authoritative.
RECOVERY_CORE_REJOIN_MAX_WAIT_MS = 4500
RECOVERY_CORE_GATE_LOG_INTERVAL_MS = 2500
RECOVERY_DEAD_NO_REZ_GRACE_MS = 5500
RECOVERY_DEAD_REZ_PATIENCE_MS = 14000

_REZ_SKILL_IDS = frozenset(
    int(Skill.GetID(name) or 0)
    for name in (
        "Restore_Life", "Resurrection_Chant", "Resurrection_Signet", "Rebirth",
        "Flesh_of_My_Flesh", "Light_of_Dwayna", "Death_Pact_Signet",
        "Signet_of_Return",
    )
    if int(Skill.GetID(name) or 0) > 0
)


def _now_ms() -> int:
    try:
        import PySystem
        return int(PySystem.get_tick_count64() or 0)
    except Exception:
        return 0


def _session_epoch() -> int:
    global _LAST_SESSION_EPOCH, _STATE, _LAST_SAFE_ANCHOR, _HEALTHY_SINCE_MS
    global _CATASTROPHIC_CONFIRM_COUNT, _LAST_CATASTROPHIC_SAMPLE_MS
    global _RECOVERY_CORE_REQUEST_UNTIL_MS, _RECOVERY_CORE_REQUEST_EPOCH, _RECOVERY_CORE_REQUEST_ANCHOR, _RECOVERY_CORE_REQUEST_REASON
    global _RECOVERY_DEAD_SINCE_MS, _RECOVERY_LAST_DEAD_COUNT, _RECOVERY_LAST_DEAD_PROGRESS_MS
    global _RECOVERY_CORE_WAIT_SINCE_MS, _LAST_CORE_GATE_LOG_MS
    try:
        from Py4GWCoreLib.Builds.Skills import CombatSession
        epoch = int(CombatSession.refresh().epoch or 0)
    except Exception:
        epoch = 0
    if epoch > 0 and epoch != int(_LAST_SESSION_EPOCH or 0):
        _LAST_SESSION_EPOCH = epoch
        _STATE = "FIGHT"
        _LAST_SAFE_ANCHOR = None
        _HEALTHY_SINCE_MS = 0
        _CATASTROPHIC_CONFIRM_COUNT = 0
        _LAST_CATASTROPHIC_SAMPLE_MS = 0
        _RECOVERY_CORE_REQUEST_UNTIL_MS = 0
        _RECOVERY_CORE_REQUEST_EPOCH = int(epoch)
        _RECOVERY_CORE_REQUEST_ANCHOR = None
        _RECOVERY_CORE_REQUEST_REASON = ""
        _RECOVERY_DEAD_SINCE_MS = 0
        _RECOVERY_LAST_DEAD_COUNT = 0
        _RECOVERY_LAST_DEAD_PROGRESS_MS = 0
        _RECOVERY_CORE_WAIT_SINCE_MS = 0
        _LAST_CORE_GATE_LOG_MS = 0
    return int(epoch)


def _party_agent_ids() -> list[int]:
    out: list[int] = []
    try:
        for player in Party.GetPlayers() or []:
            aid = int(Party.Players.GetAgentIDByLoginNumber(int(getattr(player, "login_number", 0) or 0)) or 0)
            if aid > 0:
                out.append(aid)
    except Exception:
        pass
    try:
        for hero in Party.GetHeroes() or []:
            aid = int(getattr(hero, "agent_id", 0) or 0)
            if aid > 0:
                out.append(aid)
    except Exception:
        pass
    try:
        for hench in Party.GetHenchmen() or []:
            aid = int(getattr(hench, "agent_id", 0) or 0)
            if aid > 0:
                out.append(aid)
    except Exception:
        pass
    return sorted(set(out))


def _metrics() -> RecoverySnapshot:
    ids = _party_agent_ids()
    alive = dead = low35 = low55 = 0
    lowest = 1.0
    for aid in ids:
        try:
            if not Agent.IsValid(int(aid)):
                continue
            if Agent.IsDead(int(aid)) or not Agent.IsAlive(int(aid)):
                dead += 1
                # Dead members are counted separately; do not masquerade as a
                # 0%-HP living ally in lowest_hp.
                continue
            alive += 1
            hp = max(0.0, min(1.0, float(Agent.GetHealth(int(aid)) or 0.0)))
            lowest = min(lowest, hp)
            if hp <= 0.35:
                low35 += 1
            if hp <= 0.55:
                low55 += 1
        except Exception:
            continue
    try:
        expected = int(Party.GetPartySize() or 0)
    except Exception:
        expected = 0
    observed = int(alive + dead)
    id_count = int(len(ids))
    # Stage 17 / E20b: party-agent handles may transiently exist while none of
    # them resolves to a valid Agent.  Such frames previously looked like
    # 12-party / 0-alive / 0-dead and falsely triggered catastrophic recovery.
    # Recovery decisions require a complete resolved snapshot when the native
    # party size is available.  If it is unavailable, require every discovered
    # party id to resolve and at least one member to be observable.
    if expected > 0:
        snapshot_valid = bool(observed == expected and id_count >= expected)
        size = int(expected)
    else:
        snapshot_valid = bool(id_count > 0 and observed == id_count)
        size = int(id_count)
    if alive <= 0:
        lowest = 0.0
    catastrophic_threshold = max(2, int(math.ceil(float(max(1, size)) * 0.34)))
    catastrophic = bool(
        snapshot_valid and size >= 4 and (
            dead >= catastrophic_threshold
            or alive <= max(2, int(size // 2))
        )
    )
    severe = bool(snapshot_valid and (low35 >= 2 or (dead >= 1 and low55 >= 2)))
    return RecoverySnapshot(
        state=str(_STATE), alive=int(alive), dead=int(dead), party_size=int(size),
        observed_members=int(observed), expected_members=int(expected),
        snapshot_valid=bool(snapshot_valid),
        low35=int(low35), low55=int(low55), lowest_hp=float(lowest),
        catastrophic=bool(catastrophic), severe=bool(severe),
    )


def _enemy_near(position: tuple[float, float], radius: float = RECOVERY_SAFE_ENEMY_RADIUS) -> bool:
    try:
        enemies = AgentArray.GetEnemyArray() or []
        enemies = AgentArray.Filter.ByDistance(enemies, position, float(radius))
        return any(Agent.IsValid(int(e)) and Agent.IsAlive(int(e)) for e in enemies)
    except Exception:
        return True


def _aoe_unsafe(position: tuple[float, float]) -> bool:
    try:
        from Py4GWCoreLib.Builds.Skills.AoEDangerPrediction import is_position_in_active_aoe
        return bool(is_position_in_active_aoe(position, padding=95.0, critical_only=False))
    except Exception:
        return False


def _anchor_is_safe(anchor: tuple[float, float] | None) -> bool:
    if anchor is None:
        return False
    if _enemy_near(anchor):
        return False
    if _aoe_unsafe(anchor):
        return False
    return True


def _core_rejoin_status(anchor_xy: tuple[float, float] | None = None) -> tuple[bool, bool, int, int, tuple[int, ...]]:
    """Read fresh ST core *coverage* lazily; telemetry failure must not deadlock recovery."""
    try:
        from Py4GWCoreLib.Builds.Skills import STDefensePriority
        return STDefensePriority.recovery_core_protection_status(anchor_xy=anchor_xy)
    except TypeError:
        # Compatibility with an older module during a rolling multi-client update.
        try:
            return STDefensePriority.recovery_core_protection_status()
        except Exception:
            return False, True, 0, 0, ()
    except Exception:
        return False, True, 0, 0, ()


def _arm_core_precast_request(anchor_xy: tuple[float, float] | None, reason: str) -> None:
    """Publish a short, session-bound request for the ST to prepare core spirits locally."""
    global _RECOVERY_CORE_REQUEST_UNTIL_MS, _RECOVERY_CORE_REQUEST_EPOCH
    global _RECOVERY_CORE_REQUEST_ANCHOR, _RECOVERY_CORE_REQUEST_REASON
    now = _now_ms()
    epoch = _session_epoch()
    _RECOVERY_CORE_REQUEST_UNTIL_MS = int(now + RECOVERY_CORE_REQUEST_TTL_MS)
    _RECOVERY_CORE_REQUEST_EPOCH = int(epoch)
    try:
        _RECOVERY_CORE_REQUEST_ANCHOR = (float(anchor_xy[0]), float(anchor_xy[1])) if anchor_xy is not None else None
    except Exception:
        _RECOVERY_CORE_REQUEST_ANCHOR = None
    _RECOVERY_CORE_REQUEST_REASON = str(reason or "recovery_core_setup")


def _clear_core_precast_request() -> None:
    global _RECOVERY_CORE_REQUEST_UNTIL_MS, _RECOVERY_CORE_REQUEST_ANCHOR, _RECOVERY_CORE_REQUEST_REASON
    _RECOVERY_CORE_REQUEST_UNTIL_MS = 0
    _RECOVERY_CORE_REQUEST_ANCHOR = None
    _RECOVERY_CORE_REQUEST_REASON = ""


def _reset_core_rejoin_wait() -> None:
    global _RECOVERY_CORE_WAIT_SINCE_MS, _LAST_CORE_GATE_LOG_MS
    _RECOVERY_CORE_WAIT_SINCE_MS = 0
    _LAST_CORE_GATE_LOG_MS = 0


def _core_rejoin_wait_elapsed_ms(now: int) -> int:
    """Start/return the bounded healthy recovery->ST coordination window."""
    global _RECOVERY_CORE_WAIT_SINCE_MS
    if int(_RECOVERY_CORE_WAIT_SINCE_MS or 0) <= 0:
        _RECOVERY_CORE_WAIT_SINCE_MS = int(now)
        return 0
    return max(0, int(now) - int(_RECOVERY_CORE_WAIT_SINCE_MS))


def _log_core_rejoin_gate(now: int, **fields) -> None:
    """Throttle the high-frequency recovery gate marker without hiding state."""
    global _LAST_CORE_GATE_LOG_MS
    if int(_LAST_CORE_GATE_LOG_MS or 0) > 0 and int(now) - int(_LAST_CORE_GATE_LOG_MS) < int(RECOVERY_CORE_GATE_LOG_INTERVAL_MS):
        return
    _LAST_CORE_GATE_LOG_MS = int(now)
    _log("TEAM_RECOVERY_REJOIN_GATE", **fields)


def get_core_precast_request() -> tuple[bool, tuple[float, float] | None, str]:
    """Return the live local recovery->ST setup request without creating a second state machine."""
    epoch = _session_epoch()
    now = _now_ms()
    if int(_RECOVERY_CORE_REQUEST_UNTIL_MS or 0) <= now:
        return False, None, ""
    if int(_RECOVERY_CORE_REQUEST_EPOCH or 0) > 0 and int(epoch or 0) != int(_RECOVERY_CORE_REQUEST_EPOCH):
        return False, None, ""
    return True, _RECOVERY_CORE_REQUEST_ANCHOR, str(_RECOVERY_CORE_REQUEST_REASON or "recovery_core_setup")


def _iter_party_accounts_for_rez():
    try:
        from Py4GWCoreLib.HeroAI.utils import SameMapOrPartyAsAccount
        own_party_id = int(GLOBAL_CACHE.Party.GetPartyID() or 0)
        seen: set[int] = set()
        for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
            if not getattr(account, "IsSlotActive", False) or getattr(account, "IsIsolated", False):
                continue
            if not SameMapOrPartyAsAccount(account):
                continue
            party_id = int(getattr(getattr(account, "AgentPartyData", None), "PartyID", 0) or 0)
            if own_party_id > 0 and party_id != own_party_id:
                continue
            aid = int(getattr(getattr(account, "AgentData", None), "AgentID", 0) or 0)
            if aid > 0 and aid not in seen:
                seen.add(aid)
                yield account
            for hero in GLOBAL_CACHE.ShMem.GetHeroesFromPlayers(aid) or []:
                if not getattr(hero, "IsSlotActive", False) or getattr(hero, "IsIsolated", False):
                    continue
                if not SameMapOrPartyAsAccount(hero):
                    continue
                hid = int(getattr(getattr(hero, "AgentData", None), "AgentID", 0) or 0)
                if hid > 0 and hid not in seen:
                    seen.add(hid)
                    yield hero
    except Exception:
        return


def _rez_capability_status() -> tuple[bool, bool, bool]:
    """Return (telemetry_known, ready_rez_exists, rez_cast_in_progress)."""
    known = False
    ready = False
    in_progress = False
    if not _REZ_SKILL_IDS:
        return True, False, False
    try:
        for account in _iter_party_accounts_for_rez() or []:
            known = True
            aid = int(getattr(getattr(account, "AgentData", None), "AgentID", 0) or 0)
            if aid <= 0 or not Agent.IsValid(aid) or not Agent.IsAlive(aid):
                continue
            try:
                if Agent.IsCasting(aid) and int(Agent.GetCastingSkillID(aid) or 0) in _REZ_SKILL_IDS:
                    in_progress = True
            except Exception:
                pass
            try:
                for row in getattr(getattr(getattr(account, "AgentData", None), "Skillbar", None), "Skills", ()) or ():
                    sid = int(getattr(row, "Id", 0) or 0)
                    if sid in _REZ_SKILL_IDS and float(getattr(row, "Recharge", 0.0) or 0.0) <= 0.0:
                        ready = True
                        break
            except Exception:
                pass
    except Exception:
        return False, False, False
    return bool(known), bool(ready), bool(in_progress)


def _log(event: str, **fields) -> None:
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(str(event), **fields)
    except Exception:
        pass


def _set_state(new_state: str, snapshot: RecoverySnapshot, reason: str) -> None:
    global _STATE, _STATE_SINCE_MS, _LAST_LOG_SIGNATURE, _LAST_LOG_MS
    now = _now_ms()
    new_state = str(new_state)
    signature = (new_state, str(reason), int(snapshot.alive), int(snapshot.dead), int(snapshot.low35), int(snapshot.low55))
    changed = new_state != str(_STATE)
    if changed:
        _STATE = new_state
        _STATE_SINCE_MS = int(now)
    if changed or signature != _LAST_LOG_SIGNATURE or now - int(_LAST_LOG_MS or 0) >= 2500:
        _LAST_LOG_SIGNATURE = signature
        _LAST_LOG_MS = int(now)
        _log(
            "TEAM_RECOVERY_STATE",
            recovery_state=str(new_state), reason=str(reason),
            party_size=int(snapshot.party_size), alive=int(snapshot.alive), dead=int(snapshot.dead),
            observed_members=int(snapshot.observed_members), expected_members=int(snapshot.expected_members),
            snapshot_valid=bool(snapshot.snapshot_valid),
            low35=int(snapshot.low35), low55=int(snapshot.low55),
            lowest_hp=round(float(snapshot.lowest_hp), 3),
            lowest_hp_scope="living_members_only",
            catastrophic_confirm_count=int(_CATASTROPHIC_CONFIRM_COUNT or 0),
            catastrophic_confirmation_age_ms=(
                max(0, int(now - int(_LAST_CATASTROPHIC_SAMPLE_MS or now)))
                if int(_LAST_CATASTROPHIC_SAMPLE_MS or 0) > 0 else 0
            ),
            catastrophic_confirm_min_ms=int(RECOVERY_SNAPSHOT_CONFIRM_MIN_MS),
            catastrophic_confirm_max_ms=int(RECOVERY_SNAPSHOT_CONFIRM_MAX_MS),
            has_safe_anchor=bool(_LAST_SAFE_ANCHOR is not None),
            policy="stage1_55_recovery_confirmation_observable_no_threshold_change",
        )


def _observe_safe_anchor(anchor_hint: tuple[float, float] | None, snapshot: RecoverySnapshot) -> None:
    global _LAST_SAFE_ANCHOR, _LAST_SAFE_ANCHOR_MS
    if anchor_hint is None or snapshot.party_size <= 0 or not bool(snapshot.snapshot_valid):
        return
    try:
        anchor = (float(anchor_hint[0]), float(anchor_hint[1]))
    except Exception:
        return
    # Record only genuinely quiet/healthy positions; never learn a "safe" point
    # while the group is already collapsing or standing in active AoE.
    if snapshot.dead > 0 or snapshot.low55 > 0 or float(snapshot.lowest_hp) < 0.75:
        return
    if _enemy_near(Player.GetXY(), Range.Earshot.value) or not _anchor_is_safe(anchor):
        return
    _LAST_SAFE_ANCHOR = anchor
    _LAST_SAFE_ANCHOR_MS = _now_ms()


def evaluate_recovery(*, anchor_hint: tuple[float, float] | None, manual_flag_active: bool, is_leader: bool) -> tuple[str, RecoverySnapshot, tuple[float, float] | None]:
    """Return (action, snapshot, destination): action is none|move|hold."""
    global _HEALTHY_SINCE_MS, _CATASTROPHIC_CONFIRM_COUNT, _LAST_CATASTROPHIC_SAMPLE_MS
    global _RECOVERY_DEAD_SINCE_MS, _RECOVERY_LAST_DEAD_COUNT, _RECOVERY_LAST_DEAD_PROGRESS_MS
    _session_epoch()
    snap = _metrics()
    _observe_safe_anchor(anchor_hint, snap)
    now = _now_ms()

    if bool(is_leader) or bool(manual_flag_active):
        _set_state("FIGHT", snap, "manual_or_leader_owns_movement")
        _HEALTHY_SINCE_MS = 0
        _CATASTROPHIC_CONFIRM_COUNT = 0
        _LAST_CATASTROPHIC_SAMPLE_MS = 0
        _clear_core_precast_request()
        _reset_core_rejoin_wait()
        return "none", snap, None

    # Stage 17 / E20b: never derive Stabilize/Retreat/Regroup from a partial
    # party snapshot.  Preserve an already-active support hold, but issue no
    # movement and do not mutate the recovery state on an invalid frame.
    if not bool(snap.snapshot_valid):
        _CATASTROPHIC_CONFIRM_COUNT = 0
        _LAST_CATASTROPHIC_SAMPLE_MS = 0
        _log(
            "TEAM_RECOVERY_SNAPSHOT_REJECTED",
            recovery_state=str(_STATE), party_size=int(snap.party_size),
            expected_members=int(snap.expected_members), observed_members=int(snap.observed_members),
            alive=int(snap.alive), dead=int(snap.dead),
            policy="exp36_stage17_e20b_complete_party_snapshot_required",
        )
        if str(_STATE) in {"RETREAT", "REGROUP", "STABILIZE", "RECOVER"}:
            return "hold", snap, _LAST_SAFE_ANCHOR
        return "none", snap, None

    safe_anchor = _LAST_SAFE_ANCHOR
    if safe_anchor is not None and now - int(_LAST_SAFE_ANCHOR_MS or 0) > RECOVERY_SAFE_ANCHOR_MAX_AGE_MS:
        safe_anchor = None

    if snap.catastrophic:
        _HEALTHY_SINCE_MS = 0
        _reset_core_rejoin_wait()
        # A single valid catastrophic frame may stabilize support, but Retreat
        # and Regroup require two distinct, temporally separated confirmations.
        if (
            int(_LAST_CATASTROPHIC_SAMPLE_MS or 0) > 0
            and RECOVERY_SNAPSHOT_CONFIRM_MIN_MS <= now - int(_LAST_CATASTROPHIC_SAMPLE_MS) <= RECOVERY_SNAPSHOT_CONFIRM_MAX_MS
        ):
            _CATASTROPHIC_CONFIRM_COUNT = min(2, int(_CATASTROPHIC_CONFIRM_COUNT or 0) + 1)
            _LAST_CATASTROPHIC_SAMPLE_MS = int(now)
        elif int(_LAST_CATASTROPHIC_SAMPLE_MS or 0) <= 0 or now - int(_LAST_CATASTROPHIC_SAMPLE_MS) > RECOVERY_SNAPSHOT_CONFIRM_MAX_MS:
            _CATASTROPHIC_CONFIRM_COUNT = 1
            _LAST_CATASTROPHIC_SAMPLE_MS = int(now)
        # Calls within the same scheduler slice do not count as a second sample.
        if int(_CATASTROPHIC_CONFIRM_COUNT or 0) < 2:
            _set_state("STABILIZE", snap, "catastrophic_first_valid_snapshot_waiting_confirmation")
            return "hold", snap, None
        if safe_anchor is not None and _anchor_is_safe(safe_anchor):
            try:
                distance = float(Utils.Distance(Player.GetXY(), safe_anchor))
            except Exception:
                distance = 99999.0
            if distance > RECOVERY_ARRIVAL_DISTANCE:
                _set_state("RETREAT", snap, "catastrophic_party_state_confirmed_safe_anchor_available")
                return "move", snap, safe_anchor
            _set_state("REGROUP", snap, "catastrophic_party_state_confirmed_at_safe_anchor")
            return "hold", snap, safe_anchor
        _set_state("STABILIZE", snap, "catastrophic_confirmed_but_no_verified_safe_anchor")
        return "hold", snap, None

    _CATASTROPHIC_CONFIRM_COUNT = 0
    _LAST_CATASTROPHIC_SAMPLE_MS = 0

    if snap.severe:
        _HEALTHY_SINCE_MS = 0
        _reset_core_rejoin_wait()
        _set_state("STABILIZE", snap, "severe_hp_pressure_hold_for_support")
        return "hold", snap, None

    healthy_now = bool(snap.alive > 0 and snap.low55 == 0 and float(snap.lowest_hp) >= 0.70)
    recovering = str(_STATE) in {"RETREAT", "REGROUP", "STABILIZE", "RECOVER"}

    if snap.dead > 0:
        # Stage 2.19 / P1-C: prioritize resurrection, but do not let one unreachable
        # corpse silently suppress all offense forever.  DEGRADED keeps automatic
        # movement held while releasing the offensive support gate for healthy
        # survivors after bounded no-progress time.
        if int(_RECOVERY_DEAD_SINCE_MS or 0) <= 0:
            _RECOVERY_DEAD_SINCE_MS = int(now)
            _RECOVERY_LAST_DEAD_PROGRESS_MS = int(now)
            _RECOVERY_LAST_DEAD_COUNT = int(snap.dead)
        elif int(snap.dead) < int(_RECOVERY_LAST_DEAD_COUNT or snap.dead):
            _RECOVERY_LAST_DEAD_PROGRESS_MS = int(now)
            _RECOVERY_LAST_DEAD_COUNT = int(snap.dead)
        elif int(snap.dead) > int(_RECOVERY_LAST_DEAD_COUNT or 0):
            _RECOVERY_LAST_DEAD_COUNT = int(snap.dead)
            _RECOVERY_LAST_DEAD_PROGRESS_MS = int(now)

        rez_known, rez_ready, rez_in_progress = _rez_capability_status()
        rejoin_anchor = safe_anchor or anchor_hint
        core_known, core_ready, core_required, core_active, core_missing = _core_rejoin_status(rejoin_anchor)
        core_gate_released = bool(not core_known or core_ready)
        if healthy_now and bool(core_known) and not bool(core_ready):
            core_wait_ms = _core_rejoin_wait_elapsed_ms(now)
            if core_wait_ms < int(RECOVERY_CORE_REJOIN_MAX_WAIT_MS):
                _arm_core_precast_request(rejoin_anchor, "recovery_with_dead_allies_needs_local_core")
            else:
                core_gate_released = True
                _clear_core_precast_request()
        elif core_gate_released:
            _reset_core_rejoin_wait()
        elapsed_no_progress = max(0, int(now - int(_RECOVERY_LAST_DEAD_PROGRESS_MS or now)))
        patience = int(RECOVERY_DEAD_REZ_PATIENCE_MS if (rez_ready or rez_in_progress or not rez_known) else RECOVERY_DEAD_NO_REZ_GRACE_MS)
        can_degrade = bool(
            recovering and healthy_now and core_gate_released
            and not rez_in_progress and elapsed_no_progress >= patience
        )
        if can_degrade:
            _HEALTHY_SINCE_MS = 0
            _clear_core_precast_request()
            _set_state("DEGRADED", snap, "unresurrected_ally_bounded_hold_release_offense_keep_movement_hold")
            _log(
                "TEAM_RECOVERY_DEGRADED_RELEASE", dead=int(snap.dead), rez_known=bool(rez_known),
                rez_ready=bool(rez_ready), rez_in_progress=bool(rez_in_progress),
                no_progress_ms=int(elapsed_no_progress), core_known=bool(core_known), core_ready=bool(core_ready),
                policy="stage2_19_p1c_bounded_rez_hold_offense_release_without_blind_follow",
            )
            return "hold", snap, safe_anchor
        if str(_STATE) == "DEGRADED":
            if rez_in_progress:
                _set_state("RECOVER", snap, "resurrection_started_reenter_support_hold")
            else:
                return "hold", snap, safe_anchor
        _HEALTHY_SINCE_MS = 0
        if recovering or str(_STATE) in {"RETREAT", "REGROUP", "STABILIZE", "RECOVER", "DEGRADED"}:
            _set_state("RECOVER", snap, "dead_allies_remain_allow_rez_support")
            return "hold", snap, safe_anchor
        # One isolated death is not enough to auto-retreat a healthy party.
        _set_state("FIGHT", snap, "isolated_death_no_forced_retreat")
        return "none", snap, None
    else:
        _RECOVERY_DEAD_SINCE_MS = 0
        _RECOVERY_LAST_DEAD_COUNT = 0
        _RECOVERY_LAST_DEAD_PROGRESS_MS = 0


    # Stage 2.24: once recovery has engaged, require a genuinely healthy party
    # and *request* local ST core coverage, but never let core telemetry or an ST
    # recharge hole keep the team in RECOVER forever. The normal health
    # hysteresis runs in parallel with a bounded 4.5 s core setup window.
    if recovering:
        rejoin_anchor = safe_anchor or anchor_hint
        core_known, core_ready, core_required, core_active, core_missing = _core_rejoin_status(rejoin_anchor)
        if not healthy_now:
            _HEALTHY_SINCE_MS = 0
            _reset_core_rejoin_wait()
            _clear_core_precast_request()
            _set_state("RECOVER", snap, "recovery_hysteresis_waiting_for_healthy_party")
            return "hold", snap, safe_anchor

        if _HEALTHY_SINCE_MS <= 0:
            _HEALTHY_SINCE_MS = int(now)

        if bool(core_known) and not bool(core_ready):
            core_wait_ms = _core_rejoin_wait_elapsed_ms(now)
            if core_wait_ms < int(RECOVERY_CORE_REJOIN_MAX_WAIT_MS):
                _arm_core_precast_request(rejoin_anchor, "recovery_hysteresis_waiting_for_local_core_coverage")
                _set_state("RECOVER", snap, "recovery_hysteresis_waiting_for_core_spirits")
                _log_core_rejoin_gate(
                    now,
                    recovery_state=str(_STATE), core_known=bool(core_known), core_ready=bool(core_ready),
                    core_required=int(core_required), core_active=int(core_active),
                    missing_models=",".join(str(int(v)) for v in tuple(core_missing or ())),
                    healthy_now=bool(healthy_now), core_wait_ms=int(core_wait_ms),
                    max_core_wait_ms=int(RECOVERY_CORE_REJOIN_MAX_WAIT_MS),
                    policy="stage2_24_bounded_core_gate_health_hysteresis_runs_in_parallel",
                )
                return "hold", snap, safe_anchor

            # Healthy party + stable health hysteresis wins after the bounded
            # setup window. Continue requesting ST in combat normally; do not
            # turn uncertain/missing spirits into a permanent recovery lock.
            _clear_core_precast_request()
            _log(
                "TEAM_RECOVERY_CORE_GATE_BOUNDED_RELEASE",
                recovery_state=str(_STATE), core_known=bool(core_known), core_ready=bool(core_ready),
                core_required=int(core_required), core_active=int(core_active),
                missing_models=",".join(str(int(v)) for v in tuple(core_missing or ())),
                core_wait_ms=int(core_wait_ms), healthy_stable_ms=max(0, int(now - int(_HEALTHY_SINCE_MS))),
                policy="stage2_24_healthy_party_cannot_deadlock_on_core_spirit_gate",
            )
        else:
            _clear_core_precast_request()
            _reset_core_rejoin_wait()

        if now - int(_HEALTHY_SINCE_MS) < RECOVERY_READY_HYSTERESIS_MS:
            _set_state("RECOVER", snap, "healthy_core_hysteresis_before_rejoin")
            return "hold", snap, safe_anchor
        _reset_core_rejoin_wait()
        _set_state("READY", snap, "party_stable_rejoin_allowed_core_gate_bounded")
        return "none", snap, None

    if healthy_now:
        if _HEALTHY_SINCE_MS <= 0:
            _HEALTHY_SINCE_MS = int(now)
    else:
        _HEALTHY_SINCE_MS = 0

    if str(_STATE) in {"READY", "DEGRADED"}:
        _clear_core_precast_request()
        _reset_core_rejoin_wait()
        _set_state("FIGHT", snap, "normal_team_state")
    return "none", snap, None



def is_support_hold_active() -> bool:
    """Return True only while the party is physically retreating/regrouping.

    Stage 2.23 restores the Stage-1.73 offensive action economy: transient
    STABILIZE/RECOVER states may still suppress Follow and request support, but
    they must not globally shut down eSurge/RoJ damage.  The old Stage-2.18
    behavior created a death spiral where pressure -> RECOVER -> no damage ->
    enemies live longer -> more pressure.  RETREAT/REGROUP remain hard offense
    holds because the party is intentionally repositioning.
    """
    return str(_STATE) in {"RETREAT", "REGROUP"}

def service_recovery_movement(*, anchor_hint: tuple[float, float] | None, manual_flag_active: bool = False, is_leader: bool = False) -> str:
    """Issue only verified retreat movement; return none|move|hold to follower runtime."""
    global _LAST_MOVE_MS
    action, snap, destination = evaluate_recovery(
        anchor_hint=anchor_hint, manual_flag_active=bool(manual_flag_active), is_leader=bool(is_leader)
    )
    if action != "move" or destination is None:
        return str(action)
    try:
        from Py4GWCoreLib.Builds.Skills.SupportGuardSafety import support_route_allowed
        path_ok = support_route_allowed(Player.GetXY(), destination)
    except Exception:
        path_ok = False
    if not _anchor_is_safe(destination) or not path_ok:
        _set_state("STABILIZE", snap, "safe_anchor_or_path_became_unsafe")
        return "hold"
    now = _now_ms()
    try:
        distance = float(Utils.Distance(Player.GetXY(), destination))
    except Exception:
        distance = 99999.0
    if distance <= RECOVERY_ARRIVAL_DISTANCE:
        _set_state("REGROUP", snap, "retreat_destination_reached")
        return "hold"
    if now - int(_LAST_MOVE_MS or 0) >= RECOVERY_MOVE_REISSUE_MS:
        try:
            Player.Move(float(destination[0]), float(destination[1]))
            _LAST_MOVE_MS = int(now)
            _log(
                "TEAM_RECOVERY_RETREAT_MOVE",
                destination=f"{float(destination[0]):.1f},{float(destination[1]):.1f}",
                distance=round(float(distance), 1), alive=int(snap.alive), dead=int(snap.dead),
                policy="exp36_stage16_e20_verified_old_safe_anchor_followers_only",
            )
        except Exception:
            return "hold"
    return "move"
