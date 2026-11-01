"""Dedicated Aura-of-the-Lich Minion Bomber hero controller.

Recognised core:
Aura of the Lich / Animate Bone Minions / Death Nova / Putrid Bile.
Aegis / Spirit Bond / Reversal of Fortune / Shield of Absorption remain enabled
for native hero AI so the defensive half of the bar can react immediately.

Policy:
- Aura of the Lich is used to rebuild efficiently when corpse/minion state makes it worthwhile;
- Animate Bone Minions keeps the body count healthy;
- Death Nova is reserved for genuinely endangered front-line minions, not blanket spam;
- Putrid Bile uses a short rolling HP-loss estimate and aims for roughly the last five seconds
  before a clustered foe dies, with a conservative low-HP fallback when prediction is unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from Py4GWCoreLib import Agent, AgentArray, Map, Party, Range, Routines, Skill, SkillBar, Utils

AURA_OF_THE_LICH_ID = int(Skill.GetID('Aura_of_the_Lich') or 0)
ANIMATE_BONE_MINIONS_ID = int(Skill.GetID('Animate_Bone_Minions') or 0)
DEATH_NOVA_ID = int(Skill.GetID('Death_Nova') or 0)
PUTRID_BILE_ID = int(Skill.GetID('Putrid_Bile') or 0)

_REQUIRED_IDS = frozenset(s for s in (AURA_OF_THE_LICH_ID, ANIMATE_BONE_MINIONS_ID, DEATH_NOVA_ID, PUTRID_BILE_ID) if s > 0)
_MANAGED_IDS = _REQUIRED_IDS

_SCAN_INTERVAL_MS = 100
_LOCK_REFRESH_MS = 2500
_COMMAND_TIMEOUT_MS = 3600
_MINION_CAST_TIMEOUT_MS = 5000
_FAST_CAST_TIMEOUT_MS = 2800
_FAILED_RETRY_GUARD_MS = 1400
_COMMAND_GUARD_MS = 140
_CAST_RANGE = float(Range.Spellcast.value)
_EARSHOT = float(Range.Earshot.value)
_NEARBY = float(Range.Nearby.value)
_ADJACENT = float(Range.Adjacent.value)
_DESIRED_MINIONS = 8
_EMERGENCY_MINIONS = 3
_DEATH_NOVA_HARD_HP = 0.30
_DEATH_NOVA_CLUSTER_HP = 0.42
_BILE_IDEAL_TTD_S = 5.0
_BILE_MAX_TTD_S = 7.5
_BILE_FALLBACK_HP = 0.30
_CORPSE_LOCK_MS = 4300


@dataclass(slots=True)
class _Pending:
    skill_id: int = 0
    slot: int = 0
    target_id: int = 0
    started_ms: int = 0
    start_recharge: int = 0
    purpose: str = ''
    start_minions: int = 0
    start_corpses: int = 0


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
    hp_track: dict[int, tuple[int, float, float]] = field(default_factory=dict)
    matched_logged: bool = False
    lock_failed_logged: bool = False
    minion_owner_fallback_logged: bool = False
    last_failed_skill_id: int = 0
    last_failed_target_id: int = 0
    last_failed_ms: int = 0


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
        if hid > 0 and _REQUIRED_IDS.issubset(_bar_ids(bar_index)):
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
        _log('HERO_MINION_BOMBER_NATIVE_AI_RESTORED', hero_index=state.hero_index,
             hero_id=state.hero_id, slots=','.join(map(str, restored)))
    state.locked_slots.clear()


def _reconcile() -> list[_State]:
    matches = _matching(); live = {hid for _, _, hid in matches}
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
                _restore_native(state); state.pending = _Pending(); state.hp_track.clear()
            state.hero_index = hero_index; state.bar_index = bar_index; state.managed_slots = slots
        if not state.matched_logged:
            state.matched_logged = True
            _log('HERO_MINION_BOMBER_MATCHED', hero_index=hero_index, hero_id=hid,
                 managed_slots=','.join(map(str, slots)), native_protection_slots=True)
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
            _log('HERO_MINION_BOMBER_AI_LOCKED', hero_index=state.hero_index,
                 hero_id=state.hero_id, slots=','.join(map(str, state.managed_slots)))
    elif not state.lock_failed_logged:
        state.lock_failed_logged = True
        _log('HERO_MINION_BOMBER_AI_LOCK_FAILED', hero_index=state.hero_index, hero_id=state.hero_id)
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


def _distance(a: int, b: int) -> float:
    try:
        return float(Utils.Distance(Agent.GetXY(int(a)), Agent.GetXY(int(b))))
    except Exception:
        return 999999.0


def _health(agent_id: int) -> float:
    try:
        return float(Agent.GetHealth(int(agent_id)) or 0.0)
    except Exception:
        return 1.0


def _has_effect(agent_id: int, skill_id: int) -> bool:
    try:
        return bool(Routines.Checks.Agents.HasEffect(int(agent_id), int(skill_id)))
    except Exception:
        return False


def _own_minions(state: _State) -> list[int]:
    """Return this hero's minions, with a live-runtime fallback.

    On some hero snapshots GetOwnerID() is not populated reliably even though
    GetMinionArray() contains the hero's allied minions.  Prefer exact ownership;
    if it yields nothing, use alive minions in earshot of this dedicated MM hero.
    This bar is the team's sole minion producer, so the fallback is safer than
    falsely concluding that the army is always zero and spamming AotL/Animate.
    """
    owned: list[int] = []
    nearby: list[int] = []
    try:
        minions = list(AgentArray.GetMinionArray() or [])
    except Exception:
        minions = []
    for aid in minions:
        aid = int(aid)
        try:
            if aid <= 0 or not Agent.IsAlive(aid):
                continue
            if _distance(state.hero_id, aid) <= _EARSHOT * 1.15:
                nearby.append(aid)
            if int(Agent.GetOwnerID(aid) or 0) == int(state.hero_id):
                owned.append(aid)
        except Exception:
            continue
    if owned:
        return owned
    if nearby and not state.minion_owner_fallback_logged:
        state.minion_owner_fallback_logged = True
        _log('HERO_MINION_BOMBER_OWNER_FALLBACK', hero_index=state.hero_index, hero_id=state.hero_id,
             visible_minions=len(nearby), policy='owner_id_missing_use_nearby_allied_minions')
    return nearby


def _corpses_near(state: _State, radius: float) -> list[int]:
    try:
        corpses = list(Routines.Agents.GetExploitableCorpses(max(_CAST_RANGE * 2.0, float(radius))) or [])
    except Exception:
        corpses = []
    out = [int(c) for c in corpses if _distance(state.hero_id, int(c)) <= float(radius)]
    out.sort(key=lambda c: _distance(state.hero_id, c))
    return out


def _adjacent_enemy_count(agent_id: int) -> int:
    try:
        xy = Agent.GetXY(int(agent_id))
        return sum(1 for eid in (AgentArray.GetEnemyArray() or [])
                   if Agent.IsAlive(int(eid)) and Utils.Distance(xy, Agent.GetXY(int(eid))) <= _ADJACENT * 1.15)
    except Exception:
        return 0


def _nearby_enemy_count(agent_id: int) -> int:
    try:
        xy = Agent.GetXY(int(agent_id))
        return sum(1 for eid in (AgentArray.GetEnemyArray() or [])
                   if Agent.IsAlive(int(eid)) and Utils.Distance(xy, Agent.GetXY(int(eid))) <= _NEARBY)
    except Exception:
        return 0


def _death_nova_target(state: _State) -> int:
    rows = []
    for mid in _own_minions(state):
        if _distance(state.hero_id, mid) > _CAST_RANGE or _has_effect(mid, DEATH_NOVA_ID):
            continue
        hp = _health(mid)
        near = _adjacent_enemy_count(mid)
        # No blanket novae: only minions that are genuinely close to death and
        # in position to explode into enemies are eligible.
        eligible = hp <= _DEATH_NOVA_HARD_HP or (near >= 1 and hp <= _DEATH_NOVA_CLUSTER_HP)
        if not eligible or near <= 0:
            continue
        rows.append((-near, hp, mid))
    rows.sort()
    return int(rows[0][2]) if rows else 0


def _update_ttd(state: _State, enemy_id: int, now: int) -> float | None:
    hp = _health(enemy_id)
    old = state.hp_track.get(int(enemy_id))
    if old is None:
        state.hp_track[int(enemy_id)] = (now, hp, 0.0)
        return None
    last_ms, last_hp, smooth_loss = old
    dt = max(0.001, (now - int(last_ms)) / 1000.0)
    if dt < 0.25:
        return (hp / smooth_loss) if smooth_loss > 0.003 else None
    instant = max(0.0, float(last_hp) - hp) / dt
    smooth = instant if smooth_loss <= 0 else (smooth_loss * 0.65 + instant * 0.35)
    state.hp_track[int(enemy_id)] = (now, hp, smooth)
    if smooth <= 0.003:
        return None
    return max(0.0, hp / smooth)


def _putrid_target(state: _State, now: int) -> tuple[int, float | None]:
    try:
        from Py4GWCoreLib.Builds.Skills import MesmerwayCoordination as MWC
        _anchor, members = MWC.cluster_anchor_and_members()
    except Exception:
        members = []
    if not members:
        try:
            members = [int(e) for e in (AgentArray.GetEnemyArray() or [])
                       if Agent.IsAlive(int(e)) and _distance(state.hero_id, int(e)) <= _CAST_RANGE]
        except Exception:
            members = []
    rows = []
    live = set()
    for eid in members:
        eid = int(eid); live.add(eid)
        if _has_effect(eid, PUTRID_BILE_ID) or _nearby_enemy_count(eid) < 2:
            continue
        hp = _health(eid)
        if hp <= 0.03 or hp > 0.70:
            _update_ttd(state, eid, now)
            continue
        ttd = _update_ttd(state, eid, now)
        predicted = ttd is not None and 1.2 <= ttd <= _BILE_MAX_TTD_S
        fallback = ttd is None and hp <= _BILE_FALLBACK_HP
        if not (predicted or fallback):
            continue
        closeness = abs((ttd if ttd is not None else _BILE_IDEAL_TTD_S) - _BILE_IDEAL_TTD_S)
        rows.append((0 if predicted else 1, closeness, hp, -_nearby_enemy_count(eid), eid, ttd))
    # prune stale samples
    for eid in list(state.hp_track):
        if eid not in live:
            state.hp_track.pop(eid, None)
    rows.sort(key=lambda r: r[:5])
    if not rows:
        return 0, None
    return int(rows[0][4]), rows[0][5]


def _pending_confirmed(state: _State, p: _Pending) -> bool:
    try:
        if Agent.IsCasting(state.hero_id) and int(Agent.GetCastingSkillID(state.hero_id) or 0) == p.skill_id:
            return True
    except Exception:
        pass
    if _recharge(state, p.skill_id) > p.start_recharge:
        return True
    # Hero skillbar recharge snapshots can lag. For corpse/minion skills also
    # confirm from their observable world-state consequences.
    if p.skill_id in (AURA_OF_THE_LICH_ID, ANIMATE_BONE_MINIONS_ID):
        if len(_own_minions(state)) > int(p.start_minions):
            return True
        try:
            corpses_now = len(_corpses_near(state, _EARSHOT if p.skill_id == AURA_OF_THE_LICH_ID else _CAST_RANGE))
            if int(p.start_corpses) > 0 and corpses_now < int(p.start_corpses):
                return True
        except Exception:
            pass
    return False


def _process_pending(state: _State, now: int) -> bool:
    p = state.pending
    if p.skill_id <= 0:
        return False
    if _pending_confirmed(state, p):
        _log('HERO_MINION_BOMBER_CAST_CONFIRMED', hero_index=state.hero_index, hero_id=state.hero_id,
             skill_id=p.skill_id, target_id=p.target_id, purpose=p.purpose,
             minions_now=len(_own_minions(state)))
        state.pending = _Pending(); return False
    timeout = _MINION_CAST_TIMEOUT_MS if p.skill_id in (AURA_OF_THE_LICH_ID, ANIMATE_BONE_MINIONS_ID) else _FAST_CAST_TIMEOUT_MS
    if now - p.started_ms <= timeout:
        return True
    _log('HERO_MINION_BOMBER_CAST_TIMEOUT', hero_index=state.hero_index, hero_id=state.hero_id,
         skill_id=p.skill_id, target_id=p.target_id, purpose=p.purpose, timeout_ms=timeout)
    state.last_failed_skill_id = int(p.skill_id)
    state.last_failed_target_id = int(p.target_id)
    state.last_failed_ms = int(now)
    state.pending = _Pending(); return False


def _request(state: _State, skill_id: int, target_id: int, purpose: str) -> bool:
    e = _entry(state, skill_id)
    if e is None or not _ready(state, skill_id) or not _can_pay(state, skill_id):
        return False
    if state.pending.skill_id > 0 or not _can_act(state):
        return False
    now = _now_ms()
    if now - state.last_command_ms < _COMMAND_GUARD_MS:
        return False
    if (int(state.last_failed_skill_id) == int(skill_id) and int(state.last_failed_target_id) == int(target_id)
            and now - int(state.last_failed_ms or 0) < _FAILED_RETRY_GUARD_MS):
        return False
    slot, _ = e
    start_minions = len(_own_minions(state))
    start_corpses = len(_corpses_near(state, _EARSHOT if skill_id == AURA_OF_THE_LICH_ID else _CAST_RANGE))
    try:
        SkillBar.HeroUseSkill(int(target_id), int(slot), int(state.hero_index))
    except Exception as exc:
        _log('HERO_MINION_BOMBER_CAST_ERROR', hero_index=state.hero_index, hero_id=state.hero_id,
             skill_id=skill_id, purpose=purpose, error=type(exc).__name__)
        return False
    state.pending = _Pending(skill_id=skill_id, slot=slot, target_id=int(target_id), started_ms=now,
                             start_recharge=max(0, _recharge(state, skill_id)), purpose=str(purpose),
                             start_minions=start_minions, start_corpses=start_corpses)
    state.last_command_ms = now
    _log('HERO_MINION_BOMBER_CAST_REQUESTED', hero_index=state.hero_index, hero_id=state.hero_id,
         skill_id=skill_id, target_id=int(target_id), purpose=purpose,
         minions_before=start_minions, corpses_before=start_corpses)
    return True


def _try_aotl(state: _State) -> bool:
    if not _ready(state, AURA_OF_THE_LICH_ID):
        return False
    minions = len(_own_minions(state)); corpses = len(_corpses_near(state, _EARSHOT))
    try:
        enemy_near = bool(Routines.Agents.GetNearestEnemy(Range.Earshot.value))
    except Exception:
        enemy_near = False
    # Avoid consuming a large corpse field when already near the desired cap.
    if minions >= _DESIRED_MINIONS:
        return False
    worthwhile = corpses >= 2 or (minions <= _EMERGENCY_MINIONS and (enemy_near or corpses >= 1))
    if not worthwhile:
        return False
    if _request(state, AURA_OF_THE_LICH_ID, state.hero_id, 'efficient_aotl_rebuild'):
        _log('HERO_MINION_BOMBER_AOTL', hero_index=state.hero_index, hero_id=state.hero_id,
             minions=minions, corpses_in_earshot=corpses)
        return True
    return False


def _try_animate(state: _State, emergency_only: bool = False) -> bool:
    if not _ready(state, ANIMATE_BONE_MINIONS_ID):
        return False
    count = len(_own_minions(state)); limit = _EMERGENCY_MINIONS if emergency_only else _DESIRED_MINIONS
    if count >= limit:
        return False
    corpses = _corpses_near(state, _CAST_RANGE)
    if not corpses:
        return False
    corpse = int(corpses[0])
    if _request(state, ANIMATE_BONE_MINIONS_ID, corpse, 'rebuild_bone_minions'):
        try:
            from Py4GWCoreLib.GlobalCache.WhiteboardLocks import post_minion_lock
            post_minion_lock(corpse, skill_id=ANIMATE_BONE_MINIONS_ID, aftercast_delay=_CORPSE_LOCK_MS)
        except Exception:
            pass
        return True
    return False


def _run_state(state: _State, now: int) -> None:
    if now - state.last_scan_ms < _SCAN_INTERVAL_MS:
        return
    state.last_scan_ms = now
    if not _ensure_locked(state, now) or _process_pending(state, now) or not _can_act(state):
        return

    count = len(_own_minions(state))
    if count <= _EMERGENCY_MINIONS:
        if _try_aotl(state):
            return
        if _try_animate(state, emergency_only=True):
            return

    nova = _death_nova_target(state)
    if nova > 0 and _ready(state, DEATH_NOVA_ID):
        if _request(state, DEATH_NOVA_ID, nova, 'dying_frontline_minion_only'):
            _log('HERO_MINION_BOMBER_DEATH_NOVA_TARGET', hero_index=state.hero_index,
                 hero_id=state.hero_id, minion_id=nova, hp=round(_health(nova), 3),
                 adjacent_enemies=_adjacent_enemy_count(nova))
            return

    if _ready(state, PUTRID_BILE_ID):
        target, ttd = _putrid_target(state, now)
        if target > 0 and _request(state, PUTRID_BILE_ID, target, 'five_second_execution_bile'):
            _log('HERO_MINION_BOMBER_PUTRID_BILE_TARGET', hero_index=state.hero_index,
                 hero_id=state.hero_id, target_id=target, hp=round(_health(target), 3),
                 predicted_ttd_s=(round(float(ttd), 2) if ttd is not None else -1),
                 nearby_enemies=_nearby_enemy_count(target))
            return

    # Use AotL before consuming corpses one at a time when a useful corpse packet exists.
    if _try_aotl(state):
        return
    _try_animate(state, emergency_only=False)


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
        _log('HERO_MINION_BOMBER_CONTROLLER_ACTIVE')
    states = _reconcile()
    if not states:
        if not _NO_MATCH_LOGGED:
            _NO_MATCH_LOGGED = True
            _log('HERO_MINION_BOMBER_NO_MATCH')
        return
    _NO_MATCH_LOGGED = False
    now = _now_ms()
    for state in states:
        try:
            _run_state(state, now)
        except Exception as exc:
            _log('HERO_MINION_BOMBER_CONTROLLER_ERROR', hero_index=state.hero_index,
                 hero_id=state.hero_id, error=type(exc).__name__, detail=str(exc)[:180])
