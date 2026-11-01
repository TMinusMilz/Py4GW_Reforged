from __future__ import annotations

from dataclasses import dataclass

from Py4GWCoreLib import Range
from Py4GWCoreLib.Agent import Agent
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.Skill import Skill

# Central Simple-Power dangerous-cast interrupt coordination.
# Design goal: scan often enough to catch real threats, but keep the hot path
# cheap. The scan uses only stable Agent casting data and a fixed skill allow-list.
DANGER_INTERRUPT_SCAN_THROTTLE_MS = 75
DANGER_INTERRUPT_MIN_CLAIM_MS = 900
DANGER_INTERRUPT_MAX_CLAIM_MS = 1750
DANGER_INTERRUPT_DEFAULT_CLAIM_MS = 1100
DANGER_INTERRUPT_DYNAMIC_MIN_CLAIM_MS = 500
DANGER_INTERRUPT_CAST_END_GRACE_MS = 240

# Local per-process movement ownership for a Mesmer that is making a short,
# bounded approach to interrupt a lethal AoE caster.  The follower runtime
# reads this state so normal formation movement cannot overwrite the approach.
# Expiry is intentionally short and must be refreshed every approach tick.
_INTERRUPT_APPROACH_ACTIVE_UNTIL_TICK: int = 0
_INTERRUPT_APPROACH_TARGET_ID: int = 0
_INTERRUPT_APPROACH_ENEMY_SKILL_ID: int = 0


# Ordered by practical priority: resurrection/recovery first, then prot, then
# large AoE/shutdown/pressure. This prevents a Meteor Shower from stealing the
# only ready interrupt while a resurrection or Protective Spirit is going off.
DANGER_INTERRUPT_SKILL_NAMES: tuple[str, ...] = (
    # Priority 0: Resurrection / hard recovery. These are the highest value
    # interrupts because one missed cast can reset the whole spike.
    "Death_Pact_Signet", "Flesh_of_My_Flesh", "Renew_Life", "Restore_Life",
    "Resurrection_Chant", "Resurrection_Signet", "Rebirth", "Light_of_Dwayna",
    "Unyielding_Aura", "Vengeance", "Signet_of_Return", "Sunspear_Rebirth_Signet",

    # Priority 1: Hard protection / defensive swing skills that can make the
    # focused packet survive too long or protect dangerous NPC balls.
    "Aegis", "Protective_Spirit", "Spirit_Bond", "Shield_of_Absorption",
    "Life_Sheath", "Mark_of_Protection", "Shielding_Hands", "Guardian",
    "Protective_Bond", "Life_Barrier", "Life_Bond", "Reversal_of_Fortune",
    "Shield_Guardian", "Aura_of_Stability", "Weapon_of_Warding",
    "Weapon_of_Shadow", "Xinraes_Weapon", "Xinrae's_Weapon",
    "Protective_Was_Kaolai", "Shelter", "Union", "Displacement", "Life",
    "Preservation", "Recuperation", "Recovery", "Weapon_of_Remedy",

    # Priority 2: Big heals / party recovery / condition-hex reset. These are
    # less wipe-dangerous than Meteor Shower, but they waste the team's spike.
    "Word_of_Healing", "Patient_Spirit", "Dwaynas_Kiss", "Dwayna's_Kiss",
    "Orison_of_Healing", "Heal_Other", "Healing_Touch", "Healing_Seed",
    "Seed_of_Life", "Gift_of_Health", "Jameis_Gaze", "Jamei's_Gaze",
    "Ethereal_Light", "Healing_Ribbon", "Heal_Party", "Infuse_Health",
    "Heavens_Delight", "Heaven's_Delight", "Divine_Healing", "Heal_Area",
    "Healing_Burst", "Healing_Light", "Light_of_Deliverance",
    "Karei's_Healing_Circle", "Kareis_Healing_Circle", "Restore_Condition",
    "Mend_Condition", "Mend_Ailment", "Convert_Hexes", "Deny_Hexes",
    "Purge_Signet", "Spirit_Light", "Mend_Body_and_Soul", "Spirit_Transfer",
    "Soothing_Memories", "Wielders_Boon", "Wielder's_Boon", "Vengeful_Weapon",
    "Resilient_Weapon", "Generous_Was_Tsungrai", "Mend_Soul", "Life",

    # Priority 3: Elementalist / armor-ignoring / large packet AoE, especially
    # the skills that punish a tight Simple-Power cluster.
    "Meteor_Shower", "Savannah_Heat", "Searing_Heat", "Rodgorts_Invocation",
    "Rodgort's_Invocation", "Deep_Freeze", "Maelstrom", "Earthquake",
    "Churning_Earth", "Sandstorm", "Fire_Storm", "Eruption", "Meteor",
    "Invoke_Lightning", "Chain_Lightning", "Obsidian_Flame", "Mind_Burn",
    "Mind_Freeze", "Shatterstone", "Teinais_Heat", "Teinai's_Heat",
    "Bed_of_Coals", "Mirror_of_Ice", "Thunderclap", "Shockwave", "Dragon's_Stomp",
    "Dragons_Stomp", "Unsteady_Ground", "Sliver_Armor", "Ward_Against_Harm",
    "Ward_Against_Melee", "Ward_Against_Foes", "Ward_Against_Elements",

    # Priority 4: Mesmer shutdown / punishment. These do not always wipe the
    # party instantly, but they can destroy the team's cast chain.
    "Panic", "Energy_Surge", "Mistrust", "Cry_of_Frustration", "Power_Block",
    "Psychic_Instability", "Visions_of_Regret", "Backfire", "Empathy",
    "Shame", "Diversion", "Power_Leak", "Power_Drain", "Leech_Signet",
    "Complicate", "Arcane_Conundrum", "Migraine", "Ineptitude", "Clumsiness",
    "Wandering_Eye", "Shared_Burden", "Frustration", "Hex_Eater_Vortex",

    # Priority 5: Necromancer packet pressure / melee punishment / corpse bombs.
    "Spiteful_Spirit", "Spoil_Victor", "Mark_of_Pain", "Barbs",
    "Feast_of_Corruption", "Rising_Bile", "Putrid_Explosion", "Discord",
    "Life_Siphon", "Soul_Barbs", "Insidious_Parasite", "Defile_Defenses",
    "Enfeebling_Blood", "Weaken_Armor", "Price_of_Failure", "Reckless_Haste",
    "Tainted_Flesh", "Order_of_Pain", "Order_of_the_Vampire",

    # Priority 6: Monk smite/control and hard knockdown/control from other bars.
    "Ray_of_Judgment", "Signet_of_Judgment", "Shield_of_Judgment", "Bane_Signet",
    "Symbol_of_Wrath", "Scourge_Healing", "Balthazar's_Aura", "Balthazars_Aura",
    "Stoning", "Gale", "Shock", "Yeti_Smash", "Crushing_Blow", "Wild_Blow",

    # Priority 7: Ritualist offensive spirit pressure / shutdown spirits.
    "Pain", "Bloodsong", "Shadowsong", "Dissonance", "Wanderlust", "Anguish",
    "Destruction", "Doom", "Signet_of_Spirits", "Ancestors_Rage",
    "Ancestor's_Rage", "Spirit_Rift", "Clamor_of_Souls", "Caretaker's_Charge",
    "Caretakers_Charge",

    # Priority 8: Ranger / nature ritual swing skills that can punish balling
    # or slow the spike. Edge of Extinction is included because it can snowball
    # hard in dense fights.
    "Edge_of_Extinction", "Frozen_Soil", "Greater_Conflagration",
    "Broad_Head_Arrow",
)


def _build_ordered_skill_ids(skill_names: tuple[str, ...]) -> tuple[int, ...]:
    ordered: list[int] = []
    seen: set[int] = set()
    for skill_name in skill_names:
        try:
            skill_id = int(Skill.GetID(skill_name) or 0)
        except Exception:
            skill_id = 0
        if skill_id <= 0 or skill_id in seen:
            continue
        seen.add(skill_id)
        ordered.append(skill_id)
    return tuple(ordered)


try:
    from Py4GWCoreLib.Builds.Skills import DangerousSkillPriorities as _DSP
    DANGER_INTERRUPT_SKILL_IDS = _DSP.get_registered_skill_ids(_DSP.DEFAULT_RESERVED_INTERRUPT_THRESHOLD)
    DANGER_INTERRUPT_SKILL_IDS_ORDERED = tuple(sorted(
        DANGER_INTERRUPT_SKILL_IDS,
        key=lambda sid: (-_DSP.get_base_score(int(sid), 0), int(sid)),
    ))
except Exception:
    _DSP = None
    DANGER_INTERRUPT_SKILL_IDS_ORDERED = _build_ordered_skill_ids(DANGER_INTERRUPT_SKILL_NAMES)
    DANGER_INTERRUPT_SKILL_IDS = frozenset(DANGER_INTERRUPT_SKILL_IDS_ORDERED)
DANGER_INTERRUPT_PRIORITY = {
    skill_id: index for index, skill_id in enumerate(DANGER_INTERRUPT_SKILL_IDS_ORDERED)
}

# Phase 9: practical severity tiers.  These are deliberately separate from the
# long allow-list order so a small heal cannot outrank a party-wiping AoE merely
# because its name appeared earlier in the file.
_REZ_NAMES = (
    "Death_Pact_Signet", "Flesh_of_My_Flesh", "Renew_Life",
    "Restore_Life", "Resurrection_Chant", "Resurrection_Signet",
    "Rebirth", "Light_of_Dwayna", "Unyielding_Aura", "Vengeance",
    "Signet_of_Return", "Sunspear_Rebirth_Signet",
)
_LETHAL_AOE_NAMES = (
    "Meteor_Shower", "Savannah_Heat", "Searing_Heat", "Teinais_Heat",
    "Teinai's_Heat", "Bed_of_Coals", "Ray_of_Judgment", "Fire_Storm",
    "Maelstrom", "Churning_Earth", "Sandstorm", "Eruption",
    "Deep_Freeze", "Earthquake", "Dragon's_Stomp", "Dragons_Stomp",
    "Unsteady_Ground", "Shockwave", "Invoke_Lightning", "Spirit_Rift",
)
_HARD_PROT_NAMES = (
    "Aegis", "Protective_Spirit", "Spirit_Bond", "Shield_of_Absorption",
    "Life_Sheath", "Mark_of_Protection", "Aura_of_Stability",
    "Weapon_of_Warding", "Shelter", "Union", "Displacement",
    "Protective_Was_Kaolai",
)
_MAJOR_HEAL_NAMES = (
    "Word_of_Healing", "Heal_Party", "Infuse_Health", "Healing_Burst",
    "Light_of_Deliverance", "Heavens_Delight", "Heaven's_Delight",
    "Divine_Healing", "Restore_Condition", "Mend_Body_and_Soul",
    "Spirit_Light", "Spirit_Transfer",
)
_HARD_SHUTDOWN_NAMES = (
    "Panic", "Energy_Surge", "Mistrust", "Cry_of_Frustration",
    "Power_Block", "Psychic_Instability", "Backfire", "Shame",
    "Diversion", "Migraine", "Broad_Head_Arrow", "Frozen_Soil",
    "Dissonance", "Wanderlust", "Signet_of_Judgment",
    "Shield_of_Judgment", "Gale",
)

_REZ_IDS = frozenset(_build_ordered_skill_ids(_REZ_NAMES))
_LETHAL_AOE_IDS = frozenset(_build_ordered_skill_ids(_LETHAL_AOE_NAMES))
_HARD_PROT_IDS = frozenset(_build_ordered_skill_ids(_HARD_PROT_NAMES))
_MAJOR_HEAL_IDS = frozenset(_build_ordered_skill_ids(_MAJOR_HEAL_NAMES))
_HARD_SHUTDOWN_IDS = frozenset(_build_ordered_skill_ids(_HARD_SHUTDOWN_NAMES))
_PSYCHIC_INSTABILITY_ID = int(Skill.GetID("Psychic_Instability") or 0)

@dataclass(frozen=True, slots=True)
class _InterruptProfile:
    activation_ms: int
    recharge_ms: int
    is_fast: bool
    is_slow: bool
    is_scarce: bool


def _danger_tier(skill_id: int) -> int:
    sid = int(skill_id or 0)
    try:
        if _DSP is not None:
            score = int(_DSP.get_base_score(sid, 0))
            if score >= 108:
                return 6
            if score >= 102:
                return 5
            if score >= 96:
                return 4
            if score >= 90:
                return 3
            if score >= 80:
                return 2
            return 1
    except Exception:
        pass
    if sid in _REZ_IDS:
        return 6
    if sid in _LETHAL_AOE_IDS:
        return 5
    if sid in _HARD_PROT_IDS:
        return 4
    if sid in _MAJOR_HEAL_IDS:
        return 3
    if sid in _HARD_SHUTDOWN_IDS:
        return 2
    return 1


def _interrupt_profile(interrupter_skill_id: int) -> _InterruptProfile:
    sid = int(interrupter_skill_id or 0)
    activation_ms = 750
    recharge_ms = 10000
    try:
        activation_ms = max(0, int(float(Skill.Data.GetActivation(sid) or 0.0) * 1000.0))
    except Exception:
        pass
    try:
        recharge_ms = max(0, int(float(Skill.Data.GetRecharge(sid) or 0.0) * 1000.0))
    except Exception:
        pass
    return _InterruptProfile(
        activation_ms=int(activation_ms),
        recharge_ms=int(recharge_ms),
        is_fast=bool(activation_ms <= 500),
        is_slow=bool(activation_ms >= 1000),
        is_scarce=bool(recharge_ms >= 15000),
    )

_SCAN_CACHE: dict[str, object] = {
    "tick": 0,
    "range_value": 0,
    "player_xy": None,
    "candidates": (),
}

# Cross-process whiteboard posting is not an atomic check-and-set operation.
# A deterministic micro-stagger gives one account time to publish first and
# removes the duplicate claims observed in the combat logs.
_CLAIM_ELECTION_FIRST_SEEN: dict[tuple[int, int], int] = {}
_CLAIM_ELECTION_STEP_MS = 18
_CLAIM_ELECTION_MAX_AGE_MS = 8000

# Quality-aware election delays. A real direct interrupt gets first refusal;
# Keystone proxy signets follow almost immediately; Signet of Judgment is the
# reliable but slow fallback. This prevents a slow SoJ claim from blocking a
# ready Power Drain / Cry / Signet of Disruption on another account.
_DIRECT_INTERRUPT_FIRST_IDS = frozenset(_build_ordered_skill_ids((
    "Cry_of_Frustration", "Cry_of_Pain", "Power_Drain", "Power_Spike",
    "Leech_Signet", "Power_Leak", "Complicate", "Psychic_Instability",
)))
_DIRECT_INTERRUPT_SECOND_IDS = frozenset(_build_ordered_skill_ids((
    "Signet_of_Disruption",
)))
_SLOW_CONTROL_INTERRUPT_IDS = frozenset(_build_ordered_skill_ids((
    "Signet_of_Judgment", "Bane_Signet", "Technobabble",
)))


def _interrupt_election_base_delay_ms(interrupter_skill_id: int) -> int:
    sid = int(interrupter_skill_id or 0)
    if sid in _DIRECT_INTERRUPT_FIRST_IDS:
        return 0
    if sid in _DIRECT_INTERRUPT_SECOND_IDS:
        return 10
    if sid in _SLOW_CONTROL_INTERRUPT_IDS:
        return 85
    try:
        # Under Keystone Signet, an ordinary foe-targeted signet can interrupt
        # another dangerous caster adjacent to its target. Give those proxy
        # signets priority over SoJ, but behind dedicated direct interrupts.
        if sid > 0 and Skill.Flags.IsSignet(sid):
            return 28
    except Exception:
        pass
    return 55


def get_game_tick() -> int:
    try:
        import PySystem
        tick = int(PySystem.get_tick_count64() or 0)
        if tick > 0:
            return tick
    except Exception:
        pass
    try:
        import Py4GW
        return int(Py4GW.Game.get_tick_count64() or 0)
    except Exception:
        return 0

def _owner_context() -> tuple[str, int]:
    """Return the shared multibox isolation group used for interrupt claims.

    The native PartyID is not guaranteed to be identical across every injected
    client. Prefer the ShMem account/isolation group so a player PI Mesmer and
    a PI hero owned by another client compete for the same interrupt locks.
    PartyID remains a compatibility fallback only.
    """
    try:
        from Py4GWCoreLib import GLOBAL_CACHE
        email = str(Player.GetAccountEmail() or "").strip()
        if not email:
            return "", 0
        try:
            group_id = int(GLOBAL_CACHE.ShMem.GetAccountGroupByEmail(email) or 0)
        except Exception:
            group_id = 0
        if group_id > 0:
            return email, group_id
        try:
            for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
                if str(getattr(account, "AccountEmail", "") or "").strip() == email:
                    group_id = int(getattr(account, "IsolationGroupID", 0) or 0)
                    if group_id > 0:
                        return email, group_id
                    break
        except Exception:
            pass
        try:
            group_id = int(GLOBAL_CACHE.Party.GetPartyID() or 0)
        except Exception:
            group_id = 0
        return email, max(0, int(group_id))
    except Exception:
        return "", 0


def _enemy_cast_remaining_ms(target_agent_id: int, casting_skill_id: int) -> int | None:
    """Return estimated enemy cast time remaining, or None when unavailable.

    Reforged's CastObserver is preferred through CombatSense.  The established
    polling timestamp remains the fallback inside CombatSense, so this helper
    does not create another enemy scan.
    """
    try:
        from Py4GWCoreLib.Builds.Skills import CombatSense

        activation_ms = int(CombatSense.get_cast_activation_ms_for_agent(int(target_agent_id), int(casting_skill_id), 1000))
        elapsed_ms = int(CombatSense.get_cast_seen_ms(int(target_agent_id), int(casting_skill_id)))
        if activation_ms <= 0:
            return None
        return max(0, activation_ms - max(0, elapsed_ms))
    except Exception:
        return None


def _skill_claim_duration_ms(
    interrupter_skill_id: int = 0,
    target_agent_id: int = 0,
    casting_skill_id: int = 0,
) -> int:
    """Choose a lock lifetime long enough for this attempt, but not longer.

    The old fixed 0.9-1.75 second lock could remain active after a short cast
    had already completed or been interrupted.  That briefly blocked a fresh
    recast of the same skill by the same enemy.  When cast timing is available,
    cap the lock shortly after the predicted cast end; otherwise preserve the
    previous conservative duration.
    """
    duration = DANGER_INTERRUPT_DEFAULT_CLAIM_MS
    try:
        from Py4GWCoreLib import GLOBAL_CACHE
        activation_ms = int(float(GLOBAL_CACHE.Skill.Data.GetActivation(int(interrupter_skill_id)) or 0.0) * 1000)
        aftercast_ms = int(float(GLOBAL_CACHE.Skill.Data.GetAftercast(int(interrupter_skill_id)) or 0.0) * 1000)
        duration = activation_ms + aftercast_ms + 300
    except Exception:
        pass

    duration = max(DANGER_INTERRUPT_MIN_CLAIM_MS, min(DANGER_INTERRUPT_MAX_CLAIM_MS, int(duration)))
    remaining_ms = _enemy_cast_remaining_ms(int(target_agent_id), int(casting_skill_id))
    if remaining_ms is None:
        return int(duration)

    cast_end_cap = max(
        DANGER_INTERRUPT_DYNAMIC_MIN_CLAIM_MS,
        int(remaining_ms) + DANGER_INTERRUPT_CAST_END_GRACE_MS,
    )
    dynamic_duration = max(
        DANGER_INTERRUPT_DYNAMIC_MIN_CLAIM_MS,
        min(int(duration), int(cast_end_cap)),
    )
    try:
        from Py4GWCoreLib.Builds.Skills import Telemetry
        Telemetry.count("interrupt.dynamic_claim_duration")
    except Exception:
        pass
    return int(dynamic_duration)


def get_casting_skill_id(agent_id: int) -> int:
    try:
        agent_id = int(agent_id or 0)
        if agent_id <= 0:
            return 0
        if not Agent.IsValid(agent_id) or not Agent.IsAlive(agent_id):
            return 0
        if not Agent.IsCasting(agent_id):
            return 0
        return int(Agent.GetCastingSkillID(agent_id) or 0)
    except Exception:
        return 0


def is_dangerous_cast(agent_id: int) -> bool:
    return get_casting_skill_id(agent_id) in DANGER_INTERRUPT_SKILL_IDS


def target_still_casting_skill(agent_id: int, casting_skill_id: int) -> bool:
    return int(agent_id or 0) > 0 and int(casting_skill_id or 0) > 0 and get_casting_skill_id(agent_id) == int(casting_skill_id)


def _distance_to_player(agent_id: int, player_xy=None) -> float:
    try:
        from Py4GWCoreLib.Py4GWcorelib import Utils
        if player_xy is None:
            player_xy = Player.GetXY()
        return float(Utils.Distance(player_xy, Agent.GetXY(agent_id)))
    except Exception:
        return 0.0


def danger_sort_key(agent_id: int, player_xy=None) -> tuple[int, float, int]:
    skill_id = get_casting_skill_id(agent_id)
    priority = DANGER_INTERRUPT_PRIORITY.get(skill_id, 9999)
    return (priority, _distance_to_player(agent_id, player_xy), int(agent_id))


def _scan_dangerous_casts(range_value: int = Range.Spellcast.value) -> tuple[tuple[int, int], ...]:
    """Return cached tuple of (enemy_id, casting_skill_id) in danger priority order."""
    now = get_game_tick()
    try:
        player_xy = Player.GetXY()
    except Exception:
        player_xy = None

    cached_tick = int(_SCAN_CACHE.get("tick") or 0)
    scan_throttle_ms = int(DANGER_INTERRUPT_SCAN_THROTTLE_MS)
    try:
        from Py4GWCoreLib.Builds.Skills import SimplePowerSettings
        scan_throttle_ms = int(SimplePowerSettings.get_value("danger_interrupt_scan_throttle_ms", scan_throttle_ms))
    except Exception:
        pass
    if (
        now > 0
        and cached_tick > 0
        and now - cached_tick < scan_throttle_ms
        and int(_SCAN_CACHE.get("range_value") or 0) == int(range_value)
    ):
        return tuple(_SCAN_CACHE.get("candidates") or ())

    candidates: list[tuple[int, int]] = []
    used_combat_sense = False

    # Prefer the shared CombatSense cache.  This avoids repeated full enemy
    # scans across builds and skips very-late short casts that are unlikely to
    # be interrupted in time.  If CombatSense successfully reports no valid
    # candidate, do not immediately rescan locally and re-add the late cast;
    # only use the local scan when CombatSense is disabled or failed.
    try:
        from Py4GWCoreLib.Builds.Skills import SimplePowerSettings
        if SimplePowerSettings.is_feature_enabled("combat_sense_cache", True):
            from Py4GWCoreLib.Builds.Skills import CombatSense
            used_combat_sense = True
            candidates = list(CombatSense.get_dangerous_cast_candidates(
                range_value=float(range_value),
                dangerous_skill_ids=DANGER_INTERRUPT_SKILL_IDS,
                priority_map=DANGER_INTERRUPT_PRIORITY,
            ))
    except Exception as exc:
        candidates = []
        used_combat_sense = False
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_guarded_exception(
                "danger_interrupt_combat_sense_scan",
                exc,
                range_value=int(range_value),
            )
        except Exception:
            pass

    if not candidates and not used_combat_sense:
        try:
            from Py4GWCoreLib import AgentArray
            enemies = AgentArray.GetEnemyArray()
            enemies = AgentArray.Filter.ByDistance(enemies, player_xy, int(range_value))
        except Exception:
            enemies = []

        for enemy_id in enemies or []:
            try:
                enemy_id = int(enemy_id or 0)
                if enemy_id <= 0:
                    continue
                casting_skill_id = get_casting_skill_id(enemy_id)
                if casting_skill_id in DANGER_INTERRUPT_SKILL_IDS:
                    candidates.append((enemy_id, casting_skill_id))
            except Exception:
                continue

        candidates.sort(key=lambda item: danger_sort_key(item[0], player_xy))
    result = tuple(candidates)
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug, DangerousSkillPriorities as DSP
        CombatDebug.tick()
        for debug_agent_id, debug_skill_id in result:
            CombatDebug.mark_dangerous_cast(
                int(debug_agent_id),
                int(debug_skill_id),
                score=int(DSP.get_base_score(int(debug_skill_id))),
            )
            try:
                from Py4GWCoreLib.Builds.Skills import CombatSense
                cast_source = str(CombatSense.get_cast_source(int(debug_agent_id), int(debug_skill_id)) or "unknown")
                cast_start_tick = int(CombatSense.get_cast_start_tick(int(debug_agent_id), int(debug_skill_id)) or 0)
                CombatDebug.log_keyed_state_event(
                    "DANGEROUS_CAST_SOURCE",
                    (int(debug_agent_id), int(debug_skill_id), int(cast_start_tick)),
                    signature_fields=("caster_id", "skill_id", "source", "start_tick"),
                    min_interval_ms=120,
                    heartbeat_ms=1500,
                    caster_id=int(debug_agent_id),
                    skill_id=int(debug_skill_id),
                    source=cast_source,
                    start_tick=int(cast_start_tick),
                )
            except Exception as exc:
                try:
                    CombatDebug.log_guarded_exception(
                        "danger_interrupt_cast_source_diag",
                        exc,
                        caster_id=int(debug_agent_id),
                        skill_id=int(debug_skill_id),
                    )
                except Exception:
                    pass
    except Exception:
        pass
    _SCAN_CACHE["tick"] = now
    _SCAN_CACHE["range_value"] = int(range_value)
    _SCAN_CACHE["player_xy"] = player_xy
    _SCAN_CACHE["candidates"] = result
    return result


def _active_account_emails(interrupter_skill_id: int = 0) -> list[str]:
    """Return live same-group contenders, preferably with this skill ready.

    The previous election ranked every connected account, including accounts
    without an interrupt. With eight clients that could add hundreds of
    milliseconds. Shared skillbar data lets us stagger only genuine contenders.
    """
    try:
        from Py4GWCoreLib import GLOBAL_CACHE
        own_email, own_group = _owner_context()
        all_live: list[str] = []
        skill_ready: list[str] = []
        wanted_sid = int(interrupter_skill_id or 0)
        for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
            email = str(getattr(account, "AccountEmail", "") or "").strip()
            if not email:
                continue
            account_group = int(getattr(account, "IsolationGroupID", 0) or 0)
            if own_group > 0 and account_group > 0 and account_group != own_group:
                continue
            agent_data = getattr(account, "AgentData", None)
            agent_id = int(getattr(agent_data, "AgentID", 0) or 0)
            if agent_id > 0:
                try:
                    if not Agent.IsValid(agent_id) or not Agent.IsAlive(agent_id):
                        continue
                except Exception:
                    pass
            all_live.append(email)
            if wanted_sid <= 0:
                continue
            try:
                skillbar = getattr(agent_data, "Skillbar", None)
                if int(getattr(skillbar, "CastingSkillID", 0) or 0) > 0:
                    continue
                for skill in getattr(skillbar, "Skills", ()) or ():
                    if int(getattr(skill, "Id", 0) or 0) != wanted_sid:
                        continue
                    if float(getattr(skill, "Recharge", 0.0) or 0.0) <= 0.0:
                        skill_ready.append(email)
                    break
            except Exception:
                pass
        preferred = sorted(set(skill_ready))
        if own_email and own_email in preferred:
            return preferred
        return sorted(set(all_live))
    except Exception:
        return []


def _claim_election_ready(
    target_agent_id: int,
    casting_skill_id: int,
    now_tick: int,
    interrupter_skill_id: int = 0,
) -> tuple[bool, int]:
    """Deterministically stagger accounts before posting one shared claim.

    The best interrupt class gets first refusal; equal skills are staggered by 18 ms.  If the first
    account lacks a feasible/ready interrupt, the next one takes over shortly
    afterwards.  This avoids the non-atomic ``IsLockBlocked`` + ``PostLock``
    race without adding a costly cross-process handshake.
    """
    key = (int(target_agent_id), int(casting_skill_id))
    for old_key, first_tick in list(_CLAIM_ELECTION_FIRST_SEEN.items()):
        if int(now_tick) - int(first_tick) > _CLAIM_ELECTION_MAX_AGE_MS:
            _CLAIM_ELECTION_FIRST_SEEN.pop(old_key, None)
    first_seen = int(_CLAIM_ELECTION_FIRST_SEEN.setdefault(key, int(now_tick)))
    email, _ = _owner_context()
    emails = _active_account_emails(int(interrupter_skill_id or 0))
    base_delay = _interrupt_election_base_delay_ms(int(interrupter_skill_id or 0))
    if not email or email not in emails or len(emails) <= 1:
        required_delay = int(base_delay)
        return int(now_tick) - first_seen >= required_delay, required_delay
    seed = ((int(target_agent_id) * 1103515245) ^ (int(casting_skill_id) * 2654435761)) & 0x7FFFFFFF
    offset = seed % len(emails)
    ordered = emails[offset:] + emails[:offset]
    rank = ordered.index(email)
    required_delay = int(base_delay) + int(rank) * _CLAIM_ELECTION_STEP_MS
    return int(now_tick) - first_seen >= required_delay, required_delay


def _matching_interrupt_locks(target_agent_id: int, casting_skill_id: int, group_id: int, now_tick: int):
    try:
        from Py4GWCoreLib import GLOBAL_CACHE
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        all_accounts = GLOBAL_CACHE.ShMem.GetAllAccounts()
        out = []
        for slot, intent in all_accounts.GetAllIntents() or []:
            if int(getattr(intent, "ExpiresAtTick", 0) or 0) <= int(now_tick):
                continue
            if int(getattr(intent, "KindID", 0) or 0) != int(WhiteboardLockKind.INTERRUPT_TARGET):
                continue
            if int(getattr(intent, "SkillID", 0) or 0) != int(casting_skill_id):
                continue
            if int(getattr(intent, "TargetAgentID", 0) or 0) != int(target_agent_id):
                continue
            if int(getattr(intent, "IsolationGroupID", 0) or 0) != int(group_id):
                continue
            out.append((int(slot), intent))
        return out
    except Exception:
        return []


def _verify_interrupt_claim_owner(target_agent_id: int, casting_skill_id: int, now_tick: int) -> bool:
    email, group_id = _owner_context()
    if not email:
        return True
    locks = _matching_interrupt_locks(target_agent_id, casting_skill_id, group_id, now_tick)
    if not locks:
        return True
    winner_slot, winner = min(
        locks,
        key=lambda pair: (int(getattr(pair[1], "PostedAtTick", 0) or 0), str(getattr(pair[1], "OwnerEmail", "") or ""), int(pair[0])),
    )
    winner_email = str(getattr(winner, "OwnerEmail", "") or "")
    if winner_email == email:
        return True
    try:
        from Py4GWCoreLib import GLOBAL_CACHE
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        GLOBAL_CACHE.ShMem.GetAllAccounts().ClearLockByOwnerKindTarget(
            email, int(WhiteboardLockKind.INTERRUPT_TARGET), int(target_agent_id), int(group_id)
        )
    except Exception:
        pass
    return False


def _is_interrupt_claim_blocked(target_agent_id: int, casting_skill_id: int, now_tick: int | None = None) -> bool:
    if int(target_agent_id or 0) <= 0 or int(casting_skill_id or 0) <= 0:
        return True
    try:
        from Py4GWCoreLib import GLOBAL_CACHE
        from Py4GWCoreLib.enums_src.Whiteboard_enums import (
            WhiteboardClaimStrength,
            WhiteboardLockKind,
            WhiteboardLockMode,
            WhiteboardReentryPolicy,
        )

        email, group_id = _owner_context()
        if not email:
            return False
        if now_tick is None:
            now_tick = get_game_tick()
        if now_tick <= 0:
            return False
        shmem = GLOBAL_CACHE.ShMem
        # IsLockBlocked/CountLocks already ignore expired leases.  The global
        # SharedMemory.Update callback performs physical slot cleanup on its own
        # cadence, so a second 64-slot sweep on every interrupt probe is pure
        # duplicate work.
        return bool(shmem.IsLockBlocked(
            int(WhiteboardLockKind.INTERRUPT_TARGET),
            int(casting_skill_id),
            int(target_agent_id),
            int(group_id),
            email,
            int(now_tick),
            int(WhiteboardLockMode.EXCLUSIVE),
            1,
            int(WhiteboardReentryPolicy.NON_REENTRANT),
            int(WhiteboardClaimStrength.HARD),
        ))
    except Exception:
        # Fail open: a broken whiteboard must not disable interrupts.
        return False



def _find_active_owner_account(owner_email: str):
    """Return active shared-memory player account for one claim owner."""
    wanted = str(owner_email or "").strip()
    if not wanted:
        return None
    try:
        from Py4GWCoreLib import GLOBAL_CACHE
        for account in GLOBAL_CACHE.ShMem.GetAllAccountData(sort_results=False) or []:
            if str(getattr(account, "AccountEmail", "") or "").strip() == wanted:
                return account
    except Exception:
        pass
    return None


def _owner_has_ready_direct_interrupt(account) -> bool:
    """Best-effort readiness proof from the owner's shared skillbar."""
    try:
        skillbar = getattr(getattr(account, "AgentData", None), "Skillbar", None)
        casting_sid = int(getattr(skillbar, "CastingSkillID", 0) or 0)
        if casting_sid in _DIRECT_INTERRUPT_FIRST_IDS:
            return True
        for skill in getattr(skillbar, "Skills", ()) or ():
            sid = int(getattr(skill, "Id", 0) or 0)
            if sid not in _DIRECT_INTERRUPT_FIRST_IDS:
                continue
            if float(getattr(skill, "Recharge", 0.0) or 0.0) <= 0.0:
                return True
    except Exception:
        pass
    return False


def _owner_within_interrupt_range(account, target_agent_id: int) -> bool:
    """Use shared position; unknown geometry is treated as viable, not stale."""
    try:
        ad = getattr(account, "AgentData", None)
        pos = getattr(ad, "Pos", None)
        ox, oy = float(getattr(pos, "x", 0.0)), float(getattr(pos, "y", 0.0))
        tx, ty = Agent.GetXY(int(target_agent_id))
        # Zero/zero is a common uninitialised shared position; do not clear on it.
        if ox == 0.0 and oy == 0.0:
            return True
        import math
        return math.hypot(ox - float(tx), oy - float(ty)) <= float(Range.Spellcast.value)
    except Exception:
        return True


def _claim_owner_unviable_reason(account, target_agent_id: int, claim_age_ms: int) -> str:
    """Return a conservative proof reason, or empty string while owner may fire."""
    if account is None:
        return "owner_inactive"
    ad = getattr(account, "AgentData", None)
    try:
        if not bool(getattr(ad, "Is_Alive")):
            return "owner_dead"
    except Exception:
        pass
    try:
        if bool(getattr(ad, "Is_Knocked_Down")):
            return "owner_knocked_down"
    except Exception:
        pass
    try:
        skillbar = getattr(ad, "Skillbar", None)
        casting_sid = int(getattr(skillbar, "CastingSkillID", 0) or 0)
        if casting_sid > 0 and casting_sid not in _DIRECT_INTERRUPT_FIRST_IDS:
            return "owner_busy_non_interrupt"
    except Exception:
        pass
    if not _owner_within_interrupt_range(account, int(target_agent_id)):
        return "owner_out_of_range"
    if _owner_has_ready_direct_interrupt(account):
        return ""

    # Shared skillbar/model-state fields are refreshed on the fast SHMEM cycle.
    # Give them several refreshes before declaring a just-posted owner unready;
    # this is synchronization headroom, not an invented Guild Wars cast rule.
    try:
        from Py4GWCoreLib.GlobalCache.shared_memory_src.Globals import SHMEM_AGENT_FAST_UPDATE_THROTTLE_MS
        sync_grace_ms = max(75, int(SHMEM_AGENT_FAST_UPDATE_THROTTLE_MS) * 3)
    except Exception:
        sync_grace_ms = 120
    if int(claim_age_ms) < int(sync_grace_ms):
        return ""
    return "owner_no_ready_direct_interrupt"


def release_unviable_interrupt_owner_claims(
    target_agent_id: int,
    casting_skill_id: int,
    *,
    skill_target_lock_id: int = 0,
) -> bool:
    """Release only provably stale foreign ownership for this exact live cast.

    Mesmerway uses two layers: the central exact-cast INTERRUPT_TARGET claim and
    a short legacy SKILL_TARGET lease on the enemy.  A dead/KD/out-of-range owner
    or one with no ready/casting direct interrupt must not block a replacement.
    Healthy ownership is never stolen by timeout alone.  When ``skill_target_lock_id``
    is supplied, only that exact Mesmerway SKILL_TARGET key is cleared; unrelated
    target reservations (Mistrust, EVAS, etc.) remain untouched.
    """
    target_agent_id = int(target_agent_id or 0)
    casting_skill_id = int(casting_skill_id or 0)
    skill_target_lock_id = int(skill_target_lock_id or 0)
    if target_agent_id <= 0 or casting_skill_id <= 0:
        return False
    now = int(get_game_tick() or 0)
    email, group_id = _owner_context()
    if not email or group_id <= 0 or now <= 0:
        return False

    try:
        from Py4GWCoreLib import GLOBAL_CACHE
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        all_accounts = GLOBAL_CACHE.ShMem.GetAllAccounts()
        relevant: list[tuple[int, object]] = []
        for slot, intent in all_accounts.GetAllIntents() or []:
            try:
                if int(getattr(intent, "ExpiresAtTick", 0) or 0) <= now:
                    continue
                if int(getattr(intent, "IsolationGroupID", 0) or 0) != int(group_id):
                    continue
                if int(getattr(intent, "TargetAgentID", 0) or 0) != int(target_agent_id):
                    continue
                kind = int(getattr(intent, "KindID", 0) or 0)
                key = int(getattr(intent, "SkillID", 0) or 0)
                exact = (
                    kind == int(WhiteboardLockKind.INTERRUPT_TARGET)
                    and key == int(casting_skill_id)
                )
                legacy = (
                    skill_target_lock_id > 0
                    and kind == int(WhiteboardLockKind.SKILL_TARGET)
                    and key == int(skill_target_lock_id)
                )
                if exact or legacy:
                    relevant.append((int(slot), intent))
            except Exception:
                continue
        if not relevant:
            return False

        owners: dict[str, int] = {}
        for _slot, intent in relevant:
            owner = str(getattr(intent, "OwnerEmail", "") or "").strip()
            if not owner or owner == email:
                continue
            posted = int(getattr(intent, "PostedAtTick", 0) or 0)
            if owner not in owners or (posted > 0 and posted < owners[owner]):
                owners[owner] = posted
        if not owners:
            return False

        released_any = False
        for owner, posted in owners.items():
            age = max(0, int(now) - int(posted or now))
            account = _find_active_owner_account(owner)
            reason = _claim_owner_unviable_reason(account, target_agent_id, age)
            if not reason:
                continue
            cleared = 0
            # Clear only exact matching slots. Do not use the broad owner/kind/target
            # helper because SKILL_TARGET contains unrelated per-skill reservations.
            for _slot, intent in relevant:
                try:
                    if str(getattr(intent, "OwnerEmail", "") or "").strip() != owner:
                        continue
                    intent.reset()
                    cleared += 1
                except Exception:
                    continue
            if cleared <= 0:
                continue
            released_any = True
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "INTERRUPT_CLAIM_TAKEOVER_RELEASE",
                    caster_id=int(target_agent_id), enemy_skill_id=int(casting_skill_id),
                    old_owner=str(owner), reason=str(reason), cleared=int(cleared),
                    claim_age_ms=int(age),
                    policy="stage1_25_proven_unviable_owner_exact_and_mesmerway_lease",
                )
            except Exception:
                pass
        return bool(released_any)
    except Exception:
        return False

def post_interrupt_claim(
    target_agent_id: int,
    casting_skill_id: int,
    interrupter_skill_id: int = 0,
    *,
    claim_duration_ms: int | None = None,
) -> bool:
    """Reserve one dangerous cast. True means this build may attempt the interrupt.

    Lock key: WhiteboardLockKind.INTERRUPT_TARGET + current enemy casting skill +
    enemy agent. That means exactly one account should react to that exact cast.
    """
    if int(target_agent_id or 0) <= 0 or int(casting_skill_id or 0) <= 0:
        return False
    now = get_game_tick()
    ready, delay_ms = _claim_election_ready(
        int(target_agent_id), int(casting_skill_id), int(now), int(interrupter_skill_id or 0)
    )
    if not ready:
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "INTERRUPT_CLAIM_DEFERRED", caster_id=int(target_agent_id),
                enemy_skill_id=int(casting_skill_id), delay_ms=int(delay_ms),
            )
        except Exception:
            pass
        return False
    if _is_interrupt_claim_blocked(int(target_agent_id), int(casting_skill_id), now):
        return False
    try:
        from Py4GWCoreLib import GLOBAL_CACHE
        from Py4GWCoreLib.enums_src.Whiteboard_enums import (
            WhiteboardClaimStrength,
            WhiteboardLockKind,
            WhiteboardLockMode,
            WhiteboardReentryPolicy,
        )

        email, group_id = _owner_context()
        if not email or now <= 0:
            return True
        if claim_duration_ms is None:
            duration_ms = _skill_claim_duration_ms(
                int(interrupter_skill_id or 0),
                int(target_agent_id),
                int(casting_skill_id),
            )
        else:
            # A bounded approach may need longer than the normal activation-only
            # lock.  Cap it at five seconds and never extend past an obviously
            # stale cast window.
            duration_ms = max(500, min(5000, int(claim_duration_ms)))
        expires_at_tick = now + int(duration_ms)
        shmem = GLOBAL_CACHE.ShMem
        if hasattr(shmem, "PostLock"):
            slot = shmem.PostLock(
                email,
                int(WhiteboardLockKind.INTERRUPT_TARGET),
                int(casting_skill_id),
                int(target_agent_id),
                int(expires_at_tick),
                int(group_id),
                int(WhiteboardLockMode.EXCLUSIVE),
                1,
                int(WhiteboardReentryPolicy.NON_REENTRANT),
                int(WhiteboardClaimStrength.HARD),
            )
            if int(slot) == -1:
                return False
            if not _verify_interrupt_claim_owner(int(target_agent_id), int(casting_skill_id), int(now)):
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "INTERRUPT_CLAIM_LOST", caster_id=int(target_agent_id),
                        enemy_skill_id=int(casting_skill_id), owner=email,
                    )
                except Exception:
                    pass
                return False
            return True
        if hasattr(shmem, "PostIntent"):
            return shmem.PostIntent(
                email,
                int(casting_skill_id),
                int(target_agent_id),
                int(expires_at_tick),
                int(group_id),
            ) != -1
    except Exception:
        return True
    return True


def release_interrupt_claim(
    target_agent_id: int,
    casting_skill_id: int = 0,
    *,
    reason: str = "not_fired",
) -> bool:
    """Immediately release this account's claim when no cast command was sent.

    A failed CanCast/validator/target check must not block the other accounts for
    the remainder of the lock lifetime. The shared-memory clear operation is
    owner-scoped, so another account's valid claim is never removed.
    """
    if int(target_agent_id or 0) <= 0:
        return False
    try:
        from Py4GWCoreLib import GLOBAL_CACHE
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind

        email, group_id = _owner_context()
        if not email:
            return True
        all_accounts = GLOBAL_CACHE.ShMem.GetAllAccounts()
        cleared = all_accounts.ClearLockByOwnerKindTarget(
            email,
            int(WhiteboardLockKind.INTERRUPT_TARGET),
            int(target_agent_id),
            int(group_id),
        )
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "INTERRUPT_CLAIM_RELEASED",
                caster_id=int(target_agent_id),
                enemy_skill_id=int(casting_skill_id or 0),
                reason=str(reason or "not_fired"),
            )
        except Exception:
            pass
        return bool(cleared is None or cleared)
    except Exception:
        # Fail open. Expiry remains the final safety net.
        return False


def has_dangerous_cast_in_range(range_value: int = Range.Spellcast.value) -> bool:
    """True while any registered dangerous enemy cast is live in range."""
    try:
        return bool(_scan_dangerous_casts(int(range_value)))
    except Exception:
        return False



def get_dangerous_casts_in_range(range_value: int = Range.Spellcast.value) -> tuple[tuple[int, int], ...]:
    """Public read-only view used by bounded interrupt-approach logic."""
    try:
        return tuple(_scan_dangerous_casts(int(range_value)))
    except Exception:
        return ()


def get_enemy_cast_remaining_ms(target_agent_id: int, casting_skill_id: int) -> int | None:
    """Public wrapper around the shared native-event cast timer."""
    return _enemy_cast_remaining_ms(int(target_agent_id), int(casting_skill_id))


def is_lethal_aoe_skill(skill_id: int) -> bool:
    """True only for the party-threatening AoE set used by chase interrupts."""
    return int(skill_id or 0) in _LETHAL_AOE_IDS


def is_interrupt_claim_blocked(target_agent_id: int, casting_skill_id: int) -> bool:
    """Public best-effort shared-claim check; failures remain fail-open."""
    return _is_interrupt_claim_blocked(int(target_agent_id), int(casting_skill_id))


def interrupt_is_feasible(target_agent_id: int, interrupter_skill_id: int) -> bool:
    """Public exact in-range timing check used at the end of an approach."""
    return _reforged_interrupt_is_feasible(int(target_agent_id), int(interrupter_skill_id))


def mark_interrupt_approach_movement_active(
    target_agent_id: int,
    casting_skill_id: int,
    *,
    hold_ms: int = 450,
) -> None:
    """Refresh local movement ownership while a Mesmer closes interrupt range."""
    global _INTERRUPT_APPROACH_ACTIVE_UNTIL_TICK
    global _INTERRUPT_APPROACH_TARGET_ID
    global _INTERRUPT_APPROACH_ENEMY_SKILL_ID
    now = get_game_tick()
    if now <= 0:
        return
    _INTERRUPT_APPROACH_ACTIVE_UNTIL_TICK = int(now) + max(100, int(hold_ms))
    _INTERRUPT_APPROACH_TARGET_ID = int(target_agent_id or 0)
    _INTERRUPT_APPROACH_ENEMY_SKILL_ID = int(casting_skill_id or 0)


def clear_interrupt_approach_movement() -> None:
    global _INTERRUPT_APPROACH_ACTIVE_UNTIL_TICK
    global _INTERRUPT_APPROACH_TARGET_ID
    global _INTERRUPT_APPROACH_ENEMY_SKILL_ID
    _INTERRUPT_APPROACH_ACTIVE_UNTIL_TICK = 0
    _INTERRUPT_APPROACH_TARGET_ID = 0
    _INTERRUPT_APPROACH_ENEMY_SKILL_ID = 0


def is_interrupt_approach_movement_active() -> bool:
    """True only while the current process has a freshly refreshed approach."""
    now = get_game_tick()
    if now <= 0 or int(_INTERRUPT_APPROACH_ACTIVE_UNTIL_TICK) <= int(now):
        clear_interrupt_approach_movement()
        return False
    return int(_INTERRUPT_APPROACH_TARGET_ID) > 0 and int(_INTERRUPT_APPROACH_ENEMY_SKILL_ID) > 0


def _target_is_knocked_down(target_agent_id: int) -> bool:
    """Return True when the enemy is already disabled by a knockdown.

    Simple-Power creates frequent knockdowns through Signet of Judgment and
    Keystone chains.  During the short client-state transition, an enemy can
    still appear to be casting even though the cast has already been cancelled.
    Suppressing a new interrupt claim in that window avoids wasting a dedicated
    interrupt on a cast the team has already stopped.
    """
    try:
        from Py4GWCoreLib import Routines
        return bool(Routines.Checks.Agents.IsKnockedDown(int(target_agent_id)))
    except Exception:
        return False


def _reforged_interrupt_is_feasible(target_agent_id: int, interrupter_skill_id: int) -> bool:
    """Use Reforged's native interrupt timing model when available.

    The helper accounts for observed enemy cast progress, our activation time,
    Fast Casting, range, ping and a reaction margin.  It intentionally fails
    open so the established CombatSense path remains usable if Reforged changes
    an internal API or the timing observer has not seen this cast yet.
    """
    if int(target_agent_id or 0) <= 0 or int(interrupter_skill_id or 0) <= 0:
        return True
    try:
        from Py4GWCoreLib.HeroAI import interrupt as reforged_interrupt

        try:
            fast_casting_level = int(reforged_interrupt._get_player_fast_casting_level() or 0)
        except Exception:
            fast_casting_level = 0

        try:
            ping_ms = int(reforged_interrupt._PING_HANDLER.GetCurrentPing() or 0)
        except Exception:
            ping_ms = 100
        if ping_ms <= 0:
            ping_ms = 100

        return bool(reforged_interrupt.is_interrupt_feasible(
            int(target_agent_id),
            int(interrupter_skill_id),
            int(fast_casting_level),
            int(ping_ms),
            reaction_margin_ms=90,
            debug=False,
        ))
    except Exception:
        return True

def _candidate_interrupt_score(
    target_agent_id: int,
    casting_skill_id: int,
    interrupter_skill_id: int,
) -> float:
    """Score one cast for the specific ready interrupt skill.

    Phase 9 contributes danger severity and practical impact.  Phase 10 adds
    role fit: fast interrupts are reserved for urgent short windows, slower
    control skills prefer longer casts, and long-recharge interrupts are not
    spent on low-tier pressure unless nothing better exists.
    """
    sid = int(casting_skill_id or 0)
    tier = _danger_tier(sid)
    contextual_priority = 0
    try:
        from Py4GWCoreLib.Builds.Skills import DangerousSkillPriorities as DSP
        from Py4GWCoreLib.Builds.Skills import ThreatMemory
        enemy_for_context = None
        try:
            from Py4GWCoreLib.Builds.Skills import CombatSense
            enemy_for_context = CombatSense.get_enemy_sense(int(target_agent_id), range_value=Range.Spellcast.value)
        except Exception:
            pass
        contextual_priority = DSP.contextual_score(
            sid,
            adjacent_enemies=int(enemy_for_context.adjacent_count) if enemy_for_context is not None else 1,
            enemy_low_health=bool(enemy_for_context is not None and float(enemy_for_context.health) <= 0.35),
            threat_memory_bonus=ThreatMemory.get_interrupt_bonus(int(target_agent_id)),
        )
    except Exception:
        contextual_priority = tier * 16
    score = float(contextual_priority * 1000)

    remaining_ms = _enemy_cast_remaining_ms(int(target_agent_id), sid)
    profile = _interrupt_profile(int(interrupter_skill_id or 0))
    try:
        from Py4GWCoreLib.Builds.Skills import CombatSense
        enemy = CombatSense.get_enemy_sense(int(target_agent_id), range_value=Range.Spellcast.value)
    except Exception:
        enemy = None

    if enemy is not None:
        score += float(min(6, max(0, int(enemy.adjacent_count) - 1)) * 220)
        score += float(max(0, int(enemy.threat_score)) * 3)
        score -= min(1800.0, float(enemy.distance_to_player) * 0.65)

    if remaining_ms is not None:
        remaining = max(0, int(remaining_ms))
        landing_budget = int(profile.activation_ms) + 180
        slack = remaining - landing_budget
        # Urgent but feasible casts deserve immediate attention.
        if 0 <= slack <= 450:
            score += 1800.0 if profile.is_fast else 600.0
        elif slack < 0:
            score -= 10000.0
        elif profile.is_slow and remaining >= 1200:
            score += 700.0
        elif profile.is_fast and remaining <= 900:
            score += 500.0

    # Preserve scarce/long-recharge interrupts for important casts.  This is a
    # score penalty, not a hard block, so the team still reacts if it is the only
    # available answer.
    if profile.is_scarce and tier <= 2:
        score -= 4200.0
    elif profile.is_scarce and tier == 3:
        score -= 1200.0

    # Stable tie-breaker, avoiding oscillation between equal candidates.
    score -= float(int(target_agent_id) % 997) * 0.001
    return float(score)


def get_interrupt_candidate_score(
    target_agent_id: int,
    casting_skill_id: int,
    interrupter_skill_id: int,
) -> float:
    """Public score used by opportunistic multi-skill interrupt controllers.

    This is read-only: it never posts a claim and never reserves an interrupt.
    Controllers can therefore compare every *currently ready* interrupt first,
    then claim only the exact cast they are about to fire on.
    """
    return float(_candidate_interrupt_score(
        int(target_agent_id), int(casting_skill_id), int(interrupter_skill_id)
    ))


def _opportunistic_skill_is_spell_or_chant(skill_id: int) -> bool:
    sid = int(skill_id or 0)
    if sid <= 0:
        return False
    try:
        from Py4GWCoreLib import GLOBAL_CACHE
        flags = GLOBAL_CACHE.Skill.Flags
        return bool(
            flags.IsSpell(sid) or flags.IsChant(sid) or flags.IsHex(sid)
            or flags.IsEnchantment(sid) or flags.IsWeaponSpell(sid)
            or flags.IsItemSpell(sid) or flags.IsWell(sid) or flags.IsWard(sid)
            or flags.IsGlyph(sid)
        )
    except Exception:
        return False


def _opportunistic_support_profession(agent_id: int) -> bool:
    """Cleanup heuristic: primary Monk/Rit spell casts are worth stopping off-focus.

    Secondary Monk/Rit is deliberately *not* enough for the generic cleanup
    fallback: e.g. an offensive Mesmer/Monk should not steal a peel merely
    because its secondary profession is Monk. Known heals/prots remain covered
    by the central dangerous-skill table regardless of profession.
    """
    try:
        primary, _secondary = Agent.GetProfessions(int(agent_id))
        primary_id = int(getattr(primary, "value", primary) or 0)
        try:
            from Py4GWCoreLib import Profession
            monk_id = int(getattr(Profession.Monk, "value", Profession.Monk))
            rit_id = int(getattr(Profession.Ritualist, "value", Profession.Ritualist))
        except Exception:
            return False
        return primary_id in (monk_id, rit_id)
    except Exception:
        return False


def _opportunistic_interrupt_is_feasible(
    target_agent_id: int,
    interrupter_skill_id: int,
    origin_agent_id: int,
) -> bool:
    """Timing gate that is correct for both player and hero interrupters.

    Reforged's native helper currently measures range and Fast Casting from the
    *player* agent.  That is exact for player/Keystone builds but can reject a
    PI hero that is in range while its owning player is not. For a non-player
    origin we therefore use the observed remaining cast time plus the skill's
    unmodified activation as a conservative local gate. The caller has already
    performed the hero-origin range check.
    """
    try:
        player_id = int(Player.GetAgentID() or 0)
    except Exception:
        player_id = 0
    origin_id = int(origin_agent_id or 0)
    remaining_ms = _enemy_cast_remaining_ms(
        int(target_agent_id), int(get_casting_skill_id(int(target_agent_id)) or 0)
    )
    profile = _interrupt_profile(int(interrupter_skill_id))
    pi_id = int(_PSYCHIC_INSTABILITY_ID)

    # v30 adds a command-dispatch cushion on top of Reforged's landing check.
    # The v29 runtime log showed many PI commands acknowledged by UseSkill but
    # never entering activation because the enemy cast window had effectively
    # closed by the next injected frame. Fast 1/4s interrupts keep a small
    # cushion; PI deliberately needs a larger one so Complicate/Cry/Power Drain
    # win short windows whenever they are ready now.
    if origin_id <= 0 or origin_id == player_id:
        if not interrupt_is_feasible(int(target_agent_id), int(interrupter_skill_id)):
            return False
        if remaining_ms is None:
            return True

        effective_activation_ms = int(profile.activation_ms)
        try:
            from Py4GWCoreLib import Routines
            from Py4GWCoreLib.HeroAI import interrupt as reforged_interrupt

            fast_casting_level = int(reforged_interrupt._get_player_fast_casting_level() or 0)
            activation_s, _ = Routines.Checks.Skills.apply_fast_casting(
                int(interrupter_skill_id), fast_casting_level
            )
            if float(activation_s or 0.0) >= 0.0:
                effective_activation_ms = max(0, int(float(activation_s) * 1000.0))
        except Exception:
            pass

        extra_margin_ms = 340 if int(interrupter_skill_id) == pi_id else 140
        return int(remaining_ms) >= int(effective_activation_ms) + int(extra_margin_ms)

    if remaining_ms is None:
        return True
    # Hero Fast Casting is not exposed through the shared account snapshot.
    # Base activation therefore remains the safe lower-level gate. PI gets a
    # larger dispatch cushion; fast Mesmer interrupts retain the old behavior.
    extra_margin_ms = 340 if int(interrupter_skill_id) == pi_id else 180
    return int(remaining_ms) >= int(profile.activation_ms) + int(extra_margin_ms)


def opportunistic_interrupt_is_feasible(
    target_agent_id: int,
    interrupter_skill_id: int,
    *,
    origin_agent_id: int = 0,
) -> bool:
    """Public v30 dispatch-safe feasibility gate for custom interrupt builds."""
    return bool(_opportunistic_interrupt_is_feasible(
        int(target_agent_id),
        int(interrupter_skill_id),
        int(origin_agent_id or 0),
    ))


def _opportunistic_local_counts(target_agent_id: int, range_value: float) -> tuple[int, int]:
    """Return (alive nearby foes, currently casting nearby foes)."""
    try:
        from Py4GWCoreLib import AgentArray
        from Py4GWCoreLib.Py4GWcorelib import Utils
        target_xy = Agent.GetXY(int(target_agent_id))
        enemy_ids = AgentArray.GetEnemyArray() or []
        alive_near = 0
        casting_near = 0
        for enemy_id in enemy_ids:
            enemy_id = int(enemy_id or 0)
            if enemy_id <= 0:
                continue
            try:
                if not Agent.IsValid(enemy_id) or not Agent.IsAlive(enemy_id):
                    continue
                if Utils.Distance(target_xy, Agent.GetXY(enemy_id)) > float(range_value):
                    continue
            except Exception:
                continue
            alive_near += 1
            if get_casting_skill_id(enemy_id) > 0:
                casting_near += 1
        return int(alive_near), int(casting_near)
    except Exception:
        return 1, 1


def rank_opportunistic_interrupt_options(
    interrupter_skill_ids: tuple[int, ...] | list[int],
    *,
    energy_ratio: float = 1.0,
    range_value: int = Range.Spellcast.value,
    origin_agent_id: int = 0,
    cleanup_mode: bool = False,
    focus_ids: tuple[int, ...] | list[int] | set[int] = (),
) -> tuple[tuple[float, int, int, int, str], ...]:
    """Rank live cast/interrupt pairs without reserving anything.

    Return tuples are ``(score, target_id, enemy_skill_id, our_skill_id, mode)``.
    ``mode`` is ``dangerous`` for the central danger table or
    ``cleanup_support`` for the cleanup-only Monk/Rit spell fallback.

    Only skills passed by the caller are considered, so callers should pass
    skills that are ready and affordable right now. No cooldown reservation or
    hold-for-interrupt behavior is introduced here.
    """
    ready_ids = tuple(dict.fromkeys(
        int(sid) for sid in interrupter_skill_ids if int(sid or 0) > 0
    ))
    if not ready_ids:
        return ()

    try:
        from Py4GWCoreLib import AgentArray
        from Py4GWCoreLib.Py4GWcorelib import Utils
    except Exception:
        return ()

    try:
        if int(origin_agent_id or 0) > 0:
            origin_xy = Agent.GetXY(int(origin_agent_id))
        else:
            origin_xy = Player.GetXY()
    except Exception:
        return ()

    try:
        from Py4GWCoreLib.Builds.Skills import DangerousSkillPriorities as DSP
    except Exception:
        DSP = None

    min_score = 80
    try:
        from Py4GWCoreLib.Builds.Skills import SimplePowerSettings
        min_score = int(SimplePowerSettings.get_value("danger_interrupt_min_score", 80))
    except Exception:
        pass

    focus = {int(x) for x in focus_ids if int(x or 0) > 0}
    live: list[tuple[int, int, str, int, str]] = []
    try:
        enemy_ids = AgentArray.GetEnemyArray() or []
    except Exception:
        enemy_ids = []

    for enemy_id in enemy_ids:
        enemy_id = int(enemy_id or 0)
        if enemy_id <= 0:
            continue
        try:
            if not Agent.IsValid(enemy_id) or not Agent.IsAlive(enemy_id):
                continue
            if Utils.Distance(origin_xy, Agent.GetXY(enemy_id)) > float(range_value):
                continue
            if Agent.IsKnockedDown(enemy_id):
                continue
        except Exception:
            continue
        enemy_skill_id = int(get_casting_skill_id(enemy_id) or 0)
        if enemy_skill_id <= 0:
            continue

        base_priority = 0
        category = "unknown"
        if DSP is not None:
            try:
                base_priority = int(DSP.get_base_score(enemy_skill_id, 0))
                category = str(DSP.get_category(enemy_skill_id, "unknown"))
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatSense, ThreatMemory
                    enemy_ctx = CombatSense.get_enemy_sense(enemy_id, range_value=float(range_value))
                    base_priority = int(DSP.contextual_score(
                        enemy_skill_id,
                        adjacent_enemies=int(enemy_ctx.adjacent_count) if enemy_ctx is not None else 1,
                        enemy_low_health=bool(enemy_ctx is not None and float(enemy_ctx.health) <= 0.35),
                        threat_memory_bonus=ThreatMemory.get_interrupt_bonus(enemy_id),
                    ))
                except Exception:
                    pass
            except Exception:
                base_priority = 0

        is_registered_danger = bool(
            enemy_skill_id in DANGER_INTERRUPT_SKILL_IDS
            and int(base_priority or 0) >= int(min_score)
        )
        cleanup_support = bool(
            cleanup_mode
            and _opportunistic_support_profession(enemy_id)
            and _opportunistic_skill_is_spell_or_chant(enemy_skill_id)
        )
        if not is_registered_danger and not cleanup_support:
            continue
        mode = "cleanup_support" if cleanup_support else "dangerous"
        live.append((enemy_id, enemy_skill_id, mode, int(base_priority), category))

    if not live:
        return ()

    power_drain_id = int(Skill.GetID("Power_Drain") or 0)
    complicate_id = int(Skill.GetID("Complicate") or 0)
    cry_id = int(Skill.GetID("Cry_of_Frustration") or 0)
    pi_id = int(_PSYCHIC_INSTABILITY_ID)
    soj_id = int(Skill.GetID("Signet_of_Judgment") or 0)

    support_categories = {
        "resurrection", "hard_protection", "protection", "party_heal",
        "major_heal", "heal_cleanse", "hard_shutdown", "shutdown",
    }
    out: list[tuple[float, int, int, int, str]] = []
    safe_energy = max(0.0, min(1.0, float(energy_ratio)))

    for target_id, enemy_skill_id, mode, base_priority, category in live:
        adjacent_count, casting_near = _opportunistic_local_counts(
            target_id, float(Range.Adjacent.value)
        )
        remaining_ms = _enemy_cast_remaining_ms(int(target_id), int(enemy_skill_id))
        # Cleanup healer first. Registered dangerous Monk/Rit casts receive the
        # same boost, so cleanup does not ignore a healer merely because its
        # spell is already in the central danger table.
        cleanup_bonus = 22000.0 if mode == "cleanup_support" else 0.0
        focus_bonus = 260.0 if target_id in focus else 0.0

        for our_skill_id in ready_ids:
            if our_skill_id == power_drain_id and not _opportunistic_skill_is_spell_or_chant(enemy_skill_id):
                continue
            if not _opportunistic_interrupt_is_feasible(
                target_id, our_skill_id, int(origin_agent_id or 0)
            ):
                continue

            base = get_interrupt_candidate_score(target_id, enemy_skill_id, our_skill_id)
            if base_priority <= 0 and mode == "cleanup_support":
                base = max(base, 82000.0)

            fit = 0.0
            if our_skill_id == complicate_id:
                fit += 3800.0 if category in support_categories else 1900.0
            elif our_skill_id == power_drain_id:
                fit += 2300.0 + (1.0 - safe_energy) * 2100.0
            elif our_skill_id == cry_id:
                fit += 950.0 + float(max(0, casting_near - 1)) * 1750.0
                if casting_near >= 2:
                    fit += 900.0
            elif our_skill_id == pi_id:
                # PI is the expensive AoE-control answer, not the default
                # single-caster interrupt. Keep it available when it is the
                # only ready answer, but make the fast Mesmer interrupts win
                # ordinary short/single-target windows. Dense packets still
                # give PI enough value to become the best option naturally.
                fit -= 1800.0 if adjacent_count <= 1 else (700.0 if adjacent_count == 2 else 0.0)
                fit += float(max(0, adjacent_count - 1)) * 1050.0
                fit += float(max(0, casting_near - 1)) * 550.0
                if adjacent_count >= 3:
                    fit += 900.0
                if remaining_ms is not None:
                    if int(remaining_ms) < 1400:
                        fit -= 1500.0
                    elif int(remaining_ms) >= 1800:
                        fit += 450.0
            elif our_skill_id == soj_id:
                fit += 250.0 + float(max(0, adjacent_count - 1)) * 180.0
            else:
                fit += 400.0

            out.append((
                float(base + cleanup_bonus + focus_bonus + fit),
                int(target_id), int(enemy_skill_id), int(our_skill_id), str(mode),
            ))

    out.sort(key=lambda item: (-float(item[0]), int(item[1]), int(item[2]), int(item[3])))
    return tuple(out)


def _rank_candidates_for_interrupter(
    candidates: tuple[tuple[int, int], ...],
    interrupter_skill_id: int,
) -> tuple[tuple[int, int], ...]:
    ranked = list(candidates)
    ranked.sort(
        key=lambda item: _candidate_interrupt_score(
            int(item[0]), int(item[1]), int(interrupter_skill_id or 0)
        ),
        reverse=True,
    )
    return tuple(ranked)


def claim_best_dangerous_cast(
    *,
    range_value: int = Range.Spellcast.value,
    interrupter_skill_id: int = 0,
    validator=None,
) -> tuple[int, int]:
    """Pick and reserve the best dangerous cast for this one interrupt attempt."""
    candidates = _rank_candidates_for_interrupter(
        _scan_dangerous_casts(int(range_value)), int(interrupter_skill_id or 0)
    )
    for target_agent_id, casting_skill_id in candidates:
        try:
            try:
                from Py4GWCoreLib.Builds.Skills import DangerousSkillPriorities as DSP
                from Py4GWCoreLib.Builds.Skills import SimplePowerSettings, ThreatMemory, CombatSense
                enemy_ctx = CombatSense.get_enemy_sense(int(target_agent_id), range_value=Range.Spellcast.value)
                final_priority = DSP.contextual_score(
                    int(casting_skill_id),
                    adjacent_enemies=int(enemy_ctx.adjacent_count) if enemy_ctx is not None else 1,
                    enemy_low_health=bool(enemy_ctx is not None and float(enemy_ctx.health) <= 0.35),
                    threat_memory_bonus=ThreatMemory.get_interrupt_bonus(int(target_agent_id)),
                )
                min_score = int(SimplePowerSettings.get_value("danger_interrupt_min_score", 80))
                if int(final_priority) < int(min_score):
                    continue
            except Exception:
                pass
            # Do not reserve an interrupt for a cast already cancelled by the
            # team's SoJ/Keystone knockdown chain.  Reforged can briefly retain
            # the old casting state for one frame after the knockdown lands.
            if _target_is_knocked_down(int(target_agent_id)):
                try:
                    from Py4GWCoreLib.Builds.Skills import Telemetry
                    Telemetry.count("interrupt.knockdown_suppressed")
                except Exception:
                    pass
                continue
            if _is_interrupt_claim_blocked(target_agent_id, casting_skill_id):
                try:
                    from Py4GWCoreLib.Builds.Skills import Telemetry
                    Telemetry.count("interrupt.claim_blocked")
                except Exception:
                    pass
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "INTERRUPT_SKIPPED", reason="already_claimed",
                        caster_id=int(target_agent_id), enemy_skill_id=int(casting_skill_id),
                        our_skill_id=int(interrupter_skill_id or 0),
                    )
                except Exception:
                    pass
                continue
            if validator is not None and not bool(validator(target_agent_id, casting_skill_id)):
                continue
            if int(interrupter_skill_id or 0) > 0 and not _reforged_interrupt_is_feasible(
                int(target_agent_id), int(interrupter_skill_id)
            ):
                try:
                    from Py4GWCoreLib.Builds.Skills import Telemetry
                    Telemetry.count("interrupt.infeasible_skipped")
                except Exception:
                    pass
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "INTERRUPT_SKIPPED", reason="not_feasible",
                        caster_id=int(target_agent_id), enemy_skill_id=int(casting_skill_id),
                        our_skill_id=int(interrupter_skill_id or 0),
                    )
                except Exception:
                    pass
                continue
            # Re-read immediately before posting. If the enemy changed casts, skip.
            if get_casting_skill_id(target_agent_id) != int(casting_skill_id):
                continue
            if not post_interrupt_claim(target_agent_id, casting_skill_id, interrupter_skill_id):
                continue
            try:
                from Py4GWCoreLib.Builds.Skills import Telemetry
                Telemetry.count("interrupt.claim_posted")
                Telemetry.count(f"interrupt.tier_{_danger_tier(int(casting_skill_id))}")
                Telemetry.event("interrupt_claim", str(int(casting_skill_id)))
            except Exception:
                pass
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.mark_interrupt_claim(
                    int(target_agent_id), int(casting_skill_id), int(interrupter_skill_id or 0)
                )
            except Exception:
                pass
            return (int(target_agent_id), int(casting_skill_id))
        except Exception:
            continue
    try:
        from Py4GWCoreLib.Builds.Skills import Telemetry
        Telemetry.count("interrupt.no_candidate")
    except Exception:
        pass
    return (0, 0)
