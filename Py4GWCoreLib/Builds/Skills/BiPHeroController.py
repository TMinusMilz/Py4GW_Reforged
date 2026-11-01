"""Dedicated hero controller for the current HR Mesmerway N/Rt BiP support.

Core identity (Stage 2.39):
- Blood is Power
- Mend Body and Soul

Optional supported current bar:
- Spirit Transfer
- Spirit Light
- Life
- Recuperation
- Enfeebling Blood
- Signet of Lost Souls (optional only; no longer required for controller matching)
- Protective Was Kaolai is explicitly supported as a native-delegated item spell.
  Its slot stays AI-enabled so the native hero can manage the held ashes/drop heal;
  the current project exposes no verified hero bundle-drop command for the custom controller.

The controller mirrors the player-account BiP policy closely enough for DoA hero tests:
- adaptive 75 -> 80 -> 85% party Energy buffering in sustained combat;
- lowest-Energy eligible ally first, with shared BiP target locks;
- same post-sacrifice safety floor as the player build;
- emergency Restoration healing before support/offense;
- conservative Spirit Transfer / Spirit Light spirit gating;
- Life / Recuperation maintenance and Enfeebling Blood packet support;
- optional Signet of Lost Souls self-energy recovery;
- Protective Was Kaolai compatibility without stealing native held-item/drop control.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from Py4GWCoreLib import Agent, AgentArray, GLOBAL_CACHE, Map, Party, Profession, Range, Routines, Skill, SkillBar, Utils
from Py4GWCoreLib.GlobalCache.WhiteboardLocks import (
    BLOOD_ENERGY_BUFF_LOCK_KEY,
    is_buff_target_lock_blocked,
    post_buff_target_lock,
)

BIP_ID = int(Skill.GetID("Blood_is_Power") or 0)
SOLS_ID = int(Skill.GetID("Signet_of_Lost_Souls") or 0)
MBAS_ID = int(Skill.GetID("Mend_Body_and_Soul") or 0)
SPIRIT_TRANSFER_ID = int(Skill.GetID("Spirit_Transfer") or 0)
SPIRIT_LIGHT_ID = int(Skill.GetID("Spirit_Light") or 0)
LIFE_ID = int(Skill.GetID("Life") or 0)
RECUPERATION_ID = int(Skill.GetID("Recuperation") or 0)
ENFEEBLING_BLOOD_ID = int(Skill.GetID("Enfeebling_Blood") or 0)
PROTECTIVE_WAS_KAOLAI_ID = int(Skill.GetID("Protective_Was_Kaolai") or 0)
WEAKNESS_ID = int(Skill.GetID("Weakness") or 0)

# Stage 2.39: Signet of Lost Souls is no longer part of the identity contract.
# The user's N/Rt BiP remains controller-managed when SoLS is replaced.
_REQUIRED = frozenset(s for s in (BIP_ID, MBAS_ID) if s > 0)

# Skills whose native hero-AI is disabled because this controller owns their
# targeting/timing. SoLS remains optional support if it happens to be equipped.
_SUPPORTED = frozenset(s for s in (
    BIP_ID, SOLS_ID, MBAS_ID, SPIRIT_TRANSFER_ID, SPIRIT_LIGHT_ID,
    LIFE_ID, RECUPERATION_ID, ENFEEBLING_BLOOD_ID,
) if s > 0)

# Protective Was Kaolai is an item spell. Do NOT lock this slot: after the cast
# the native hero must retain authority to drop the ashes and release the party
# heal. The custom controller has no verified HeroDropBundle primitive in this
# project. We still detect/log the slot as explicitly supported/delegated.
_NATIVE_DELEGATED = frozenset(s for s in (PROTECTIVE_WAS_KAOLAI_ID,) if s > 0)
_STAGE239_POLICY = "stage2_39_bip_nrt_optional_sols_1based_mapping_pwk_native_drop"

_SCAN_MS = 90
_LOCK_REFRESH_MS = 2400
_COMMAND_GUARD_MS = 140
_COMMAND_TIMEOUT_MS = 3000
_HEAL_TIMEOUT_MS = 1500
_MATCH_GRACE_MS = 1600
_CAST_RANGE = float(Range.Spellcast.value)
_EARSHOT = float(Range.Earshot.value)
_NEARBY = float(Range.Nearby.value)
_BIP_TARGET_LEASE_MS = 10500  # +20% enchantment-duration BiP setup: ~12 s effect, refresh opens at 10.5 s
_BIP_THROTTLE_MS = 1450


@dataclass(slots=True)
class _Pending:
    skill_id: int = 0
    slot: int = 0
    target_id: int = 0
    started_ms: int = 0
    start_recharge: int = 0
    purpose: str = ""


@dataclass(slots=True)
class _State:
    hero_index: int = 0
    bar_index: int = -1
    hero_id: int = 0
    managed_slots: tuple[int, ...] = ()
    locked_slots: set[int] = field(default_factory=set)
    last_lock_ms: int = 0
    last_scan_ms: int = 0
    last_command_ms: int = 0
    pending: _Pending = field(default_factory=_Pending)
    matched_logged: bool = False
    last_seen_ms: int = 0
    combat_started_ms: int = 0
    was_in_combat: bool = False
    last_bip_ms: int = 0
    bip_target_until: dict[int, int] = field(default_factory=dict)


_STATES: dict[int, _State] = {}
_DISCOVERED: set[int] = set()
_LAST_MAP_ID = 0
_RUNTIME_LOGGED = False
_NO_MATCH_LOGGED = False


def _now() -> int:
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
            sid = int(getattr(getattr(data, "id", None), "id", 0) or 0)
        except Exception:
            sid = 0
        out.append((slot, sid, data))
    return out


def _bar_ids(index: int) -> set[int]:
    return {sid for _, sid, _ in _entries(index) if sid > 0}


def _primary_necro(hero_id: int) -> bool:
    try:
        primary, _secondary = Agent.GetProfessions(int(hero_id))
        p = int(getattr(primary, "value", primary) or 0)
        return p == int(getattr(Profession.Necromancer, "value", Profession.Necromancer))
    except Exception:
        return True


def _matching() -> list[tuple[int, int, int]]:
    try:
        heroes = list(Party.GetHeroes() or [])
    except Exception:
        heroes = []
    out = []
    # Stage 2.39: GetHeroSkillbar() and HeroUseSkill() both use the same
    # 1-based hero position in this Reforged runtime. The old BiP controller
    # read position 0 for the first hero, which caused live HERO_BIP_NO_MATCH.
    for hero_position, hero in enumerate(heroes, start=1):
        hid = int(getattr(hero, "agent_id", 0) or 0)
        if hid <= 0 or not _primary_necro(hid):
            continue
        ids = _bar_ids(hero_position)
        if _REQUIRED and _REQUIRED.issubset(ids):
            _DISCOVERED.add(hid)
        if hid in _DISCOVERED:
            out.append((hero_position, hero_position, hid))
    return out


def _managed_slots(bar_index: int) -> tuple[int, ...]:
    return tuple(sorted(slot for slot, sid, _ in _entries(bar_index) if sid in _SUPPORTED))


def _native_delegated_slots(bar_index: int) -> tuple[int, ...]:
    return tuple(sorted(slot for slot, sid, _ in _entries(bar_index) if sid in _NATIVE_DELEGATED))


def _restore(state: _State) -> None:
    for slot in sorted(state.locked_slots):
        try:
            Party.Heroes.SetSkillAIEnabled(int(state.hero_id), int(slot), True)
        except Exception:
            pass
    if state.locked_slots:
        _log("HERO_BIP_NATIVE_AI_RESTORED", hero_index=state.hero_index, hero_id=state.hero_id,
             slots=",".join(map(str, sorted(state.locked_slots))))
    state.locked_slots.clear()


def _reconcile() -> list[_State]:
    now = _now()
    matches = _matching()
    live = {hid for _, _, hid in matches}
    for hid in list(_STATES):
        if hid in live:
            continue
        state = _STATES[hid]
        if state.last_seen_ms and now - state.last_seen_ms <= _MATCH_GRACE_MS:
            continue
        _restore(_STATES.pop(hid))
    ordered = []
    for hero_index, bar_index, hid in matches:
        slots = _managed_slots(bar_index)
        state = _STATES.get(hid)
        if state is None:
            state = _State(hero_index=hero_index, bar_index=bar_index, hero_id=hid,
                           managed_slots=slots, last_seen_ms=now)
            _STATES[hid] = state
        else:
            state.hero_index = hero_index
            state.bar_index = bar_index
            state.managed_slots = slots
            state.last_seen_ms = now
        if not state.matched_logged:
            state.matched_logged = True
            delegated = _native_delegated_slots(bar_index)
            _log("HERO_BIP_MATCHED", hero_index=hero_index, hero_id=hid,
                 managed_slots=",".join(map(str, slots)),
                 native_delegated_slots=",".join(map(str, delegated)),
                 signet_of_lost_souls_required=False,
                 policy=_STAGE239_POLICY)
            if delegated:
                _log("HERO_BIP_NATIVE_DELEGATED", hero_index=hero_index, hero_id=hid,
                     skill_id=PROTECTIVE_WAS_KAOLAI_ID,
                     slots=",".join(map(str, delegated)),
                     reason="preserve_native_held_ashes_drop_heal")
        ordered.append(state)
    return ordered


def _ensure_locked(state: _State, now: int) -> bool:
    desired = set(state.managed_slots)
    if state.locked_slots == desired and now - state.last_lock_ms < _LOCK_REFRESH_MS:
        return True
    ok = True
    for slot in state.managed_slots:
        try:
            Party.Heroes.SetSkillAIEnabled(int(state.hero_id), int(slot), False)
            state.locked_slots.add(int(slot))
        except Exception:
            ok = False
    state.last_lock_ms = now
    if ok:
        _log("HERO_BIP_AI_LOCKED", hero_index=state.hero_index, hero_id=state.hero_id,
             slots=",".join(map(str, state.managed_slots)))
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
        return int(getattr(e[1], "recharge", 0) or 0)
    except Exception:
        return 0


def _ready(state: _State, skill_id: int) -> bool:
    return skill_id > 0 and _entry(state, skill_id) is not None and _recharge(state, skill_id) == 0


def _energy_ratio(agent_id: int) -> float:
    try:
        from Py4GWCoreLib.HeroAI.utils import GetEnergyValues, IsValidEnergyValue
        value = float(GetEnergyValues(int(agent_id)))
        if IsValidEnergyValue(value):
            return max(0.0, min(1.0, value))
    except Exception:
        pass
    try:
        return max(0.0, min(1.0, float(Agent.GetEnergy(int(agent_id)) or 0.0)))
    except Exception:
        return 1.0


def _energy_current(state: _State) -> int:
    try:
        return int(_energy_ratio(state.hero_id) * max(0, int(Agent.GetMaxEnergy(state.hero_id) or 0)))
    except Exception:
        return 0


def _can_pay(state: _State, skill_id: int) -> bool:
    try:
        return _energy_current(state) >= int(Skill.Data.GetEnergyCost(int(skill_id)) or 0)
    except Exception:
        return True


def _health(agent_id: int) -> float:
    try:
        return max(0.0, min(1.0, float(Agent.GetHealth(int(agent_id)) or 0.0)))
    except Exception:
        return 1.0


def _alive(agent_id: int) -> bool:
    try:
        return bool(Agent.IsAlive(int(agent_id)))
    except Exception:
        return False


def _distance(a: int, b: int) -> float:
    try:
        return float(Utils.Distance(Agent.GetXY(int(a)), Agent.GetXY(int(b))))
    except Exception:
        return 999999.0


def _can_act(state: _State) -> bool:
    try:
        return not (Agent.IsDead(state.hero_id) or Agent.IsKnockedDown(state.hero_id)
                    or Agent.IsCasting(state.hero_id) or Agent.IsMoving(state.hero_id))
    except Exception:
        return False


def _party_ids() -> list[int]:
    ids: set[int] = set()
    try:
        for p in Party.GetPlayers() or []:
            login = int(getattr(p, "login_number", 0) or 0)
            aid = int(Party.Players.GetAgentIDByLoginNumber(login) or 0)
            if aid > 0:
                ids.add(aid)
    except Exception:
        pass
    for getter in ("GetHeroes", "GetHenchmen"):
        try:
            for member in getattr(Party, getter)() or []:
                aid = int(getattr(member, "agent_id", 0) or 0)
                if aid > 0:
                    ids.add(aid)
        except Exception:
            pass
    return sorted(ids)


def _allies_in_cast_range(state: _State, *, other_only: bool = False) -> list[int]:
    rows = []
    for aid in _party_ids():
        if aid <= 0 or (other_only and aid == state.hero_id) or not _alive(aid):
            continue
        try:
            if Agent.IsMinion(aid):
                continue
        except Exception:
            pass
        if _distance(state.hero_id, aid) <= _CAST_RANGE:
            rows.append(aid)
    if not other_only and state.hero_id not in rows and _alive(state.hero_id):
        rows.append(state.hero_id)
    rows.sort(key=lambda aid: (_health(aid), aid))
    return rows


def _lowest_ally(state: _State, hp_threshold: float) -> int:
    rows = [( _health(aid), aid) for aid in _allies_in_cast_range(state) if _health(aid) <= hp_threshold]
    rows.sort()
    return int(rows[0][1]) if rows else 0


def _damaged_count(state: _State, threshold: float) -> int:
    return sum(1 for aid in _allies_in_cast_range(state) if _health(aid) <= threshold)


def _degen_count(state: _State) -> int:
    count = 0
    for aid in _allies_in_cast_range(state):
        try:
            count += 1 if float(Agent.GetHealthRegen(aid) or 0.0) < 0.0 else 0
        except Exception:
            pass
    return count


def _has_effect(agent_id: int, skill_id: int) -> bool:
    try:
        return bool(Routines.Checks.Agents.HasEffect(int(agent_id), int(skill_id)))
    except Exception:
        return False


def _spirit_skill_exists(state: _State, skill_id: int) -> bool:
    try:
        from Py4GWCoreLib import SpiritModelID
        from Py4GWCoreLib.enums import SPIRIT_BUFF_MAP
        for sid in AgentArray.GetSpiritPetArray() or []:
            sid = int(sid or 0)
            if sid <= 0 or not Agent.IsAlive(sid) or _distance(state.hero_id, sid) > _EARSHOT:
                continue
            model = int(Agent.GetPlayerNumber(sid) or 0)
            if model in SpiritModelID._value2member_map_ and int(SPIRIT_BUFF_MAP.get(SpiritModelID(model), 0) or 0) == int(skill_id):
                return True
    except Exception:
        pass
    return False


def _healthy_spirit_in_earshot(state: _State) -> bool:
    try:
        for sid in AgentArray.GetSpiritPetArray() or []:
            sid = int(sid or 0)
            if sid > 0 and Agent.IsAlive(sid) and _distance(state.hero_id, sid) <= _EARSHOT and _health(sid) >= 0.35:
                return True
    except Exception:
        pass
    return False


def _low_enemy_for_sols(state: _State) -> int:
    rows = []
    try:
        enemies = AgentArray.GetEnemyArray() or []
    except Exception:
        enemies = []
    for eid in enemies:
        eid = int(eid)
        if not _alive(eid) or _distance(state.hero_id, eid) > _CAST_RANGE:
            continue
        hp = _health(eid)
        if hp >= 0.50:
            continue
        rows.append((_distance(state.hero_id, eid), hp, eid))
    rows.sort()
    return int(rows[0][2]) if rows else 0


def _enfeebling_target(state: _State) -> int:
    try:
        from Py4GWCoreLib.Builds.Skills import MesmerwayCoordination as MWC
        _anchor, members = MWC.cluster_anchor_and_members()
    except Exception:
        members = []
    rows = []
    for eid in members:
        eid = int(eid)
        if not _alive(eid) or _distance(state.hero_id, eid) > _CAST_RANGE or _has_effect(eid, WEAKNESS_ID):
            continue
        physical = attacking = 0
        try:
            primary, _ = Agent.GetProfessions(eid)
            p = int(getattr(primary, "value", primary) or 0)
            martial = {int(getattr(Profession.Warrior,"value",Profession.Warrior)), int(getattr(Profession.Ranger,"value",Profession.Ranger)),
                       int(getattr(Profession.Assassin,"value",Profession.Assassin)), int(getattr(Profession.Dervish,"value",Profession.Dervish)),
                       int(getattr(Profession.Paragon,"value",Profession.Paragon))}
            physical = 1 if p in martial else 0
            attacking = 1 if Agent.IsAttacking(eid) else 0
        except Exception:
            pass
        rows.append((physical, attacking, -_health(eid), -eid, eid))
    rows.sort(reverse=True)
    return int(rows[0][-1]) if rows and (rows[0][0] or rows[0][1]) else 0


def _combat_clock(state: _State, now: int) -> int:
    try:
        enemy_near = bool(Routines.Agents.GetNearestEnemy(Range.Earshot.value))
    except Exception:
        enemy_near = False
    if enemy_near and not state.was_in_combat:
        state.combat_started_ms = now
    elif not enemy_near:
        state.combat_started_ms = 0
    state.was_in_combat = enemy_near
    return max(0, now - state.combat_started_ms) if enemy_near and state.combat_started_ms else 0


def _bip_threshold(state: _State, combat_ms: int) -> tuple[float, str, int, float, float]:
    values = [_energy_ratio(aid) for aid in _allies_in_cast_range(state, other_only=True)]
    low80 = sum(1 for v in values if v < 0.80)
    mn = min(values) if values else 1.0
    avg = sum(values) / len(values) if values else 1.0
    threshold, mode = 0.75, "opening_75"
    if combat_ms >= 6000:
        threshold, mode = 0.80, "sustained_80"
    if combat_ms >= 15000 and (low80 >= 2 or avg < 0.82 or mn < 0.60):
        threshold, mode = 0.85, "heavy_sustained_85"
    return threshold, mode, low80, mn, avg


def _bip_safe(state: _State) -> bool:
    # Mirrors current player build: 33% max-HP sacrifice, remain >55% and >175 HP.
    hp = _health(state.hero_id)
    try:
        max_hp = int(Agent.GetMaxHealth(state.hero_id) or 0)
    except Exception:
        max_hp = 0
    if max_hp <= 0:
        return False
    hp_after = hp * max_hp - 0.33 * max_hp
    return hp > 0.33 and (hp_after / max_hp) > 0.55 and hp_after > 175


def _bip_target(state: _State, threshold: float, now: int) -> tuple[int, float]:
    state.bip_target_until = {aid: exp for aid, exp in state.bip_target_until.items() if exp > now}
    rows = []
    for aid in _allies_in_cast_range(state, other_only=True):
        if state.bip_target_until.get(aid, 0) > now or _has_effect(aid, BIP_ID):
            continue
        if is_buff_target_lock_blocked(aid, BLOOD_ENERGY_BUFF_LOCK_KEY):
            continue
        e = _energy_ratio(aid)
        if e > threshold:
            continue
        rows.append((e, _distance(state.hero_id, aid), aid))
    rows.sort()
    return (int(rows[0][2]), float(rows[0][0])) if rows else (0, 1.0)


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
        _log("HERO_BIP_CAST_CONFIRMED", hero_index=state.hero_index, hero_id=state.hero_id,
             skill_id=p.skill_id, target_id=p.target_id, purpose=p.purpose)
        state.pending = _Pending()
        return False
    timeout = _HEAL_TIMEOUT_MS if p.skill_id in (MBAS_ID, SPIRIT_TRANSFER_ID, SPIRIT_LIGHT_ID) else _COMMAND_TIMEOUT_MS
    if now - p.started_ms <= timeout:
        return True
    _log("HERO_BIP_CAST_TIMEOUT", hero_index=state.hero_index, hero_id=state.hero_id,
         skill_id=p.skill_id, target_id=p.target_id, purpose=p.purpose)
    state.pending = _Pending()
    return False


def _request(state: _State, skill_id: int, target_id: int, purpose: str) -> bool:
    e = _entry(state, skill_id)
    if e is None or not _ready(state, skill_id) or not _can_pay(state, skill_id) or state.pending.skill_id > 0 or not _can_act(state):
        return False
    now = _now()
    if now - state.last_command_ms < _COMMAND_GUARD_MS:
        return False
    slot, _ = e
    try:
        SkillBar.HeroUseSkill(int(target_id), int(slot), int(state.hero_index))
    except Exception as exc:
        _log("HERO_BIP_CAST_ERROR", hero_index=state.hero_index, hero_id=state.hero_id,
             skill_id=skill_id, purpose=purpose, error=type(exc).__name__)
        return False
    state.pending = _Pending(skill_id=skill_id, slot=slot, target_id=int(target_id), started_ms=now,
                             start_recharge=max(0, _recharge(state, skill_id)), purpose=purpose)
    state.last_command_ms = now
    _log("HERO_BIP_CAST_REQUESTED", hero_index=state.hero_index, hero_id=state.hero_id,
         skill_id=skill_id, target_id=target_id, purpose=purpose)
    return True


def _try_heal(state: _State, skill_id: int, threshold: float, purpose: str, *, spirit_required: bool = False) -> bool:
    if not _ready(state, skill_id):
        return False
    if spirit_required and not _healthy_spirit_in_earshot(state):
        return False
    target = _lowest_ally(state, threshold)
    return bool(target > 0 and _request(state, skill_id, target, purpose))


def _run_state(state: _State, now: int) -> None:
    if now - state.last_scan_ms < _SCAN_MS:
        return
    state.last_scan_ms = now
    if not _ensure_locked(state, now) or _process_pending(state, now) or not _can_act(state):
        return
    combat_ms = _combat_clock(state, now)

    # Emergency triage first.
    if _try_heal(state, SPIRIT_TRANSFER_ID, 0.30, "emergency_spirit_transfer", spirit_required=True):
        return
    if _try_heal(state, SPIRIT_LIGHT_ID, 0.32, "emergency_spirit_light", spirit_required=True):
        return
    if _try_heal(state, MBAS_ID, 0.42, "emergency_mbas"):
        return

    # Keep the BiP caster funded enough to continue supporting the team.
    if _ready(state, SOLS_ID) and _energy_ratio(state.hero_id) < 0.32:
        target = _low_enemy_for_sols(state)
        if target and _request(state, SOLS_ID, target, "critical_sols"):
            return

    # Adaptive proactive BiP, same 75/80/85 concept as the player account.
    if _ready(state, BIP_ID) and now - state.last_bip_ms >= _BIP_THROTTLE_MS and _bip_safe(state):
        threshold, mode, low80, mn, avg = _bip_threshold(state, combat_ms)
        target, energy_before = _bip_target(state, threshold, now)
        if target > 0:
            post_buff_target_lock(target, key_id=BLOOD_ENERGY_BUFF_LOCK_KEY, skill_id=BIP_ID)
            if _request(state, BIP_ID, target, f"adaptive_bip_{mode}"):
                state.last_bip_ms = now
                state.bip_target_until[target] = now + _BIP_TARGET_LEASE_MS
                _log("HERO_BIP_ENERGY_CAST", hero_index=state.hero_index, hero_id=state.hero_id,
                     target_id=target, target_energy_pct=round(energy_before, 3),
                     threshold=round(threshold, 2), bip_mode=mode, combat_ms=combat_ms,
                     party_below_80=low80, party_min_energy=round(mn, 3), party_avg_energy=round(avg, 3),
                     target_lease_ms=_BIP_TARGET_LEASE_MS)
                return

    # Proactive sustain: do not wait for crisis.
    if _try_heal(state, MBAS_ID, 0.75, "sustain_mbas"):
        return
    if _try_heal(state, SPIRIT_LIGHT_ID, 0.72, "sustain_spirit_light", spirit_required=True):
        return

    if _ready(state, RECUPERATION_ID) and not _spirit_skill_exists(state, RECUPERATION_ID):
        if _damaged_count(state, 0.80) >= 4 or _degen_count(state) >= 2:
            if _request(state, RECUPERATION_ID, state.hero_id, "party_recuperation"):
                return

    if _ready(state, LIFE_ID) and not _spirit_skill_exists(state, LIFE_ID):
        try:
            close = bool(Routines.Agents.GetNearestEnemy(Range.Earshot.value))
        except Exception:
            close = False
        if close or _damaged_count(state, 0.90) >= 2:
            if _request(state, LIFE_ID, state.hero_id, "life_spirit_prep"):
                return

    if _ready(state, ENFEEBLING_BLOOD_ID):
        target = _enfeebling_target(state)
        if target and _request(state, ENFEEBLING_BLOOD_ID, target, "physical_packet_weakness"):
            return

    if _ready(state, SOLS_ID) and _energy_ratio(state.hero_id) < 0.62:
        target = _low_enemy_for_sols(state)
        if target and _request(state, SOLS_ID, target, "normal_sols"):
            return

    # Preventive top-up after team utility.
    _try_heal(state, MBAS_ID, 0.85, "preventive_mbas")


def _restore_all() -> None:
    for state in list(_STATES.values()):
        _restore(state)
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
        _log("HERO_BIP_CONTROLLER_ACTIVE")
    states = _reconcile()
    if not states:
        if not _NO_MATCH_LOGGED:
            _NO_MATCH_LOGGED = True
            _log("HERO_BIP_NO_MATCH")
        return
    _NO_MATCH_LOGGED = False
    now = _now()
    for state in states:
        try:
            _run_state(state, now)
        except Exception as exc:
            _log("HERO_BIP_CONTROLLER_ERROR", hero_index=state.hero_index, hero_id=state.hero_id,
                 error=type(exc).__name__, detail=str(exc)[:180])
