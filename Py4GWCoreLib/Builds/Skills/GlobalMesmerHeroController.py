"""Stage 2.53: Combat-only Mesmer hero support for the three-eSurge team.

Runs from the existing HeroAI widget. No movement, NPC/quest/consumable or
resurrection actions. Reuses PanicMesmerHeroController's confirmed HeroUseSkill
and native-AI lock/restore mechanics and the existing team-focus/interrupt APIs.

Only recognises elite+bar-shaped eSurge or Ineptitude heroes. Player-account
Mesmers are *not* intercepted and continue using the existing BuildMgr classes.
"""
from __future__ import annotations

from Py4GWCoreLib import Agent, Map, Party, Player, Range, Skill, Utils
from Py4GWCoreLib.Builds.Skills import MesmerwayCoordination as MWC
from Py4GWCoreLib.Builds.Skills import PanicMesmerHeroController as Native
from Py4GWCoreLib.Builds.Skills import TeamCombatFocus as Focus


def _id(name: str) -> int:
    return int(Skill.GetID(name) or 0)

ENERGY_SURGE = _id('Energy_Surge')
INEPTITUDE = _id('Ineptitude')
WANDERING_EYE = _id('Wandering_Eye')
CLUMSINESS = _id('Clumsiness')
SIGNET_OF_CLUMSINESS = _id('Signet_of_Clumsiness')
ACCUMULATED_PAIN = _id('Accumulated_Pain')
ARCANE_CONUNDRUM = _id('Arcane_Conundrum')
MISTRUST = _id('Mistrust')
CRY = _id('Cry_of_Frustration')
UNNATURAL = _id('Unnatural_Signet')
SPIRITUAL_PAIN = _id('Spiritual_Pain')
POWER_DRAIN = _id('Power_Drain')
POWER_SPIKE = _id('Power_Spike')
SHATTER_HEX = _id('Shatter_Hex')
SHATTER_ENCHANTMENT = _id('Shatter_Enchantment')
DRAIN_ENCHANTMENT = _id('Drain_Enchantment')
FRUSTRATION = _id('Frustration')

# Shatter Hex uses the same cross-account hex-removal lease as player builds.
# Never pass an enemy target to this ally-targeted spell.
ESURGE_MANAGED = frozenset(x for x in (
    ENERGY_SURGE, MISTRUST, CRY, UNNATURAL, SPIRITUAL_PAIN,
    POWER_DRAIN, POWER_SPIKE, SHATTER_ENCHANTMENT, DRAIN_ENCHANTMENT, SHATTER_HEX
) if x > 0)
INEPT_MANAGED = frozenset(x for x in (
    INEPTITUDE, WANDERING_EYE, CLUMSINESS, SIGNET_OF_CLUMSINESS,
    ACCUMULATED_PAIN, ARCANE_CONUNDRUM, POWER_DRAIN, FRUSTRATION
) if x > 0)

_STATES: dict[int, Native._State] = {}
_ROLES: dict[int, str] = {}
_LAST_MAP = 0
_SCANNED_MS = 0


def _log(event: str, **fields) -> None:
    Native._log(event, **fields)


def _role_for_ids(ids: set[int]) -> str:
    # Fail closed on partially populated bars, borrowed elite skills and other
    # build variants: require both the elite and distinguishing core skills.
    esurge_core = (ENERGY_SURGE, MISTRUST, CRY, UNNATURAL, SPIRITUAL_PAIN)
    inept_core = (INEPTITUDE, WANDERING_EYE, CLUMSINESS)
    if all(s > 0 for s in esurge_core) and set(esurge_core).issubset(ids):
        return 'esurge'
    if all(s > 0 for s in inept_core) and set(inept_core).issubset(ids):
        return 'ineptitude'
    return ''


def _owned_heroes() -> list[tuple[int, int, int, str]]:
    try:
        heroes = list(Party.GetHeroes() or [])
        login = int(Player.GetLoginNumber() or 0)
    except Exception:
        return []
    out = []
    for bar_index, hero in enumerate(heroes):
        hid = int(getattr(hero, 'agent_id', 0) or 0)
        owner = int(getattr(hero, 'owner_player_id', 0) or 0)
        if hid <= 0 or (login > 0 and owner != login):
            continue
        role = _role_for_ids(Native._bar_ids(bar_index))
        if role:
            out.append((bar_index + 1, bar_index, hid, role))
    return out


def _restore_all() -> None:
    for state in _STATES.values():
        Native._restore_native(state)
    _STATES.clear()
    _ROLES.clear()


def _reconcile() -> list[Native._State]:
    matched = _owned_heroes()
    found = {hid for _, _, hid, _ in matched}
    for hid in tuple(_STATES):
        if hid not in found:
            Native._restore_native(_STATES.pop(hid))
            _ROLES.pop(hid, None)
    states = []
    for hero_index, bar_index, hid, role in matched:
        managed = ESURGE_MANAGED if role == 'esurge' else INEPT_MANAGED
        slots = tuple(slot for slot, sid, _ in Native._entries(bar_index) if sid in managed)
        state = _STATES.get(hid)
        if state is not None and (state.managed_slots != slots or _ROLES.get(hid) != role
                                  or state.bar_index != bar_index):
            Native._restore_native(state)
            state.pending = Native._Pending()
        if state is None:
            state = Native._State(hero_index=hero_index, bar_index=bar_index,
                                  hero_id=hid, managed_slots=slots)
            _STATES[hid] = state
            _log('GLOBAL_MESMER_HERO_MATCH', hero_id=hid, role=role,
                 slots=','.join(str(v) for v in slots))
        state.hero_index = hero_index
        state.bar_index = bar_index
        state.managed_slots = slots
        _ROLES[hid] = role
        states.append(state)
    return states


def _in_range(hero_id: int, target_id: int) -> bool:
    if target_id <= 0:
        return False
    try:
        return (Agent.IsValid(target_id) and Agent.IsAlive(target_id)
                and Utils.Distance(Agent.GetXY(hero_id), Agent.GetXY(target_id))
                <= float(Range.Spellcast.value))
    except Exception:
        return False


def _packet(state: Native._State) -> tuple[int, list[int], int]:
    """Read shared fight; never discover/approach a separate unpulled packet."""
    try:
        plan = Focus.get_team_combat_plan(
            filter_range=float(Range.Spellcast.value), minimum_enemies=1,
            consumer_role='global_hero_' + _ROLES.get(state.hero_id, 'mesmer'))
        members = [int(a) for a in plan.member_ids if _in_range(state.hero_id, int(a))]
        focus = int(plan.focus_target or 0)
        if focus not in members:
            focus = 0
        anchor = int(plan.aoe_anchor or 0)
        if anchor not in members:
            anchor = 0
        return focus, members, anchor
    except Exception:
        return 0, [], 0


def _danger_interrupt(state: Native._State, sid: int) -> bool:
    if not sid or not Native._ready(state, sid):
        return False
    try:
        target = int(MWC.reserve_single_interrupt_target(sid) or 0)
    except Exception:
        return False
    if not target:
        return False
    if _in_range(state.hero_id, target) and Native._request(state, sid, target, 'hero_exact_interrupt'):
        return True
    try:
        MWC.release_single_interrupt_reservation(target, sid, reason='hero_not_in_range_or_not_fired')
    except Exception:
        pass
    return False


def _cast(state: Native._State, sid: int, target: int, reason: str) -> bool:
    return bool(sid and _in_range(state.hero_id, target) and Native._ready(state, sid)
                and Native._request(state, sid, target, reason))


def _shatter_hex_hero(state: Native._State) -> bool:
    """Use existing cross-account hex removal queue on an in-range ally."""
    if not Native._ready(state, SHATTER_HEX) or not Native._can_pay(state, SHATTER_HEX):
        return False
    try:
        from Py4GWCoreLib.GlobalCache.HexRemovalPriority import get_hexed_ally_array
        from Py4GWCoreLib.GlobalCache.WhiteboardLocks import (
            filter_unlocked_hex_targets, post_hex_removal_lock,
        )
        # Filter by hero position, not player position, and only remove
        # genuinely active ally hexes. Highest hex urgency first.
        for ally in filter_unlocked_hex_targets(get_hexed_ally_array(4500.0)):
            ally = int(ally)
            if (ally <= 0 or not Agent.IsAlive(ally)
                    or Utils.Distance(Agent.GetXY(state.hero_id), Agent.GetXY(ally)) > float(Range.Spellcast.value)):
                continue
            if Native._request(state, SHATTER_HEX, ally, 'hero_shared_shatter_hex'):
                post_hex_removal_lock(ally, skill_id=SHATTER_HEX, aftercast_delay=250)
                return True
    except Exception as exc:
        _log('GLOBAL_MESMER_HERO_HEX_ERROR', hero_id=state.hero_id, error=type(exc).__name__)
    return False


def _hero_cleanup(state: Native._State, *, hard_only: bool = False, role: str = "esurge") -> bool:
    """Shared <=10% last-hit lane; never targets outside a manual engaged packet."""
    usable = ((UNNATURAL, SPIRITUAL_PAIN) if role == "esurge" else (ACCUMULATED_PAIN,))
    if not any(sid and Native._ready(state, sid) and Native._can_pay(state, sid) for sid in usable):
        return False
    target = int(MWC.reserve_cleanup_target(origin_id=state.hero_id, hard_only=hard_only) or 0)
    if not target:
        return False
    skill = 0
    for sid in usable:
        if not (sid and Native._ready(state, sid) and Native._can_pay(state, sid)):
            continue
        if sid == ACCUMULATED_PAIN:
            try:
                from Py4GWCoreLib import GLOBAL_CACHE
                effects = tuple(MWC._effect_ids(target))
                if sum(1 for eid in effects if GLOBAL_CACHE.Skill.Flags.IsHex(int(eid))) < 2:
                    continue
            except Exception:
                continue
        skill = sid
        break
    accepted = bool(skill and _cast(state, skill, target, 'hero_cleanup_direct'))
    if not accepted:
        MWC.release_cleanup_reservation(target, reason='hero_no_usable_damage_or_request_failed')
    else:
        _log('GLOBAL_MESMER_HERO_CLEANUP_REQUESTED', hero_id=state.hero_id,
             target_id=target, skill_id=skill, hard_only=hard_only)
    return accepted


def _esurge_action(state: Native._State, focus: int, members: list[int], anchor: int) -> bool:
    # A real urgent cast may interrupt the damage order, but regular damage
    # never waits for defensive slots not equipped on this bar.
    if Native._ready(state, CRY):
        packet = MWC.reserve_cry_packet()
        if packet is not None and _cast(state, CRY, int(packet.target_id), 'hero_cry'):
            MWC.register_cry_interrupt_cast(packet, CRY)
            return True
    if _danger_interrupt(state, POWER_DRAIN) or _danger_interrupt(state, POWER_SPIKE):
        return True
    if _shatter_hex_hero(state):
        return True
    fast_cleanup = globals().get('_hero_cleanup')
    if callable(fast_cleanup) and fast_cleanup(state, hard_only=True):
        return True
    # A dying isolated target can receive an elite only when the expected
    # Energy drain produces damage; this shared lease prevents random AoE drift.
    esurge_cleanup_picker = getattr(MWC, 'reserve_energy_surge_cleanup_target', None)
    if (callable(esurge_cleanup_picker) and Native._ready(state, ENERGY_SURGE)
            and getattr(Native, '_can_pay', lambda *_args: True)(state, ENERGY_SURGE)):
        target = int(esurge_cleanup_picker(
            origin_id=state.hero_id, allow_hard_single_target=True) or 0)
        if target:
            if _cast(state, ENERGY_SURGE, target, 'hero_cleanup_esurge'):
                return True
            MWC.release_cleanup_reservation(target, reason='hero_cleanup_esurge_not_requested')
            MWC.release_energy_surge_reservation(target, reason='hero_cleanup_esurge_not_requested')
    if Native._ready(state, ENERGY_SURGE):
        # Do not open a third damage front. Both candidates must cover the
        # authoritative primary and be within the casting hero's actual range.
        legal_centers = []
        for aid in (anchor, focus):
            try:
                aid = int(aid or 0)
                if not aid or aid in legal_centers or not _in_range(state.hero_id, aid):
                    continue
                if focus and aid != focus and Utils.Distance(
                        Agent.GetXY(aid), Agent.GetXY(focus)) > Range.Adjacent.value:
                    continue
                legal_centers.append(aid)
            except Exception:
                continue
        if legal_centers:
            def _score_center(aid: int):
                try:
                    m = MWC.esurge_target_metrics(int(aid))
                    return (float(m.get('expected_total_damage', 0) or 0),
                            int(m.get('density', 0) or 0), int(aid == focus))
                except Exception:
                    return (0.0, 0, int(aid == focus))
            target = max(legal_centers, key=_score_center)
            if _cast(state, ENERGY_SURGE, target, 'hero_shared_esurge_focus_aoe'):
                return True
    if callable(fast_cleanup) and fast_cleanup(state):
        return True
    if Native._ready(state, MISTRUST):
        target = int(MWC.reserve_mistrust_target() or 0)
        if _cast(state, MISTRUST, target, 'hero_shared_mistrust'):
            MWC.register_mistrust_cast(target)
            return True
    if Native._ready(state, SHATTER_ENCHANTMENT):
        target = int(MWC.reserve_shatter_enchantment_target() or 0)
        if _cast(state, SHATTER_ENCHANTMENT, target, 'hero_shatter_enchantment'):
            return True
    for sid in (UNNATURAL, SPIRITUAL_PAIN):
        picker = MWC.reserve_unnatural_target if sid == UNNATURAL else MWC.reserve_spiritual_pain_target
        if Native._ready(state, sid):
            target = int(picker() or 0)
            if _cast(state, sid, target, 'hero_direct_damage'):
                return True
    if Native._ready(state, DRAIN_ENCHANTMENT):
        target = int(MWC.reserve_shatter_enchantment_target() or 0)
        if _cast(state, DRAIN_ENCHANTMENT, target, 'hero_shared_drain_enchantment'):
            return True
    return False


def _ineptitude_action(state: Native._State, focus: int, members: list[int], _anchor: int) -> bool:
    # Only target physical attackers for expensive blind/attack punishment.
    attackers: list[tuple[int, int]] = []
    for aid in members:
        try:
            main, _secondary = Agent.GetProfessions(aid)
            profession = int(getattr(main, 'value', main) or 0)
            physical = bool(MWC.is_martial_attack_target(aid))  # W, R, A, P, D and weapon fallback
            active = bool(Agent.IsAttacking(aid))
            if physical or (active and FRUSTRATION in MWC._effect_ids(aid)):
                attackers.append((1 if active else 0, aid))
        except Exception:
            continue
    attackers.sort(key=lambda v: (-v[0], v[1]))
    melee_target = attackers[0][1] if attackers else 0
    effects = set(MWC._effect_ids(melee_target)) if melee_target else set()
    if _danger_interrupt(state, POWER_DRAIN):
        return True
    # Anti-melee remains first for genuinely active attackers; use the
    # conditional hex damage skill to finish only when two hexes are present.
    if not (melee_target and Agent.IsAttacking(melee_target)) and _hero_cleanup(state, hard_only=True, role="ineptitude"):
        return True
    if Native._ready(state, FRUSTRATION):
        target = int(MWC.reserve_frustration_target(origin_id=state.hero_id) or 0)
        if _cast(state, FRUSTRATION, target, 'hero_shared_frustration'):
            return True
    for sid in (INEPTITUDE, WANDERING_EYE, CLUMSINESS, SIGNET_OF_CLUMSINESS):
        if not melee_target or sid in effects:
            continue
        # If live attack evidence is the only classifier, never blindly cast
        # Ineptitude (Blind) on an unclassified creature. Clumsiness/Wandering
        # Eye can still trigger the existing Frustration hex on a real attack.
        if sid == INEPTITUDE and not MWC.is_martial_attack_target(melee_target):
            continue
        if sid == SIGNET_OF_CLUMSINESS and not Agent.IsAttacking(melee_target):
            continue
        if _cast(state, sid, melee_target, 'hero_antimelee'):
            return True
    try:
        casting = next((aid for aid in members if Agent.IsCasting(aid)), 0)
    except Exception:
        casting = 0
    if casting and _cast(state, ARCANE_CONUNDRUM, casting, 'hero_caster_control'):
        return True
    # Accumulated Pain is a hex-dependent damage skill; do not blindly spam.
    try:
        from Py4GWCoreLib import GLOBAL_CACHE
        effect_ids = tuple(MWC._effect_ids(focus)) if focus else ()
        hex_count = sum(1 for eid in effect_ids if GLOBAL_CACHE.Skill.Flags.IsHex(int(eid)))
    except Exception:
        hex_count = 0
    if _hero_cleanup(state, role="ineptitude"):
        return True
    if focus and hex_count >= 2 and _cast(state, ACCUMULATED_PAIN, focus, 'hero_hex_finisher'):
        return True
    return False


def _run_state(state: Native._State, now: int) -> None:
    if now - state.last_scan_ms < 90:
        return
    state.last_scan_ms = now
    if not Native._ensure_locked(state, now):
        return
    p = state.pending
    if p.skill_id > 0 and Native._pending_confirmed(state, p):
        if p.skill_id == ENERGY_SURGE:
            try:
                MWC.register_energy_surge_cast(p.target_id)
            except Exception:
                pass
    if Native._process_pending(state, now) or not Native._can_act(state):
        return
    focus, members, anchor = _packet(state)
    if not members:
        return
    try:
        if _ROLES.get(state.hero_id) == 'esurge':
            _esurge_action(state, focus, members, anchor)
        else:
            _ineptitude_action(state, focus, members, anchor)
    except Exception as exc:
        _log('GLOBAL_MESMER_HERO_ACTION_ERROR', hero_id=state.hero_id,
             error=type(exc).__name__, detail=str(exc)[:140])


def run(*, enabled: bool = True) -> None:
    global _LAST_MAP
    if not enabled:
        _restore_all()
        return
    try:
        map_id = int(Map.GetMapID() or 0)
        if not Map.IsExplorable() or not Party.IsPartyLoaded():
            _restore_all()
            return
    except Exception:
        return
    if _LAST_MAP and map_id != _LAST_MAP:
        _restore_all()
    _LAST_MAP = map_id
    states = _reconcile()
    now = Native._now_ms()
    for state in states:
        try:
            _run_state(state, now)
        except Exception as exc:
            _log('GLOBAL_MESMER_HERO_ERROR', hero_id=state.hero_id,
                 error=type(exc).__name__, detail=str(exc)[:140])
