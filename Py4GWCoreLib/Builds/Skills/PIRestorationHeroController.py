# EXP36 Stage 12 / E19 phase 2; legacy regression marker: exp36_stage5_e19_observation_only
"""Dedicated controller for the PI / Restoration Mesmer hero.

Exact supported bar (user build):
    Psychic Instability / Power Drain / Cry of Frustration / Ghostmirror Light /
    Spirit Light / Life / Mend Body and Soul / Complicate

Priority:
- emergency healing
- critical Power Drain if energy is too low to fund PI
- one coordinated PI per enemy packet
- normal Restoration sustain window
- Cry of Frustration on real multi-cast packets
- Power Drain for energy supply
- dangerous Complicate fallback without double-interrupts
- Life preparation and ordinary shutdown while PI is recharging

PI and Complicate coordinate with the player's PI Mesmer through shared-memory
leases, so the two Mesmers do not spend the same elite/disable window together.
PI also publishes the cross-account knockdown packet consumed by the Fire hero
for PI -> Bed of Coals.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from Py4GWCoreLib import Agent, AgentArray, GLOBAL_CACHE, Map, Party, Profession, Range, Routines, Skill, SkillBar, Utils
from Py4GWCoreLib.Builds.Skills.BurningComboCoordination import publish_pi_knockdown_packet
from Py4GWCoreLib.Builds.Skills.DangerInterruptClaim import (
    get_casting_skill_id,
    is_dangerous_cast,
    opportunistic_interrupt_is_feasible,
    post_interrupt_claim,
    rank_opportunistic_interrupt_options,
    release_interrupt_claim,
)
from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import is_support_or_caster
from Py4GWCoreLib.Builds.Skills.PIMesmerCoordination import (
    pi_packet_reserved,
    release_complicate_skill,
    release_cry_packet,
    release_pi_packet,
    reserve_complicate_skill,
    reserve_cry_packet,
    reserve_pi_packet,
)

PSYCHIC_INSTABILITY_ID = int(Skill.GetID("Psychic_Instability") or 0)
POWER_DRAIN_ID = int(Skill.GetID("Power_Drain") or 0)
CRY_OF_FRUSTRATION_ID = int(Skill.GetID("Cry_of_Frustration") or 0)
GHOSTMIRROR_LIGHT_ID = int(Skill.GetID("Ghostmirror_Light") or 0)
SPIRIT_LIGHT_ID = int(Skill.GetID("Spirit_Light") or 0)
LIFE_ID = int(Skill.GetID("Life") or 0)
MEND_BODY_AND_SOUL_ID = int(Skill.GetID("Mend_Body_and_Soul") or 0)
COMPLICATE_ID = int(Skill.GetID("Complicate") or 0)

POWER_DRAIN_NORMAL_THRESHOLD = 0.70
POWER_DRAIN_CRITICAL_THRESHOLD = 0.30

_REQUIRED_IDS = frozenset(
    sid for sid in (
        PSYCHIC_INSTABILITY_ID, POWER_DRAIN_ID, CRY_OF_FRUSTRATION_ID,
        GHOSTMIRROR_LIGHT_ID, SPIRIT_LIGHT_ID, LIFE_ID,
        MEND_BODY_AND_SOUL_ID, COMPLICATE_ID,
    ) if sid > 0
)
_MANAGED_IDS = _REQUIRED_IDS

_SCAN_INTERVAL_MS = 80
_LOCK_REFRESH_MS = 2400
_COMMAND_TIMEOUT_MS = 3000
# Healing commands must either begin promptly or be reconsidered. Waiting the
# full generic 3s timeout can leave the support hero visibly idle while an ally
# continues to drop. Offensive/control casts keep the original timeout.
_HEAL_COMMAND_TIMEOUT_MS = 1100
_FAILED_CAST_RETRY_GUARD_MS = 450
# Party/hero snapshots can briefly omit a hero for one scan. Keep ownership
# sticky for a short grace period so native AI is never re-enabled mid-heal.
_MATCH_GRACE_MS = 1600
_PI_KNOCKDOWN_CONFIRM_MS = 1700
_COMMAND_GUARD_MS = 150
_CAST_RANGE = float(Range.Spellcast.value)
_NEARBY = float(Range.Nearby.value)
_ADJACENT = float(Range.Adjacent.value)
_EARSHOT = float(Range.Earshot.value)


@dataclass(slots=True)
class _PendingCommand:
    skill_id: int = 0
    slot: int = 0
    target_id: int = 0
    started_ms: int = 0
    start_recharge: int = 0
    purpose: str = ""
    enemy_casting_skill_id: int = 0
    pi_packet_members: tuple[int, ...] = ()
    pi_publish_roj_burst: bool = True
    complicate_enemy_skill_id: int = 0


@dataclass(slots=True)
class _ControllerState:
    hero_index: int = 0
    hero_bar_index: int = 0
    hero_agent_id: int = 0
    managed_slots: tuple[int, ...] = ()
    locked_slots: set[int] = field(default_factory=set)
    last_lock_refresh_ms: int = 0
    last_scan_ms: int = 0
    last_command_ms: int = 0
    pending: _PendingCommand = field(default_factory=_PendingCommand)
    # EXP6: PI knockdown observation is non-blocking once activation starts.
    pi_observation: _PendingCommand = field(default_factory=_PendingCommand)
    matched_logged: bool = False
    lock_failed_logged: bool = False
    last_seen_ms: int = 0
    last_failed_skill_id: int = 0
    last_failed_target_id: int = 0
    last_failed_ms: int = 0
    health_samples: dict[int, tuple[float, int]] = field(default_factory=dict)


_STATES: dict[int, _ControllerState] = {}
_DISCOVERED_HEROES: set[int] = set()
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


def _skill_name(skill_id: int) -> str:
    try:
        return str(Skill.GetName(int(skill_id)) or int(skill_id))
    except Exception:
        return str(int(skill_id or 0))


def _hero_bar(index: int):
    try:
        return list(SkillBar.GetHeroSkillbar(int(index)) or [])
    except Exception:
        return []


def _bar_entries(index: int) -> list[tuple[int, int, object]]:
    rows: list[tuple[int, int, object]] = []
    for slot, data in enumerate(_hero_bar(index), start=1):
        try:
            sid = int(getattr(getattr(data, "id", None), "id", 0) or 0)
        except Exception:
            sid = 0
        rows.append((int(slot), sid, data))
    return rows


def _bar_ids(index: int) -> set[int]:
    return {sid for _, sid, _ in _bar_entries(index) if sid > 0}


def _primary_is_mesmer(hero_id: int) -> bool:
    try:
        primary, _secondary = Agent.GetProfessions(int(hero_id))
        p = int(getattr(primary, "value", primary) or 0)
        return p == int(getattr(Profession.Mesmer, "value", Profession.Mesmer))
    except Exception:
        # The bar identity is already strong; fail open if profession metadata
        # is briefly unavailable during map load.
        return True


def _matching_heroes() -> list[tuple[int, int, int]]:
    """Match the exact PI/Resto hero once, then keep it sticky for the map.

    Hero skillbar reads can transiently return an incomplete snapshot while a
    command is being accepted. Requiring all eight IDs on every 80 ms scan made
    the controller briefly restore native AI and discard pending casts. The RoJ
    controller already uses sticky discovery; use the same proven pattern here.
    """
    try:
        heroes = list(Party.GetHeroes() or [])
    except Exception:
        heroes = []
    result: list[tuple[int, int, int]] = []
    for hero_position, hero in enumerate(heroes, start=1):
        hero_id = int(getattr(hero, "agent_id", 0) or 0)
        if hero_id <= 0 or not _primary_is_mesmer(hero_id):
            continue
        ids = _bar_ids(hero_position)
        if _REQUIRED_IDS and _REQUIRED_IDS.issubset(ids):
            _DISCOVERED_HEROES.add(hero_id)
        if hero_id in _DISCOVERED_HEROES:
            result.append((hero_position, hero_position, hero_id))
    return result


def _managed_slots(bar_index: int) -> tuple[int, ...]:
    # Once the exact build has been discovered, own all eight slots just like
    # the hardened RoJ/Keystone controllers. This prevents a one-frame bar read
    # glitch from handing a support skill back to native hero AI.
    return tuple(range(1, 9))


def _restore_native_ai(state: _ControllerState) -> None:
    restored = tuple(sorted(state.locked_slots))
    for slot in restored:
        try:
            Party.Heroes.SetSkillAIEnabled(int(state.hero_agent_id), int(slot), True)
        except Exception:
            pass
    if restored:
        _log("HERO_PI_RESTO_NATIVE_AI_RESTORED", hero_index=state.hero_index,
             hero_id=state.hero_agent_id, slots=",".join(map(str, restored)))
    state.locked_slots.clear()


def _reconcile_states() -> list[_ControllerState]:
    now_ms = _now_ms()
    matches = _matching_heroes()
    live = {hero_id for _, _, hero_id in matches}
    for hero_id in list(_STATES):
        if hero_id in live:
            continue
        state = _STATES[hero_id]
        # A one-frame Party.GetHeroes()/skillbar glitch must not hand all eight
        # support skills back to native AI, especially with a heal pending.
        grace_ms = max(int(_MATCH_GRACE_MS), int(_HEAL_COMMAND_TIMEOUT_MS) if state.pending.skill_id in {SPIRIT_LIGHT_ID, MEND_BODY_AND_SOUL_ID, GHOSTMIRROR_LIGHT_ID} else 0)
        if state.last_seen_ms > 0 and now_ms - state.last_seen_ms <= grace_ms:
            continue
        _restore_native_ai(_STATES.pop(hero_id))
    ordered: list[_ControllerState] = []
    for hero_index, bar_index, hero_id in matches:
        slots = _managed_slots(bar_index)
        state = _STATES.get(hero_id)
        if state is None:
            state = _ControllerState(
                hero_index=hero_index, hero_bar_index=bar_index,
                hero_agent_id=hero_id, managed_slots=slots, last_seen_ms=now_ms,
            )
            _STATES[hero_id] = state
        else:
            state.hero_index = hero_index
            state.hero_bar_index = bar_index
            state.managed_slots = slots
            state.last_seen_ms = now_ms
        if not state.matched_logged:
            state.matched_logged = True
            ids_ordered = [int(sid) for sid in _bar_ids(int(bar_index))]
            _log("HERO_PI_RESTO_MATCHED", hero_index=hero_index, hero_id=hero_id,
                 slots=",".join(map(str, slots)), mapping=f"read{bar_index}->cast{hero_index}")
            _log(
                "HEROAI_CONTROLLER_MATCH_DIAG",
                owner_kind="native_hero", owner_id=int(hero_id), hero_index=int(hero_index),
                intended_role="pi_resto", selected_controller="PIRestorationHeroController",
                matched=True, reject_reason="none",
                bar_skill_ids=",".join(map(str, ids_ordered)),
                required_skill_ids=",".join(map(str, sorted(_REQUIRED_IDS))),
                policy="exp36_stage12_e19_phase2_controller_plus_session_observation",
            )
        ordered.append(state)
    return ordered


def _ensure_locked(state: _ControllerState, now_ms: int) -> bool:
    desired = set(state.managed_slots)
    was_fully_locked = state.locked_slots == desired
    if was_fully_locked and now_ms - state.last_lock_refresh_ms < _LOCK_REFRESH_MS:
        return True
    ok = True
    for slot in state.managed_slots:
        try:
            Party.Heroes.SetSkillAIEnabled(int(state.hero_agent_id), int(slot), False)
            state.locked_slots.add(int(slot))
        except Exception:
            ok = False
    state.last_lock_refresh_ms = now_ms
    if ok:
        state.lock_failed_logged = False
        if not was_fully_locked:
            _log("HERO_PI_RESTO_AI_LOCKED", hero_index=state.hero_index,
                 hero_id=state.hero_agent_id, slots=",".join(map(str, state.managed_slots)))
    elif not state.lock_failed_logged:
        state.lock_failed_logged = True
        _log("HERO_PI_RESTO_AI_LOCK_FAILED", hero_index=state.hero_index, hero_id=state.hero_agent_id)
    return ok


def _entry(state: _ControllerState, skill_id: int) -> tuple[int, object] | None:
    for slot, sid, data in _bar_entries(state.hero_bar_index):
        if sid == int(skill_id):
            return slot, data
    return None


def _recharge(state: _ControllerState, skill_id: int) -> int:
    entry = _entry(state, skill_id)
    if entry is None:
        return -1
    try:
        return int(getattr(entry[1], "recharge", 0) or 0)
    except Exception:
        return 0


def _ready(state: _ControllerState, skill_id: int) -> bool:
    return skill_id > 0 and _entry(state, skill_id) is not None and _recharge(state, skill_id) == 0


def _energy_current(state: _ControllerState) -> int:
    try:
        return int(max(0.0, float(Agent.GetEnergy(state.hero_agent_id) or 0.0)) *
                   max(0, int(Agent.GetMaxEnergy(state.hero_agent_id) or 0)))
    except Exception:
        return 0


def _energy_ratio(state: _ControllerState) -> float:
    try:
        return max(0.0, min(1.0, float(Agent.GetEnergy(state.hero_agent_id) or 0.0)))
    except Exception:
        return 0.0


def _skill_energy_cost(skill_id: int) -> int:
    try:
        return max(0, int(Skill.Data.GetEnergyCost(int(skill_id)) or 0))
    except Exception:
        return 0


def _can_pay(state: _ControllerState, skill_id: int) -> bool:
    return _energy_current(state) >= _skill_energy_cost(skill_id)


def _can_pay_with_pi_reserve(state: _ControllerState, skill_id: int) -> bool:
    required = _skill_energy_cost(skill_id)
    if int(skill_id) != int(PSYCHIC_INSTABILITY_ID) and _ready(state, PSYCHIC_INSTABILITY_ID):
        required += _skill_energy_cost(PSYCHIC_INSTABILITY_ID)
    return _energy_current(state) >= required


def _hero_can_cast(state: _ControllerState) -> bool:
    try:
        return not (
            Agent.IsDead(state.hero_agent_id)
            or Agent.IsKnockedDown(state.hero_agent_id)
            or Agent.IsCasting(state.hero_agent_id)
            or Agent.IsMoving(state.hero_agent_id)
        )
    except Exception:
        return False


def _distance(a_id: int, b_id: int) -> float:
    try:
        return float(Utils.Distance(Agent.GetXY(int(a_id)), Agent.GetXY(int(b_id))))
    except Exception:
        return 999999.0


def _health(agent_id: int) -> float:
    try:
        return max(0.0, min(1.0, float(Routines.Checks.Agents.GetHealth(int(agent_id)))))
    except Exception:
        try:
            return max(0.0, min(1.0, float(Agent.GetHealth(int(agent_id)))))
        except Exception:
            return 1.0


def _alive(agent_id: int) -> bool:
    try:
        return bool(Routines.Checks.Agents.IsAlive(int(agent_id)))
    except Exception:
        try:
            return bool(Agent.IsAlive(int(agent_id)))
        except Exception:
            return False


def _conditioned(agent_id: int) -> bool:
    try:
        return bool(Routines.Checks.Agents.IsConditioned(int(agent_id)))
    except Exception:
        try:
            return bool(Agent.IsConditioned(int(agent_id)))
        except Exception:
            return False


def _weapon_spelled(agent_id: int) -> bool:
    try:
        return bool(Agent.IsWeaponSpelled(int(agent_id)))
    except Exception:
        return False


def _party_agent_ids() -> list[int]:
    result: set[int] = set()
    try:
        for player in Party.GetPlayers() or []:
            login = int(getattr(player, "login_number", 0) or 0)
            aid = int(Party.Players.GetAgentIDByLoginNumber(login) or 0)
            if aid > 0:
                result.add(aid)
    except Exception:
        pass
    for getter_name in ("GetHeroes", "GetHenchmen"):
        try:
            for member in getattr(Party, getter_name)() or []:
                aid = int(getattr(member, "agent_id", 0) or 0)
                if aid > 0:
                    result.add(aid)
        except Exception:
            continue
    return sorted(result)


def _healable_allies(state: _ControllerState, *, other_only: bool = False) -> list[int]:
    result: list[int] = []
    for aid in _party_agent_ids():
        if aid <= 0 or (other_only and aid == state.hero_agent_id):
            continue
        try:
            if not _alive(aid) or Agent.IsMinion(aid):
                continue
        except Exception:
            if not _alive(aid):
                continue
        if _distance(state.hero_agent_id, aid) > _CAST_RANGE:
            continue
        result.append(aid)
    if not other_only and state.hero_agent_id not in result and _alive(state.hero_agent_id):
        result.append(state.hero_agent_id)
    result.sort(key=lambda aid: (_health(aid), aid))
    return result


def _lowest_ally(state: _ControllerState, threshold: float, *, other_only: bool = False,
                 require_weapon: bool = False, avoid_weapon: bool = False,
                 prefer_conditioned: bool = False) -> int:
    candidates = []
    for aid in _healable_allies(state, other_only=other_only):
        hp = _health(aid)
        if hp > float(threshold):
            continue
        has_weapon = _weapon_spelled(aid)
        if require_weapon and not has_weapon:
            continue
        if avoid_weapon and has_weapon:
            continue
        candidates.append((0 if (prefer_conditioned and _conditioned(aid)) else 1, hp, aid))
    candidates.sort()
    return int(candidates[0][2]) if candidates else 0


def _damaged_count(state: _ControllerState, threshold: float) -> int:
    return sum(1 for aid in _healable_allies(state) if _health(aid) <= float(threshold))


def _spirit_in_earshot(state: _ControllerState) -> bool:
    try:
        for spirit_id in AgentArray.GetSpiritPetArray() or []:
            spirit_id = int(spirit_id or 0)
            if spirit_id > 0 and Agent.IsValid(spirit_id) and Agent.IsAlive(spirit_id):
                if _distance(state.hero_agent_id, spirit_id) <= _EARSHOT:
                    return True
    except Exception:
        pass
    return False


def _life_spirit_exists(state: _ControllerState) -> bool:
    """Detect this team's live Life spirit in earshot of the PI/Resto hero."""
    if LIFE_ID <= 0:
        return False
    try:
        from Py4GWCoreLib import SpiritModelID
        from Py4GWCoreLib.enums import SPIRIT_BUFF_MAP
        for spirit_id in AgentArray.GetSpiritPetArray() or []:
            spirit_id = int(spirit_id or 0)
            if spirit_id <= 0 or not Agent.IsValid(spirit_id) or not Agent.IsAlive(spirit_id):
                continue
            if _distance(state.hero_agent_id, spirit_id) > _EARSHOT:
                continue
            model_value = int(Agent.GetPlayerNumber(spirit_id) or 0)
            if model_value not in SpiritModelID._value2member_map_:
                continue
            if int(SPIRIT_BUFF_MAP.get(SpiritModelID(model_value), 0) or 0) == int(LIFE_ID):
                return True
    except Exception:
        pass
    return False


def _valid_enemy(state: _ControllerState, enemy_id: int) -> bool:
    try:
        return bool(
            int(enemy_id or 0) > 0 and Agent.IsValid(int(enemy_id)) and Agent.IsAlive(int(enemy_id))
            and _distance(state.hero_agent_id, int(enemy_id)) <= _CAST_RANGE
        )
    except Exception:
        return False


def _cluster_anchor(state: _ControllerState) -> int:
    try:
        from Py4GWCoreLib.Builds.Skills import HeroClusterCoordinator
        return int(HeroClusterCoordinator.get_shared_cluster_anchor(
            origin_agent_id=state.hero_agent_id,
            range_value=_CAST_RANGE,
            minimum_enemies=2,
        ) or 0)
    except Exception:
        return 0


def _cluster_members(state: _ControllerState, anchor_id: int, radius: float = _ADJACENT) -> list[int]:
    try:
        from Py4GWCoreLib.Builds.Skills import HeroClusterCoordinator
        return list(HeroClusterCoordinator.cluster_members(
            int(anchor_id), origin_agent_id=state.hero_agent_id,
            radius=float(radius), range_value=_CAST_RANGE,
        ) or [])
    except Exception:
        return [int(anchor_id)] if _valid_enemy(state, int(anchor_id)) else []


def _enemy_prof_score(enemy_id: int) -> int:
    # Lower is better: caster/support targets produce the highest PI value.
    try:
        primary, _ = Agent.GetProfessions(int(enemy_id))
        p = int(getattr(primary, "value", primary) or 0)
        caster = {
            int(getattr(Profession.Monk, "value", Profession.Monk)),
            int(getattr(Profession.Mesmer, "value", Profession.Mesmer)),
            int(getattr(Profession.Necromancer, "value", Profession.Necromancer)),
            int(getattr(Profession.Elementalist, "value", Profession.Elementalist)),
            int(getattr(Profession.Ritualist, "value", Profession.Ritualist)),
        }
        return 0 if p in caster else 1
    except Exception:
        return 1


def _is_spell_or_chant(skill_id: int) -> bool:
    if int(skill_id or 0) <= 0:
        return False
    try:
        flags = GLOBAL_CACHE.Skill.Flags
        return bool(
            flags.IsSpell(skill_id) or flags.IsChant(skill_id) or flags.IsHex(skill_id)
            or flags.IsEnchantment(skill_id) or flags.IsWeaponSpell(skill_id)
            or flags.IsItemSpell(skill_id) or flags.IsWell(skill_id)
            or flags.IsWard(skill_id) or flags.IsGlyph(skill_id)
        )
    except Exception:
        return False


def _pending_confirmed(state: _ControllerState, pending: _PendingCommand) -> bool:
    try:
        if Agent.IsCasting(state.hero_agent_id) and int(Agent.GetCastingSkillID(state.hero_agent_id) or 0) == pending.skill_id:
            return True
    except Exception:
        pass
    entry = _entry(state, pending.skill_id)
    if entry is None:
        return True
    return _recharge(state, pending.skill_id) > pending.start_recharge


def _pi_knockdown_targets(pending: _PendingCommand) -> list[int]:
    knocked: list[int] = []
    members = pending.pi_packet_members or ((pending.target_id,) if pending.target_id > 0 else ())
    for enemy_id in members:
        enemy_id = int(enemy_id or 0)
        if enemy_id <= 0:
            continue
        try:
            if Agent.IsValid(enemy_id) and Agent.IsAlive(enemy_id) and Agent.IsKnockedDown(enemy_id):
                knocked.append(enemy_id)
        except Exception:
            continue
    return knocked


def _process_pi_observation(state: _ControllerState, now_ms: int) -> None:
    """Observe confirmed PI knockdown without owning the hero's action queue."""
    p = state.pi_observation
    if p.skill_id != int(PSYCHIC_INSTABILITY_ID):
        return
    knocked = _pi_knockdown_targets(p)
    if knocked:
        _log("HERO_PI_RESTO_CAST_CONFIRMED", hero_index=state.hero_index,
             hero_id=state.hero_agent_id, skill_id=p.skill_id, skill=_skill_name(p.skill_id),
             purpose=p.purpose, target_id=p.target_id,
             knocked_ids=",".join(map(str, knocked)), confirmation="actual_knockdown_nonblocking")
        if bool(p.pi_publish_roj_burst):
            posted = int(publish_pi_knockdown_packet(p.pi_packet_members, anchor_id=int(p.target_id)) or 0)
            _log("HERO_PI_RESTO_PI_PACKET_PUBLISHED", hero_index=state.hero_index,
                 hero_id=state.hero_agent_id, target_id=int(p.target_id),
                 enemy_skill_id=int(p.enemy_casting_skill_id or 0), packet_size=len(p.pi_packet_members),
                 leases_posted=int(posted), knocked_ids=",".join(map(str, knocked)),
                 policy="actual_hero_pi_knockdown_confirmed_nonblocking")
        state.pi_observation = _PendingCommand()
        return
    if now_ms - int(p.started_ms or now_ms) > int(_PI_KNOCKDOWN_CONFIRM_MS):
        _log("HERO_PI_RESTO_PI_NO_KNOCKDOWN", hero_index=state.hero_index,
             hero_id=state.hero_agent_id, target_id=int(p.target_id),
             enemy_skill_id=int(p.enemy_casting_skill_id or 0), packet_size=len(p.pi_packet_members),
             policy="nonblocking_observation_timeout")
        if p.enemy_casting_skill_id > 0:
            release_interrupt_claim(p.target_id, p.enemy_casting_skill_id, reason="hero_pi_no_confirmed_knockdown")
        release_pi_packet(p.pi_packet_members, reason="hero_pi_no_confirmed_knockdown")
        state.pi_observation = _PendingCommand()


def _process_pending(state: _ControllerState, now_ms: int) -> bool:
    p = state.pending
    if p.skill_id <= 0:
        return False
    if _pending_confirmed(state, p):
        # PI is special: observing the hero begin casting is not proof that the
        # enemy packet was actually knocked down. Keep the pending transaction
        # alive briefly and publish only after local KD state is visible.
        if int(p.skill_id) == int(PSYCHIC_INSTABILITY_ID) and p.pi_packet_members:
            knocked = _pi_knockdown_targets(p)
            if not knocked:
                # EXP6: activation is real, but observing the network/local KD
                # transition must not freeze the hero's healing/offense for up to
                # 1.7s. Move confirmation to a telemetry-only side transaction.
                state.pi_observation = p
                state.pending = _PendingCommand()
                _log(
                    "HERO_PI_RESTO_PI_OBSERVE_NONBLOCKING",
                    hero_index=state.hero_index, hero_id=state.hero_agent_id,
                    target_id=int(p.target_id), packet_size=len(p.pi_packet_members),
                    policy="activation_confirmed_release_action_queue",
                )
                return False

            _log("HERO_PI_RESTO_CAST_CONFIRMED", hero_index=state.hero_index,
                 hero_id=state.hero_agent_id, skill_id=p.skill_id, skill=_skill_name(p.skill_id),
                 purpose=p.purpose, target_id=p.target_id,
                 knocked_ids=",".join(map(str, knocked)),
                 confirmation="actual_knockdown")
            posted = 0
            if bool(p.pi_publish_roj_burst):
                posted = int(publish_pi_knockdown_packet(
                    p.pi_packet_members, anchor_id=int(p.target_id)
                ) or 0)
                _log(
                    "HERO_PI_RESTO_PI_PACKET_PUBLISHED",
                    hero_index=state.hero_index, hero_id=state.hero_agent_id,
                    target_id=int(p.target_id), enemy_skill_id=int(p.enemy_casting_skill_id or 0),
                    packet_size=len(p.pi_packet_members), leases_posted=int(posted),
                    knocked_ids=",".join(map(str, knocked)),
                    policy="actual_hero_pi_knockdown_confirmed_for_roj_burst",
                )
            else:
                _log(
                    "HERO_PI_RESTO_DANGER_PEELOFF_KNOCKDOWN_CONFIRMED",
                    hero_index=state.hero_index, hero_id=state.hero_agent_id,
                    target_id=int(p.target_id), enemy_skill_id=int(p.enemy_casting_skill_id or 0),
                    packet_size=len(p.pi_packet_members),
                    knocked_ids=",".join(map(str, knocked)),
                    policy="safety_interrupt_only_keep_roj_on_authoritative_focus",
                )
            state.pending = _PendingCommand()
            return False

        _log("HERO_PI_RESTO_CAST_CONFIRMED", hero_index=state.hero_index,
             hero_id=state.hero_agent_id, skill_id=p.skill_id, skill=_skill_name(p.skill_id),
             purpose=p.purpose, target_id=p.target_id)
        state.pending = _PendingCommand()
        return False
    heal_ids = {SPIRIT_LIGHT_ID, MEND_BODY_AND_SOUL_ID, GHOSTMIRROR_LIGHT_ID}
    timeout_ms = int(_HEAL_COMMAND_TIMEOUT_MS if (p.skill_id in heal_ids and _small_party_support_mode()) else _COMMAND_TIMEOUT_MS)
    if now_ms - p.started_ms <= timeout_ms:
        return True
    _log("HERO_PI_RESTO_CAST_TIMEOUT", hero_index=state.hero_index,
         hero_id=state.hero_agent_id, skill_id=p.skill_id, skill=_skill_name(p.skill_id),
         purpose=p.purpose, target_id=p.target_id, timeout_ms=timeout_ms,
         fast_heal_recovery=bool(p.skill_id in heal_ids))
    state.last_failed_skill_id = int(p.skill_id)
    state.last_failed_target_id = int(p.target_id)
    state.last_failed_ms = int(now_ms)
    if p.enemy_casting_skill_id > 0:
        release_interrupt_claim(p.target_id, p.enemy_casting_skill_id, reason="hero_command_timeout")
    if p.pi_packet_members:
        release_pi_packet(p.pi_packet_members, reason="hero_pi_command_timeout")
    if p.complicate_enemy_skill_id > 0:
        release_complicate_skill(p.complicate_enemy_skill_id, reason="hero_complicate_command_timeout")
    state.pending = _PendingCommand()
    return False


def _request_cast(state: _ControllerState, skill_id: int, target_id: int, *, purpose: str,
                  enemy_casting_skill_id: int = 0,
                  pi_packet_members: tuple[int, ...] | list[int] = (),
                  pi_publish_roj_burst: bool = True,
                  complicate_enemy_skill_id: int = 0) -> bool:
    entry = _entry(state, skill_id)
    if entry is None or not _ready(state, skill_id) or not _can_pay(state, skill_id):
        return False
    if state.pending.skill_id > 0 or not _hero_can_cast(state):
        return False
    now_ms = _now_ms()
    if (
        int(state.last_failed_skill_id) == int(skill_id)
        and int(state.last_failed_target_id) == int(target_id)
        and now_ms - int(state.last_failed_ms or 0) < int(_FAILED_CAST_RETRY_GUARD_MS)
    ):
        # Let the next healing option try instead of immediately re-queuing the
        # exact command that just failed to start.
        return False
    if now_ms - state.last_command_ms < _COMMAND_GUARD_MS:
        return False
    slot, _ = entry
    try:
        SkillBar.HeroUseSkill(int(target_id), int(slot), int(state.hero_index))
    except Exception as exc:
        _log("HERO_PI_RESTO_CAST_ERROR", hero_index=state.hero_index,
             hero_id=state.hero_agent_id, skill_id=skill_id, purpose=purpose,
             error=type(exc).__name__)
        return False
    state.pending = _PendingCommand(
        skill_id=int(skill_id), slot=int(slot), target_id=int(target_id),
        started_ms=now_ms, start_recharge=max(0, _recharge(state, skill_id)),
        purpose=str(purpose), enemy_casting_skill_id=int(enemy_casting_skill_id or 0),
        pi_packet_members=tuple(sorted({int(x) for x in pi_packet_members if int(x or 0) > 0})),
        pi_publish_roj_burst=bool(pi_publish_roj_burst),
        complicate_enemy_skill_id=int(complicate_enemy_skill_id or 0),
    )
    state.last_command_ms = now_ms
    _log("HERO_PI_RESTO_CAST_REQUESTED", hero_index=state.hero_index,
         hero_id=state.hero_agent_id, skill_id=skill_id, skill=_skill_name(skill_id),
         purpose=purpose, target_id=int(target_id))
    return True


def _try_emergency_heals(state: _ControllerState) -> bool:
    # Spirit Light is the largest immediate rescue on this exact bar. Avoid its
    # sacrifice clause when the hero itself is already in danger and no spirit
    # is nearby.
    if _ready(state, SPIRIT_LIGHT_ID):
        if _spirit_in_earshot(state) or _health(state.hero_agent_id) >= 0.72:
            target = _lowest_ally(state, 0.38, prefer_conditioned=True)
            if target > 0 and _request_cast(state, SPIRIT_LIGHT_ID, target, purpose="emergency_spirit_light"):
                return True

    if _ready(state, MEND_BODY_AND_SOUL_ID):
        target = _lowest_ally(state, 0.44, prefer_conditioned=_spirit_in_earshot(state))
        if target > 0 and _request_cast(state, MEND_BODY_AND_SOUL_ID, target, purpose="emergency_mend_body_and_soul"):
            return True

    if _ready(state, GHOSTMIRROR_LIGHT_ID):
        target = _lowest_ally(state, 0.44, other_only=True, prefer_conditioned=True)
        if target > 0 and _request_cast(state, GHOSTMIRROR_LIGHT_ID, target, purpose="emergency_ghostmirror_light"):
            return True

    return False


def _pi_candidates(state: _ControllerState) -> tuple[int, list[int], list[tuple[int, int]]]:
    anchor = _cluster_anchor(state)
    members = _cluster_members(state, anchor, _ADJACENT) if anchor > 0 else []
    if not members:
        # A dangerous single caster is still worth PI in cleanup.
        members = [int(e) for e in AgentArray.GetEnemyArray() or [] if _valid_enemy(state, int(e))]
    casts: list[tuple[int, int]] = []
    for enemy_id in members:
        sid = int(get_casting_skill_id(int(enemy_id)) or 0)
        if sid > 0 and _valid_enemy(state, int(enemy_id)):
            try:
                if Agent.IsKnockedDown(int(enemy_id)):
                    continue
            except Exception:
                pass
            casts.append((int(enemy_id), sid))
    return int(anchor or 0), sorted(set(int(x) for x in members)), casts




def _pick_offfocus_priority_caster(
    state: _ControllerState,
    *,
    focus_members: list[int],
    interrupter_skill_id: int,
    spell_or_chant_only: bool = False,
    require_priority_role: bool = True,
) -> tuple[int, int]:
    """EXP21 hero parity with the player PI support targeting.

    The main Keystone/RoJ packet already receives packet-wide shutdown.  The
    PI/Resto hero therefore gives first refusal to a live healer/support or
    dangerous caster OUTSIDE that primary packet.  With two clusters this
    naturally picks the dangerous/support caster in the secondary packet.  If
    there is no useful external threat, callers fall back to the primary packet.
    """
    focus_set = {int(x) for x in (focus_members or []) if int(x or 0) > 0}
    outside: list[int] = []
    for enemy_id in AgentArray.GetEnemyArray() or []:
        enemy_id = int(enemy_id or 0)
        if enemy_id <= 0 or enemy_id in focus_set or not _valid_enemy(state, enemy_id):
            continue
        outside.append(enemy_id)

    ranked: list[tuple[int, int, int, float, int, int]] = []
    for enemy_id in outside:
        enemy_skill_id = int(get_casting_skill_id(enemy_id) or 0)
        if enemy_skill_id <= 0:
            continue
        if spell_or_chant_only and not _is_spell_or_chant(enemy_skill_id):
            continue
        priority_role = bool(is_dangerous_cast(enemy_id) or is_support_or_caster(enemy_id))
        if require_priority_role and not priority_role:
            continue
        if not opportunistic_interrupt_is_feasible(
            enemy_id, int(interrupter_skill_id), origin_agent_id=int(state.hero_agent_id)
        ):
            continue
        try:
            xy = Agent.GetXY(enemy_id)
            density = sum(
                1 for other in outside
                if _valid_enemy(state, other)
                and Utils.Distance(xy, Agent.GetXY(int(other))) <= _ADJACENT
            )
        except Exception:
            density = 1
        ranked.append((
            -int(is_dangerous_cast(enemy_id)),
            -int(is_support_or_caster(enemy_id)),
            -int(density),
            _distance(state.hero_agent_id, enemy_id),
            enemy_id,
            enemy_skill_id,
        ))
    ranked.sort()
    for _danger, _support, _density, _distance_v, enemy_id, enemy_skill_id in ranked:
        if int(get_casting_skill_id(enemy_id) or 0) != int(enemy_skill_id):
            continue
        return int(enemy_id), int(enemy_skill_id)
    return 0, 0

def _danger_packet_members(state: _ControllerState, target_id: int) -> list[int]:
    target_id = int(target_id or 0)
    if target_id <= 0:
        return []
    try:
        target_xy = Agent.GetXY(target_id)
        enemy_ids = AgentArray.GetEnemyArray() or []
        enemy_ids = AgentArray.Filter.ByDistance(enemy_ids, target_xy, float(_ADJACENT))
        out = [int(eid) for eid in enemy_ids if _valid_enemy(state, int(eid))]
    except Exception:
        out = [target_id]
    if target_id not in out and _valid_enemy(state, target_id):
        out.append(target_id)
    return sorted(set(out))


def _try_opportunistic_danger_interrupt(state: _ControllerState) -> bool:
    """Interrupt one live threat with the best skill that is ready right now.

    This path never holds a skill for a future cast.  It compares only live,
    affordable interrupts, posts one exact-cast claim, fires, and then the hero
    returns to its normal shared-focus controller on the next tick.
    """
    anchor = int(_cluster_anchor(state) or 0)
    focus_members = _cluster_members(state, anchor, _ADJACENT) if anchor > 0 else []
    cleanup_mode = bool(anchor > 0 and len(focus_members) <= 1)

    ready_ids: list[int] = []
    # EXP6: reserve PI for packet knockdown / single-target fallback. Generic
    # dangerous casts are handled by the normal interrupt kit.
    for skill_id in (COMPLICATE_ID, POWER_DRAIN_ID, CRY_OF_FRUSTRATION_ID):
        if int(skill_id or 0) <= 0:
            continue
        if not _ready(state, int(skill_id)) or not _can_pay(state, int(skill_id)):
            continue
        ready_ids.append(int(skill_id))
    if not ready_ids:
        return False

    opportunities = rank_opportunistic_interrupt_options(
        ready_ids,
        energy_ratio=float(_energy_ratio(state)),
        range_value=int(Range.Spellcast.value),
        origin_agent_id=int(state.hero_agent_id),
        cleanup_mode=bool(cleanup_mode),
        focus_ids=tuple(focus_members),
    )
    if not opportunities:
        return False

    focus_set = set(int(x) for x in focus_members)
    for score, target_id, enemy_skill_id, our_skill_id, mode in opportunities:
        target_id = int(target_id)
        enemy_skill_id = int(enemy_skill_id)
        our_skill_id = int(our_skill_id)
        if not _valid_enemy(state, target_id):
            continue
        if get_casting_skill_id(target_id) != enemy_skill_id:
            continue
        if not post_interrupt_claim(target_id, enemy_skill_id, our_skill_id):
            continue

        complicate_reserved = False
        if our_skill_id == int(COMPLICATE_ID):
            if not reserve_complicate_skill(enemy_skill_id):
                release_interrupt_claim(
                    target_id, enemy_skill_id, reason="hero_danger_peel_complicate_lease_lost"
                )
                continue
            complicate_reserved = True

        packet: list[int] = []
        publish_roj_burst = False
        if our_skill_id == int(PSYCHIC_INSTABILITY_ID):
            packet = _danger_packet_members(state, target_id) or [target_id]
            if pi_packet_reserved(packet) or not reserve_pi_packet(packet):
                release_interrupt_claim(
                    target_id, enemy_skill_id, reason="hero_danger_peel_pi_packet_owned"
                )
                continue
            publish_roj_burst = bool(len(focus_members) > 1 and target_id in focus_set)

        if not _request_cast(
            state, our_skill_id, target_id,
            purpose=f"opportunistic_{mode}_interrupt",
            enemy_casting_skill_id=enemy_skill_id,
            pi_packet_members=packet,
            pi_publish_roj_burst=bool(publish_roj_burst),
            complicate_enemy_skill_id=(enemy_skill_id if complicate_reserved else 0),
        ):
            release_interrupt_claim(
                target_id, enemy_skill_id, reason="hero_danger_peel_not_dispatched"
            )
            if complicate_reserved:
                release_complicate_skill(
                    enemy_skill_id, reason="hero_danger_peel_complicate_not_dispatched"
                )
            if packet:
                release_pi_packet(packet, reason="hero_danger_peel_pi_not_dispatched")
            continue

        _log(
            "HERO_PI_RESTO_OPPORTUNISTIC_DANGER_INTERRUPT",
            hero_index=state.hero_index, hero_id=state.hero_agent_id,
            target_id=target_id, enemy_skill_id=enemy_skill_id, our_skill_id=our_skill_id,
            mode=str(mode), score=round(float(score), 1),
            off_focus=bool(target_id not in focus_set), cleanup=bool(cleanup_mode),
            policy="ready_now_one_peel_then_return_shared_focus",
        )
        return True

    return False


def _try_psychic_instability(state: _ControllerState) -> bool:
    if not _ready(state, PSYCHIC_INSTABILITY_ID) or _energy_ratio(state) < 0.20:
        return False

    anchor = _cluster_anchor(state)
    members = _cluster_members(state, anchor, _ADJACENT) if anchor > 0 else []
    if not members:
        members = [int(e) for e in AgentArray.GetEnemyArray() or [] if _valid_enemy(state, int(e))]
    if not members:
        return False

    # EXP21: mirror the player PI account.  Keystone already owns the primary
    # ball, so first knock down an external healer/support or dangerous caster.
    # With two clusters this selects the relevant caster in cluster #2.
    off_target, off_skill = _pick_offfocus_priority_caster(
        state,
        focus_members=list(members),
        interrupter_skill_id=PSYCHIC_INSTABILITY_ID,
        require_priority_role=True,
    )
    if off_target > 0:
        packet = _danger_packet_members(state, off_target) or [off_target]
        if not pi_packet_reserved(packet) and post_interrupt_claim(off_target, off_skill, PSYCHIC_INSTABILITY_ID):
            if reserve_pi_packet(packet):
                if _request_cast(
                    state, PSYCHIC_INSTABILITY_ID, off_target,
                    purpose="pi_offpacket_priority_peel",
                    enemy_casting_skill_id=off_skill,
                    pi_packet_members=packet,
                    pi_publish_roj_burst=False,
                ):
                    _log(
                        "HERO_PI_RESTO_OFFPACKET_PI", hero_index=state.hero_index,
                        hero_id=state.hero_agent_id, target_id=off_target,
                        enemy_skill_id=off_skill, packet_size=len(packet),
                        policy="external_healer_support_danger_first",
                    )
                    return True
                release_pi_packet(packet, reason="hero_offpacket_pi_not_dispatched")
            release_interrupt_claim(off_target, off_skill, reason="hero_offpacket_pi_not_dispatched")

    # No external priority threat: use PI on the authoritative cluster / cleanup
    # exactly as before.
    casts: list[tuple[int, int]] = []
    for enemy_id in members:
        sid = int(get_casting_skill_id(int(enemy_id)) or 0)
        if sid > 0 and _valid_enemy(state, int(enemy_id)):
            casts.append((int(enemy_id), sid))
    if not casts or pi_packet_reserved(members):
        return False

    def packet_count(target_id: int) -> int:
        try:
            xy = Agent.GetXY(int(target_id))
            return sum(1 for eid in members if Utils.Distance(xy, Agent.GetXY(int(eid))) <= _ADJACENT)
        except Exception:
            return 1

    casts.sort(key=lambda item: (
        -packet_count(int(item[0])),
        -int(is_dangerous_cast(int(item[0]))),
        -int(is_support_or_caster(int(item[0]))),
        _enemy_prof_score(int(item[0])),
        _distance(state.hero_agent_id, int(item[0])),
        int(item[0]),
    ))
    for target_id, enemy_skill_id in casts:
        count = packet_count(target_id)
        if count < 2 and not (is_dangerous_cast(target_id) or is_support_or_caster(target_id)):
            continue
        if not opportunistic_interrupt_is_feasible(
            target_id, PSYCHIC_INSTABILITY_ID, origin_agent_id=int(state.hero_agent_id)
        ):
            continue
        if not post_interrupt_claim(target_id, enemy_skill_id, PSYCHIC_INSTABILITY_ID):
            continue
        if int(get_casting_skill_id(target_id) or 0) != int(enemy_skill_id):
            release_interrupt_claim(target_id, enemy_skill_id, reason="hero_cast_changed_before_pi")
            continue
        packet = [eid for eid in members if _valid_enemy(state, eid)] or [target_id]
        if not reserve_pi_packet(packet):
            release_interrupt_claim(target_id, enemy_skill_id, reason="hero_pi_packet_reservation_lost")
            continue
        if _request_cast(
            state, PSYCHIC_INSTABILITY_ID, target_id,
            purpose="pi_packet_shutdown", enemy_casting_skill_id=enemy_skill_id,
            pi_packet_members=packet,
        ):
            _log(
                "HERO_PI_RESTO_PI_PACKET_ARMED", hero_index=state.hero_index,
                hero_id=state.hero_agent_id, target_id=target_id,
                enemy_skill_id=enemy_skill_id, packet_size=len(packet),
                policy="offpacket_priority_then_primary_cluster",
            )
            return True
        release_interrupt_claim(target_id, enemy_skill_id, reason="hero_pi_not_dispatched")
        release_pi_packet(packet, reason="hero_pi_not_dispatched")
    return False

def _cluster_or_visible_enemies(state: _ControllerState, radius: float = _ADJACENT) -> tuple[int, list[int]]:
    anchor = _cluster_anchor(state)
    members = _cluster_members(state, anchor, radius) if anchor > 0 else []
    if not members:
        members = [int(e) for e in AgentArray.GetEnemyArray() or [] if _valid_enemy(state, int(e))]
    return int(anchor or 0), sorted(set(int(x) for x in members))


def _try_complicate(state: _ControllerState, *, dangerous_only: bool) -> bool:
    if not _ready(state, COMPLICATE_ID) or _energy_ratio(state) < 0.16:
        return False
    anchor, members = _cluster_or_visible_enemies(state, _ADJACENT)
    if not members:
        return False

    # Preserve Complicate when another PI Mesmer has already committed the
    # packet knockdown. Once that race lease expires, Complicate is free again.
    if pi_packet_reserved(members):
        return False

    candidates: list[tuple[int, int]] = []
    off_target, off_skill = _pick_offfocus_priority_caster(
        state, focus_members=list(members), interrupter_skill_id=COMPLICATE_ID,
        require_priority_role=True,
    )
    if off_target > 0:
        candidates.append((off_target, off_skill))
    for enemy_id in members:
        sid = int(get_casting_skill_id(enemy_id) or 0)
        if sid <= 0 or not _valid_enemy(state, enemy_id):
            continue
        try:
            if Agent.IsKnockedDown(enemy_id):
                continue
        except Exception:
            pass
        dangerous = bool(is_dangerous_cast(enemy_id))
        if dangerous_only and not dangerous:
            continue
        # Ordinary Complicate is only filler while PI is recharging, and only
        # on real caster/support professions rather than random melee skills.
        if not dangerous_only:
            if _ready(state, PSYCHIC_INSTABILITY_ID):
                continue
            if _enemy_prof_score(enemy_id) != 0:
                continue
        candidates.append((enemy_id, sid))

    candidates.sort(key=lambda item: (
        -int(is_dangerous_cast(item[0])),
        _enemy_prof_score(item[0]),
        _distance(state.hero_agent_id, item[0]),
        item[0],
    ))

    for target_id, enemy_skill_id in candidates:
        # Exact cast claim coordinates with every other team interrupt, including
        # the player PI Mesmer. It is used for ordinary casts as well.
        if not post_interrupt_claim(target_id, enemy_skill_id, COMPLICATE_ID):
            continue
        if get_casting_skill_id(target_id) != enemy_skill_id:
            release_interrupt_claim(target_id, enemy_skill_id, reason="hero_cast_changed_before_complicate")
            continue
        # Skill-level lease additionally prevents both PI Mesmers from using
        # Complicate on two nearby foes casting the same skill at once.
        if not reserve_complicate_skill(enemy_skill_id):
            release_interrupt_claim(target_id, enemy_skill_id, reason="hero_complicate_skill_reserved_elsewhere")
            continue
        if _request_cast(
            state, COMPLICATE_ID, target_id,
            purpose=("dangerous_complicate" if dangerous_only else "ordinary_support_complicate"),
            enemy_casting_skill_id=enemy_skill_id,
            complicate_enemy_skill_id=enemy_skill_id,
        ):
            return True
        release_interrupt_claim(target_id, enemy_skill_id, reason="hero_complicate_not_dispatched")
        release_complicate_skill(enemy_skill_id, reason="hero_complicate_not_dispatched")
    return False


def _casting_candidates(state: _ControllerState, members: list[int], *, spell_or_chant_only: bool = False) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for enemy_id in members:
        enemy_id = int(enemy_id or 0)
        if enemy_id <= 0 or not _valid_enemy(state, enemy_id):
            continue
        try:
            if Agent.IsKnockedDown(enemy_id):
                continue
        except Exception:
            pass
        enemy_skill_id = int(get_casting_skill_id(enemy_id) or 0)
        if enemy_skill_id <= 0:
            continue
        if spell_or_chant_only and not _is_spell_or_chant(enemy_skill_id):
            continue
        out.append((enemy_id, enemy_skill_id))
    return out


def _casting_count_near(target_id: int, candidates: list[tuple[int, int]]) -> int:
    try:
        target_xy = Agent.GetXY(int(target_id))
        return sum(
            1 for enemy_id, _sid in candidates
            if Utils.Distance(target_xy, Agent.GetXY(int(enemy_id))) <= _NEARBY
        )
    except Exception:
        return 1


def _try_power_drain(state: _ControllerState, *, critical_only: bool = False) -> bool:
    if not _ready(state, POWER_DRAIN_ID):
        return False
    threshold = POWER_DRAIN_CRITICAL_THRESHOLD if critical_only else POWER_DRAIN_NORMAL_THRESHOLD
    if _energy_ratio(state) > float(threshold):
        return False

    _anchor, members = _cluster_or_visible_enemies(state, _ADJACENT)
    if not members:
        return False

    off_target, off_skill = _pick_offfocus_priority_caster(
        state, focus_members=list(members), interrupter_skill_id=POWER_DRAIN_ID,
        spell_or_chant_only=True, require_priority_role=True,
    )
    candidates: list[tuple[int, int]] = []
    if off_target > 0:
        candidates.append((off_target, off_skill))
    # If another PI owns the primary packet, external peel remains allowed; only
    # suppress primary-packet candidates.
    if not pi_packet_reserved(members):
        candidates.extend(_casting_candidates(state, members, spell_or_chant_only=True))
    candidates.sort(key=lambda item: (
        -int(is_dangerous_cast(item[0])),
        _enemy_prof_score(item[0]),
        _distance(state.hero_agent_id, item[0]),
        item[0],
    ))
    for target_id, enemy_skill_id in candidates:
        if not post_interrupt_claim(target_id, enemy_skill_id, POWER_DRAIN_ID):
            continue
        if get_casting_skill_id(target_id) != int(enemy_skill_id):
            release_interrupt_claim(target_id, enemy_skill_id, reason="hero_cast_changed_before_power_drain")
            continue
        if _request_cast(
            state, POWER_DRAIN_ID, target_id,
            purpose=("critical_power_drain" if critical_only else "energy_supply_power_drain"),
            enemy_casting_skill_id=enemy_skill_id,
        ):
            _log(
                "HERO_PI_RESTO_POWER_DRAIN", hero_index=state.hero_index,
                hero_id=state.hero_agent_id, target_id=target_id,
                enemy_skill_id=enemy_skill_id, energy_ratio=round(_energy_ratio(state), 3),
                critical=bool(critical_only),
            )
            return True
        release_interrupt_claim(target_id, enemy_skill_id, reason="hero_power_drain_not_dispatched")
    return False


def _try_cry_of_frustration(state: _ControllerState, *, multi_only: bool) -> bool:
    if not _ready(state, CRY_OF_FRUSTRATION_ID):
        return False
    # Preserve enough energy for PI whenever the elite is currently available.
    if not _can_pay_with_pi_reserve(state, CRY_OF_FRUSTRATION_ID):
        return False

    _anchor, members = _cluster_or_visible_enemies(state, _ADJACENT)
    if not members:
        return False
    off_target, off_skill = _pick_offfocus_priority_caster(
        state, focus_members=list(members), interrupter_skill_id=CRY_OF_FRUSTRATION_ID,
        require_priority_role=True,
    )
    candidates: list[tuple[int, int]] = []
    if off_target > 0:
        candidates.append((off_target, off_skill))
    if not pi_packet_reserved(members):
        candidates.extend(_casting_candidates(state, members))
    if not candidates:
        return False

    ranked = sorted(
        candidates,
        key=lambda item: (
            -_casting_count_near(int(item[0]), candidates),
            -int(is_dangerous_cast(int(item[0]))),
            _enemy_prof_score(int(item[0])),
            _distance(state.hero_agent_id, int(item[0])),
            int(item[0]),
        ),
    )
    for target_id, enemy_skill_id in ranked:
        casting_count = _casting_count_near(int(target_id), candidates)
        off_focus = int(target_id) not in {int(x) for x in members}
        if multi_only and casting_count < 2 and not off_focus:
            continue
        if not multi_only and not (is_dangerous_cast(target_id) or is_support_or_caster(target_id)):
            continue
        cry_packet = (_danger_packet_members(state, target_id) or [target_id]) if off_focus else list(members)
        if not reserve_cry_packet(cry_packet):
            continue
        if not post_interrupt_claim(target_id, enemy_skill_id, CRY_OF_FRUSTRATION_ID):
            release_cry_packet(cry_packet, reason="hero_cry_claim_lost")
            continue
        if get_casting_skill_id(target_id) != int(enemy_skill_id):
            release_interrupt_claim(target_id, enemy_skill_id, reason="hero_cast_changed_before_cry")
            continue
        if _request_cast(
            state, CRY_OF_FRUSTRATION_ID, target_id,
            purpose=("multi_cast_cry" if casting_count >= 2 else "dangerous_single_cry_fallback"),
            enemy_casting_skill_id=enemy_skill_id,
        ):
            _log(
                "HERO_PI_RESTO_CRY_OF_FRUSTRATION", hero_index=state.hero_index,
                hero_id=state.hero_agent_id, target_id=target_id,
                enemy_skill_id=enemy_skill_id, casting_count=int(casting_count),
                multi_only=bool(multi_only),
            )
            return True
        release_interrupt_claim(target_id, enemy_skill_id, reason="hero_cry_not_dispatched")
        release_cry_packet(cry_packet, reason="hero_cry_not_fired")
    return False


def _try_life_prep(state: _ControllerState) -> bool:
    # Life is strongest as pre-loaded delayed party healing. Waiting until three
    # allies are already below 88% made it effectively never cast in the test.
    if not _ready(state, LIFE_ID) or _life_spirit_exists(state):
        return False
    _anchor, members = _cluster_or_visible_enemies(state, _ADJACENT)
    if not members:
        return False
    # Do not spend the elite's energy reserve just to pre-cast Life.
    if not _can_pay_with_pi_reserve(state, LIFE_ID):
        return False
    if _request_cast(state, LIFE_ID, state.hero_agent_id, purpose="combat_prep_life"):
        _log("HERO_PI_RESTO_LIFE_PREP", hero_index=state.hero_index, hero_id=state.hero_agent_id,
             enemy_count=len(members), pi_ready=bool(_ready(state, PSYCHIC_INSTABILITY_ID)))
        return True
    return False


def _small_party_support_mode() -> bool:
    try:
        party_size = int(Party.GetPartySize() or 0)
    except Exception:
        party_size = 0
    return 2 <= party_size <= 8


def _healing_pressure(state: _ControllerState, now_ms: int) -> tuple[bool, bool, float, int]:
    """Return (urgent, pressure, lowest_hp, damaged_count).

    Fixed HP thresholds alone react late to a fast spike. A lightweight rolling
    sample lets the support react early when HP is visibly falling, without
    turning every minor scratch into a heal. Samples are local and require no
    shared-memory work.
    """
    allies = _healable_allies(state)
    if not allies:
        return False, False, 1.0, 0
    lowest = 1.0
    damaged = 0
    rapid_drop = False
    live = set()
    for aid in allies:
        aid = int(aid)
        hp = float(_health(aid))
        live.add(aid)
        lowest = min(lowest, hp)
        if hp <= 0.74:
            damaged += 1
        previous = state.health_samples.get(aid)
        if previous is None:
            state.health_samples[aid] = (hp, int(now_ms))
            continue
        prev_hp, prev_ms = previous
        age = int(now_ms) - int(prev_ms)
        if 250 <= age <= 1100 and float(prev_hp) - hp >= 0.08:
            rapid_drop = True
        if age >= 350:
            state.health_samples[aid] = (hp, int(now_ms))
    for aid in list(state.health_samples):
        if aid not in live:
            state.health_samples.pop(aid, None)
    urgent = bool(lowest <= 0.60 or rapid_drop)
    pressure = bool(urgent or lowest <= 0.68 or damaged >= 2)
    return urgent, pressure, float(lowest), int(damaged)


def _try_normal_heals(state: _ControllerState, *, proactive: bool = False) -> bool:
    spirit_near = _spirit_in_earshot(state)
    mbas_threshold = 0.72 if proactive else 0.68
    ghost_threshold = 0.70 if proactive else 0.66
    spirit_threshold = 0.74 if proactive else 0.70

    # MBaS first when it can cleanse as well as heal.
    if _ready(state, MEND_BODY_AND_SOUL_ID):
        target = _lowest_ally(state, mbas_threshold, prefer_conditioned=spirit_near)
        if target > 0 and _request_cast(state, MEND_BODY_AND_SOUL_ID, target, purpose="sustain_mend_body_and_soul"):
            return True

    if _ready(state, GHOSTMIRROR_LIGHT_ID):
        target = _lowest_ally(state, ghost_threshold, other_only=True, prefer_conditioned=True)
        if target > 0 and _request_cast(state, GHOSTMIRROR_LIGHT_ID, target, purpose="sustain_ghostmirror_light"):
            return True

    if _ready(state, SPIRIT_LIGHT_ID) and (spirit_near or _health(state.hero_agent_id) >= 0.78):
        target = _lowest_ally(state, spirit_threshold, prefer_conditioned=True)
        if target > 0 and _request_cast(state, SPIRIT_LIGHT_ID, target, purpose="sustain_spirit_light"):
            return True

    return False


def _run_state(state: _ControllerState, now_ms: int) -> None:
    if now_ms - state.last_scan_ms < _SCAN_INTERVAL_MS:
        return
    state.last_scan_ms = now_ms
    if not _ensure_locked(state, now_ms):
        return
    _process_pi_observation(state, now_ms)
    if _process_pending(state, now_ms):
        return
    if not _hero_can_cast(state):
        return

    # 1) A collapsing ally always overrides shutdown.
    if _try_emergency_heals(state):
        return

    small_party_support = _small_party_support_mode()
    if small_party_support:
        urgent_heal, heal_pressure, lowest_hp, damaged_count = _healing_pressure(state, now_ms)
    else:
        urgent_heal, heal_pressure, lowest_hp, damaged_count = False, False, 1.0, 0
    # A fast HP collapse is treated as a real emergency in 8-man-and-smaller
    # parties. 9-12 man keeps the v33 priority order unchanged.
    if small_party_support and urgent_heal and _try_normal_heals(state, proactive=True):
        _log("HERO_PI_RESTO_PROACTIVE_HEAL", hero_index=state.hero_index, hero_id=state.hero_agent_id,
             lowest_hp=round(lowest_hp, 3), damaged_count=damaged_count, priority="before_danger_interrupt")
        return

    # EXP6: PI first serves as cluster knockdown. If the shared/local packet has
    # 2+ foes, give it first refusal after urgent healing. This never gates any
    # Keystone Mesmer; it is an independent opportunistic spike enhancer.
    _pi_anchor, _pi_members = _cluster_or_visible_enemies(state, _ADJACENT)
    if len(_pi_members) >= 2 and _try_psychic_instability(state):
        return

    # Dangerous single casts belong to Complicate/Power Drain/Cry; PI is excluded
    # from this ready-now pool.
    if _try_opportunistic_danger_interrupt(state):
        return
    # 2) If energy has collapsed so far that the hero cannot even pay for a
    #    ready PI, Power Drain gets one emergency refill opportunity first.
    if (
        _ready(state, PSYCHIC_INSTABILITY_ID)
        and _energy_ratio(state) <= POWER_DRAIN_CRITICAL_THRESHOLD
        and not _can_pay(state, PSYCHIC_INSTABILITY_ID)
        and _try_power_drain(state, critical_only=True)
    ):
        return
    # 3) Under meaningful party pressure, heal before spending the elite on an
    #    optional PI opener. This is the main 8-man sustain correction.
    if small_party_support and heal_pressure and _try_normal_heals(state, proactive=True):
        _log("HERO_PI_RESTO_PROACTIVE_HEAL", hero_index=state.hero_index, hero_id=state.hero_agent_id,
             lowest_hp=round(lowest_hp, 3), damaged_count=damaged_count, priority="before_pi_opener")
        return

    # 4) If no useful packet PI fired above, allow PI as the single-target
    #    fallback interrupt/knockdown.
    if _try_psychic_instability(state):
        return
    # 5) Normal Restoration sustain remains available before optional secondary
    #    shutdown even when the proactive gate did not fire.
    if _try_normal_heals(state):
        return
    # 5) Cry is most valuable when two or more foes are casting in the same
    #    Nearby packet; use it before single-target filler interrupts.
    if _try_cry_of_frustration(state, multi_only=True):
        return
    # 6) Power Drain now replaces Energy Tap as actual interrupt + energy
    #    management. PI and multi-Cry have already had first refusal.
    if _try_power_drain(state, critical_only=False):
        return
    # 7) Complicate disables a genuinely dangerous remaining cast.
    if _try_complicate(state, dangerous_only=True):
        return
    # 8) Keep Life pre-loaded in real combat, while reserving PI energy.
    if _try_life_prep(state):
        return
    # 9) While PI is recharging and healing is stable, Complicate may disable
    #    an ordinary caster/support skill instead of sitting idle.
    if _try_complicate(state, dangerous_only=False):
        return
    # 10) If Complicate is unavailable, Cry may still save a dangerous single
    #     cast instead of waiting for an artificial multi-cast condition.
    _try_cry_of_frustration(state, multi_only=False)


def _restore_all() -> None:
    for state in list(_STATES.values()):
        _restore_native_ai(state)
    _STATES.clear()
    _DISCOVERED_HEROES.clear()


def run(*, enabled: bool = True) -> None:
    global _LAST_MAP_ID, _RUNTIME_LOGGED, _NO_MATCH_LOGGED
    if not enabled:
        _restore_all()
        return
    try:
        map_id = int(Map.GetMapID() or 0)
    except Exception:
        map_id = 0
    if _LAST_MAP_ID and map_id != _LAST_MAP_ID:
        _restore_all()
    _LAST_MAP_ID = map_id
    try:
        if not Map.IsExplorable() or not Party.IsPartyLoaded():
            _restore_all()
            return
    except Exception:
        return
    if not _RUNTIME_LOGGED:
        _RUNTIME_LOGGED = True
        _log("HERO_PI_RESTO_CONTROLLER_ACTIVE", version="v22_power_drain_cry_bar")
    states = _reconcile_states()
    if not states:
        if not _NO_MATCH_LOGGED:
            _NO_MATCH_LOGGED = True
            _log("HERO_PI_RESTO_NO_MATCH")
            try:
                heroes = list(Party.GetHeroes() or [])
            except Exception:
                heroes = []
            for hero_position, hero in enumerate(heroes, start=1):
                hero_id = int(getattr(hero, "agent_id", 0) or 0)
                ids = set(int(x) for x in _bar_ids(int(hero_position)) if int(x or 0) > 0)
                missing = sorted(int(x) for x in _REQUIRED_IDS if int(x) not in ids)
                try:
                    is_mesmer = bool(hero_id > 0 and _primary_is_mesmer(int(hero_id)))
                except Exception:
                    is_mesmer = False
                if hero_id <= 0:
                    reason = "no_hero_agent"
                elif not is_mesmer:
                    reason = "wrong_primary_profession"
                elif missing:
                    reason = "missing_required_pi_resto_skills"
                else:
                    reason = "transient_mapping_or_discovery"
                _log(
                    "HEROAI_CONTROLLER_MATCH_DIAG",
                    owner_kind="native_hero", owner_id=int(hero_id), hero_index=int(hero_position),
                    intended_role="pi_resto", selected_controller="PIRestorationHeroController",
                    matched=False, reject_reason=str(reason),
                    bar_skill_ids=",".join(map(str, sorted(ids))),
                    required_skill_ids=",".join(map(str, sorted(_REQUIRED_IDS))),
                    missing_skill_ids=",".join(map(str, missing)),
                    policy="exp36_stage12_e19_phase2_controller_plus_session_observation",
                )
        return
    _NO_MATCH_LOGGED = False
    try:
        from Py4GWCoreLib.Builds.Skills.CombatSense import single_hostile_spirit_cleanup_target
        if int(single_hostile_spirit_cleanup_target() or 0) > 0:
            return
    except Exception:
        pass
    now_ms = _now_ms()
    for state in states:
        try:
            _run_state(state, now_ms)
        except Exception as exc:
            _log("HERO_PI_RESTO_CONTROLLER_ERROR", hero_index=state.hero_index,
                 hero_id=state.hero_agent_id, error=type(exc).__name__, detail=str(exc)[:180])
