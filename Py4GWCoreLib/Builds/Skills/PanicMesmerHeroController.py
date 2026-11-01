"""Dedicated Hero Panic controller for the HR Mesmerway.

Recognised core:
Panic / Cry of Frustration / (Mistrust or Complicate) / Unnatural Signet.
Cry of Pain, Overload and Shatter Enchantment are optional managed slots.
Shatter Hex is intentionally left to native hero AI because ally hex-removal is
already handled well there and should not be starved by the offensive controller.

All offensive casts stay inside the same Mesmerway/KeySoJway packet and share the
same Mistrust/Complicate/interrupt/utility reservations as the player-account Mesmers.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from Py4GWCoreLib import Agent, Map, Party, Player, Range, Skill, SkillBar, Utils
from Py4GWCoreLib.Builds.Skills import MesmerwayCoordination as MWC

PANIC_ID = int(Skill.GetID('Panic') or 0)
CRY_OF_FRUSTRATION_ID = int(Skill.GetID('Cry_of_Frustration') or 0)
MISTRUST_ID = int(Skill.GetID('Mistrust') or 0)
COMPLICATE_ID = int(Skill.GetID('Complicate') or 0)
UNNATURAL_SIGNET_ID = int(Skill.GetID('Unnatural_Signet') or 0)
CRY_OF_PAIN_ID = int(Skill.GetID('Cry_of_Pain') or 0)
OVERLOAD_ID = int(Skill.GetID('Overload') or 0)
SHATTER_ENCHANTMENT_ID = int(Skill.GetID('Shatter_Enchantment') or 0)
FRUSTRATION_ID = int(Skill.GetID('Frustration') or 0)
DIVERSION_ID = int(Skill.GetID('Diversion') or 0)
POWER_SPIKE_ID = int(Skill.GetID('Power_Spike') or 0)
SPIRITUAL_PAIN_ID = int(Skill.GetID('Spiritual_Pain') or 0)

_REQUIRED_CORE_IDS = frozenset(s for s in (PANIC_ID, CRY_OF_FRUSTRATION_ID, UNNATURAL_SIGNET_ID) if s > 0)
_MANAGED_IDS = frozenset(s for s in (
    PANIC_ID, CRY_OF_FRUSTRATION_ID, MISTRUST_ID, COMPLICATE_ID, UNNATURAL_SIGNET_ID,
    CRY_OF_PAIN_ID, OVERLOAD_ID, SHATTER_ENCHANTMENT_ID,
    FRUSTRATION_ID, DIVERSION_ID, POWER_SPIKE_ID, SPIRITUAL_PAIN_ID,
) if s > 0)

_SCAN_INTERVAL_MS = 90
_LOCK_REFRESH_MS = 2500
_COMMAND_TIMEOUT_MS = 3200
_COMMAND_GUARD_MS = 120


@dataclass(slots=True)
class _Pending:
    skill_id: int = 0
    slot: int = 0
    target_id: int = 0
    started_ms: int = 0
    start_recharge: int = 0
    purpose: str = ''


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
    matched_logged: bool = False
    lock_failed_logged: bool = False


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


def _bar(index: int):
    try:
        return list(SkillBar.GetHeroSkillbar(int(index)) or [])
    except Exception:
        return []


def _entries(index: int) -> list[tuple[int, int, object]]:
    out = []
    for slot, data in enumerate(_bar(index), start=1):
        try:
            sid = int(getattr(getattr(data, 'id', None), 'id', 0) or 0)
        except Exception:
            sid = 0
        out.append((slot, sid, data))
    return out


def _bar_ids(index: int) -> set[int]:
    return {sid for _, sid, _ in _entries(index) if sid > 0}


def _matching() -> list[tuple[int, int, int]]:
    try:
        heroes = list(Party.GetHeroes() or [])
    except Exception:
        heroes = []
    out = []
    for bar_index, hero in enumerate(heroes):
        hid = int(getattr(hero, 'agent_id', 0) or 0)
        if hid <= 0:
            continue
        # When multiple human clients each own heroes, only control the
        # heroes belonging to this account. Otherwise duplicate skill
        # requests can originate on different clients for the same hero.
        try:
            login = int(Player.GetLoginNumber() or 0)
            owner = int(getattr(hero, 'owner_player_id', 0) or 0)
            if login > 0 and owner > 0 and login != owner:
                continue
        except Exception:
            pass
        ids = _bar_ids(bar_index)
        has_control_slot = bool((MISTRUST_ID > 0 and MISTRUST_ID in ids) or (COMPLICATE_ID > 0 and COMPLICATE_ID in ids))
        if _REQUIRED_CORE_IDS and _REQUIRED_CORE_IDS.issubset(ids) and has_control_slot:
            out.append((bar_index + 1, bar_index, hid))
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
    if restored:
        _log('HERO_PANIC_NATIVE_AI_RESTORED', hero_index=state.hero_index, hero_id=state.hero_id,
             slots=','.join(map(str, restored)))
    state.locked_slots.clear()


def _reconcile() -> list[_State]:
    matches = _matching()
    live = {hid for _, _, hid in matches}
    for hid in list(_STATES):
        if hid not in live:
            _restore_native(_STATES.pop(hid))
    ordered = []
    for hero_index, bar_index, hid in matches:
        slots = _managed_slots(bar_index)
        state = _STATES.get(hid)
        if state is None:
            state = _State(hero_index=hero_index, bar_index=bar_index, hero_id=hid, managed_slots=slots)
            _STATES[hid] = state
        else:
            if state.bar_index != bar_index or state.managed_slots != slots:
                _restore_native(state)
                state.pending = _Pending()
            state.hero_index = hero_index
            state.bar_index = bar_index
            state.managed_slots = slots
        if not state.matched_logged:
            state.matched_logged = True
            _log('HERO_PANIC_MATCHED', hero_index=hero_index, hero_id=hid,
                 managed_slots=','.join(map(str, slots)), native_shatter_hex=True)
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
            _log('HERO_PANIC_AI_LOCKED', hero_index=state.hero_index, hero_id=state.hero_id,
                 slots=','.join(map(str, state.managed_slots)))
    elif not state.lock_failed_logged:
        state.lock_failed_logged = True
        _log('HERO_PANIC_AI_LOCK_FAILED', hero_index=state.hero_index, hero_id=state.hero_id)
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
        return int(getattr(e[1], 'recharge', 0) or 0)
    except Exception:
        return 0


def _ready(state: _State, skill_id: int) -> bool:
    return _entry(state, skill_id) is not None and _recharge(state, skill_id) == 0


def _energy_current(state: _State) -> int:
    try:
        return int(max(0.0, float(Agent.GetEnergy(state.hero_id) or 0.0)) * max(0, int(Agent.GetMaxEnergy(state.hero_id) or 0)))
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


def _pending_confirmed(state: _State, p: _Pending) -> bool:
    try:
        if Agent.IsCasting(state.hero_id) and int(Agent.GetCastingSkillID(state.hero_id) or 0) == p.skill_id:
            return True
    except Exception:
        pass
    return _recharge(state, p.skill_id) > p.start_recharge


def _process_pending(state: _State, now: int) -> bool:
    p = state.pending
    if p.skill_id <= 0:
        return False
    if _pending_confirmed(state, p):
        _log('HERO_PANIC_CAST_CONFIRMED', hero_index=state.hero_index, hero_id=state.hero_id,
             skill_id=p.skill_id, target_id=p.target_id, purpose=p.purpose)
        state.pending = _Pending()
        return False
    if now - p.started_ms <= _COMMAND_TIMEOUT_MS:
        return True
    _log('HERO_PANIC_CAST_TIMEOUT', hero_index=state.hero_index, hero_id=state.hero_id,
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
    try:
        SkillBar.HeroUseSkill(int(target_id), int(slot), int(state.hero_index))
    except Exception as exc:
        _log('HERO_PANIC_CAST_ERROR', hero_index=state.hero_index, hero_id=state.hero_id,
             skill_id=skill_id, error=type(exc).__name__, purpose=purpose)
        return False
    state.pending = _Pending(skill_id=skill_id, slot=slot, target_id=int(target_id),
                             started_ms=now, start_recharge=max(0, _recharge(state, skill_id)),
                             purpose=str(purpose))
    state.last_command_ms = now
    _log('HERO_PANIC_CAST_REQUESTED', hero_index=state.hero_index, hero_id=state.hero_id,
         skill_id=skill_id, target_id=int(target_id), purpose=purpose)
    return True


def _panic_target() -> tuple[int, int, int]:
    anchor, members = MWC.cluster_anchor_and_members()
    if anchor <= 0 or not members:
        return 0, 0, 0
    rows = []
    caster_count = 0
    try:
        from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
    except Exception:
        EnemyKnowledge = None
    for eid in members:
        eid = int(eid)
        caster = support = healer = offense = 0
        if EnemyKnowledge is not None:
            try:
                scores = EnemyKnowledge.role_scores(eid)
                caster = int(scores.get('caster', 0)); support = int(scores.get('support', 0))
                healer = int(scores.get('healer', 0)); offense = int(scores.get('offense', 0))
            except Exception:
                pass
        relevant = max(caster, support, healer) >= 55
        caster_count += int(relevant)
        try:
            casting = 160 if Agent.IsCasting(eid) else 0
        except Exception:
            casting = 0
        density = 0
        try:
            from Py4GWCoreLib.Builds.Skills import TeamCombatFocus
            density = int(TeamCombatFocus.count_adjacent_enemies(eid, Range.Adjacent.value) or 0)
        except Exception:
            pass
        score = casting + caster + support + healer + int(offense * 0.45) + density * 35
        rows.append((score, int(relevant), density, -eid, eid))
    rows.sort(reverse=True)
    return int(rows[0][-1]), int(anchor), int(caster_count)


def _panic_quick_cleanup(state: _State) -> bool:
    if not any(_ready(state, sid) and _can_pay(state, sid)
               for sid in (UNNATURAL_SIGNET_ID, SPIRITUAL_PAIN_ID) if sid > 0):
        return False
    target = int(MWC.reserve_cleanup_target(origin_id=state.hero_id, hard_only=True) or 0)
    if not target:
        return False
    accepted = False
    for sid in (UNNATURAL_SIGNET_ID, SPIRITUAL_PAIN_ID):
        if sid > 0 and _ready(state, sid) and _can_pay(state, sid):
            accepted = bool(_request(state, sid, target, 'hero_panic_cleanup'))
            if accepted:
                break
    if not accepted:
        MWC.release_cleanup_reservation(target, reason='hero_panic_cleanup_not_requested')
    return accepted


def _run_state(state: _State, now: int) -> None:
    if now - state.last_scan_ms < _SCAN_INTERVAL_MS:
        return
    state.last_scan_ms = now
    if not _ensure_locked(state, now) or _process_pending(state, now) or not _can_act(state):
        return

    # Panic packet owner first. Player-account Panic uses the same short claim.
    if _ready(state, PANIC_ID):
        target, anchor, casters = _panic_target()
        if target > 0 and MWC.try_claim_panic_packet(anchor):
            if _request(state, PANIC_ID, target, 'shared_packet_panic'):
                _log('HERO_PANIC_PACKET', hero_index=state.hero_index, hero_id=state.hero_id,
                     target_id=target, anchor_id=anchor, caster_count=casters)
                return

    # Stage 2.16: if this Panic hero carries Complicate instead of Mistrust, give
    # dangerous casts (notably Splinter Weapon) first refusal.  The shared
    # reservation prevents duplicate Complicate/Cry spending across accounts.
    if _ready(state, COMPLICATE_ID):
        target, enemy_skill_id = MWC.reserve_complicate_target(emergency_only=True)
        if int(target or 0) > 0:
            if _request(state, COMPLICATE_ID, int(target), 'shared_complicate_emergency'):
                _log('HERO_PANIC_COMPLICATE', hero_index=state.hero_index, hero_id=state.hero_id,
                     target_id=int(target), enemy_skill_id=int(enemy_skill_id), emergency=True)
                return
            MWC.release_complicate_reservation(
                int(enemy_skill_id), target_id=int(target), reason='hero_panic_complicate_emergency_not_fired'
            )

    if _ready(state, CRY_OF_FRUSTRATION_ID):
        packet = MWC.reserve_cry_packet()
        if packet is not None and _request(state, CRY_OF_FRUSTRATION_ID, int(packet.target_id), 'shared_cry_packet'):
            MWC.register_cry_interrupt_cast(packet, CRY_OF_FRUSTRATION_ID)
            return

    if _panic_quick_cleanup(state):
        return

    if _ready(state, SHATTER_ENCHANTMENT_ID):
        target = int(MWC.reserve_shatter_enchantment_target() or 0)
        if target > 0 and _request(state, SHATTER_ENCHANTMENT_ID, target, 'shatter_enchantment'):
            return

    if _ready(state, MISTRUST_ID):
        target = int(MWC.reserve_mistrust_target() or 0)
        if target > 0 and _request(state, MISTRUST_ID, target, 'shared_mistrust'):
            MWC.register_mistrust_cast(target)
            return

    if _ready(state, COMPLICATE_ID):
        target, enemy_skill_id = MWC.reserve_complicate_target(emergency_only=False)
        if int(target or 0) > 0:
            if _request(state, COMPLICATE_ID, int(target), 'shared_complicate_value'):
                _log('HERO_PANIC_COMPLICATE', hero_index=state.hero_index, hero_id=state.hero_id,
                     target_id=int(target), enemy_skill_id=int(enemy_skill_id), emergency=False)
                return
            MWC.release_complicate_reservation(
                int(enemy_skill_id), target_id=int(target), reason='hero_panic_complicate_value_not_fired'
            )

    if _ready(state, OVERLOAD_ID):
        target = int(MWC.reserve_overload_target() or 0)
        if target > 0 and _request(state, OVERLOAD_ID, target, 'shared_overload'):
            return

    if _ready(state, CRY_OF_PAIN_ID):
        target = int(MWC.reserve_cry_of_pain_target() or 0)
        if target > 0 and _request(state, CRY_OF_PAIN_ID, target, 'shared_cry_of_pain'):
            MWC.register_single_interrupt_cast(target, CRY_OF_PAIN_ID)
            return

    # Optional new eight-skill Panic loadout: control high-value active casters.
    # The existing shared exact-cast interrupt claims are authoritative.
    if _ready(state, POWER_SPIKE_ID):
        target = int(MWC.reserve_single_interrupt_target(POWER_SPIKE_ID) or 0)
        if target > 0:
            if _request(state, POWER_SPIKE_ID, target, 'shared_power_spike'):
                return
            MWC.release_single_interrupt_reservation(target, POWER_SPIKE_ID, reason='hero_panic_no_dispatch')

    # Predict the *next* valuable caster skill. Diversion's 3s cast cannot
    # reliably react to an already-started spell; do not compete with interrupts.
    if _ready(state, DIVERSION_ID) and _can_pay(state, DIVERSION_ID):
        target = int(MWC.reserve_diversion_target(origin_id=state.hero_id) or 0)
        if (target > 0 and Utils.Distance(Agent.GetXY(state.hero_id), Agent.GetXY(target)) <= float(Range.Spellcast.value)
                and _request(state, DIVERSION_ID, target, 'shared_predictive_diversion')):
            return
    # Legacy Frustration bars remain supported through the shared selector.
    if _ready(state, FRUSTRATION_ID) and _can_pay(state, FRUSTRATION_ID):
        target = int(MWC.reserve_frustration_target(origin_id=state.hero_id) or 0)
        if (target > 0 and Utils.Distance(Agent.GetXY(state.hero_id), Agent.GetXY(target)) <= float(Range.Spellcast.value)
                and _request(state, FRUSTRATION_ID, target, 'shared_frustration')):
            return

    if _ready(state, SPIRITUAL_PAIN_ID):
        target = int(MWC.reserve_spiritual_pain_target() or 0)
        if target > 0 and _request(state, SPIRITUAL_PAIN_ID, target, 'shared_spiritual_pain'):
            return

    if _ready(state, UNNATURAL_SIGNET_ID):
        target = int(MWC.reserve_unnatural_target() or 0)
        if target > 0:
            _request(state, UNNATURAL_SIGNET_ID, target, 'packet_unnatural_filler')


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
        _log('HERO_PANIC_CONTROLLER_ACTIVE')
    states = _reconcile()
    if not states:
        if not _NO_MATCH_LOGGED:
            _NO_MATCH_LOGGED = True
            _log('HERO_PANIC_NO_MATCH')
        return
    _NO_MATCH_LOGGED = False
    now = _now_ms()
    for state in states:
        try:
            _run_state(state, now)
        except Exception as exc:
            _log('HERO_PANIC_CONTROLLER_ERROR', hero_index=state.hero_index, hero_id=state.hero_id,
                 error=type(exc).__name__, detail=str(exc)[:180])
