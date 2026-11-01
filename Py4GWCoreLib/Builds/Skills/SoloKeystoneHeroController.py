"""Solo-client support for *variant* Keystone native heroes.

Keeps the project's older, highly tuned dedicated Keystone hero controller
unchanged. This handler only owns bar variants that do not fit that exact
older pattern. It never issues movement, target-changing, travel or bot actions.

Only the local player-owned native heroes are eligible. The player's own
Keystone/ST/PI/Panic/HR build continues through the existing account controller.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from Py4GWCoreLib import Agent, AgentArray, Map, Party, Player, Profession, Range, Routines, Skill, SkillBar, Utils
from Py4GWCoreLib.Builds.Skills import KeystoneMesmerHeroController as Legacy


def _id(name: str) -> int:
    try:
        return int(Skill.GetID(name) or 0)
    except Exception:
        return 0


KEYSTONE = _id("Keystone_Signet")
CELERITY = _id("Symbolic_Celerity")
INSCRIPTIONS = _id("Mantra_of_Inscriptions")
SIGNETS = _id("Mantra_of_Signets")
HEROIC_REFRAIN = _id("Heroic_Refrain")
MIMICRY = _id("Arcane_Mimicry")

# The base controller owns its older 8-skill SoJ/Mimicry bar. The solo variant
# explicitly does not try to use Mimicry, which needs a separately verified donor.
OFFENSE = tuple(s for s in dict.fromkeys(_id(name) for name in (
    "Unnatural_Signet", "Signet_of_Clumsiness", "Signet_of_Sorrow",
    "Signet_of_Corruption_kurzick", "Signet_of_Corruption_luxon",
    "Signet_of_Toxic_Shock", "Signet_of_Deadly_Corruption",
    "Barbed_Signet",
    "Signet_of_Weariness", "Tryptophan_Signet", "Castigation_Signet",
    "Bane_Signet", "Signet_of_Rage", "Signet_of_Mystic_Wrath",
    # Hex Eater is an ally utility. Signet of Lost Souls and the interrupt
    # signets have situational preconditions; let native Hero AI handle them.
)) if s > 0)
# Current 2026-10-20 Me/Mo template used by the four Urgoz heroes.  One
# test hero still had Inscriptions rather than Signets in slot 8; accept that
# stance as a transitional variant, but never rewrite a player's hero skillbar.
CURRENT_MONK_SIGNETS = tuple(_id(name) for name in (
    "Unnatural_Signet", "Bane_Signet", "Castigation_Signet",
    "Signet_of_Rage", "Signet_of_Mystic_Wrath",
))
CURRENT_MONK_CORE = frozenset((KEYSTONE, CELERITY) + CURRENT_MONK_SIGNETS)
SUPPORTED = frozenset(s for s in (KEYSTONE, CELERITY, INSCRIPTIONS, SIGNETS) + OFFENSE if s > 0)
SCAN_MS = 60  # faster hero-only polling; does not bypass GW casting locks
_DIAGNOSTIC_MS = 7000
_LAST_SCAN_LOG = 0
LOCK_REFRESH_MS = 2500
# Keep this long enough: real confirmations in the 20 Oct HM log arrived
# as late as 3344 ms. Blindly shortening the timeout would resend valid casts.
PENDING_MS = 3600
STALE_TARGET_GRACE_MS = 850
STALE_TARGET_RETRY_MS = 1250
COMMAND_GUARD_MS = 175
BUFF_RENEW_BELOW_MS = 20_000
MANTRA_CRITICAL_RENEW_MS = 6_000
BUFF_RETRY_GUARD_MS = 2_000
# Precast before contact, no movement or enemy-pull actions.
PREP_RANGE = float(Range.Spellcast.value) * 0.90


@dataclass(slots=True)
class State:
    hero_id: int
    hero_position: int
    managed: tuple[int, ...] = ()
    locked: set[int] = field(default_factory=set)
    seen_ms: int = 0
    scanned_ms: int = 0
    locked_ms: int = 0
    last_cmd_ms: int = 0
    pending_skill: int = 0
    pending_since: int = 0
    pending_recharge: int = 0
    pending_target: int = 0
    pending_effect_before: int | None = None
    pending_invalid_since: int = 0
    last_stale_request: tuple[int, int] = (0, 0)
    last_stale_request_ms: int = 0
    last_signet_skill: int = 0
    buff_last_cast: dict[int, int] = field(default_factory=dict)


_STATES: dict[int, State] = {}
_LAST_MAP = 0
_LAST_DIAG = ""


def _now() -> int:
    try:
        import PySystem
        return int(PySystem.get_tick_count64() or 0)
    except Exception:
        import time
        return int(time.monotonic() * 1000)


def _log(event: str, **kw):
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(event, **kw)
    except Exception:
        pass


def _rows(position: int) -> list[tuple[int, int, object]]:
    # Reforged Keystone/RoJ controller confirmed ONE-based slot mapping both
    # for GetHeroSkillbar() and HeroUseSkill(). No guess or zero-based shift.
    try:
        bar = SkillBar.GetHeroSkillbar(int(position)) or []
    except Exception:
        return []
    out = []
    for slot, entry in enumerate(bar, 1):
        try:
            sid = int(getattr(getattr(entry, "id", None), "id", 0) or 0)
        except Exception:
            sid = 0
        out.append((slot, sid, entry))
    return out


def _owner_matches(hero_id: int) -> bool:
    try:
        login = int(Player.GetLoginNumber() or 0)
        if login <= 0:
            return False
        for h in Party.GetHeroes() or []:
            if int(getattr(h, "agent_id", 0) or 0) == hero_id:
                return int(getattr(h, "owner_player_id", 0) or 0) == login
        return False
    except Exception:
        return False


def _primary_mesmer(hero_id: int) -> bool:
    try:
        primary, _secondary = Agent.GetProfessionIDs(hero_id)
        return int(getattr(primary, "value", primary)) == int(getattr(Profession.Mesmer, "value", Profession.Mesmer))
    except Exception:
        return False


def _variant_matches(skill_ids: set[int]) -> bool:
    # Do not steal the bar from the original dedicated Keystone hero controller.
    old_required = getattr(Legacy, "_FIXED_KEYSTONE_IDS", frozenset())
    if old_required and old_required.issubset(skill_ids):
        return False
    # Prefer the proven full current Me/Mo signature. A hero using Mantra of
    # Inscriptions instead of Signets still matches for safe, explicit testing.
    if (all(s > 0 for s in CURRENT_MONK_CORE)
            and CURRENT_MONK_CORE.issubset(skill_ids)
            and (SIGNETS in skill_ids or INSCRIPTIONS in skill_ids)):
        return True
    # Keep the existing solo-hero variants working without taking ownership
    # away from the older dedicated Mimicry controller.
    return bool(KEYSTONE > 0 and KEYSTONE in skill_ids
                and (CELERITY in skill_ids or INSCRIPTIONS in skill_ids)
                and any(s in skill_ids for s in OFFENSE))


def _reconcile(now: int) -> list[State]:
    global _LAST_SCAN_LOG
    found = set()
    diagnostics = []
    scan_log = now - _LAST_SCAN_LOG >= _DIAGNOSTIC_MS
    if scan_log:
        _LAST_SCAN_LOG = now
    for pos in range(1, 8):
        try:
            hero_id = int(Party.Heroes.GetHeroAgentIDByPartyPosition(pos) or 0)
        except Exception:
            continue
        if hero_id <= 0:
            continue
        own = _owner_matches(hero_id)
        mes = _primary_mesmer(hero_id) if own else False
        if not own or not mes:
            if scan_log:
                diagnostics.append(f"p{pos}:h{hero_id}:owner={int(own)}:mesmer={int(mes)}")
            continue
        rows = _rows(pos)
        ids = {sid for _slot, sid, _entry in rows if sid > 0}
        state = _STATES.get(hero_id)
        if scan_log and KEYSTONE in ids:
            diagnostics.append(
                f"p{pos}:h{hero_id}:ks=1:match={int(_variant_matches(ids))}:"
                f"bar={','.join(map(str, sorted(ids)))}"
            )
        if state is None and not _variant_matches(ids):
            continue
        if state is not None and not _variant_matches(ids):
            # A transient empty/incomplete bar is never grounds for rapidly
            # handing slots back to native Hero AI mid-cast.
            if (not ids or KEYSTONE in ids) and now - state.seen_ms < 1300:
                found.add(hero_id)
                continue
            _restore(state)
            _STATES.pop(hero_id, None)
            continue
        if state is None:
            state = State(hero_id=hero_id, hero_position=pos)
            _STATES[hero_id] = state
            current = (all(s > 0 for s in CURRENT_MONK_CORE)
                       and CURRENT_MONK_CORE.issubset(ids))
            _log("SOLO_KEYSTONE_VARIANT_MATCHED", hero_id=hero_id, hero_index=pos,
                 skill_ids=",".join(map(str, sorted(ids))),
                 current_me_mo=bool(current), mantra_id=(SIGNETS if SIGNETS in ids else INSCRIPTIONS))
        state.hero_position = pos
        state.seen_ms = now
        state.managed = tuple(slot for slot, sid, _entry in rows if sid in SUPPORTED)
        found.add(hero_id)
    for hero_id, state in list(_STATES.items()):
        if hero_id not in found and now - state.seen_ms > 1300:
            _restore(state)
            _STATES.pop(hero_id, None)
    if scan_log and diagnostics:
        _log("SOLO_KEYSTONE_DISCOVERY", details=";".join(diagnostics)[:1300],
             managed_count=len(found), policy="local_owner_only_no_movement")
    return [s for s in _STATES.values() if s.hero_id in found]


def _restore(state: State) -> None:
    for slot in tuple(state.locked):
        try:
            Party.Heroes.SetSkillAIEnabled(state.hero_id, slot, True)
        except Exception:
            pass
    state.locked.clear()
    state.pending_skill = 0


def _lock(state: State, now: int) -> bool:
    if state.locked == set(state.managed) and now - state.locked_ms < LOCK_REFRESH_MS:
        return True
    for slot in state.managed:
        try:
            ok = Party.Heroes.SetSkillAIEnabled(state.hero_id, slot, False)
            if ok is False:
                _log("SOLO_KEYSTONE_AI_LOCK_FAILED", hero_id=state.hero_id,
                     slot=slot, reason="returned_false")
                return False
            state.locked.add(slot)
        except Exception as exc:
            _log("SOLO_KEYSTONE_AI_LOCK_FAILED", hero_id=state.hero_id,
                 slot=slot, reason=type(exc).__name__)
            return False
    state.locked_ms = now
    return True


def _entry(state: State, skill_id: int):
    for slot, sid, row in _rows(state.hero_position):
        if sid == skill_id:
            return slot, row
    return None


def _ready(state: State, sid: int) -> bool:
    e = _entry(state, sid)
    return bool(e and int(getattr(e[1], "recharge", 1) or 0) == 0)


def _buff(agent_id: int, sid: int) -> bool:
    try:
        return bool(sid and Routines.Checks.Effects.HasBuff(agent_id, sid))
    except Exception:
        return False


def _remaining_effect(state: State, sid: int) -> int | None:
    try:
        if not _buff(state.hero_id, sid):
            return 0
        from Py4GWCoreLib import GLOBAL_CACHE
        remaining = int(GLOBAL_CACHE.Effects.GetEffectTimeRemaining(int(state.hero_id), int(sid)) or 0)
        return remaining if remaining > 0 else None
    except Exception:
        return None


def _refresh_due(state: State, sid: int, now: int, *, missing_only: bool = False) -> bool:
    if not sid or _entry(state, sid) is None:
        return False
    remaining = _remaining_effect(state, sid)
    if remaining is None or (remaining > 0 and (missing_only or remaining > BUFF_RENEW_BELOW_MS)):
        return False
    if now - int(state.buff_last_cast.get(sid, -BUFF_RETRY_GUARD_MS)) < BUFF_RETRY_GUARD_MS:
        return False
    return True


def _refresh(state: State, sid: int, now: int, reason: str, *, missing_only: bool = False) -> bool:
    if not _refresh_due(state, sid, now, missing_only=missing_only):
        return False
    remaining = _remaining_effect(state, sid)
    if _request(state, sid, 0, reason, now):
        state.buff_last_cast[sid] = now
        _log('SOLO_KEYSTONE_PREBUFF_UPKEEP', hero_id=state.hero_id, skill_id=sid,
             remaining_ms=remaining, threshold_ms=BUFF_RENEW_BELOW_MS, reason=reason)
        return True
    return False


def _hero_free(state: State) -> bool:
    try:
        return bool(Agent.IsAlive(state.hero_id) and not Agent.IsKnockedDown(state.hero_id)
                    and not Agent.IsCasting(state.hero_id) and not Agent.IsMoving(state.hero_id))
    except Exception:
        return False


def _energy_enough(state: State, sid: int) -> bool:
    try:
        current = float(Agent.GetEnergy(state.hero_id) or 0) * float(Agent.GetMaxEnergy(state.hero_id) or 0)
        return current >= float(Skill.Data.GetEnergyCost(sid) or 0)
    except Exception:
        return False


def _clear_pending(state: State) -> None:
    state.pending_skill = 0
    state.pending_since = 0
    state.pending_recharge = 0
    state.pending_target = 0
    state.pending_effect_before = None
    state.pending_invalid_since = 0


def _stale_target(state: State) -> str:
    """Only observe invalid/escaped offensive targets; never move heroes."""
    target = int(state.pending_target)
    if target <= 0:
        return ""
    try:
        if not Agent.IsValid(target) or not Agent.IsAlive(target):
            return "target_dead_or_invalid"
        if _distance(state.hero_id, target) > float(Range.Spellcast.value):
            return "target_out_of_cast_range"
    except Exception:
        # Unknown status is NOT proof a cast failed.
        return ""
    return ""


def _process_pending(state: State, now: int) -> bool:
    if not state.pending_skill:
        return False
    skill = state.pending_skill
    # Cast/recharge are the two existing confirmation paths. Observe refreshed
    # self-buff duration as a third path: a very short cast can be missed
    # between HeroAI frames, especially when Keystone resets recharge.
    casting_this = False
    try:
        casting_this = bool(Agent.IsCasting(state.hero_id)
                            and int(Agent.GetCastingSkillID(state.hero_id) or 0) == skill)
    except Exception:
        pass
    e = _entry(state, skill)
    recharge_changed = False
    if e is not None:
        try:
            recharge_changed = int(getattr(e[1], "recharge", 0) or 0) > state.pending_recharge
        except Exception:
            pass
    effect_refreshed = False
    if (state.pending_target == 0 and state.pending_effect_before is not None
            and skill in (CELERITY, INSCRIPTIONS, SIGNETS)):
        remaining = _remaining_effect(state, skill)
        if remaining is not None and remaining > max(3000, state.pending_effect_before + 1800):
            effect_refreshed = True
    if casting_this or recharge_changed or effect_refreshed:
        _log("SOLO_KEYSTONE_CAST_CONFIRMED", hero_id=state.hero_id,
             skill_id=skill, source=("casting" if casting_this else
                                     "recharge" if recharge_changed else "buff_duration"))
        _clear_pending(state)
        return False

    elapsed = now - state.pending_since
    stale = _stale_target(state)
    if stale:
        if not state.pending_invalid_since:
            state.pending_invalid_since = now
        invalid_elapsed = now - state.pending_invalid_since
        # Do NOT cancel during a currently observed cast. When the target
        # disappears/escapes and the hero is idle, an unconfirmed queued
        # command is no longer a reason to freeze the hero for 3.6 seconds.
        if (elapsed >= STALE_TARGET_GRACE_MS
                and invalid_elapsed >= 250 and _hero_free(state)):
            _log("SOLO_KEYSTONE_CAST_STALE_TARGET_RELEASE", hero_id=state.hero_id,
                 skill_id=skill, target_id=state.pending_target,
                 reason=stale, elapsed_ms=elapsed)
            state.last_stale_request = (skill, state.pending_target)
            state.last_stale_request_ms = now
            _clear_pending(state)
            return False
    else:
        state.pending_invalid_since = 0

    if elapsed <= PENDING_MS:
        return True
    # Timeout means "not observed", not necessarily "not executed".
    # A long queued or instantly reset signet can evade both old signals.
    _log("SOLO_KEYSTONE_CAST_TIMEOUT", hero_id=state.hero_id,
         skill_id=skill, target_id=state.pending_target,
         elapsed_ms=elapsed, target_status=(stale or "valid_or_self"),
         reason="confirmation_unobserved")
    _clear_pending(state)
    return False


def _request(state: State, sid: int, target: int, label: str, now: int) -> bool:
    e = _entry(state, sid)
    if (int(target) > 0 and state.last_stale_request == (sid, int(target))
            and now - state.last_stale_request_ms < STALE_TARGET_RETRY_MS):
        return False
    # A target may have died or moved between cluster choice and command.
    if int(target) > 0:
        try:
            if (not Agent.IsValid(int(target)) or not Agent.IsAlive(int(target))
                    or _distance(state.hero_id, int(target)) > float(Range.Spellcast.value)):
                return False
        except Exception:
            return False
    if (e is None or not _ready(state, sid) or not _energy_enough(state, sid)
            or not _hero_free(state) or state.pending_skill or now - state.last_cmd_ms < COMMAND_GUARD_MS):
        return False
    effect_before = (_remaining_effect(state, sid)
                     if int(target) == 0 and sid in (CELERITY, INSCRIPTIONS, SIGNETS)
                     else None)
    try:
        result = SkillBar.HeroUseSkill(int(target), int(e[0]), int(state.hero_position))
        if result is False:
            _log("SOLO_KEYSTONE_CAST_REJECTED", hero_id=state.hero_id,
                 skill_id=sid, reason="hero_use_skill_returned_false")
            return False
    except Exception as exc:
        _log("SOLO_KEYSTONE_CAST_ERROR", hero_id=state.hero_id,
             skill_id=sid, detail=type(exc).__name__)
        return False
    state.pending_effect_before = effect_before
    state.pending_invalid_since = 0
    state.pending_skill = sid
    state.pending_since = now
    state.pending_recharge = int(getattr(e[1], "recharge", 0) or 0)
    state.pending_target = int(target)
    state.last_cmd_ms = now
    _log("SOLO_KEYSTONE_CAST_REQUESTED", hero_id=state.hero_id, skill_id=sid,
         target_id=target, purpose=label, hero_index=state.hero_position)
    return True


def _distance(a: int, b: int) -> float:
    try:
        return float(Utils.Distance(Agent.GetXY(a), Agent.GetXY(b)))
    except Exception:
        return 1e9


def _enemies(state: State, radius: float) -> list[int]:
    try:
        return [int(e) for e in AgentArray.GetEnemyArray() or []
                if Agent.IsValid(int(e)) and Agent.IsAlive(int(e))
                and _distance(state.hero_id, int(e)) <= radius]
    except Exception:
        return []


def _target(state: State, enemies: list[int]) -> int:
    """Use the same currently engaged packet as the five player Mesmers.

    Never chase or flag a hero; candidates must already be within the hero's
    *actual* cast range. In a multi-foe packet leave <=5% cleanup to existing
    player-account 2+1 execute claims when another live foe is available.
    """
    if not enemies:
        return 0
    valid = set(enemies)
    try:
        from Py4GWCoreLib.Builds.Skills import TeamCombatFocus as TCF
        plan = TCF.get_team_combat_plan(consumer_role="hero_keystone")
        members = [int(e) for e in (getattr(plan, "member_ids", ()) or ())
                   if int(e) in valid]
        if members:
            focus = int(getattr(plan, "focus_target", 0) or 0)
            secondary = int(getattr(plan, "secondary_focus_target", 0) or 0)
            if focus not in members:
                focus = int(getattr(plan, "aoe_anchor", 0) or 0)
            if focus not in members:
                focus = members[0]
            # Low-HP 2+1 finishers are owned by the player-account controller.
            # Supporting heroes should not send four extra signets to the same
            # nearly dead target while other members remain alive.
            try:
                hp = float(Agent.GetHealth(focus) or 0.0)
            except Exception:
                hp = 1.0
            if 0.0 < hp <= 0.05 and len(members) > 1:
                others = [a for a in members if a != focus]
                if others:
                    focus = secondary if secondary in others else min(
                        others, key=lambda a: float(Agent.GetHealth(a) or 1.0)
                    )
            if secondary not in members or secondary == focus:
                secondary = 0
            # Never make the alternate lane reselect the dying execute target
            # just because the original focus was moved away from it.
            if len(members) > 1:
                try:
                    if secondary and float(Agent.GetHealth(secondary) or 0.0) <= 0.05:
                        secondary = 0
                    if not secondary:
                        secondary = next(
                            (a for a in members if a != focus
                             and float(Agent.GetHealth(a) or 0.0) > 0.05), 0)
                except Exception:
                    secondary = 0
            # Four hero lanes use 2:2 distribution, but single-target cleanup
            # remains hard focus. Never switch outside the committed packet.
            if secondary and state.hero_position % 2 == 0:
                return int(secondary)
            return int(focus)
    except Exception as exc:
        # A shared-plan API failure must not interrupt native hero combat.
        pass
    try:
        from Py4GWCoreLib.Builds.Skills import MesmerwayCoordination as MWC
        anchor, members = MWC.cluster_anchor_and_members(consumer_role="hero_keystone")
        candidates = [int(e) for e in members if int(e) in valid]
        if int(anchor or 0) in candidates:
            return int(anchor)
        if candidates:
            return candidates[0]
    except Exception:
        pass
    try:
        called = int(Player.GetTargetID() or 0)
        if called in valid:
            return called
    except Exception:
        pass
    return min(enemies, key=lambda e: _distance(state.hero_id, e))

def _run(state: State, now: int) -> None:
    if now - state.scanned_ms < SCAN_MS:
        return
    state.scanned_ms = now
    if not _lock(state, now) or _process_pending(state, now) or not _hero_free(state):
        return
    foes = _enemies(state, float(Range.Spellcast.value))
    if not foes:
        return
    near_prepull = bool(_enemies(state, PREP_RANGE))
    # The controller owns skill casts, not movement or a pull command. Enemy
    # proximity simply allows self-buffs before the first offensive signet.
    # Missing/removed enchantment recovers immediately, regardless of whether
    # a damage signet is ready. Active enchantment renewal is handled below.
    if _refresh(state, CELERITY, now, 'missing_celerity_urgent', missing_only=True):
        return
    mantra = INSCRIPTIONS if _entry(state, INSCRIPTIONS) else SIGNETS if _entry(state, SIGNETS) else 0

    # Prep when enemies first enter the 90% spellcast safety margin. Renewals
    # at <=20s happen here only when no foe is currently casting/attacking.
    # Otherwise do not postpone a ready damage signet for a proactive refresh.
    pre_prep = False
    if near_prepull:
        try:
            pre_prep = not any(bool(Agent.IsCasting(e) or Agent.IsAttacking(e)) for e in foes)
        except Exception:
            pre_prep = False
    if pre_prep and _refresh(state, CELERITY, now, 'prepull_celerity_20s'):
        return

    # Current build uses only one stance: Mantra. Prepare it after Celerity
    # and before Keystone without any obsolete Symbolic Posture dependency.
    if pre_prep and mantra:
        if _refresh(state, mantra, now, 'prepull_mantra_before_keystone'):
            return

    if KEYSTONE and not _buff(state.hero_id, KEYSTONE) and _buff(state.hero_id, CELERITY):
        if mantra and not _buff(state.hero_id, mantra) and pre_prep:
            # No rush or forced wait during actual combat, but pre-pull prime
            # comes only after available enchantment + Mantra setup.
            if _ready(state, mantra):
                return
        if _request(state, KEYSTONE, 0, 'prime_keystone_after_prebuffs', now):
            return

    target = _target(state, foes)
    if target <= 0:
        return
    # Urgent stance upkeep beats ordinary signets only in the <=6s window.
    # _refresh() is ready-now and fail-open on cooldown, unknown timers,
    # missing energy or current casting. There is no second stance.
    if mantra:
        mantra_remaining = _remaining_effect(state, mantra)
        if (mantra_remaining is not None
            and mantra_remaining <= MANTRA_CRITICAL_RENEW_MS
            and _refresh(state, mantra, now, 'critical_mantra_6s_before_offense')):
            _log('SOLO_KEYSTONE_MANTRA_CRITICAL_RENEW',
                 hero_id=state.hero_id, remaining_ms=mantra_remaining,
                 threshold_ms=MANTRA_CRITICAL_RENEW_MS,
                 policy='ready_now_before_offense_no_wait')
            return
    # Existing offense target/skill order is unchanged. Ready damaging signets
    # have priority over proactive active-buff renewals during combat.
    is_current = (all(s > 0 for s in CURRENT_MONK_CORE)
                  and CURRENT_MONK_CORE.issubset({sid for _slot, sid, _r in _rows(state.hero_position)}))
    order = [sid for sid in (CURRENT_MONK_SIGNETS if is_current else OFFENSE)
             if sid > 0 and _entry(state, sid) is not None]
    if order:
        # Rotate the start so all four heroes do not cast the same packet
        # signet in the same frame. No extra timed wait is introduced.
        offset = (max(1, state.hero_position) - 1) % len(order)
        order = order[offset:] + order[:offset]
    for sid in order:
        if _ready(state, sid) and _request(state, sid, target, 'keystone_offense', now):
            state.last_signet_skill = sid
            return
    # Free cast window: the full damage packet was probed and had no
    # executable signet. Neither upkeep path creates an offensive wait state.
    if _refresh(state, CELERITY, now, 'free_window_celerity_20s'):
        return
    if mantra:
        if _refresh(state, mantra, now, 'free_window_mantra_20s'):
            return


def run(*, enabled: bool = True) -> None:
    global _LAST_MAP, _LAST_DIAG
    if not enabled:
        for s in list(_STATES.values()):
            _restore(s)
        _STATES.clear()
        return
    try:
        map_id = int(Map.GetMapID() or 0)
        if not Map.IsExplorable() or not Party.IsPartyLoaded():
            run(enabled=False)
            return
    except Exception:
        return
    if _LAST_MAP and map_id != _LAST_MAP:
        run(enabled=False)
    _LAST_MAP = map_id
    now = _now()
    for state in _reconcile(now):
        try:
            _run(state, now)
        except Exception as exc:
            _log("SOLO_KEYSTONE_CONTROLLER_ERROR", hero_id=state.hero_id,
                 error=type(exc).__name__, detail=str(exc)[:160])
