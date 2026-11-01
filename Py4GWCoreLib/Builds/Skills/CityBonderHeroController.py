from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any

from Py4GWCoreLib import Agent
from Py4GWCoreLib import AgentArray
from Py4GWCoreLib import Map
from Py4GWCoreLib import Party
from Py4GWCoreLib import Player
from Py4GWCoreLib import Profession
from Py4GWCoreLib import Range
from Py4GWCoreLib import Routines
from Py4GWCoreLib import Skill
from Py4GWCoreLib import SkillBar
from Py4GWCoreLib import Utils
from Py4GWCoreLib.enums_src.GameData_enums import Attribute

HEALING_BREEZE_ID = int(Skill.GetID("Healing_Breeze") or 0)
REBIRTH_ID = int(Skill.GetID("Rebirth") or 0)
QUICKENING_ZEPHYR_ID = int(Skill.GetID("Quickening_Zephyr") or 0)
BLESSED_SIGNET_ID = int(Skill.GetID("Blessed_Signet") or 0)
MENDING_ID = int(Skill.GetID("Mending") or 0)
BALTHAZARS_SPIRIT_ID = int(Skill.GetID("Balthazars_Spirit") or 0)
LIFE_BOND_ID = int(Skill.GetID("Life_Bond") or 0)
LIFE_BARRIER_ID = int(Skill.GetID("Life_Barrier") or 0)

_BONDER_SKILLS = (
    HEALING_BREEZE_ID,
    REBIRTH_ID,
    QUICKENING_ZEPHYR_ID,
    BLESSED_SIGNET_ID,
    MENDING_ID,
    BALTHAZARS_SPIRIT_ID,
    LIFE_BOND_ID,
    LIFE_BARRIER_ID,
)
_REQUIRED = frozenset(skill_id for skill_id in _BONDER_SKILLS if skill_id > 0)
_BONDS = (LIFE_BARRIER_ID, LIFE_BOND_ID, MENDING_ID, BALTHAZARS_SPIRIT_ID)
_SCAN_MS = 90
_LOCK_REFRESH_MS = 2400
_MATCH_GRACE_MS = 1600
_COMMAND_GUARD_MS = 140
_COMMAND_TIMEOUT_MS = 4000
_CAST_RANGE = float(Range.Spellcast.value)


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
    hero_index: int
    hero_id: int
    locked_slots: set[int] = field(default_factory=lambda: set[int]())
    last_lock_ms: int = 0
    last_seen_ms: int = 0
    last_scan_ms: int = 0
    last_command_ms: int = 0
    pending: _Pending = field(default_factory=_Pending)
    matched_logged: bool = False


_STATES: dict[int, _State] = {}
_last_map_id = 0
_no_match_logged = False
_active_owner = ""
_owner_conflict_logged = False


def get_bonder_template_code() -> str:
    return Utils.GenerateSkillbarTemplateFrom(
        int(Profession.Monk.value),
        int(Profession.Ranger.value),
        {
            int(Attribute.ProtectionPrayers.value): 12,
            int(Attribute.HealingPrayers.value): 10,
            int(Attribute.DivineFavor.value): 4,
            int(Attribute.WildernessSurvival.value): 7,
        },
        list(_BONDER_SKILLS),
    )


def _now() -> int:
    try:
        import PySystem

        return int(PySystem.get_tick_count64() or 0)
    except Exception:
        import time

        return int(time.monotonic() * 1000.0)


def _log(event: str, **fields: object) -> None:
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug

        CombatDebug.log_event(str(event), **fields)
    except Exception:
        pass


def _entries(hero_index: int) -> list[tuple[int, int, object]]:
    try:
        bar: list[Any] = getattr(SkillBar, "GetHeroSkillbar")(int(hero_index)) or []
    except Exception:
        return []
    entries: list[tuple[int, int, object]] = []
    for slot, data in enumerate(bar, start=1):
        try:
            skill_id = int(getattr(getattr(data, "id", None), "id", 0) or 0)
        except Exception:
            skill_id = 0
        entries.append((slot, skill_id, data))
    return entries


def _professions_match(hero_id: int) -> bool:
    try:
        primary, secondary = Agent.GetProfessions(int(hero_id))
        primary_value = int(getattr(primary, "value", primary) or 0)
        secondary_value = int(getattr(secondary, "value", secondary) or 0)
        return primary_value == int(Profession.Monk.value) and secondary_value == int(Profession.Ranger.value)
    except Exception:
        return False


def _matching() -> list[tuple[int, int]]:
    try:
        own_login = int(Player.GetLoginNumber() or 0)
        if own_login <= 0:
            return []
        heroes = list(Party.GetHeroes() or [])
    except Exception:
        return []

    matches: list[tuple[int, int]] = []
    for hero_position, hero in enumerate(heroes, start=1):
        hero_id = int(getattr(hero, "agent_id", 0) or 0)
        owner_login = int(getattr(hero, "owner_player_id", -1) or -1)
        if hero_id <= 0 or owner_login != own_login or not _professions_match(hero_id):
            continue
        skill_ids = frozenset(skill_id for _, skill_id, _ in _entries(hero_position) if skill_id > 0)
        if skill_ids == _REQUIRED:
            matches.append((hero_position, hero_id))
    return matches


def _restore(state: _State) -> None:
    for slot in sorted(state.locked_slots):
        try:
            getattr(Party.Heroes, "SetSkillAIEnabled")(int(state.hero_id), int(slot), True)
        except Exception:
            pass
    if state.locked_slots:
        _log("CITY_BONDER_NATIVE_AI_RESTORED", hero_index=state.hero_index, hero_id=state.hero_id)
    state.locked_slots.clear()
    state.pending = _Pending()


def _restore_all() -> None:
    for state in list(_STATES.values()):
        _restore(state)
    _STATES.clear()


def reset(*, owner: str | None = None) -> None:
    global _last_map_id, _no_match_logged, _active_owner, _owner_conflict_logged
    if owner is not None and _active_owner and owner != _active_owner:
        return
    _restore_all()
    _last_map_id = 0
    _no_match_logged = False
    _active_owner = ""
    _owner_conflict_logged = False


def _reconcile(now: int) -> list[_State]:
    matches = _matching()
    live_ids = {hero_id for _, hero_id in matches}
    for hero_id in list(_STATES):
        if hero_id in live_ids:
            continue
        state = _STATES[hero_id]
        if state.last_seen_ms and now - state.last_seen_ms <= _MATCH_GRACE_MS:
            continue
        _restore(_STATES.pop(hero_id))

    states: list[_State] = []
    for hero_index, hero_id in matches:
        state = _STATES.get(hero_id)
        if state is None:
            state = _State(hero_index=hero_index, hero_id=hero_id, last_seen_ms=now)
            _STATES[hero_id] = state
        state.hero_index = hero_index
        state.last_seen_ms = now
        if not state.matched_logged:
            state.matched_logged = True
            _log("CITY_BONDER_MATCHED", hero_index=hero_index, hero_id=hero_id)
        states.append(state)
    return states


def _ensure_locked(state: _State, now: int) -> bool:
    desired = set(range(1, 9))
    if state.locked_slots == desired and now - state.last_lock_ms < _LOCK_REFRESH_MS:
        return True
    success = True
    for slot in sorted(desired):
        try:
            getattr(Party.Heroes, "SetSkillAIEnabled")(int(state.hero_id), int(slot), False)
            state.locked_slots.add(slot)
        except Exception:
            success = False
    state.last_lock_ms = now
    if success:
        _log("CITY_BONDER_AI_LOCKED", hero_index=state.hero_index, hero_id=state.hero_id)
    return success


def _entry(state: _State, skill_id: int) -> tuple[int, object] | None:
    for slot, current_id, data in _entries(state.hero_index):
        if current_id == int(skill_id):
            return slot, data
    return None


def _recharge(state: _State, skill_id: int) -> int:
    entry = _entry(state, skill_id)
    if entry is None:
        return -1
    try:
        return int(getattr(entry[1], "recharge", 0) or 0)
    except Exception:
        return 0


def _ready(state: _State, skill_id: int) -> bool:
    return skill_id > 0 and _entry(state, skill_id) is not None and _recharge(state, skill_id) == 0


def _energy_ratio(agent_id: int) -> float:
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
        energy_cost = getattr(Skill.Data, "GetEnergyCost")(int(skill_id))
        return _energy_current(state) >= int(energy_cost or 0)
    except Exception:
        return True


def _distance(first_id: int, second_id: int) -> float:
    try:
        distance = getattr(Utils, "Distance")
        return float(distance(Agent.GetXY(int(first_id)), Agent.GetXY(int(second_id))))
    except Exception:
        return 999999.0


def _can_act(state: _State) -> bool:
    try:
        return not (
            Agent.IsDead(state.hero_id)
            or Agent.IsKnockedDown(state.hero_id)
            or Agent.IsCasting(state.hero_id)
            or Agent.IsMoving(state.hero_id)
        )
    except Exception:
        return False


def _has_effect(agent_id: int, skill_id: int) -> bool:
    try:
        has_effect = getattr(Routines.Checks.Agents, "HasEffect")
        return bool(has_effect(int(agent_id), int(skill_id)))
    except Exception:
        return False


def _quickening_zephyr_active() -> bool:
    try:
        from Py4GWCoreLib import SpiritModelID
        from Py4GWCoreLib.enums import SPIRIT_BUFF_MAP

        player_id = Player.GetAgentID()
        for spirit_id in AgentArray.GetSpiritPetArray() or []:
            spirit_id = int(spirit_id or 0)
            if (
                spirit_id <= 0
                or not Agent.IsAlive(spirit_id)
                or _distance(player_id, spirit_id) > float(Range.Earshot.value)
            ):
                continue
            model_id = int(Agent.GetPlayerNumber(spirit_id) or 0)
            if model_id not in SpiritModelID._value2member_map_:
                continue
            if int(SPIRIT_BUFF_MAP.get(SpiritModelID(model_id), 0) or 0) == QUICKENING_ZEPHYR_ID:
                return True
    except Exception:
        pass
    return False


def _pending_confirmed(state: _State, pending: _Pending) -> bool:
    try:
        if Agent.IsCasting(state.hero_id) and int(Agent.GetCastingSkillID(state.hero_id) or 0) == pending.skill_id:
            return True
    except Exception:
        pass
    return _recharge(state, pending.skill_id) > pending.start_recharge


def _process_pending(state: _State, now: int) -> bool:
    pending = state.pending
    if pending.skill_id <= 0:
        return False
    if _pending_confirmed(state, pending):
        _log(
            "CITY_BONDER_CAST_CONFIRMED",
            hero_index=state.hero_index,
            skill_id=pending.skill_id,
            purpose=pending.purpose,
        )
        state.pending = _Pending()
        return False
    if now - pending.started_ms <= _COMMAND_TIMEOUT_MS:
        return True
    _log("CITY_BONDER_CAST_TIMEOUT", hero_index=state.hero_index, skill_id=pending.skill_id, purpose=pending.purpose)
    state.pending = _Pending()
    return False


def _request(state: _State, skill_id: int, target_id: int, purpose: str) -> bool:
    if (
        not _ready(state, skill_id)
        or not _can_pay(state, skill_id)
        or state.pending.skill_id > 0
        or not _can_act(state)
    ):
        return False
    if target_id > 0 and target_id != state.hero_id and _distance(state.hero_id, target_id) > _CAST_RANGE:
        return False
    now = _now()
    if now - state.last_command_ms < _COMMAND_GUARD_MS:
        return False
    entry = _entry(state, skill_id)
    if entry is None:
        return False
    slot, _ = entry
    try:
        getattr(SkillBar, "HeroUseSkill")(int(target_id), int(slot), int(state.hero_index))
    except Exception as exc:
        _log(
            "CITY_BONDER_CAST_ERROR",
            hero_index=state.hero_index,
            skill_id=skill_id,
            purpose=purpose,
            error=type(exc).__name__,
        )
        return False
    state.pending = _Pending(
        skill_id=skill_id,
        slot=slot,
        target_id=int(target_id),
        started_ms=now,
        start_recharge=max(0, _recharge(state, skill_id)),
        purpose=purpose,
    )
    state.last_command_ms = now
    _log(
        "CITY_BONDER_CAST_REQUESTED",
        hero_index=state.hero_index,
        skill_id=skill_id,
        target_id=target_id,
        purpose=purpose,
    )
    return True


def _run_state(state: _State, now: int) -> None:
    if now - state.last_scan_ms < _SCAN_MS:
        return
    state.last_scan_ms = now
    if not _ensure_locked(state, now) or _process_pending(state, now) or not _can_act(state):
        return

    player_id = Player.GetAgentID()
    if player_id <= 0:
        return
    if Agent.IsDead(player_id):
        _request(state, REBIRTH_ID, player_id, "resurrect_ra")
        return

    bonds_ready = True
    for skill_id in _BONDS:
        if _has_effect(player_id, skill_id):
            continue
        bonds_ready = False
        if _request(state, skill_id, player_id, "maintain_bonds"):
            return

    if not bonds_ready:
        return

    if _energy_ratio(state.hero_id) < 0.60:
        if _request(state, BLESSED_SIGNET_ID, state.hero_id, "energy_recovery"):
            return

    if not _quickening_zephyr_active():
        if _request(state, QUICKENING_ZEPHYR_ID, state.hero_id, "quickening_zephyr"):
            return

    if not _has_effect(player_id, HEALING_BREEZE_ID):
        _request(state, HEALING_BREEZE_ID, player_id, "healing_breeze")


def run(*, enabled: bool = True, owner: str) -> None:
    global _last_map_id, _no_match_logged, _active_owner, _owner_conflict_logged
    if not enabled:
        if owner == _active_owner:
            reset(owner=owner)
        return
    if _active_owner and owner != _active_owner:
        if not _owner_conflict_logged:
            _owner_conflict_logged = True
            _log("CITY_BONDER_OWNER_CONFLICT", active_owner=_active_owner, rejected_owner=owner)
        return
    _active_owner = owner
    _owner_conflict_logged = False
    try:
        map_id = int(Map.GetMapID() or 0)
    except Exception:
        map_id = 0
    if _last_map_id and map_id != _last_map_id:
        _restore_all()
    _last_map_id = map_id
    try:
        if not Map.IsExplorable() or not Party.IsPartyLoaded():
            _restore_all()
            return
    except Exception:
        return

    now = _now()
    states = _reconcile(now)
    if not states:
        if not _no_match_logged:
            _no_match_logged = True
            _log("CITY_BONDER_NO_MATCH")
        return
    _no_match_logged = False
    for state in states:
        try:
            _run_state(state, now)
        except Exception as exc:
            _log(
                "CITY_BONDER_CONTROLLER_ERROR",
                hero_index=state.hero_index,
                hero_id=state.hero_id,
                error=type(exc).__name__,
                detail=str(exc)[:180],
            )
