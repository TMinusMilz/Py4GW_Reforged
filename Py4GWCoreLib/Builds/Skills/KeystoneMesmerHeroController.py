# EXP36 Stage 12 / E19 phase 2; legacy regression marker: exp36_stage5_e19_observation_only
"""RoJ-controller-derived controller for owned Keystone Mesmer heroes.

This module intentionally follows the proven RoJMonkHeroController architecture:
- hero skillbars are READ with the same one-based hero position used for casts;
- hero casts use that same one-based hero position;
- states are keyed by hero AgentID;
- owned slots are locked from native Hero AI and restored on unload/disable;
- every cast is request/confirmation driven, never assumed successful;
- Precast setup mirrors the real Keystone-account build: Symbolic Celerity is
  armed only near an approaching enemy packet, while Posture/Mimicry/Keystone
  wait for the tighter near-contact setup radius.
- Symbolic Posture is preferred, but if Keystone is ready while Posture is more
  than 2.5 seconds from readiness, the controller performs a fast Keystone
  fallback cycle instead of idling for the stance.
- Arcane Mimicry is an optional bonus step: when ready it hard-locks a
  currently verified SoJ Monk and uses the proven RoJ target-0/current-target
  dispatch; when unavailable/cooling down, the base Keystone rotation continues
  immediately without SoJ.

Expected native Keystone bar (order does not matter):
    Symbolic Celerity
    Symbolic Posture
    Arcane Mimicry
    Keystone Signet
    Mantra of Signets
    Unnatural Signet
    Signet of Sorrow
    Signet of Lost Souls

Arcane Mimicry's physical slot is allowed to transform into Signet of Judgment.
The controller only accepts a verified PRIMARY MONK whose exposed native elite is
Signet of Judgment as donor. A Paragon/HR target can never pass that firewall.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from Py4GWCoreLib import (
    Agent,
    AgentArray,
    GLOBAL_CACHE,
    Map,
    Party,
    Player,
    Profession,
    Range,
    Routines,
    Skill,
    SkillBar,
    Utils,
)
import PyGameThread
import PySkillbar


SYMBOLIC_CELERITY_ID = int(Skill.GetID("Symbolic_Celerity") or 0)
SYMBOLIC_POSTURE_ID = int(Skill.GetID("Symbolic_Posture") or 0)
ARCANE_MIMICRY_ID = int(Skill.GetID("Arcane_Mimicry") or 0)
KEYSTONE_SIGNET_ID = int(Skill.GetID("Keystone_Signet") or 0)
MANTRA_OF_SIGNETS_ID = int(Skill.GetID("Mantra_of_Signets") or 0)
UNNATURAL_SIGNET_ID = int(Skill.GetID("Unnatural_Signet") or 0)
SIGNET_OF_SORROW_ID = int(Skill.GetID("Signet_of_Sorrow") or 0)
SIGNET_OF_LOST_SOULS_ID = int(Skill.GetID("Signet_of_Lost_Souls") or 0)
SIGNET_OF_JUDGMENT_ID = int(Skill.GetID("Signet_of_Judgment") or 0)
RAY_OF_JUDGMENT_ID = int(Skill.GetID("Ray_of_Judgment") or 0)

_FIXED_KEYSTONE_IDS = frozenset(
    sid
    for sid in (
        SYMBOLIC_CELERITY_ID,
        SYMBOLIC_POSTURE_ID,
        KEYSTONE_SIGNET_ID,
        MANTRA_OF_SIGNETS_ID,
        UNNATURAL_SIGNET_ID,
        SIGNET_OF_SORROW_ID,
        SIGNET_OF_LOST_SOULS_ID,
    )
    if sid > 0
)
_NATIVE_REQUIRED_IDS = frozenset(set(_FIXED_KEYSTONE_IDS) | ({ARCANE_MIMICRY_ID} if ARCANE_MIMICRY_ID > 0 else set()))
_MANAGED_BASE_IDS = _NATIVE_REQUIRED_IDS
_OFFENSIVE_SIGNET_ORDER = (
    SIGNET_OF_JUDGMENT_ID,   # copied Mimicry slot first
    UNNATURAL_SIGNET_ID,
    SIGNET_OF_SORROW_ID,
    SIGNET_OF_LOST_SOULS_ID,
)

_SCAN_INTERVAL_MS = 90
_LOCK_REFRESH_MS = 2500
_COMMAND_TIMEOUT_MS = 3300
_MIMICRY_COMMAND_TIMEOUT_MS = 5200
_COMMAND_GUARD_MS = 170
_MIMICRY_TARGET_STABLE_MS = 120
_MIMICRY_TARGET_TIMEOUT_MS = 1800
# If Symbolic Posture will be ready very soon, waiting is worth it.  If it is
# farther away and Keystone is already ready, do a fast fallback Keystone cycle
# instead of idling for the stance.
_POSTURE_WAIT_THRESHOLD_MS = 2500
# KAY: proactive recasts only when verified effect time <=20s; never spam
# at unknown duration. A removed Celerity enchantment is recovered ASAP.
_PREBUFF_RENEW_MS = 20_000
_MANTRA_CRITICAL_RENEW_MS = 6_000  # never reserve or wait for cooldown
_PREBUFF_RETRY_GUARD_MS = 2_000
_PREBUFF_LAST_CAST: dict[tuple[int,int],int] = {}
_CAST_RANGE = float(Range.Spellcast.value)
_NEARBY = float(Range.Nearby.value)
_ADJACENT = float(Range.Adjacent.value)

# Match the proven real-account Keystone precombat timings.  Celerity may be
# prepared slightly earlier because it is long-lived; Posture/Mimicry/Keystone
# wait until near contact so the copied SoJ window and Keystone prime are not
# burned while merely running across the map.
_SYMBOLIC_PRECAST_SETUP_RANGE = _CAST_RANGE * 0.90
_KEYSTONE_PRECAST_SETUP_RANGE = _CAST_RANGE * 0.78

# Local owned-hero equivalent of the real accounts' cross-account packet
# distribution.  In a multi-enemy packet the three heroes rotate their target
# member instead of all wasting the same signet on the same foe.  Hard cleanup
# and mission-priority targets remain common focus.
_ELITE_PRIORITY_TARGET_NAMES = ("twisted bark", "krummrinde", "crooked bark")
_SKILL_TARGET_OFFSETS = {
    SIGNET_OF_JUDGMENT_ID: 0,
    UNNATURAL_SIGNET_ID: 1,
    SIGNET_OF_SORROW_ID: 2,
    SIGNET_OF_LOST_SOULS_ID: 3,
}


@dataclass(slots=True)
class _PendingCommand:
    skill_id: int = 0
    slot: int = 0
    target_id: int = 0
    started_ms: int = 0
    start_recharge: int = 0
    purpose: str = ""


@dataclass(slots=True)
class _ControllerState:
    # IMPORTANT: in this Reforged runtime both GetHeroSkillbar and HeroUseSkill
    # are addressed by the same one-based owned-hero position.  Keeping the two
    # values as fields makes the logs explicit, but they MUST be identical.
    hero_index: int
    hero_bar_index: int
    hero_agent_id: int

    managed_slots: tuple[int, ...] = ()
    locked_slots: set[int] = field(default_factory=set)

    celerity_slot: int = 0
    posture_slot: int = 0
    mimicry_slot: int = 0
    keystone_slot: int = 0
    mantra_slot: int = 0
    unnatural_slot: int = 0
    sorrow_slot: int = 0
    lost_souls_slot: int = 0

    phase: str = "setup"
    signets_spent_since_keystone: int = 0

    last_lock_refresh_ms: int = 0
    last_scan_ms: int = 0
    last_command_ms: int = 0
    pending: _PendingCommand = field(default_factory=_PendingCommand)
    matched_logged: bool = False

    # Mimicry transaction state; copied from the proven RoJ current-target pattern.
    mimicry_target_id: int = 0
    mimicry_old_player_target: int = 0
    mimicry_target_started_ms: int = 0
    mimicry_dispatch_result: str = ""
    unexpected_mimicry_logged_id: int = 0
    proximity_stage: str = ""


_STATES: dict[int, _ControllerState] = {}
_DISCOVERED_HEROES: set[int] = set()
_LAST_MAP_ID = 0
_RUNTIME_LOGGED = False
_ENTRY_LOGGED = False
_LAST_NO_MATCH_LOG_MS = 0
_LAST_NO_MATCH_SIGNATURE = ""
_MIMICRY_OWNER_HERO_ID = 0
_CONFIRMED_SOJ_DONOR_ID = 0
_MIMICRY_SLOT_TEMPLATE = 0


def _process_persistent_no_match_once(token: str) -> bool:
    """Stage 19 / E19f: one semantic no-match report per CombatSession token.

    The caller's token already embeds CombatSession.refresh().key.  Stage 18
    redundantly derived a second instance serial from native instance uptime;
    transient uptime/read glitches could therefore re-arm an identical report
    inside the same session.  Persist only the exact semantic token in builtins.
    A genuine map/reentry changes CombatSession's key and naturally permits one
    new report without any controller-local state.
    """
    try:
        import builtins
        state_name = "_exp36_keystone_no_match_seen_tokens"
        seen = getattr(builtins, state_name, None)
        if not isinstance(seen, set):
            seen = set()
        key = str(token or "")
        if not key or key in seen:
            setattr(builtins, state_name, seen)
            return False
        seen.add(key)
        # Bounded process lifetime memory; tokens are already session-qualified.
        if len(seen) > 256:
            # Keep the newest token and clear ancient sessions.  This can only
            # re-arm old sessions that are no longer active, never the current one.
            seen = {key}
        setattr(builtins, state_name, seen)
        return True
    except Exception:
        return True


def _now_ms() -> int:
    try:
        import PySystem
        return int(PySystem.get_tick_count64() or 0)
    except Exception:
        import time
        return int(time.monotonic() * 1000.0)


def _log(event: str, **fields: Any) -> None:
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(str(event), **fields)
    except Exception:
        pass


def _flush_debug() -> None:
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.flush_pending(force=True)
    except Exception:
        pass


def _as_int(value: Any) -> int:
    try:
        return int(getattr(value, "value", value))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# RoJ-derived hero index / skillbar plumbing
# ---------------------------------------------------------------------------

def _hero_bar(hero_position: int):
    """Read one owned hero bar by the SAME 1-based position used to cast it.

    This is the crucial mapping rule for the current Reforged runtime.  Reading
    position N and then casting hero N prevents the previous one-hero shift
    (read 1 -> cast 2, read 2 -> cast 3) that left hero 1 unmanaged and made
    confirmations observe the wrong hero.
    """
    try:
        return list(SkillBar.GetHeroSkillbar(int(hero_position)) or [])
    except Exception:
        return []


def _bar_entries(hero_position: int) -> list[tuple[int, int, object]]:
    rows: list[tuple[int, int, object]] = []
    for slot, data in enumerate(_hero_bar(hero_position), start=1):
        try:
            sid = int(getattr(getattr(data, "id", None), "id", 0) or 0)
        except Exception:
            sid = 0
        rows.append((slot, sid, data))
    return rows


def _bar_ids(hero_position: int) -> set[int]:
    return {sid for _, sid, _ in _bar_entries(hero_position) if sid > 0}


def _slot_map(hero_position: int) -> dict[int, int]:
    return {sid: slot for slot, sid, _ in _bar_entries(hero_position) if sid > 0}


def _hero_agent_for_position(hero_position: int) -> int:
    try:
        return int(Party.Heroes.GetHeroAgentIDByPartyPosition(int(hero_position)) or 0)
    except Exception:
        return 0


def _slot_for_base_skill(entries: list[tuple[int, int, object]], skill_id: int, mimicry_slot: int = 0) -> int:
    """Return the native slot, never a transformed Mimicry duplicate.

    If Mimicry copied Keystone, the bar temporarily contains Keystone twice.
    A plain {skill_id: slot} map would select the later copied slot and corrupt
    the state machine.  Excluding the known Mimicry slot keeps the native slot.
    """
    for slot, sid, _data in entries:
        if int(sid) == int(skill_id) and int(slot) != int(mimicry_slot or 0):
            return int(slot)
    return 0


def _matching_heroes() -> list[tuple[int, int, int]]:
    """Return owned Keystone Mesmers with strict 1:1 position mapping.

    We scan the actual HeroUseSkill positions 1..7 instead of enumerating the
    Party.GetHeroes list.  For every match, GetHeroSkillbar(position),
    GetHeroAgentIDByPartyPosition(position), SetSkillAIEnabled(agent, slot), and
    HeroUseSkill(..., position) all refer to the SAME hero.

    Discovery requires the seven invariant Keystone skills + primary Mesmer.
    Arcane Mimicry itself may already be transformed; once a hero is discovered
    we keep ownership through temporary skillbar read gaps/transformations.
    """
    global _MIMICRY_SLOT_TEMPLATE

    result: list[tuple[int, int, int]] = []

    # First learn the physical Mimicry slot from any clean native copy.
    if _MIMICRY_SLOT_TEMPLATE <= 0:
        for hero_position in range(1, 8):
            hero_id = _hero_agent_for_position(hero_position)
            if hero_id <= 0:
                continue
            entries = _bar_entries(hero_position)
            ids = {sid for _, sid, _ in entries if sid > 0}
            if len(entries) >= 8 and _NATIVE_REQUIRED_IDS and _NATIVE_REQUIRED_IDS.issubset(ids):
                slot = next((slot for slot, sid, _ in entries if sid == ARCANE_MIMICRY_ID), 0)
                if slot > 0:
                    # Stage 8: close the clean-bar discovery window before the
                    # second reconciliation pass. Native AI runs concurrently
                    # with this hook, so lock Mimicry the instant the slot is known.
                    try:
                        Party.Heroes.SetSkillAIEnabled(int(hero_id), int(slot), False)
                    except Exception:
                        pass
                    _MIMICRY_SLOT_TEMPLATE = int(slot)
                    break

    for hero_position in range(1, 8):
        hero_id = _hero_agent_for_position(hero_position)
        if hero_id <= 0:
            continue

        entries = _bar_entries(hero_position)
        ids = {sid for _, sid, _ in entries if sid > 0}

        try:
            primary, _secondary = Agent.GetProfessionIDs(hero_id)
            is_mesmer = _as_int(primary) == _as_int(Profession.Mesmer)
        except Exception:
            is_mesmer = False

        invariant_match = bool(
            is_mesmer
            and len(entries) >= 8
            and _FIXED_KEYSTONE_IDS
            and _FIXED_KEYSTONE_IDS.issubset(ids)
        )
        if invariant_match:
            _DISCOVERED_HEROES.add(hero_id)

        if hero_id not in _DISCOVERED_HEROES:
            continue

        # Lock all eight slots immediately on the correctly paired hero.  Do not
        # let a temporary transformed Mimicry slot make the hero disappear.
        for slot in range(1, 9):
            try:
                result_flag = Party.Heroes.SetSkillAIEnabled(int(hero_id), int(slot), False)
                if result_flag is False:
                    _log(
                        "HERO_KEYSTONE_AI_LOCK_FAILED",
                        hero_index=hero_position,
                        bar_index=hero_position,
                        hero_id=hero_id,
                        slot=slot,
                        error="returned_false",
                    )
            except Exception as exc:
                _log(
                    "HERO_KEYSTONE_AI_LOCK_FAILED",
                    hero_index=hero_position,
                    bar_index=hero_position,
                    hero_id=hero_id,
                    slot=slot,
                    error=type(exc).__name__,
                )

        # Both indices intentionally carry the SAME one-based position.
        result.append((hero_position, hero_position, hero_id))

    return result

def _managed_slots(bar_index: int) -> tuple[int, ...]:
    # This controller owns the entire 8-skill Keystone bar. Keeping all eight
    # slots disabled is essential: otherwise native Hero AI can fire Mimicry
    # between our state-machine steps and copy Keystone from another Mesmer.
    entries = _bar_entries(bar_index)
    if len(entries) >= 8:
        return tuple(range(1, 9))
    return tuple(sorted(slot for slot, _sid, _ in entries))


def _restore_native_ai(state: _ControllerState) -> None:
    global _MIMICRY_OWNER_HERO_ID
    # EXP36 Stage 9: restoring normal native AI must never reopen Arcane
    # Mimicry. Stage 8 proved the per-controller hard lock works, but one PI
    # copy still occurred inside the brief ownership-release window before the
    # Keystone controller reacquired the hero. Restore every ordinary slot,
    # while the learned physical Mimicry slot remains permanently AI-off for a
    # hero already discovered as this Keystone build.
    mimicry_slot = int(state.mimicry_slot or _MIMICRY_SLOT_TEMPLATE or 0)
    for slot in range(1, 9):
        try:
            Party.Heroes.SetSkillAIEnabled(
                int(state.hero_agent_id),
                int(slot),
                False if int(slot) == int(mimicry_slot) and mimicry_slot > 0 else True,
            )
        except Exception:
            pass
    state.locked_slots.clear()
    if _MIMICRY_OWNER_HERO_ID == state.hero_agent_id:
        _restore_player_target_after_mimicry(state)
        _MIMICRY_OWNER_HERO_ID = 0
    _log(
        "HERO_KEYSTONE_NATIVE_AI_RESTORED",
        hero_index=state.hero_index,
        bar_index=state.hero_bar_index,
        hero_id=state.hero_agent_id,
        mimicry_slot=int(mimicry_slot),
        mimicry_ai_locked=bool(mimicry_slot > 0),
        policy="exp36_stage9_mimicry_stays_ai_off_across_native_restore",
    )


def _reconcile_states() -> list[_ControllerState]:
    global _MIMICRY_SLOT_TEMPLATE
    matches = _matching_heroes()
    live = {hero_id for _, _, hero_id in matches}

    for hero_id in list(_STATES):
        if hero_id not in live:
            _restore_native_ai(_STATES.pop(hero_id))

    ordered: list[_ControllerState] = []
    for hero_index, bar_index, hero_id in matches:
        state = _STATES.get(hero_id)
        entries = _bar_entries(bar_index)
        current_managed = _managed_slots(bar_index)

        if state is None:
            visible_mimicry = next((slot for slot, sid, _ in entries if sid == ARCANE_MIMICRY_ID), 0)
            # Current user build uses Mimicry in slot 8.  Normally the clean bar
            # learns this automatically; slot 8 is only a recovery fallback if
            # native AI transformed Mimicry before the first controller frame.
            inferred_mimicry_slot = int(visible_mimicry or _MIMICRY_SLOT_TEMPLATE or (8 if len(entries) >= 8 else 0))
            if inferred_mimicry_slot > 0 and _MIMICRY_SLOT_TEMPLATE <= 0:
                _MIMICRY_SLOT_TEMPLATE = inferred_mimicry_slot
            state = _ControllerState(
                hero_index=hero_index,
                hero_bar_index=bar_index,
                hero_agent_id=hero_id,
                managed_slots=current_managed,
                celerity_slot=_slot_for_base_skill(entries, SYMBOLIC_CELERITY_ID, inferred_mimicry_slot),
                posture_slot=_slot_for_base_skill(entries, SYMBOLIC_POSTURE_ID, inferred_mimicry_slot),
                mimicry_slot=inferred_mimicry_slot,
                keystone_slot=_slot_for_base_skill(entries, KEYSTONE_SIGNET_ID, inferred_mimicry_slot),
                mantra_slot=_slot_for_base_skill(entries, MANTRA_OF_SIGNETS_ID, inferred_mimicry_slot),
                unnatural_slot=_slot_for_base_skill(entries, UNNATURAL_SIGNET_ID, inferred_mimicry_slot),
                sorrow_slot=_slot_for_base_skill(entries, SIGNET_OF_SORROW_ID, inferred_mimicry_slot),
                lost_souls_slot=_slot_for_base_skill(entries, SIGNET_OF_LOST_SOULS_ID, inferred_mimicry_slot),
            )
            _STATES[hero_id] = state
            # Lock Mimicry FIRST, then the rest of the bar, in the same frame
            # the hero becomes known. This closes the map-entry race where the
            # native Hero AI copied Keystone before _run_state() got control.
            lock_order = []
            if state.mimicry_slot > 0:
                lock_order.append(int(state.mimicry_slot))
            lock_order.extend(slot for slot in state.managed_slots if slot not in lock_order)
            for slot in lock_order:
                try:
                    lock_result = Party.Heroes.SetSkillAIEnabled(int(state.hero_agent_id), int(slot), False)
                    if lock_result is False:
                        _log(
                            "HERO_KEYSTONE_AI_LOCK_FAILED",
                            hero_index=state.hero_index, bar_index=state.hero_bar_index,
                            hero_id=state.hero_agent_id, slot=slot, error="returned_false",
                        )
                        continue
                    state.locked_slots.add(int(slot))
                except Exception as exc:
                    _log(
                        "HERO_KEYSTONE_AI_LOCK_FAILED",
                        hero_index=state.hero_index, bar_index=state.hero_bar_index,
                        hero_id=state.hero_agent_id, slot=slot, error=type(exc).__name__,
                    )
            state.last_lock_refresh_ms = _now_ms()
            _log(
                "HERO_KEYSTONE_AI_LOCK_ARMED",
                hero_index=state.hero_index, bar_index=state.hero_bar_index,
                hero_id=state.hero_agent_id, mimicry_slot=state.mimicry_slot,
                managed_slots=",".join(map(str, state.managed_slots)),
            )
        else:
            state.hero_index = hero_index
            state.hero_bar_index = bar_index
            # Keep the original Mimicry slot managed while it is transformed.
            if current_managed:
                state.managed_slots = tuple(sorted(set(state.managed_slots) | set(current_managed)))
            # Refresh native slots while explicitly excluding the physical
            # Mimicry slot.  This prevents a copied Keystone in slot 8 from
            # replacing the true native Keystone slot in controller state.
            visible_mimicry = next((slot for slot, sid, _ in entries if sid == ARCANE_MIMICRY_ID), 0)
            if visible_mimicry > 0:
                state.mimicry_slot = int(visible_mimicry)
            elif state.mimicry_slot <= 0:
                state.mimicry_slot = int(_MIMICRY_SLOT_TEMPLATE or (8 if len(entries) >= 8 else 0))
            for attr, sid in (
                ("celerity_slot", SYMBOLIC_CELERITY_ID),
                ("posture_slot", SYMBOLIC_POSTURE_ID),
                ("keystone_slot", KEYSTONE_SIGNET_ID),
                ("mantra_slot", MANTRA_OF_SIGNETS_ID),
                ("unnatural_slot", UNNATURAL_SIGNET_ID),
                ("sorrow_slot", SIGNET_OF_SORROW_ID),
                ("lost_souls_slot", SIGNET_OF_LOST_SOULS_ID),
            ):
                found = _slot_for_base_skill(entries, sid, state.mimicry_slot)
                if found > 0:
                    setattr(state, attr, int(found))

        if not state.matched_logged:
            state.matched_logged = True
            _log(
                "HERO_KEYSTONE_MATCHED",
                hero_index=hero_index,
                bar_index=bar_index,
                hero_id=hero_id,
                managed_slots=",".join(map(str, state.managed_slots)),
                mapping=f"read{bar_index}->cast{hero_index}",
                ordered_skill_ids=",".join(str(sid) for _slot, sid, _data in entries),
                celerity_slot=state.celerity_slot,
                posture_slot=state.posture_slot,
                mimicry_slot=state.mimicry_slot,
                keystone_slot=state.keystone_slot,
                mantra_slot=state.mantra_slot,
            )
            _log(
                "HEROAI_CONTROLLER_MATCH_DIAG",
                owner_kind="native_hero",
                owner_id=int(hero_id),
                hero_index=int(hero_index),
                intended_role="keystone_mesmer",
                selected_controller="KeystoneMesmerHeroController",
                matched=True,
                reject_reason="none",
                bar_skill_ids=",".join(str(sid) for _slot, sid, _data in entries),
                required_skill_ids=",".join(map(str, sorted(_FIXED_KEYSTONE_IDS))),
                policy="exp36_stage12_e19_phase2_controller_plus_session_observation",
            )
        ordered.append(state)

    return ordered


def _hard_lock_mimicry_slot(state: _ControllerState) -> bool:
    """Reassert Mimicry AI-off every frame, independent of the 2.5s bulk lock.

    Live Stage 7 proved that the native hero could still autonomously transform
    Arcane Mimicry into Psychic Instability twice even though our local lock set
    believed all eight slots were disabled.  Treat the physical Mimicry slot as
    special: reassert AI-off every controller frame so an engine-side toggle or
    transformed-slot refresh cannot reopen a native-AI copy window.
    """
    slot = int(state.mimicry_slot or 0)
    if slot <= 0:
        return False
    try:
        result = Party.Heroes.SetSkillAIEnabled(int(state.hero_agent_id), slot, False)
        if result is False:
            state.locked_slots.discard(slot)
            return False
        state.locked_slots.add(slot)
        return True
    except Exception:
        state.locked_slots.discard(slot)
        return False


def _ensure_locked(state: _ControllerState, now_ms: int) -> bool:
    wanted = set(state.managed_slots)
    if state.locked_slots == wanted and now_ms - state.last_lock_refresh_ms < _LOCK_REFRESH_MS:
        return True

    ok = True
    for slot in state.managed_slots:
        try:
            lock_result = Party.Heroes.SetSkillAIEnabled(int(state.hero_agent_id), int(slot), False)
            if lock_result is False:
                ok = False
                state.locked_slots.discard(int(slot))
                _log(
                    "HERO_KEYSTONE_AI_LOCK_FAILED",
                    hero_index=state.hero_index,
                    bar_index=state.hero_bar_index,
                    hero_id=state.hero_agent_id,
                    slot=slot,
                    error="returned_false",
                )
                continue
            state.locked_slots.add(int(slot))
        except Exception as exc:
            ok = False
            state.locked_slots.discard(int(slot))
            _log(
                "HERO_KEYSTONE_AI_LOCK_FAILED",
                hero_index=state.hero_index,
                bar_index=state.hero_bar_index,
                hero_id=state.hero_agent_id,
                slot=slot,
                error=type(exc).__name__,
            )
    state.last_lock_refresh_ms = now_ms
    return ok


# ---------------------------------------------------------------------------
# Skill state / cast confirmation -- same request-confirm pattern as RoJ
# ---------------------------------------------------------------------------

def _slot_skill_id(state: _ControllerState, slot: int) -> int:
    for s, sid, _ in _bar_entries(state.hero_bar_index):
        if s == int(slot):
            return int(sid)
    return 0


def _entry_for_slot(state: _ControllerState, slot: int):
    for s, sid, data in _bar_entries(state.hero_bar_index):
        if s == int(slot):
            return int(sid), data
    return None


def _entries_by_id(state: _ControllerState, skill_id: int) -> list[tuple[int, object]]:
    return [
        (slot, data)
        for slot, sid, data in _bar_entries(state.hero_bar_index)
        if sid == int(skill_id)
    ]


def _ready_entry(state: _ControllerState, skill_id: int):
    for slot, data in _entries_by_id(state, skill_id):
        if _recharge_data(data) == 0:
            return slot, data
    return None


def _recharge_data(data: object) -> int:
    try:
        return int(getattr(data, "recharge", 0) or 0)
    except Exception:
        return 0


def _remaining_recharge_ms(data: object) -> int:
    """Return live remaining recharge in milliseconds when exposed by PySkillbar.

    ``SkillbarSkill.recharge`` is the cast timestamp in this runtime, while the
    raw ``get_recharge`` property is the live remaining cooldown (also used by
    existing Reforged code).  Return -1 only when a skill is visibly cooling
    down but the live value cannot be read.
    """
    try:
        remaining = int(getattr(data, "get_recharge", 0) or 0)
        return max(0, remaining)
    except Exception:
        return 0 if _recharge_data(data) == 0 else -1


def _energy_current(state: _ControllerState) -> int:
    try:
        return int(
            float(Agent.GetEnergy(state.hero_agent_id) or 0.0)
            * int(Agent.GetMaxEnergy(state.hero_agent_id) or 0)
        )
    except Exception:
        return 0


def _can_pay(state: _ControllerState, skill_id: int) -> bool:
    try:
        return _energy_current(state) >= int(Skill.Data.GetEnergyCost(int(skill_id)) or 0)
    except Exception:
        return True


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


def _has_effect(state: _ControllerState, skill_id: int) -> bool:
    if skill_id <= 0:
        return False
    try:
        return bool(Routines.Checks.Effects.HasBuff(int(state.hero_agent_id), int(skill_id)))
    except Exception:
        return False


def _buff_remaining_ms(state: _ControllerState, skill_id: int) -> int | None:
    """Missing = 0, verified duration = positive ms, unreadable = None."""
    try:
        if not _has_effect(state, skill_id):
            return 0
        t = int(GLOBAL_CACHE.Effects.GetEffectTimeRemaining(int(state.hero_agent_id), int(skill_id)) or 0)
        return t if t > 0 else None
    except Exception:
        return None


def _try_hero_prebuff(
    state: _ControllerState, skill_id: int, slot: int, now_ms: int,
    purpose: str, *, allow_renew: bool,
) -> bool:
    if skill_id <= 0 or slot <= 0:
        return False
    remaining = _buff_remaining_ms(state, skill_id)
    if remaining is None or (remaining > 0 and (not allow_renew or remaining > _PREBUFF_RENEW_MS)):
        return False
    guard_key = (int(state.hero_agent_id), int(skill_id))
    if now_ms - int(_PREBUFF_LAST_CAST.get(guard_key, -_PREBUFF_RETRY_GUARD_MS)) < _PREBUFF_RETRY_GUARD_MS:
        return False
    if skill_id == MANTRA_OF_SIGNETS_ID and _has_effect(state, SYMBOLIC_POSTURE_ID):
        return False
    if _try_self_skill(state, skill_id, slot, purpose):
        _PREBUFF_LAST_CAST[guard_key] = now_ms
        _log('HERO_KEYSTONE_PREBUFF_UPKEEP', hero_id=state.hero_agent_id,
             skill_id=skill_id, remaining_ms=remaining, threshold_ms=_PREBUFF_RENEW_MS,
             reason=purpose)
        return True
    return False


def _pending_confirmed(state: _ControllerState, pending: _PendingCommand) -> bool:
    # Arcane Mimicry is special: starting the cast is NOT confirmation.  The
    # selected donor must remain locked until the physical Mimicry slot has
    # actually transformed.  Restoring the player's previous target at cast
    # start can make target-0/current resolve against a different ally.
    if pending.skill_id == ARCANE_MIMICRY_ID:
        for slot, sid, _data in _bar_entries(state.hero_bar_index):
            if slot != pending.slot:
                continue
            return bool(sid > 0 and sid != ARCANE_MIMICRY_ID)
        return False

    # For normal skills use the same confirmation rule as the working RoJ hero
    # controller: seeing THIS hero cast THIS requested skill is already proof
    # that the command reached the correct hero.  _hero_can_cast() prevents the
    # next step from being issued until that cast has ended.
    try:
        if (
            Agent.IsCasting(state.hero_agent_id)
            and int(Agent.GetCastingSkillID(state.hero_agent_id) or 0) == int(pending.skill_id)
        ):
            return True
    except Exception:
        pass

    # Instant skills/stances are confirmed by recharge or slot transformation.
    for slot, sid, data in _bar_entries(state.hero_bar_index):
        if slot != pending.slot:
            continue
        if sid != pending.skill_id:
            return True
        if _recharge_data(data) > pending.start_recharge:
            return True
    return False


def _process_pending(state: _ControllerState, now_ms: int) -> bool:
    global _MIMICRY_OWNER_HERO_ID, _CONFIRMED_SOJ_DONOR_ID

    p = state.pending
    if p.skill_id <= 0:
        return False

    if _pending_confirmed(state, p):
        _log(
            "HERO_KEYSTONE_CAST_CONFIRMED",
            hero_index=state.hero_index,
            bar_index=state.hero_bar_index,
            hero_id=state.hero_agent_id,
            skill_id=p.skill_id,
            slot=p.slot,
            target_id=p.target_id,
            purpose=p.purpose,
        )

        if p.purpose == "setup_celerity":
            state.phase = "posture"
        elif p.purpose == "setup_posture":
            state.phase = "mimicry"
        elif p.purpose == "copy_soj_current_target":
            copied_id = _slot_skill_id(state, state.mimicry_slot)
            donor = int(state.mimicry_target_id or p.target_id or 0)
            _restore_player_target_after_mimicry(state)
            if _MIMICRY_OWNER_HERO_ID == state.hero_agent_id:
                _MIMICRY_OWNER_HERO_ID = 0
            if copied_id == SIGNET_OF_JUDGMENT_ID:
                _CONFIRMED_SOJ_DONOR_ID = donor
                state.phase = "keystone"
                _log(
                    "HERO_KEYSTONE_MIMICRY_SOJ_CONFIRMED",
                    hero_index=state.hero_index,
                    bar_index=state.hero_bar_index,
                    hero_id=state.hero_agent_id,
                    donor_id=donor,
                    copied_skill_id=copied_id,
                )
            else:
                # Never pretend the copy was correct. Wait for the physical
                # Mimicry slot to return before another setup attempt.
                state.phase = "mimicry_wait_base"
                _log(
                    "HERO_KEYSTONE_MIMICRY_WRONG_COPY",
                    hero_index=state.hero_index,
                    bar_index=state.hero_bar_index,
                    hero_id=state.hero_agent_id,
                    donor_id=donor,
                    copied_skill_id=copied_id,
                    expected_skill_id=SIGNET_OF_JUDGMENT_ID,
                )
        elif p.purpose == "setup_keystone":
            state.signets_spent_since_keystone = 0
            state.phase = "mantra"
        elif p.purpose == "setup_mantra":
            state.phase = "attack"
        elif p.purpose.startswith("attack_"):
            state.signets_spent_since_keystone += 1
            state.phase = "attack"

        state.pending = _PendingCommand()
        return False

    timeout_ms = _MIMICRY_COMMAND_TIMEOUT_MS if p.skill_id == ARCANE_MIMICRY_ID else _COMMAND_TIMEOUT_MS
    if now_ms - p.started_ms <= timeout_ms:
        return True

    _log(
        "HERO_KEYSTONE_CAST_TIMEOUT",
        hero_index=state.hero_index,
        bar_index=state.hero_bar_index,
        hero_id=state.hero_agent_id,
        skill_id=p.skill_id,
        slot=p.slot,
        purpose=p.purpose,
        dispatch=state.mimicry_dispatch_result if p.skill_id == ARCANE_MIMICRY_ID else "",
    )

    if p.purpose == "copy_soj_current_target":
        _restore_player_target_after_mimicry(state)
        if _MIMICRY_OWNER_HERO_ID == state.hero_agent_id:
            _MIMICRY_OWNER_HERO_ID = 0
        state.phase = "mimicry"

    state.pending = _PendingCommand()
    return False


def _request_slot(
    state: _ControllerState,
    slot: int,
    skill_id: int,
    target_id: int,
    *,
    purpose: str,
) -> bool:
    if state.pending.skill_id > 0 or not _hero_can_cast(state):
        return False
    if not _can_pay(state, skill_id):
        return False

    now_ms = _now_ms()
    if now_ms - state.last_command_ms < _COMMAND_GUARD_MS:
        return False

    current = _entry_for_slot(state, slot)
    if current is None or int(current[0]) != int(skill_id) or _recharge_data(current[1]) != 0:
        return False

    try:
        # RoJ-controller convention: explicit target, slot, ONE-BASED hero_index.
        SkillBar.HeroUseSkill(int(target_id), int(slot), int(state.hero_index))
    except Exception as exc:
        _log(
            "HERO_KEYSTONE_CAST_ERROR",
            hero_index=state.hero_index,
            bar_index=state.hero_bar_index,
            hero_id=state.hero_agent_id,
            skill_id=skill_id,
            slot=slot,
            target_id=target_id,
            purpose=purpose,
            error=type(exc).__name__,
        )
        return False

    state.pending = _PendingCommand(
        skill_id=int(skill_id),
        slot=int(slot),
        target_id=int(target_id),
        started_ms=now_ms,
        start_recharge=_recharge_data(current[1]),
        purpose=str(purpose),
    )
    state.last_command_ms = now_ms
    _log(
        "HERO_KEYSTONE_CAST_REQUESTED",
        hero_index=state.hero_index,
        bar_index=state.hero_bar_index,
        hero_id=state.hero_agent_id,
        skill_id=skill_id,
        slot=slot,
        target_id=target_id,
        purpose=purpose,
    )
    return True


# ---------------------------------------------------------------------------
# Verified Signet-of-Judgment donor discovery
# ---------------------------------------------------------------------------

def _is_primary_monk(agent_id: int) -> bool:
    try:
        primary, _secondary = Agent.GetProfessionIDs(int(agent_id))
        return _as_int(primary) == _as_int(Profession.Monk)
    except Exception:
        return False


def _alive_in_hero_range(state: _ControllerState, agent_id: int) -> bool:
    try:
        return bool(
            agent_id > 0
            and Agent.IsValid(agent_id)
            and Agent.IsAlive(agent_id)
            and Utils.Distance(Agent.GetXY(state.hero_agent_id), Agent.GetXY(agent_id)) <= _CAST_RANGE
        )
    except Exception:
        return False


def _skills_have_native_soj(skills: tuple[int, ...] | list[int] | set[int]) -> bool:
    skills_tuple = tuple(int(x) for x in skills if int(x or 0) > 0)
    if SIGNET_OF_JUDGMENT_ID <= 0 or SIGNET_OF_JUDGMENT_ID not in skills_tuple:
        return False

    # Hard elite firewall. A native RoJ Monk with a temporary copied SoJ exposes
    # RoJ as another elite and is rejected. Likewise every non-SoJ elite.
    elites: list[int] = []
    for sid in skills_tuple:
        try:
            if Skill.Flags.IsElite(int(sid)):
                elites.append(int(sid))
        except Exception:
            continue

    if elites and not all(sid == SIGNET_OF_JUDGMENT_ID for sid in elites):
        return False
    if RAY_OF_JUDGMENT_ID > 0 and RAY_OF_JUDGMENT_ID in skills_tuple:
        return False
    return True


def _shared_skills(account: Any) -> tuple[int, ...]:
    result: list[int] = []
    try:
        skillbar = getattr(getattr(account, "AgentData", None), "Skillbar", None)
        for item in (getattr(skillbar, "Skills", []) if skillbar is not None else []):
            sid = int(getattr(item, "Id", 0) or 0)
            if sid > 0:
                result.append(sid)
    except Exception:
        pass
    return tuple(result)


def _shared_login(account: Any) -> int:
    try:
        return int(getattr(getattr(account, "AgentData", None), "LoginNumber", 0) or 0)
    except Exception:
        return 0


def _shared_primary_monk(account: Any) -> bool:
    try:
        professions = getattr(getattr(account, "AgentData", None), "Profession", (0, 0))
        return int(professions[0] or 0) == _as_int(Profession.Monk)
    except Exception:
        return False


def _party_primary_monk_ids(state: _ControllerState) -> list[int]:
    """Return live primary-Monk party agents visible on this client.

    This is deliberately profession-only and is used only as a last-resort
    fallback when the party contains EXACTLY ONE primary Monk.  That makes it
    impossible for Arcane Mimicry to fall onto a Keystone Mesmer merely because
    shared-memory skill publication is late.
    """
    result: set[int] = set()

    try:
        for player in Party.GetPlayers() or []:
            login = int(getattr(player, "login_number", 0) or 0)
            aid = int(Party.Players.GetAgentIDByLoginNumber(login) or 0)
            if aid > 0 and _is_primary_monk(aid) and _alive_in_hero_range(state, aid):
                result.add(aid)
    except Exception:
        pass

    for getter_name in ("GetHeroes", "GetHenchmen"):
        try:
            getter = getattr(Party, getter_name)
            for member in getter() or []:
                aid = int(getattr(member, "agent_id", 0) or 0)
                if aid > 0 and _is_primary_monk(aid) and _alive_in_hero_range(state, aid):
                    result.add(aid)
        except Exception:
            continue

    result.discard(int(state.hero_agent_id))
    return sorted(result)


def _verified_soj_donors(state: _ControllerState) -> list[int]:
    """Return deterministic live donors proven to expose native SoJ.

    Sources:
      1) locally readable hero bars (primary Monk + native SoJ only),
      2) other player accounts published through HeroAI shared memory, mapped
         from login number to THIS client's local party AgentID.
    """
    result: set[int] = set()

    # 1) Locally controllable hero bars, using the same one-based position for
    # both agent lookup and skillbar read.
    for hero_position in range(1, 8):
        agent_id = _hero_agent_for_position(hero_position)
        if agent_id <= 0 or agent_id == state.hero_agent_id:
            continue
        if not _is_primary_monk(agent_id) or not _alive_in_hero_range(state, agent_id):
            continue
        if _skills_have_native_soj(tuple(_bar_ids(hero_position))):
            result.add(agent_id)

    # 2) Other player accounts via Shared Memory -- same resolution rule as the
    # working RoJVerifiedTargetMimicry: shared LOGIN -> local party AgentID.
    try:
        from Py4GWCoreLib.HeroAI.utils import SameMapOrPartyAsAccount
    except Exception:
        SameMapOrPartyAsAccount = lambda _account: True

    own_login = int(Player.GetLoginNumber() or 0)
    try:
        own_party = int(GLOBAL_CACHE.Party.GetPartyID() or 0)
    except Exception:
        own_party = 0

    try:
        accounts = GLOBAL_CACHE.ShMem.GetAllAccountData() or []
    except Exception:
        accounts = []

    for account in accounts:
        try:
            if not getattr(account, "IsSlotActive", False) or getattr(account, "IsIsolated", False):
                continue
            if not SameMapOrPartyAsAccount(account):
                continue
            party_id = int(getattr(getattr(account, "AgentPartyData", None), "PartyID", 0) or 0)
            if own_party > 0 and party_id > 0 and party_id != own_party:
                continue
            if not _shared_primary_monk(account):
                continue
            login = _shared_login(account)
            if login <= 0 or login == own_login:
                continue
            skills = _shared_skills(account)
            if not _skills_have_native_soj(skills):
                continue
            agent_id = int(Party.Players.GetAgentIDByLoginNumber(login) or 0)
            if agent_id <= 0:
                continue
            if not _is_primary_monk(agent_id) or not _alive_in_hero_range(state, agent_id):
                continue
            try:
                live_login = int(Agent.GetLoginNumber(agent_id) or 0)
            except Exception:
                live_login = 0
            if live_login > 0 and live_login != login:
                continue
            result.add(agent_id)
        except Exception:
            continue

    # Hard safety fallback for the user's current team topology: if skillbar
    # publication cannot prove the donor but the entire visible party contains
    # exactly ONE primary Monk, that Monk is the only legal Mimicry target.
    # A Keystone Mesmer can therefore never be selected by this fallback.
    if not result:
        unique_monks = _party_primary_monk_ids(state)
        if len(unique_monks) == 1:
            result.add(int(unique_monks[0]))
            _log(
                "HERO_KEYSTONE_UNIQUE_MONK_FALLBACK",
                hero_index=state.hero_index,
                bar_index=state.hero_bar_index,
                hero_id=state.hero_agent_id,
                donor_id=int(unique_monks[0]),
                policy="exactly_one_primary_monk_never_mesmer",
            )

    donors = list(result)
    donors.sort(
        key=lambda aid: (
            Utils.Distance(Agent.GetXY(state.hero_agent_id), Agent.GetXY(aid))
            if Agent.IsValid(aid)
            else 999999.0,
            aid,
        )
    )
    return donors


def _pick_soj_donor(state: _ControllerState) -> int:
    global _CONFIRMED_SOJ_DONOR_ID
    # FAIL CLOSED.  A previously known donor is never reused merely because
    # shared-memory verification is temporarily empty.  Mimicry is allowed only
    # while the exact ally is CURRENTLY proven to be a primary Monk exposing
    # native Signet of Judgment.
    verified = _verified_soj_donors(state)
    if not verified:
        return 0

    confirmed = int(_CONFIRMED_SOJ_DONOR_ID or 0)
    if confirmed > 0 and confirmed in verified:
        return confirmed

    _CONFIRMED_SOJ_DONOR_ID = int(verified[0])
    return int(verified[0])


def _restore_player_target_after_mimicry(state: _ControllerState) -> None:
    old_target = int(state.mimicry_old_player_target or 0)
    state.mimicry_target_id = 0
    state.mimicry_target_started_ms = 0
    state.mimicry_dispatch_result = ""
    state.mimicry_old_player_target = 0
    if old_target > 0:
        try:
            Player.ChangeTarget(old_target)
        except Exception:
            pass


def _request_mimicry_current_target(state: _ControllerState, slot: int, donor: int) -> bool:
    """Use the exact safe dispatch principle from the working RoJ project."""
    if state.pending.skill_id > 0 or not _hero_can_cast(state):
        return False
    if not _can_pay(state, ARCANE_MIMICRY_ID):
        return False

    now_ms = _now_ms()
    if now_ms - state.last_command_ms < _COMMAND_GUARD_MS:
        return False

    current = _entry_for_slot(state, slot)
    if not current or int(current[0]) != ARCANE_MIMICRY_ID or _recharge_data(current[1]) != 0:
        return False
    if not _is_primary_monk(donor) or not _alive_in_hero_range(state, donor):
        return False
    # Hard donor firewall immediately before dispatch.  Empty verification is
    # a rejection, never permission.
    verified_now = _verified_soj_donors(state)
    if int(donor) not in verified_now:
        return False
    try:
        if int(Player.GetTargetID() or 0) != int(donor):
            return False
    except Exception:
        return False

    hero_index = int(state.hero_index)
    donor_id = int(donor)
    slot_i = int(slot)
    hero_id = int(state.hero_agent_id)
    state.mimicry_dispatch_result = "queued"

    def _guarded_hero_mimicry_cast():
        try:
            current_target = int(Player.GetTargetID() or 0)
            if current_target != donor_id:
                state.mimicry_dispatch_result = f"target_changed:{current_target}"
                return
            if not Agent.IsValid(donor_id) or not Agent.IsAlive(donor_id):
                state.mimicry_dispatch_result = "invalid_donor"
                return
            if not _is_primary_monk(donor_id):
                state.mimicry_dispatch_result = "not_primary_monk"
                return
            # Revalidate native SoJ immediately before dispatch.  This is a
            # strict firewall: if verification disappears, do not cast.
            verified = _verified_soj_donors(state)
            if donor_id not in verified:
                state.mimicry_dispatch_result = "donor_not_verified_soj"
                return
            # Use the exact dispatch that the working RoJ hero controller uses:
            # target 0 means CURRENT selected target. The current target is
            # revalidated immediately above and remains locked until the Mimicry
            # slot transforms, so only the verified SoJ Monk can be copied.
            PySkillbar.Skillbar().HeroUseSkill(0, slot_i, hero_index)
            state.mimicry_dispatch_result = "issued"
        except Exception as exc:
            state.mimicry_dispatch_result = f"error:{type(exc).__name__}"
            _log(
                "HERO_KEYSTONE_MIMICRY_DISPATCH_ERROR",
                hero_index=hero_index,
                hero_id=hero_id,
                donor_id=donor_id,
                error=type(exc).__name__,
            )

    try:
        PyGameThread.enqueue(_guarded_hero_mimicry_cast)
    except Exception as exc:
        _log(
            "HERO_KEYSTONE_MIMICRY_QUEUE_ERROR",
            hero_index=state.hero_index,
            bar_index=state.hero_bar_index,
            hero_id=state.hero_agent_id,
            error=type(exc).__name__,
        )
        return False

    state.pending = _PendingCommand(
        skill_id=ARCANE_MIMICRY_ID,
        slot=slot_i,
        target_id=donor_id,
        started_ms=now_ms,
        start_recharge=_recharge_data(current[1]),
        purpose="copy_soj_current_target",
    )
    state.last_command_ms = now_ms
    _log(
        "HERO_KEYSTONE_MIMICRY_CURRENT_TARGET_QUEUED",
        hero_index=state.hero_index,
        bar_index=state.hero_bar_index,
        hero_id=state.hero_agent_id,
        donor_id=donor_id,
        current_target=int(Player.GetTargetID() or 0),
        policy="verified_primary_monk_native_soj_current_target_then_target0_locked_until_transform",
    )
    return True


def _try_mimicry(state: _ControllerState) -> bool:
    global _MIMICRY_OWNER_HERO_ID

    # If the physical slot is already the correct copy, there is nothing to cast.
    current_id = _slot_skill_id(state, state.mimicry_slot) if state.mimicry_slot > 0 else 0
    if current_id == SIGNET_OF_JUDGMENT_ID:
        state.phase = "keystone"
        return False
    if current_id not in (0, ARCANE_MIMICRY_ID):
        # Mimicry is an optional enhancement, never a hard gate.  A stale or
        # wrong temporary copy must not freeze Keystone/Mantra/native signets.
        state.phase = "keystone"
        return False

    entry = _ready_entry(state, ARCANE_MIMICRY_ID)
    if entry is None:
        return False

    # One controller may own the shared player target at a time. This prevents
    # three Keystone heroes from stealing the donor target from each other.
    if _MIMICRY_OWNER_HERO_ID not in (0, state.hero_agent_id):
        return False

    donor = _pick_soj_donor(state)
    if donor <= 0:
        _log(
            "HERO_KEYSTONE_NO_VERIFIED_SOJ_DONOR",
            hero_index=state.hero_index,
            bar_index=state.hero_bar_index,
            hero_id=state.hero_agent_id,
        )
        return False

    now_ms = _now_ms()
    if int(state.mimicry_target_id or 0) != int(donor):
        _MIMICRY_OWNER_HERO_ID = int(state.hero_agent_id)
        try:
            state.mimicry_old_player_target = int(Player.GetTargetID() or 0)
        except Exception:
            state.mimicry_old_player_target = 0
        state.mimicry_target_id = int(donor)
        state.mimicry_target_started_ms = now_ms
        state.mimicry_dispatch_result = "targeting"
        try:
            Player.ChangeTarget(int(donor))
        except Exception:
            pass
        _log(
            "HERO_KEYSTONE_MIMICRY_TARGETING_SOJ_MONK",
            hero_index=state.hero_index,
            bar_index=state.hero_bar_index,
            hero_id=state.hero_agent_id,
            donor_id=int(donor),
            previous_target=int(state.mimicry_old_player_target),
        )
        return True

    try:
        current_target = int(Player.GetTargetID() or 0)
    except Exception:
        current_target = 0

    if current_target != int(donor):
        if now_ms - int(state.mimicry_target_started_ms or now_ms) > _MIMICRY_TARGET_TIMEOUT_MS:
            _log(
                "HERO_KEYSTONE_MIMICRY_TARGET_ABORT",
                hero_index=state.hero_index,
                bar_index=state.hero_bar_index,
                hero_id=state.hero_agent_id,
                donor_id=int(donor),
                current_target=current_target,
            )
            _restore_player_target_after_mimicry(state)
            if _MIMICRY_OWNER_HERO_ID == state.hero_agent_id:
                _MIMICRY_OWNER_HERO_ID = 0
            return False
        try:
            Player.ChangeTarget(int(donor))
        except Exception:
            pass
        return True

    if now_ms - int(state.mimicry_target_started_ms or now_ms) < _MIMICRY_TARGET_STABLE_MS:
        return True

    return _request_mimicry_current_target(state, int(entry[0]), int(donor))


# ---------------------------------------------------------------------------
# Combat target / Keystone rotation
# ---------------------------------------------------------------------------

def _distance(a_id: int, b_id: int) -> float:
    try:
        return float(Utils.Distance(Agent.GetXY(int(a_id)), Agent.GetXY(int(b_id))))
    except Exception:
        return 999999.0


def _precast_proximity(state: _ControllerState) -> tuple[bool, bool]:
    """One-pass enemy proximity gate matching the real-account setup ranges.

    Origins include the actual owned hero, its owner player and the party leader.
    One enemy-array scan is shared by both the 90% Celerity gate and the tighter
    78% Posture/Mimicry/Keystone gate to keep per-frame overhead tiny.
    """
    origins: list[tuple[float, float]] = []
    for getter in (
        lambda: Agent.GetXY(int(state.hero_agent_id)),
        lambda: Player.GetXY(),
    ):
        try:
            xy = getter()
            if xy:
                origins.append((float(xy[0]), float(xy[1])))
        except Exception:
            pass
    try:
        leader_id = int(Party.GetPartyLeaderID() or 0)
        if leader_id > 0 and Agent.IsValid(leader_id):
            xy = Agent.GetXY(leader_id)
            if xy:
                origins.append((float(xy[0]), float(xy[1])))
    except Exception:
        pass

    if not origins:
        return False, False

    symbolic_sq = float(_SYMBOLIC_PRECAST_SETUP_RANGE) ** 2
    precombat_sq = float(_KEYSTONE_PRECAST_SETUP_RANGE) ** 2
    symbolic = False
    try:
        for enemy_id in AgentArray.GetEnemyArray() or []:
            enemy_id = int(enemy_id or 0)
            try:
                if enemy_id <= 0 or not Agent.IsValid(enemy_id) or not Agent.IsAlive(enemy_id):
                    continue
                ex, ey = Agent.GetXY(enemy_id)
                ex = float(ex)
                ey = float(ey)
                for ox, oy in origins:
                    dx = ox - ex
                    dy = oy - ey
                    dist_sq = dx * dx + dy * dy
                    if dist_sq <= precombat_sq:
                        return True, True
                    if dist_sq <= symbolic_sq:
                        symbolic = True
            except Exception:
                continue
    except Exception:
        return False, False
    return bool(symbolic), False


def _is_elite_priority_target(agent_id: int) -> bool:
    try:
        name = str(Agent.GetNameByID(int(agent_id)) or "").strip().lower()
    except Exception:
        return False
    return any(token in name for token in _ELITE_PRIORITY_TARGET_NAMES)


def _small_party_focus_mode() -> bool:
    try:
        party_size = int(Party.GetPartySize() or 0)
    except Exception:
        party_size = 0
    return 2 <= party_size <= 8


def _keystone_hero_lane(state: _ControllerState) -> tuple[int, int]:
    ordered = sorted(
        (int(s.hero_index), int(s.hero_agent_id))
        for s in _STATES.values()
        if int(s.hero_agent_id or 0) > 0
    )
    ids = [aid for _idx, aid in ordered]
    try:
        return int(ids.index(int(state.hero_agent_id))), max(1, len(ids))
    except ValueError:
        return max(0, int(state.hero_index) - 1), max(1, len(ids) or 1)


def _distributed_packet_target(state: _ControllerState, anchor: int, skill_id: int) -> int:
    """Spread owned heroes inside one shared packet, never outside it.

    This is deliberately deterministic and local.  It gives the three owned
    heroes the same practical 3-way staggering that the real Keystone accounts
    obtain from shared target claims, without adding another cross-account lock
    layer to the already stable hero controller.
    """
    anchor = int(anchor or 0)
    if anchor <= 0 or _is_elite_priority_target(anchor):
        return anchor
    try:
        from Py4GWCoreLib.Builds.Skills import HeroClusterCoordinator
        members = [
            int(aid) for aid in HeroClusterCoordinator.cluster_members(
                anchor,
                origin_agent_id=int(state.hero_agent_id),
                radius=_NEARBY,
                range_value=_CAST_RANGE,
            )
            if _valid_enemy(state, int(aid))
        ]
    except Exception:
        members = []
    if len(members) <= 1:
        return anchor

    if _small_party_focus_mode():
        # 8-man experimental profile: concentrate the Keystone heroes on at
        # most two packet members. Do not rotate every signet to a new enemy;
        # consistent lane ownership produces the harder spike seen in the older
        # build while still leaving one secondary lane when several Keystones
        # are present.
        primary = int(anchor) if int(anchor) in members else int(members[0])
        try:
            hp = float(Agent.GetHealth(primary) or 0.0)
        except Exception:
            hp = 1.0
        if hp <= 0.12 and len(members) > 1:
            primary = next((int(x) for x in members if int(x) != primary), primary)
        secondary = next((int(x) for x in members if int(x) != primary), 0)
        if secondary <= 0:
            return primary
        lane, count = _keystone_hero_lane(state)
        if count <= 1:
            return primary
        if count == 2:
            return primary if lane % 2 == 0 else secondary
        return secondary if lane % 4 == 2 else primary

    # 12-man/default profile: retain v33 wide packet distribution. Hero lanes
    # and a per-skill offset rotate successive signets to minimize overkill.
    offset = int(_SKILL_TARGET_OFFSETS.get(int(skill_id), 0))
    index = (max(1, int(state.hero_index)) - 1 + offset) % len(members)
    return int(members[index])


def _valid_enemy(state: _ControllerState, enemy_id: int) -> bool:
    try:
        return bool(
            enemy_id > 0
            and Agent.IsValid(enemy_id)
            and Agent.IsAlive(enemy_id)
            and _distance(state.hero_agent_id, enemy_id) <= _CAST_RANGE
        )
    except Exception:
        return False


def _combat_target(state: _ControllerState, skill_id: int = 0) -> int:
    # Same authoritative shared cluster as the player accounts.  In packet mode
    # the owned heroes then distribute only *inside* that packet.
    try:
        from Py4GWCoreLib.Builds.Skills import HeroClusterCoordinator
        anchor = int(
            HeroClusterCoordinator.get_shared_cluster_anchor(
                origin_agent_id=state.hero_agent_id,
                range_value=_CAST_RANGE,
                minimum_enemies=2,
            )
            or 0
        )
        if _valid_enemy(state, anchor):
            return _distributed_packet_target(state, anchor, int(skill_id))
    except Exception:
        pass

    try:
        from Py4GWCoreLib.Builds.Skills import CombatSense
        anchor = int(
            CombatSense.pick_single_target_anchor(
                range_value=_CAST_RANGE,
                assignment_slot=max(0, int(state.hero_index) - 1),
                consumer_role="hero_keystone",
            )
            or 0
        )
        if _valid_enemy(state, anchor):
            return anchor
    except Exception:
        pass

    # Low-tech final fallback: nearest live enemy in cast range.
    try:
        enemies = [
            int(aid)
            for aid in (AgentArray.GetEnemyArray() or [])
            if _valid_enemy(state, int(aid))
        ]
        enemies.sort(key=lambda aid: _distance(state.hero_agent_id, aid))
        return int(enemies[0]) if enemies else 0
    except Exception:
        return 0


def _try_self_skill(state: _ControllerState, skill_id: int, slot: int, purpose: str) -> bool:
    if slot <= 0:
        return False
    entry = _entry_for_slot(state, slot)
    if not entry or int(entry[0]) != int(skill_id) or _recharge_data(entry[1]) != 0:
        return False
    return _request_slot(state, slot, skill_id, state.hero_agent_id, purpose=purpose)


def _unnatural_combat_target(state: _ControllerState) -> int:
    """Hex-first native Keystone target with deterministic no-wait spreading.

    Owned Keystone heroes do not use the account target-claim layer, so their
    hero lane provides deterministic spread.  If a shared packet contains hexed
    enemies, distribute Unnatural among those.  With no hexed enemy, preserve
    the exact pre-Stage-19 _combat_target behavior immediately.
    """
    try:
        from Py4GWCoreLib.Builds.Skills import HeroClusterCoordinator
        anchor = int(HeroClusterCoordinator.get_shared_cluster_anchor(
            origin_agent_id=int(state.hero_agent_id),
            range_value=_CAST_RANGE,
            minimum_enemies=2,
        ) or 0)
        if _valid_enemy(state, anchor):
            members = [
                int(aid) for aid in HeroClusterCoordinator.cluster_members(
                    anchor,
                    origin_agent_id=int(state.hero_agent_id),
                    radius=_NEARBY,
                    range_value=_CAST_RANGE,
                )
                if _valid_enemy(state, int(aid))
            ]
            hexed = []
            for aid in members:
                try:
                    if Agent.IsHexed(int(aid)):
                        hexed.append(int(aid))
                except Exception as exc:
                    try:
                        from Py4GWCoreLib.Builds.Skills import CombatDebug
                        CombatDebug.log_guarded_exception(
                            "keystone_hero_hex_probe",
                            exc,
                            target_id=int(aid or 0),
                            hero_agent_id=int(getattr(state, "hero_agent_id", 0) or 0),
                        )
                    except Exception:
                        pass
                    continue
            if hexed:
                # Better splash density first; lane rotation spreads equal-good
                # choices across owned Keystone heroes without blocking a cast.
                hexed.sort(key=lambda aid: (
                    -sum(1 for other in members if _distance(int(aid), int(other)) <= _ADJACENT),
                    _distance(state.hero_agent_id, int(aid)),
                    int(aid),
                ))
                lane, _count = _keystone_hero_lane(state)
                return int(hexed[int(lane) % len(hexed)])
    except Exception as exc:
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_guarded_exception(
                "keystone_hero_unnatural_target",
                exc,
                hero_agent_id=int(getattr(state, "hero_agent_id", 0) or 0),
            )
        except Exception:
            pass
    return int(_combat_target(state, UNNATURAL_SIGNET_ID) or 0)


def _try_offensive_signet(state: _ControllerState, skill_id: int, purpose: str) -> bool:
    entry = _ready_entry(state, skill_id)
    if entry is None:
        return False
    target = (
        _unnatural_combat_target(state)
        if int(skill_id) == int(UNNATURAL_SIGNET_ID)
        else _combat_target(state, int(skill_id))
    )
    if target <= 0:
        return False
    if int(skill_id) == int(UNNATURAL_SIGNET_ID):
        try:
            _log(
                "HERO_KEYSTONE_UNNATURAL_TARGET",
                hero_index=int(state.hero_index), hero_id=int(state.hero_agent_id),
                target_id=int(target), hexed=bool(Agent.IsHexed(int(target))),
                policy="stage19_hex_first_lane_spread_no_wait_else_original_target",
            )
        except Exception:
            pass
    return _request_slot(state, int(entry[0]), int(skill_id), int(target), purpose=purpose)


def _all_base_offensive_signets_unready(state: _ControllerState) -> bool:
    for sid in (UNNATURAL_SIGNET_ID, SIGNET_OF_SORROW_ID, SIGNET_OF_LOST_SOULS_ID):
        if sid > 0 and _ready_entry(state, sid) is not None:
            return False
    # Copied SoJ counts too while Mimicry is transformed.
    if SIGNET_OF_JUDGMENT_ID > 0 and _ready_entry(state, SIGNET_OF_JUDGMENT_ID) is not None:
        return False
    return True


def _reset_phase_for_next_keystone(state: _ControllerState) -> None:
    # Symbolic Celerity is long-duration setup; only refresh it when its effect
    # actually disappeared. Every Keystone reset itself begins with Posture.
    if not _has_effect(state, SYMBOLIC_CELERITY_ID):
        state.phase = "setup"
    else:
        state.phase = "posture"


def _run_state(state: _ControllerState, now_ms: int) -> None:
    # EXP36 Stage 8: Mimicry is the only slot whose wrong autonomous cast can
    # change the skill identity itself. Reassert this one lock even on frames
    # skipped by the normal 90 ms controller scan. The globals() lookup keeps
    # the historical isolated-function regression harness compatible.
    hard_lock = globals().get("_hard_lock_mimicry_slot")
    if hard_lock is not None and not hard_lock(state):
        return
    if now_ms - state.last_scan_ms < _SCAN_INTERVAL_MS:
        return
    state.last_scan_ms = now_ms

    if not _ensure_locked(state, now_ms):
        return
    if _process_pending(state, now_ms):
        return
    if not _hero_can_cast(state):
        return

    # Keep the learned Mimicry physical slot safe while it is transformed.
    # IMPORTANT: Mimicry is OPTIONAL for the base Keystone rotation.  A stale,
    # wrong or cooling-down Mimicry slot is observed/logged, but it must never
    # force the whole hero into a wait state.
    mimicry_current = _slot_skill_id(state, state.mimicry_slot) if state.mimicry_slot > 0 else 0
    if mimicry_current not in (0, ARCANE_MIMICRY_ID, SIGNET_OF_JUDGMENT_ID):
        if int(state.unexpected_mimicry_logged_id or 0) != int(mimicry_current):
            state.unexpected_mimicry_logged_id = int(mimicry_current)
            _log(
                "HERO_KEYSTONE_MIMICRY_SLOT_UNEXPECTED",
                hero_index=state.hero_index,
                bar_index=state.hero_bar_index,
                hero_id=state.hero_agent_id,
                copied_skill_id=mimicry_current,
                policy="ignore_for_base_rotation_until_arcane_mimicry_returns",
            )
    else:
        state.unexpected_mimicry_logged_id = 0

    symbolic_setup, precombat_setup = _precast_proximity(state)
    proximity_stage = "precombat" if precombat_setup else ("symbolic" if symbolic_setup else "far")
    if proximity_stage != str(state.proximity_stage or ""):
        state.proximity_stage = str(proximity_stage)
        _log(
            "HERO_KEYSTONE_PRECAST_GATE",
            hero_index=state.hero_index,
            bar_index=state.hero_bar_index,
            hero_id=state.hero_agent_id,
            stage=proximity_stage,
            symbolic_range=round(float(_SYMBOLIC_PRECAST_SETUP_RANGE), 1),
            precombat_range=round(float(_KEYSTONE_PRECAST_SETUP_RANGE), 1),
            policy="real_account_90pct_celerity_78pct_posture_mimicry_keystone",
        )

    phase = state.phase or "setup"

    # Missing/stripped Celerity is an urgent damage-precondition regardless
    # of phase. Optional <=20s early renewal only pre-pull; in attack phase
    # wait until offensive signets have been probed below.
    if symbolic_setup and _try_hero_prebuff(
        state, SYMBOLIC_CELERITY_ID, state.celerity_slot, now_ms,
        'celerity_missing_or_precombat_20s', allow_renew=(phase != 'attack'),
    ):
        return

    # 1) Symbolic Celerity: same wider pre-pull gate as the real accounts.
    if phase == "setup":
        if not symbolic_setup:
            return
        if _has_effect(state, SYMBOLIC_CELERITY_ID):
            state.phase = "posture"
            return
        if _try_self_skill(state, SYMBOLIC_CELERITY_ID, state.celerity_slot, "setup_celerity"):
            return
        return

    # 2) Symbolic Posture is preferred immediately before Keystone, but it must
    # not create a long idle gap.  Symbolic Celerity can make Keystone become
    # ready noticeably before this stance.  If Posture is <=2.5s away, wait and
    # keep the ideal Posture -> Mimicry -> Keystone sequence.  If it is farther
    # away AND Keystone is already ready, skip Posture for this cycle and cast
    # Keystone immediately.  The next cycle evaluates Posture again normally.
    if phase == "posture":
        # Do not spend the short setup chain while still crossing empty terrain.
        # Celerity may already be up from the wider 90% gate; everything from
        # Posture onward waits for the tighter 78% near-contact gate.
        if not precombat_setup:
            return
        if _has_effect(state, SYMBOLIC_POSTURE_ID):
            state.phase = "mimicry"
            return

        posture_entry = _entry_for_slot(state, state.posture_slot)
        posture_remaining_ms = -1
        if posture_entry is not None and int(posture_entry[0]) == SYMBOLIC_POSTURE_ID:
            posture_remaining_ms = _remaining_recharge_ms(posture_entry[1])

        if posture_remaining_ms == 0:
            if _try_self_skill(state, SYMBOLIC_POSTURE_ID, state.posture_slot, "setup_posture"):
                return
            return

        keystone_ready = _ready_entry(state, KEYSTONE_SIGNET_ID) is not None
        if (
            keystone_ready
            and posture_remaining_ms > _POSTURE_WAIT_THRESHOLD_MS
        ):
            _log(
                "HERO_KEYSTONE_POSTURE_OPTIONAL_SKIP",
                hero_index=state.hero_index,
                bar_index=state.hero_bar_index,
                hero_id=state.hero_agent_id,
                posture_remaining_ms=posture_remaining_ms,
                wait_threshold_ms=_POSTURE_WAIT_THRESHOLD_MS,
                reason="keystone_ready_posture_too_far_away",
                next_phase="keystone",
            )
            # Fast fallback cycle: do not add a fresh Mimicry transaction here.
            # If SoJ is already copied it can still be spent in attack; otherwise
            # this is simply Keystone -> Mantra -> native signets.
            state.phase = "keystone"
            return

        # Posture is close enough to readiness (or remaining time is unknown),
        # so preserve the ideal sequence instead of throwing away the setup for
        # only a very short wait.
        return

    # 3) Arcane Mimicry is an OPTIONAL bonus step.
    # If SoJ is already copied, continue immediately. If native Mimicry is
    # ready, try the proven verified-SoJ transaction. If Mimicry is cooling
    # down, unavailable, has a stale/wrong copy, lacks energy/donor, etc., do
    # NOT wait: continue the normal Keystone rotation without SoJ.
    if phase == "mimicry":
        if not precombat_setup:
            return
        if mimicry_current == SIGNET_OF_JUDGMENT_ID:
            state.phase = "keystone"
            return

        if mimicry_current == ARCANE_MIMICRY_ID:
            mimicry_ready = _ready_entry(state, ARCANE_MIMICRY_ID) is not None
            if mimicry_ready:
                # Preserve sequential target ownership while another hero is in
                # the middle of a verified Mimicry transaction. This wait is
                # transaction-length only, never cooldown-length.
                if _MIMICRY_OWNER_HERO_ID not in (0, state.hero_agent_id):
                    return
                if _try_mimicry(state):
                    return
                reason = "ready_but_not_started"
            else:
                reason = "cooldown_or_unready"
        elif mimicry_current == 0:
            reason = "slot_temporarily_unreadable"
        else:
            reason = f"non_soj_copy_{int(mimicry_current)}"

        _log(
            "HERO_KEYSTONE_MIMICRY_OPTIONAL_SKIP",
            hero_index=state.hero_index,
            bar_index=state.hero_bar_index,
            hero_id=state.hero_agent_id,
            mimicry_slot=state.mimicry_slot,
            mimicry_current=mimicry_current,
            reason=reason,
            next_phase="keystone",
        )
        state.phase = "keystone"
        return

    # Compatibility with any state left by an older hot-reloaded controller:
    # the old wait-base state is now explicitly non-blocking.
    if phase == "mimicry_wait_base":
        if not precombat_setup:
            return
        _log(
            "HERO_KEYSTONE_MIMICRY_WAIT_BASE_BYPASSED",
            hero_index=state.hero_index,
            bar_index=state.hero_bar_index,
            hero_id=state.hero_agent_id,
            mimicry_current=mimicry_current,
            next_phase="keystone",
        )
        state.phase = "keystone"
        return

    # 4) Keystone Signet consumes Symbolic Posture and establishes the signet
    # reset window. This is the same ordering used by the player Keystone logic:
    # Posture -> Mimicry -> Keystone -> Mantra -> offensive signets.
    if phase == "keystone":
        if not precombat_setup and _combat_target(state, KEYSTONE_SIGNET_ID) <= 0:
            return
        if _try_self_skill(state, KEYSTONE_SIGNET_ID, state.keystone_slot, "setup_keystone"):
            return
        # If Keystone is already active from a prior confirmed cast, continue.
        if _has_effect(state, KEYSTONE_SIGNET_ID):
            state.phase = "mantra"
        return

    # 5) Mantra comes AFTER Symbolic Posture/Keystone because both Posture and
    # Mantra are stances; casting Mantra earlier would cancel Posture.
    # EXP36/E05: Mantra is preferred, but it is no longer allowed to create an
    # unbounded hero idle state. If the stance cannot be started right now
    # (cooldown, energy, transient dispatch failure), continue the offensive
    # base cycle and recover Mantra later in a genuinely free attack window.
    if phase == "mantra":
        if not precombat_setup and _combat_target(state, MANTRA_OF_SIGNETS_ID) <= 0:
            return
        if _has_effect(state, MANTRA_OF_SIGNETS_ID):
            # Keep the existing stance through the opening. Its 20s advance
            # renewal gets the next *free attack* window, not first damage.
            state.phase = "attack"
            return
        if _try_hero_prebuff(
            state, MANTRA_OF_SIGNETS_ID, state.mantra_slot, now_ms,
            'missing_mantra_after_keystone', allow_renew=False,
        ):
            return
        _log(
            "HERO_KEYSTONE_MANTRA_OPTIONAL_SKIP",
            hero_index=state.hero_index,
            bar_index=state.hero_bar_index,
            hero_id=state.hero_agent_id,
            reason="mantra_not_executable_now",
            next_phase="attack",
            policy="exp36_e05_no_unbounded_mantra_idle",
        )
        state.phase = "attack"
        return

    # 6) Spend the copied SoJ first, then the three native damage/energy signets.
    if phase == "attack":
        # Missing/<=6s Mantra: a READY cast outranks ordinary attack signets.
        # Unknown effect timers and recharging skills fall through immediately
        # to offensive casting, never entering a cooldown wait state.
        mantra_remaining = _buff_remaining_ms(state, MANTRA_OF_SIGNETS_ID)
        if (mantra_remaining is not None
            and mantra_remaining <= _MANTRA_CRITICAL_RENEW_MS
            and not _has_effect(state, SYMBOLIC_POSTURE_ID)
            and _try_hero_prebuff(
                state, MANTRA_OF_SIGNETS_ID, state.mantra_slot, now_ms,
                'critical_mantra_6s_before_offense', allow_renew=True,
            )):
            _log('HERO_KEYSTONE_MANTRA_CRITICAL_RENEW',
                 hero_id=state.hero_agent_id, remaining_ms=mantra_remaining,
                 threshold_ms=_MANTRA_CRITICAL_RENEW_MS,
                 policy='ready_now_no_cooldown_wait')
            return
        if _try_offensive_signet(state, SIGNET_OF_JUDGMENT_ID, "attack_copied_soj"):
            return
        if _try_offensive_signet(state, UNNATURAL_SIGNET_ID, "attack_unnatural"):
            return
        if _try_offensive_signet(state, SIGNET_OF_SORROW_ID, "attack_sorrow"):
            return
        if _try_offensive_signet(state, SIGNET_OF_LOST_SOULS_ID, "attack_lost_souls"):
            return

        # Only after every offensive signet got first refusal: renew active
        # prebuffs when their VERIFIED timer is <=20s, never on a blind 10s
        # cycle. A truly missing Celerity was already handled ahead of offense.
        if _try_hero_prebuff(
            state, SYMBOLIC_CELERITY_ID, state.celerity_slot, now_ms,
            'free_window_celerity_20s', allow_renew=True,
        ):
            return
        if _try_hero_prebuff(
            state, MANTRA_OF_SIGNETS_ID, state.mantra_slot, now_ms,
            'free_window_mantra_20s', allow_renew=True,
        ):
            return

        # Do not idle for Arcane Mimicry or the copied elite. Keystone can begin
        # another BASE rotation whenever it is ready and at least one signet was
        # actually spent. The next Mimicry phase will opportunistically copy SoJ
        # only if Mimicry is ready at that moment.
        keystone_ready = _ready_entry(state, KEYSTONE_SIGNET_ID) is not None
        if (
            state.signets_spent_since_keystone > 0
            and keystone_ready
            and _all_base_offensive_signets_unready(state)
        ):
            _reset_phase_for_next_keystone(state)
            return

        # Fallback: if no offensive signet is currently ready but Keystone is,
        # arm the next base cycle regardless of Mimicry cooldown.  This removes
        # the old hard dependency on Arcane Mimicry being ready.
        if keystone_ready and _all_base_offensive_signets_unready(state):
            _reset_phase_for_next_keystone(state)
        return

    state.phase = "setup"


def _restore_all() -> None:
    global _MIMICRY_OWNER_HERO_ID, _CONFIRMED_SOJ_DONOR_ID, _MIMICRY_SLOT_TEMPLATE, _LAST_NO_MATCH_SIGNATURE
    for state in list(_STATES.values()):
        _restore_native_ai(state)
    _STATES.clear()
    _DISCOVERED_HEROES.clear()
    _PREBUFF_LAST_CAST.clear()
    _MIMICRY_OWNER_HERO_ID = 0
    _CONFIRMED_SOJ_DONOR_ID = 0
    _MIMICRY_SLOT_TEMPLATE = 0
    _LAST_NO_MATCH_SIGNATURE = ""


def run(*, enabled: bool = True) -> None:
    """Per-frame entry point. Called directly from the active HeroAI runtime hook."""
    global _LAST_MAP_ID, _RUNTIME_LOGGED, _ENTRY_LOGGED, _LAST_NO_MATCH_LOG_MS, _LAST_NO_MATCH_SIGNATURE

    if not _ENTRY_LOGGED:
        _ENTRY_LOGGED = True
        _log("HERO_KEYSTONE_RUN_ENTER")
        _flush_debug()

    if not enabled:
        _restore_all()
        return

    try:
        map_id = int(Map.GetMapID() or 0)
        explorable = bool(Map.IsExplorable())
        party_loaded = bool(Party.IsPartyLoaded())
        if not explorable or not party_loaded:
            _restore_all()
            return
        # Do NOT require this client to be party leader.
    except Exception as exc:
        _log("HERO_KEYSTONE_ENV_ERROR", error=type(exc).__name__, detail=str(exc)[:180])
        _flush_debug()
        return

    if _LAST_MAP_ID and map_id != _LAST_MAP_ID:
        _restore_all()
    _LAST_MAP_ID = map_id

    if not _RUNTIME_LOGGED:
        _RUNTIME_LOGGED = True
        _log("HERO_KEYSTONE_CONTROLLER_ACTIVE", map_id=map_id)
        _flush_debug()

    states = _reconcile_states()
    if not states:
        # EXP36 Stage 7 / E19b: no-match diagnostics are state-change driven.
        # Stage 5 aggregated the row but still emitted the identical snapshot
        # every two seconds. Build a stable map/hero/bar/reason signature and
        # emit only when that observable controller-match state changes.
        bars = []
        diag_rows = []
        signature_rows = []
        hero_count = 0
        for pos in range(1, 8):
            hid = _hero_agent_for_position(pos)
            ids_ordered = [sid for _slot, sid, _data in _bar_entries(pos) if sid > 0]
            if hid > 0 or ids_ordered:
                hero_count += 1
                bars.append(f"p{pos}:h{hid}:" + ",".join(map(str, ids_ordered)))
                ids_set = set(int(x) for x in ids_ordered if int(x or 0) > 0)
                primary_id = 0
                try:
                    primary, _secondary = Agent.GetProfessionIDs(int(hid)) if hid > 0 else (0, 0)
                    primary_id = _as_int(primary)
                    is_mesmer = int(primary_id) == _as_int(Profession.Mesmer)
                except Exception:
                    is_mesmer = False
                missing = sorted(int(x) for x in _FIXED_KEYSTONE_IDS if int(x) not in ids_set)
                if hid <= 0:
                    reject_reason = "no_hero_agent"
                elif not is_mesmer:
                    reject_reason = "wrong_primary_profession"
                elif len(ids_ordered) < 8:
                    reject_reason = "incomplete_skillbar_snapshot"
                elif missing:
                    reject_reason = "missing_fixed_keystone_skills"
                else:
                    reject_reason = "not_discovered_or_transient_mapping"
                diag_rows.append(
                    f"p{int(pos)}:h{int(hid)}:{str(reject_reason)}:missing="
                    + ",".join(map(str, missing))
                )
                # EXP36 Stage 14 / E19c: once the primary profession already
                # proves that a hero cannot be a Keystone Mesmer, temporary
                # RoJ/Mimicry/Echo slot mutations are irrelevant to match state.
                # Keep the full bar in the diagnostic payload, but exclude it
                # from the dedupe signature for wrong-profession rejects.
                if str(reject_reason) == "wrong_primary_profession":
                    signature_rows.append(
                        f"p{int(pos)}:h{int(hid)}:primary={int(primary_id)}:{str(reject_reason)}"
                    )
                else:
                    signature_rows.append(
                        f"p{int(pos)}:h{int(hid)}:primary={int(primary_id)}:{str(reject_reason)}:"
                        + ",".join(map(str, ids_ordered))
                        + ":missing=" + ",".join(map(str, missing))
                    )
        bars_text = ";".join(bars)
        reject_text = ";".join(diag_rows)
        signature_text = ";".join(signature_rows)
        try:
            from Py4GWCoreLib.Builds.Skills import CombatSession
            session_key = str(CombatSession.refresh().key)
        except Exception:
            session_key = f"map={int(map_id)}"
        diag_signature = (
            f"session={session_key}|heroes={int(hero_count)}|state={signature_text}|"
            f"required={','.join(map(str, sorted(_FIXED_KEYSTONE_IDS)))}"
        )
        emit_no_match_diag = False
        if diag_signature != str(_LAST_NO_MATCH_SIGNATURE or ""):
            emit_no_match_diag = True
        try:
            from Py4GWCoreLib.Builds.Skills import CombatSession
            # Stage 20 / E19 final: the user-facing NO_MATCH diagnostic is a
            # session-level fact, not a bar/profession-snapshot stream.  Stage 19
            # could still emit twice when a hidden signature component changed
            # while the visible reject payload remained identical.  Key only on
            # CombatSession so the contract is literally one NO_MATCH per session.
            semantic_token = "keystone_no_match|session=" + str(session_key)
            emit_no_match_diag = bool(
                CombatSession.diagnostic_once(semantic_token)
                and _process_persistent_no_match_once(semantic_token)
            )
        except Exception:
            pass
        if emit_no_match_diag:
            _LAST_NO_MATCH_SIGNATURE = str(diag_signature)
            _LAST_NO_MATCH_LOG_MS = _now_ms()
            _log(
                "HERO_KEYSTONE_NO_MATCH",
                hero_count=hero_count,
                required=",".join(map(str, sorted(_NATIVE_REQUIRED_IDS))),
                fixed=",".join(map(str, sorted(_FIXED_KEYSTONE_IDS))),
                mimicry_slot_template=_MIMICRY_SLOT_TEMPLATE,
                bars=bars_text[:1200],
                mapping_policy="GetHeroSkillbar(N)->HeroUseSkill(N), N=1..7",
            )
            _log(
                "HEROAI_CONTROLLER_MATCH_DIAG",
                owner_kind="native_hero_group",
                owner_id=0,
                intended_role="keystone_mesmer",
                selected_controller="KeystoneMesmerHeroController",
                matched=False,
                reject_reason="no_native_keystone_match",
                reject_summary=reject_text[:1600],
                required_skill_ids=",".join(map(str, sorted(_FIXED_KEYSTONE_IDS))),
                # Legacy Stage-7 regression marker retained intentionally:
                # policy="exp36_stage7_e19b_state_change_only"
                # Legacy cumulative marker: exp36_stage18_e19e_process_persistent_session_semantic_dedupe
                policy="exp36_stage20_e19_final_one_no_match_per_combat_session",
                # Legacy cumulative marker: exp36_stage14_e19c_reject_semantics_not_transient_bar_noise
            )
            _flush_debug()
        return

    # A later transition back to no-match remains observable only when the
    # semantic reject itself changed; Stage 17 keeps identical rejects deduped
    # sessionwide in CombatSession across transient controller rebuilds.
    if _LAST_NO_MATCH_SIGNATURE:
        _LAST_NO_MATCH_SIGNATURE = ""

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
            _log(
                "HERO_KEYSTONE_CONTROLLER_ERROR",
                hero_index=state.hero_index,
                bar_index=state.hero_bar_index,
                hero_id=state.hero_agent_id,
                error=type(exc).__name__,
                detail=str(exc)[:200],
            )
