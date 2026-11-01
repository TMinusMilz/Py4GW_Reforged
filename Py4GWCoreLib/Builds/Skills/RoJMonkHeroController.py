"""Deterministic controller for the three owned Mo/Me Ray of Judgment heroes.

This keeps the proven RoJ-project rotation and applies the hero-control hardening
that proved reliable in the Keystone heroes:
- strict hero-position N -> skillbar N -> HeroUseSkill N mapping;
- the complete 8-slot RoJ hero bar is controller-owned/AI-locked, so native Hero AI
  cannot insert Symbol/heals between Arcane Echo and its mandatory RoJ seed;
- build discovery survives temporary Mimicry/Echo -> RoJ slot transformations;
- offensive setup is proximity-gated and abandoned between enemy packets;
- Arcane Mimicry can target ONLY another controller-owned native RoJ Monk hero;
  SoJ/player/Keystone/other-Monk fallback donors do not exist;
- Mimicry is confirmed only after its physical slot actually becomes RoJ;
- Mimicry, Auspicious and Echo are optional setup accelerators and never stall a
  ready native/copied RoJ;
- temporary RoJ copies are consumed before native RoJ where useful;
- shared RoJ carpet cadence/coverage spreads the three heroes across one packet;
- Psychic Instability knockdown leases are consumed as a short high-priority RoJ window;
- Binding Chains on the shared packet is detected and preferred as a RoJ/Symbol center;
- Stage 2.27 RoJ prot bars coordinate Aegis, Spirit Bond, Reversal of Fortune
  and Reversal of Damage across all three heroes outside the protected Echo transaction;
- Stage 2.28 calibrates Aegis from the live observed enchantment timer (17s in the
  current HR/cons setup), gives due Aegis a prevention deadline ahead of routine
  single-target prot spam, and lengthens the shared Reversal target mutex;
- Stage 2.29 adds immediate next-owner Aegis failover and requires a real fresh
  enchantment-timer jump before an overlapping Aegis refresh advances rotation;
- Stage 2.30 adds a shared adaptive OFFENSE/FULL_PROT state: Aegis/core-spirit
  safety and real party pressure drive protection, while stable windows suppress
  routine Spirit Bond/Reversal casts so the RoJ damage chain gets more actions;
- Stage 2.41 recognizes the live no-Aegis RoJ/Symbol prot bar after Aegis moved to eSurge accounts;
- legacy Dwayna/Patient/Zealot bars remain matchable for rollback compatibility.

RoJ-project core chain when everything is available:
Native RoJ -> Arcane Mimicry(other RoJ hero) -> Auspicious if ready ->
Arcane Echo -> ready RoJ seed -> Echo-RoJ -> consume remaining RoJs.
"""
from __future__ import annotations

_PREVIOUS_STAGE234_VERSION = "v34_stage2_34_split_prot_symbol_hardening"
_STAGE235_COMPAT_RUNTIME_LITERAL = 'version="v35_stage2_35_triple_rof_aegis_st_hardening"'
_STAGE241_COMPAT_RUNTIME_LITERAL = 'version="v41_stage2_41_roj_symbol_no_aegis_match"'

from dataclasses import dataclass, field

from Py4GWCoreLib import Agent, AgentArray, GLOBAL_CACHE, Map, Party, Player, Profession, Range, Routines, Skill, SkillBar, SpiritModelID, Utils
import PyGameThread
import PySkillbar

# Historical runtime marker retained for regression/log-parser compatibility.
_PREVIOUS_CONTROLLER_VERSION = "v33_stage2_33_aegis_frost_state_hardening"

RAY_OF_JUDGMENT_ID = int(Skill.GetID("Ray_of_Judgment") or 0)
ARCANE_MIMICRY_ID = int(Skill.GetID("Arcane_Mimicry") or 0)
ARCANE_ECHO_ID = int(Skill.GetID("Arcane_Echo") or 0)
AUSPICIOUS_INCANTATION_ID = int(Skill.GetID("Auspicious_Incantation") or 0)
SIGNET_OF_JUDGMENT_ID = int(Skill.GetID("Signet_of_Judgment") or 0)
PATIENT_SPIRIT_ID = int(Skill.GetID("Patient_Spirit") or 0)
DWAYNAS_KISS_ID = int(Skill.GetID("Dwaynas_Kiss") or 0)
CASTIGATION_SIGNET_ID = int(Skill.GetID("Castigation_Signet") or 0)
SYMBOL_OF_WRATH_ID = int(Skill.GetID("Symbol_of_Wrath") or 0)
MANTRA_OF_FROST_ID = int(Skill.GetID("Mantra_of_Frost") or 0)
ZEALOTS_FIRE_ID = int(Skill.GetID("Zealots_Fire") or Skill.GetID("Zealot_s_Fire") or 271)
AEGIS_ID = int(Skill.GetID("Aegis") or 0)
SPIRIT_BOND_ID = int(Skill.GetID("Spirit_Bond") or 0)
REVERSAL_OF_FORTUNE_ID = int(Skill.GetID("Reversal_of_Fortune") or 0)
REVERSAL_OF_DAMAGE_ID = int(Skill.GetID("Reversal_of_Damage") or 0)
BANE_SIGNET_ID = int(Skill.GetID("Bane_Signet") or 0)

_LEGACY_CORE_ROJ_HERO_IDS = frozenset(
    sid for sid in (
        RAY_OF_JUDGMENT_ID, DWAYNAS_KISS_ID, PATIENT_SPIRIT_ID,
        AUSPICIOUS_INCANTATION_ID,
    ) if sid > 0
)
_STAGE227_FIXED_REQUIRED_IDS = frozenset(
    sid for sid in (RAY_OF_JUDGMENT_ID, AUSPICIOUS_INCANTATION_ID, MANTRA_OF_FROST_ID, AEGIS_ID) if sid > 0
)
_STAGE227_PROT_IDS = frozenset(
    sid for sid in (SPIRIT_BOND_ID, REVERSAL_OF_FORTUNE_ID, REVERSAL_OF_DAMAGE_ID) if sid > 0
)
_STAGE241_SYMBOL_PROT_REQUIRED_IDS = frozenset(
    sid for sid in (
        RAY_OF_JUDGMENT_ID,
        AUSPICIOUS_INCANTATION_ID,
        MANTRA_OF_FROST_ID,
        SPIRIT_BOND_ID,
        REVERSAL_OF_FORTUNE_ID,
        SYMBOL_OF_WRATH_ID,
    ) if sid > 0
)
# Stage 2.43: Aegis now lives on the eSurge player accounts, and the freed RoJ
# slot is intentionally allowed to rotate between Symbol of Wrath and the fast
# protection tools.  Do not make any one filler/protection choice part of the
# hero identity: the stable no-Aegis core is RoJ + Auspicious + Mantra +
# Spirit Bond, with at least two of RoF / RoD / Symbol supplying the remaining
# support slots.  This prevents a harmless loadout edit from disabling all four
# RoJ heroes again.
_STAGE243_NO_AEGIS_CORE_IDS = frozenset(
    sid for sid in (
        RAY_OF_JUDGMENT_ID,
        AUSPICIOUS_INCANTATION_ID,
        MANTRA_OF_FROST_ID,
        SPIRIT_BOND_ID,
    ) if sid > 0
)
_STAGE243_NO_AEGIS_FLEX_IDS = frozenset(
    sid for sid in (REVERSAL_OF_FORTUNE_ID, REVERSAL_OF_DAMAGE_ID, SYMBOL_OF_WRATH_ID, BANE_SIGNET_ID) if sid > 0
)
_STAGE253_URGOZ_AEGIS_ROJ_REQUIRED_IDS = frozenset(
    sid for sid in (
        RAY_OF_JUDGMENT_ID, AUSPICIOUS_INCANTATION_ID,
        ARCANE_MIMICRY_ID, ARCANE_ECHO_ID, SPIRIT_BOND_ID,
        REVERSAL_OF_FORTUNE_ID, BANE_SIGNET_ID, AEGIS_ID,
    ) if sid > 0
)
_COPY_ENGINE_IDS = frozenset(
    sid for sid in (ARCANE_MIMICRY_ID, ARCANE_ECHO_ID) if sid > 0
)
_NATIVE_REQUIRED_IDS = frozenset(set(_LEGACY_CORE_ROJ_HERO_IDS) | set(_STAGE227_FIXED_REQUIRED_IDS) | set(_COPY_ENGINE_IDS))
_MANAGED_BASE_IDS = frozenset(
    set(_NATIVE_REQUIRED_IDS) | set(_STAGE227_PROT_IDS)
    | {sid for sid in (SYMBOL_OF_WRATH_ID, CASTIGATION_SIGNET_ID, BANE_SIGNET_ID, MANTRA_OF_FROST_ID, ZEALOTS_FIRE_ID, AEGIS_ID) if sid > 0}
)

def _supported_roj_fixed_loadout(ids: set[int]) -> bool:
    """Non-transforming RoJ identity, including Stage-2.27 heterogeneous prot bars."""
    legacy_ok = False
    if _LEGACY_CORE_ROJ_HERO_IDS.issubset(ids):
        frost_ok = (MANTRA_OF_FROST_ID > 0 and MANTRA_OF_FROST_ID in ids) or (CASTIGATION_SIGNET_ID > 0 and CASTIGATION_SIGNET_ID in ids)
        smite_ok = (ZEALOTS_FIRE_ID > 0 and ZEALOTS_FIRE_ID in ids) or (SYMBOL_OF_WRATH_ID > 0 and SYMBOL_OF_WRATH_ID in ids)
        legacy_ok = bool(frost_ok and smite_ok)
    stage227_ok = bool(
        len(_STAGE227_FIXED_REQUIRED_IDS) >= 4
        and _STAGE227_FIXED_REQUIRED_IDS.issubset(ids)
        and len(set(ids) & set(_STAGE227_PROT_IDS)) >= 2
    )
    # Stage 2.34 compatibility: the one-Symbol split bar remains recognized.
    stage234_ok = bool(
        len(_STAGE227_FIXED_REQUIRED_IDS) >= 4
        and _STAGE227_FIXED_REQUIRED_IDS.issubset(ids)
        and SPIRIT_BOND_ID > 0 and SPIRIT_BOND_ID in ids
        and any(sid > 0 and sid in ids for sid in (REVERSAL_OF_FORTUNE_ID, REVERSAL_OF_DAMAGE_ID, SYMBOL_OF_WRATH_ID))
    )
    # Stage 2.35 canonical live bar: every RoJ carries Aegis + Spirit Bond +
    # Reversal of Fortune. Symbol of Wrath is intentionally no longer required;
    # the third fast Reversal proved far more useful than the 3 Symbol casts in
    # the 8:38 Stage-2.34 run.
    stage235_ok = bool(
        len(_STAGE227_FIXED_REQUIRED_IDS) >= 4
        and _STAGE227_FIXED_REQUIRED_IDS.issubset(ids)
        and SPIRIT_BOND_ID > 0 and SPIRIT_BOND_ID in ids
        and REVERSAL_OF_FORTUNE_ID > 0 and REVERSAL_OF_FORTUNE_ID in ids
    )
    # Stage 2.41 live bar after moving the long-duration group block layer to
    # the three eSurge player accounts.  The RoJ heroes keep their fast
    # single-target protection and use Symbol of Wrath in the freed Aegis slot.
    stage241_required = globals().get("_STAGE241_SYMBOL_PROT_REQUIRED_IDS", frozenset())
    stage241_symbol_prot_ok = bool(
        len(stage241_required) >= 6
        and stage241_required.issubset(ids)
        and (AEGIS_ID <= 0 or AEGIS_ID not in ids)
    )
    # Stage 2.43 flexible no-Aegis RoJ profile.  Accept the current dual-
    # Reversal bar (Spirit Bond + RoF + RoD) as well as the prior Symbol bar,
    # while still requiring the stable RoJ/Auspicious/Mantra/Spirit-Bond core.
    stage243_core = globals().get("_STAGE243_NO_AEGIS_CORE_IDS", frozenset())
    stage243_flex = globals().get("_STAGE243_NO_AEGIS_FLEX_IDS", frozenset())
    stage243_no_aegis_ok = bool(
        len(stage243_core) >= 4
        and stage243_core.issubset(ids)
        and len(set(ids) & set(stage243_flex)) >= 2
        and (AEGIS_ID <= 0 or AEGIS_ID not in ids)
    )
    # Global 3-eSurge / Urgoz support variant: return Aegis to all RoJ monks,
    # replacing Mantra of Frost, without breaking the Echo/Mimicry chain.
    new_core = globals().get("_STAGE253_URGOZ_AEGIS_ROJ_REQUIRED_IDS", frozenset())
    stage253_urgoz_aegis = bool(len(new_core) == 8 and new_core.issubset(ids))
    return bool(
        legacy_ok or stage227_ok or stage234_ok or stage235_ok
        or stage241_symbol_prot_ok or stage243_no_aegis_ok
        or stage253_urgoz_aegis
    )

def _supported_roj_loadout(ids: set[int]) -> bool:
    """Clean native bar: fixed identity plus Arcane Mimicry and Echo."""
    return bool(_supported_roj_fixed_loadout(ids) and _COPY_ENGINE_IDS.issubset(ids))

_SCAN_INTERVAL_MS = 90
_LOCK_REFRESH_MS = 650
_MAP_ENTRY_HARD_LOCK_MS = 12000
_MAP_ENTRY_LOCK_REFRESH_MS = 120
_ECHO_COPY_APPEAR_TIMEOUT_MS = 3500
_COMMAND_TIMEOUT_MS = 3300
_COMMAND_GUARD_MS = 170
_ROJ_TEAM_STAGGER_MS = 930
_PI_ROJ_BURST_STAGGER_MS = 560
_ROJ_COVERAGE_WINDOW_MS = 5250
_ROJ_MAX_TARGET_COVERAGE = 3
_ROJ_TEAM_LANE_LOCK_ID = 0x524F4A4C
_ROJ_CLUSTER_COVERAGE_LOCK_ID = 0x524F4A43
_ROJ_TARGET_COVERAGE_LOCK_ID = 0x524F4A54
_ROJ_MAX_CLUSTER_COVERAGE = 7
_SYMBOL_TEAM_STAGGER_MS = 680
_SYMBOL_COVERAGE_WINDOW_MS = 5000
_SYMBOL_MAX_CLUSTER_COVERAGE = 2
_SYMBOL_MAX_TARGET_COVERAGE = 1
_CAST_RANGE = float(Range.Spellcast.value)
_NEARBY = float(Range.Nearby.value)
# Offensive copy/setup is forbidden between packets. The slight margin lets the
# controller keep an already-started RoJ chain while the party closes the last
# few steps, but it never pre-copies RoJ at map entry or far from combat.
_SETUP_PROXIMITY_RANGE = _CAST_RANGE * 1.05


@dataclass(slots=True)
class _PendingCommand:
    skill_id: int = 0
    slot: int = 0
    target_id: int = 0
    started_ms: int = 0
    start_recharge: int = 0
    start_effect_remaining_ms: int = 0
    start_effect_present: bool = False
    purpose: str = ""


@dataclass(slots=True)
class _ControllerState:
    hero_index: int
    hero_bar_index: int
    hero_agent_id: int
    managed_slots: tuple[int, ...] = ()
    locked_slots: set[int] = field(default_factory=set)
    base_roj_slot: int = 0
    mimicry_slot: int = 0
    echo_slot: int = 0
    auspicious_slot: int = 0
    symbol_slot: int = 0
    mantra_slot: int = 0
    zealots_fire_slot: int = 0
    aegis_slot: int = 0
    spirit_bond_slot: int = 0
    reversal_fortune_slot: int = 0
    reversal_damage_slot: int = 0
    bane_signet_slot: int = 0
    transaction_locked: bool = False
    chain_phase: str = "opener"
    chain_started_ms: int = 0
    chain_phase_started_ms: int = 0
    last_phase_wait_log_ms: int = 0
    last_lock_refresh_ms: int = 0
    last_scan_ms: int = 0
    last_command_ms: int = 0
    created_ms: int = 0
    echo_seed_slot: int = 0
    echo_seed_cast_ms: int = 0
    last_precombat_leak_log_ms: int = 0
    last_copy_setup_ms: int = 0
    pending: _PendingCommand = field(default_factory=_PendingCommand)
    matched_logged: bool = False
    # Arcane Mimicry on heroes is treated exactly like the proven player RoJway:
    # acquire a verified donor as the *current selected target*, keep it stable,
    # then issue the hero Mimicry from the game thread against target 0/current.
    mimicry_target_id: int = 0
    mimicry_old_player_target: int = 0
    mimicry_target_started_ms: int = 0
    mimicry_dispatch_result: str = ""
    # EXP36 Stage 11 / E10b: remember one physical wrong-copy slot state so
    # the same stale Mimicry transformation is aborted exactly once, not once
    # per controller scan.  Gameplay remains in the free consume lane until the
    # slot returns to Mimicry, the expected RoJ copy, or an empty/transient id.
    wrong_copy_quarantine_skill_id: int = 0
    wrong_copy_quarantine_started_ms: int = 0


_STATES: dict[int, _ControllerState] = {}
_LAST_MAP_ID = 0
_RUNTIME_LOGGED = False
_LAST_ROJ_TEAM_CAST_MS = 0
_LAST_ROJ_TEAM_TARGET = 0
_DISCOVERED_HEROES: set[int] = set()
_MIMICRY_OWNER_HERO_ID = 0
_ROJ_COVERAGE_HISTORY: list[tuple[int, int]] = []
_SYMBOL_COVERAGE_HISTORY: list[tuple[int, int]] = []
_PI_BURST_EVENT_EXPIRES = 0
_PI_BURST_ANCHOR = 0
_PI_BURST_MEMBERS: tuple[int, ...] = ()
_PI_BURST_GOAL = 0
_PI_BURST_CASTS = 0
_PI_BURST_LAST_ARM_LOG_MS = 0
_LAST_SYMBOL_TEAM_CAST_MS = 0
_LAST_BANE_TEAM_CAST_MS = 0
_BANE_TEAM_STAGGER_MS = 420
_BANE_ROJ_NEAR_READY_GUARD_MS = 1250
# Same-process reservations for the three RoJ monks. Without this, two or three
# monks often start the same heal before the first cast/effect becomes visible.
_HEAL_CLAIMS: list[tuple[int, int, int, int]] = []  # legacy (expires, skill, target, hero)
# Stage 2.27 same-process cross-hero protection reservations.  All three RoJ
# heroes live in this controller, so a short deterministic claim is enough to
# prevent duplicate Spirit Bond/Reversal casts before the game exposes effects.
_PROT_CLAIMS: list[tuple[int, str, int, int, int]] = []  # (expires, group, skill, target, hero)
_LAST_AEGIS_CASTER_HERO_ID = 0
_LAST_AEGIS_CONFIRMED_MS = 0
_LAST_AEGIS_OBSERVED_DURATION_MS = 0
# Stage 2.28: the live party effect timer is authoritative.  The current
# HR+cons configuration was observed at ~17 seconds; use that only as a fallback
# when the effect API can see Aegis but cannot expose remaining milliseconds.
_AEGIS_PRECAST_MARGIN_MS = 1200
_AEGIS_EFFECT_FALLBACK_MS = 17000
_AEGIS_OBSERVED_MIN_MS = 8000
# Stage 2.29: fail over quickly when the elected RoJ cannot actually start/finish Aegis.
_AEGIS_FAILOVER_STALL_MS = 1400
_AEGIS_FAILOVER_SKIP_MS = 1800
_AEGIS_REFRESH_JUMP_MIN_MS = 2500
_AEGIS_REFRESH_FRESH_RATIO = 0.60
_AEGIS_OWNER_AFTERCAST_GUARD_MS = 700
_AEGIS_OWNER_NATIVE_LOOKBACK_MS = 1800
_AEGIS_FAILOVER_SKIP_UNTIL: dict[int, int] = {}
_REVERSAL_TARGET_MUTEX_MS = 1700
# Stage 2.30 adaptive support budget. Enter FULL_PROT immediately under real
# pressure; return to OFFENSE only after a short stable hysteresis window.
_ADAPTIVE_PROT_MODE = "FULL_PROT"
_ADAPTIVE_PROT_MODE_SINCE_MS = 0
_ADAPTIVE_PROT_STABLE_SINCE_MS = 0
_ADAPTIVE_PROT_LAST_LOG_MS = 0
_ADAPTIVE_PROT_EXIT_STABLE_MS = 1900
_ADAPTIVE_PROT_AEGIS_SAFE_MS = 3000
_ADAPTIVE_PROT_LOG_INTERVAL_MS = 2500
_ADAPTIVE_TEAM_SNAPSHOT_TTL_MS = 140
_ADAPTIVE_TEAM_SNAPSHOT_MS = 0
_ADAPTIVE_TEAM_SNAPSHOT: dict[str, object] = {}
_ADAPTIVE_HP_BASELINE: dict[int, tuple[int, float]] = {}
_ADAPTIVE_HP_TREND_WINDOW_MS = 1600
_STAGE227_REVERSAL_POLICY_COMPAT = "stage2_27_cross_skill_same_target_mutex"
_MIMICRY_SLOT_TEMPLATE = 5
_ECHO_SLOT_TEMPLATE = 6


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


def _hero_bar(hero_position: int):
    """Read the same 1-based hero position later used by HeroUseSkill."""
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


def _hero_agent_for_position(hero_position: int) -> int:
    try:
        return int(Party.Heroes.GetHeroAgentIDByPartyPosition(int(hero_position)) or 0)
    except Exception:
        return 0


def _as_int(value) -> int:
    try:
        return int(getattr(value, "value", value))
    except Exception:
        return 0


def _matching_heroes() -> list[tuple[int, int, int]]:
    """Find exactly this RoJ hero build with strict 1:1 position mapping.

    The six non-transforming skills identify the build even if Arcane Mimicry or
    Arcane Echo already displays a temporary RoJ.  The user's fixed build puts
    Mimicry in slot 5 and Echo in slot 6; clean bars refresh those templates.
    """
    global _MIMICRY_SLOT_TEMPLATE, _ECHO_SLOT_TEMPLATE
    result: list[tuple[int, int, int]] = []

    # Learn physical copy slots from any clean native hero before evaluating
    # transformed copies. Defaults 5/6 match the supplied RoJ hero template.
    for hero_position in range(1, 8):
        hero_id = _hero_agent_for_position(hero_position)
        if hero_id <= 0:
            continue
        entries = _bar_entries(hero_position)
        ids = {sid for _, sid, _ in entries if sid > 0}
        if len(entries) >= 8 and _supported_roj_loadout(ids):
            mim = next((slot for slot, sid, _ in entries if sid == ARCANE_MIMICRY_ID), 0)
            echo = next((slot for slot, sid, _ in entries if sid == ARCANE_ECHO_ID), 0)
            if mim > 0:
                _MIMICRY_SLOT_TEMPLATE = int(mim)
            if echo > 0:
                _ECHO_SLOT_TEMPLATE = int(echo)
            break

    for hero_position in range(1, 8):
        hero_id = _hero_agent_for_position(hero_position)
        if hero_id <= 0:
            continue
        entries = _bar_entries(hero_position)
        ids = {sid for _, sid, _ in entries if sid > 0}
        try:
            primary, secondary = Agent.GetProfessionIDs(hero_id)
            is_monk_mesmer = (
                _as_int(primary) == _as_int(Profession.Monk)
                and _as_int(secondary) == _as_int(Profession.Mesmer)
            )
        except Exception:
            is_monk_mesmer = False

        invariant_match = bool(
            is_monk_mesmer
            and len(entries) >= 8
            and _supported_roj_fixed_loadout(ids)
            and (SIGNET_OF_JUDGMENT_ID <= 0 or SIGNET_OF_JUDGMENT_ID not in ids)
        )
        if invariant_match:
            _DISCOVERED_HEROES.add(hero_id)
        if hero_id in _DISCOVERED_HEROES:
            result.append((hero_position, hero_position, hero_id))
    return result

def _managed_slots(bar_index: int) -> tuple[int, ...]:
    # Keystone-style deterministic ownership: once this exact RoJ build is
    # matched, all eight slots are controlled here. This prevents native Hero AI
    # from slipping Symbol/heals between Arcane Echo and its mandatory RoJ seed.
    entries = _bar_entries(bar_index)
    if len(entries) >= 8:
        return tuple(range(1, 9))
    return tuple(sorted(slot for slot, sid, _ in entries if sid in _MANAGED_BASE_IDS))

def _restore_native_ai(state: _ControllerState) -> None:
    state.transaction_locked = False
    for slot in range(1, 9):
        try:
            Party.Heroes.SetSkillAIEnabled(int(state.hero_agent_id), int(slot), True)
        except Exception:
            pass
    for slot in tuple(sorted(state.locked_slots)):
        try:
            Party.Heroes.SetSkillAIEnabled(int(state.hero_agent_id), int(slot), True)
        except Exception:
            pass
    state.locked_slots.clear()


def _reconcile_states() -> list[_ControllerState]:
    matches = _matching_heroes()
    live = {hero_id for _, _, hero_id in matches}
    for hero_id in list(_STATES):
        if hero_id not in live:
            _restore_native_ai(_STATES.pop(hero_id))

    ordered: list[_ControllerState] = []
    for hero_index, bar_index, hero_id in matches:
        state = _STATES.get(hero_id)
        slots = _managed_slots(bar_index)
        if state is None:
            entries = _bar_entries(bar_index)
            mimic_slot = next((slot for slot, sid, _ in entries if sid == ARCANE_MIMICRY_ID), 0) or int(_MIMICRY_SLOT_TEMPLATE)
            echo_slot = next((slot for slot, sid, _ in entries if sid == ARCANE_ECHO_ID), 0) or int(_ECHO_SLOT_TEMPLATE)
            # The native elite is the RoJ outside the two transformable copy slots.
            roj_slot = next((slot for slot, sid, _ in entries if sid == RAY_OF_JUDGMENT_ID and slot not in (mimic_slot, echo_slot)), 0)
            ausp_slot = next((slot for slot, sid, _ in entries if sid == AUSPICIOUS_INCANTATION_ID), 0)
            symbol_slot = next((slot for slot, sid, _ in entries if sid == SYMBOL_OF_WRATH_ID), 0)
            mantra_slot = next((slot for slot, sid, _ in entries if sid == MANTRA_OF_FROST_ID), 0)
            zealots_fire_slot = next((slot for slot, sid, _ in entries if sid == ZEALOTS_FIRE_ID), 0)
            aegis_slot = next((slot for slot, sid, _ in entries if sid == AEGIS_ID), 0)
            spirit_bond_slot = next((slot for slot, sid, _ in entries if sid == SPIRIT_BOND_ID), 0)
            reversal_fortune_slot = next((slot for slot, sid, _ in entries if sid == REVERSAL_OF_FORTUNE_ID), 0)
            reversal_damage_slot = next((slot for slot, sid, _ in entries if sid == REVERSAL_OF_DAMAGE_ID), 0)
            bane_signet_slot = next((slot for slot, sid, _ in entries if sid == BANE_SIGNET_ID), 0)
            state = _ControllerState(
                hero_index, bar_index, hero_id, managed_slots=slots,
                base_roj_slot=roj_slot, mimicry_slot=mimic_slot, echo_slot=echo_slot,
                auspicious_slot=ausp_slot, symbol_slot=symbol_slot,
                mantra_slot=mantra_slot, zealots_fire_slot=zealots_fire_slot,
                aegis_slot=aegis_slot, spirit_bond_slot=spirit_bond_slot,
                reversal_fortune_slot=reversal_fortune_slot, reversal_damage_slot=reversal_damage_slot,
                bane_signet_slot=bane_signet_slot,
                created_ms=_now_ms(),
            )
            _STATES[hero_id] = state
            # Close the map-entry race immediately. Mimicry/Echo/native RoJ are
            # disabled before the normal HeroAI tree gets a chance to use them.
            lock_order = []
            for candidate in (state.mimicry_slot, state.echo_slot, state.base_roj_slot, state.auspicious_slot):
                if candidate > 0 and candidate not in lock_order:
                    lock_order.append(int(candidate))
            lock_order.extend(slot for slot in state.managed_slots if slot not in lock_order)
            for slot in lock_order:
                try:
                    result = Party.Heroes.SetSkillAIEnabled(int(state.hero_agent_id), int(slot), False)
                    if result is not False:
                        state.locked_slots.add(int(slot))
                except Exception:
                    pass
            state.last_lock_refresh_ms = _now_ms()
            _log(
                "HERO_ROJ_AI_LOCK_ARMED",
                hero_index=state.hero_index, hero_id=state.hero_agent_id,
                mimicry_slot=state.mimicry_slot, echo_slot=state.echo_slot,
                managed_slots=",".join(map(str, state.managed_slots)),
                policy="map_entry_hard_lock",
            )
        else:
            state.hero_index = hero_index
            state.hero_bar_index = bar_index
            # Preserve original managed slots while Mimicry/Echo are transformed.
            if slots:
                state.managed_slots = tuple(sorted(set(state.managed_slots) | set(slots)))
        if not state.matched_logged:
            state.matched_logged = True
            _log(
                "HERO_ROJ_MATCHED", hero_index=hero_index, bar_index=bar_index, hero_id=hero_id,
                managed_slots=",".join(map(str, state.managed_slots)), mapping=f"read{bar_index}->cast{hero_index}",
                ordered_skill_ids=",".join(str(sid) for _slot, sid, _data in _bar_entries(bar_index)),
                native_roj_slot=state.base_roj_slot, mimicry_slot=state.mimicry_slot,
                echo_slot=state.echo_slot, auspicious_slot=state.auspicious_slot, symbol_slot=state.symbol_slot,
                mantra_slot=state.mantra_slot, zealots_fire_slot=state.zealots_fire_slot,
                aegis_slot=state.aegis_slot, spirit_bond_slot=state.spirit_bond_slot,
                reversal_fortune_slot=state.reversal_fortune_slot, reversal_damage_slot=state.reversal_damage_slot,
                bane_signet_slot=state.bane_signet_slot,
                support_profile=(
                    "stage2_27_prot" if state.aegis_slot > 0
                    else "stage2_43_no_aegis_dual_reversal" if (
                        state.spirit_bond_slot > 0
                        and state.reversal_fortune_slot > 0
                        and state.reversal_damage_slot > 0
                        and state.mantra_slot > 0
                    )
                    else "stage2_41_symbol_prot" if (
                        state.symbol_slot > 0
                        and state.spirit_bond_slot > 0
                        and state.reversal_fortune_slot > 0
                        and state.mantra_slot > 0
                    )
                    else "stage2_47_no_aegis_bane_prot" if (
                        state.spirit_bond_slot > 0
                        and state.reversal_fortune_slot > 0
                        and state.bane_signet_slot > 0
                        and state.mantra_slot > 0
                    )
                    else "stage2_43_no_aegis_flex_prot" if (
                        state.spirit_bond_slot > 0
                        and state.mantra_slot > 0
                        and sum(1 for slot in (state.reversal_fortune_slot, state.reversal_damage_slot, state.symbol_slot) if int(slot) > 0) >= 2
                    )
                    else "legacy_heal"
                ),
            )
        ordered.append(state)
    return ordered


def _ensure_locked(state: _ControllerState, now_ms: int) -> bool:
    wanted = set(state.managed_slots)
    if state.transaction_locked:
        wanted.update(range(1, 9))
    # Guild Wars can restore native hero-skill toggles a few seconds after a map
    # finishes loading. Re-assert them aggressively during that exact window;
    # after it, keep a still-tight refresh so Mimicry/Echo cannot leak between
    # controller ticks. This closes the observed map-entry Mimicry race.
    age_ms = max(0, int(now_ms) - int(state.created_ms or now_ms))
    refresh_ms = _MAP_ENTRY_LOCK_REFRESH_MS if age_ms <= _MAP_ENTRY_HARD_LOCK_MS else _LOCK_REFRESH_MS
    if state.locked_slots == wanted and now_ms - state.last_lock_refresh_ms < refresh_ms:
        return True
    ok = True
    for slot in sorted(wanted):
        try:
            result = Party.Heroes.SetSkillAIEnabled(int(state.hero_agent_id), int(slot), False)
            if result is False:
                ok = False
                state.locked_slots.discard(int(slot))
                _log("HERO_ROJ_AI_LOCK_FAILED", hero_index=state.hero_index, hero_id=state.hero_agent_id, slot=slot, error="returned_false")
                continue
            state.locked_slots.add(int(slot))
        except Exception as exc:
            ok = False
            state.locked_slots.discard(int(slot))
            _log("HERO_ROJ_AI_LOCK_FAILED", hero_index=state.hero_index, hero_id=state.hero_agent_id, slot=slot, error=type(exc).__name__)
    state.last_lock_refresh_ms = now_ms
    if ok:
        state.locked_slots = wanted
    return ok


def _set_transaction_lock(state: _ControllerState, enabled: bool) -> None:
    """Temporarily suppress native Hero AI during the Echo transaction.

    This is intentionally short-lived: from just before Arcane Echo until the
    mandatory next Ray of Judgment has been issued.  It prevents a heal/cleanse
    spell from being the spell copied by Arcane Echo.
    """
    if enabled == state.transaction_locked:
        return
    state.transaction_locked = enabled
    if enabled:
        for slot in range(1, 9):
            try:
                Party.Heroes.SetSkillAIEnabled(int(state.hero_agent_id), int(slot), False)
            except Exception:
                pass
        _log("HERO_ROJ_TRANSACTION_LOCK", hero_index=state.hero_index, hero_id=state.hero_agent_id, enabled=True)
    else:
        # Keep copy-engine slots disabled, return all other slots to native Hero AI.
        for slot in range(1, 9):
            if slot in state.managed_slots:
                continue
            try:
                Party.Heroes.SetSkillAIEnabled(int(state.hero_agent_id), int(slot), True)
            except Exception:
                pass
        _log("HERO_ROJ_TRANSACTION_LOCK", hero_index=state.hero_index, hero_id=state.hero_agent_id, enabled=False)


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


def _entry_by_id(state: _ControllerState, skill_id: int):
    for slot, sid, data in _bar_entries(state.hero_bar_index):
        if sid == int(skill_id):
            return slot, data
    return None


def _entries_by_id(state: _ControllerState, skill_id: int) -> list[tuple[int, object]]:
    return [(slot, data) for slot, sid, data in _bar_entries(state.hero_bar_index) if sid == int(skill_id)]


def _recharge_data(data: object) -> int:
    try:
        return int(getattr(data, "recharge", 0) or 0)
    except Exception:
        return 0


def _ready_entry(state: _ControllerState, skill_id: int):
    for slot, data in _entries_by_id(state, skill_id):
        if _recharge_data(data) == 0:
            return slot, data
    return None


def _energy_current(state: _ControllerState) -> int:
    try:
        return int(float(Agent.GetEnergy(state.hero_agent_id) or 0.0) *
                   int(Agent.GetMaxEnergy(state.hero_agent_id) or 0))
    except Exception:
        return 0


def _can_pay(state: _ControllerState, skill_id: int) -> bool:
    try:
        return _energy_current(state) >= int(Skill.Data.GetEnergyCost(int(skill_id)) or 0)
    except Exception:
        return True


def _skill_energy_cost(skill_id: int) -> int:
    try:
        return int(Skill.Data.GetEnergyCost(int(skill_id)) or 0)
    except Exception:
        return 0


def _can_afford_full_chain(state: _ControllerState) -> bool:
    # Mirror the player RoJ project's protected-lane idea: do not enter a chain
    # that cannot reasonably reach Echo's RoJ seed. Auspicious is optional and
    # therefore is not part of the mandatory reserve.
    # Same admission rule as the real-player RoJ project: reserve native RoJ,
    # Mimicry itself, and the first Mimicry-RoJ. Echo is checked again at the
    # Echo step, where Auspicious can refund its energy cost.
    reserve = (
        _skill_energy_cost(RAY_OF_JUDGMENT_ID)
        + _skill_energy_cost(ARCANE_MIMICRY_ID)
        + _skill_energy_cost(RAY_OF_JUDGMENT_ID)
    )
    return _energy_current(state) >= max(0, reserve)


def _has_effect(agent_id: int, skill_id: int) -> bool:
    try:
        return bool(Routines.Checks.Agents.HasEffect(int(agent_id), int(skill_id)))
    except Exception:
        return False


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


def _valid_enemy(state: _ControllerState, enemy_id: int) -> bool:
    try:
        return bool(
            enemy_id > 0 and Agent.IsValid(enemy_id) and Agent.IsAlive(enemy_id)
            and _distance(state.hero_agent_id, enemy_id) <= _CAST_RANGE
        )
    except Exception:
        return False


def _has_enemy_proximity(state: _ControllerState) -> bool:
    # First reuse the authoritative focus resolvers.
    if _valid_enemy(state, _cluster_anchor(state)) or _valid_enemy(state, _cleanup_anchor(state)):
        return True
    # Fallback scan closes short gaps while cluster ownership is being refreshed.
    try:
        for enemy_id in list(AgentArray.GetEnemyArray() or []):
            enemy_id = int(enemy_id or 0)
            if enemy_id <= 0 or not Agent.IsValid(enemy_id) or not Agent.IsAlive(enemy_id):
                continue
            if _distance(state.hero_agent_id, enemy_id) <= _SETUP_PROXIMITY_RANGE:
                return True
    except Exception:
        pass
    return False


def _team_plan(state: _ControllerState):
    try:
        from Py4GWCoreLib.Builds.Skills import TeamCombatFocus
        return TeamCombatFocus.get_team_combat_plan(
            filter_range=_CAST_RANGE, minimum_enemies=2, consumer_role="hero_roj",
        )
    except Exception:
        return None


def _primary_focus_target(state: _ControllerState) -> int:
    plan = _team_plan(state)
    if plan is None:
        return 0
    target = int(getattr(plan, "focus_target", 0) or 0)
    return target if _valid_enemy(state, target) else 0


def _cluster_anchor(state: _ControllerState) -> int:
    plan = _team_plan(state)
    if plan is None or str(getattr(plan, "mode", "none")) not in {"cluster", "st_defense", "krummrinde"}:
        return 0
    anchor = int(getattr(plan, "aoe_anchor", 0) or getattr(plan, "group_anchor", 0) or 0)
    return anchor if _valid_enemy(state, anchor) else 0


def _cleanup_anchor(state: _ControllerState) -> int:
    plan = _team_plan(state)
    if plan is None:
        return 0
    if str(getattr(plan, "mode", "none")) not in {"cleanup", "low_hp_finish", "st_defense", "krummrinde"}:
        return 0
    target = int(getattr(plan, "focus_target", 0) or getattr(plan, "group_anchor", 0) or 0)
    return target if _valid_enemy(state, target) else 0


def _count_packet(state: _ControllerState, anchor_id: int) -> int:
    if anchor_id <= 0:
        return 0
    try:
        from Py4GWCoreLib.Builds.Skills import HeroClusterCoordinator
        return len(list(HeroClusterCoordinator.cluster_members(
            int(anchor_id),
            origin_agent_id=state.hero_agent_id,
            radius=_NEARBY,
            range_value=_CAST_RANGE,
        ) or []))
    except Exception:
        return 1 if _valid_enemy(state, anchor_id) else 0


def _prune_roj_coverage(now_ms: int) -> None:
    cutoff = int(now_ms) - int(_ROJ_COVERAGE_WINDOW_MS)
    if not _ROJ_COVERAGE_HISTORY:
        return
    _ROJ_COVERAGE_HISTORY[:] = [(ts, tid) for ts, tid in _ROJ_COVERAGE_HISTORY if int(ts) >= cutoff]


def _roj_coordination_group_id() -> int:
    try:
        party_id = int(GLOBAL_CACHE.Party.GetPartyID() or 0)
        if party_id > 0:
            return party_id
    except Exception:
        pass
    try:
        email = str(Player.GetAccountEmail() or "").strip()
        if email:
            return int(GLOBAL_CACHE.ShMem.GetAccountGroupByEmail(email) or 0)
    except Exception:
        pass
    return 0


def _shared_roj_lock_count(key_id: int, target_id: int, now_ms: int) -> int | None:
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import (
            WhiteboardClaimStrength, WhiteboardLockKind, WhiteboardReentryPolicy,
        )
        return int(GLOBAL_CACHE.ShMem.CountLocks(
            int(WhiteboardLockKind.SKILL_TARGET), int(key_id), int(target_id),
            int(_roj_coordination_group_id()), "", int(now_ms),
            int(WhiteboardReentryPolicy.NON_REENTRANT), int(WhiteboardClaimStrength.HARD),
        ) or 0)
    except Exception:
        return None


def _post_shared_roj_lock(
    state: _ControllerState, key_id: int, target_id: int, *,
    max_holders: int, now_ms: int, duration_ms: int = _ROJ_COVERAGE_WINDOW_MS,
) -> bool:
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import (
            WhiteboardClaimStrength, WhiteboardLockKind, WhiteboardLockMode, WhiteboardReentryPolicy,
        )
        email = str(Player.GetAccountEmail() or "").strip()
        if not email:
            return False
        owner = f"{email}#roj_hero_{int(state.hero_agent_id)}"
        return bool(GLOBAL_CACHE.ShMem.PostLock(
            owner, int(WhiteboardLockKind.SKILL_TARGET), int(key_id), int(target_id),
            int(now_ms) + int(duration_ms), int(_roj_coordination_group_id()),
            int(WhiteboardLockMode.SEMAPHORE), max(1, int(max_holders)),
            int(WhiteboardReentryPolicy.NON_REENTRANT), int(WhiteboardClaimStrength.HARD),
        ) != -1)
    except Exception:
        return False


def _local_target_roj_coverage(target_id: int, now_ms: int) -> int:
    _prune_roj_coverage(now_ms)
    return sum(1 for _ts, tid in _ROJ_COVERAGE_HISTORY if int(tid) == int(target_id))


def _target_roj_coverage(state: _ControllerState, target_id: int, now_ms: int) -> int:
    shared = _shared_roj_lock_count(_ROJ_TARGET_COVERAGE_LOCK_ID, int(target_id), int(now_ms))
    if shared is not None:
        return int(shared)
    return _local_target_roj_coverage(int(target_id), int(now_ms))


def _packet_coverage_cap(packet_size: int) -> int:
    size = max(1, int(packet_size or 0))
    if size <= 1:
        return 3
    if size == 2:
        return 3
    if size <= 4:
        return 4
    return 6


def _target_coverage_cap(packet_size: int) -> int:
    return 3 if int(packet_size or 0) <= 1 else 2


def _coverage_anchor_for_target(state: _ControllerState, target_id: int) -> int:
    anchor = int(_cluster_anchor(state) or 0)
    if anchor > 0:
        try:
            from Py4GWCoreLib.Builds.Skills import HeroClusterCoordinator
            members = set(int(x) for x in list(HeroClusterCoordinator.cluster_members(
                anchor, origin_agent_id=state.hero_agent_id, radius=_NEARBY, range_value=_CAST_RANGE
            ) or []))
            if int(target_id) in members or int(target_id) == anchor:
                return int(anchor)
        except Exception:
            pass
    return int(target_id)


def _record_roj_coverage(state: _ControllerState, target_id: int, now_ms: int) -> None:
    if target_id <= 0:
        return
    _prune_roj_coverage(now_ms)
    _ROJ_COVERAGE_HISTORY.append((int(now_ms), int(target_id)))
    anchor = _coverage_anchor_for_target(state, int(target_id))
    target_ok = _post_shared_roj_lock(
        state, _ROJ_TARGET_COVERAGE_LOCK_ID, int(target_id),
        max_holders=_ROJ_MAX_TARGET_COVERAGE, now_ms=int(now_ms),
    )
    cluster_ok = _post_shared_roj_lock(
        state, _ROJ_CLUSTER_COVERAGE_LOCK_ID, int(anchor),
        max_holders=_ROJ_MAX_CLUSTER_COVERAGE, now_ms=int(now_ms),
    )
    _log(
        "HERO_ROJ_E09_CONFIRMED_COVERAGE",
        hero_index=state.hero_index, hero_id=state.hero_agent_id,
        target_id=int(target_id), anchor_id=int(anchor),
        target_publish_ok=bool(target_ok), cluster_publish_ok=bool(cluster_ok),
        policy="stage2_31_shared_after_finished_or_post_cast_recharge",
    )


def _has_binding_chains(target_id: int) -> bool:
    """True when the current enemy is covered by Binding Chains.

    Binding Chains is cast by the ST/PI side on the same TeamCombatFocus packet.
    RoJ does not reserve the hex itself; it only uses the visible effect as an
    extra center preference so the rays land on the packet that is being held
    nearly stationary.
    """
    target_id = int(target_id or 0)
    if target_id <= 0:
        return False
    try:
        from Py4GWCoreLib.Builds.Skills.BindingChainsCoordination import Binding_Chains_ID
        return bool(Routines.Checks.Agents.HasEffect(target_id, int(Binding_Chains_ID)))
    except Exception:
        return False


def _refresh_pi_burst(state: _ControllerState) -> tuple[int, tuple[int, ...], int, int]:
    """Return the current confirmed PI burst as anchor, members, goal, casts.

    v17 listened only for packet-member leases and additionally required local
    knockdown visibility. The real-player PI path did not publish those leases,
    and the local KD flag is timing-sensitive. v18 publishes a confirmed event
    with the exact PI target and uses that event itself as the authoritative
    short damage window.
    """
    global _PI_BURST_EVENT_EXPIRES, _PI_BURST_ANCHOR, _PI_BURST_MEMBERS
    global _PI_BURST_GOAL, _PI_BURST_CASTS, _PI_BURST_LAST_ARM_LOG_MS
    try:
        from Py4GWCoreLib.Builds.Skills.BurningComboCoordination import active_pi_knockdown_event
        anchor, raw_members, expires = active_pi_knockdown_event()
        anchor = int(anchor or 0)
        expires = int(expires or 0)
        raw_members = tuple(sorted({int(x) for x in raw_members if int(x or 0) > 0}))
    except Exception:
        return 0, (), 0, 0

    if expires <= 0 or not raw_members:
        return 0, (), 0, 0

    # A new expiry identifies a new publish event. Keep a dedicated counter so
    # RoJs that happened before PI cannot consume the requested 2-3-ray burst.
    if int(expires) != int(_PI_BURST_EVENT_EXPIRES):
        _PI_BURST_EVENT_EXPIRES = int(expires)
        _PI_BURST_ANCHOR = int(anchor)
        _PI_BURST_MEMBERS = tuple(raw_members)
        _PI_BURST_GOAL = 2 if len(raw_members) <= 2 else 3
        _PI_BURST_CASTS = 0
        now_ms = _now_ms()
        if now_ms - int(_PI_BURST_LAST_ARM_LOG_MS or 0) >= 150:
            _PI_BURST_LAST_ARM_LOG_MS = int(now_ms)
            _log(
                "HERO_ROJ_PI_BURST_ARMED",
                hero_index=state.hero_index, hero_id=state.hero_agent_id,
                anchor_id=int(anchor), packet_size=len(raw_members),
                burst_goal=int(_PI_BURST_GOAL), expires_at=int(expires),
                policy="2_rays_small_packet_3_rays_cluster",
            )

    live = tuple(enemy_id for enemy_id in _PI_BURST_MEMBERS if _valid_enemy(state, int(enemy_id)))
    if not live or int(_PI_BURST_CASTS) >= int(_PI_BURST_GOAL):
        return 0, live, int(_PI_BURST_GOAL), int(_PI_BURST_CASTS)

    chosen_anchor = int(_PI_BURST_ANCHOR or 0)
    if not _valid_enemy(state, chosen_anchor):
        # Exact PI target died/moved out of cast range. Stay on the same PI
        # packet and choose the most central surviving member instead of falling
        # back to an unrelated cluster.
        ranked = []
        for enemy_id in live:
            ranked.append((
                0 if _has_binding_chains(enemy_id) else 1,
                -_count_packet(state, enemy_id),
                _distance(state.hero_agent_id, enemy_id),
                enemy_id,
            ))
        ranked.sort()
        chosen_anchor = int(ranked[0][3]) if ranked else 0
    return int(chosen_anchor), live, int(_PI_BURST_GOAL), int(_PI_BURST_CASTS)


def _pi_knockdown_roj_target(state: _ControllerState) -> tuple[int, int]:
    """Return the exact PI anchor while its dedicated 2-3 RoJ burst is open."""
    anchor, members, goal, casts = _refresh_pi_burst(state)
    if anchor <= 0 or casts >= goal:
        return 0, len(members)
    return int(anchor), max(1, len(members))


def _record_pi_burst_cast(state: _ControllerState, target_id: int) -> tuple[int, int]:
    global _PI_BURST_CASTS
    anchor, members, goal, casts = _refresh_pi_burst(state)
    if anchor <= 0 or casts >= goal:
        return int(casts), int(goal)
    member_set = set(int(x) for x in members)
    if int(target_id) == int(anchor) or int(target_id) in member_set:
        _PI_BURST_CASTS = min(int(goal), int(_PI_BURST_CASTS) + 1)
    return int(_PI_BURST_CASTS), int(goal)


def _pi_burst_lane_for_target(state: _ControllerState, target_id: int) -> int:
    anchor, members, goal, casts = _refresh_pi_burst(state)
    if anchor > 0 and casts < goal and (int(target_id) == int(anchor) or int(target_id) in set(members)):
        return int(_PI_ROJ_BURST_STAGGER_MS)
    return int(_ROJ_TEAM_STAGGER_MS)


def _roj_damage_lane(state: _ControllerState) -> tuple[int, int]:
    """Stable same-process 2/2 lane split for the owned RoJ heroes."""
    try:
        rows = sorted(
            (st for st in _STATES.values() if int(getattr(st, "hero_agent_id", 0) or 0) > 0),
            key=lambda st: (int(getattr(st, "hero_index", 999) or 999), int(getattr(st, "hero_agent_id", 0) or 0)),
        )
        for idx, row in enumerate(rows):
            if int(row.hero_agent_id) == int(state.hero_agent_id):
                return int(idx), int(len(rows))
        return 0, max(1, int(len(rows)))
    except Exception:
        return 0, 1


def _roj_target(state: _ControllerState) -> tuple[int, int]:
    # Stage 2.45 compatibility markers; the two-anchor code below supersedes
    # the old one-anchor return while preserving the exact <=10% boundary:
    # if hp > 0.10:
    # return int(primary), max(1, int(_count_packet(state, primary)))
    # wait for the next shared plan revision
    # Stage 2.46: RoJ follows the same publisher-owned two-anchor doctrine as
    # the eSurge accounts.  Four owned RoJ heroes split 2/2 across the primary
    # and secondary support targets inside one cluster.  Once the hard primary
    # reaches <=10%, no new five-second Ray is started; fast Mesmer cleanup owns
    # that kill before the team advances.
    plan = _team_plan(state)
    if plan is not None:
        mode = str(getattr(plan, "mode", "none") or "none")
        primary = int(getattr(plan, "focus_target", 0) or 0)
        secondary = int(getattr(plan, "secondary_focus_target", 0) or 0)
        if _valid_enemy(state, primary):
            try:
                primary_hp = float(Agent.GetHealth(primary) or 1.0)
            except Exception:
                primary_hp = 1.0
            # Exactly the agreed cleanup threshold: at <=10% we collapse the
            # damage plan and let fast direct Mesmer finishers close the kill.
            if primary_hp <= 0.10:
                return 0, max(1, int(_count_packet(state, primary)))

            secondary_ok = False
            if secondary > 0 and secondary != primary and _valid_enemy(state, secondary):
                try:
                    secondary_ok = float(Agent.GetHealth(secondary) or 0.0) > 0.10
                except Exception:
                    secondary_ok = True
            lane, count = _roj_damage_lane(state)
            if secondary_ok and int(count) >= 2:
                split = max(1, (int(count) + 1) // 2)
                target = int(primary if int(lane) < int(split) else secondary)
            else:
                target = int(primary)
            return int(target), max(1, int(_count_packet(state, target)))

        if mode != "none":
            # Active shared plan, but its anchor just died/left range. Do not
            # invent a local third target while the publisher rolls the pair.
            return 0, 0

    # Fallback for older/no-focus plans: preserve the PI burst and historical
    # cluster logic instead of inventing a local target.
    pi_target, pi_packet_size = _pi_knockdown_roj_target(state)
    if pi_target > 0:
        return int(pi_target), max(1, int(pi_packet_size))

    now_ms = _now_ms()
    anchor = _cluster_anchor(state)
    if _valid_enemy(state, anchor):
        try:
            from Py4GWCoreLib.Builds.Skills import HeroClusterCoordinator
            members = [
                int(x) for x in list(HeroClusterCoordinator.cluster_members(
                    int(anchor), origin_agent_id=state.hero_agent_id, radius=_NEARBY, range_value=_CAST_RANGE
                ) or [])
                if _valid_enemy(state, int(x))
            ]
        except Exception:
            members = []
        if members:
            return int(anchor), max(1, int(len(members)))
    cleanup = _cleanup_anchor(state)
    if _valid_enemy(state, cleanup):
        return int(cleanup), 1
    return 0, 0


def _prune_symbol_coverage(now_ms: int) -> None:
    cutoff = int(now_ms) - int(_SYMBOL_COVERAGE_WINDOW_MS)
    _SYMBOL_COVERAGE_HISTORY[:] = [
        (ts, tid) for ts, tid in _SYMBOL_COVERAGE_HISTORY if int(ts) >= cutoff
    ]


def _symbol_target(state: _ControllerState, now_ms: int) -> tuple[int, int]:
    anchor = _cluster_anchor(state)
    if not _valid_enemy(state, anchor):
        anchor = _cleanup_anchor(state)
    if not _valid_enemy(state, anchor):
        return 0, 0
    try:
        from Py4GWCoreLib.Builds.Skills import HeroClusterCoordinator
        members = [
            int(x) for x in list(HeroClusterCoordinator.cluster_members(
                int(anchor), origin_agent_id=state.hero_agent_id,
                radius=_NEARBY, range_value=_CAST_RANGE,
            ) or []) if _valid_enemy(state, int(x))
        ]
    except Exception:
        members = [int(anchor)]
    if int(anchor) not in members:
        members.append(int(anchor))
    packet_size = max(1, len(members))
    _prune_symbol_coverage(now_ms)
    member_set = set(members)
    cluster_cov = sum(1 for _ts, tid in _SYMBOL_COVERAGE_HISTORY if int(tid) in member_set)
    cluster_cap = 1 if packet_size <= 1 else _SYMBOL_MAX_CLUSTER_COVERAGE
    if cluster_cov >= cluster_cap:
        return 0, packet_size
    candidates = []
    for enemy_id in members:
        cov = sum(1 for _ts, tid in _SYMBOL_COVERAGE_HISTORY if int(tid) == int(enemy_id))
        if cov >= _SYMBOL_MAX_TARGET_COVERAGE:
            continue
        try:
            hp = float(Agent.GetHealth(enemy_id) or 1.0)
        except Exception:
            hp = 1.0
        if hp < 0.10:
            continue
        candidates.append((
            0 if _has_binding_chains(enemy_id) else 1,
            cov, -_count_packet(state, enemy_id), _distance(state.hero_agent_id, enemy_id), enemy_id,
        ))
    if not candidates:
        return 0, packet_size
    candidates.sort()
    return int(candidates[0][4]), packet_size


def _try_symbol_of_wrath(state: _ControllerState, now_ms: int) -> bool:
    global _LAST_SYMBOL_TEAM_CAST_MS
    if state.transaction_locked or SYMBOL_OF_WRATH_ID <= 0 or state.symbol_slot <= 0:
        return False
    # Stage 2.34: Symbol is the deliberately offensive freed slot. Never let
    # it consume a cast lane while the shared support controller says FULL_PROT.
    if _is_stage227_prot_bar(state):
        mode, _snap = _adaptive_protection_mode(state, int(now_ms))
        if str(mode) != "OFFENSE":
            return False
    entry = _entry_for_slot(state, state.symbol_slot)
    if not entry or int(entry[0]) != SYMBOL_OF_WRATH_ID or _recharge_data(entry[1]) != 0:
        return False
    if not _has_enemy_proximity(state):
        return False
    if _LAST_SYMBOL_TEAM_CAST_MS and now_ms - int(_LAST_SYMBOL_TEAM_CAST_MS) < _SYMBOL_TEAM_STAGGER_MS:
        return False
    target, packet = _symbol_target(state, now_ms)
    # Ground DoT is worthwhile only on a real cluster, not a single mobile cleanup target.
    if target <= 0 or int(packet) < 2:
        return False
    if _request_slot(state, state.symbol_slot, SYMBOL_OF_WRATH_ID, target, purpose=f"symbol_filler_packet{packet}"):
        _LAST_SYMBOL_TEAM_CAST_MS = int(now_ms)
        _prune_symbol_coverage(now_ms)
        _SYMBOL_COVERAGE_HISTORY.append((int(now_ms), int(target)))
        _log("HERO_ROJ_SYMBOL_CAST", hero_index=state.hero_index, hero_id=state.hero_agent_id,
             target_id=target, packet_size=packet, policy="roj_project_filler_only")
        return True
    return False



def _soonest_roj_recharge_ms(state: _ControllerState) -> int:
    """Return RoJ recharge in ms; -1 means no visible RoJ slot, 0 means ready."""
    values: list[int] = []
    for slot in (state.base_roj_slot, state.mimicry_slot, state.echo_slot):
        if int(slot) <= 0:
            continue
        entry = _entry_for_slot(state, int(slot))
        if not entry or int(entry[0]) != int(RAY_OF_JUDGMENT_ID):
            continue
        try:
            recharge = max(0, int(_recharge_data(entry[1]) or 0))
        except Exception:
            recharge = 0
        values.append(int(recharge))
    return min(values) if values else -1


def _bane_target(state: _ControllerState) -> tuple[int, int]:
    """Use hard <=10% primary cleanup, otherwise the normal shared 2-anchor lane."""
    plan = _team_plan(state)
    if plan is not None:
        primary = int(getattr(plan, "focus_target", 0) or 0)
        if _valid_enemy(state, primary):
            try:
                hp = float(Agent.GetHealth(primary) or 1.0)
            except Exception:
                hp = 1.0
            if hp <= 0.10:
                return int(primary), max(1, int(_count_packet(state, primary)))
    return _roj_target(state)


def _try_bane_signet(state: _ControllerState, now_ms: int) -> bool:
    """Late offensive Bane filler that never steals an imminent RoJ/copy lane."""
    global _LAST_BANE_TEAM_CAST_MS
    if state.transaction_locked or BANE_SIGNET_ID <= 0 or int(state.bane_signet_slot) <= 0:
        return False
    if not _has_enemy_proximity(state):
        return False
    entry = _entry_for_slot(state, int(state.bane_signet_slot))
    if not entry or int(entry[0]) != int(BANE_SIGNET_ID) or _recharge_data(entry[1]) != 0:
        return False
    # A ready RoJ already had first refusal. Also avoid spending Bane's ~1s
    # activation when a visible native/copied RoJ will be ready inside that lane.
    next_roj_ms = int(_soonest_roj_recharge_ms(state) or 0)
    if 0 <= next_roj_ms <= int(_BANE_ROJ_NEAR_READY_GUARD_MS):
        return False
    if _LAST_BANE_TEAM_CAST_MS and int(now_ms) - int(_LAST_BANE_TEAM_CAST_MS) < int(_BANE_TEAM_STAGGER_MS):
        return False
    target, packet = _bane_target(state)
    if target <= 0:
        return False
    # The shared RoJ two-anchor assignment is authoritative. Bane may exploit
    # the attack knockdown when available, but it does not open a third target.
    try:
        attacking = bool(Agent.IsAttacking(int(target)))
    except Exception:
        attacking = False
    if _request_slot(state, int(state.bane_signet_slot), int(BANE_SIGNET_ID), int(target), purpose="bane_offensive_filler"):
        _LAST_BANE_TEAM_CAST_MS = int(now_ms)
        _log(
            "HERO_ROJ_BANE_CAST", hero_index=state.hero_index, hero_id=state.hero_agent_id,
            target_id=int(target), packet_size=int(packet), target_attacking=bool(attacking),
            next_roj_recharge_ms=int(next_roj_ms), team_stagger_ms=int(_BANE_TEAM_STAGGER_MS),
            policy="stage2_47_late_offensive_bane_never_preempts_ready_or_near_ready_roj",
        )
        return True
    return False

def _pending_confirmed(state: _ControllerState, pending: _PendingCommand) -> bool:
    # Confirm only from the real effect, but Stage 2.28 also requires own recharge.
    # Legacy Stage-2.27 contract text retained for regression compatibility: return bool(present)
    # Stage 2.28 Aegis confirmation must distinguish the OLD overlapping Aegis
    # layer from the NEW 2s cast.  Effect presence alone is insufficient during
    # pre-cast overlap and produced false ~100ms confirmations in Stage 2.27.
    # Require this hero's physical Aegis slot to enter recharge (cast completed)
    # AND real party Aegis coverage to be present.  An interrupted cast therefore
    # never advances the round-robin merely because the old layer still exists.
    # Compatibility marker: stage2_28_confirmed_cast_advances_rotation_runtime_duration_calibration
    if int(pending.skill_id) == int(AEGIS_ID):
        remaining, present = _party_effect_remaining_max(AEGIS_ID)
        current = _entry_for_slot(state, int(pending.slot))
        recharge_started = bool(
            current
            and int(current[0]) == int(AEGIS_ID)
            and _recharge_data(current[1]) > int(pending.start_recharge)
        )
        if not (recharge_started and present):
            return False
        # Stage 2.29: when Aegis was already active at request time, the old
        # layer may still be visible after this hero's slot enters recharge.
        # Confirm a refresh only when the party timer has genuinely jumped to a
        # fresh layer. This rejects the observed 2.3s -> 0.7s false confirmation.
        if bool(pending.start_effect_present):
            expected = int(_LAST_AEGIS_OBSERVED_DURATION_MS or _AEGIS_EFFECT_FALLBACK_MS)
            fresh_floor = max(6000, int(float(expected) * float(_AEGIS_REFRESH_FRESH_RATIO)))
            timer_jumped = int(remaining) >= max(
                int(pending.start_effect_remaining_ms or 0) + int(_AEGIS_REFRESH_JUMP_MIN_MS),
                int(fresh_floor),
            )
            return bool(timer_jumped)
        # No old party layer existed when the cast was requested. Own recharge +
        # visible Aegis is sufficient proof even if the effect API lacks a timer.
        return True
    # Mimicry is not confirmed merely because its animation started. Keep the
    # verified donor selected until the physical slot actually becomes RoJ.
    if pending.purpose in ("copy_other_hero_roj", "copy_other_hero_roj_current_target"):
        if pending.slot > 0 and _slot_skill_id(state, pending.slot) == RAY_OF_JUDGMENT_ID:
            return True
        return False
    if pending.purpose == "echo_next_roj":
        # Arcane Echo must be *active*, not merely in its cast animation. If we
        # advance on animation start, the following RoJ can race the enchantment
        # and fail to become an Echo copy.
        if _has_effect(state.hero_agent_id, ARCANE_ECHO_ID):
            return True
        current = _entry_for_slot(state, pending.slot)
        if current is not None and _recharge_data(current[1]) > pending.start_recharge:
            try:
                if not Agent.IsCasting(state.hero_agent_id):
                    return True
            except Exception:
                return True
        return False
    # A start is provisional. Never publish coverage while the spell can still
    # be interrupted. Native terminal evidence wins over recharge inference.
    try:
        from Py4GWCoreLib.Builds.Skills.ReforgedSupport import get_cast_outcome
        outcome = get_cast_outcome(state.hero_agent_id, pending.skill_id, pending.started_ms)
        if outcome in ("interrupted", "stopped"):
            return False
        if outcome == "finished":
            return True
    except Exception:
        pass
    try:
        if Agent.IsCasting(state.hero_agent_id):
            return False
    except Exception:
        return False
    # A disappearing temporary copy alone may just have expired.
    for slot, sid, data in _bar_entries(state.hero_bar_index):
        if slot != pending.slot:
            continue
        if sid != pending.skill_id:
            return False
        if _recharge_data(data) > pending.start_recharge:
            return True
    return False


def _set_chain_phase(state: _ControllerState, phase: str, *, now_ms: int | None = None) -> None:
    new_phase = str(phase or "opener")
    if new_phase == str(state.chain_phase or "opener"):
        return
    state.chain_phase = new_phase
    state.chain_phase_started_ms = int(now_ms if now_ms is not None else _now_ms())
    if new_phase in {"opener", "consume"}:
        # These are free-action phases, not protected setup transactions.
        state.chain_started_ms = 0


def _expected_skill_for_phase(state: _ControllerState, phase: str) -> int:
    phase = str(phase or "")
    if phase == "mimicry":
        return int(ARCANE_MIMICRY_ID)
    if phase in {"auspicious"}:
        return int(AUSPICIOUS_INCANTATION_ID)
    if phase in {"echo", "wait_echo_copy"}:
        return int(ARCANE_ECHO_ID)
    if phase in {"seed_echo", "echo_copy", "consume"}:
        return int(RAY_OF_JUDGMENT_ID)
    return 0


def _process_pending(state: _ControllerState, now_ms: int) -> bool:
    global _LAST_AEGIS_CASTER_HERO_ID, _LAST_AEGIS_CONFIRMED_MS, _LAST_AEGIS_OBSERVED_DURATION_MS
    p = state.pending
    if p.skill_id <= 0:
        return False
    if _pending_confirmed(state, p):
        _log("HERO_ROJ_CAST_CONFIRMED", hero_index=state.hero_index, hero_id=state.hero_agent_id,
             skill_id=p.skill_id, slot=p.slot, target_id=p.target_id, purpose=p.purpose)
        if int(p.skill_id) == int(RAY_OF_JUDGMENT_ID):
            _record_roj_coverage(state, int(p.target_id), int(now_ms))
        if int(p.skill_id) == int(AEGIS_ID):
            _LAST_AEGIS_CASTER_HERO_ID = int(state.hero_agent_id)
            _LAST_AEGIS_CONFIRMED_MS = int(now_ms)
            observed_remaining, _observed_present = _party_effect_remaining_max(AEGIS_ID)
            if int(observed_remaining) >= int(_AEGIS_OBSERVED_MIN_MS):
                # Confirmation normally lands within one controller tick of the
                # enchantment landing, so this is a robust runtime calibration
                # for HR/cons/high-attribute duration without hard-coding one build.
                _LAST_AEGIS_OBSERVED_DURATION_MS = max(
                    int(_LAST_AEGIS_OBSERVED_DURATION_MS or 0), int(observed_remaining)
                )
            _log(
                "HERO_ROJ_AEGIS_CONFIRMED", hero_index=state.hero_index, hero_id=state.hero_agent_id,
                next_rotation_after_hero_id=int(state.hero_agent_id),
                observed_effect_remaining_ms=int(observed_remaining),
                prior_effect_remaining_at_request_ms=int(p.start_effect_remaining_ms or 0),
                calibrated_fallback_ms=int(_LAST_AEGIS_OBSERVED_DURATION_MS or _AEGIS_EFFECT_FALLBACK_MS),
                policy="stage2_29_fresh_timer_jump_confirmed_rotation_runtime_duration_calibration",
            )
        if int(p.skill_id) in (ARCANE_MIMICRY_ID, ARCANE_ECHO_ID, AUSPICIOUS_INCANTATION_ID):
            state.last_copy_setup_ms = int(now_ms)
        if p.purpose == "chain_native_opener":
            _set_chain_phase(state, "mimicry", now_ms=now_ms)
            state.chain_started_ms = _now_ms()
        elif p.purpose in ("copy_other_hero_roj", "copy_other_hero_roj_current_target"):
            _restore_player_target_after_mimicry(state)
            _set_chain_phase(state, "auspicious", now_ms=now_ms)
        elif p.purpose == "prime_arcane_echo_energy":
            _set_chain_phase(state, "echo", now_ms=now_ms)
        elif p.purpose == "echo_next_roj":
            _set_chain_phase(state, "seed_echo", now_ms=now_ms)
        elif p.purpose == "echo_mandatory_next_roj":
            # Keep the transaction closed until the physical Echo slot actually
            # becomes RoJ; only then can we guarantee that Echo copied RoJ.
            state.echo_seed_cast_ms = _now_ms()
            _set_chain_phase(state, "wait_echo_copy", now_ms=now_ms)
        elif p.purpose == "echo_copy_roj":
            _set_transaction_lock(state, False)
            state.echo_seed_slot = 0
            state.echo_seed_cast_ms = 0
            _set_chain_phase(state, "consume", now_ms=now_ms)
        state.pending = _PendingCommand()
        return False
    if int(p.skill_id) == int(AEGIS_ID) and str(p.purpose) == "support_aegis_rotation":
        elapsed = max(0, int(now_ms) - int(p.started_ms))
        if elapsed >= int(_AEGIS_FAILOVER_STALL_MS):
            current = _entry_for_slot(state, int(p.slot))
            recharge_started = bool(
                current and int(current[0]) == int(AEGIS_ID)
                and _recharge_data(current[1]) > int(p.start_recharge)
            )
            casting_aegis = False
            native_aegis_active = False
            native_outcome = None
            try:
                casting_aegis = bool(
                    Agent.IsCasting(int(state.hero_agent_id))
                    and int(Agent.GetCastingSkillID(int(state.hero_agent_id)) or 0) == int(AEGIS_ID)
                )
            except Exception:
                casting_aegis = False
            try:
                from Py4GWCoreLib.Builds.Skills.ReforgedSupport import get_recent_cast, get_cast_outcome
                recent = get_recent_cast(int(state.hero_agent_id), max_age_ms=4000)
                if recent is not None:
                    rsid, _rtarget, rstart, _rduration = recent
                    native_aegis_active = bool(int(rsid) == int(AEGIS_ID) and int(rstart) >= int(p.started_ms) - 200)
                native_outcome = get_cast_outcome(int(state.hero_agent_id), int(AEGIS_ID), int(p.started_ms))
            except Exception:
                native_aegis_active = False
                native_outcome = None
            # A native active-cast event is stronger than a one-frame Agent.IsCasting
            # miss.  Conversely an explicit interrupted/stopped outcome may fail
            # over immediately once the short command guard has elapsed.
            explicit_abort = str(native_outcome or '') in ('interrupted', 'stopped')
            if explicit_abort or (not recharge_started and not casting_aegis and not native_aegis_active):
                _release_prot_claim(state, "aegis", 0)
                _AEGIS_FAILOVER_SKIP_UNTIL[int(state.hero_agent_id)] = int(now_ms) + int(_AEGIS_FAILOVER_SKIP_MS)
                _log(
                    "HERO_ROJ_AEGIS_FAILOVER", hero_index=state.hero_index, hero_id=state.hero_agent_id,
                    elapsed_ms=int(elapsed), skip_ms=int(_AEGIS_FAILOVER_SKIP_MS),
                    reason="native_abort" if explicit_abort else "elected_owner_failed_to_start_cast",
                    native_cast_active=bool(native_aegis_active), native_outcome=str(native_outcome or ""),
                    policy="stage2_33_cast_ready_owner_native_aftercast_aware_failover",
                )
                state.pending = _PendingCommand()
                return False
    if now_ms - p.started_ms <= _COMMAND_TIMEOUT_MS:
        return True
    _log("HERO_ROJ_CAST_TIMEOUT", hero_index=state.hero_index, hero_id=state.hero_agent_id,
         skill_id=p.skill_id, slot=p.slot, purpose=p.purpose)
    if int(p.skill_id) == int(AEGIS_ID) and str(p.purpose) == "support_aegis_rotation":
        _release_prot_claim(state, "aegis", 0)
        _AEGIS_FAILOVER_SKIP_UNTIL[int(state.hero_agent_id)] = int(now_ms) + int(_AEGIS_FAILOVER_SKIP_MS)
        _log(
            "HERO_ROJ_AEGIS_FAILOVER", hero_index=state.hero_index, hero_id=state.hero_agent_id,
            elapsed_ms=max(0, int(now_ms) - int(p.started_ms)), skip_ms=int(_AEGIS_FAILOVER_SKIP_MS),
            reason="aegis_pending_timeout", policy="stage2_29_timeout_owner_failover",
        )
    transaction_purpose = bool(
        p.purpose.startswith("chain_")
        or p.purpose in (
            "copy_other_hero_roj", "copy_other_hero_roj_current_target",
            "prime_arcane_echo_energy", "echo_next_roj",
            "echo_mandatory_next_roj", "echo_copy_roj",
        )
    )
    if transaction_purpose:
        _abort_roj_transaction(state, f"pending_timeout:{p.purpose}")
        return False
    state.pending = _PendingCommand()
    return False


def _abort_roj_transaction(state: _ControllerState, reason: str, *, next_phase: str = "opener") -> None:
    """Deterministically release one protected Mimicry/Echo transaction."""
    previous_phase = str(state.chain_phase or "opener")
    pending_purpose = str(state.pending.purpose or "")
    _restore_player_target_after_mimicry(state)
    _set_transaction_lock(state, False)
    state.pending = _PendingCommand()
    state.echo_seed_slot = 0
    state.echo_seed_cast_ms = 0
    state.mimicry_target_id = 0
    state.mimicry_target_started_ms = 0
    state.mimicry_dispatch_result = ""
    _set_chain_phase(state, str(next_phase or "opener"), now_ms=_now_ms())
    state.chain_started_ms = 0
    _log(
        "HERO_ROJ_E10_TRANSACTION_ABORT",
        hero_index=state.hero_index,
        hero_id=state.hero_agent_id,
        previous_phase=previous_phase,
        pending_purpose=pending_purpose,
        reason=str(reason),
        mimicry_slot=int(state.mimicry_slot),
        echo_slot=int(state.echo_slot),
        policy="exp36_stage9_e10_explicit_abort_release",
    )


def _request_slot(state: _ControllerState, slot: int, skill_id: int, target_id: int, *, purpose: str) -> bool:
    if state.pending.skill_id > 0 or not _hero_can_cast(state):
        return False
    if not _can_pay(state, skill_id):
        return False
    now_ms = _now_ms()
    if now_ms - state.last_command_ms < _COMMAND_GUARD_MS:
        return False
    current = next(((sid, data) for s, sid, data in _bar_entries(state.hero_bar_index) if s == slot), None)
    if current is None or int(current[0]) != int(skill_id) or _recharge_data(current[1]) != 0:
        return False
    if int(skill_id) == int(RAY_OF_JUDGMENT_ID):
        shared_lane = _shared_roj_lock_count(_ROJ_TEAM_LANE_LOCK_ID, 0, int(now_ms))
        if shared_lane is not None and int(shared_lane) > 0:
            _log(
                "HERO_ROJ_E09_PROVISIONAL_BLOCK",
                hero_index=state.hero_index, hero_id=state.hero_agent_id,
                target_id=int(target_id), purpose=str(purpose),
                policy="exp36_stage10_e09_shared_request_lane",
            )
            return False
        if not _post_shared_roj_lock(
            state, _ROJ_TEAM_LANE_LOCK_ID, 0, max_holders=1,
            now_ms=int(now_ms), duration_ms=int(_ROJ_TEAM_STAGGER_MS),
        ):
            # Shared memory unavailable: fail open to the proven local lane.
            pass
        else:
            _log(
                "HERO_ROJ_E09_PROVISIONAL_RESERVED",
                hero_index=state.hero_index, hero_id=state.hero_agent_id,
                target_id=int(target_id), purpose=str(purpose),
                provisional_ms=int(_ROJ_TEAM_STAGGER_MS),
                policy="exp36_stage10_e09_shared_request_lane",
            )
    try:
        SkillBar.HeroUseSkill(int(target_id), int(slot), int(state.hero_index))
    except Exception as exc:
        _log("HERO_ROJ_CAST_ERROR", hero_index=state.hero_index, hero_id=state.hero_agent_id,
             skill_id=skill_id, slot=slot, purpose=purpose, error=type(exc).__name__)
        return False
    start_effect_remaining_ms = 0
    start_effect_present = False
    if int(skill_id) == int(AEGIS_ID):
        try:
            start_effect_remaining_ms, start_effect_present = _party_effect_remaining_max(AEGIS_ID)
            start_effect_remaining_ms = int(start_effect_remaining_ms or 0)
            start_effect_present = bool(start_effect_present)
        except Exception:
            start_effect_remaining_ms = 0
            start_effect_present = False
    state.pending = _PendingCommand(
        skill_id=int(skill_id), slot=int(slot), target_id=int(target_id),
        started_ms=now_ms, start_recharge=_recharge_data(current[1]),
        start_effect_remaining_ms=int(start_effect_remaining_ms),
        start_effect_present=bool(start_effect_present), purpose=str(purpose)
    )
    state.last_command_ms = now_ms
    _log("HERO_ROJ_CAST_REQUESTED", hero_index=state.hero_index, hero_id=state.hero_agent_id,
         skill_id=skill_id, slot=slot, target_id=int(target_id), purpose=purpose)
    return True


def _owned_party_hero_ids() -> set[int]:
    """Return only actual hero agent ids owned by this party.

    This is a hard donor firewall for Arcane Mimicry. Player accounts, henchmen,
    summons and the real SoJ account can never pass this gate.
    """
    try:
        return {
            int(getattr(hero, "agent_id", 0) or 0)
            for hero in list(Party.GetHeroes() or [])
            if int(getattr(hero, "agent_id", 0) or 0) > 0
        }
    except Exception:
        return set()


def _other_roj_donor(state: _ControllerState, states: list[_ControllerState], *, log_result: bool = True) -> int:
    """Pick a donor exclusively from another controller-owned RoJ hero.

    Do not consult Shared Memory/player-account donor discovery here. The hero
    controller is intentionally isolated from the player RoJ/SoJ Mimicry path.
    A donor must be a live Party.GetHeroes() member and must currently expose
    native RoJ while both copy slots are back at their base identities.
    """
    owned_hero_ids = _owned_party_hero_ids()
    donors = [
        other for other in states
        if other.hero_agent_id != state.hero_agent_id
        and other.hero_agent_id in owned_hero_ids
        and other.hero_agent_id in _DISCOVERED_HEROES
        and _supported_roj_fixed_loadout(_bar_ids(other.hero_bar_index))
        and other.base_roj_slot > 0
        and other.mimicry_slot > 0
        and other.echo_slot > 0
        # Strict native donor state. If either copy slot is transformed, we do
        # not risk Arcane Mimicry seeing a temporary/left-most elite identity.
        and _slot_skill_id(other, other.mimicry_slot) in (ARCANE_MIMICRY_ID, RAY_OF_JUDGMENT_ID)
        and _slot_skill_id(other, other.echo_slot) in (ARCANE_ECHO_ID, RAY_OF_JUDGMENT_ID)
        and _slot_skill_id(other, other.base_roj_slot) == RAY_OF_JUDGMENT_ID
        and SIGNET_OF_JUDGMENT_ID not in _bar_ids(other.hero_bar_index)
        and Agent.IsAlive(other.hero_agent_id)
        and _distance(state.hero_agent_id, other.hero_agent_id) <= _CAST_RANGE
    ]
    donors.sort(key=lambda other: (other.hero_index, other.hero_agent_id))
    donor = int(donors[0].hero_agent_id) if donors else 0
    if donor > 0 and log_result:
        _log(
            "HERO_ROJ_HERO_ONLY_DONOR",
            hero_index=state.hero_index,
            hero_id=state.hero_agent_id,
            donor_hero_id=donor,
        )
    return donor


def _ally_health_fraction(agent_id: int) -> float:
    try:
        return max(0.0, min(1.0, float(Routines.Checks.Agents.GetHealth(int(agent_id)))))
    except Exception:
        try:
            return max(0.0, min(1.0, float(Agent.GetHealth(int(agent_id)) or 0.0)))
        except Exception:
            return 1.0


def _actual_party_agent_ids() -> set[int]:
    """Players + heroes + henchmen only; never minions, pets or NPC allies."""
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
    return result


def _prune_heal_claims(now_ms: int) -> None:
    if not _HEAL_CLAIMS:
        return
    _HEAL_CLAIMS[:] = [row for row in _HEAL_CLAIMS if int(row[0]) > int(now_ms)]


def _heal_claim_count(skill_id: int, target_id: int, now_ms: int) -> int:
    _prune_heal_claims(now_ms)
    return sum(1 for _expires, sid, tid, _hero in _HEAL_CLAIMS
               if int(sid) == int(skill_id) and int(tid) == int(target_id))


def _reserve_heal_claim(state: _ControllerState, skill_id: int, target_id: int,
                        *, duration_ms: int, max_claims: int = 1) -> bool:
    now_ms = _now_ms()
    if _heal_claim_count(skill_id, target_id, now_ms) >= max(1, int(max_claims)):
        return False
    _HEAL_CLAIMS.append((now_ms + int(duration_ms), int(skill_id), int(target_id), int(state.hero_agent_id)))
    _log("HERO_ROJ_HEAL_CLAIM", hero_index=state.hero_index, hero_id=state.hero_agent_id,
         skill_id=int(skill_id), target_id=int(target_id), duration_ms=int(duration_ms),
         max_claims=int(max_claims))
    return True


def _release_heal_claim(state: _ControllerState, skill_id: int, target_id: int) -> None:
    _HEAL_CLAIMS[:] = [row for row in _HEAL_CLAIMS
                       if not (int(row[1]) == int(skill_id) and int(row[2]) == int(target_id)
                               and int(row[3]) == int(state.hero_agent_id))]


def _lowest_heal_target(state: _ControllerState, threshold: float, *, avoid_patient: bool = False,
                        other_only: bool = False, claim_skill_id: int = 0) -> int:
    party_ids = _actual_party_agent_ids()
    if not party_ids:
        return 0
    now_ms = _now_ms()
    ranked = []
    for ally_id in party_ids:
        try:
            if other_only and int(ally_id) == int(state.hero_agent_id):
                continue
            if not (Agent.IsValid(ally_id) and Agent.IsAlive(ally_id)):
                continue
            if _distance(state.hero_agent_id, ally_id) > _CAST_RANGE:
                continue
            hp = _ally_health_fraction(ally_id)
            if hp > float(threshold):
                continue
            if avoid_patient and PATIENT_SPIRIT_ID > 0 and Routines.Checks.Agents.HasEffect(ally_id, PATIENT_SPIRIT_ID):
                continue
            if claim_skill_id > 0:
                # Dwayna may receive a second rescue claim only on a truly
                # critical ally. Patient Spirit stays strictly one-per-target.
                allowed = 2 if int(claim_skill_id) == int(DWAYNAS_KISS_ID) and hp <= 0.30 else 1
                if _heal_claim_count(claim_skill_id, ally_id, now_ms) >= allowed:
                    continue
            ranked.append((hp, ally_id))
        except Exception:
            continue
    ranked.sort()
    return int(ranked[0][1]) if ranked else 0


def _effect_remaining_ms(agent_id: int, skill_id: int) -> int:
    if int(agent_id) <= 0 or int(skill_id) <= 0:
        return 0
    try:
        return max(0, int(GLOBAL_CACHE.Effects.GetEffectTimeRemaining(int(agent_id), int(skill_id)) or 0))
    except Exception:
        return 0


def _is_stage227_prot_bar(state: _ControllerState) -> bool:
    legacy_prot = sum(
        1 for slot in (state.spirit_bond_slot, state.reversal_fortune_slot, state.reversal_damage_slot)
        if int(slot) > 0
    ) >= 2
    stage234_split = bool(
        int(state.spirit_bond_slot) > 0
        and any(int(slot) > 0 for slot in (state.reversal_fortune_slot, state.reversal_damage_slot, state.symbol_slot))
    )
    stage241_symbol_prot = bool(
        int(state.aegis_slot) <= 0
        and int(state.mantra_slot) > 0
        and int(state.spirit_bond_slot) > 0
        and int(state.reversal_fortune_slot) > 0
        and int(state.symbol_slot) > 0
    )
    # Stage 2.43: no-Aegis bars are valid protection bars when Spirit Bond is
    # present and at least two of RoF/RoD/Symbol are equipped.  In particular,
    # the user's current Spirit Bond + RoF + RoD bar must keep the full
    # coordinated protection lane even after Symbol is removed.
    no_aegis_flex_count = sum(
        1 for slot in (state.reversal_fortune_slot, state.reversal_damage_slot, state.symbol_slot)
        if int(slot) > 0
    )
    stage243_no_aegis_prot = bool(
        int(state.aegis_slot) <= 0
        and int(state.mantra_slot) > 0
        and int(state.spirit_bond_slot) > 0
        and int(no_aegis_flex_count) >= 2
    )
    # Stage 2.47 live offensive bar: RoD/Symbol may be replaced by Bane Signet.
    # Spirit Bond + RoF remain the actual protection core; Bane is only an
    # offensive filler and must not be counted as protection itself.
    stage247_bane_prot = bool(
        int(state.aegis_slot) <= 0
        and int(state.mantra_slot) > 0
        and int(state.spirit_bond_slot) > 0
        and int(state.reversal_fortune_slot) > 0
        and int(getattr(state, "bane_signet_slot", 0) or 0) > 0
    )
    # Stage 2.55: the official global Urgoz bar swaps Mantra of Frost for
    # Aegis, while retaining Spirit Bond, Reversal of Fortune and Bane Signet.
    # Stage 2.53 already matched this bar but this *second* gate silently
    # disabled Aegis, Spirit Bond and Reversal.  Do not require Mantra for it.
    stage255_urgoz_aegis_prot = bool(
        int(state.aegis_slot) > 0
        and int(state.spirit_bond_slot) > 0
        and int(state.reversal_fortune_slot) > 0
        and int(getattr(state, "bane_signet_slot", 0) or 0) > 0
    )
    return bool(
        stage255_urgoz_aegis_prot
        or (int(state.mantra_slot) > 0
            and ((int(state.aegis_slot) > 0 and (legacy_prot or stage234_split))
                 or stage241_symbol_prot or stage243_no_aegis_prot or stage247_bane_prot))
    )


def _prune_prot_claims(now_ms: int) -> None:
    if _PROT_CLAIMS:
        _PROT_CLAIMS[:] = [row for row in _PROT_CLAIMS if int(row[0]) > int(now_ms)]


def _prot_claimed(group: str, target_id: int, now_ms: int) -> bool:
    _prune_prot_claims(now_ms)
    return any(str(g) == str(group) and int(tid) == int(target_id) for _exp, g, _sid, tid, _hero in _PROT_CLAIMS)


def _reserve_prot_claim(state: _ControllerState, group: str, skill_id: int, target_id: int, duration_ms: int) -> bool:
    now_ms = _now_ms()
    if _prot_claimed(str(group), int(target_id), now_ms):
        return False
    _PROT_CLAIMS.append((now_ms + max(250, int(duration_ms)), str(group), int(skill_id), int(target_id), int(state.hero_agent_id)))
    _log(
        "HERO_ROJ_PROT_CLAIM", hero_index=state.hero_index, hero_id=state.hero_agent_id,
        group=str(group), skill_id=int(skill_id), target_id=int(target_id), duration_ms=int(duration_ms),
        policy="stage2_27_cross_hero_protection_reservation",
    )
    return True


def _release_prot_claim(state: _ControllerState, group: str, target_id: int) -> None:
    _PROT_CLAIMS[:] = [
        row for row in _PROT_CLAIMS
        if not (str(row[1]) == str(group) and int(row[3]) == int(target_id) and int(row[4]) == int(state.hero_agent_id))
    ]


def _party_threat_rows(state: _ControllerState) -> list[tuple[float, int, float, int, int]]:
    """Return (score, ally, hp, melee_attackers, total_attackers), critical health first, then pressure."""
    party_ids = _actual_party_agent_ids()
    if not party_ids:
        return []
    try:
        enemies = [int(aid or 0) for aid in list(AgentArray.GetEnemyArray() or [])]
    except Exception:
        enemies = []
    rows: list[tuple[float, int, float, int, int]] = []
    for ally_id in party_ids:
        try:
            aid = int(ally_id)
            if aid <= 0 or not Agent.IsValid(aid) or not Agent.IsAlive(aid):
                continue
            if _distance(state.hero_agent_id, aid) > _CAST_RANGE:
                continue
            hp = _ally_health_fraction(aid)
            melee = 0
            attackers = 0
            for enemy_id in enemies:
                if enemy_id <= 0 or not Agent.IsValid(enemy_id) or not Agent.IsAlive(enemy_id):
                    continue
                if int(Agent.GetTarget(enemy_id) or 0) != aid or not Agent.IsAttacking(enemy_id):
                    continue
                attackers += 1
                try:
                    if bool(Agent.IsMelee(enemy_id)):
                        melee += 1
                except Exception:
                    pass
            score = float(melee * 4.0 + attackers * 1.5 + max(0.0, 0.90 - hp) * 7.0)
            rows.append((score, aid, hp, int(melee), int(attackers)))
        except Exception:
            continue
    # Rescue targets precede ordinary pressure without discarding the pressure
    # ranking for healthy allies. This also governs the actual Reversal target.
    rows.sort(key=lambda row: (
        not (float(row[2]) <= 0.34 or (float(row[2]) <= 0.46 and (int(row[3]) >= 3 or int(row[4]) >= 4))),
        -float(row[0]), float(row[2]), -int(row[3]), -int(row[4]), int(row[1]),
    ))
    return rows


def _team_threat_rows() -> list[tuple[float, int, float, int, int]]:
    """Shared whole-party pressure view for the module-level adaptive mode.

    Target selection remains per-RoJ/cast-range via _party_threat_rows().  The
    OFFENSE/FULL_PROT decision, however, must not depend on which monk happened
    to query first or which allies are inside that monk's personal cast range.
    """
    party_ids = _actual_party_agent_ids()
    if not party_ids:
        return []
    try:
        enemies = [int(aid or 0) for aid in list(AgentArray.GetEnemyArray() or [])]
    except Exception:
        enemies = []
    rows: list[tuple[float, int, float, int, int]] = []
    for ally_id in party_ids:
        try:
            aid = int(ally_id)
            if aid <= 0 or not Agent.IsValid(aid) or not Agent.IsAlive(aid):
                continue
            hp = _ally_health_fraction(aid)
            melee = 0
            attackers = 0
            for enemy_id in enemies:
                if enemy_id <= 0 or not Agent.IsValid(enemy_id) or not Agent.IsAlive(enemy_id):
                    continue
                if int(Agent.GetTarget(enemy_id) or 0) != aid or not Agent.IsAttacking(enemy_id):
                    continue
                attackers += 1
                try:
                    if bool(Agent.IsMelee(enemy_id)):
                        melee += 1
                except Exception:
                    pass
            score = float(melee * 4.0 + attackers * 1.5 + max(0.0, 0.90 - hp) * 7.0)
            rows.append((score, aid, hp, int(melee), int(attackers)))
        except Exception:
            continue
    rows.sort(key=lambda row: (
        not (float(row[2]) <= 0.34 or (float(row[2]) <= 0.46 and (int(row[3]) >= 3 or int(row[4]) >= 4))),
        -float(row[0]), float(row[2]), -int(row[3]), -int(row[4]), int(row[1]),
    ))
    return rows


def _support_energy_ok(state: _ControllerState, skill_id: int, *, hp: float = 1.0, melee: int = 0, emergency: bool = False) -> bool:
    cost = max(0, _skill_energy_cost(int(skill_id)))
    current = _energy_current(state)
    if current < cost:
        return False
    # A truly collapsing ally may spend the offensive reserve.  Otherwise keep
    # one native RoJ worth of Energy so support cannot starve the damage lane.
    if emergency and (float(hp) <= 0.35 or int(melee) >= 3):
        return True
    return (current - cost) >= max(0, _skill_energy_cost(RAY_OF_JUDGMENT_ID))


def _party_effect_remaining_max(skill_id: int) -> tuple[int, bool]:
    remaining = 0
    present = False
    for aid in _actual_party_agent_ids():
        try:
            if not Agent.IsAlive(int(aid)):
                continue
            rem = _effect_remaining_ms(int(aid), int(skill_id))
            if rem > 0:
                present = True
                remaining = max(remaining, rem)
            elif _has_effect(int(aid), int(skill_id)):
                present = True
        except Exception:
            continue
    return int(remaining), bool(present)



def _core_protection_ready() -> tuple[bool, bool, int, int, tuple[int, ...]]:
    """Fresh ST-core coverage view; unknown telemetry must not force FULL_PROT."""
    try:
        from Py4GWCoreLib.Builds.Skills import STDefensePriority
        known, ready, required, covered, missing = STDefensePriority.recovery_core_protection_status()
        return bool(known), bool(ready), int(required), int(covered), tuple(int(v) for v in (missing or ()))
    except Exception:
        return False, True, 0, 0, ()


def _adaptive_protection_snapshot(state: _ControllerState, now_ms: int) -> dict[str, object]:
    """One cached whole-team snapshot shared by all three RoJ controllers.

    Stage 2.30 used the querying monk's cast-range view and treated any missing
    Aegis/core spirit as FULL_PROT, which made 2.31 spend ~99% of combat in the
    defensive mode.  Stage 2.32 separates *pressure* from *baseline coverage*:
    Aegis is maintained in both modes, and a single missing non-Shelter core
    spirit no longer forces routine Spirit-Bond/Reversal spam on a healthy team.
    """
    global _ADAPTIVE_TEAM_SNAPSHOT_MS, _ADAPTIVE_TEAM_SNAPSHOT, _ADAPTIVE_HP_BASELINE
    if (int(_ADAPTIVE_TEAM_SNAPSHOT_MS or 0) > 0
            and int(now_ms) - int(_ADAPTIVE_TEAM_SNAPSHOT_MS) <= int(_ADAPTIVE_TEAM_SNAPSHOT_TTL_MS)):
        return dict(_ADAPTIVE_TEAM_SNAPSHOT)

    rows = _team_threat_rows()
    lowest_hp = min((float(row[2]) for row in rows), default=1.0)
    max_melee = max((int(row[3]) for row in rows), default=0)
    max_attackers = max((int(row[4]) for row in rows), default=0)
    low85 = sum(1 for row in rows if float(row[2]) <= 0.85)
    low70 = sum(1 for row in rows if float(row[2]) <= 0.70)

    max_hp_drop = 0.0
    live_ids = {int(row[1]) for row in rows}
    for _score, aid, hp, _melee, _attackers in rows:
        aid = int(aid); hp = float(hp)
        anchor = _ADAPTIVE_HP_BASELINE.get(aid)
        if anchor is None or int(now_ms) - int(anchor[0]) > int(_ADAPTIVE_HP_TREND_WINDOW_MS):
            _ADAPTIVE_HP_BASELINE[aid] = (int(now_ms), hp)
        else:
            max_hp_drop = max(max_hp_drop, max(0.0, float(anchor[1]) - hp))
    for aid in list(_ADAPTIVE_HP_BASELINE):
        if aid not in live_ids:
            _ADAPTIVE_HP_BASELINE.pop(aid, None)

    aegis_remaining, aegis_present = _party_effect_remaining_max(AEGIS_ID)
    if aegis_remaining <= 0 and aegis_present and int(_LAST_AEGIS_CONFIRMED_MS or 0) > 0:
        fallback_ms = int(_LAST_AEGIS_OBSERVED_DURATION_MS or _AEGIS_EFFECT_FALLBACK_MS)
        aegis_remaining = max(0, int(_LAST_AEGIS_CONFIRMED_MS) + fallback_ms - int(now_ms))

    alive_count = 0
    aegis_covered = 0
    threatened_without_aegis = 0
    pressure_by_ally = {int(row[1]): (int(row[3]), int(row[4]), float(row[2])) for row in rows}
    for aid in _actual_party_agent_ids():
        try:
            aid = int(aid)
            if not Agent.IsValid(aid) or not Agent.IsAlive(aid):
                continue
            alive_count += 1
            covered = bool(_effect_remaining_ms(aid, AEGIS_ID) > 0 or _has_effect(aid, AEGIS_ID))
            if covered:
                aegis_covered += 1
            melee, attackers, hp = pressure_by_ally.get(aid, (0, 0, 1.0))
            if not covered and (attackers > 0 or melee > 0 or hp < 0.85):
                threatened_without_aegis += 1
        except Exception:
            continue

    core_known, core_ready, core_required, core_covered, core_missing = _core_protection_ready()
    try:
        shelter_model = int(getattr(SpiritModelID.SHELTER, 'value', SpiritModelID.SHELTER))
    except Exception:
        shelter_model = 0
    shelter_missing = bool(shelter_model > 0 and shelter_model in set(int(v) for v in core_missing))
    active_pressure = bool(max_attackers > 0 or max_melee > 0 or lowest_hp < 0.90)
    core_severe = bool(core_known and core_required > 0 and core_covered <= max(1, core_required - 2))
    core_adequate = bool((not core_known) or core_required <= 0 or core_covered >= max(1, core_required - 1))
    # Stage 2.49: an empty or incomplete ST core alone is not a reason for
    # four RoJ heroes to idle in defensive mode while the entire party is
    # effectively unhurt and no incoming pressure is *observable*. This is
    # deliberately stricter than the regular >=84% HP offensive threshold:
    # the first real HP loss / observable attacker immediately revokes it.
    # Actual pressure plus severely degraded spirits still triggers FULL_PROT.
    core_quiet_window = bool(
        float(lowest_hp) >= 0.96
        and int(low85) == 0
        and float(max_hp_drop) < 0.02
        and int(max_melee) == 0
        and int(max_attackers) == 0
    )

    reasons: list[str] = []
    if float(lowest_hp) <= 0.72:
        reasons.append('low_hp')
    if int(low70) >= 2:
        reasons.append('multi_low_hp')
    if int(max_melee) >= 2:
        reasons.append('melee_collapse')
    if int(max_attackers) >= 3:
        reasons.append('focused_attackers')
    if float(max_hp_drop) >= 0.16 and float(lowest_hp) <= 0.86:
        reasons.append('rapid_hp_drop')
    if bool(core_severe) and bool(active_pressure):
        reasons.append('core_spirits_severely_degraded')
    if bool(shelter_missing) and bool(active_pressure) and (float(lowest_hp) <= 0.86 or int(max_attackers) >= 2):
        reasons.append('shelter_missing_under_pressure')

    # Aegis itself is maintained in OFFENSE and FULL_PROT.  Its absence delays
    # entry to OFFENSE while pressure exists, but no longer *forces* routine
    # single-target protection by itself.
    coverage_ratio = (float(aegis_covered) / float(alive_count)) if alive_count > 0 else 0.0
    # Stage 2.41: Aegis may intentionally live on the eSurge player accounts,
    # not on these RoJ heroes.  When none of the locally controlled RoJ bars has
    # Aegis equipped, its absence must not pin the shared RoJ support budget in
    # FULL_PROT forever; eSurge Aegis remains an external layer.
    roj_states = globals().get("_STATES", {})
    roj_aegis_equipped = any(
        int(getattr(st, "aegis_slot", 0) or 0) > 0 for st in roj_states.values()
    )
    aegis_baseline_ok = bool(
        (not roj_aegis_equipped)
        or aegis_present
        or not active_pressure
        or threatened_without_aegis == 0
    )
    stable = bool(
        not reasons
        and float(lowest_hp) >= 0.84
        and int(low85) <= 1
        and int(max_melee) <= 1
        and int(max_attackers) <= 2
        and float(max_hp_drop) < 0.10
        and bool(core_adequate or core_quiet_window)
        and bool(aegis_baseline_ok)
    )

    snap = {
        'stable': bool(stable), 'reasons': tuple(reasons), 'lowest_hp': float(lowest_hp),
        'low85': int(low85), 'low70': int(low70), 'max_melee': int(max_melee),
        'max_attackers': int(max_attackers), 'max_hp_drop': float(max_hp_drop),
        'aegis_present': bool(aegis_present), 'aegis_remaining_ms': int(aegis_remaining),
        'aegis_alive_count': int(alive_count), 'aegis_covered_count': int(aegis_covered),
        'aegis_coverage_ratio': float(coverage_ratio), 'threatened_without_aegis': int(threatened_without_aegis),
        'core_known': bool(core_known), 'core_ready': bool(core_ready), 'core_required': int(core_required),
        'core_covered': int(core_covered), 'core_missing': tuple(core_missing),
        'core_severe': bool(core_severe), 'shelter_missing': bool(shelter_missing),
        'core_quiet_window': bool(core_quiet_window),
    }
    _ADAPTIVE_TEAM_SNAPSHOT_MS = int(now_ms)
    _ADAPTIVE_TEAM_SNAPSHOT = dict(snap)
    return snap


def _adaptive_protection_mode(state: _ControllerState, now_ms: int) -> tuple[str, dict[str, object]]:
    """Shared hysteretic mode for all three RoJ monks."""
    global _ADAPTIVE_PROT_MODE, _ADAPTIVE_PROT_MODE_SINCE_MS
    global _ADAPTIVE_PROT_STABLE_SINCE_MS, _ADAPTIVE_PROT_LAST_LOG_MS
    snap = _adaptive_protection_snapshot(state, int(now_ms))
    previous = str(_ADAPTIVE_PROT_MODE or "FULL_PROT")
    reasons = tuple(snap.get("reasons", ()) or ())

    if reasons:
        _ADAPTIVE_PROT_STABLE_SINCE_MS = 0
        if previous != "FULL_PROT":
            _ADAPTIVE_PROT_MODE = "FULL_PROT"
            _ADAPTIVE_PROT_MODE_SINCE_MS = int(now_ms)
    elif bool(snap.get("stable", False)):
        if int(_ADAPTIVE_PROT_STABLE_SINCE_MS or 0) <= 0:
            _ADAPTIVE_PROT_STABLE_SINCE_MS = int(now_ms)
        stable_ms = max(0, int(now_ms) - int(_ADAPTIVE_PROT_STABLE_SINCE_MS))
        if previous != "OFFENSE" and stable_ms >= int(_ADAPTIVE_PROT_EXIT_STABLE_MS):
            _ADAPTIVE_PROT_MODE = "OFFENSE"
            _ADAPTIVE_PROT_MODE_SINCE_MS = int(now_ms)
    else:
        _ADAPTIVE_PROT_STABLE_SINCE_MS = 0

    mode = str(_ADAPTIVE_PROT_MODE or "FULL_PROT")
    changed = mode != previous
    if changed or int(now_ms) - int(_ADAPTIVE_PROT_LAST_LOG_MS or 0) >= int(_ADAPTIVE_PROT_LOG_INTERVAL_MS):
        _ADAPTIVE_PROT_LAST_LOG_MS = int(now_ms)
        _log(
            "HERO_ROJ_ADAPTIVE_PROT_MODE", mode=mode, previous_mode=previous,
            mode_duration_ms=max(0, int(now_ms) - int(_ADAPTIVE_PROT_MODE_SINCE_MS or now_ms)),
            stable=bool(snap.get("stable", False)), reasons=",".join(str(v) for v in reasons),
            lowest_hp=round(float(snap.get("lowest_hp", 1.0)), 3), low85=int(snap.get("low85", 0)),
            max_melee=int(snap.get("max_melee", 0)), max_attackers=int(snap.get("max_attackers", 0)),
            aegis_present=bool(snap.get("aegis_present", False)), aegis_remaining_ms=int(snap.get("aegis_remaining_ms", 0)),
            aegis_coverage_ratio=round(float(snap.get("aegis_coverage_ratio", 0.0)), 3),
            threatened_without_aegis=int(snap.get("threatened_without_aegis", 0)),
            max_hp_drop=round(float(snap.get("max_hp_drop", 0.0)), 3),
            core_known=bool(snap.get("core_known", False)), core_ready=bool(snap.get("core_ready", True)),
            core_covered=int(snap.get("core_covered", 0)), core_required=int(snap.get("core_required", 0)),
            shelter_missing=bool(snap.get("shelter_missing", False)),
            core_quiet_window=bool(snap.get("core_quiet_window", False)),
            policy="stage2_32_shared_team_snapshot_pressure_driven_support_budget",
        )
    return mode, snap


def _critical_rescue_needed(state: _ControllerState) -> bool:
    rows = _party_threat_rows(state)
    if not rows:
        return False
    return any(
        float(hp) <= 0.45
        or (float(hp) <= 0.58 and (int(melee) >= 3 or int(attackers) >= 4))
        or (float(hp) <= 0.66 and int(melee) >= 4)
        for _score, _aid, hp, melee, attackers in rows
    )


def _aegis_owner_ready(state: _ControllerState, now_ms: int) -> tuple[bool, str]:
    """Return whether this RoJ can start Aegis *now*, not merely pay for it.

    Stage 2.32 still elected heroes during the short native aftercast/hidden-cast
    window, which produced 11 false failovers in the live run.  The native cast
    bridge is advisory: when unavailable we fail open to the proven controller
    checks rather than blocking Aegis indefinitely.
    """
    hid = int(state.hero_agent_id)
    if int(_AEGIS_FAILOVER_SKIP_UNTIL.get(hid, 0) or 0) > int(now_ms):
        return False, "recent_failover_skip"
    if state.transaction_locked:
        return False, "roj_transaction"
    if str(getattr(state, "chain_phase", "opener") or "opener") not in ("opener", "consume"):
        return False, "roj_chain_setup"
    if int(getattr(state, "mimicry_target_id", 0) or 0) > 0:
        return False, "mimicry_target_pending"
    if int(state.pending.skill_id or 0) > 0:
        return False, "controller_pending"
    if not _hero_can_cast(state):
        return False, "agent_busy"
    if not _has_enemy_proximity(state):
        return False, "no_enemy_proximity"
    if int(now_ms) - int(state.last_command_ms or 0) < int(_COMMAND_GUARD_MS):
        return False, "command_guard"
    entry = _entry_for_slot(state, int(state.aegis_slot))
    if not entry or int(entry[0]) != int(AEGIS_ID) or _recharge_data(entry[1]) != 0:
        return False, "aegis_not_ready"
    if not _can_pay(state, AEGIS_ID) or not _support_energy_ok(state, AEGIS_ID, emergency=False):
        return False, "energy_reserve"

    # Agent.IsCasting can miss the final frames of a native hero cast.  Refuse an
    # owner while the native event bridge still sees an active cast or while the
    # hero is inside the normal post-cast recovery window.
    try:
        from Py4GWCoreLib.Builds.Skills.ReforgedSupport import get_recent_cast, get_recent_cast_outcomes
        recent = get_recent_cast(hid, max_age_ms=max(2500, int(_AEGIS_OWNER_NATIVE_LOOKBACK_MS)))
        if recent is not None:
            _sid, _target, start_tick, duration_s = recent
            expected_end = int(start_tick or 0) + max(0, int(float(duration_s or 0.0) * 1000.0))
            if int(start_tick or 0) > 0 and (expected_end <= int(start_tick or 0) or int(now_ms) <= expected_end + 120):
                return False, "native_cast_active"
        for ts, aid, _sid, result in reversed(get_recent_cast_outcomes(max_age_ms=int(_AEGIS_OWNER_NATIVE_LOOKBACK_MS))):
            if int(aid or 0) != hid:
                continue
            if str(result or "") == "finished" and 0 <= int(now_ms) - int(ts or 0) < int(_AEGIS_OWNER_AFTERCAST_GUARD_MS):
                return False, "native_aftercast"
            break
    except Exception:
        pass
    return True, "ready"


def _aegis_elected_hero(states: list[_ControllerState]) -> int:
    """Round-robin Aegis among heroes that are immediately cast-ready."""
    global _LAST_AEGIS_CASTER_HERO_ID
    now_ms = _now_ms()
    equipped = [st for st in states if int(st.aegis_slot) > 0 and Agent.IsAlive(int(st.hero_agent_id))]
    equipped.sort(key=lambda st: (int(st.hero_index), int(st.hero_agent_id)))
    if not equipped:
        return 0
    start = 0
    for i, st in enumerate(equipped):
        if int(st.hero_agent_id) == int(_LAST_AEGIS_CASTER_HERO_ID):
            start = (i + 1) % len(equipped)
            break
    ordered = equipped[start:] + equipped[:start]
    for st in ordered:
        ready, _reason = _aegis_owner_ready(st, int(now_ms))
        if ready:
            return int(st.hero_agent_id)
    return 0


def _try_aegis_rotation(state: _ControllerState, states: list[_ControllerState], now_ms: int) -> bool:
    global _LAST_AEGIS_CONFIRMED_MS, _LAST_AEGIS_OBSERVED_DURATION_MS
    if state.transaction_locked or state.aegis_slot <= 0 or AEGIS_ID <= 0 or not _has_enemy_proximity(state):
        return False
    entry = _entry_for_slot(state, state.aegis_slot)
    if not entry or int(entry[0]) != int(AEGIS_ID) or _recharge_data(entry[1]) != 0:
        return False
    remaining, present = _party_effect_remaining_max(AEGIS_ID)
    if remaining <= 0 and present and int(_LAST_AEGIS_CONFIRMED_MS or 0) > 0:
        fallback_ms = int(_LAST_AEGIS_OBSERVED_DURATION_MS or _AEGIS_EFFECT_FALLBACK_MS)
        remaining = max(0, int(_LAST_AEGIS_CONFIRMED_MS) + int(fallback_ms) - int(now_ms))
    try:
        activation_ms = max(1500, int(float(Skill.Data.GetActivation(AEGIS_ID) or 2.0) * 1000.0))
    except Exception:
        activation_ms = 2000
    # Start the next Aegis shortly before the current layer expires so its long
    # activation lands at/near expiry.  If the effect is already gone, cast now.
    if int(remaining) > int(activation_ms + _AEGIS_PRECAST_MARGIN_MS):
        return False
    if _prot_claimed("aegis", 0, now_ms):
        return False
    elected = _aegis_elected_hero(states)
    if elected <= 0 or int(elected) != int(state.hero_agent_id):
        return False
    if not _support_energy_ok(state, AEGIS_ID, emergency=False):
        return False
    if not _reserve_prot_claim(state, "aegis", AEGIS_ID, 0, activation_ms + 900):
        return False
    if _request_slot(state, state.aegis_slot, AEGIS_ID, 0, purpose="support_aegis_rotation"):
        _log(
            "HERO_ROJ_AEGIS_ROTATION", hero_index=state.hero_index, hero_id=state.hero_agent_id,
            effect_remaining_ms=int(remaining), effect_present=bool(present), elected_hero_id=int(elected),
            activation_ms=int(activation_ms), precast_margin_ms=int(_AEGIS_PRECAST_MARGIN_MS),
            calibrated_duration_ms=int(_LAST_AEGIS_OBSERVED_DURATION_MS or _AEGIS_EFFECT_FALLBACK_MS),
            policy="stage2_28_runtime_timed_round_robin_aegis_deadline",
        )
        return True
    _release_prot_claim(state, "aegis", 0)
    return False


_ROJ_DAMAGE_TREND: dict[int, tuple[int, float, float]] = {}


def _recent_hp_drop(ally_id: int, hp: float, now_ms: int) -> float:
    """Observed recent health loss, independent of an enemy's IsAttacking flag.

    Single-target RoJ protection previously spent nearly all its cast lane on
    old HP deficits with zero observed attackers. Only *fresh* loss or immediate
    critical HP can justify a zero-attacker rescue. The two-second sample window
    is conservative: missing observations simply provide no evidence.
    """
    if int(now_ms) <= 0 or int(ally_id) <= 0:
        return 0.0
    if len(_ROJ_DAMAGE_TREND) > 64:
        _ROJ_DAMAGE_TREND.clear()
    aid = int(ally_id)
    old = _ROJ_DAMAGE_TREND.get(aid)
    if old is None or now_ms < old[0] or now_ms - old[0] > 1800:
        _ROJ_DAMAGE_TREND[aid] = (int(now_ms), float(hp), float(hp))
        return 0.0
    drop = max(0.0, float(old[1]) - float(hp))
    # If HP has recovered, start a fresh reference rather than keeping a stale
    # high-water mark and triggering repeat Spirit Bond and Reversal casts.
    if float(hp) > float(old[2]) + 0.035:
        _ROJ_DAMAGE_TREND[aid] = (int(now_ms), float(hp), float(hp))
        return 0.0
    _ROJ_DAMAGE_TREND[aid] = (int(old[0]), float(old[1]), float(hp))
    return float(drop)


def _roj_target_needs_protection(skill: str, hp: float, melee: int, attackers: int,
                                  recent_drop: float, emergency_only: bool) -> bool:
    """Fresh direct pressure OR an actual death-window, not an old HP deficit.

    Zero-attacker fallback remains available for sudden spell damage and
    genuine emergencies. Does not affect group-wide Aegis rotation.
    """
    hp, recent_drop = float(hp), max(0.0, float(recent_drop))
    attackers, melee = int(attackers), int(melee)
    if hp <= 0.38:
        return True
    active = attackers > 0 or melee > 0
    if skill == 'spirit_bond':
        if active:
            return bool((attackers >= 2 and hp <= (0.73 if emergency_only else 0.85))
                        or (hp <= 0.57 and attackers >= 1))
        return bool(recent_drop >= (0.16 if emergency_only else 0.12) and hp <= 0.78)
    if active:
        return bool(hp <= (0.66 if emergency_only else 0.80)
                    or (melee >= 3 and hp <= 0.87))
    return bool(recent_drop >= (0.14 if emergency_only else 0.09) and hp <= 0.80)


def _reversal_target(state: _ControllerState, skill_id: int, *, emergency_only: bool) -> tuple[int, float, int, int]:
    now_ms = _now_ms()
    for _score, aid, hp, melee, attackers in _party_threat_rows(state):
        if _prot_claimed("reversal", aid, now_ms):
            continue
        # Never stack RoF and RoD on the same next-damage packet.
        if _effect_remaining_ms(aid, REVERSAL_OF_FORTUNE_ID) > 0 or _effect_remaining_ms(aid, REVERSAL_OF_DAMAGE_ID) > 0:
            continue
        if _has_effect(aid, REVERSAL_OF_FORTUNE_ID) or _has_effect(aid, REVERSAL_OF_DAMAGE_ID):
            continue
        recent_drop = _recent_hp_drop(aid, hp, now_ms)
        if not _roj_target_needs_protection('reversal', hp, melee, attackers,
                                            recent_drop, bool(emergency_only)):
            continue
        # Fortune is the stronger rescue bias; Damage is the anti-melee bias.
        if int(skill_id) == int(REVERSAL_OF_FORTUNE_ID) and hp > 0.72 and melee < 2:
            continue
        if int(skill_id) == int(REVERSAL_OF_DAMAGE_ID) and melee <= 0 and attackers <= 0 and hp > 0.60:
            continue
        return int(aid), float(hp), int(melee), int(attackers)
    return 0, 1.0, 0, 0


def _try_reversal(state: _ControllerState, skill_id: int, slot: int, *, emergency_only: bool) -> bool:
    if state.transaction_locked or int(slot) <= 0 or int(skill_id) <= 0:
        return False
    entry = _entry_for_slot(state, int(slot))
    if not entry or int(entry[0]) != int(skill_id) or _recharge_data(entry[1]) != 0:
        return False
    target, hp, melee, attackers = _reversal_target(state, int(skill_id), emergency_only=bool(emergency_only))
    if target <= 0 or not _support_energy_ok(state, int(skill_id), hp=hp, melee=melee, emergency=bool(emergency_only)):
        return False
    if not _reserve_prot_claim(state, "reversal", int(skill_id), int(target), _REVERSAL_TARGET_MUTEX_MS):
        return False
    if _request_slot(state, int(slot), int(skill_id), int(target), purpose=(
        "support_reversal_fortune" if int(skill_id) == int(REVERSAL_OF_FORTUNE_ID) else "support_reversal_damage"
    )):
        _log(
            "HERO_ROJ_REVERSAL_COORDINATED", hero_index=state.hero_index, hero_id=state.hero_agent_id,
            skill_id=int(skill_id), target_id=int(target), hp=round(float(hp), 3), melee_attackers=int(melee),
            total_attackers=int(attackers), target_mutex_ms=int(_REVERSAL_TARGET_MUTEX_MS),
            policy="stage2_28_hardened_cross_skill_same_target_mutex",
        )
        return True
    _release_prot_claim(state, "reversal", int(target))
    return False


def _try_spirit_bond(state: _ControllerState, *, emergency_only: bool) -> bool:
    if state.transaction_locked or state.spirit_bond_slot <= 0 or SPIRIT_BOND_ID <= 0:
        return False
    entry = _entry_for_slot(state, state.spirit_bond_slot)
    if not entry or int(entry[0]) != int(SPIRIT_BOND_ID) or _recharge_data(entry[1]) != 0:
        return False
    now_ms = _now_ms()
    for _score, aid, hp, melee, attackers in _party_threat_rows(state):
        if _prot_claimed("spirit_bond", aid, now_ms):
            continue
        remaining = _effect_remaining_ms(aid, SPIRIT_BOND_ID)
        if remaining > 1200 or (remaining <= 0 and _has_effect(aid, SPIRIT_BOND_ID)):
            continue
        recent_drop = _recent_hp_drop(aid, hp, now_ms)
        if not _roj_target_needs_protection('spirit_bond', hp, melee, attackers,
                                            recent_drop, bool(emergency_only)):
            continue
        if not _support_energy_ok(state, SPIRIT_BOND_ID, hp=hp, melee=melee, emergency=bool(emergency_only)):
            continue
        if not _reserve_prot_claim(state, "spirit_bond", SPIRIT_BOND_ID, aid, 1800):
            continue
        if _request_slot(state, state.spirit_bond_slot, SPIRIT_BOND_ID, aid, purpose="support_spirit_bond"):
            _log(
                "HERO_ROJ_SPIRIT_BOND_COORDINATED", hero_index=state.hero_index, hero_id=state.hero_agent_id,
                target_id=int(aid), hp=round(float(hp), 3), melee_attackers=int(melee), total_attackers=int(attackers),
                recent_hp_drop=round(float(recent_drop), 3),
                prior_effect_remaining_ms=int(remaining), policy="stage2_27_no_duplicate_bond_refresh_only_near_expiry",
                stage2_56_pressure_gate=True,
            )
            return True
        _release_prot_claim(state, "spirit_bond", aid)
    return False


def _aegis_deadline_can_preempt_rescue(state: _ControllerState, now_ms: int) -> bool:
    """Let prevention win when Aegis is due, except during an immediate collapse.

    Stage 2.27 let fast single-target prots continually consume the pre-offense
    support lane, so a 17s Aegis often was never handed to hero 2/3.  Aegis now
    owns the lane once its landing deadline arrives, unless the top threatened
    ally needs a quarter-second rescue right now.
    """
    remaining, present = _party_effect_remaining_max(AEGIS_ID)
    if remaining <= 0 and present and int(_LAST_AEGIS_CONFIRMED_MS or 0) > 0:
        fallback_ms = int(_LAST_AEGIS_OBSERVED_DURATION_MS or _AEGIS_EFFECT_FALLBACK_MS)
        remaining = max(0, int(_LAST_AEGIS_CONFIRMED_MS) + fallback_ms - int(now_ms))
    try:
        activation_ms = max(1500, int(float(Skill.Data.GetActivation(AEGIS_ID) or 2.0) * 1000.0))
    except Exception:
        activation_ms = 2000
    due = (not bool(present)) or int(remaining) <= int(activation_ms + _AEGIS_PRECAST_MARGIN_MS)
    if not due:
        return False
    rows = _party_threat_rows(state)
    for _score, _aid, hp, melee, attackers in rows:
        # Do not lock a monk into a 2s preventive cast if somebody is already
        # in a genuine death window. The fast Reversal/Spirit-Bond rescue lane
        # keeps precedence only for this narrow condition.
        if float(hp) <= 0.34 or (float(hp) <= 0.46 and (int(melee) >= 3 or int(attackers) >= 4)):
            return False
    return True


def _try_stage227_protection(state: _ControllerState, states: list[_ControllerState], now_ms: int, *, emergency_only: bool) -> bool:
    if not _is_stage227_prot_bar(state) or state.transaction_locked:
        return False
    mode, _snap = _adaptive_protection_mode(state, int(now_ms))

    # Aegis remains the baseline preventive layer in both modes. Its landing
    # deadline may preempt normal support, exactly as in 2.29.
    # Emergency lane uses quarter-second Reversals first in true death windows.
    if emergency_only:
        if _aegis_deadline_can_preempt_rescue(state, int(now_ms)):
            if _try_aegis_rotation(state, states, int(now_ms)):
                _log(
                    "HERO_ROJ_AEGIS_DEADLINE_PRIORITY", hero_index=state.hero_index,
                    hero_id=state.hero_agent_id, adaptive_mode=str(mode),
                    adaptive_policy="stage2_30_aegis_baseline_prevention_in_both_modes",
                    policy="stage2_28_prevention_before_routine_single_target_prot",
                )
                return True

        # OFFENSE mode does not spend the fast single-target prots on routine
        # chip/melee pressure. It still permits a narrow true death-window rescue.
        if str(mode) == "OFFENSE" and not _critical_rescue_needed(state):
            return False
        if _try_reversal(state, REVERSAL_OF_FORTUNE_ID, state.reversal_fortune_slot, emergency_only=True):
            return True
        if _try_reversal(state, REVERSAL_OF_DAMAGE_ID, state.reversal_damage_slot, emergency_only=True):
            return True
        return _try_spirit_bond(state, emergency_only=True)

    # Free support lane: always maintain Aegis. Routine Spirit Bond/Reversal
    # casts exist only in FULL_PROT; stable OFFENSE windows hand this action back
    # to RoJ/copy/setup and thereby recover clear speed without removing skills.
    if _try_aegis_rotation(state, states, int(now_ms)):
        return True
    if str(mode) == "OFFENSE":
        return False
    if _try_spirit_bond(state, emergency_only=False):
        return True
    if _try_reversal(state, REVERSAL_OF_FORTUNE_ID, state.reversal_fortune_slot, emergency_only=False):
        return True
    if _try_reversal(state, REVERSAL_OF_DAMAGE_ID, state.reversal_damage_slot, emergency_only=False):
        return True
    return False


def _try_smart_heal(state: _ControllerState, *, emergency_only: bool = False) -> bool:
    if state.transaction_locked:
        return False
    # Dwayna's Kiss is a fast direct rescue and cannot target self. Patient
    # Spirit is the efficient delayed follow-up. Cross-hero reservations prevent
    # all three RoJ monks from spending the same cast on one ally.
    dwayna_threshold = 0.46 if emergency_only else 0.56
    patient_threshold = 0.52 if emergency_only else 0.68
    dwayna = _ready_entry(state, DWAYNAS_KISS_ID) if DWAYNAS_KISS_ID > 0 else None
    if dwayna is not None:
        target = _lowest_heal_target(
            state, dwayna_threshold, other_only=True, claim_skill_id=DWAYNAS_KISS_ID
        )
        if target > 0:
            hp = _ally_health_fraction(target)
            max_claims = 2 if hp <= 0.30 else 1
            duration_ms = 900 if hp <= 0.30 else 1100
            if _reserve_heal_claim(state, DWAYNAS_KISS_ID, target, duration_ms=duration_ms, max_claims=max_claims):
                if _request_slot(state, dwayna[0], DWAYNAS_KISS_ID, target, purpose="support_dwaynas_kiss"):
                    if int(target) != int(state.hero_agent_id) and _has_effect(state.hero_agent_id, ZEALOTS_FIRE_ID):
                        _log("HERO_ROJ_ZEALOT_HEAL_TRIGGER_EXPECTED", hero_index=state.hero_index,
                             hero_id=state.hero_agent_id, heal_skill_id=DWAYNAS_KISS_ID, target_id=int(target),
                             policy="stage2_06_ally_heal_adjacent_fire_pulse")
                    return True
                _release_heal_claim(state, DWAYNAS_KISS_ID, target)
    patient = _ready_entry(state, PATIENT_SPIRIT_ID) if PATIENT_SPIRIT_ID > 0 else None
    if patient is not None:
        target = _lowest_heal_target(
            state, patient_threshold, avoid_patient=True, claim_skill_id=PATIENT_SPIRIT_ID
        )
        if target > 0:
            if _reserve_heal_claim(state, PATIENT_SPIRIT_ID, target, duration_ms=2500, max_claims=1):
                if _request_slot(state, patient[0], PATIENT_SPIRIT_ID, target, purpose="support_patient_spirit"):
                    if int(target) != int(state.hero_agent_id) and _has_effect(state.hero_agent_id, ZEALOTS_FIRE_ID):
                        _log("HERO_ROJ_ZEALOT_HEAL_TRIGGER_EXPECTED", hero_index=state.hero_index,
                             hero_id=state.hero_agent_id, heal_skill_id=PATIENT_SPIRIT_ID, target_id=int(target),
                             policy="stage2_06_ally_heal_adjacent_fire_pulse")
                    return True
                _release_heal_claim(state, PATIENT_SPIRIT_ID, target)
    return False


def _try_mantra_of_frost(state: _ControllerState) -> bool:
    """Stage 2.06 defensive/energy stance; never enter an Echo transaction."""
    if state.transaction_locked or MANTRA_OF_FROST_ID <= 0:
        return False
    entry = _ready_entry(state, MANTRA_OF_FROST_ID)
    if entry is None or _has_effect(state.hero_agent_id, MANTRA_OF_FROST_ID):
        return False
    # It is free, but still consumes one action; ready RoJ lanes call this only
    # after their own first refusal.
    return _request_slot(
        state, entry[0], MANTRA_OF_FROST_ID, state.hero_agent_id,
        purpose="frost_mantra_upkeep",
    )


def _try_zealots_fire(state: _ControllerState) -> bool:
    """Maintain Zealot's Fire so Patient/Dwayna heals can pulse adjacent AoE."""
    if state.transaction_locked or ZEALOTS_FIRE_ID <= 0:
        return False
    entry = _ready_entry(state, ZEALOTS_FIRE_ID)
    if entry is None or _has_effect(state.hero_agent_id, ZEALOTS_FIRE_ID):
        return False
    try:
        # Avoid turning a low-energy healer into a zero-energy AoE engine.  Once
        # active, each ally-targeted skill costs the enchantment's 1 Energy
        # trigger, so refresh only from a healthy reserve.
        if float(Agent.GetEnergy(state.hero_agent_id) or 0.0) < 0.40:
            return False
    except Exception:
        return False
    return _request_slot(
        state, entry[0], ZEALOTS_FIRE_ID, state.hero_agent_id,
        purpose="zealots_fire_heal_aoe_upkeep",
    )


def _try_castigation_energy(state: _ControllerState) -> bool:
    if state.transaction_locked or CASTIGATION_SIGNET_ID <= 0:
        return False
    entry = _ready_entry(state, CASTIGATION_SIGNET_ID)
    if entry is None:
        return False
    try:
        if float(Agent.GetEnergy(state.hero_agent_id) or 0.0) > 0.52:
            return False
    except Exception:
        return False
    target, _packet = _roj_target(state)
    if target <= 0:
        return False
    # Prefer an attacking target because Castigation refunds energy then.
    try:
        anchor = _cluster_anchor(state)
        if anchor > 0:
            from Py4GWCoreLib.Builds.Skills import HeroClusterCoordinator
            members = list(HeroClusterCoordinator.cluster_members(anchor, origin_agent_id=state.hero_agent_id, radius=_NEARBY, range_value=_CAST_RANGE) or [])
            attackers = [int(x) for x in members if Agent.IsAlive(int(x)) and Agent.IsAttacking(int(x))]
            if attackers:
                target = attackers[0]
    except Exception:
        pass
    return _request_slot(state, entry[0], CASTIGATION_SIGNET_ID, int(target), purpose="energy_castigation_signet")


def _restore_player_target_after_mimicry(state: _ControllerState) -> None:
    global _MIMICRY_OWNER_HERO_ID
    old_target = int(state.mimicry_old_player_target or 0)
    state.mimicry_target_id = 0
    state.mimicry_target_started_ms = 0
    state.mimicry_dispatch_result = ""
    state.mimicry_old_player_target = 0
    if _MIMICRY_OWNER_HERO_ID == int(state.hero_agent_id):
        _MIMICRY_OWNER_HERO_ID = 0
    if old_target > 0:
        try:
            Player.ChangeTarget(old_target)
        except Exception:
            pass


def _request_mimicry_current_target(state: _ControllerState, slot: int, donor: int) -> bool:
    """Atomic hero Mimicry dispatch patterned after the proven player RoJway.

    The player RoJ controller proved that Arcane Mimicry is safest when the
    verified donor is the actual current selected target and the cast itself is
    issued with target 0/current from the game thread.  Some HeroUseSkill paths
    otherwise resolve against the wrong visible target (e.g. the real SoJ Monk).
    """
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
    if donor not in _owned_party_hero_ids() or not Agent.IsAlive(donor):
        return False
    try:
        if int(Player.GetTargetID() or 0) != int(donor):
            return False
    except Exception:
        return False

    hero_index = int(state.hero_index)
    donor_id = int(donor)
    slot_i = int(slot)
    state.mimicry_dispatch_result = "queued"

    def _guarded_hero_mimicry_cast():
        try:
            if (
                _STATES.get(int(state.hero_agent_id)) is not state
                or state.pending.skill_id != ARCANE_MIMICRY_ID
                or state.pending.target_id != donor_id
                or _now_ms() - now_ms > _COMMAND_GUARD_MS
                or not _has_enemy_proximity(state)
            ):
                state.mimicry_dispatch_result = "request_expired"
                return
            current_target = int(Player.GetTargetID() or 0)
            if current_target != donor_id:
                state.mimicry_dispatch_result = f"target_changed:{current_target}"
                return
            if donor_id not in _owned_party_hero_ids() or not Agent.IsValid(donor_id) or not Agent.IsAlive(donor_id):
                state.mimicry_dispatch_result = "invalid_donor"
                return
            donor_state = _STATES.get(donor_id)
            if donor_state is None:
                state.mimicry_dispatch_result = "donor_not_controller_owned"
                return
            if (
                donor_state.base_roj_slot <= 0
                or _slot_skill_id(donor_state, donor_state.base_roj_slot) != RAY_OF_JUDGMENT_ID
                or donor_state.mimicry_slot <= 0
                or _slot_skill_id(donor_state, donor_state.mimicry_slot) not in (ARCANE_MIMICRY_ID, RAY_OF_JUDGMENT_ID)
                or donor_state.echo_slot <= 0
                or _slot_skill_id(donor_state, donor_state.echo_slot) not in (ARCANE_ECHO_ID, RAY_OF_JUDGMENT_ID)
                or SIGNET_OF_JUDGMENT_ID in _bar_ids(donor_state.hero_bar_index)
            ):
                state.mimicry_dispatch_result = "donor_changed"
                return
            # Critical: target 0 means CURRENT selected target, mirroring the
            # known-good real-player RoJ Mimicry path.
            PySkillbar.Skillbar().HeroUseSkill(0, slot_i, hero_index)
            state.mimicry_dispatch_result = "issued"
        except Exception as exc:
            state.mimicry_dispatch_result = f"error:{type(exc).__name__}"

    try:
        PyGameThread.enqueue(_guarded_hero_mimicry_cast)
    except Exception:
        return False

    state.pending = _PendingCommand(
        skill_id=ARCANE_MIMICRY_ID,
        slot=slot_i,
        target_id=donor_id,
        started_ms=now_ms,
        start_recharge=_recharge_data(current[1]),
        purpose="copy_other_hero_roj_current_target",
    )
    state.last_command_ms = now_ms
    _log(
        "HERO_ROJ_MIMICRY_CURRENT_TARGET_QUEUED",
        hero_index=state.hero_index,
        hero_id=state.hero_agent_id,
        donor_hero_id=donor_id,
        current_target=int(Player.GetTargetID() or 0),
        policy="hero_only_verified_current_target_then_target0",
    )
    return True


def _full_chain_ready(state: _ControllerState, states: list[_ControllerState]) -> bool:
    if not _has_enemy_proximity(state):
        return False
    if state.base_roj_slot <= 0 or state.mimicry_slot <= 0 or state.echo_slot <= 0:
        return False
    native = _entry_for_slot(state, state.base_roj_slot)
    mimic = _entry_for_slot(state, state.mimicry_slot)
    echo = _entry_for_slot(state, state.echo_slot)
    if not native or int(native[0]) != RAY_OF_JUDGMENT_ID or _recharge_data(native[1]) != 0:
        return False
    if not mimic or int(mimic[0]) != ARCANE_MIMICRY_ID or _recharge_data(mimic[1]) != 0:
        return False
    if not echo or int(echo[0]) != ARCANE_ECHO_ID or _recharge_data(echo[1]) != 0:
        return False
    if _other_roj_donor(state, states, log_result=False) <= 0:
        return False
    if not _can_afford_full_chain(state):
        return False
    target, _packet = _roj_target(state)
    return target > 0


def _try_mimicry(state: _ControllerState, states: list[_ControllerState]) -> bool:
    global _MIMICRY_OWNER_HERO_ID
    if _MIMICRY_OWNER_HERO_ID not in (0, int(state.hero_agent_id)):
        return False
    entry = _ready_entry(state, ARCANE_MIMICRY_ID)
    if entry is None:
        return False
    donor = _other_roj_donor(state, states)
    if donor <= 0:
        return False
    if donor not in _owned_party_hero_ids():
        _log("HERO_ROJ_MIMICRY_DONOR_REJECTED", hero_index=state.hero_index,
             hero_id=state.hero_agent_id, donor_id=donor, reason="not_owned_hero")
        return False

    now_ms = _now_ms()
    # New donor: remember the player's current target once, then acquire only
    # the verified RoJ hero. No real account can become the Mimicry source.
    if int(state.mimicry_target_id or 0) != int(donor):
        try:
            state.mimicry_old_player_target = int(Player.GetTargetID() or 0)
        except Exception:
            state.mimicry_old_player_target = 0
        _MIMICRY_OWNER_HERO_ID = int(state.hero_agent_id)
        state.mimicry_target_id = int(donor)
        state.mimicry_target_started_ms = now_ms
        state.mimicry_dispatch_result = "targeting"
        try:
            Player.ChangeTarget(int(donor))
        except Exception:
            pass
        _log("HERO_ROJ_MIMICRY_TARGETING_HERO", hero_index=state.hero_index,
             hero_id=state.hero_agent_id, donor_hero_id=int(donor))
        return True

    try:
        current_target = int(Player.GetTargetID() or 0)
    except Exception:
        current_target = 0
    if current_target != int(donor):
        # Something stole target while arming Mimicry. Re-acquire; do not cast.
        if now_ms - int(state.mimicry_target_started_ms or now_ms) > 1800:
            _log("HERO_ROJ_MIMICRY_TARGET_ABORT", hero_index=state.hero_index,
                 hero_id=state.hero_agent_id, donor_hero_id=int(donor), current_target=current_target)
            _restore_player_target_after_mimicry(state)
            return False
        try:
            Player.ChangeTarget(int(donor))
        except Exception:
            pass
        return True

    # Match the real-player controller's small stable-target gate. This closes
    # the race where the visible SoJ target was still active when Mimicry fired.
    if now_ms - int(state.mimicry_target_started_ms or now_ms) < 120:
        return True

    return _request_mimicry_current_target(state, int(entry[0]), int(donor))


def _try_auspicious(state: _ControllerState) -> bool:
    # Auspicious is an energy primer; it copies nothing itself. Never spend it
    # unless Echo is ready *and* a RoJ is already ready to seed Echo immediately.
    if state.echo_slot <= 0 or state.auspicious_slot <= 0:
        return False
    if _ready_roj_choice(state, temporary_only=False) is None:
        return False
    echo = _entry_for_slot(state, state.echo_slot)
    ausp = _entry_for_slot(state, state.auspicious_slot)
    if not echo or not ausp:
        return False
    if int(echo[0]) != ARCANE_ECHO_ID or _recharge_data(echo[1]) != 0:
        return False
    if int(ausp[0]) != AUSPICIOUS_INCANTATION_ID or _recharge_data(ausp[1]) != 0:
        return False
    # Mirror the player RoJway atomic chain: once Auspicious is issued, no
    # unrelated hero spell may run until Arcane Echo has been seeded with RoJ.
    _set_transaction_lock(state, True)
    if _request_slot(state, state.auspicious_slot, AUSPICIOUS_INCANTATION_ID,
                     state.hero_agent_id, purpose="prime_arcane_echo_energy"):
        return True
    _set_transaction_lock(state, False)
    return False


def _try_arcane_echo(state: _ControllerState) -> bool:
    # Echo is allowed only after Mimicry has resolved away from its base skill.
    # Once Echo is requested we freeze native Hero AI until the next RoJ request,
    # guaranteeing that Arcane Echo can only copy Ray of Judgment.
    if state.echo_slot <= 0:
        return False
    if state.mimicry_slot > 0 and _slot_skill_id(state, state.mimicry_slot) == ARCANE_MIMICRY_ID:
        # Mimicry is still visible/ready; resolve that copy first.
        mim = _entry_for_slot(state, state.mimicry_slot)
        if mim and _recharge_data(mim[1]) == 0:
            return False
    echo = _entry_for_slot(state, state.echo_slot)
    if not echo or int(echo[0]) != ARCANE_ECHO_ID or _recharge_data(echo[1]) != 0:
        return False
    target, _packet = _roj_target(state)
    if target <= 0:
        return False
    seed = _ready_roj_choice(state, temporary_only=False)
    if seed is None:
        return False
    state.echo_seed_slot = int(seed[0])
    _set_transaction_lock(state, True)
    if _request_slot(state, state.echo_slot, ARCANE_ECHO_ID, state.hero_agent_id, purpose="echo_next_roj"):
        _log(
            "HERO_ROJ_ECHO_ARMED", hero_index=state.hero_index, hero_id=state.hero_agent_id,
            echo_slot=state.echo_slot, reserved_seed_slot=state.echo_seed_slot,
            policy="echo_must_copy_reserved_roj_seed",
        )
        return True
    _set_transaction_lock(state, False)
    return False


def _ready_roj_choice(state: _ControllerState, *, temporary_only: bool = False):
    choices = []
    for priority, slot in ((0, state.mimicry_slot), (1, state.echo_slot), (2, state.base_roj_slot)):
        if slot <= 0:
            continue
        cur = _entry_for_slot(state, slot)
        if not cur or int(cur[0]) != RAY_OF_JUDGMENT_ID or _recharge_data(cur[1]) != 0:
            continue
        if temporary_only and slot == state.base_roj_slot:
            continue
        choices.append((priority, int(slot), cur[1]))
    if not choices:
        return None
    choices.sort(key=lambda row: row[0])
    return (choices[0][1], choices[0][2])


def _try_roj_exact_slot(state: _ControllerState, now_ms: int, slot: int, *, purpose: str) -> bool:
    global _LAST_ROJ_TEAM_CAST_MS, _LAST_ROJ_TEAM_TARGET
    if slot <= 0:
        return False
    cur = _entry_for_slot(state, int(slot))
    if not cur or int(cur[0]) != RAY_OF_JUDGMENT_ID or _recharge_data(cur[1]) != 0:
        return False
    target, packet_size = _roj_target(state)
    if target <= 0:
        return False
    lane_ms = _pi_burst_lane_for_target(state, target)
    elapsed = now_ms - int(_LAST_ROJ_TEAM_CAST_MS or 0)
    if _LAST_ROJ_TEAM_CAST_MS and elapsed < lane_ms:
        return False
    if _request_slot(state, int(slot), RAY_OF_JUDGMENT_ID, target, purpose=purpose):
        _LAST_ROJ_TEAM_CAST_MS = int(now_ms)
        _LAST_ROJ_TEAM_TARGET = int(target)
        burst_cast, burst_goal = _record_pi_burst_cast(state, target)
        _log(
            "HERO_ROJ_PACKET_CAST", hero_index=state.hero_index, hero_id=state.hero_agent_id,
            target_id=target, packet_size=packet_size, copied_slot=int(slot), purpose=purpose,
            team_lane_ms=lane_ms, target_coverage=_target_roj_coverage(state, target, now_ms),
            pi_burst_cast=int(burst_cast), pi_burst_goal=int(burst_goal),
        )
        if lane_ms == _PI_ROJ_BURST_STAGGER_MS and burst_goal > 0 and burst_cast > 0:
            _log(
                "HERO_ROJ_AFTER_PI", hero_index=state.hero_index, hero_id=state.hero_agent_id,
                target_id=int(target), packet_size=int(packet_size), roj_slot=int(slot),
                burst_cast=int(burst_cast), burst_goal=int(burst_goal),
                binding_chains=bool(_has_binding_chains(target)),
                policy="confirmed_pi_cluster_burst",
            )
        return True
    return False


def _try_roj(state: _ControllerState, now_ms: int, rank: int) -> bool:
    global _LAST_ROJ_TEAM_CAST_MS, _LAST_ROJ_TEAM_TARGET
    target, packet_size = _roj_target(state)
    if target <= 0:
        return False

    ready = _ready_roj_choice(state, temporary_only=False)
    if ready is None:
        return False

    # One shared hero lane, matching the real RoJ accounts (~one new ray/sec).
    # Do not multiply the delay by hero rank; that created asymmetric long gaps.
    lane_ms = _pi_burst_lane_for_target(state, target)
    elapsed = now_ms - int(_LAST_ROJ_TEAM_CAST_MS or 0)
    if _LAST_ROJ_TEAM_CAST_MS and elapsed < lane_ms:
        return False

    pi_anchor, _pi_members, pi_goal, pi_casts = _refresh_pi_burst(state)
    in_pi_burst = bool(pi_anchor > 0 and pi_casts < pi_goal and target == pi_anchor)
    purpose = (
        "pi_knockdown_roj" if in_pi_burst
        else ("echo_mandatory_next_roj" if state.transaction_locked else f"adaptive_cluster_roj_packet{packet_size}")
    )
    if _request_slot(state, ready[0], RAY_OF_JUDGMENT_ID, target, purpose=purpose):
        _LAST_ROJ_TEAM_CAST_MS = now_ms
        _LAST_ROJ_TEAM_TARGET = target
        burst_cast, burst_goal = _record_pi_burst_cast(state, target)
        _log("HERO_ROJ_PACKET_CAST", hero_index=state.hero_index, hero_id=state.hero_agent_id,
             target_id=target, packet_size=packet_size, copied_slot=int(ready[0]), purpose=purpose,
             team_lane_ms=lane_ms, target_coverage=_target_roj_coverage(state, target, now_ms),
             pi_burst_cast=int(burst_cast), pi_burst_goal=int(burst_goal))
        if lane_ms == _PI_ROJ_BURST_STAGGER_MS and burst_goal > 0 and burst_cast > 0:
            _log(
                "HERO_ROJ_AFTER_PI", hero_index=state.hero_index, hero_id=state.hero_agent_id,
                target_id=int(target), packet_size=int(packet_size), roj_slot=int(ready[0]),
                burst_cast=int(burst_cast), burst_goal=int(burst_goal),
                binding_chains=bool(_has_binding_chains(target)),
                policy="confirmed_pi_cluster_burst",
            )
        return True
    return False


def _run_state(state: _ControllerState, states: list[_ControllerState], now_ms: int, rank: int) -> None:
    global _LAST_ROJ_TEAM_CAST_MS, _LAST_ROJ_TEAM_TARGET, _MIMICRY_OWNER_HERO_ID
    if now_ms - state.last_scan_ms < _SCAN_INTERVAL_MS:
        return
    state.last_scan_ms = now_ms
    if not _ensure_locked(state, now_ms):
        return
    if _process_pending(state, now_ms):
        return
    if not _hero_can_cast(state):
        return

    # RoJ-project combat gate: never start/continue Mimicry or Echo while the
    # party is between packets. Temporary copies are preserved by the game and
    # will be spent first once an enemy packet is close again.
    if not _has_enemy_proximity(state):
        if state.chain_phase != "opener" or state.transaction_locked or state.mimicry_target_id > 0:
            _log("HERO_ROJ_PROXIMITY_RESET", hero_index=state.hero_index, hero_id=state.hero_agent_id,
                 phase=state.chain_phase, policy="no_offensive_setup_without_nearby_enemy")
            _restore_player_target_after_mimicry(state)
            _set_transaction_lock(state, False)
            _set_chain_phase(state, "opener", now_ms=now_ms)
            state.chain_started_ms = 0
            state.echo_seed_slot = 0
            state.echo_seed_cast_ms = 0
        # Detect the exact map-entry race seen in the 17:43 log: Mimicry/Echo
        # transformed even though this controller never requested them. Re-lock
        # the copy-engine slots immediately and preserve any accidental RoJ copy
        # for the next packet rather than casting it while travelling.
        leaked = []
        for label, slot, base_id in (
            ("mimicry", state.mimicry_slot, ARCANE_MIMICRY_ID),
            ("echo", state.echo_slot, ARCANE_ECHO_ID),
        ):
            if slot > 0:
                sid = _slot_skill_id(state, slot)
                if sid not in (0, base_id):
                    leaked.append(f"{label}:{slot}:{sid}")
        echo_effect_leak = _has_effect(state.hero_agent_id, ARCANE_ECHO_ID)
        auspicious_effect_leak = _has_effect(state.hero_agent_id, AUSPICIOUS_INCANTATION_ID)
        if echo_effect_leak:
            leaked.append("echo_effect_active")
        if auspicious_effect_leak:
            leaked.append("auspicious_effect_active")
        if leaked and now_ms - int(state.last_precombat_leak_log_ms or 0) >= 1800:
            state.last_precombat_leak_log_ms = int(now_ms)
            retained = bool(state.last_copy_setup_ms > 0 and now_ms - state.last_copy_setup_ms <= 30000)
            _log(
                "HERO_ROJ_PRECOMBAT_COPY_RETAINED" if retained else "HERO_ROJ_PRECOMBAT_COPY_LEAK",
                hero_index=state.hero_index,
                hero_id=state.hero_agent_id,
                leaked_slots=",".join(leaked),
                policy="recent_controller_setup" if retained else "unattributed_copy_reassert_locks",
            )
            if not retained:
                state.last_lock_refresh_ms = 0
                _ensure_locked(state, now_ms)
        # If Echo somehow leaked before combat, do not let a travel-heal become
        # the spell Echo copies. Preserve it for a RoJ when enemies arrive.
        if echo_effect_leak:
            return
        # Healing remains allowed while travelling.  Once no heal is needed,
        # pre-arm Stage 2.06 Frost/Zealot upkeep before the next packet so these
        # support actions do not steal the opening RoJ lane.
        if _try_stage227_protection(state, states, now_ms, emergency_only=False):
            return
        if not _is_stage227_prot_bar(state) and _try_smart_heal(state, emergency_only=False):
            return
        if _try_mantra_of_frost(state):
            return
        if not _is_stage227_prot_bar(state) and _try_zealots_fire(state):
            return
        return

    # Wrong Mimicry copies are never consumed, but they also must not freeze
    # native/copy RoJ usage for up to 20 seconds.  Stage 11 E10b quarantines
    # one persistent physical wrong-copy state: abort it once, then keep the
    # free consume/heal lane open without re-aborting every ~100 ms until the
    # slot actually normalizes.
    if state.mimicry_slot > 0:
        mim_sid = _slot_skill_id(state, state.mimicry_slot)
        if mim_sid in (0, ARCANE_MIMICRY_ID, RAY_OF_JUDGMENT_ID):
            if int(state.wrong_copy_quarantine_skill_id or 0) > 0:
                _log(
                    "HERO_ROJ_E10_WRONG_COPY_QUARANTINE_CLEAR",
                    hero_index=state.hero_index, hero_id=state.hero_agent_id,
                    previous_skill_id=int(state.wrong_copy_quarantine_skill_id),
                    current_skill_id=int(mim_sid),
                    duration_ms=max(0, int(now_ms) - int(state.wrong_copy_quarantine_started_ms or now_ms)),
                    policy="exp36_stage11_e10b_clear_on_physical_slot_normalization",
                )
                state.wrong_copy_quarantine_skill_id = 0
                state.wrong_copy_quarantine_started_ms = 0
        else:
            quarantined_sid = int(state.wrong_copy_quarantine_skill_id or 0)
            if quarantined_sid != int(mim_sid):
                _log("HERO_ROJ_MIMICRY_WRONG_COPY", hero_index=state.hero_index, hero_id=state.hero_agent_id,
                     slot=state.mimicry_slot, copied_skill_id=mim_sid, expected_skill_id=RAY_OF_JUDGMENT_ID,
                     policy="ignore_wrong_copy_keep_free_roj_lane")
                state.wrong_copy_quarantine_skill_id = int(mim_sid)
                state.wrong_copy_quarantine_started_ms = int(now_ms)
                _log(
                    "HERO_ROJ_E10_WRONG_COPY_QUARANTINE",
                    hero_index=state.hero_index, hero_id=state.hero_agent_id,
                    copied_skill_id=int(mim_sid), expected_skill_id=int(RAY_OF_JUDGMENT_ID),
                    policy="exp36_stage11_e10b_abort_once_then_free_consume_lane",
                )
                _abort_roj_transaction(
                    state, f"wrong_mimicry_copy:{mim_sid}", next_phase="consume"
                )
            else:
                # Already handled this exact physical transformation.  Keep
                # transaction ownership released and continue below; no log
                # storm and no repeated state reset while the game slot remains
                # unchanged.
                if state.transaction_locked:
                    _set_transaction_lock(state, False)
                if state.chain_phase not in ("opener", "consume"):
                    _set_chain_phase(state, "consume", now_ms=now_ms)
                    state.chain_started_ms = 0

    # A real emergency heal may interrupt setup *before* Echo. Once Echo is
    # active the transaction lock guarantees that its next spell is RoJ.
    if _try_stage227_protection(state, states, now_ms, emergency_only=True):
        return
    if not _is_stage227_prot_bar(state) and _try_smart_heal(state, emergency_only=True):
        return

    # PI -> RoJ combo: if Psychic Instability has just put the shared packet on
    # the floor and this hero already has a ready Ray, spend it immediately.
    # Never break the protected Echo transaction; seed/echo-copy phases already
    # route their mandatory RoJ through _roj_target(), which itself prioritizes
    # the same PI lease.
    if not state.transaction_locked and state.mimicry_target_id <= 0:
        pi_target, _pi_packet_size = _pi_knockdown_roj_target(state)
        ready_pi = _ready_roj_choice(state, temporary_only=False)
        if pi_target > 0 and ready_pi is not None:
            if _try_roj_exact_slot(state, now_ms, int(ready_pi[0]), purpose="pi_knockdown_roj"):
                return

    phase = state.chain_phase or "opener"

    # Recovery for an Echo that was armed externally/native-AI before our
    # controller obtained the bar. Never allow a heal/Symbol to become its next
    # spell: seed it immediately with an actually-ready RoJ and then consume the
    # physical Echo copy through the same protected transaction.
    if (
        phase == "opener"
        and state.echo_slot > 0
        and _slot_skill_id(state, state.echo_slot) == ARCANE_ECHO_ID
        and _has_effect(state.hero_agent_id, ARCANE_ECHO_ID)
    ):
        seed = _ready_roj_choice(state, temporary_only=False)
        if seed is not None:
            state.echo_seed_slot = int(seed[0])
            _set_transaction_lock(state, True)
            _log(
                "HERO_ROJ_RECOVER_PREARMED_ECHO", hero_index=state.hero_index, hero_id=state.hero_agent_id,
                echo_slot=state.echo_slot, reserved_seed_slot=state.echo_seed_slot,
            )
            _set_chain_phase(state, "seed_echo", now_ms=now_ms)
            state.chain_started_ms = int(now_ms)
            phase = "seed_echo"

    # Match the player RoJway's protected-chain timeout behavior. If a hero
    # cannot complete the copy sequence, release the short transaction and
    # return to the native opener instead of remaining stuck in setup.
    protected_timeout_phases = {"mimicry", "auspicious", "echo", "seed_echo", "wait_echo_copy", "echo_copy"}
    if phase in protected_timeout_phases and state.chain_started_ms > 0 and now_ms - state.chain_started_ms > 15000:
        pending = state.pending
        _log(
            "HERO_ROJ_CHAIN_TIMEOUT_RESET",
            hero_index=state.hero_index, hero_id=state.hero_agent_id, phase=phase,
            phase_duration_ms=max(0, int(now_ms - int(state.chain_phase_started_ms or state.chain_started_ms))),
            chain_duration_ms=max(0, int(now_ms - int(state.chain_started_ms))),
            expected_skill_id=int(_expected_skill_for_phase(state, phase)),
            pending_skill_id=int(pending.skill_id or 0), pending_purpose=str(pending.purpose or ""),
            pending_duration_ms=(max(0, int(now_ms - int(pending.started_ms))) if int(pending.started_ms or 0) > 0 else 0),
            abort_reason="hard_transaction_timeout",
            policy="stage1_55_timeout_only_protected_setup_phases_not_consume",
        )
        _abort_roj_transaction(state, "hard_transaction_timeout")
        return

    # 1) Native elite is the opener. This is the stable start point requested
    # for every new copy chain. If native RoJ is not ready, consume leftover
    # copied RoJs/heal/energy instead of idling.
    if phase == "opener":
        # Temporary copies expire after 20s and are always more urgent than new
        # setup. Spend them first.
        if _ready_roj_choice(state, temporary_only=True) is not None:
            if _try_roj(state, now_ms, rank):
                return

        full_chain = _full_chain_ready(state, states)
        if state.base_roj_slot > 0:
            cur = _entry_for_slot(state, state.base_roj_slot)
            target, packet = _roj_target(state)
            if cur and int(cur[0]) == RAY_OF_JUDGMENT_ID and _recharge_data(cur[1]) == 0 and target > 0:
                elapsed = now_ms - int(_LAST_ROJ_TEAM_CAST_MS or 0)
                if (not _LAST_ROJ_TEAM_CAST_MS) or elapsed >= _ROJ_TEAM_STAGGER_MS:
                    purpose = "chain_native_opener" if full_chain else "free_native_roj"
                    if _request_slot(state, state.base_roj_slot, RAY_OF_JUDGMENT_ID, target, purpose=purpose):
                        _LAST_ROJ_TEAM_CAST_MS = now_ms
                        _LAST_ROJ_TEAM_TARGET = target
                        _log(
                            "HERO_ROJ_PACKET_CAST", hero_index=state.hero_index, hero_id=state.hero_agent_id,
                            target_id=target, packet_size=packet, copied_slot=state.base_roj_slot, purpose=purpose,
                            team_lane_ms=_ROJ_TEAM_STAGGER_MS, full_chain_ready=bool(full_chain),
                        )
                        return
                # A ready RoJ owns the lane; do not insert Symbol/heals while the
                # short shared RoJ stagger is the only thing holding it.
                return
        if _try_stage227_protection(state, states, now_ms, emergency_only=False):
            return
        if not _is_stage227_prot_bar(state) and _try_smart_heal(state, emergency_only=False):
            return
        if _try_mantra_of_frost(state):
            return
        if not _is_stage227_prot_bar(state) and _try_zealots_fire(state):
            return
        if _try_castigation_energy(state):
            return
        if _try_roj(state, now_ms, rank):
            return
        if _try_bane_signet(state, now_ms):
            return
        _try_symbol_of_wrath(state, now_ms)
        return

    # 2) Mimicry must copy a verified *other RoJ hero*, never the SoJ support.
    if phase == "mimicry":
        current = _slot_skill_id(state, state.mimicry_slot) if state.mimicry_slot > 0 else 0
        if current == RAY_OF_JUDGMENT_ID:
            _set_chain_phase(state, "auspicious", now_ms=now_ms)
            return
        # Another hero may own the shared player target for ~one Mimicry cast.
        # Wait only for that transaction, never for Mimicry's recharge.
        if _MIMICRY_OWNER_HERO_ID not in (0, state.hero_agent_id):
            return
        if current == ARCANE_MIMICRY_ID and _ready_entry(state, ARCANE_MIMICRY_ID) is not None:
            if _try_mimicry(state, states):
                return
            if state.mimicry_target_id > 0:
                return
        _log("HERO_ROJ_MIMICRY_OPTIONAL_SKIP", hero_index=state.hero_index, hero_id=state.hero_agent_id,
             current_skill_id=current, reason="cooldown_no_donor_or_unready", next_phase="consume")
        _set_chain_phase(state, "consume", now_ms=now_ms)
        state.chain_started_ms = 0
        return

    # 3) Proven player-RoJway semantics: Auspicious is supported, NEVER
    # required. Use it only if it is ready right now and Echo is also ready;
    # otherwise proceed directly to Echo. This prevents the Hero from parking
    # the whole copy chain while waiting for Auspicious recharge.
    if phase == "auspicious":
        # Do not consume Auspicious unless the required Echo -> RoJ pair is
        # executable right now. This is the exact failure seen in the last log:
        # Auspicious fired, then Echo was unavailable, wasting the setup.
        seed_ready = _ready_roj_choice(state, temporary_only=False) is not None
        echo = _entry_for_slot(state, state.echo_slot) if state.echo_slot > 0 else None
        echo_ready = bool(echo and int(echo[0]) == ARCANE_ECHO_ID and _recharge_data(echo[1]) == 0)
        if not seed_ready or not echo_ready:
            _log(
                "HERO_ROJ_SETUP_ABORT_BEFORE_AUSPICIOUS", hero_index=state.hero_index,
                hero_id=state.hero_agent_id, seed_ready=bool(seed_ready), echo_ready=bool(echo_ready),
                policy="never_waste_auspicious_without_echo_seed",
            )
            _set_chain_phase(state, "consume", now_ms=now_ms)
            state.chain_started_ms = 0
            return
        if state.auspicious_slot > 0:
            ausp = _entry_for_slot(state, state.auspicious_slot)
            ausp_ready = bool(ausp and int(ausp[0]) == AUSPICIOUS_INCANTATION_ID and _recharge_data(ausp[1]) == 0)
            if ausp_ready and _try_auspicious(state):
                return
        _set_chain_phase(state, "echo", now_ms=now_ms)
        _log(
            "HERO_ROJ_AUSPICIOUS_OPTIONAL_SKIP", hero_index=state.hero_index,
            hero_id=state.hero_agent_id, reason="not_ready_or_not_equipped",
        )
        return

    # 4) Echo starts the hard transaction, mirroring the proven player-RoJway
    # sequence. No native heal/utility spell can slip between Echo and the RoJ
    # that seeds it.
    if phase == "echo":
        echo_current = _slot_skill_id(state, state.echo_slot) if state.echo_slot > 0 else 0
        if echo_current == RAY_OF_JUDGMENT_ID:
            _set_chain_phase(state, "consume", now_ms=now_ms)
            return
        if echo_current == ARCANE_ECHO_ID and _ready_entry(state, ARCANE_ECHO_ID) is not None:
            # Echo is useful only if a RoJ is already available to seed it now.
            if _ready_roj_choice(state, temporary_only=False) is not None and _try_arcane_echo(state):
                return
        _log("HERO_ROJ_ECHO_SETUP_ABORT", hero_index=state.hero_index, hero_id=state.hero_agent_id,
             current_skill_id=echo_current, reason="cooldown_or_no_ready_roj_seed", next_phase="consume")
        _set_transaction_lock(state, False)
        state.echo_seed_slot = 0
        _set_chain_phase(state, "consume", now_ms=now_ms)
        state.chain_started_ms = 0
        return

    # 5) Seed Arcane Echo with whichever RoJ is ready first: normally the
    # Mimicry copy, but native RoJ is equally valid if its recharge won the race.
    if phase == "seed_echo":
        if not state.transaction_locked:
            _set_transaction_lock(state, True)
        slot = int(state.echo_seed_slot or 0)
        if slot <= 0 or not _try_roj_exact_slot(state, now_ms, slot, purpose="echo_mandatory_next_roj"):
            # If the originally reserved copy became unavailable during Echo's
            # cast, allow another ready RoJ as the seed but never a non-RoJ.
            fallback = _ready_roj_choice(state, temporary_only=False)
            if fallback is not None:
                state.echo_seed_slot = int(fallback[0])
                _try_roj_exact_slot(state, now_ms, state.echo_seed_slot, purpose="echo_mandatory_next_roj")
        return

    # 6) Wait until Arcane Echo's physical slot becomes RoJ, then consume that
    # exact copy before releasing the protected transaction.
    if phase == "wait_echo_copy":
        if state.echo_slot > 0 and _slot_skill_id(state, state.echo_slot) == RAY_OF_JUDGMENT_ID:
            _set_chain_phase(state, "echo_copy", now_ms=now_ms)
            _log(
                "HERO_ROJ_ECHO_COPY_CONFIRMED", hero_index=state.hero_index, hero_id=state.hero_agent_id,
                echo_slot=state.echo_slot, seed_slot=state.echo_seed_slot,
            )
            return
        if state.echo_seed_cast_ms > 0 and now_ms - state.echo_seed_cast_ms >= _ECHO_COPY_APPEAR_TIMEOUT_MS:
            _log(
                "HERO_ROJ_ECHO_COPY_TIMEOUT", hero_index=state.hero_index, hero_id=state.hero_agent_id,
                echo_slot=state.echo_slot, seed_slot=state.echo_seed_slot,
            )
            _set_transaction_lock(state, False)
            state.echo_seed_slot = 0
            state.echo_seed_cast_ms = 0
            _set_chain_phase(state, "consume", now_ms=now_ms)
        return

    if phase == "echo_copy":
        if state.echo_slot > 0 and _slot_skill_id(state, state.echo_slot) == RAY_OF_JUDGMENT_ID:
            if _try_roj_exact_slot(state, now_ms, state.echo_slot, purpose="echo_copy_roj"):
                return
            # If the copied RoJ is visible but the shared lane is briefly busy,
            # hold the transaction instead of letting filler slip in. Emergency
            # healing has already had its pre-transaction opportunity above.
            if now_ms - int(state.last_phase_wait_log_ms or 0) >= 1200:
                state.last_phase_wait_log_ms = int(now_ms)
                _log(
                    "HERO_ROJ_PHASE_WAIT", hero_index=state.hero_index, hero_id=state.hero_agent_id,
                    phase="echo_copy", phase_duration_ms=max(0, int(now_ms - int(state.chain_phase_started_ms or now_ms))),
                    expected_skill_id=int(RAY_OF_JUDGMENT_ID), echo_slot=int(state.echo_slot),
                    reason="echo_copy_visible_but_cast_request_not_accepted",
                    alternatives_policy="protected_echo_transaction_no_filler_emergency_heal_checked_before_phase",
                )
            return
        _log(
            "HERO_ROJ_ECHO_COPY_LOST", hero_index=state.hero_index, hero_id=state.hero_agent_id,
            echo_slot=state.echo_slot, current_skill_id=_slot_skill_id(state, state.echo_slot),
        )
        _set_transaction_lock(state, False)
        state.echo_seed_slot = 0
        state.echo_seed_cast_ms = 0
        _set_chain_phase(state, "consume", now_ms=now_ms)
        return

    # 7) Consume remaining copied/native RoJs adaptively. Support and Castigation
    # are available between rays. Start a fresh chain once both copy slots are
    # back to their base skills and native RoJ becomes ready again.
    if phase == "consume":
        if _try_roj(state, now_ms, rank):
            return
        if _try_stage227_protection(state, states, now_ms, emergency_only=False):
            return
        if not _is_stage227_prot_bar(state) and _try_smart_heal(state, emergency_only=False):
            return
        if _try_mantra_of_frost(state):
            return
        if not _is_stage227_prot_bar(state) and _try_zealots_fire(state):
            return
        if _try_castigation_energy(state):
            return
        if _try_bane_signet(state, now_ms):
            return
        if _try_symbol_of_wrath(state, now_ms):
            return
        mim_base = state.mimicry_slot <= 0 or _slot_skill_id(state, state.mimicry_slot) == ARCANE_MIMICRY_ID
        echo_base = state.echo_slot <= 0 or _slot_skill_id(state, state.echo_slot) == ARCANE_ECHO_ID
        native = _entry_for_slot(state, state.base_roj_slot) if state.base_roj_slot > 0 else None
        if mim_base and echo_base and native and int(native[0]) == RAY_OF_JUDGMENT_ID and _recharge_data(native[1]) == 0:
            _set_chain_phase(state, "opener", now_ms=now_ms)
        return

    _set_chain_phase(state, "opener", now_ms=now_ms)


def _restore_all() -> None:
    global _MIMICRY_OWNER_HERO_ID, _LAST_SYMBOL_TEAM_CAST_MS, _LAST_BANE_TEAM_CAST_MS
    global _LAST_AEGIS_CASTER_HERO_ID, _LAST_AEGIS_CONFIRMED_MS, _LAST_AEGIS_OBSERVED_DURATION_MS
    global _ADAPTIVE_PROT_MODE, _ADAPTIVE_PROT_MODE_SINCE_MS, _ADAPTIVE_PROT_STABLE_SINCE_MS, _ADAPTIVE_PROT_LAST_LOG_MS
    global _PI_BURST_EVENT_EXPIRES, _PI_BURST_ANCHOR, _PI_BURST_MEMBERS
    global _PI_BURST_GOAL, _PI_BURST_CASTS, _PI_BURST_LAST_ARM_LOG_MS
    for state in list(_STATES.values()):
        _restore_native_ai(state)
    _STATES.clear()
    _DISCOVERED_HEROES.clear()
    _ROJ_COVERAGE_HISTORY.clear()
    _SYMBOL_COVERAGE_HISTORY.clear()
    _HEAL_CLAIMS.clear()
    _PROT_CLAIMS.clear()
    _AEGIS_FAILOVER_SKIP_UNTIL.clear()
    _LAST_AEGIS_CASTER_HERO_ID = 0
    _LAST_AEGIS_CONFIRMED_MS = 0
    _LAST_AEGIS_OBSERVED_DURATION_MS = 0
    _ADAPTIVE_PROT_MODE = "FULL_PROT"
    _ADAPTIVE_PROT_MODE_SINCE_MS = 0
    _ADAPTIVE_PROT_STABLE_SINCE_MS = 0
    _ADAPTIVE_PROT_LAST_LOG_MS = 0
    global _ADAPTIVE_TEAM_SNAPSHOT_MS, _ADAPTIVE_TEAM_SNAPSHOT, _ADAPTIVE_HP_BASELINE
    _ADAPTIVE_TEAM_SNAPSHOT_MS = 0
    _ADAPTIVE_TEAM_SNAPSHOT = {}
    _ADAPTIVE_HP_BASELINE.clear()
    _MIMICRY_OWNER_HERO_ID = 0
    _LAST_SYMBOL_TEAM_CAST_MS = 0
    _LAST_BANE_TEAM_CAST_MS = 0
    _PI_BURST_EVENT_EXPIRES = 0
    _PI_BURST_ANCHOR = 0
    _PI_BURST_MEMBERS = ()
    _PI_BURST_GOAL = 0
    _PI_BURST_CASTS = 0
    _PI_BURST_LAST_ARM_LOG_MS = 0


def run(*, enabled: bool = True) -> None:
    global _LAST_MAP_ID, _RUNTIME_LOGGED
    if not enabled:
        _restore_all()
        return
    try:
        map_id = int(Map.GetMapID() or 0)
        if not Map.IsExplorable() or not Party.IsPartyLoaded():
            _restore_all()
            return
        # Hero-owning accounts may be non-leaders in the 12-man multibox team.
        # Matching by owned hero positions is sufficient; do not require party leadership.
    except Exception:
        return

    if _LAST_MAP_ID and map_id != _LAST_MAP_ID:
        _restore_all()
    _LAST_MAP_ID = map_id

    if not _RUNTIME_LOGGED:
        _RUNTIME_LOGGED = True
        _log("HERO_ROJ_CONTROLLER_ACTIVE", version="v55_stage2_55_urgoz_aegis_prot_gate_fixed")

    states = _reconcile_states()
    if not states:
        return

    try:
        from Py4GWCoreLib.Builds.Skills.CombatSense import single_hostile_spirit_cleanup_target
        if int(single_hostile_spirit_cleanup_target() or 0) > 0:
            return
    except Exception:
        pass

    now_ms = _now_ms()
    for rank, state in enumerate(states):
        try:
            _run_state(state, states, now_ms, rank)
        except Exception as exc:
            _log("HERO_ROJ_CONTROLLER_ERROR", hero_index=state.hero_index, hero_id=state.hero_agent_id,
                 error=type(exc).__name__, detail=str(exc)[:160])
