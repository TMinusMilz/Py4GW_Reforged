"""Urgoz Frost Mesmer/Ranger hero controller.

Exact supported bar core:
Greater Conflagration / Edge of Extinction / Mistrust / Cry of Frustration /
Unnatural Signet / Spiritual Pain / Shatter Delusions / Mantra of Frost.
Mesmer primary is preferred for Domination 16; legacy R/Me + Shatter Hex remains supported during migration.

Policy:
- Before aggro, while the party is close to the next pull, plant Greater
  Conflagration first and Edge of Extinction second.
- Replant when the party has moved out of the previous spirit coverage or the
  expected spirit lifetime is near expiry.
- During combat, Cry/Mistrust keep first refusal. Missing frost nature spirits
  may be repaired after the control lane, before filler damage.
- Mesmer offensive targets reuse the HR Mesmerway shared reservations.
"""
from __future__ import annotations

# Historical runtime marker retained for regression/log-parser compatibility.
_PREVIOUS_CONTROLLER_VERSION = 'v33_stage2_33_frost_state_preserving_reconcile'

from dataclasses import dataclass, field

from Py4GWCoreLib import Agent, AgentArray, Map, Party, Player, Profession, Range, Routines, Skill, SkillBar, SpiritModelID, Utils
from Py4GWCoreLib.Builds.Skills import MesmerwayCoordination as MWC

GREATER_CONFLAGRATION_ID = int(Skill.GetID('Greater_Conflagration') or 0)
EDGE_OF_EXTINCTION_ID = int(Skill.GetID('Edge_of_Extinction') or 0)
MISTRUST_ID = int(Skill.GetID('Mistrust') or 0)
CRY_OF_FRUSTRATION_ID = int(Skill.GetID('Cry_of_Frustration') or 0)
UNNATURAL_SIGNET_ID = int(Skill.GetID('Unnatural_Signet') or 0)
SPIRITUAL_PAIN_ID = int(Skill.GetID('Spiritual_Pain') or 0)
SHATTER_HEX_ID = int(Skill.GetID('Shatter_Hex') or 0)
SHATTER_DELUSIONS_ID = int(Skill.GetID('Shatter_Delusions') or 0)
MANTRA_OF_FROST_ID = int(Skill.GetID('Mantra_of_Frost') or 0)

# Stage 2.40: Greater Conflagration remains fully supported/managed, but is no
# longer required for Frost-Ranger build matching. This lets the slot be changed
# without disabling the controller. Edge of Extinction remains part of the core.
_REQUIRED_IDS = frozenset(s for s in (
    EDGE_OF_EXTINCTION_ID, MISTRUST_ID,
    CRY_OF_FRUSTRATION_ID, UNNATURAL_SIGNET_ID, SPIRITUAL_PAIN_ID,
    MANTRA_OF_FROST_ID,
) if s > 0)
_MANAGED_IDS = frozenset(
    set(_REQUIRED_IDS)
    | ({GREATER_CONFLAGRATION_ID} if GREATER_CONFLAGRATION_ID > 0 else set())
    | ({SHATTER_DELUSIONS_ID} if SHATTER_DELUSIONS_ID > 0 else set())
)

_SCAN_INTERVAL_MS = 90
_LOCK_REFRESH_MS = 2500
_COMMAND_TIMEOUT_MS = 3500
_COMMAND_GUARD_MS = 130
# Use a conservative radius below nature-spirit maximum range so movement
# triggers a replant before the party is actually outside the effect.
_SPIRIT_SAFE_RADIUS = 2600.0
_SPIRIT_EXPIRY_HEADROOM_MS = 9000
_FROST_SNAPSHOT_GRACE_MS = 5000
_FROST_CONFIRMED_REMOVED_GRACE_MS = 1800
_FROST_BUILD_CHANGE_CONFIRM_MS = 1800
_SPIRIT_UNKNOWN_OBS_GRACE_MS = 5000
_SPIRIT_UNKNOWN_LOG_INTERVAL_MS = 15000


@dataclass(slots=True)
class _Pending:
    skill_id: int = 0
    slot: int = 0
    target_id: int = 0
    started_ms: int = 0
    start_recharge: int = 0
    purpose: str = ''
    cast_x: float = 0.0
    cast_y: float = 0.0


@dataclass(slots=True)
class _SpiritLease:
    x: float = 0.0
    y: float = 0.0
    until_ms: int = 0
    model_id: int = 0
    owner_id: int = 0
    agent_id: int = 0
    observed_ms: int = 0
    published_key: int = 0
    last_unknown_log_ms: int = 0


@dataclass(slots=True)
class _State:
    hero_index: int = 0
    bar_index: int = -1
    hero_id: int = 0
    managed_slots: tuple[int, ...] = ()
    locked_slots: set[int] = field(default_factory=set)
    last_lock_refresh_ms: int = 0
    last_scan_ms: int = 0
    last_command_ms: int = 0
    pending: _Pending = field(default_factory=_Pending)
    conflagration: _SpiritLease = field(default_factory=_SpiritLease)
    eoe: _SpiritLease = field(default_factory=_SpiritLease)
    matched_logged: bool = False
    lock_failed_logged: bool = False
    missing_since_ms: int = 0
    mismatch_since_ms: int = 0
    mismatch_reason: str = ''
    native_passthrough: bool = False


_STATES: dict[int, _State] = {}
_LAST_MAP_ID = 0
_RUNTIME_LOGGED = False
_NO_MATCH_LOGGED = False


def _now_ms() -> int:
    try:
        import PySystem
        return int(PySystem.get_tick_count64() or 0)
    except Exception:
        import time
        return int(time.monotonic() * 1000.0)


def _log(event: str, **fields) -> None:
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(str(event), **fields)
    except Exception:
        pass


def _entries(index: int) -> list[tuple[int, int, object]]:
    try:
        bar = list(SkillBar.GetHeroSkillbar(int(index)) or [])
    except Exception:
        bar = []
    out = []
    for slot, data in enumerate(bar, start=1):
        try:
            sid = int(getattr(getattr(data, 'id', None), 'id', 0) or 0)
        except Exception:
            sid = 0
        out.append((slot, sid, data))
    return out


def _bar_ids(index: int) -> set[int]:
    return {sid for _, sid, _ in _entries(index) if sid > 0}


def _supported_frost_profession(hero_id: int) -> tuple[bool, bool]:
    """Return (supported, preferred_mesmer_primary)."""
    try:
        primary, secondary = Agent.GetProfessions(int(hero_id))
        p = int(getattr(primary, 'value', primary) or 0)
        sec = int(getattr(secondary, 'value', secondary) or 0)
        ranger = int(Profession.Ranger.value)
        mesmer = int(Profession.Mesmer.value)
        return bool((p == mesmer and sec == ranger) or (p == ranger and sec == mesmer)), bool(p == mesmer and sec == ranger)
    except Exception:
        return False, False


def _frost_profession_snapshot(hero_id: int) -> tuple[bool, bool]:
    """Return (known, supported) without turning an API gap into a build change."""
    try:
        primary, secondary = Agent.GetProfessions(int(hero_id))
        p = int(getattr(primary, 'value', primary) or 0)
        sec = int(getattr(secondary, 'value', secondary) or 0)
        ranger = int(Profession.Ranger.value)
        mesmer = int(Profession.Mesmer.value)
        return True, bool((p == mesmer and sec == ranger) or (p == ranger and sec == mesmer))
    except Exception:
        return False, False


def _frost_bar_snapshot(bar_index: int) -> tuple[bool, set[int]]:
    """Return a trustworthy non-empty hero bar snapshot when one exists."""
    try:
        bar = list(SkillBar.GetHeroSkillbar(int(bar_index)) or [])
    except Exception:
        return False, set()
    if not bar:
        return False, set()
    ids: set[int] = set()
    for data in bar:
        try:
            sid = int(getattr(getattr(data, 'id', None), 'id', 0) or 0)
        except Exception:
            sid = 0
        if sid > 0:
            ids.add(sid)
    # Partial/transient bars are UNKNOWN. A materially populated bar is strong
    # enough to prove an intentional build change when required skills vanish.
    return bool(len(ids) >= 6), ids

def _matching() -> list[tuple[int, int, int]]:
    try:
        heroes = list(Party.GetHeroes() or [])
    except Exception:
        heroes = []
    out = []
    # GetHeroSkillbar() and HeroUseSkill() use the native hero position
    # (1..N), not a Python 0-based list offset. Stage 2.04 accidentally read
    # hero #1 through index 0, so an exact first-slot R/Mesmer bar never
    # matched even though the runtime hook was active. Keep one canonical
    # 1-based hero position for discovery, reads, AI locks and casts.
    for hero_position, hero in enumerate(heroes, start=1):
        hid = int(getattr(hero, 'agent_id', 0) or 0)
        supported, _preferred = _supported_frost_profession(hid)
        if hid <= 0 or not supported:
            continue
        ids = _bar_ids(hero_position)
        shatter_ok = bool(SHATTER_DELUSIONS_ID in ids or SHATTER_HEX_ID in ids)
        if _REQUIRED_IDS.issubset(ids) and shatter_ok:
            out.append((hero_position, hero_position, hid))
    return out


def _managed_slots(bar_index: int) -> tuple[int, ...]:
    return tuple(sorted(slot for slot, sid, _ in _entries(bar_index) if sid in _MANAGED_IDS))


def _restore_native(state: _State) -> None:
    restored = tuple(sorted(state.locked_slots))
    for slot in restored:
        try:
            Party.Heroes.SetSkillAIEnabled(int(state.hero_id), int(slot), True)
        except Exception:
            pass
    state.locked_slots.clear()
    if restored:
        _log('HERO_FROST_RANGER_NATIVE_AI_RESTORED', hero_index=state.hero_index,
             hero_id=state.hero_id, slots=','.join(map(str, restored)))


def _reconcile() -> list[_State]:
    matches = _matching()
    live = {hid for _, _, hid in matches}
    now = _now_ms()
    party_snapshot_known = True
    try:
        party_heroes = list(Party.GetHeroes() or [])
        party_snapshot_known = bool(party_heroes)
    except Exception:
        party_snapshot_known = False
        party_heroes = []
    hero_positions = {int(getattr(hero, 'agent_id', 0) or 0): pos for pos, hero in enumerate(party_heroes, start=1)}

    def mark_mismatch(state: _State, reason: str, confirm_ms: int) -> bool:
        if str(state.mismatch_reason or '') != str(reason):
            state.mismatch_reason = str(reason)
            state.mismatch_since_ms = int(now)
            _log('HERO_FROST_SNAPSHOT_CONTRADICTION', hero_id=state.hero_id, reason=str(reason), confirm_ms=int(confirm_ms))
            return False
        return max(0, int(now) - int(state.mismatch_since_ms or now)) >= int(confirm_ms)

    for hid in list(_STATES):
        if hid in live:
            continue
        state = _STATES[hid]
        present = bool(party_snapshot_known and int(hid) in hero_positions)

        # Confirmed party removal/build change is different from an unreadable
        # moment. Only contradictory evidence may destroy leases/controller state.
        if party_snapshot_known and not present:
            if mark_mismatch(state, 'hero_confirmed_removed', _FROST_CONFIRMED_REMOVED_GRACE_MS):
                _log('HERO_FROST_SNAPSHOT_RELEASE', hero_id=hid, reason='hero_confirmed_removed',
                     held_ms=max(0, int(now) - int(state.mismatch_since_ms or now)))
                _restore_native(_STATES.pop(hid))
            continue

        if present:
            profession_known, supported = _frost_profession_snapshot(int(hid))
            bar_index = int(hero_positions.get(int(hid), state.bar_index) or state.bar_index)
            bar_known, ids = _frost_bar_snapshot(bar_index)
            shatter_ok = bool(SHATTER_DELUSIONS_ID in ids or SHATTER_HEX_ID in ids) if bar_known else False
            bar_supported = bool(bar_known and _REQUIRED_IDS.issubset(ids) and shatter_ok)

            # Stage 2.34: a profession-only contradiction is a transient API glitch
            # if the exact Frost bar is still visible. Destroy GC/EoE leases only
            # when independent populated-bar evidence agrees the build changed.
            if profession_known and not supported:
                if bar_known and not bar_supported:
                    if mark_mismatch(state, 'confirmed_profession_and_skillbar_changed', _FROST_BUILD_CHANGE_CONFIRM_MS):
                        _log('HERO_FROST_SNAPSHOT_RELEASE', hero_id=hid, reason='confirmed_profession_and_skillbar_changed')
                        _restore_native(_STATES.pop(hid))
                    continue
                state.mismatch_since_ms = 0
                state.mismatch_reason = ''
                _log('HERO_FROST_PROFESSION_GLITCH_HOLD', hero_id=hid, bar_supported=bool(bar_supported),
                     policy='stage2_34_profession_only_contradiction_cannot_destroy_spirit_leases')
            elif bar_known and not bar_supported:
                if mark_mismatch(state, 'confirmed_skillbar_changed', _FROST_BUILD_CHANGE_CONFIRM_MS):
                    _log('HERO_FROST_SNAPSHOT_RELEASE', hero_id=hid, reason='confirmed_skillbar_changed')
                    _restore_native(_STATES.pop(hid))
                continue

        # No contradictory evidence: preserve the same state and spirit leases.
        state.mismatch_since_ms = 0
        state.mismatch_reason = ''
        if state.missing_since_ms <= 0:
            state.missing_since_ms = int(now)
            _log('HERO_FROST_SNAPSHOT_HOLD', hero_id=hid, grace_ms=int(_FROST_SNAPSHOT_GRACE_MS),
                 reason='identity_or_skillbar_temporarily_unreadable')
        held_ms = max(0, int(now) - int(state.missing_since_ms or now))
        if held_ms < int(_FROST_SNAPSHOT_GRACE_MS):
            continue
        # A long unreadable period must not leave the hero inert forever. Restore
        # native AI temporarily, but DO NOT delete state/GC/EoE leases. On recovery
        # the controller re-locks the same hero and resumes without recasting merely
        # because discovery flickered.
        if not bool(state.native_passthrough):
            _restore_native(state)
            state.native_passthrough = True
            state.pending = _Pending()
            _log('HERO_FROST_SNAPSHOT_NATIVE_PASSTHROUGH', hero_id=hid, held_ms=int(held_ms),
                 policy='stage2_33_preserve_spirit_leases_during_long_unknown_snapshot')

    ordered = []
    for hero_index, bar_index, hid in matches:
        slots = _managed_slots(bar_index)
        state = _STATES.get(hid)
        if state is None:
            state = _State(hero_index=hero_index, bar_index=bar_index, hero_id=hid, managed_slots=slots)
            _STATES[hid] = state
        else:
            if state.missing_since_ms > 0:
                _log('HERO_FROST_SNAPSHOT_RECOVERED', hero_id=hid, held_ms=max(0, int(now - state.missing_since_ms)),
                     native_passthrough=bool(state.native_passthrough),
                     policy='stage2_33_same_state_recovers_without_spirit_lease_reset')
            state.missing_since_ms = 0
            state.mismatch_since_ms = 0
            state.mismatch_reason = ''
            state.native_passthrough = False
            # A party-position change only changes the native read/cast index. It
            # is not evidence that GC/EoE died, so preserve the leases.
            if state.managed_slots != slots:
                _restore_native(state)
                state.pending = _Pending()
            state.hero_index = hero_index
            state.bar_index = bar_index
            state.managed_slots = slots
        if not state.matched_logged:
            state.matched_logged = True
            ids = _bar_ids(bar_index)
            _log('HERO_FROST_RANGER_MATCHED', hero_index=hero_index, hero_id=hid,
                 managed_slots=','.join(map(str, slots)),
                 mesmer_primary=bool(_supported_frost_profession(hid)[1]),
                 shatter_delusions=bool(SHATTER_DELUSIONS_ID in ids),
                 legacy_native_shatter_hex=bool(SHATTER_HEX_ID in ids and SHATTER_DELUSIONS_ID not in ids),
                 policy='mesmer_primary_preferred_gc_threat_aware_eoe_shared_shatter_mesmerway')
        ordered.append(state)
    return ordered

def _ensure_locked(state: _State, now: int) -> bool:
    full = state.locked_slots == set(state.managed_slots)
    if full and now - state.last_lock_refresh_ms < _LOCK_REFRESH_MS:
        return True
    ok = True
    for slot in state.managed_slots:
        try:
            Party.Heroes.SetSkillAIEnabled(int(state.hero_id), int(slot), False)
            state.locked_slots.add(int(slot))
        except Exception:
            ok = False
    state.last_lock_refresh_ms = now
    if ok:
        state.lock_failed_logged = False
        if not full:
            _log('HERO_FROST_RANGER_AI_LOCKED', hero_index=state.hero_index, hero_id=state.hero_id,
                 slots=','.join(map(str, state.managed_slots)), shatter_hex_native=True)
    elif not state.lock_failed_logged:
        state.lock_failed_logged = True
        _log('HERO_FROST_RANGER_AI_LOCK_FAILED', hero_index=state.hero_index, hero_id=state.hero_id)
    return ok


def _entry(state: _State, skill_id: int):
    for slot, sid, data in _entries(state.bar_index):
        if sid == int(skill_id):
            return slot, data
    return None


def _recharge(state: _State, skill_id: int) -> int:
    e = _entry(state, skill_id)
    if e is None:
        return -1
    try:
        value = int(getattr(e[1], 'recharge', 0) or 0)
        # API wrap/sentinel values are not real multi-day recharges.
        if value >= 0xF0000000:
            return 0
        return max(0, value)
    except Exception:
        return 0


def _ready(state: _State, skill_id: int) -> bool:
    return _entry(state, skill_id) is not None and _recharge(state, skill_id) == 0


def _energy_current(state: _State) -> int:
    try:
        return int(max(0.0, float(Agent.GetEnergy(state.hero_id) or 0.0)) *
                   max(0, int(Agent.GetMaxEnergy(state.hero_id) or 0)))
    except Exception:
        return 0


def _can_pay(state: _State, skill_id: int) -> bool:
    try:
        return _energy_current(state) >= int(Skill.Data.GetEnergyCost(int(skill_id)) or 0)
    except Exception:
        return True


def _can_act(state: _State) -> bool:
    try:
        return not (Agent.IsDead(state.hero_id) or Agent.IsKnockedDown(state.hero_id)
                    or Agent.IsCasting(state.hero_id) or Agent.IsMoving(state.hero_id))
    except Exception:
        return False


def _has_effect(agent_id: int, skill_id: int) -> bool:
    try:
        return bool(Routines.Checks.Agents.HasEffect(int(agent_id), int(skill_id)))
    except Exception:
        return False


def _attribute_rank_for_skill(hero_id: int, skill_id: int) -> int:
    try:
        attr = Skill.Attribute.GetAttribute(int(skill_id))
        attr_id = int(getattr(attr, 'value', attr) or 0)
        for row in Agent.GetAttributes(int(hero_id)) or []:
            if int(getattr(row, 'attribute_id', 0) or 0) != attr_id:
                continue
            return max(0, int(getattr(row, 'level', 0) or getattr(row, 'level_base', 0) or 0))
    except Exception:
        pass
    return 0


def _nature_duration_ms(hero_id: int, skill_id: int) -> int:
    # Current PvE progression for Greater Conflagration and EoE:
    # 30s at rank 0, +14s per rank.
    rank = _attribute_rank_for_skill(hero_id, skill_id)
    return int(max(30, 30 + 14 * rank) * 1000)


def _player_xy() -> tuple[float, float]:
    try:
        x, y = Player.GetXY()
        return float(x), float(y)
    except Exception:
        return 0.0, 0.0


def _hero_xy(state: _State) -> tuple[float, float]:
    try:
        x, y = Agent.GetXY(int(state.hero_id))
        return float(x), float(y)
    except Exception:
        return _player_xy()


def _distance_xy(a: tuple[float, float], b: tuple[float, float]) -> float:
    try:
        return float(Utils.Distance(a, b))
    except Exception:
        dx = float(a[0]) - float(b[0]); dy = float(a[1]) - float(b[1])
        return (dx * dx + dy * dy) ** 0.5


def _revoke_spirit_lease(lease: _SpiritLease, *, reason: str) -> None:
    if lease.published_key:
        try:
            MWC.release_greater_conflagration_cast(lease.published_key)
        except Exception:
            pass
    _log('HERO_FROST_SPIRIT_COVERAGE_REVOKED', model=lease.model_id, spirit_id=lease.agent_id, reason=str(reason))
    lease.until_ms = 0
    lease.published_key = 0


def _spirit_covered(lease: _SpiritLease, now: int) -> bool:
    if lease.until_ms <= now + _SPIRIT_EXPIRY_HEADROOM_MS:
        return False

    # Spatial coverage remains authoritative for the replant policy.  Moving out
    # of the conservative nature-spirit radius intentionally invalidates the old
    # lease so GC/EoE are replanted near the current pull.  This preserves Astra's
    # desired "keep the effects covered" behavior.
    if _distance_xy(_player_xy(), (lease.x, lease.y)) > _SPIRIT_SAFE_RADIUS:
        _revoke_spirit_lease(lease, reason='party_moved_out_of_coverage')
        return False

    if lease.model_id <= 0:
        return True

    found = False
    explicit_dead = False
    try:
        # If a concrete agent was observed previously, query that identity first.
        if lease.agent_id > 0 and Agent.IsValid(int(lease.agent_id)):
            if Agent.IsAlive(int(lease.agent_id)) and Agent.IsSpawned(int(lease.agent_id)):
                lease.observed_ms = now
                found = True
            else:
                explicit_dead = True
        if not found and not explicit_dead:
            for raw in AgentArray.GetSpiritPetArray() or []:
                aid = int(raw or 0)
                if aid <= 0 or not Agent.IsValid(aid):
                    continue
                if int(Agent.GetPlayerNumber(aid) or 0) != lease.model_id:
                    continue
                if int(Agent.GetOwnerID(aid) or 0) not in (0, lease.owner_id):
                    continue
                if _distance_xy(Agent.GetXY(aid), (lease.x, lease.y)) > 220.0:
                    continue
                if Agent.IsAlive(aid) and Agent.IsSpawned(aid):
                    lease.agent_id = aid
                    lease.observed_ms = now
                    found = True
                    break
                if lease.agent_id > 0 and aid == lease.agent_id:
                    explicit_dead = True
    except Exception:
        pass

    if explicit_dead:
        _revoke_spirit_lease(lease, reason='observed_dead')
        return False

    # Critical Stage-2.32 distinction: agent_id==0 means the runtime never gave
    # us a trackable spirit identity.  That is UNKNOWN, not DEAD.  Keep the
    # completed-cast lease while the party is still spatially covered and the
    # expected lifetime has not expired.  In the 2.31 log every false revocation
    # had spirit_id=0, so the previous 2.5s timeout caused needless recasts.
    if not found:
        unknown_for = max(0, int(now - int(lease.observed_ms or now)))
        if unknown_for >= int(_SPIRIT_UNKNOWN_OBS_GRACE_MS) and (
            int(lease.last_unknown_log_ms or 0) <= 0
            or now - int(lease.last_unknown_log_ms or 0) >= int(_SPIRIT_UNKNOWN_LOG_INTERVAL_MS)
        ):
            lease.last_unknown_log_ms = now
            _log('HERO_FROST_SPIRIT_PRESENCE_UNKNOWN_HOLD', model=lease.model_id, spirit_id=lease.agent_id,
                 unknown_for_ms=int(unknown_for), remaining_ms=max(0, int(lease.until_ms - now)),
                 policy='stage2_32_unknown_identity_keeps_spatial_completed_cast_lease')
    return True


def _in_aggro() -> bool:
    try:
        return bool(Routines.Checks.Agents.InAggro())
    except Exception:
        return False


def _close_to_aggro() -> bool:
    try:
        return bool(Routines.Checks.Agents.IsCloseToAggro())
    except Exception:
        return False


def _pending_confirmed(state: _State, p: _Pending) -> bool:
    # Nature rituals remain explicit because their long spatial leases must only
    # be published after a completed activation. Stage 2.20 applies the same
    # completion rule to every other managed Frost skill, including Shatter.
    if p.skill_id in (GREATER_CONFLAGRATION_ID, EDGE_OF_EXTINCTION_ID):
        return _recharge(state, p.skill_id) > p.start_recharge
    try:
        return _recharge(state, p.skill_id) > p.start_recharge
    except Exception:
        return False


def _register_spirit_confirm(state: _State, p: _Pending, now: int) -> None:
    if p.skill_id not in (GREATER_CONFLAGRATION_ID, EDGE_OF_EXTINCTION_ID):
        return
    duration = _nature_duration_ms(state.hero_id, p.skill_id)
    model = SpiritModelID.GREATER_CONFLAGRATION if p.skill_id == GREATER_CONFLAGRATION_ID else SpiritModelID.EDGE_OF_EXTINCTION
    lease = _SpiritLease(x=float(p.cast_x), y=float(p.cast_y), until_ms=now + duration,
                        model_id=int(getattr(model, 'value', model)), owner_id=state.hero_id, observed_ms=now)
    if p.skill_id == GREATER_CONFLAGRATION_ID:
        # Remove the previous spatial registration before publishing the new
        # generation; otherwise a moved/replanted spirit can leave a stale whiteboard
        # lease alive until its natural timeout.
        try:
            if state.conflagration.published_key:
                MWC.release_greater_conflagration_cast(state.conflagration.published_key)
        except Exception:
            pass
        state.conflagration = lease
        try:
            record = MWC.register_greater_conflagration_cast(
                duration_ms=int(duration),
                cast_xy=(float(p.cast_x), float(p.cast_y)),
                source='frost_me_r_hero',
            )
            lease.published_key = int(record.get('spatial_key', 0) or 0)
        except Exception:
            pass
        event = 'HERO_FROST_RANGER_CONFLAGRATION_ACTIVE'
    else:
        state.eoe = lease
        event = 'HERO_FROST_RANGER_EOE_ACTIVE'
    _log(event, hero_index=state.hero_index, hero_id=state.hero_id,
         duration_ms=duration, x=round(lease.x, 1), y=round(lease.y, 1),
         safe_radius=int(_SPIRIT_SAFE_RADIUS))


def _process_pending(state: _State, now: int) -> bool:
    p = state.pending
    if p.skill_id <= 0:
        return False
    if _pending_confirmed(state, p):
        _register_spirit_confirm(state, p, now)
        if p.skill_id == SHATTER_DELUSIONS_ID:
            try:
                feeder = bool(MWC.is_fragility_shatter_feeder_target(int(p.target_id)))
                MWC.register_shatter_delusions_cast(int(p.target_id), fragility_feeder=feeder)
            except Exception:
                pass
        _log('HERO_FROST_RANGER_CAST_CONFIRMED', hero_index=state.hero_index, hero_id=state.hero_id,
             skill_id=p.skill_id, target_id=p.target_id, purpose=p.purpose)
        state.pending = _Pending()
        return False
    if now - p.started_ms <= _COMMAND_TIMEOUT_MS:
        return True
    _log('HERO_FROST_RANGER_CAST_TIMEOUT', hero_index=state.hero_index, hero_id=state.hero_id,
         skill_id=p.skill_id, target_id=p.target_id, purpose=p.purpose)
    state.pending = _Pending()
    return False


def _request(state: _State, skill_id: int, target_id: int, purpose: str) -> bool:
    e = _entry(state, skill_id)
    if e is None or not _ready(state, skill_id) or not _can_pay(state, skill_id):
        return False
    if state.pending.skill_id > 0 or not _can_act(state):
        return False
    now = _now_ms()
    if now - state.last_command_ms < _COMMAND_GUARD_MS:
        return False
    slot, _ = e
    cast_x, cast_y = _hero_xy(state)
    try:
        SkillBar.HeroUseSkill(int(target_id), int(slot), int(state.hero_index))
    except Exception as exc:
        _log('HERO_FROST_RANGER_CAST_ERROR', hero_index=state.hero_index, hero_id=state.hero_id,
             skill_id=skill_id, error=type(exc).__name__, purpose=purpose)
        return False
    state.pending = _Pending(skill_id=skill_id, slot=slot, target_id=int(target_id),
                             started_ms=now, start_recharge=max(0, _recharge(state, skill_id)),
                             purpose=str(purpose), cast_x=cast_x, cast_y=cast_y)
    state.last_command_ms = now
    _log('HERO_FROST_RANGER_CAST_REQUESTED', hero_index=state.hero_index, hero_id=state.hero_id,
         skill_id=skill_id, target_id=int(target_id), purpose=purpose,
         in_aggro=_in_aggro(), close_to_aggro=_close_to_aggro())
    return True


def _try_spirit(state: _State, now: int, skill_id: int, lease: _SpiritLease, purpose: str) -> bool:
    if _spirit_covered(lease, now):
        return False
    if not _ready(state, skill_id):
        return False
    return _request(state, skill_id, state.hero_id, purpose)


def _try_mantra(state: _State) -> bool:
    if _has_effect(state.hero_id, MANTRA_OF_FROST_ID):
        return False
    if not _ready(state, MANTRA_OF_FROST_ID):
        return False
    return _request(state, MANTRA_OF_FROST_ID, state.hero_id, 'maintain_mantra_of_frost')


def _try_cry(state: _State) -> bool:
    if not _ready(state, CRY_OF_FRUSTRATION_ID):
        return False
    packet = MWC.reserve_cry_packet()
    if packet is None:
        return False
    if _request(state, CRY_OF_FRUSTRATION_ID, int(packet.target_id), 'shared_cry_packet'):
        MWC.register_cry_interrupt_cast(packet, CRY_OF_FRUSTRATION_ID)
        return True
    return False


def _try_mistrust(state: _State) -> bool:
    if not _ready(state, MISTRUST_ID):
        return False
    target = int(MWC.reserve_mistrust_target() or 0)
    if target <= 0:
        return False
    if _request(state, MISTRUST_ID, target, 'shared_mistrust'):
        MWC.register_mistrust_cast(target)
        return True
    return False


def _try_shatter_delusions(state: _State) -> bool:
    if SHATTER_DELUSIONS_ID <= 0 or not _ready(state, SHATTER_DELUSIONS_ID):
        return False
    target = int(MWC.reserve_shatter_delusions_target() or 0)
    if target <= 0:
        return False
    return _request(state, SHATTER_DELUSIONS_ID, target, 'shared_shatter_delusions')


def _try_spiritual_pain(state: _State) -> bool:
    if not _ready(state, SPIRITUAL_PAIN_ID):
        return False
    target = int(MWC.reserve_spiritual_pain_target() or 0)
    return bool(target > 0 and _request(state, SPIRITUAL_PAIN_ID, target, 'packet_spiritual_pain'))


def _try_unnatural(state: _State) -> bool:
    if not _ready(state, UNNATURAL_SIGNET_ID):
        return False
    target = int(MWC.reserve_unnatural_target() or 0)
    return bool(target > 0 and _request(state, UNNATURAL_SIGNET_ID, target, 'packet_unnatural'))


def _run_state(state: _State, now: int) -> None:
    if now - state.last_scan_ms < _SCAN_INTERVAL_MS:
        return
    state.last_scan_ms = now
    # Observe even while another spell owns the action lane.
    for lease in (state.conflagration, state.eoe):
        if lease.until_ms > now:
            _spirit_covered(lease, now)
    if not _ensure_locked(state, now) or _process_pending(state, now) or not _can_act(state):
        return

    in_aggro = _in_aggro()
    close = _close_to_aggro()

    # Pre-pull lane: only when combat is imminent but not yet engaged. Greater
    # Conflagration is mandatory first because it enables the Physical->Fire->Cold
    # chain; EoE follows if there is still time before the pull.
    if not in_aggro and close:
        if MWC.greater_conflagration_should_cast():
            if _try_spirit(state, now, GREATER_CONFLAGRATION_ID, state.conflagration, 'preaggro_greater_conflagration_physical_threat'):
                return
        if _try_spirit(state, now, EDGE_OF_EXTINCTION_ID, state.eoe, 'preaggro_edge_of_extinction'):
            return
        if _try_mantra(state):
            return
        return

    # During combat use the same shared Mesmerway control/target reservations as
    # the eSurge lane. Spirit repair is deliberately after Cry/Mistrust so a 2s
    # nature ritual does not steal an important interrupt/control opportunity.
    if in_aggro:
        if _try_cry(state):
            return
        if _try_mistrust(state):
            return
        if _try_mantra(state):
            return
        if MWC.greater_conflagration_should_cast():
            if _try_spirit(state, now, GREATER_CONFLAGRATION_ID, state.conflagration, 'combat_repair_greater_conflagration_physical_threat'):
                return
        if _try_shatter_delusions(state):
            return
        if _try_spirit(state, now, EDGE_OF_EXTINCTION_ID, state.eoe, 'combat_repair_edge_of_extinction'):
            return
        if _try_spiritual_pain(state):
            return
        _try_unnatural(state)
        return

    # Travel lane: keep the cheap stance ready, but do not waste nature rituals
    # far away from the next enemy pack.
    _try_mantra(state)


def _restore_all() -> None:
    for state in list(_STATES.values()):
        _restore_native(state)
    _STATES.clear()


def run(*, enabled: bool = True) -> None:
    global _LAST_MAP_ID, _RUNTIME_LOGGED, _NO_MATCH_LOGGED
    if not enabled:
        _restore_all(); return
    try:
        map_id = int(Map.GetMapID() or 0)
    except Exception:
        map_id = 0
    if _LAST_MAP_ID and map_id != _LAST_MAP_ID:
        _restore_all()
    _LAST_MAP_ID = map_id
    try:
        if not Map.IsExplorable() or not Party.IsPartyLoaded():
            _restore_all(); return
    except Exception:
        return
    if not _RUNTIME_LOGGED:
        _RUNTIME_LOGGED = True
        _log('HERO_FROST_RANGER_CONTROLLER_ACTIVE', version='v34_stage2_34_frost_dual_evidence_reconcile',
             policy='stage2_34_spatial_replant_dual_evidence_state_preserving')
    states = _reconcile()
    if not states:
        if not _NO_MATCH_LOGGED:
            _NO_MATCH_LOGGED = True
            _log('HERO_FROST_RANGER_NO_MATCH')
        return
    _NO_MATCH_LOGGED = False
    now = _now_ms()
    for state in states:
        try:
            _run_state(state, now)
        except Exception as exc:
            _log('HERO_FROST_RANGER_CONTROLLER_ERROR', hero_index=state.hero_index, hero_id=state.hero_id,
                 error=type(exc).__name__, detail=str(exc)[:180])
