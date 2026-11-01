from dataclasses import dataclass
from typing import TypedDict


class ProtectionStatus(TypedDict):
    reason: str
    spirits: tuple[int, ...]
    threatened: tuple[int, ...]
    uncovered: tuple[int, ...]
    lowest_hp: float
    direct_pressure: bool
    anchor_covered: bool


from Py4GWCoreLib import Agent, Player, Profession, Routines, BuildMgr, AgentArray, GLOBAL_CACHE, Range, SpiritModelID
from Py4GWCoreLib.Skill import Skill
from Py4GWCoreLib.Builds.Any.HeroAI import HeroAI as HeroAIBuild
from Py4GWCoreLib.Builds.Skills import HexRemovalPriority, SkillsTemplate
from Py4GWCoreLib.Builds.Skills.AoEDangerPrediction import (
    avoid_active_aoe_if_needed,
    get_player_active_aoe_context,
    is_aoe_escape_safe_hold_active,
    is_position_in_active_aoe,
    refresh_aoe_danger_zones,
)
from Py4GWCoreLib.Builds.Skills.BindingChainsCoordination import (
    packet_has_binding_chains,
    register_binding_chains_fired,
    release_binding_chains_reservation,
    reserve_binding_chains,
)


Soul_Twisting_ID = Skill.GetID("Soul_Twisting")
Boon_of_Creation_ID = Skill.GetID("Boon_of_Creation")
Shelter_ID = Skill.GetID("Shelter")
Union_ID = Skill.GetID("Union")
Displacement_ID = Skill.GetID("Displacement")
Earthbind_ID = Skill.GetID("Earthbind")  # legacy bar safety only; no active cast
Dulled_Weapon_ID = Skill.GetID("Dulled_Weapon")
Edge_of_Extinction_ID = Skill.GetID("Edge_of_Extinction")
Winter_ID = Skill.GetID("Winter")
Binding_Chains_ID = Skill.GetID("Binding_Chains")
Spirit_Siphon_ID = Skill.GetID("Spirit_Siphon")
Vital_Weapon_ID = Skill.GetID("Vital_Weapon")
Spirit_Light_ID = Skill.GetID("Spirit_Light")
Keystone_Signet_ID = Skill.GetID("Keystone_Signet")
Signet_of_Judgment_ID = Skill.GetID("Signet_of_Judgment")
Splinter_Weapon_ID = Skill.GetID("Splinter_Weapon")
Summon_Spirits_kurzick_ID = Skill.GetID("Summon_Spirits_kurzick")
Summon_Spirits_luxon_ID = Skill.GetID("Summon_Spirits_luxon")
Armor_of_Unfeeling_ID = Skill.GetID("Armor_of_Unfeeling")
Spirits_Gift_ID = Skill.GetID("Spirits_Gift")
Explosive_Growth_ID = Skill.GetID("Explosive_Growth")
Breath_of_the_Great_Dwarf_ID = Skill.GetID("Breath_of_the_Great_Dwarf")
Ebon_Vanguard_Assassin_Support_ID = Skill.GetID("Ebon_Vanguard_Assassin_Support")
Ebon_Battle_Standard_of_Wisdom_ID = Skill.GetID("Ebon_Battle_Standard_of_Wisdom")
I_Am_Unstoppable_ID = Skill.GetID("I_Am_Unstoppable")
Air_of_Superiority_ID = Skill.GetID("Air_of_Superiority")
Remove_Hex_ID = Skill.GetID("Remove_Hex")

# Read-only view of the RoJ team's active five-second cluster leases. Keeping
# the shared numeric key here avoids importing the Monk build (whose filename
# contains spaces) and does not create a dependency on any Keystone build.
ROJ_CLUSTER_COVERAGE_LOCK_ID = 0x524F4A43
ROJ_BINDING_OPENING_MAX_COVERAGE = 2

# EXP36 Stage 11 / Astra E15.  These thresholds are deliberately conservative:
# healthy, correctly placed spirits keep the EXP35/Stage10 reuse path.  E15 only
# overrides reuse for an actual in-combat coverage gap or a critically damaged
# protective spirit that is still under direct enemy pressure.
E15_THREATENED_ALLY_HP = 0.78
E15_CRITICAL_SPIRIT_HP = 0.22
E15_SOUL_TWISTING_URGENT_REFRESH_MS = 6500
E15_DIRECT_CORE_ST_TRANSIENT_WAIT_MS = 1600
# Stage 2.01: the live Stage 2.00 rebase still showed raw Union casts only
# 266-360 ms before Soul Twisting became usable.  Give only plausible
# near-ready transient ST snapshots one short, non-blocking second chance.
E15_DIRECT_CORE_SECOND_CHANCE_MS = 650
# Stage 2.42: the 2.41 live run missed a Displacement reservation at ~2489 ms
# and that spirit was then repeatedly overtaken.  The refresh reservation is
# non-blocking, so widen only the *ownership* horizon; the 650 ms hard hold is
# unchanged.
E15_DIRECT_CORE_SECOND_CHANCE_ARM_MAX_RECHARGE_MS = 3200
# Stage 2.37: the 650 ms second-chance remains the short hard hold, but a
# separate Soul-Twisting refresh reservation survives until the observed ST
# recharge should actually be ready.  This reservation does *not* own the cast
# lane; it only prevents another core spirit from stealing the next ST refresh.
E15_ST_REFRESH_RESERVATION_GRACE_MS = 900
E15_ST_REFRESH_RESERVATION_MAX_MS = (
    E15_DIRECT_CORE_SECOND_CHANCE_ARM_MAX_RECHARGE_MS
    + E15_ST_REFRESH_RESERVATION_GRACE_MS
)
# Stage 2.42: once an actionable core gap has survived this long, fixed
# Shelter -> Union -> Displacement ordering yields to the oldest gap.  This is
# not a timer-based forced cast; normal castability/ST hygiene still applies.
E15_CORE_STARVATION_PRIORITY_MS = 9000
# Only when >=3 allies are uncovered under *direct* pressure, act earlier on
# an already-actionable gap. Never force an ST or ritual cast through cooldown,
# never override committed followups, and preserve Shelter in life danger.
E15_CORE_PRESSURE_STARVATION_PRIORITY_MS = 6000
# Stage 2.24: a raw Union/Displacement cast while Soul Twisting is equipped can
# create a 30-60 s native ritual recharge hole. Stage 2.25 extends the same
# recharge hygiene to Shelter whenever Soul Twisting is plausibly returning soon.
# A raw Shelter fallback remains available only for a genuinely long/unknown ST
# outage; it must not fire during the near-ready race seen in live Stage 2.24 logs.
E15_RAW_CORE_SHELTER_ONLY_WHILE_ST_EQUIPPED = True
E15_RAW_SHELTER_WAIT_FOR_ST_MS = 9000
# Stage 2.18: an urgent Soul Twisting refresh is a short transaction.  While
# the requested core ritual is only briefly unavailable, no Winter/Vital/filler
# action may consume the cast lane.  A genuinely long native ritual recharge is
# treated as impossible for this transaction so the ST can service other core
# protection instead of idling for many seconds.
E15_COMMITTED_FOLLOWUP_DEADLINE_MS = 7000
E15_COMMITTED_FOLLOWUP_MAX_RECHARGE_MS = 4500
E15_COMMITTED_FOLLOWUP_HOLD_LOG_MS = 900
E15_COMMITTED_FOLLOWUP_UNKNOWN_MAX_HOLD_MS = 1000
E15_COVERAGE_HEADROOM = 180.0

# Keystone ST Travel/Pre-Pull 2026-10-19: do not plant core spirits on an
# empty travel path.  These distances are measured in Guild Wars game units,
# NOT in minimap pixels.  They intentionally leave several seconds to prepare
# Shelter/Union/Displacement before the player reaches spellcasting range.
ST_PREP_ENEMY_DISTANCE_FROM_ST = 2150.0
ST_PREP_ENEMY_DISTANCE_FROM_LEADER = 2050.0
ST_PREP_MAX_ST_TO_LEADER_DISTANCE = 750.0
ST_ACTIVE_COMBAT_ENEMY_DISTANCE = 2850.0
ST_ACTIVE_COMBAT_MAX_ST_TO_LEADER_DISTANCE = 1200.0
PRE_ENGAGE_CORE_COVERAGE_HEADROOM = 300.0

# EXP36 Stage 20.2: optional Ranger nature-ritual support for large packs.
# Edge of Extinction must never compete with the ST defensive package: it is
# considered only after every equipped core spirit is already covering the
# engagement.  Four living enemies is the deliberate "large group" floor.
EDGE_OF_EXTINCTION_MIN_ENEMIES = 4
EDGE_OF_EXTINCTION_SCAN_CACHE_MS = 350
EDGE_OF_EXTINCTION_MIN_RECAST_HOLD_MS = 30000
EDGE_OF_EXTINCTION_MIN_ENERGY_FRACTION = 0.35
EDGE_OF_EXTINCTION_MIN_CORE_RESERVE = 10.0
EDGE_OF_EXTINCTION_BLOCK_LOG_MS = 2000
ST_CORE_ENERGY_RESERVE_BUFFER = 5.0
ST_CORE_ENERGY_RESERVE_LOG_MS = 1500
# Stage 20.8: read-only live proof that Guild Wars actually consumed EoE.
# Polling is local and throttled; it never touches SharedMemory and never feeds
# back into the gameplay recast/priority state.
EDGE_OF_EXTINCTION_OBSERVER_POLL_MS = 100

# Keystone October 18: optional Dulled Weapon is a 15-energy Communing HEX,
# NOT a weapon spell. It never competes with core protective spirit repair.
DULLED_COVERAGE_GUARD_MS = 14500  # cast-request backoff; never assume permanent debuff
DULLED_MIN_ENERGY_FRAC = 0.55
DULLED_ADDITIONAL_CORE_HEADROOM = 5.0
DULLED_MIN_PACKET_SIZE = 2


@dataclass(slots=True)
class _SoulTwistingSnapshot:
    in_aggro: bool = False
    close_to_aggro: bool = False
    pre_spirit_setup: bool = False
    player_energy_pct: float = 1.0


class Soul_Twisting(BuildMgr):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Soul Twisting - HR Mesmerway Boon Spirits Gift",
            required_primary=Profession.Ritualist,
            template_code="OACjAyiDJTYTWPPOhTQTMT0gUTA",
            # HR Mesmerway contract: keep the proven Shelter/Union core and
            # support Boon of Creation + Spirit's Gift. Edge of Extinction is
            # optional again and uses the proven KeySoJWay large-pack safety path.
            # Earthbind remains outside the intended Mesmerway bar.
            required_skills=[
                Soul_Twisting_ID,
                Shelter_ID,
                Union_ID,
            ],
            optional_skills=[
                Displacement_ID,
                Boon_of_Creation_ID,
                Armor_of_Unfeeling_ID,
                Vital_Weapon_ID,
                Dulled_Weapon_ID,
                Edge_of_Extinction_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAIBuild(standalone_fallback=True))
        self._last_dulled_cast_tick: int = 0
        self._dulled_recent_coverage: dict[int, int] = {}
        self._last_idle_weapon_attack_tick: int = 0
        self._last_idle_weapon_target_id: int = 0
        self._last_vital_weapon_cast_tick: int = 0
        self._last_vital_weapon_target_id: int = 0
        self._last_e15_status_signature: tuple = ()
        self._last_e15_status_log_ms: int = 0
        self._e15_pending_followup_model: int = 0
        self._e15_pending_followup_deadline_ms: int = 0
        self._e15_pending_followup_last_hold_log_ms: int = 0
        # Stage 2.35: an ST refresh and the following Binding Ritual are two
        # distinct phases. Give the ritual one fresh confirmation window after
        # its *actual dispatch* instead of spending the whole verifier deadline
        # while waiting for the ritual to become castable. Rearm at most once so
        # a repeatedly interrupted ritual cannot hold the controller forever.
        self._e15_pending_followup_verify_rearmed: bool = False
        self._e15_gap_started_ms: dict[int, int] = {}
        self._e15_gap_reason: dict[int, str] = {}
        self._e15_gap_peak_threatened: dict[int, int] = {}
        self._e15_gap_peak_uncovered: dict[int, int] = {}
        self._e15_starvation_last_log_ms: int = 0
        self._e15_last_block_log_ms: dict[int, int] = {}
        self._e15_session_key: str = ""
        self._e15_last_core_wait_log_ms: int = 0
        self._e15_direct_core_second_chance_deadline_ms: dict[int, int] = {}
        # Stage 2.37: longer-lived ownership of the *next ST refresh* only.
        # Unlike the 650 ms second-chance this state never hard-holds the whole
        # rotation while ST is still recharging.
        self._e15_st_refresh_reservation_model: int = 0
        self._e15_st_refresh_reservation_deadline_ms: int = 0
        self._e15_st_refresh_reservation_last_log_ms: int = 0
        self._last_edge_of_extinction_cast_tick: int = 0
        self._edge_of_extinction_scan_tick: int = 0
        self._edge_of_extinction_scan_count: int = 0
        self._edge_of_extinction_status_logged: bool = False
        self._edge_of_extinction_last_block_log_tick: int = 0
        self._edge_of_extinction_last_block_reason: str = ""
        self._st_core_energy_last_log_ms: int = 0
        self._st_core_energy_last_block_skill: int = 0
        self._edge_of_extinction_observer_tick: int = 0
        self._edge_of_extinction_recharge_prev: int | None = None
        self._edge_of_extinction_activation_active: bool = False
        self._mesmerway_wisdom_combat_started_ms: int = 0
        self._mesmerway_wisdom_was_in_aggro: bool = False
        self._st_last_travel_setup_phase: str = ""
        self.SetBlockedSkills([
            Soul_Twisting_ID,
            Boon_of_Creation_ID,
            Shelter_ID,
            Union_ID,
            Displacement_ID,
            Earthbind_ID,
            Edge_of_Extinction_ID,
            Winter_ID,
            Binding_Chains_ID,
            Dulled_Weapon_ID,
            Spirit_Siphon_ID,
            Vital_Weapon_ID,
            Summon_Spirits_kurzick_ID,
            Summon_Spirits_luxon_ID,
            Armor_of_Unfeeling_ID,
            Spirits_Gift_ID,
            Explosive_Growth_ID,
            Breath_of_the_Great_Dwarf_ID,
            Ebon_Vanguard_Assassin_Support_ID,
            Ebon_Battle_Standard_of_Wisdom_ID,
            I_Am_Unstoppable_ID,
            Air_of_Superiority_ID,
            Remove_Hex_ID,
        ])
        self.SetSkillCastingFn(self._run_local_skill_logic)
        self.skills: SkillsTemplate = SkillsTemplate(self)

    def _has_enemy_near_position(self, position: tuple[float, float] | None, max_distance: float) -> bool:
        if not position:
            return False

        max_distance_sq = max_distance * max_distance
        px, py = position

        for enemy_id in AgentArray.GetEnemyArray() or []:
            if not Agent.IsAlive(enemy_id):
                continue

            enemy_pos = Agent.GetXY(enemy_id)
            if not enemy_pos:
                continue

            dx = px - enemy_pos[0]
            dy = py - enemy_pos[1]
            if (dx * dx + dy * dy) <= max_distance_sq:
                return True

        return False

    def _explosive_growth_has_damage_target(self) -> bool:
        """True only when a newly created spirit can plausibly hit a foe.

        Explosive Growth deals damage around the creature at creation time.
        Binding Ritual spirits are created at the Ritualist, so a backline ST
        should not spend cast time on the enchantment unless a living foe has
        already reached Nearby range of the ST's normal creation point.
        """
        player_id = int(Player.GetAgentID() or 0)
        if player_id <= 0:
            return False
        return bool(self._has_enemy_near_position(Agent.GetXY(player_id), Range.Nearby.value))

    def _normal_core_spirit_creation_due(self, snapshot: _SoulTwistingSnapshot) -> bool:
        """Whether the existing ST policy is already going to create a core spirit.

        This helper is intentionally read-only.  Explosive Growth must never
        make the ST walk forward, replace a healthy spirit early, or create an
        extra spirit just to deal damage.  It may only prepare for a normal
        Shelter/Union/Displacement creation that the existing protection logic
        would perform anyway.
        """
        coverage_headroom = (
            PRE_ENGAGE_CORE_COVERAGE_HEADROOM
            if snapshot.pre_spirit_setup and not snapshot.in_aggro
            else E15_COVERAGE_HEADROOM
        )
        for model, skill_id in (
            (SpiritModelID.SHELTER, Shelter_ID),
            (SpiritModelID.UNION, Union_ID),
            (SpiritModelID.DISPLACEMENT, Displacement_ID),
        ):
            if not self.IsSkillEquipped(skill_id):
                continue
            if not self._core_spirit_effect_still_covers(
                model,
                coverage_headroom=coverage_headroom,
            ):
                return True
        return False

    def _nearest_enemy_distance_to_position(self, position: tuple[float, float] | None) -> float:
        """Return distance to the nearest living enemy, or a very large value.

        Summon Spirits teleports the protection package onto the Ritualist.  This
        helper is therefore used as a hard backline safety gate: a valid spirit
        package must never be dragged onto a Ritualist who is standing in the
        enemy front line.
        """
        if not position:
            return 99999.0
        px, py = position
        nearest = 99999.0
        for enemy_id in AgentArray.GetEnemyArray() or []:
            try:
                if not Agent.IsAlive(enemy_id):
                    continue
                enemy_pos = Agent.GetXY(enemy_id)
                if not enemy_pos:
                    continue
                dx = float(px) - float(enemy_pos[0])
                dy = float(py) - float(enemy_pos[1])
                distance = (dx * dx + dy * dy) ** 0.5
                if distance < nearest:
                    nearest = distance
            except Exception:
                continue
        return float(nearest)

    def _summon_spirits_backline_safe(self, *, reason: str) -> bool:
        """Only allow a spirit pull when the ST is clearly behind the front.

        We deliberately use a little more than Earshot-like spacing.  If an enemy
        is closer than this, leaving Shelter/Union/Displacement planted behind the
        team is safer than teleporting them into melee/AoE range.
        """
        min_enemy_distance = 1100.0
        nearest = self._nearest_enemy_distance_to_position(Player.GetXY())
        if nearest >= min_enemy_distance:
            return True
        self._log_st_aoe_event(
            "ST_SPIRIT_PULL_BLOCKED_FRONTLINE",
            reason=str(reason),
            nearest_enemy=f"{float(nearest):.1f}",
            required_distance=f"{float(min_enemy_distance):.1f}",
        )
        return False

    _CORE_SPIRIT_MODELS = {
        SpiritModelID.SHELTER,
        SpiritModelID.UNION,
        SpiritModelID.DISPLACEMENT,
        SpiritModelID.EARTHBIND,
    }

    def _summon_spirits_skill_id(self) -> int:
        if self.IsSkillEquipped(Summon_Spirits_kurzick_ID):
            return int(Summon_Spirits_kurzick_ID or 0)
        if self.IsSkillEquipped(Summon_Spirits_luxon_ID):
            return int(Summon_Spirits_luxon_ID or 0)
        return 0

    def _get_owned_core_spirits(self, max_distance: float | None = Range.Spellcast.value) -> list[int]:
        player_agent_id = Player.GetAgentID()
        try:
            spirit_array = AgentArray.GetSpiritPetArray()
            if max_distance is not None:
                spirit_array = AgentArray.Filter.ByDistance(spirit_array, Player.GetXY(), float(max_distance))
            spirit_array = AgentArray.Filter.ByCondition(
                spirit_array,
                lambda agent_id: Agent.IsAlive(agent_id) and Agent.IsSpawned(agent_id),
            )
        except Exception:
            return []

        owned_spirits: list[int] = []
        ownerless_spirits: list[int] = []
        nearby_spirits: list[int] = []
        for spirit_id in spirit_array or []:
            try:
                model_value = Agent.GetPlayerNumber(spirit_id)
                if model_value not in SpiritModelID._value2member_map_:
                    continue
                if SpiritModelID(model_value) not in self._CORE_SPIRIT_MODELS:
                    continue
                nearby_spirits.append(spirit_id)

                owner_id = Agent.GetOwnerID(spirit_id)
                if owner_id == player_agent_id:
                    owned_spirits.append(spirit_id)
                elif owner_id == 0:
                    ownerless_spirits.append(spirit_id)
            except Exception:
                continue

        if owned_spirits:
            return owned_spirits
        if ownerless_spirits:
            return ownerless_spirits
        return nearby_spirits

    def _core_spirit_distance_stats(self) -> tuple[list[int], float, float]:
        """Return all owned core spirits plus nearest/farthest distance to the ST."""
        spirits = self._get_owned_core_spirits(max_distance=None)
        if not spirits:
            return [], 0.0, 0.0
        distances: list[float] = []
        px, py = Player.GetXY()
        for spirit_id in spirits:
            try:
                sx, sy = Agent.GetXY(spirit_id)
                distances.append(((float(px) - float(sx)) ** 2 + (float(py) - float(sy)) ** 2) ** 0.5)
            except Exception:
                continue
        if not distances:
            return spirits, 0.0, 0.0
        return spirits, min(distances), max(distances)

    def _get_owned_core_spirits_by_model(self, model: SpiritModelID) -> list[int]:
        """Return living copies of one ST core spirit model.

        Ownership is preferred.  Some Py4GW builds temporarily expose freshly
        created spirits with owner id 0, so ownerless copies are an accepted
        fallback.  A final nearby-model fallback preserves compatibility with
        older runtimes whose owner field is unavailable.
        """
        player_agent_id = int(Player.GetAgentID() or 0)
        owned: list[int] = []
        ownerless: list[int] = []
        nearby: list[int] = []
        try:
            spirit_array = AgentArray.GetSpiritPetArray() or []
        except Exception:
            return []

        for spirit_id in spirit_array:
            try:
                if not (Agent.IsAlive(spirit_id) and Agent.IsSpawned(spirit_id)):
                    continue
                model_value = Agent.GetPlayerNumber(spirit_id)
                if model_value not in SpiritModelID._value2member_map_:
                    continue
                if SpiritModelID(model_value) != model:
                    continue
                nearby.append(int(spirit_id))
                owner_id = int(Agent.GetOwnerID(spirit_id) or 0)
                if owner_id == player_agent_id:
                    owned.append(int(spirit_id))
                elif owner_id == 0:
                    ownerless.append(int(spirit_id))
            except Exception:
                continue
        if owned:
            return owned
        if ownerless:
            return ownerless
        return nearby

    def _next_engagement_anchor_xy(self) -> tuple[float, float] | None:
        """Best position the current spirit package needs to keep covering.

        Prefer the exact shared TeamCombatFocus packet/cleanup target.  If that
        has not been published yet while travelling, use the nearest upcoming
        enemy around the party leader.  Only then fall back to the leader/ST
        position.  This lets an old spirit package be carried across pulls while
        its *effect radius* still reaches the next fight instead of rebuilding
        merely because the ST walked outside ordinary spellcast range.
        """
        # 1) Exact team focus, including single-target cleanup.
        try:
            from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import get_team_cluster_anchor
            focus_id = int(get_team_cluster_anchor(
                filter_range=Range.Spirit.value,
                minimum_enemies=1,
                consumer_role="st_spirit_coverage_reuse",
            ) or 0)
            if focus_id > 0 and Agent.IsValid(focus_id) and Agent.IsAlive(focus_id):
                xy = Agent.GetXY(focus_id)
                if xy:
                    return xy
        except Exception:
            pass

        # 2) Party leader is the travel/frontline reference.
        leader_xy = None
        try:
            from Py4GWCoreLib.Party import Party
            leader_id = int(Party.GetPartyLeaderID() or 0)
            if leader_id > 0 and Agent.IsValid(leader_id):
                leader_xy = Agent.GetXY(leader_id)
        except Exception:
            leader_xy = None
        if not leader_xy:
            try:
                leader_xy = Player.GetXY()
            except Exception:
                leader_xy = None

        # 3) During approach, project to the nearest upcoming enemy so reuse is
        # allowed only when the existing aura reaches a little beyond that pull.
        if leader_xy:
            lx, ly = leader_xy
            nearest_xy = None
            nearest_dist = 99999.0
            scan_limit = float(Range.Spirit.value) * 1.30
            for enemy_id in AgentArray.GetEnemyArray() or []:
                try:
                    if not Agent.IsAlive(enemy_id):
                        continue
                    exy = Agent.GetXY(enemy_id)
                    if not exy:
                        continue
                    dx = float(lx) - float(exy[0])
                    dy = float(ly) - float(exy[1])
                    dist = (dx * dx + dy * dy) ** 0.5
                    if dist < nearest_dist and dist <= scan_limit:
                        nearest_dist = dist
                        nearest_xy = exy
                except Exception:
                    continue
            if nearest_xy:
                return nearest_xy
            return leader_xy

        try:
            return Player.GetXY()
        except Exception:
            return None

    def _core_spirit_effect_still_covers(
        self,
        model: SpiritModelID,
        *,
        coverage_headroom: float = E15_COVERAGE_HEADROOM,
    ) -> bool:
        """True while an existing spirit can still service the next engagement.

        EXP28 deliberately judges reuse by the spirit's *effect radius*, not by
        ordinary spellcast distance from the Ritualist.  A small headroom margin
        means we rebuild before the next packet actually falls outside the aura,
        but not at every enemy group while the old spirits are still useful.
        """
        spirits = self._get_owned_core_spirits_by_model(model)
        if not spirits:
            return False
        anchor_xy = self._next_engagement_anchor_xy()
        if not anchor_xy:
            return False

        # Keep a little range in reserve for party movement during the pull.
        coverage_limit = max(0.0, float(Range.Spirit.value) - float(coverage_headroom))
        ax, ay = anchor_xy
        for spirit_id in spirits:
            try:
                sxy = Agent.GetXY(spirit_id)
                if not sxy:
                    continue
                dx = float(ax) - float(sxy[0])
                dy = float(ay) - float(sxy[1])
                if (dx * dx + dy * dy) ** 0.5 <= coverage_limit:
                    return True
            except Exception:
                continue
        return False

    def _e15_soul_twisting_remaining_ms(self) -> int:
        try:
            player_id = int(Player.GetAgentID() or 0)
            if player_id <= 0 or not Routines.Checks.Agents.HasEffect(player_id, Soul_Twisting_ID):
                return 0
            return int(GLOBAL_CACHE.Effects.GetEffectTimeRemaining(player_id, Soul_Twisting_ID) or 0)
        except Exception:
            return 0

    @staticmethod
    def _e15_distance(a: tuple[float, float] | None, b: tuple[float, float] | None) -> float:
        if not a or not b:
            return 999999.0
        try:
            dx = float(a[0]) - float(b[0])
            dy = float(a[1]) - float(b[1])
            return (dx * dx + dy * dy) ** 0.5
        except Exception:
            return 999999.0

    def _e15_threatened_party_ids(self) -> list[int]:
        """Party members a newly planted core spirit could meaningfully protect.

        Split allies outside the ST's own Spirit-radius are deliberately ignored:
        replacing a spirit at the ST cannot protect them and would only waste the
        existing package.  Within that solvable radius, current low health or
        immediate nearby enemy pressure is enough to mark a member threatened.
        """
        try:
            st_xy = Player.GetXY()
        except Exception:
            st_xy = None
        if not st_xy:
            return []
        solvable_range = max(0.0, float(Range.Spirit.value) - float(E15_COVERAGE_HEADROOM))
        threatened: list[int] = []
        for aid in self._real_party_member_ids():
            try:
                if not Agent.IsValid(int(aid)) or not Agent.IsAlive(int(aid)):
                    continue
                ally_xy = Agent.GetXY(int(aid))
                if self._e15_distance(st_xy, ally_xy) > solvable_range:
                    continue
                hp = float(Agent.GetHealth(int(aid)) or 0.0)
                immediate_pressure = self._has_enemy_near_position(ally_xy, float(Range.Area.value))
                if hp <= float(E15_THREATENED_ALLY_HP) or immediate_pressure:
                    threatened.append(int(aid))
            except Exception as exc:
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_guarded_exception(
                        "st_e15_threatened_party_scan",
                        exc,
                        ally_id=int(aid or 0),
                    )
                except Exception:
                    pass
                continue
        return sorted(set(threatened))

    def _e15_core_protection_status(self, model: SpiritModelID) -> ProtectionStatus:
        """Return only evidence strong enough to override normal spirit reuse.

        Earthbind is intentionally excluded by the caller: it is useful control,
        but it is not silently treated as Shelter/Union/Displacement protection.
        Spirit lifetime is not guessed because the runtime does not expose a
        trustworthy remaining-duration value here; E15 uses observable HP,
        position, pressure, and Soul Twisting duration only.
        """
        spirits = self._get_owned_core_spirits_by_model(model)
        threatened = self._e15_threatened_party_ids()
        coverage_limit = max(0.0, float(Range.Spirit.value) - float(E15_COVERAGE_HEADROOM))
        uncovered: list[int] = []
        for aid in threatened:
            try:
                ally_xy = Agent.GetXY(int(aid))
            except Exception as exc:
                ally_xy = None
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_guarded_exception(
                        "st_e15_coverage_ally_xy",
                        exc,
                        ally_id=int(aid or 0),
                    )
                except Exception:
                    pass
            covered = False
            for sid in spirits:
                try:
                    if self._e15_distance(Agent.GetXY(int(sid)), ally_xy) <= coverage_limit:
                        covered = True
                        break
                except Exception as exc:
                    try:
                        from Py4GWCoreLib.Builds.Skills import CombatDebug
                        CombatDebug.log_guarded_exception(
                            "st_e15_coverage_spirit_xy",
                            exc,
                            spirit_id=int(sid or 0),
                            ally_id=int(aid or 0),
                        )
                    except Exception:
                        pass
                    continue
            if not covered:
                uncovered.append(int(aid))

        health_rows: list[tuple[int, float]] = []
        direct_pressure = False
        for sid in spirits:
            try:
                hp = float(Agent.GetHealth(int(sid)) or 0.0)
                health_rows.append((int(sid), hp))
                sxy = Agent.GetXY(int(sid))
                if self._has_enemy_near_position(sxy, float(Range.Area.value)):
                    direct_pressure = True
            except Exception:
                continue
        lowest_hp = min((hp for _sid, hp in health_rows), default=0.0)
        in_combat = bool(self.IsInAggro())
        normal_anchor_covered = bool(self._core_spirit_effect_still_covers(model))

        reason = ""
        if in_combat and not spirits:
            reason = "missing_core_spirit_under_pressure"
        elif uncovered:
            reason = "threatened_party_member_uncovered"
        elif (
            spirits
            and direct_pressure
            and float(lowest_hp) <= float(E15_CRITICAL_SPIRIT_HP)
            and (in_combat or bool(threatened))
        ):
            reason = "critical_spirit_hp_under_direct_pressure"
        elif in_combat and not normal_anchor_covered:
            reason = "engagement_outside_core_aura"

        return {
            "reason": str(reason),
            "spirits": tuple(int(sid) for sid in spirits),
            "threatened": tuple(int(aid) for aid in threatened),
            "uncovered": tuple(int(aid) for aid in uncovered),
            "lowest_hp": float(lowest_hp),
            "direct_pressure": bool(direct_pressure),
            "anchor_covered": bool(normal_anchor_covered),
        }

    @staticmethod
    def _e15_status_actionable(status: ProtectionStatus) -> bool:
        """Stage 12 / E15b: geometry alone is observation, not an urgent cast.

        Live Stage 11 showed two Soul Twisting refreshes with a healthy spirit,
        threatened_count=0 and uncovered_count=0 purely because the engagement
        anchor moved outside the old aura.  Preserve that signal for diagnostics,
        but only spend cast time when party protection is actually missing or a
        core spirit is critically endangered under direct pressure.
        """
        reason = str(status.get("reason", "") or "")
        threatened = tuple(status.get("threatened", ()) or ())
        uncovered = tuple(status.get("uncovered", ()) or ())
        if uncovered:
            return True
        if reason == "missing_core_spirit_under_pressure":
            return True
        if (
            reason == "critical_spirit_hp_under_direct_pressure"
            and bool(status.get("direct_pressure", False))
            and float(status.get("lowest_hp", 1.0)) <= float(E15_CRITICAL_SPIRIT_HP)
        ):
            return True
        return False

    def _e15_poll_pending_followup(self, model: SpiritModelID, status: ProtectionStatus) -> None:
        """Diagnostic-only proof that an urgent ST refresh was followed by protection."""
        pending = int(self._e15_pending_followup_model or 0)
        if pending <= 0 or pending != int(getattr(model, "value", model)):
            return
        now = int(self._game_tick() or 0)
        if now > int(self._e15_pending_followup_deadline_ms or 0):
            self._log_st_aoe_event(
                "ST_E15_FOLLOWUP_EXPIRED",
                model=int(pending),
                policy="exp36_stage12_e15b_observation_only_followup",
            )
            self._e15_pending_followup_model = 0
            self._e15_pending_followup_deadline_ms = 0
            return
        if tuple(status.get("spirits", ()) or ()) and not self._e15_status_actionable(status):
            self._log_st_aoe_event(
                "ST_E15_FOLLOWUP_CONFIRMED",
                model=int(pending),
                spirit_count=len(status.get("spirits", ()) or ()),
                policy="exp36_stage12_e15b_observation_only_followup",
            )
            self._e15_close_gap_episode_on_confirmed_core(
                model, spirit_count=len(status.get("spirits", ()) or ()), source="followup_poll"
            )
            self._e15_pending_followup_model = 0
            self._e15_pending_followup_deadline_ms = 0

    def _e15_service_pending_followup_commit(self):
        """Stage 2.18: make an urgent Soul Twisting refresh a hard short transaction.

        After Soul Twisting was spent for Shelter/Union/Displacement, that exact
        core ritual owns the next cast lane.  Temporary recharge/aftercast races
        *hold* the controller instead of allowing Winter, Vital Weapon, Siphon or
        filler to interleave.  If the ritual is on a genuinely long native
        recharge, the transaction is explicitly declared impossible and released
        so other defensive core work can continue.
        """
        if False:
            yield
        pending = int(self._e15_pending_followup_model or 0)
        if pending <= 0:
            return False
        now = int(self._game_tick() or 0)
        deadline = int(self._e15_pending_followup_deadline_ms or 0)
        if now > deadline:
            self._log_st_aoe_event(
                "ST_E15_FOLLOWUP_EXPIRED", model=int(pending),
                policy="stage2_18_hard_committed_followup_deadline",
            )
            self._e15_pending_followup_model = 0
            self._e15_pending_followup_deadline_ms = 0
            return False
        mapping = {
            int(getattr(SpiritModelID.SHELTER, "value", SpiritModelID.SHELTER)): (SpiritModelID.SHELTER, int(Shelter_ID), "Shelter"),
            int(getattr(SpiritModelID.UNION, "value", SpiritModelID.UNION)): (SpiritModelID.UNION, int(Union_ID), "Union"),
            int(getattr(SpiritModelID.DISPLACEMENT, "value", SpiritModelID.DISPLACEMENT)): (SpiritModelID.DISPLACEMENT, int(Displacement_ID), "Displacement"),
        }
        row = mapping.get(int(pending))
        if row is None:
            self._e15_pending_followup_model = 0
            self._e15_pending_followup_deadline_ms = 0
            return False
        model, skill_id, label = row
        if not self.IsSkillEquipped(skill_id):
            self._log_st_aoe_event(
                "ST_E15_FOLLOWUP_IMPOSSIBLE", protection_skill=str(label), model=int(pending),
                reason="skill_not_equipped", policy="stage2_18_fail_open_only_when_exact_core_is_impossible",
            )
            self._e15_pending_followup_model = 0
            self._e15_pending_followup_deadline_ms = 0
            return False

        status = self._e15_core_protection_status(model)
        if tuple(status.get("spirits", ()) or ()) and not self._e15_status_actionable(status):
            self._log_st_aoe_event(
                "ST_E15_FOLLOWUP_CONFIRMED", model=int(pending),
                spirit_count=len(status.get("spirits", ()) or ()),
                policy="stage2_18_hard_committed_followup_already_satisfied",
            )
            self._e15_close_gap_episode_on_confirmed_core(
                model, spirit_count=len(status.get("spirits", ()) or ()), source="committed_followup"
            )
            self._e15_pending_followup_model = 0
            self._e15_pending_followup_deadline_ms = 0
            return False

        allow_existing_spirit = bool(tuple(status.get("uncovered", ()) or ())) or (
            status.get("reason") == "critical_spirit_hp_under_direct_pressure" and self._e15_status_actionable(status)
        )
        can_cast = bool(self.CanCastSkillID(skill_id, allow_existing_spirit=allow_existing_spirit))
        reject_reason = str(self.GetLastCastRejectReason() or "") if not can_cast else ""
        recharge_ms = int(self._e15_skill_recharge_ms(skill_id))
        deadline_remaining = max(0, int(deadline - now))

        if not can_cast:
            # Stage 2.19 / Astra P1-D: classify the blocker.  Only short local
            # cast/aftercast or near-ready recharge races may own the lane.  A
            # disabled/invalid skill, long recharge or insufficient energy must
            # release this exact transaction so another urgent core repair (or
            # the existing energy helper) can run.
            permanent_rejects = {
                "map_not_explorable", "extra_condition_false", "skill_not_on_bar",
                "shared_skill_toggle_disabled", "weapon_requirement_failed",
                "shared_condition_failed", "target_validation_failed",
            }
            energy_reject = reject_reason == "insufficient_energy"
            long_recharge = bool(
                recharge_ms > int(E15_COMMITTED_FOLLOWUP_MAX_RECHARGE_MS)
                or (recharge_ms >= 0 and deadline_remaining > 0 and recharge_ms > deadline_remaining)
            )
            started_ms = int(deadline - int(E15_COMMITTED_FOLLOWUP_DEADLINE_MS))
            hold_age_ms = max(0, int(now - started_ms))
            unknown_too_long = bool(
                reject_reason not in {"local_cast_pending", "skill_not_ready"}
                and reject_reason not in permanent_rejects
                and not energy_reject
                and hold_age_ms >= int(E15_COMMITTED_FOLLOWUP_UNKNOWN_MAX_HOLD_MS)
            )
            if long_recharge or reject_reason in permanent_rejects or energy_reject or unknown_too_long:
                block_class = (
                    "long_recharge" if long_recharge else
                    "energy" if energy_reject else
                    "permanent_reject" if reject_reason in permanent_rejects else
                    "unknown_reject_timeout"
                )
                self._log_st_aoe_event(
                    "ST_E15_FOLLOWUP_DEFERRED", protection_skill=str(label), model=int(pending),
                    block_class=str(block_class), core_skill_recharge_ms=int(recharge_ms),
                    reject_reason=str(reject_reason), deadline_remaining_ms=int(deadline_remaining),
                    policy="stage2_19_p1d_only_transient_short_races_keep_lane",
                )
                self._e15_pending_followup_model = 0
                self._e15_pending_followup_deadline_ms = 0
                return False

            last_log = int(getattr(self, "_e15_pending_followup_last_hold_log_ms", 0) or 0)
            if now - last_log >= int(E15_COMMITTED_FOLLOWUP_HOLD_LOG_MS):
                self._e15_pending_followup_last_hold_log_ms = int(now)
                self._log_st_aoe_event(
                    "ST_E15_FOLLOWUP_HARD_HOLD", protection_skill=str(label), model=int(pending),
                    core_skill_recharge_ms=int(recharge_ms), reject_reason=str(reject_reason),
                    deadline_remaining_ms=int(deadline_remaining),
                    threatened_count=len(status.get("threatened", ()) or ()),
                    uncovered_count=len(status.get("uncovered", ()) or ()),
                    policy="stage2_19_p1d_transient_aftercast_or_near_ready_recharge_only",
                )
            return True

        did = yield from self.CastSkillID(
            skill_id=int(skill_id), target_agent_id=0, log=False, aftercast_delay=250,
            allow_existing_spirit=allow_existing_spirit,
        )
        if did:
            # Stage 2.35: Stage 2.34 could dispatch Displacement near the end of
            # the original ST->core deadline and then expire the transaction
            # before the spirit had any realistic confirmation window (39.3s gap
            # in the live 2.34 run). Rearm once from the actual ritual dispatch.
            if not bool(getattr(self, "_e15_pending_followup_verify_rearmed", False)):
                self._e15_pending_followup_verify_rearmed = True
                # Stage 2.37: `now` was captured before CastSkillID yielded. A
                # Compatibility marker retained for older audit/tests:
                # stage2_35_confirmation_window_starts_at_actual_core_dispatch
                # Binding Ritual can spend seconds inside that generator, so use
                # a fresh game tick *after* dispatch acceptance. Otherwise the
                # promised 7 s confirmation window is silently shortened.
                dispatch_tick_fn = getattr(self, "_game_tick", None)
                dispatch_now = int(dispatch_tick_fn() or 0) if callable(dispatch_tick_fn) else int(now)
                self._e15_pending_followup_deadline_ms = int(dispatch_now) + int(E15_COMMITTED_FOLLOWUP_DEADLINE_MS)
                self._e15_pending_followup_last_hold_log_ms = 0
                self._log_st_aoe_event(
                    "ST_E15_FOLLOWUP_VERIFY_REARMED",
                    protection_skill=str(label), model=int(pending),
                    deadline_ms=int(E15_COMMITTED_FOLLOWUP_DEADLINE_MS),
                    dispatch_tick_ms=int(dispatch_now),
                    policy="stage2_37_confirmation_window_starts_from_fresh_post_dispatch_tick",
                )
            self._log_st_aoe_event(
                "ST_E15_FOLLOWUP_COMMIT",
                protection_skill=str(label), model=int(pending),
                threatened_count=len(status.get("threatened", ()) or ()),
                uncovered_count=len(status.get("uncovered", ()) or ()),
                policy="stage2_37_soul_twisting_then_exact_core_with_fresh_dispatch_verifier",
            )
            return True
        # Dispatch can lose a one-frame race.  Keep ownership until the next
        # scheduler tick rather than letting utility interleave immediately.
        return True

    def _e15_refresh_session_boundary(self) -> None:
        """Drop transient E15 state when the explorable/session identity changes."""
        try:
            from Py4GWCoreLib.Builds.Skills import CombatSession
            snap = CombatSession.refresh()
            key = str(getattr(snap, "key", "") or "")
        except Exception:
            return
        if not key:
            return
        previous = str(self._e15_session_key or "")
        if previous and previous != key:
            had_state = bool(
                self._e15_gap_started_ms
                or int(self._e15_pending_followup_model or 0) > 0
            )
            self._e15_gap_started_ms.clear()
            self._e15_gap_reason.clear()
            self._e15_gap_peak_threatened.clear()
            self._e15_gap_peak_uncovered.clear()
            self._e15_last_block_log_ms.clear()
            second_chance_state = getattr(
                self, "_e15_direct_core_second_chance_deadline_ms", None
            )
            if isinstance(second_chance_state, dict):
                second_chance_state.clear()
            clear_reservation = getattr(self, "_e15_clear_st_refresh_reservation", None)
            if callable(clear_reservation):
                clear_reservation()
            else:
                # Keep extracted-method/legacy objects safe during session reset.
                setattr(self, "_e15_st_refresh_reservation_model", 0)
                setattr(self, "_e15_st_refresh_reservation_deadline_ms", 0)
                setattr(self, "_e15_st_refresh_reservation_last_log_ms", 0)
            self._e15_pending_followup_model = 0
            self._e15_pending_followup_deadline_ms = 0
            self._e15_pending_followup_last_hold_log_ms = 0
            self._e15_pending_followup_verify_rearmed = False
            self._last_e15_status_signature = ()
            self._last_e15_status_log_ms = 0
            if had_state:
                self._log_st_aoe_event(
                    "ST_E15_SESSION_STATE_RESET",
                    previous_session=previous,
                    new_session=key,
                    policy="stage1_67_session_bound_gap_and_followup_state",
                )
        self._e15_session_key = key


    def _e15_close_gap_episode_on_confirmed_core(self, model: SpiritModelID, *, spirit_count: int, source: str) -> None:
        """Close stale diagnostic gap state as soon as the core is proven restored."""
        model_id = int(getattr(model, "value", model))
        started = int(self._e15_gap_started_ms.pop(model_id, 0) or 0)
        if started <= 0:
            return
        now = int(self._game_tick() or 0)
        self._log_st_aoe_event(
            "ST_E15_PROTECTION_GAP_RECOVERED", model=model_id,
            original_reason=str(self._e15_gap_reason.pop(model_id, "") or ""),
            duration_ms=max(0, int(now - started)),
            peak_threatened=int(self._e15_gap_peak_threatened.pop(model_id, 0) or 0),
            peak_uncovered=int(self._e15_gap_peak_uncovered.pop(model_id, 0) or 0),
            spirit_count=int(spirit_count), confirmation_source=str(source),
            policy="stage2_26_followup_confirmation_closes_gap_immediately",
        )

    def _e15_update_gap_episode(self, model: SpiritModelID, status: ProtectionStatus) -> None:
        """Collapse repeated status lines into one actionable gap episode."""
        model_id = int(getattr(model, "value", model))
        now = int(self._game_tick() or 0)
        actionable = bool(self._e15_status_actionable(status))
        reason = str(status.get("reason", "") or "")
        if actionable:
            threatened = len(status.get("threatened", ()) or ())
            uncovered = len(status.get("uncovered", ()) or ())
            if model_id not in self._e15_gap_started_ms:
                self._e15_gap_started_ms[model_id] = int(now)
                self._e15_gap_reason[model_id] = reason
                self._e15_gap_peak_threatened[model_id] = int(threatened)
                self._e15_gap_peak_uncovered[model_id] = int(uncovered)
                self._log_st_aoe_event(
                    "ST_E15_PROTECTION_GAP_START", model=model_id, reason=reason,
                    threatened_count=int(threatened), uncovered_count=int(uncovered),
                    spirit_count=len(status.get("spirits", ()) or ()),
                    policy="stage1_55_actionable_gap_episode_start",
                )
            else:
                self._e15_gap_peak_threatened[model_id] = max(
                    int(self._e15_gap_peak_threatened.get(model_id, 0)), int(threatened)
                )
                self._e15_gap_peak_uncovered[model_id] = max(
                    int(self._e15_gap_peak_uncovered.get(model_id, 0)), int(uncovered)
                )
            return
        started = int(self._e15_gap_started_ms.pop(model_id, 0) or 0)
        if started > 0:
            self._log_st_aoe_event(
                "ST_E15_PROTECTION_GAP_RECOVERED", model=model_id,
                original_reason=str(self._e15_gap_reason.pop(model_id, "") or ""),
                duration_ms=max(0, int(now - started)),
                peak_threatened=int(self._e15_gap_peak_threatened.pop(model_id, 0) or 0),
                peak_uncovered=int(self._e15_gap_peak_uncovered.pop(model_id, 0) or 0),
                spirit_count=len(status.get("spirits", ()) or ()),
                policy="stage1_55_gap_duration_and_recovery",
            )

    def _doa_demonic_miasma_active(self) -> bool:
        """Reuse the existing effect-verified DoA hazard predicate.

        All four DoA sections share Map 474, so a map-only rule is unsafe.
        If the environment effect is not exposed, don't guess a subregion.
        """
        try:
            from Py4GWCoreLib.Builds.Skills.GlobalCombatRules import displacement_is_hazardous
            return bool(displacement_is_hazardous())
        except Exception:
            return False

    def CanCastSkillID(self, skill_id: int, extra_condition=True, *, allow_existing_spirit=False) -> bool:
        """Shared cast gate: covers normal and acute ST Displacement paths."""
        if int(skill_id or 0) == int(Displacement_ID) and self._doa_demonic_miasma_active():
            self._set_cast_reject_reason('doa_demonic_miasma_no_displacement')
            return False
        return super().CanCastSkillID(
            skill_id, extra_condition, allow_existing_spirit=allow_existing_spirit
        )

    def _e15_core_service_plan(self) -> list[tuple[SpiritModelID, int, str]]:
        """Observe all core gaps, then prevent fixed-order starvation.

        Stage 2.41 serviced Shelter -> Union -> Displacement and only observed a
        later spirit when earlier services returned.  Under churn that let a
        long-lived Displacement gap be repeatedly overtaken by fresh Shelter/Union
        gaps.  Stage 2.42 samples all three first.  Normal order is unchanged
        until an actionable gap is at least nine seconds old; then the oldest
        starved core gets first refusal for that controller tick.
        """
        rows: list[tuple[SpiritModelID, int, str]] = [
            (SpiritModelID.SHELTER, int(Shelter_ID), "Shelter"),
            (SpiritModelID.UNION, int(Union_ID), "Union"),
            (SpiritModelID.DISPLACEMENT, int(Displacement_ID), "Displacement"),
        ]
        if self._doa_demonic_miasma_active():
            rows = [row for row in rows if int(row[1]) != int(Displacement_ID)]
        now = int(self._game_tick() or 0)
        observed: list[tuple[SpiritModelID, int, str, ProtectionStatus, int]] = []
        for model, skill_id, label in rows:
            if int(skill_id or 0) <= 0 or not self.IsSkillEquipped(int(skill_id)):
                continue
            try:
                status = self._e15_core_protection_status(model)
            except Exception:
                continue
            self._e15_update_gap_episode(model, status)
            model_id = int(getattr(model, "value", model))
            started = int(self._e15_gap_started_ms.get(model_id, 0) or 0)
            age = max(0, int(now - started)) if started > 0 and now > 0 else 0
            observed.append((model, int(skill_id), str(label), status, int(age)))

        def gap_priority_threshold(row) -> int:
            status = row[3]
            uncovered = len(status.get("uncovered", ()) or ())
            if (uncovered >= 3 and bool(status.get("direct_pressure", False))
                    and len(status.get("threatened", ()) or ()) > 0):
                return int(E15_CORE_PRESSURE_STARVATION_PRIORITY_MS)
            return int(E15_CORE_STARVATION_PRIORITY_MS)

        starving = [
            row for row in observed
            if int(row[4]) >= gap_priority_threshold(row)
            and self._e15_status_actionable(row[3])
        ]
        # A critically uncovered Shelter should not spend another several
        # seconds behind a longer but less immediately lethal Displacement gap.
        # This is only a *priority order*: it never bypasses an unavailable
        # skill, Soul Twisting ownership, cast-state or committed followup.
        shelter_id = int(getattr(SpiritModelID.SHELTER, "value", SpiritModelID.SHELTER))
        acute_shelter = [
            row for row in observed
            if int(getattr(row[0], 'value', row[0])) == shelter_id
            and int(row[4]) >= 2500
            and len(row[3].get('uncovered', ()) or ()) >= 3
            and bool(row[3].get('direct_pressure', False))
            and float(row[3].get('lowest_hp', 1.0) or 1.0) <= 0.67
            and self._e15_status_actionable(row[3])
        ]
        if acute_shelter:
            urgent = acute_shelter[0]
            if now <= 0 or now - int(getattr(self, '_e15_acute_shelter_last_log_ms', 0) or 0) >= 1500:
                self._e15_acute_shelter_last_log_ms = int(now)
                self._log_st_aoe_event(
                    'ST_E15_ACUTE_SHELTER_PRIORITY', gap_age_ms=int(urgent[4]),
                    uncovered_count=len(urgent[3].get('uncovered', ()) or ()),
                    lowest_hp=round(float(urgent[3].get('lowest_hp', 0) or 0), 3),
                    st_recharge_ms=int(self._e15_skill_recharge_ms(Soul_Twisting_ID)),
                    policy='stage2_56_acute_shelter_priority_only_no_forced_cast',
                )
            return rows  # The established order already starts with Shelter.
        if not starving:
            return rows

        # Oldest gap first. If ages are effectively tied, more uncovered allies
        # wins, then the established Shelter/Union/Displacement order remains the
        # deterministic tie-breaker.
        base_index = {
            int(getattr(SpiritModelID.SHELTER, "value", SpiritModelID.SHELTER)): 0,
            int(getattr(SpiritModelID.UNION, "value", SpiritModelID.UNION)): 1,
            int(getattr(SpiritModelID.DISPLACEMENT, "value", SpiritModelID.DISPLACEMENT)): 2,
        }
        # An old but unavailable ritual should not win every scan over another
        # overdue, *ready* core. This is only a priority hint; the existing
        # CanCastSkillID, ST-charge and followup guards still decide dispatch.
        def ready_to_serve(row) -> bool:
            try:
                sid = int(row[1]); active_st = int(self._e15_soul_twisting_remaining_ms()) > 0
                if not active_st or int(self._e15_skill_recharge_ms(sid)) > 0:
                    return False
                return bool(self.CanCastSkillID(sid, allow_existing_spirit=True))
            except Exception:
                return False

        starving.sort(
            key=lambda row: (
                int(ready_to_serve(row)),
                int(row[4]),
                len(row[3].get("uncovered", ()) or ()),
                -int(base_index.get(int(getattr(row[0], "value", row[0])), 99)),
            ),
            reverse=True,
        )
        winner = starving[0]
        winner_id = int(getattr(winner[0], "value", winner[0]))
        # Stage 2.57 live: Displacement was missing for 37.75 s. There was
        # a ~12.5 s interval between the first ready observation and the next
        # observed renewal; the log does not establish why the controller did
        # not act during that interval. Emit an explicit watchdog observation
        # rather than assuming a cooldown bug or forcing a raw ritual cast.
        if ready_to_serve(winner) and int(winner[4]) >= 9000:
            last = int(getattr(self, "_e15_ready_gap_last_log_ms", 0) or 0)
            if now > 0 and (last <= 0 or now - last >= 2000):
                self._e15_ready_gap_last_log_ms = now
                self._log_st_aoe_event(
                    "ST_E15_READY_GAP_WATCHDOG", model=int(winner_id),
                    gap_age_ms=int(winner[4]),
                    threatened_count=len(winner[3].get("threatened", ()) or ()),
                    uncovered_count=len(winner[3].get("uncovered", ()) or ()),
                    soul_twisting_remaining_ms=int(self._e15_soul_twisting_remaining_ms()),
                    ritual_recharge_ms=int(self._e15_skill_recharge_ms(winner[1])),
                    policy="stage2_58_readiness_and_gap_dispatch_diagnostics_no_forced_cast",
                )
        ordered = [(winner[0], winner[1], winner[2])] + [
            row for row in rows
            if int(getattr(row[0], "value", row[0])) != int(winner_id)
        ]
        if now <= 0 or now - int(getattr(self, "_e15_starvation_last_log_ms", 0) or 0) >= 1500:
            self._e15_starvation_last_log_ms = int(now)
            self._log_st_aoe_event(
                "ST_E15_STARVATION_PRIORITY",
                model=int(winner_id), protection_skill=str(winner[2]),
                gap_age_ms=int(winner[4]),
                reason=str(winner[3].get("reason", "") or ""),
                threatened_count=len(winner[3].get("threatened", ()) or ()),
                uncovered_count=len(winner[3].get("uncovered", ()) or ()),
                threshold_ms=int(gap_priority_threshold(winner)),
                st_recharge_ms=int(self._e15_skill_recharge_ms(Soul_Twisting_ID)),
                ritual_recharge_ms=int(self._e15_skill_recharge_ms(winner[1])),
                policy="stage2_55_pressure_aware_actionable_gap_priority_no_forced_cast",
            )
        return ordered

    @staticmethod
    def _e15_should_defer_direct_core_for_soul_twisting(
        st_recharge_ms: int, st_reject_reason: str
    ) -> bool:
        """Avoid burning a core ritual for a transient ST castability race.

        Stage 1.68 live logs showed raw Displacement/Shelter fallbacks followed
        by a successful Soul Twisting refresh only ~300 ms later. Those raw
        casts then entered long native ritual recharge and created the real
        30+ second protection gaps. Defer only when ST is already ready or
        within the narrow transient window and the rejection is a temporary
        local cast/readiness condition. Genuine longer recharge and non-transient
        failures keep the emergency direct-core path.
        """
        recharge = int(st_recharge_ms)
        reason = str(st_reject_reason or "")
        if recharge < 0 or recharge > int(E15_DIRECT_CORE_ST_TRANSIENT_WAIT_MS):
            return False
        return reason in {"local_cast_pending", "skill_not_ready"}

    @staticmethod
    def _e15_should_arm_direct_core_second_chance(
        st_recharge_ms: int, st_reject_reason: str
    ) -> bool:
        """Arm only for a plausibly near-ready transient Soul Twisting state.

        This is deliberately wider than the old 1200 ms stateless first-refusal
        gate because Stage 2.00 live logs showed a stale ~1350 ms snapshot just
        before ST became usable.  Long recharge, unknown recharge, energy failure
        and hard policy rejects must still fall straight through to emergency
        direct-core protection.
        """
        recharge = int(st_recharge_ms)
        reason = str(st_reject_reason or "")
        if recharge < 0 or recharge > int(E15_DIRECT_CORE_SECOND_CHANCE_ARM_MAX_RECHARGE_MS):
            return False
        return reason in {"local_cast_pending", "skill_not_ready"}

    @staticmethod
    def _e15_pick_armed_second_chance_owner(second_chance_state: dict[int, int], now: int) -> int:
        """Return the oldest still-live ST second-chance owner.

        Stage 2.36: a core spirit that already deferred its raw cast for a
        near-ready Soul Twisting refresh keeps first refusal until that short
        window resolves.  Without this owner lock, the normal Shelter -> Union ->
        Displacement scan can let a newly urgent earlier slot steal the refresh
        on the next scheduler tick and leave the original missing spirit waiting.
        """
        if not isinstance(second_chance_state, dict) or not second_chance_state:
            return 0
        candidates: list[tuple[int, int]] = []
        for raw_model, raw_deadline in second_chance_state.items():
            try:
                model_id = int(raw_model or 0)
                deadline = int(raw_deadline or 0)
            except Exception:
                continue
            if model_id > 0 and deadline > int(now):
                candidates.append((deadline, model_id))
        if not candidates:
            return 0
        candidates.sort(key=lambda row: (int(row[0]), int(row[1])))
        return int(candidates[0][1])

    @staticmethod
    def _e15_st_refresh_reservation_hold_ms(st_recharge_ms: int) -> int:
        """Bound refresh ownership to observed ST readiness plus scheduler grace."""
        recharge = max(0, int(st_recharge_ms))
        hold = max(
            int(E15_DIRECT_CORE_SECOND_CHANCE_MS),
            recharge + int(E15_ST_REFRESH_RESERVATION_GRACE_MS),
        )
        return min(int(E15_ST_REFRESH_RESERVATION_MAX_MS), int(hold))

    def _e15_active_st_refresh_reservation(self, now: int | None = None) -> int:
        """Return the live Stage-2.37 ST-refresh owner, clearing stale state."""
        if now is None:
            now = int(self._game_tick() or 0)
        owner = int(getattr(self, "_e15_st_refresh_reservation_model", 0) or 0)
        deadline = int(getattr(self, "_e15_st_refresh_reservation_deadline_ms", 0) or 0)
        if owner <= 0 or deadline <= int(now):
            self._e15_st_refresh_reservation_model = 0
            self._e15_st_refresh_reservation_deadline_ms = 0
            return 0
        return int(owner)

    def _e15_arm_st_refresh_reservation(
        self, model_id: int, *, now: int, st_recharge_ms: int, label: str
    ) -> bool:
        """Reserve only the next ST refresh; never reserve the whole cast lane."""
        model_id = int(model_id or 0)
        if model_id <= 0:
            return False
        active = int(self._e15_active_st_refresh_reservation(int(now)) or 0)
        if active > 0 and active != model_id:
            return False
        hold_ms = int(self._e15_st_refresh_reservation_hold_ms(st_recharge_ms))
        deadline = int(now) + int(hold_ms)
        is_new = active != model_id
        self._e15_st_refresh_reservation_model = int(model_id)
        self._e15_st_refresh_reservation_deadline_ms = int(deadline)
        if is_new:
            self._log_st_aoe_event(
                "ST_E15_REFRESH_RESERVATION_ARMED",
                protection_skill=str(label), model=int(model_id),
                soul_twisting_recharge_ms=int(st_recharge_ms),
                reservation_ms=int(hold_ms),
                policy="stage2_37_reserve_next_st_refresh_until_observed_readiness",
            )
        return True

    def _e15_clear_st_refresh_reservation(self, model_id: int = 0) -> bool:
        owner = int(getattr(self, "_e15_st_refresh_reservation_model", 0) or 0)
        if owner <= 0 or (int(model_id or 0) > 0 and owner != int(model_id)):
            return False
        self._e15_st_refresh_reservation_model = 0
        self._e15_st_refresh_reservation_deadline_ms = 0
        self._e15_st_refresh_reservation_last_log_ms = 0
        return True

    def _e15_service_st_refresh_reservation(self):
        """Give the reserved core first refusal on ST without idling the rotation."""
        if False:
            yield
        now = int(self._game_tick() or 0)
        raw_owner = int(getattr(self, "_e15_st_refresh_reservation_model", 0) or 0)
        raw_deadline = int(getattr(self, "_e15_st_refresh_reservation_deadline_ms", 0) or 0)
        owner = int(self._e15_active_st_refresh_reservation(now) or 0)
        if owner <= 0:
            if raw_owner > 0 and raw_deadline > 0 and raw_deadline <= now:
                self._log_st_aoe_event(
                    "ST_E15_REFRESH_RESERVATION_EXPIRED", model=int(raw_owner),
                    overdue_ms=max(0, int(now - raw_deadline)),
                    policy="stage2_37_bounded_refresh_reservation_expired_fail_open",
                )
            return False
        mapping = {
            int(getattr(SpiritModelID.SHELTER, "value", SpiritModelID.SHELTER)): (SpiritModelID.SHELTER, int(Shelter_ID), "Shelter"),
            int(getattr(SpiritModelID.UNION, "value", SpiritModelID.UNION)): (SpiritModelID.UNION, int(Union_ID), "Union"),
            int(getattr(SpiritModelID.DISPLACEMENT, "value", SpiritModelID.DISPLACEMENT)): (SpiritModelID.DISPLACEMENT, int(Displacement_ID), "Displacement"),
        }
        row = mapping.get(owner)
        if row is None:
            self._e15_clear_st_refresh_reservation(owner)
            return False
        model, skill_id, label = row
        did = yield from self._e15_service_protective_spirit(model, skill_id, label=label)
        if did:
            deadline = int(getattr(self, "_e15_st_refresh_reservation_deadline_ms", 0) or 0)
            self._log_st_aoe_event(
                "ST_E15_REFRESH_RESERVATION_PRIORITY",
                protection_skill=str(label), model=int(owner),
                remaining_ms=max(0, int(deadline - now)),
                policy="stage2_37_reserved_core_gets_first_refusal_on_next_st_refresh",
            )
            return True

        # The owner is still waiting for ST. Do not return True here: this is the
        # key difference from the short second-chance hold. Other protection,
        # energy support and utility may continue while the ST refresh remains
        # reserved for this core.
        last_log = int(getattr(self, "_e15_st_refresh_reservation_last_log_ms", 0) or 0)
        if now - last_log >= 900:
            self._e15_st_refresh_reservation_last_log_ms = int(now)
            self._log_st_aoe_event(
                "ST_E15_REFRESH_RESERVATION_WAIT",
                protection_skill=str(label), model=int(owner),
                remaining_ms=max(0, int(getattr(self, "_e15_st_refresh_reservation_deadline_ms", 0) or 0) - int(now)),
                soul_twisting_recharge_ms=int(self._e15_skill_recharge_ms(Soul_Twisting_ID)),
                policy="stage2_37_refresh_reserved_but_rotation_not_hard_held",
            )
        return False

    def _e15_service_armed_second_chance_owner(self):
        """Service an already-armed near-ready ST repair before normal slot order."""
        if False:
            yield
        state = getattr(self, "_e15_direct_core_second_chance_deadline_ms", None)
        if not isinstance(state, dict) or not state:
            return False
        now = int(self._game_tick() or 0)
        owner = int(self._e15_pick_armed_second_chance_owner(state, now) or 0)
        if owner <= 0:
            return False
        mapping = {
            int(getattr(SpiritModelID.SHELTER, "value", SpiritModelID.SHELTER)): (SpiritModelID.SHELTER, int(Shelter_ID), "Shelter"),
            int(getattr(SpiritModelID.UNION, "value", SpiritModelID.UNION)): (SpiritModelID.UNION, int(Union_ID), "Union"),
            int(getattr(SpiritModelID.DISPLACEMENT, "value", SpiritModelID.DISPLACEMENT)): (SpiritModelID.DISPLACEMENT, int(Displacement_ID), "Displacement"),
        }
        row = mapping.get(owner)
        if row is None:
            state.pop(owner, None)
            return False
        model, skill_id, label = row
        did = yield from self._e15_service_protective_spirit(model, skill_id, label=label)
        if did:
            self._log_st_aoe_event(
                "ST_E15_SECOND_CHANCE_OWNER_PRIORITY",
                protection_skill=str(label), model=int(owner),
                remaining_ms=max(0, int(state.get(owner, 0) or 0) - int(now)),
                policy="stage2_36_armed_second_chance_keeps_next_soul_twisting_refresh",
            )
            return True
        return False

    def _e15_skill_recharge_ms(self, skill_id: int) -> int:
        """Return live SkillBar remaining recharge, or -1 when unavailable."""
        sid = int(skill_id or 0)
        if sid <= 0 or not self.IsSkillEquipped(sid):
            return -1
        try:
            slot = int(GLOBAL_CACHE.SkillBar.GetSlotBySkillID(sid) or 0)
            if not (1 <= slot <= 8):
                return -1
            data = GLOBAL_CACHE.SkillBar.GetSkillData(slot)
            raw = int(getattr(data, "get_recharge", 0) or 0)
            # Some live SkillBar snapshots expose a signed millisecond value
            # through an unsigned 32-bit field (for example 2^32-87).  That is
            # effectively ready/expired, not a 49-day recharge.
            if 0x80000000 <= raw <= 0xFFFFFFFF:
                raw -= 0x100000000
            return max(0, int(raw))
        except Exception:
            return -1

    def _e15_log_action_blocked(self, model: SpiritModelID, skill_id: int, status: ProtectionStatus, *, stage: str) -> None:
        model_id = int(getattr(model, "value", model))
        now = int(self._game_tick() or 0)
        if now > 0 and now - int(self._e15_last_block_log_ms.get(model_id, 0) or 0) < 1200:
            return
        self._e15_last_block_log_ms[model_id] = int(now)
        player_id = int(Player.GetAgentID() or 0)
        try:
            energy_pct = float(Agent.GetEnergy(player_id) or 0.0)
        except Exception:
            energy_pct = -1.0
        try:
            casting_skill_id = int(Agent.GetCastingSkillID(player_id) or 0)
        except Exception:
            casting_skill_id = 0
        try:
            knocked = bool(Agent.IsKnockedDown(player_id))
        except Exception:
            knocked = False
        st_can = bool(self.IsSkillEquipped(Soul_Twisting_ID) and self.CanCastSkillID(Soul_Twisting_ID))
        st_reject = str(self.GetLastCastRejectReason() or "") if not st_can else ""
        core_can = bool(self.CanCastSkillID(int(skill_id), allow_existing_spirit=True))
        core_reject = str(self.GetLastCastRejectReason() or "") if not core_can else ""
        self._log_st_aoe_event(
            "ST_E15_ACTION_BLOCKED", model=model_id, stage=str(stage),
            reason=str(status.get("reason", "") or ""),
            threatened_count=len(status.get("threatened", ()) or ()),
            uncovered_count=len(status.get("uncovered", ()) or ()),
            spirit_count=len(status.get("spirits", ()) or ()),
            soul_twisting_remaining_ms=int(self._e15_soul_twisting_remaining_ms()),
            soul_twisting_recharge_ms=int(self._e15_skill_recharge_ms(Soul_Twisting_ID)),
            soul_twisting_charge_data_available=False,
            soul_twisting_can_cast=bool(st_can), soul_twisting_reject_reason=st_reject,
            core_skill_id=int(skill_id), core_skill_recharge_ms=int(self._e15_skill_recharge_ms(int(skill_id))),
            core_skill_can_cast=bool(core_can), core_reject_reason=core_reject,
            eoe_equipped=bool(self.IsSkillEquipped(Edge_of_Extinction_ID)),
            eoe_recharge_ms=int(self._e15_skill_recharge_ms(Edge_of_Extinction_ID)),
            winter_equipped=bool(self.IsSkillEquipped(Winter_ID)),
            winter_recharge_ms=int(self._e15_skill_recharge_ms(Winter_ID)),
            eoe_activation_observed=bool(self._edge_of_extinction_activation_active),
            pending_followup_model=int(self._e15_pending_followup_model or 0),
            pending_followup_remaining_ms=max(0, int(self._e15_pending_followup_deadline_ms or 0) - int(now))
            if int(self._e15_pending_followup_model or 0) > 0 else 0,
            player_energy_pct=round(float(energy_pct), 3), player_casting_skill_id=int(casting_skill_id),
            player_knocked_down=bool(knocked),
            diagnostic_revision="stage1_66_st_gap_live_recharge_diagnostics_no_priority_change",
            policy="stage1_55_diagnose_without_relaxing_protection_gates",
        )

    def _e15_log_status_if_needed(self, model: SpiritModelID, status: ProtectionStatus) -> None:
        reason = str(status.get("reason", "") or "")
        if not reason:
            return
        now = int(self._game_tick() or 0)
        signature = (
            int(getattr(model, "value", model)),
            reason,
            tuple(status.get("spirits", ())),
            tuple(status.get("uncovered", ())),
            round(float(status.get("lowest_hp", 0.0) or 0.0), 2),
        )
        # START/RECOVERED and action diagnostics are logged separately and remain
        # lossless. This is only the repeating status heartbeat, so cap it to
        # one line per 5 s even when HP noise changes the signature every tick.
        if now - int(self._last_e15_status_log_ms or 0) < 5000:
            return
        self._last_e15_status_signature = signature
        self._last_e15_status_log_ms = now
        self._log_st_aoe_event(
            "ST_E15_PROTECTION_GAP",
            model=int(getattr(model, "value", model)),
            reason=reason,
            spirit_count=len(status.get("spirits", ())),
            threatened_count=len(status.get("threatened", ())),
            uncovered_count=len(status.get("uncovered", ())),
            lowest_hp=f"{float(status.get('lowest_hp', 0.0) or 0.0):.4f}",
            direct_pressure=bool(status.get("direct_pressure", False)),
            anchor_covered=bool(status.get("anchor_covered", False)),
            spirit_duration_source="unavailable_not_guessed",
            policy="exp36_stage11_e15_observable_core_protection_status",
        )

    def _e15_service_protective_spirit(self, model: SpiritModelID, skill_id: int, *, label: str) -> bool:
        """Repair one acute protective-spirit gap without churning good spirits."""
        if False:
            yield
        _refresh_session_boundary = getattr(self, "_e15_refresh_session_boundary", None)
        if callable(_refresh_session_boundary):
            _refresh_session_boundary()
        skill_id = int(skill_id or 0)
        if skill_id <= 0 or not self.IsSkillEquipped(skill_id):
            return False
        status = self._e15_core_protection_status(model)
        self._e15_poll_pending_followup(model, status)
        update_gap = getattr(self, "_e15_update_gap_episode", None)
        if callable(update_gap):
            update_gap(model, status)
        if not str(status.get("reason", "") or ""):
            second_chance_state = getattr(
                self, "_e15_direct_core_second_chance_deadline_ms", None
            )
            model_id = int(getattr(model, "value", model))
            if isinstance(second_chance_state, dict):
                second_chance_state.pop(model_id, None)
            self._e15_clear_st_refresh_reservation(model_id)
            return False
        self._e15_log_status_if_needed(model, status)
        if not self._e15_status_actionable(status):
            second_chance_state = getattr(
                self, "_e15_direct_core_second_chance_deadline_ms", None
            )
            model_id = int(getattr(model, "value", model))
            if isinstance(second_chance_state, dict):
                second_chance_state.pop(model_id, None)
            self._e15_clear_st_refresh_reservation(model_id)
            self._log_st_aoe_event(
                "ST_E15_ACTION_SUPPRESSED",
                protection_skill=str(label),
                reason=str(status.get("reason", "")),
                threatened_count=len(status.get("threatened", ()) or ()),
                uncovered_count=len(status.get("uncovered", ()) or ()),
                lowest_hp=f"{float(status.get('lowest_hp', 0.0) or 0.0):.4f}",
                direct_pressure=bool(status.get("direct_pressure", False)),
                policy="exp36_stage12_e15b_anchor_only_is_observation_not_action",
            )
            return False

        st_remaining = int(self._e15_soul_twisting_remaining_ms())
        model_id = int(getattr(model, "value", model))
        tick_fn = getattr(self, "_game_tick", None)
        now = int(tick_fn() or 0) if callable(tick_fn) else 0
        second_chance_state = getattr(
            self, "_e15_direct_core_second_chance_deadline_ms", None
        )
        if not isinstance(second_chance_state, dict):
            second_chance_state = {}
            setattr(self, "_e15_direct_core_second_chance_deadline_ms", second_chance_state)
        second_chance_deadline = int(second_chance_state.get(model_id, 0) or 0)
        if st_remaining > int(E15_SOUL_TWISTING_URGENT_REFRESH_MS):
            second_chance_state.pop(model_id, None)
            second_chance_deadline = 0

        if st_remaining <= int(E15_SOUL_TWISTING_URGENT_REFRESH_MS):
            st_equipped = bool(self.IsSkillEquipped(Soul_Twisting_ID))
            st_can_cast = bool(st_equipped and self.CanCastSkillID(Soul_Twisting_ID))
            st_reject_reason = str(self.GetLastCastRejectReason() or "") if st_equipped and not st_can_cast else ""
            st_recharge_ms = int(self._e15_skill_recharge_ms(Soul_Twisting_ID)) if st_equipped else -1
            second_chance_active = bool(second_chance_deadline > now)
            reservation_owner = int(self._e15_active_st_refresh_reservation(now) or 0)
            st_refresh_reserved_elsewhere = bool(reservation_owner > 0 and reservation_owner != model_id)
            if st_can_cast and not st_refresh_reserved_elsewhere:
                did_refresh = yield from self.CastSkillID(
                    skill_id=Soul_Twisting_ID,
                    target_agent_id=int(Player.GetAgentID() or 0),
                    log=False,
                    aftercast_delay=250,
                )
                if did_refresh:
                    second_chance_state.pop(model_id, None)
                    reservation_consumed = bool(self._e15_clear_st_refresh_reservation(model_id))
                    self._e15_pending_followup_model = int(getattr(model, "value", model))
                    self._e15_pending_followup_deadline_ms = int(self._game_tick() or 0) + int(E15_COMMITTED_FOLLOWUP_DEADLINE_MS)
                    self._e15_pending_followup_verify_rearmed = False
                    self._log_st_aoe_event(
                        "ST_E15_SOUL_TWISTING_URGENT_REFRESH",
                        protection_skill=str(label),
                        reason=str(status.get("reason", "")),
                        previous_remaining_ms=int(st_remaining),
                        refresh_reservation_consumed=bool(reservation_consumed),
                        policy="stage2_37_reserved_refresh_owner_or_normal_urgent_refresh",
                    )
                    if second_chance_active:
                        self._log_st_aoe_event(
                            "ST_E15_SOUL_TWISTING_SECOND_CHANCE_REFRESH",
                            protection_skill=str(label),
                            reason=str(status.get("reason", "")),
                            waited_ms=max(0, int(E15_DIRECT_CORE_SECOND_CHANCE_MS) - max(0, second_chance_deadline - now)),
                            policy="stage2_01_nonblocking_second_chance_won_before_raw_core",
                        )
                    return True
                st_reject_reason = str(self.GetLastCastRejectReason() or st_reject_reason or "")
                st_recharge_ms = int(self._e15_skill_recharge_ms(Soul_Twisting_ID))
            elif st_can_cast and st_refresh_reserved_elsewhere:
                # Stage 2.37: ST is ready, but an earlier acute core owns this
                # refresh. Continue evaluating local protection without letting
                # this model consume Soul Twisting.
                st_reject_reason = "st_refresh_reserved_for_other_core"
                st_recharge_ms = int(self._e15_skill_recharge_ms(Soul_Twisting_ID))

            # Stage 2.01: replace repeated stateless deferral with one bounded,
            # non-blocking second-chance state.  There is no yield/sleep loop: the
            # controller returns now and rechecks ST on subsequent normal ticks.
            # Genuine long recharge still keeps the Stage 1.57 emergency fallback.
            if second_chance_deadline > 0:
                if now < second_chance_deadline:
                    return True
                second_chance_state.pop(model_id, None)
                self._log_st_aoe_event(
                    "ST_E15_DIRECT_CORE_SECOND_CHANCE_EXPIRED",
                    protection_skill=str(label),
                    reason=str(status.get("reason", "")),
                    soul_twisting_recharge_ms=int(st_recharge_ms),
                    soul_twisting_reject_reason=str(st_reject_reason),
                    policy="stage2_01_bounded_second_chance_expired_allow_emergency_fallback",
                )
            elif (
                st_equipped
                and st_remaining <= 1600
                and int(reservation_owner or 0) <= 0
                and self._e15_should_arm_direct_core_second_chance(
                    st_recharge_ms, st_reject_reason
                )
            ):
                deadline = int(now + E15_DIRECT_CORE_SECOND_CHANCE_MS)
                second_chance_state[model_id] = deadline
                self._e15_arm_st_refresh_reservation(
                    model_id, now=int(now), st_recharge_ms=int(st_recharge_ms), label=str(label)
                )
                self._log_st_aoe_event(
                    "ST_E15_DIRECT_CORE_SECOND_CHANCE_STARTED",
                    protection_skill=str(label),
                    reason=str(status.get("reason", "")),
                    soul_twisting_remaining_ms=int(st_remaining),
                    soul_twisting_recharge_ms=int(st_recharge_ms),
                    soul_twisting_reject_reason=str(st_reject_reason),
                    window_ms=int(E15_DIRECT_CORE_SECOND_CHANCE_MS),
                    policy="stage2_01_nonblocking_second_chance_before_raw_core",
                )
                # Keep the established Stage 1.69 marker for log consumers and
                # historical regression contracts.  Stage 2.01 bounds the defer
                # with the explicit 500 ms state above rather than changing the
                # meaning of the old marker downstream.
                self._log_st_aoe_event(
                    "ST_E15_DIRECT_CORE_DEFERRED_FOR_SOUL_TWISTING",
                    protection_skill=str(label),
                    reason=str(status.get("reason", "")),
                    soul_twisting_remaining_ms=int(st_remaining),
                    soul_twisting_recharge_ms=int(st_recharge_ms),
                    soul_twisting_reject_reason=str(st_reject_reason),
                    threatened_count=len(status.get("threatened", ()) or ()),
                    uncovered_count=len(status.get("uncovered", ()) or ()),
                    policy="stage1_69_transient_st_first_refusal_prevents_native_core_recharge",
                )
                return True

            # Stage 2.24 recharge hygiene. Live logs proved that raw Union and
            # Displacement fallbacks immediately before a Soul Twisting refresh
            # put those rituals onto their long native recharge, producing
            # 30-58 s protection gaps. When Soul Twisting is equipped, never
            # create that hole for Union/Displacement. Shelter retains a narrow
            # emergency fallback because complete Shelter loss is the greater
            # immediate survival risk. If ST is not equipped, preserve the old
            # direct-core behavior for non-ST bars.
            if st_remaining <= 1600:
                shelter_model_id = int(getattr(SpiritModelID.SHELTER, "value", SpiritModelID.SHELTER))
                is_shelter = bool(int(model_id) == int(shelter_model_id))
                transient_st_state = str(st_reject_reason or "") in {"local_cast_pending", "skill_not_ready"}
                shelter_wait_for_st = bool(
                    is_shelter
                    and st_equipped
                    and (
                        (0 <= int(st_recharge_ms) <= int(E15_RAW_SHELTER_WAIT_FOR_ST_MS))
                        or (int(st_recharge_ms) < 0 and transient_st_state)
                    )
                )
                raw_core_allowed = bool(
                    not st_equipped
                    or not E15_RAW_CORE_SHELTER_ONLY_WHILE_ST_EQUIPPED
                    or (is_shelter and not shelter_wait_for_st)
                )
                if st_equipped and not raw_core_allowed:
                    second_chance_state.pop(model_id, None)
                    self._log_st_aoe_event(
                        "ST_E15_RAW_CORE_SUPPRESSED_FOR_RECHARGE_HYGIENE",
                        protection_skill=str(label), reason=str(status.get("reason", "")),
                        soul_twisting_remaining_ms=int(st_remaining),
                        soul_twisting_recharge_ms=int(st_recharge_ms),
                        soul_twisting_reject_reason=str(st_reject_reason),
                        shelter_wait_for_st=bool(shelter_wait_for_st),
                        shelter_wait_budget_ms=int(E15_RAW_SHELTER_WAIT_FOR_ST_MS) if is_shelter else 0,
                        policy=(
                            "stage2_25_shelter_waits_for_near_ready_soul_twisting"
                            if is_shelter else "stage2_24_never_raw_union_or_displacement_with_st_equipped"
                        ),
                    )
                    log_blocked = getattr(self, "_e15_log_action_blocked", None)
                    if callable(log_blocked):
                        log_blocked(model, skill_id, status, stage="raw_core_suppressed_wait_for_soul_twisting")
                    return False

                allow_existing_spirit = bool(tuple(status.get("uncovered", ()) or ())) or (
                    status.get("reason") == "critical_spirit_hp_under_direct_pressure"
                    and self._e15_status_actionable(status)
                )
                if self.CanCastSkillID(skill_id, allow_existing_spirit=allow_existing_spirit):
                    did_direct = yield from self.CastSkillID(
                        skill_id=skill_id, target_agent_id=0, log=False, aftercast_delay=250,
                        allow_existing_spirit=allow_existing_spirit,
                    )
                    if did_direct:
                        self._log_st_aoe_event(
                            "ST_E15_DIRECT_CORE_FALLBACK", protection_skill=str(label),
                            reason=str(status.get("reason", "")),
                            soul_twisting_remaining_ms=int(st_remaining),
                            policy=(
                                "stage2_25_shelter_raw_only_after_long_or_unknown_st_outage"
                                if st_equipped else "stage1_57_non_st_bar_direct_core_fallback"
                            ),
                        )
                        return True
                log_blocked = getattr(self, "_e15_log_action_blocked", None)
                if callable(log_blocked):
                    log_blocked(model, skill_id, status, stage="urgent_st_refresh_and_direct_core_unavailable")
                return False

        allow_existing_spirit = bool(tuple(status.get("uncovered", ()) or ())) or (
            status.get("reason") == "critical_spirit_hp_under_direct_pressure" and self._e15_status_actionable(status)
        )
        if not self.CanCastSkillID(skill_id, allow_existing_spirit=allow_existing_spirit):
            log_blocked = getattr(self, "_e15_log_action_blocked", None)
            if callable(log_blocked):
                log_blocked(model, skill_id, status, stage="core_spirit_not_castable")
            return False
        did_cast = yield from self.CastSkillID(
            skill_id=skill_id,
            target_agent_id=0,
            log=False,
            aftercast_delay=250,
            allow_existing_spirit=allow_existing_spirit,
        )
        if did_cast:
            # Stage 2.34: dispatch acceptance is not proof the Binding Ritual
            # completed. The 2.33 log had a 45.7s Displacement gap after two
            # accepted renewals that never produced an observed spirit. Arm the
            # existing bounded hard-followup verifier for every urgent core cast.
            self._e15_pending_followup_model = int(getattr(model, "value", model))
            self._e15_pending_followup_verify_rearmed = True
            tick_fn = getattr(self, "_game_tick", None)
            verify_now = int(tick_fn() or 0) if callable(tick_fn) else 0
            verify_window_ms = int(globals().get("E15_COMMITTED_FOLLOWUP_DEADLINE_MS", 7000) or 7000)
            self._e15_pending_followup_deadline_ms = int(verify_now) + int(verify_window_ms)
            self._e15_pending_followup_last_hold_log_ms = 0
            self._log_st_aoe_event(
                "ST_E15_CORE_RENEWAL",
                protection_skill=str(label),
                model=int(getattr(model, "value", model)),
                reason=str(status.get("reason", "")),
                threatened_count=len(status.get("threatened", ())),
                uncovered_count=len(status.get("uncovered", ())),
                previous_lowest_hp=f"{float(status.get('lowest_hp', 0.0) or 0.0):.4f}",
                soul_twisting_remaining_ms=int(st_remaining),
                verification_deadline_ms=int(verify_window_ms),
                policy="stage2_34_core_renewal_requires_observed_spirit_confirmation",
            )
            self._log_st_aoe_event(
                "ST_E15_CORE_RENEWAL_VERIFY_ARMED", protection_skill=str(label),
                model=int(getattr(model, "value", model)), deadline_ms=int(verify_window_ms),
                policy="stage2_34_retry_interrupted_or_lost_core_without_raw_recharge_regression",
            )
            return True
        return False

    def _cast_summon_spirits_for_follow(self) -> bool:
        """Travel/post-combat reposition only; never drag safe spirits into combat.

        The core package may remain behind while its Spirit-range protection still
        covers the engagement.  During an active fight this routine intentionally
        does nothing.  The package is moved after combat/travel, or by the separate
        low-health/AoE rescue paths when that is actually necessary.
        """
        if False:
            yield

        skill_id = self._summon_spirits_skill_id()
        if skill_id <= 0 or not self.CanCastSkillID(skill_id):
            return False

        spirits, _nearest, farthest = self._core_spirit_distance_stats()
        if not spirits:
            return False

        # Any living enemy within Spirit range of the Ritualist means the party is
        # still in/at the current engagement. Keep the protection package planted
        # in the backline instead of teleporting it onto the moving ST.
        if self._has_enemy_near_position(Player.GetXY(), Range.Spirit.value):
            return False

        follow_threshold = 1850.0
        if float(farthest) < follow_threshold:
            return False
        if not self._summon_spirits_backline_safe(reason="post_combat_follow"):
            return False

        did_cast = yield from self.CastSkillID(
            skill_id=skill_id,
            log=False,
            aftercast_delay=180,
        )
        if did_cast:
            self._log_st_aoe_event(
                "ST_SPIRIT_POST_COMBAT_REPOSITION",
                core_count=len(spirits),
                farthest_distance=f"{float(farthest):.1f}",
                threshold=f"{float(follow_threshold):.1f}",
            )
        return bool(did_cast)

    def _cast_summon_spirits_at_safe_aoe_hold(self) -> bool:
        """After the ST escapes an AoE, pull its core spirits onto the safe position."""
        if False:
            yield

        skill_id = self._summon_spirits_skill_id()
        if skill_id <= 0 or not self.CanCastSkillID(skill_id):
            return False

        spirits, _nearest, farthest = self._core_spirit_distance_stats()
        if not spirits:
            return False

        exposed = 0
        for spirit_id in spirits:
            try:
                if is_position_in_active_aoe(
                    Agent.GetXY(spirit_id),
                    padding=80.0,
                    critical_only=False,
                ):
                    exposed += 1
            except Exception:
                continue

        if exposed <= 0 and float(farthest) < 450.0:
            return False

        did_cast = yield from self.CastSkillID(
            skill_id=skill_id,
            log=False,
            aftercast_delay=180,
        )
        if did_cast:
            self._log_st_aoe_event(
                "ST_AOE_SAFE_SPIRIT_PULL",
                core_count=len(spirits),
                exposed_count=int(exposed),
                farthest_distance=f"{float(farthest):.1f}",
            )
        return bool(did_cast)

    def _has_enemy_pressure_near_core_spirits(self, spirit_ids: list[int]) -> bool:
        positions: list[tuple[float, float]] = []
        try:
            player_xy = Player.GetXY()
            if player_xy:
                positions.append(player_xy)
        except Exception:
            pass
        for spirit_id in spirit_ids or []:
            try:
                spirit_xy = Agent.GetXY(spirit_id)
                if spirit_xy:
                    positions.append(spirit_xy)
            except Exception:
                continue
        return any(self._has_enemy_near_position(position, Range.Spirit.value) for position in positions)

    def _log_st_aoe_event(self, event: str, **fields) -> None:
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                str(event),
                account=str(Player.GetAccountEmail() or ""),
                **fields,
            )
        except Exception:
            pass

    def _cast_summon_spirits_for_aoe_rescue(self) -> bool:
        """Rescue planted core spirits only when they are inside a danger field.

        This is deliberately narrower than travel repositioning.  Pulling safe
        Shelter/Union away from the party merely because the ST was targeted
        would be harmful; exposed spirits, however, should be moved to the
        stable safe-hold point before replacements are planted.
        """
        if False:
            yield

        skill_id = self._summon_spirits_skill_id()
        if skill_id <= 0:
            return False
        spirits = self._get_owned_core_spirits(Range.Spellcast.value)
        if len(spirits) < 2:
            return False

        exposed = []
        for spirit_id in spirits:
            try:
                if is_position_in_active_aoe(
                    Agent.GetXY(spirit_id),
                    padding=80.0,
                    critical_only=False,
                ):
                    exposed.append(int(spirit_id))
            except Exception:
                continue
        if not exposed:
            return False

        did_cast = yield from self.CastSkillID(
            skill_id=skill_id,
            log=False,
            aftercast_delay=250,
        )
        if did_cast:
            self._log_st_aoe_event(
                "ST_AOE_SPIRIT_RESCUE",
                exposed_count=len(exposed),
                core_count=len(spirits),
            )
        return did_cast

    def _cast_summon_spirits_for_core_heal(self) -> bool:
        if False:
            yield

        skill_id = self._summon_spirits_skill_id()
        if skill_id <= 0:
            return False

        # Use Summon Spirits only as an optional emergency spirit-sustain tool.
        # Do not use it merely for travelling/repositioning in this build, because
        # pulling Shelter/Union/Displacement to the wrong spot can be worse than
        # leaving them planted.
        spirits = self._get_owned_core_spirits(Range.Spellcast.value)
        if len(spirits) < 2:
            return False
        low_core_spirits = [spirit_id for spirit_id in spirits if float(Agent.GetHealth(spirit_id) or 0.0) < 0.30]
        if not low_core_spirits:
            return False
        if not self._has_enemy_pressure_near_core_spirits(spirits):
            return False

        # Low-health spirits are worth rescuing, but never by teleporting the
        # package into melee range. Wait until the ST is back in a safe backline
        # position; otherwise the old planted spirits remain the safer option.
        if not self._summon_spirits_backline_safe(reason="low_core_health"):
            return False

        did_cast = yield from self.CastSkillID(
            skill_id=skill_id,
            log=False,
            aftercast_delay=250,
        )
        if did_cast:
            self._log_st_aoe_event(
                "ST_SPIRIT_LOW_HP_SAFE_PULL",
                low_count=len(low_core_spirits),
                core_count=len(spirits),
            )
        return bool(did_cast)

    def _st_pre_spirit_setup_decision(self, *, in_aggro: bool) -> tuple[bool, str, float, float]:
        """Admit core spirit setup only for a *real* nearby enemy packet.

        Generic IsCloseToAggro() and TeamRecovery requests are intentionally
        insufficient: they used to trigger Shelter/Union/Displacement over and
        over while the team was simply travelling between fights.

        The leader scan prepares in time for an approaching pull, but only when
        the ST is close enough to the leader for newly cast spirits to cover it.
        Already active combat has a somewhat larger radius so protection keeps
        running when opponents kite just outside the pre-pull window.
        """
        try:
            st_xy = Player.GetXY()
        except Exception:
            st_xy = None
        nearest_st = float(self._nearest_enemy_distance_to_position(st_xy))
        nearest_leader = 99999.0
        distance_to_leader = 99999.0
        try:
            from Py4GWCoreLib.Party import Party
            leader_id = int(Party.GetPartyLeaderID() or 0)
            if leader_id > 0 and Agent.IsValid(leader_id):
                leader_xy = Agent.GetXY(leader_id)
                if leader_xy and st_xy:
                    distance_to_leader = float(self._e15_distance(st_xy, leader_xy))
                    nearest_leader = float(self._nearest_enemy_distance_to_position(leader_xy))
        except Exception:
            pass

        # Combat first: never delay urgent protection while enemies are fighting
        # close enough to the ST or the nearby party leader.
        if in_aggro and (
            nearest_st <= float(ST_ACTIVE_COMBAT_ENEMY_DISTANCE)
            or (
                distance_to_leader <= float(ST_ACTIVE_COMBAT_MAX_ST_TO_LEADER_DISTANCE)
                and nearest_leader <= float(ST_ACTIVE_COMBAT_ENEMY_DISTANCE)
            )
        ):
            return True, "active_combat", nearest_st, nearest_leader

        if nearest_st <= float(ST_PREP_ENEMY_DISTANCE_FROM_ST):
            return True, "enemy_near_st_prepull", nearest_st, nearest_leader

        if (
            distance_to_leader <= float(ST_PREP_MAX_ST_TO_LEADER_DISTANCE)
            and nearest_leader <= float(ST_PREP_ENEMY_DISTANCE_FROM_LEADER)
        ):
            return True, "enemy_near_leader_prepull", nearest_st, nearest_leader

        return False, "travel_no_relevant_enemy", nearest_st, nearest_leader

    def _is_pre_spirit_setup_needed(self) -> bool:
        # Retain the old boolean API for any external callers; the snapshot
        # routine below owns the richer diagnostic event/phase transition.
        needed, _reason, _st_distance, _leader_distance = self._st_pre_spirit_setup_decision(
            in_aggro=bool(self.IsInAggro())
        )
        return bool(needed)

    def _cast_winter_primary(self, *, close_to_aggro: bool, in_aggro: bool) -> bool:
        """ST owns Winter first refusal; PI only backs it up near expiry."""
        if not (bool(close_to_aggro) or bool(in_aggro)):
            return False
        if not self.IsSkillEquipped(Winter_ID):
            return False
        if not self.CanCastSkillID(Winter_ID, allow_existing_spirit=True):
            return False
        if not self._st_optional_energy_budget_ok(Winter_ID, label="Winter"):
            return False
        try:
            from Py4GWCoreLib.Builds.Skills import MesmerwayCoordination as MWC
        except Exception:
            return False
        if not MWC.winter_should_cast("primary"):
            return False
        if not MWC.reserve_winter_cast():
            return False
        try:
            winter_cast_xy = tuple(Player.GetXY())
        except Exception:
            winter_cast_xy = None
        did = bool((yield from self.CastSkillID(
            Winter_ID, log=False, aftercast_delay=250, allow_existing_spirit=True
        )))
        if not did:
            MWC.release_winter_pending(str(self.GetLastCastRejectReason() or "st_winter_not_fired"))
            return False
        record = MWC.register_winter_cast(role="st_primary", cast_xy=winter_cast_xy)
        self._log_st_aoe_event(
            "MESMERWAY_WINTER_CAST", role="st_primary",
            duration_ms=int(record.get("duration_ms", 0) or 0),
            policy="stage2_05_st_primary_panic_backup_fresh_handoff",
        )
        return True

    def _cast_winter_preaggro_first_refusal(self, snapshot) -> bool:
        """Give a missing Winter one approach-window action before optional setup.

        Acute Shelter/Union/Displacement repair remains earlier in the main
        rotation.  This helper exists separately so the normal Stage 2.02
        core-package -> Winter ordering stays intact for ordinary refreshes.
        """
        if not bool(getattr(snapshot, "close_to_aggro", False)):
            return False
        if bool(getattr(snapshot, "in_aggro", False)):
            return False
        did = bool((yield from self._cast_winter_primary(
            close_to_aggro=True, in_aggro=False
        )))
        if did:
            self._log_st_aoe_event(
                "MESMERWAY_WINTER_PREAGGRO_FIRST_REFUSAL",
                policy="stage2_05_initial_winter_before_optional_setup",
            )
        return did

    def _edge_of_extinction_large_group_count(self) -> int:
        """Count a real nearby pack without adding any SharedMemory traffic.

        The scan is local to the ST client and cached briefly because this helper
        sits in the combat hot path.  Only enemies inside the actual 2500-ish
        spirit coverage around the ST count, so the ritual is not dropped for a
        distant packet that it cannot affect.
        """
        now = int(self._game_tick() or 0)
        if (
            now > 0
            and self._edge_of_extinction_scan_tick > 0
            and now - int(self._edge_of_extinction_scan_tick) < int(EDGE_OF_EXTINCTION_SCAN_CACHE_MS)
        ):
            return int(self._edge_of_extinction_scan_count or 0)

        try:
            px, py = Player.GetXY()
        except Exception:
            px = py = None
        if px is None or py is None:
            self._edge_of_extinction_scan_count = 0
            self._edge_of_extinction_scan_tick = now
            return 0

        max_distance_sq = float(Range.Spirit.value) ** 2
        count = 0
        for enemy_id in AgentArray.GetEnemyArray() or []:
            try:
                if not Agent.IsAlive(int(enemy_id)):
                    continue
                exy = Agent.GetXY(int(enemy_id))
                if not exy:
                    continue
                dx = float(px) - float(exy[0])
                dy = float(py) - float(exy[1])
                if (dx * dx + dy * dy) <= max_distance_sq:
                    count += 1
            except Exception:
                continue

        self._edge_of_extinction_scan_count = int(count)
        self._edge_of_extinction_scan_tick = int(now)
        return int(count)

    def _edge_of_extinction_immediate_core_action_ready(self) -> bool:
        """Whether EoE would steal a core-protection action that can fire now.

        KeySoJWay's useful behavior came from *first refusal*, not from requiring
        every core spirit to remain simultaneously alive before optional support
        could ever run.  The main loop already services Shelter/Union/Displacement
        immediately before EoE.  This second guard is deliberately narrower: if
        an acute E15 gap exists *and* either Soul Twisting or the corresponding
        core ritual is actually executable this tick, EoE yields.  If the repair
        is unavailable (recharge/energy/etc.), EoE may use the otherwise idle
        window while the absolute core-energy reserve is still protected below.
        """
        if int(self._e15_pending_followup_model or 0) > 0:
            return True

        st_remaining = int(self._e15_soul_twisting_remaining_ms())
        for model, skill_id in (
            (SpiritModelID.SHELTER, Shelter_ID),
            (SpiritModelID.UNION, Union_ID),
            (SpiritModelID.DISPLACEMENT, Displacement_ID),
        ):
            skill_id = int(skill_id or 0)
            if skill_id <= 0 or not self.IsSkillEquipped(skill_id):
                continue
            status = self._e15_core_protection_status(model)
            if not self._e15_status_actionable(status):
                continue
            if (
                st_remaining <= int(E15_SOUL_TWISTING_URGENT_REFRESH_MS)
                and self.IsSkillEquipped(Soul_Twisting_ID)
                and self.CanCastSkillID(Soul_Twisting_ID)
            ):
                return True
            allow_existing_spirit = bool(tuple(status.get("uncovered", ()) or ())) or (
                status.get("reason") == "critical_spirit_hp_under_direct_pressure"
                and self._e15_status_actionable(status)
            )
            if st_remaining > 1200 and self.CanCastSkillID(
                skill_id, allow_existing_spirit=allow_existing_spirit
            ):
                return True
        return False


    def _st_core_energy_reserve(self) -> tuple[float, float]:
        """Return (current absolute energy, protected core reserve).

        Reserve one immediately-needed core ritual plus Soul Twisting when its
        buff is not currently active. This prevents Winter/support fillers from
        spending the last energy that Shelter/Union/Displacement needs next tick.
        """
        player_id = int(Player.GetAgentID() or 0)
        if player_id <= 0:
            return 0.0, 0.0
        try:
            current = float(Agent.GetEnergy(player_id)) * float(Agent.GetMaxEnergy(player_id))
        except Exception:
            current = 0.0
        max_core = 0.0
        for sid in (Shelter_ID, Union_ID, Displacement_ID):
            if int(sid or 0) <= 0 or not self.IsSkillEquipped(int(sid)):
                continue
            try:
                cost = float(Routines.Checks.Skills.GetEnergyCostWithEffects(int(sid), player_id))
            except Exception:
                try:
                    cost = float(Skill.Data.GetEnergyCost(int(sid)) or 0.0)
                except Exception:
                    cost = 0.0
            max_core = max(float(max_core), float(cost))
        st_cost = 0.0
        if self.IsSkillEquipped(Soul_Twisting_ID) and int(self._e15_soul_twisting_remaining_ms()) <= 0:
            try:
                st_cost = float(Routines.Checks.Skills.GetEnergyCostWithEffects(Soul_Twisting_ID, player_id))
            except Exception:
                try:
                    st_cost = float(Skill.Data.GetEnergyCost(Soul_Twisting_ID) or 0.0)
                except Exception:
                    st_cost = 0.0
        reserve = max(float(ST_CORE_ENERGY_RESERVE_BUFFER), float(max_core + st_cost + ST_CORE_ENERGY_RESERVE_BUFFER))
        return float(current), float(reserve)

    def _st_optional_energy_budget_ok(self, skill_id: int, *, label: str) -> bool:
        sid = int(skill_id or 0)
        if sid <= 0:
            return False
        current, reserve = self._st_core_energy_reserve()
        player_id = int(Player.GetAgentID() or 0)
        try:
            cost = float(Routines.Checks.Skills.GetEnergyCostWithEffects(sid, player_id))
        except Exception:
            try:
                cost = float(Skill.Data.GetEnergyCost(sid) or 0.0)
            except Exception:
                cost = 0.0
        ok = bool(float(current) + 0.01 >= float(cost + reserve))
        if not ok:
            now = int(self._game_tick() or 0)
            if (
                now <= 0
                or int(self._st_core_energy_last_block_skill or 0) != sid
                or now - int(self._st_core_energy_last_log_ms or 0) >= int(ST_CORE_ENERGY_RESERVE_LOG_MS)
            ):
                self._st_core_energy_last_log_ms = int(now)
                self._st_core_energy_last_block_skill = int(sid)
                self._log_st_aoe_event(
                    "ST_CORE_ENERGY_RESERVE_BLOCK", skill_id=int(sid), skill=str(label),
                    absolute_energy=round(float(current), 2), optional_cost=round(float(cost), 2),
                    protected_reserve=round(float(reserve), 2),
                    policy="stage2_26_core_spirit_energy_first_refusal",
                )
        return ok

    def _edge_of_extinction_energy_budget_ok(self) -> tuple[bool, float, float, float]:
        """Keep enough absolute energy for the next core-spirit action.

        This is intentionally local and read-only.  EoE may only spend energy
        when the ST can still afford at least the most expensive equipped core
        ritual immediately afterwards.
        """
        player_id = int(Player.GetAgentID() or 0)
        if player_id <= 0:
            return False, 0.0, 0.0, 0.0
        try:
            current_energy = float(Agent.GetEnergy(player_id)) * float(Agent.GetMaxEnergy(player_id))
        except Exception:
            current_energy = 0.0
        try:
            edge_cost = float(Routines.Checks.Skills.GetEnergyCostWithEffects(Edge_of_Extinction_ID, player_id))
        except Exception:
            try:
                edge_cost = float(Skill.Data.GetEnergyCost(Edge_of_Extinction_ID) or 0.0)
            except Exception:
                edge_cost = 0.0

        _current, generalized_reserve = self._st_core_energy_reserve()
        reserve = max(float(EDGE_OF_EXTINCTION_MIN_CORE_RESERVE), float(generalized_reserve))
        return bool(current_energy + 0.01 >= edge_cost + reserve), current_energy, edge_cost, reserve

    def _log_edge_of_extinction_status_once(self) -> None:
        if bool(self._edge_of_extinction_status_logged):
            return
        self._edge_of_extinction_status_logged = True
        skill_id = int(Edge_of_Extinction_ID or 0)
        equipped = False
        slot = 0
        if skill_id > 0:
            try:
                equipped = bool(self.IsSkillEquipped(skill_id))
            except Exception:
                equipped = False
            try:
                slot = int(GLOBAL_CACHE.SkillBar.GetSlotBySkillID(skill_id) or 0)
            except Exception:
                slot = 0
        self._log_st_aoe_event(
            "ST_EDGE_OF_EXTINCTION_STATUS",
            skill_id=skill_id,
            equipped=bool(equipped),
            slot=int(slot),
            min_enemies=int(EDGE_OF_EXTINCTION_MIN_ENEMIES),
            policy="stage20_3_runtime_contract_probe_local_only",
        )

    def _observe_edge_of_extinction_runtime(self) -> None:
        """Read-only Stage-20.8 proof for real EoE activation/recharge.

        ``ST_EDGE_OF_EXTINCTION_CAST`` proves that our controller returned a
        successful CastSkillID result.  This observer independently watches the
        local player's live casting skill plus the equipped slot's ready->
        recharge transition, so a real Guild Wars cast is visible even when it
        came through another local dispatcher path.  It is deliberately kept
        out of all decision state: no SharedMemory, no enemy scan, no waits, and
        no writes to ``_last_edge_of_extinction_cast_tick``.
        """
        skill_id = int(Edge_of_Extinction_ID or 0)
        if skill_id <= 0:
            return
        try:
            equipped = bool(self.IsSkillEquipped(skill_id))
        except Exception:
            equipped = False
        if not equipped:
            self._edge_of_extinction_recharge_prev = None
            self._edge_of_extinction_activation_active = False
            return

        now = int(self._game_tick() or 0)
        if (
            now > 0
            and int(self._edge_of_extinction_observer_tick or 0) > 0
            and now - int(self._edge_of_extinction_observer_tick)
            < int(EDGE_OF_EXTINCTION_OBSERVER_POLL_MS)
        ):
            return
        self._edge_of_extinction_observer_tick = int(now)

        try:
            slot = int(GLOBAL_CACHE.SkillBar.GetSlotBySkillID(skill_id) or 0)
        except Exception:
            slot = 0
        if not (1 <= slot <= 8):
            self._edge_of_extinction_recharge_prev = None
            return

        # Activation observation is useful if a recharge reset happens before
        # the next sample. Nature Rituals cast long enough that the 100 ms local
        # poll normally catches this edge without any per-frame log spam.
        casting_now = False
        try:
            player_id = int(Player.GetAgentID() or 0)
            casting_now = bool(
                player_id > 0
                and Agent.IsCasting(player_id)
                and int(Agent.GetCastingSkillID(player_id) or 0) == skill_id
            )
        except Exception:
            casting_now = False
        if casting_now and not bool(self._edge_of_extinction_activation_active):
            self._log_st_aoe_event(
                "ST_EDGE_OF_EXTINCTION_ACTIVATION_OBSERVED",
                skill_id=int(skill_id),
                slot=int(slot),
                policy="stage20_8_local_read_only_activation_probe",
            )
        self._edge_of_extinction_activation_active = bool(casting_now)

        try:
            data = GLOBAL_CACHE.SkillBar.GetSkillData(int(slot))
            recharge = max(0, int(getattr(data, "get_recharge", 0) or 0))
        except Exception:
            return

        prev = self._edge_of_extinction_recharge_prev
        self._edge_of_extinction_recharge_prev = int(recharge)
        if prev is None:
            return
        if int(prev) <= 0 and int(recharge) > 0:
            self._log_st_aoe_event(
                "ST_EDGE_OF_EXTINCTION_RECHARGE_STARTED",
                skill_id=int(skill_id),
                slot=int(slot),
                recharge_ms=int(recharge),
                policy="stage20_8_actual_cast_proof_ready_to_recharge_edge_local_only",
            )

    def _log_edge_of_extinction_block(self, reason: str, *, enemy_count: int, phase: str) -> None:
        now = int(self._game_tick() or 0)
        reason = str(reason)
        if (
            reason == str(self._edge_of_extinction_last_block_reason or "")
            and now > 0
            and int(self._edge_of_extinction_last_block_log_tick or 0) > 0
            and now - int(self._edge_of_extinction_last_block_log_tick) < int(EDGE_OF_EXTINCTION_BLOCK_LOG_MS)
        ):
            return
        self._edge_of_extinction_last_block_reason = reason
        self._edge_of_extinction_last_block_log_tick = int(now)
        self._log_st_aoe_event(
            "ST_EDGE_OF_EXTINCTION_BLOCK",
            reason=reason,
            enemy_count=int(enemy_count),
            phase=str(phase),
            policy="stage20_3_rate_limited_local_diagnostic",
        )

    def _cast_edge_of_extinction_large_group(
        self,
        *,
        player_energy_pct: float,
        in_aggro: bool,
        phase: str,
    ) -> bool:
        """Use optional EoE for real 4+ packs without stealing ST protection.

        Shelter/Union/Displacement keep first refusal in the caller.  Unlike the
        over-conservative Stage 1.52 gate, EoE no longer requires all equipped
        core spirits to be simultaneously alive.  It still yields to a pending
        urgent follow-up or any immediately executable acute core repair.
        """
        self._log_edge_of_extinction_status_once()
        skill_id = int(Edge_of_Extinction_ID or 0)
        if skill_id <= 0:
            return False
        if not self.IsSkillEquipped(skill_id):
            return False

        enemy_count = int(self._edge_of_extinction_large_group_count() or 0)
        if enemy_count < int(EDGE_OF_EXTINCTION_MIN_ENEMIES):
            return False
        if float(player_energy_pct) < float(EDGE_OF_EXTINCTION_MIN_ENERGY_FRACTION):
            self._log_edge_of_extinction_block("low_energy_fraction", enemy_count=enemy_count, phase=phase)
            return False
        if not self.CanCastSkillID(skill_id):
            self._log_edge_of_extinction_block("skill_not_ready", enemy_count=enemy_count, phase=phase)
            return False
        if int(self._e15_pending_followup_model or 0) > 0:
            self._log_edge_of_extinction_block("pending_core_followup", enemy_count=enemy_count, phase=phase)
            return False
        if self._edge_of_extinction_immediate_core_action_ready():
            self._log_edge_of_extinction_block("immediate_core_action_ready", enemy_count=enemy_count, phase=phase)
            return False

        budget_ok, current_energy, edge_cost, reserve = self._edge_of_extinction_energy_budget_ok()
        if not budget_ok:
            self._log_edge_of_extinction_block("core_energy_reserve", enemy_count=enemy_count, phase=phase)
            return False

        now = int(self._game_tick() or 0)
        if (
            now > 0
            and int(self._last_edge_of_extinction_cast_tick or 0) > 0
            and now - int(self._last_edge_of_extinction_cast_tick)
            < int(EDGE_OF_EXTINCTION_MIN_RECAST_HOLD_MS)
        ):
            self._log_edge_of_extinction_block("local_recast_hold", enemy_count=enemy_count, phase=phase)
            return False

        did_cast = yield from self.CastSkillID(
            skill_id=Edge_of_Extinction_ID,
            log=False,
            aftercast_delay=250,
        )
        if did_cast:
            self._last_edge_of_extinction_cast_tick = int(self._game_tick() or now)
            self._log_st_aoe_event(
                "ST_EDGE_OF_EXTINCTION_CAST",
                enemy_count=int(enemy_count),
                phase=str(phase),
                energy_before=f"{float(player_energy_pct):.4f}",
                absolute_energy_before=f"{float(current_energy):.1f}",
                edge_cost=f"{float(edge_cost):.1f}",
                core_reserve=f"{float(reserve):.1f}",
                policy="stage1_56_keysojway_first_refusal_without_all_core_present_gate",
            )
        return bool(did_cast)

    def _cast_precombat_self_enchantment_if_missing(self, skill_id: int, *, label: str) -> bool:
        """Pre-cast an equipped self enchantment only when it is completely absent.

        EXP18 uses this only during the widened pre-spirit setup window. This
        intentionally does not refresh an enchantment that is already active;
        normal in-combat maintenance remains responsible for later refreshes.
        """
        skill_id = int(skill_id or 0)
        if skill_id <= 0 or not self.IsSkillEquipped(skill_id):
            return False
        player_id = int(Player.GetAgentID() or 0)
        if player_id <= 0:
            return False
        if Routines.Checks.Agents.HasEffect(player_id, skill_id):
            return False
        if not self.CanCastSkillID(skill_id):
            return False
        if not self._st_optional_energy_budget_ok(skill_id, label=str(label)):
            return False
        did_cast = yield from self.CastSkillID(
            skill_id=skill_id,
            target_agent_id=player_id,
            log=False,
            aftercast_delay=250,
        )
        if did_cast:
            self._log_st_aoe_event(
                "ST_PRECOMBAT_ENCHANTMENT",
                skill=str(label),
                policy="enemy_near_pre_spirit_missing_only",
            )
        return bool(did_cast)

    def _get_bar_snapshot(self) -> _SoulTwistingSnapshot:
        snapshot = _SoulTwistingSnapshot()
        snapshot.in_aggro = bool(self.IsInAggro())
        now = self._game_tick()
        if snapshot.in_aggro and not self._mesmerway_wisdom_was_in_aggro:
            self._mesmerway_wisdom_combat_started_ms = int(now)
        elif not snapshot.in_aggro:
            self._mesmerway_wisdom_combat_started_ms = 0
        self._mesmerway_wisdom_was_in_aggro = bool(snapshot.in_aggro)
        (snapshot.pre_spirit_setup, travel_reason,
         nearest_st, nearest_leader) = self._st_pre_spirit_setup_decision(
            in_aggro=bool(snapshot.in_aggro)
        )
        # Never bypass the measured enemy gate via the generic close-to-aggro
        # flag: that shortcut caused ritual casts during enemy-free travel.
        snapshot.close_to_aggro = bool(snapshot.pre_spirit_setup)
        phase = "combat" if snapshot.in_aggro and snapshot.pre_spirit_setup else (
            "prepull" if snapshot.pre_spirit_setup else "travel"
        )
        if phase != self._st_last_travel_setup_phase:
            self._st_last_travel_setup_phase = str(phase)
            self._log_st_aoe_event(
                "ST_TRAVEL_PREPULL_PHASE",
                phase=str(phase),
                reason=str(travel_reason),
                nearest_st_enemy=f"{nearest_st:.0f}",
                nearest_leader_enemy=f"{nearest_leader:.0f}",
                policy="no_rituals_while_travelling_prepare_near_pull_preserve_combat",
            )
        snapshot.player_energy_pct = float(Agent.GetEnergy(Player.GetAgentID()))
        return snapshot

    @staticmethod
    def _game_tick() -> int:
        try:
            import PySystem

            return int(PySystem.get_tick_count64() or 0)
        except Exception:
            return 0

    def _coordination_group_id(self) -> int:
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

    def _roj_cluster_coverage_count(self, anchor_agent_id: int) -> int:
        """Read the current RoJ carpet depth without reserving any RoJ lane."""
        try:
            from Py4GWCoreLib.enums_src.Whiteboard_enums import (
                WhiteboardClaimStrength,
                WhiteboardLockKind,
                WhiteboardReentryPolicy,
            )

            now_tick = self._game_tick()
            if now_tick <= 0 or int(anchor_agent_id or 0) <= 0:
                return 0
            return int(GLOBAL_CACHE.ShMem.CountLocks(
                int(WhiteboardLockKind.SKILL_TARGET),
                int(ROJ_CLUSTER_COVERAGE_LOCK_ID),
                int(anchor_agent_id),
                int(self._coordination_group_id()),
                "",
                int(now_tick),
                int(WhiteboardReentryPolicy.NON_REENTRANT),
                int(WhiteboardClaimStrength.HARD),
            ) or 0)
        except Exception:
            return 0

    def _pick_idle_weapon_target(self) -> int:
        """ST weapon pressure follows the shared primary kill target."""
        try:
            from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import get_team_primary_target
            return int(get_team_primary_target(
                filter_range=Range.Spellcast.value,
                minimum_enemies=1,
                consumer_role="st_idle_weapon_primary_focus",
            ) or 0)
        except Exception:
            return 0

    def _get_roj_team_focus_packet(self) -> tuple[int, list[int]]:
        """Return the exact shared RoJway packet; never invent a local target."""
        try:
            from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import (
                get_team_primary_target,
                get_team_cluster_members,
            )

            anchor = int(get_team_primary_target(
                filter_range=Range.Spellcast.value,
                minimum_enemies=1,
                consumer_role="st_binding_chains_primary_focus",
            ) or 0)
            if anchor <= 0 or not Agent.IsValid(anchor) or not Agent.IsAlive(anchor):
                return 0, []
            members = [
                int(agent_id)
                for agent_id in get_team_cluster_members(
                    anchor,
                    radius=Range.Adjacent.value,
                    filter_range=Range.Spellcast.value,
                )
                if int(agent_id or 0) > 0
                and Agent.IsValid(int(agent_id))
                and Agent.IsAlive(int(agent_id))
            ]
        except Exception:
            return 0, []

        if anchor not in members:
            members.append(anchor)
        return anchor, sorted(set(members))

    def _binding_chains_target_still_valid(self, expected_anchor_id: int) -> bool:
        anchor, members = self._get_roj_team_focus_packet()
        return bool(
            int(anchor) == int(expected_anchor_id)
            and len(members) >= 2
            and Agent.IsValid(int(anchor))
            and Agent.IsAlive(int(anchor))
            and not packet_has_binding_chains(members)
        )

    def _dulled_weapon_core_safe(self) -> bool:
        """No optional 15e hex may steal a cast from an urgent ST protection repair."""
        if self._e15_pending_followup_model or self._e15_st_refresh_reservation_model:
            return False
        for model, skill_id in (
            (SpiritModelID.SHELTER, Shelter_ID),
            (SpiritModelID.UNION, Union_ID),
            (SpiritModelID.DISPLACEMENT, Displacement_ID),
        ):
            if not self.IsSkillEquipped(skill_id):
                continue
            if skill_id == Displacement_ID and self._doa_demonic_miasma_active():
                continue  # Environmental skill ban does not block all support.
            if not self._core_spirit_effect_still_covers(
                model, coverage_headroom=E15_COVERAGE_HEADROOM
            ):
                return False
        return True

    def _dulled_weapon_select_packet(self, now: int) -> tuple[int, tuple[int, ...], dict]:
        """Prefer overlapping unhexed enemies and physical threats INSIDE the shared packet.

        Read-only target selection; does not issue movement, change team focus or
        query enemies beyond the canonical combat-plan member_ids.
        """
        try:
            from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import get_team_combat_plan
            from Py4GWCoreLib.Py4GWcorelib import Utils
            plan = get_team_combat_plan(
                filter_range=Range.Spellcast.value,
                minimum_enemies=2, consumer_role='st_dulled_weapon_packet',
            )
            members = []
            for raw in plan.member_ids:
                enemy = int(raw or 0)
                if enemy <= 0 or not Agent.IsValid(enemy) or not Agent.IsAlive(enemy):
                    continue
                if float(Utils.Distance(Player.GetXY(), Agent.GetXY(enemy))) > float(Range.Spellcast.value):
                    continue
                members.append(enemy)
            members = sorted(set(members))
            if len(members) < DULLED_MIN_PACKET_SIZE:
                return 0, (), {}
            positions = {enemy: Agent.GetXY(enemy) for enemy in members}
            nearby_radius = float(Range.Adjacent.value)
            martial = {1, 2, 7, 9, 10}  # W, R, A, P, D (GW primary-profession IDs)
            best = None
            for target in members:
                adjacent = tuple(enemy for enemy in members
                                 if float(Utils.Distance(positions[target], positions[enemy])) <= nearby_radius)
                uncovered = tuple(enemy for enemy in adjacent
                    if int(self._dulled_recent_coverage.get(enemy, 0) or 0) <= now)
                if not uncovered:
                    continue
                unhexed = sum(1 for enemy in uncovered if not bool(Agent.IsHexed(enemy)))
                physical = 0
                attackers = 0
                for enemy in adjacent:
                    try:
                        primary, _secondary = Agent.GetProfessionIDs(enemy)
                        if int(getattr(primary, 'value', primary)) in martial:
                            physical += 1
                    except Exception:
                        pass
                    try:
                        if Agent.IsAttacking(enemy):
                            attackers += 1
                    except Exception:
                        pass
                # A new cluster hex enables Unnatural Signet bonuses. When the
                # team already has Panic hexes, attack suppression still helps.
                if unhexed <= 0 and physical <= 0 and attackers <= 0:
                    continue
                value = (unhexed * 4 + physical * 3 + attackers * 2
                         + len(uncovered) + min(len(adjacent), 4)
                         + (2 if target == int(plan.aoe_anchor or 0) else 0))
                score = (value, len(uncovered), -target)
                if best is None or score > best[0]:
                    best = (score, target, adjacent,
                            {'unhexed': unhexed, 'physical': physical,
                             'attackers': attackers, 'packet_size': len(members)})
            if best is None:
                return 0, (), {}
            return int(best[1]), tuple(best[2]), dict(best[3])
        except Exception:
            # Unknown API state cannot justify an optional expensive cast.
            return 0, (), {}

    def _cast_dulled_weapon_cluster(self, *, player_energy_pct: float):
        """Use idle cast windows only; core spirits/emergency heals ALWAYS win."""
        if False:
            yield
        if not (self.IsSkillEquipped(Dulled_Weapon_ID)
                and self.IsInAggro()
                and float(player_energy_pct) >= DULLED_MIN_ENERGY_FRAC
                and self._dulled_weapon_core_safe()
                and self.CanCastSkillID(Dulled_Weapon_ID)):
            return False
        if not self._st_optional_energy_budget_ok(Dulled_Weapon_ID, label='Dulled_Weapon'):
            return False
        current, reserve = self._st_core_energy_reserve()
        try:
            energy_cost = float(Routines.Checks.Skills.GetEnergyCostWithEffects(
                Dulled_Weapon_ID, int(Player.GetAgentID())
            ))
        except Exception:
            energy_cost = 15.0
        if current < energy_cost + reserve + DULLED_ADDITIONAL_CORE_HEADROOM:
            return False
        now = int(self._game_tick() or 0)
        if now <= 0 or now - int(self._last_dulled_cast_tick or 0) < 1500:
            return False
        # Drop stale coverage reservations; they are only temporary anti-spam
        # guards, not claims of confirmed target-side effect persistence.
        self._dulled_recent_coverage = {
            eid: until for eid, until in self._dulled_recent_coverage.items() if until > now
        }
        target, adjacent, stats = self._dulled_weapon_select_packet(now)
        if target <= 0 or not adjacent:
            return False
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Dulled_Weapon_ID,
            target_agent_id=int(target),
            extra_condition=lambda tid=int(target): bool(
                Agent.IsValid(tid) and Agent.IsAlive(tid)
                and self._dulled_weapon_core_safe()),
            log=False, aftercast_delay=250,
        )
        if did_cast:
            self._last_dulled_cast_tick = int(now)
            for enemy in adjacent:
                self._dulled_recent_coverage[int(enemy)] = int(now + DULLED_COVERAGE_GUARD_MS)
            self._log_st_aoe_event(
                'ST_DULLED_WEAPON_CLUSTER', target_id=int(target),
                affected_candidates=len(adjacent),
                unhexed_before=int(stats.get('unhexed', 0)),
                physical_before=int(stats.get('physical', 0)),
                attackers=int(stats.get('attackers', 0)),
                packet_size=int(stats.get('packet_size', 0)),
                remaining_energy=round(float(current-energy_cost), 2),
                core_reserved=round(float(reserve), 2),
                policy='protect_spirits_first_no_refresh_spam_aoe_hex',
            )
            return True
        return False

    def _cast_binding_chains_for_roj(self, *, player_energy_pct: float) -> bool:
        """Snare the RoJ opening, or re-snare foes trying to leave the carpet."""
        if False:
            yield
        if not self.IsSkillEquipped(Binding_Chains_ID):
            return False
        if not self.IsInAggro() or float(player_energy_pct) < 0.35:
            return False
        if not self.CanCastSkillID(Binding_Chains_ID):
            return False
        if not self._st_optional_energy_budget_ok(Binding_Chains_ID, label="Binding_Chains"):
            return False

        anchor, members = self._get_roj_team_focus_packet()
        if anchor <= 0 or len(members) < 2:
            return False
        roj_coverage = int(self._roj_cluster_coverage_count(anchor))
        moving_members = 0
        for member_id in members:
            try:
                if Agent.IsMoving(int(member_id)):
                    moving_members += 1
            except Exception:
                continue
        # The first two RoJs are the ideal opening window. If core-spirit work
        # delayed the ST, Binding Chains is still useful later only while at
        # least one foe is actually trying to leave the ground fields.
        opening_window = roj_coverage <= ROJ_BINDING_OPENING_MAX_COVERAGE
        if not opening_window and moving_members <= 0:
            return False
        reservation = reserve_binding_chains(
            anchor_id=int(anchor),
            member_ids=members,
            role="st",
        )
        if reservation is None:
            return False

        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Binding_Chains_ID,
            target_agent_id=int(anchor),
            extra_condition=lambda: self._binding_chains_target_still_valid(int(anchor)),
            log=False,
            aftercast_delay=250,
        )
        if did_cast:
            register_binding_chains_fired(
                reservation,
                source="st_after_core_spirits",
            )
            self._log_st_aoe_event(
                "ST_BINDING_CHAINS_ROJ_PACKET",
                target_id=int(anchor),
                packet_size=int(len(members)),
                packet_key=int(reservation.packet_key),
                roj_coverage_before=int(roj_coverage),
                moving_members=int(moving_members),
                opening_window=bool(opening_window),
                packet_members=",".join(str(int(agent_id)) for agent_id in members),
                policy="st_primary_coordinated_after_core_opening_or_moving_packet",
            )
            return True
        release_binding_chains_reservation(
            reservation,
            reason="st_cast_command_rejected_or_focus_changed",
        )
        return False

    def _cast_evas_on_roj_focus(self, *, player_energy_pct: float) -> bool:
        """Keep the ST's only foe-targeted PvE summon on the team packet."""
        if False:
            yield
        if not self.IsSkillEquipped(Ebon_Vanguard_Assassin_Support_ID):
            return False
        if float(player_energy_pct) < 0.40:
            return False
        if not self.CanCastSkillID(Ebon_Vanguard_Assassin_Support_ID):
            return False
        if not self._st_optional_energy_budget_ok(Ebon_Vanguard_Assassin_Support_ID, label="EVAS"):
            return False

        anchor, _members = self._get_roj_team_focus_packet()
        if anchor <= 0:
            return False
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Ebon_Vanguard_Assassin_Support_ID,
            target_agent_id=int(anchor),
            extra_condition=lambda: int(self._get_roj_team_focus_packet()[0]) == int(anchor),
            log=False,
            aftercast_delay=250,
        )
        if did_cast:
            self._log_st_aoe_event(
                "ST_EVAS_TEAM_FOCUS",
                target_id=int(anchor),
                policy="same_authoritative_packet_and_cleanup_no_local_fallback",
            )
            return True
        return False

    def _idle_weapon_autoattack(self) -> bool:
        """Free ST DPS only after the entire protection/support rotation is idle.

        Intentionally weapon-type agnostic: a staff, spear or wand may attack.
        This function remains the final action in the ST rotation, so it never
        gets priority over Soul Twisting, spirits, cleanses, support or energy.
        """
        try:
            from Py4GWCoreLib import get_game_tick
            now = int(get_game_tick() or 0)
        except Exception:
            now = 0
        if now > 0 and now - int(self._last_idle_weapon_attack_tick or 0) < 650:
            return False
        if not self.IsInAggro():
            return False

        target_id = int(self._pick_idle_weapon_target() or 0)
        if target_id <= 0:
            return False
        try:
            if not Agent.IsValid(target_id) or not Agent.IsAlive(target_id):
                return False
        except Exception:
            return False

        try:
            if int(Player.GetTargetID() or 0) != target_id:
                yield from Routines.Yield.Agents.ChangeTarget(target_id)
            Player.Interact(target_id, False)
            self._last_idle_weapon_attack_tick = int(now)
            self._last_idle_weapon_target_id = int(target_id)
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "ST_IDLE_WEAPON_ATTACK",
                    target_id=int(target_id),
                    splinter_active=bool(
                        Routines.Checks.Agents.HasEffect(
                            Player.GetAgentID(),
                            Splinter_Weapon_ID,
                            exact_weapon_spell=True,
                        )
                    ),
                    policy="only_after_full_st_rotation_idle_weapon_agnostic",
                )
            except Exception:
                pass
            return True
        except Exception:
            return False

    @staticmethod
    def _shared_account_skill_ids(account) -> set[int]:
        try:
            skillbar = getattr(getattr(account, "AgentData", None), "Skillbar", None)
            return {
                int(getattr(skill, "Id", 0) or 0)
                for skill in (getattr(skillbar, "Skills", ()) or ())
                if int(getattr(skill, "Id", 0) or 0) > 0
            }
        except Exception:
            return set()

    def _real_party_member_ids(self) -> list[int]:
        """Real party players/heroes/henchmen only; never pets/minions/spirits."""
        ids: set[int] = set()
        try:
            from Py4GWCoreLib.Party import Party
            for member in Party.GetPlayers() or []:
                login_number = int(getattr(member, "login_number", 0) or 0)
                if login_number > 0:
                    aid = int(Party.Players.GetAgentIDByLoginNumber(login_number) or 0)
                    if aid > 0:
                        ids.add(aid)
            for hero in Party.GetHeroes() or []:
                aid = int(getattr(hero, "agent_id", 0) or 0)
                if aid > 0:
                    ids.add(aid)
            for henchman in Party.GetHenchmen() or []:
                aid = int(getattr(henchman, "agent_id", 0) or 0)
                if aid > 0:
                    ids.add(aid)
        except Exception:
            try:
                ids.add(int(Player.GetAgentID() or 0))
            except Exception:
                pass
        return sorted(aid for aid in ids if aid > 0)

    def _vital_weapon_role_sets(self, party_ids: set[int]) -> tuple[set[int], set[int]]:
        """Resolve Keystone accounts and the SoJ Monk from their published bars."""
        keystone_ids: set[int] = set()
        soj_ids: set[int] = set()
        try:
            for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
                if not getattr(account, "IsSlotActive", False) or getattr(account, "IsIsolated", False):
                    continue
                agent_data = getattr(account, "AgentData", None)
                aid = int(getattr(agent_data, "AgentID", 0) or 0)
                if aid <= 0 or aid not in party_ids:
                    continue
                skills = self._shared_account_skill_ids(account)
                if int(Keystone_Signet_ID or 0) > 0 and int(Keystone_Signet_ID) in skills:
                    keystone_ids.add(aid)
                if int(Signet_of_Judgment_ID or 0) > 0 and int(Signet_of_Judgment_ID) in skills:
                    soj_ids.add(aid)
        except Exception:
            pass
        return keystone_ids, soj_ids

    @staticmethod
    def _ally_primary_profession_id(agent_id: int) -> int:
        try:
            primary, _secondary = Agent.GetProfessions(int(agent_id))
            return int(getattr(primary, "value", primary) or 0)
        except Exception:
            return 0

    def _ally_within_spellcast(self, agent_id: int) -> bool:
        try:
            px, py = Player.GetXY()
            ax, ay = Agent.GetXY(int(agent_id))
            dx = float(px) - float(ax)
            dy = float(py) - float(ay)
            return (dx * dx + dy * dy) ** 0.5 <= float(Range.Spellcast.value)
        except Exception:
            return False

    def _pick_vital_weapon_target(self) -> tuple[int, dict]:
        """Pick an actually pressured non-HR party member for Vital Weapon.

        The extra maximum health is reactive support, not permanent upkeep. A
        sharply dropping/low ally wins first; when pressure is comparable we
        prefer Keystone Mesmers, then the SoJ Monk, then the ST itself, then the
        rest of the real party. The HR Paragon is a hard exclusion so Splinter
        Weapon always owns that weapon-spell slot.
        """
        party_ids = set(self._real_party_member_ids())
        if not party_ids:
            return 0, {}
        try:
            self.UpdatePartyHealthMonitor(sample_interval_ms=120, window_ms=1100)
        except Exception:
            pass

        keystone_ids, soj_ids = self._vital_weapon_role_sets(party_ids)
        self_id = int(Player.GetAgentID() or 0)
        paragon_id = int(getattr(Profession.Paragon, "value", Profession.Paragon))
        candidates: list[tuple[tuple, int, dict]] = []
        for aid in party_ids:
            aid = int(aid)
            try:
                if not Agent.IsValid(aid) or not Agent.IsAlive(aid):
                    continue
            except Exception:
                continue
            # HR Para never receives Vital Weapon: Splinter Weapon is reserved
            # for that character and weapon spells must not fight each other.
            if self._ally_primary_profession_id(aid) == paragon_id:
                continue
            if not self._ally_within_spellcast(aid):
                continue
            try:
                if Agent.IsWeaponSpelled(aid):
                    continue
            except Exception:
                pass
            try:
                hp = float(Agent.GetHealth(aid) or 0.0)
            except Exception:
                hp = 1.0
            try:
                drop = float(self.GetPartyHealthDelta(aid) or 0.0)
            except Exception:
                drop = 0.0

            # No prophylactic full-health upkeep. Vital is only spent when the
            # ally is already taking meaningful damage or has just been spiked.
            # Stage 2.59: reserve the HP cushion for actual low HP or a fresh
            # substantial spike. A healed 94-100% ally with an old delta is
            # not a reason to delay emergency ST spirit refresh.
            pressured = bool(hp <= 0.80 or (hp <= 0.90 and drop >= 0.07))
            if not pressured:
                continue

            if aid in keystone_ids:
                role = "keystone"
                role_rank = 0
            elif aid in soj_ids:
                role = "soj"
                role_rank = 1
            elif aid == self_id:
                role = "st"
                role_rank = 2
            else:
                role = "party"
                role_rank = 3

            # Severe HP/spike beats role preference. At equal pressure, protect
            # the signet core first as requested.
            urgency = 0 if hp <= 0.62 else (1 if (hp <= 0.76 or drop >= 0.09) else 2)
            rank = (urgency, float(hp), -float(drop), role_rank, aid)
            candidates.append((rank, aid, {"hp": hp, "drop": drop, "role": role}))

        if not candidates:
            return 0, {}
        candidates.sort(key=lambda row: row[0])
        _rank, target_id, meta = candidates[0]
        return int(target_id), dict(meta)

    def _cast_vital_weapon_pressure_filler(self, *, player_energy_pct: float) -> bool:
        """Reactive max-HP weapon support, strictly after the ST core package."""
        if False:
            yield
        if not self.IsSkillEquipped(Vital_Weapon_ID):
            return False
        if not self.IsInAggro() or float(player_energy_pct) < 0.45:
            return False
        if not self.CanCastSkillID(Vital_Weapon_ID):
            return False
        if not self._st_optional_energy_budget_ok(Vital_Weapon_ID, label="Vital_Weapon"):
            return False
        # EXP36/E13: do not issue a second direct weapon request while the
        # previous one can still be sitting in the local dispatch/aftercast
        # window and its effect has not propagated yet.
        now_tick = int(self._game_tick() or 0)
        last_tick = int(getattr(self, "_last_vital_weapon_cast_tick", 0) or 0)
        if now_tick > 0 and last_tick > 0 and now_tick - last_tick < 650:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "ST_VITAL_WEAPON_DEDUPE_BLOCK",
                    ms_since_request=max(0, int(now_tick - last_tick)),
                    previous_target_id=int(getattr(self, "_last_vital_weapon_target_id", 0) or 0),
                    hr_excluded=True,
                    policy="exp36_stage3_e13_dispatch_guard_observation",
                )
            except Exception:
                pass
            return False

        target_id, meta = self._pick_vital_weapon_target()
        if target_id <= 0:
            return False

        try:
            slot = int(GLOBAL_CACHE.SkillBar.GetSlotBySkillID(Vital_Weapon_ID) or 0)
        except Exception:
            slot = 0
        if not (1 <= slot <= 8):
            return False
        # Revalidate immediately before the low-level request. The target may
        # have moved or acquired another weapon spell since selection.
        try:
            if not Agent.IsValid(int(target_id)) or not Agent.IsAlive(int(target_id)):
                return False
        except Exception:
            return False
        if not self._ally_within_spellcast(int(target_id)):
            return False
        try:
            if Agent.IsWeaponSpelled(target_id):
                return False
        except Exception:
            pass

        # Direct ally-target dispatch avoids generic weapon-spell heuristics and
        # mirrors the proven SoJ Splinter path. Core spirits are rechecked before
        # this helper every tick, so Vital can never postpone a missing spirit.
        try:
            GLOBAL_CACHE.SkillBar.UseSkill(int(slot), target_agent_id=int(target_id), aftercast_delay=180)
            self.SetTickSuccess()
            did_cast = True
        except Exception:
            did_cast = False
        if not did_cast:
            return False

        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "ST_VITAL_WEAPON_PRESSURE",
                target_id=int(target_id),
                target_role=str(meta.get("role", "party")),
                target_hp=round(float(meta.get("hp", 1.0)), 3),
                health_drop=round(float(meta.get("drop", 0.0)), 3),
                energy_pct=round(float(player_energy_pct), 3),
                hr_excluded=True,
                policy="after_core_spirits_pressure_only_keystone_soj_st_then_party",
            )
        except Exception:
            pass
        self._last_vital_weapon_cast_tick = int(self._game_tick() or 0)
        self._last_vital_weapon_target_id = int(target_id)
        return True

    def _dangerous_caster_precast_needed(self) -> bool:
        try:
            from Py4GWCoreLib.Builds.Skills.CombatSense import dangerous_aoe_caster_cluster
            dangerous, _anchor, _members = dangerous_aoe_caster_cluster(
                range_value=Range.Spellcast.value,
                minimum_dangerous_casters=2,
            )
            return bool(dangerous)
        except Exception:
            return False

    def _cast_wisdom_mesmer_core(self):
        """Optional ST-carried Wisdom; only nearby Mesmers decide placement."""
        if False:
            yield
        if not self.IsSkillEquipped(Ebon_Battle_Standard_of_Wisdom_ID):
            return False
        if not self.IsInAggro() or not self.CanCastSkillID(Ebon_Battle_Standard_of_Wisdom_ID):
            return False
        if not self._st_optional_energy_budget_ok(Ebon_Battle_Standard_of_Wisdom_ID, label="Wisdom"):
            return False
        now = self._game_tick()
        started = int(self._mesmerway_wisdom_combat_started_ms or 0)
        elapsed = int(max(0, now - started)) if now > 0 and started > 0 else 0
        try:
            from Py4GWCoreLib.Builds.Skills import MesmerwayCoordination as MWC
            ready, living_mesmers, mesmers_in_area, required = MWC.wisdom_mesmer_readiness(
                int(Player.GetAgentID() or 0), elapsed
            )
        except Exception:
            return False
        if not ready or not MWC.reserve_wisdom_cast():
            return False
        did = yield from self.CastSkillID(
            skill_id=Ebon_Battle_Standard_of_Wisdom_ID,
            target_agent_id=int(Player.GetAgentID() or 0),
            log=False,
            aftercast_delay=250,
        )
        if did:
            MWC.register_wisdom_cast()
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "ST_WISDOM_POST_CONTACT",
                    elapsed_ms=int(elapsed),
                    living_mesmers=int(living_mesmers),
                    mesmers_in_area=int(mesmers_in_area),
                    required_mesmers=int(required),
                    policy="mesmer_only_area_5_at_1500_4_at_1800_3_at_2100_nonmesmers_ignored",
                )
            except Exception:
                pass
        return bool(did)

    def _run_local_skill_logic(self):
        try:
            from Py4GWCoreLib.Builds.Skills import TeamMovementPolicy
            TeamMovementPolicy.register_local_role("st")
        except Exception:
            pass
        refresh_aoe_danger_zones()
        # Stage 1.52: re-enable the proven KeySoJWay EoE runtime observer.
        # This path is read-only and only produces local activation/recharge proof.
        self._observe_edge_of_extinction_runtime()

        # Meteor Shower and other knockdown fields can prevent the Ritualist
        # from ever reaching safety.  Use the instant self-protection first when
        # available, then move on the next tick.  Pure damage fields do not need
        # this detour; the ST leaves them immediately.
        aoe_context = get_player_active_aoe_context(padding=70.0, critical_only=True)
        if aoe_context is not None:
            control = str(aoe_context.get("control", "damage"))
            try:
                from Py4GWCoreLib.Builds.Skills import SimplePowerSettings
                preescape_iau_enabled = SimplePowerSettings.is_feature_enabled(
                    "st_aoe_preescape_knockdown_immunity", True
                )
            except Exception:
                preescape_iau_enabled = True
            if (
                preescape_iau_enabled
                and control in ("repeated_knockdown", "knockdown")
                and int(aoe_context.get("remaining_ms", 0) or 0) > 650
                and self.IsSkillEquipped(I_Am_Unstoppable_ID)
                and Routines.Checks.Skills.CanCast()
                and not Routines.Checks.Agents.HasEffect(Player.GetAgentID(), I_Am_Unstoppable_ID)
            ):
                if (yield from self.skills.Any.NoAttribute.I_Am_Unstoppable()):
                    self._log_st_aoe_event(
                        "ST_AOE_PREESCAPE_IAU",
                        skill_id=int(aoe_context.get("skill_id", 0) or 0),
                        control=control,
                        remaining_ms=int(aoe_context.get("remaining_ms", 0) or 0),
                    )
                    return True

        # While still inside the field or travelling to the escape point, AoE
        # movement owns the tick.  Once the stable safe point is reached, allow
        # this ST rotation to run even though normal Follow remains suspended.
        try:
            from Py4GWCoreLib.Builds.Skills import SimplePowerSettings
            safe_hold_setup_enabled = SimplePowerSettings.is_feature_enabled(
                "st_aoe_safe_hold_spirit_setup", True
            )
        except Exception:
            safe_hold_setup_enabled = True

        if avoid_active_aoe_if_needed(
            role="st",
            allow_actions_at_safe_hold=bool(safe_hold_setup_enabled),
        ):
            return True

        safe_aoe_hold = bool(
            safe_hold_setup_enabled and is_aoe_escape_safe_hold_active(role="st")
        )

        if not Routines.Checks.Skills.CanCast():
            yield from Routines.Yield.wait(100)
            return False

        snapshot = self._get_bar_snapshot()
        # A stale AoE safe-hold marker is not an enemy: never bypass the travel
        # gate solely because an old escape/reposition state is still present.
        # Live AoE/combat pressure is handled by the threat-aware decision above.
        if not snapshot.pre_spirit_setup:
            return False

        # Emergency cleanse always wins. Do not let long spirit casts delay a
        # dangerous hex removal when the team is already near combat.
        if (yield from self.skills.Monk.NoAttribute.Remove_Hex(min_priority=HexRemovalPriority.HIGH)):
            return True

        # Stage 18 / E15c: once an urgent Soul Twisting refresh was spent for a
        # specific missing core spirit, finish that exact repair before normal
        # setup/fillers can consume the next action window.
        if (yield from self._e15_service_pending_followup_commit()):
            return True

        # Stage 2.36: if a core spirit already gave Soul Twisting a bounded
        # second chance, preserve that ownership across scheduler ticks.  This
        # closes the live 2.35 race where Displacement armed at 533 ms remaining
        # but a newly critical Shelter stole the refresh 657 ms later.
        if (yield from self._e15_service_armed_second_chance_owner()):
            return True

        # Stage 2.37: once the 650 ms hard second-chance window ends, keep only
        # ownership of the next Soul-Twisting refresh until the observed recharge
        # should be ready. This call intentionally returns False while ST is still
        # unavailable so the rest of the defensive/utility rotation can proceed.
        if (yield from self._e15_service_st_refresh_reservation()):
            return True

        # EXP36 Stage 11 / Astra E15: acute core protection retains first refusal.
        # Stage 2.19 / P1-D: after an impossible exact follow-up is released, give
        # the other core spirits immediate first refusal before optional utility.
        # This also keeps the normal acute-protection ordering deterministic.
        # Stage 2.42 observes all three core gaps before choosing an action. The
        # normal order stays Shelter -> Union -> Displacement, but a gap that has
        # remained actionable for >=9 s gets first refusal so Displacement cannot
        # be starved for 30-40 s by a stream of fresh higher-slot gaps.
        for _core_model, _core_skill, _core_label in self._e15_core_service_plan():
            if (yield from self._e15_service_protective_spirit(
                _core_model, _core_skill, label=_core_label
            )):
                return True
        # Compatibility markers for historical regression tests; Stage 2.42
        # executes the dynamic loop above rather than these old fixed-order calls:
        # if (yield from self._e15_service_protective_spirit(SpiritModelID.SHELTER, Shelter_ID, label="Shelter")):
        # if (yield from self._e15_service_protective_spirit(SpiritModelID.UNION, Union_ID, label="Union")):
        # if (yield from self._e15_service_protective_spirit(SpiritModelID.DISPLACEMENT, Displacement_ID, label="Displacement")):

        # Hochnäsigkeit / Air of Superiority should be up before kills start.
        # It is preferred over Mighty Was Vorizun for this setup because BiP +
        # Soul Twisting normally covers energy, while Air can accelerate the
        # whole bar during kill chains.
        if (
            not safe_aoe_hold
            and self.IsSkillEquipped(Air_of_Superiority_ID)
            and (yield from self.skills.Any.PvE.Air_of_Superiority())
        ):
            return True

        # Outside an AoE escape episode, use IAU proactively under pressure.
        # During safe hold it would only delay the core spirit rebuild.
        if (
            not safe_aoe_hold
            and snapshot.in_aggro
            and (yield from self.skills.Any.NoAttribute.I_Am_Unstoppable())
        ):
            return True

        if safe_aoe_hold and (yield from self._cast_summon_spirits_at_safe_aoe_hold()):
            return True

        # Travel/post-combat follow only. During combat the core protection
        # package is deliberately allowed to remain behind the front line while
        # its effects still cover the fight.
        if not safe_aoe_hold and (yield from self._cast_summon_spirits_for_follow()):
            return True

        # Stage 2.05: when approaching the next pull, establish Winter before
        # ordinary boon/support setup. The acute core-protection repairs above
        # still win, but a missing frost field no longer waits behind several
        # optional pre-combat actions and then appears only after aggro starts.
        if (yield from self._cast_winter_preaggro_first_refusal(snapshot)):
            return True

        # HR Mesmerway ST preparation. Boon of Creation and one equipped
        # creature-creation support enchantment are prepared BEFORE the normal
        # spirit package. Explosive Growth is an offensive alternative to
        # Spirit's Gift: it triggers when the spirit is CREATED, not when that
        # spirit is attacked or destroyed. Acute E15 protection repairs above
        # still outrank all setup enchantments.
        if (yield from self._cast_precombat_self_enchantment_if_missing(
            Boon_of_Creation_ID, label="Boon_of_Creation"
        )):
            return True
        # Explosive Growth is deliberately reactive-only: prepare it when an
        # enemy has ALREADY reached the backline ST and the unchanged core
        # protection policy is already due to create Shelter/Union/Displacement.
        # It never creates an extra spirit and never changes spirit placement.
        if (
            self.IsSkillEquipped(Explosive_Growth_ID)
            and self._explosive_growth_has_damage_target()
            and self._normal_core_spirit_creation_due(snapshot)
            and (yield from self._cast_precombat_self_enchantment_if_missing(
                Explosive_Growth_ID, label="Explosive_Growth"
            ))
        ):
            self._log_st_aoe_event(
                "ST_EXPLOSIVE_GROWTH_CAST",
                policy="enemy_already_near_st_and_normal_core_creation_due_no_reposition",
            )
            return True

        # Spirit's Gift retains its existing support behavior when equipped.
        # The user may replace it with Explosive Growth; neither is required.
        if (yield from self._cast_precombat_self_enchantment_if_missing(
            Spirits_Gift_ID, label="Spirits_Gift"
        )):
            return True

        # Core ST setup. Reuse the proven Shelter/Union/Displacement coverage
        # behavior across pulls and rebuild only the individual missing spirit.
        if (yield from self.skills.Ritualist.SpawningPower.Soul_Twisting(
            allow_precast=bool(snapshot.pre_spirit_setup)
        )):
            return True

        # Stage 2.00 audit carry-forward: this headroom is consumed by the
        # missing-core scan below, so compute it before its first read.  Keep the
        # otherwise fast Stage 1.72 ST behavior unchanged.
        core_coverage_headroom = (
            PRE_ENGAGE_CORE_COVERAGE_HEADROOM
            if snapshot.pre_spirit_setup and not snapshot.in_aggro
            else E15_COVERAGE_HEADROOM
        )

        # Stage 1.67: never plant a normal/pre-engage core ritual while Soul
        # Twisting is absent merely because the elite itself is still recharging.
        # A raw Shelter/Union/Displacement cast enters its long native recharge;
        # if the party then moves or that spirit dies, later ST activation cannot
        # retroactively reset that already-running cooldown. Acute E15 repair
        # above keeps its existing direct-cast fallback, so real combat pressure
        # can still choose immediate protection over recharge hygiene.
        normal_core_st_active = int(self._e15_soul_twisting_remaining_ms()) > 0
        if not normal_core_st_active:
            missing_core_labels = []
            for _model, _sid, _label in (
                (SpiritModelID.SHELTER, Shelter_ID, "Shelter"),
                (SpiritModelID.UNION, Union_ID, "Union"),
                (SpiritModelID.DISPLACEMENT, Displacement_ID, "Displacement"),
            ):
                if int(_sid or 0) <= 0 or not self.IsSkillEquipped(int(_sid)):
                    continue
                if not self._core_spirit_effect_still_covers(
                    _model, coverage_headroom=core_coverage_headroom
                ):
                    missing_core_labels.append(str(_label))
            now = int(self._game_tick() or 0)
            if (
                missing_core_labels
                and (
                    int(self._e15_last_core_wait_log_ms or 0) <= 0
                    or now - int(self._e15_last_core_wait_log_ms or 0) >= 1500
                )
            ):
                self._e15_last_core_wait_log_ms = int(now)
                self._log_st_aoe_event(
                    "ST_CORE_WAIT_FOR_SOUL_TWISTING",
                    missing=",".join(missing_core_labels),
                    soul_twisting_recharge_ms=int(self._e15_skill_recharge_ms(Soul_Twisting_ID)),
                    in_aggro=bool(snapshot.in_aggro),
                    policy="stage1_67_normal_core_never_enters_native_recharge_without_st",
                )

        # ST self-energy: Spirit Siphon is optional/supported. Use it before the
        # expensive core rebuild only when energy is actually becoming limiting.
        # Emergency cleanse, AoE escape and Soul Twisting itself remain above it.
        if (
            self.IsSkillEquipped(Spirit_Siphon_ID)
            and snapshot.player_energy_pct <= 0.65
            and (yield from self.skills.Ritualist.ChannelingMagic.Spirit_Siphon(max_self_energy_pct=0.65))
        ):
            self._log_st_aoe_event(
                "ST_SPIRIT_SIPHON",
                energy_before=f"{float(snapshot.player_energy_pct):.4f}",
                policy="self_energy_before_core_rebuild",
            )
            return True

        # Optional spirit heal: only if Summon Spirits is equipped and core
        # spirits are already planted, damaged, and still under enemy pressure.
        # Missing core spirits are handled by the normal Shelter/Union/etc. recast
        # chain below.
        if (yield from self._cast_summon_spirits_for_core_heal()):
            return True

        # Dangerous caster packet: accelerate defensive spirit setup.
        # This does not gate Para/Mesmer offense; ST simply services protection first.
        if normal_core_st_active and self._dangerous_caster_precast_needed():
            if (
                self.IsSkillEquipped(Shelter_ID)
                and not self._core_spirit_effect_still_covers(
                    SpiritModelID.SHELTER,
                    coverage_headroom=core_coverage_headroom,
                )
                and (yield from self.skills.Ritualist.Communing.Shelter())
            ):
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event("DANGEROUS_CASTER_ST_PRECAST", skill="Shelter")
                except Exception:
                    pass
                return True
            if (
                self.IsSkillEquipped(Union_ID)
                and not self._core_spirit_effect_still_covers(
                    SpiritModelID.UNION,
                    coverage_headroom=core_coverage_headroom,
                )
                and (yield from self.skills.Ritualist.Communing.Union())
            ):
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event("DANGEROUS_CASTER_ST_PRECAST", skill="Union")
                except Exception:
                    pass
                return True
            if (
                self.IsSkillEquipped(Displacement_ID)
                and not self._core_spirit_effect_still_covers(
                    SpiritModelID.DISPLACEMENT,
                    coverage_headroom=core_coverage_headroom,
                )
                and (yield from self.skills.Ritualist.Communing.Displacement())
            ):
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event("DANGEROUS_CASTER_ST_PRECAST", skill="Displacement")
                except Exception:
                    pass
                return True

        if (
            normal_core_st_active
            and not self._core_spirit_effect_still_covers(
                SpiritModelID.SHELTER,
                coverage_headroom=core_coverage_headroom,
            )
            and (yield from self.skills.Ritualist.Communing.Shelter())
        ):
            return True

        if (
            normal_core_st_active
            and not self._core_spirit_effect_still_covers(
                SpiritModelID.UNION,
                coverage_headroom=core_coverage_headroom,
            )
            and (yield from self.skills.Ritualist.Communing.Union())
        ):
            return True

        if (
            normal_core_st_active
            and not self._core_spirit_effect_still_covers(
                SpiritModelID.DISPLACEMENT,
                coverage_headroom=core_coverage_headroom,
            )
            and (yield from self.skills.Ritualist.Communing.Displacement())
        ):
            return True

        # Optional emergency Spirit Light: lower restoration rank; the helper
        # enforces spirit/sacrifice safety. Never runs before Shelter/Union/
        # Displacement because it sits below the full core renewal sequence.
        if (snapshot.in_aggro and self.IsSkillEquipped(Spirit_Light_ID)
                and self._st_optional_energy_budget_ok(Spirit_Light_ID, label="Spirit_Light")
                and (yield from self.skills.Ritualist.RestorationMagic.Spirit_Light(
                    health_threshold=0.35))):
            return True

        # Stage 2.02 Urgoz Frost package: Winter replaces EoE on the recommended
        # ST bar. Core protection above always wins; Winter is planted only once
        # the ST package has had its normal first refusal. A PI/R backup may take
        # over only when this carrier cannot refresh the shared spatial lease.
        if (yield from self._cast_winter_primary(
            close_to_aggro=bool(snapshot.close_to_aggro),
            in_aggro=bool(snapshot.in_aggro),
        )):
            return True

        # Earthbind is legacy-only; Dulled Weapon is serviced much later,
        # after the protective package and creation support.
        # Stage 1.56 restores the useful KeySoJWay first-refusal policy: core
        # spirits are attempted immediately above, but EoE is not starved merely
        # because one core spirit is recharging/temporarily absent.  A pending or
        # immediately executable acute repair still wins, and the energy reserve
        # remains protected inside _cast_edge_of_extinction_large_group().
        if (yield from self._cast_edge_of_extinction_large_group(
            player_energy_pct=float(snapshot.player_energy_pct),
            in_aggro=bool(snapshot.in_aggro),
            phase="combat" if snapshot.in_aggro else "pre_engage",
        )):
            return True

        # Offensive support starts only after Shelter/Union/Displacement
        # have had their normal defensive first refusal. Binding Chains targets the same authoritative
        # packet as RoJ; its nearby snare keeps moving foes inside the five-second
        # ground fields without ever replacing the ST's defensive obligations.
        if (yield from self._cast_binding_chains_for_roj(
            player_energy_pct=float(snapshot.player_energy_pct),
        )):
            return True

        # Once the important core spirits exist, protect them. Armor targeting
        # avoids repeatedly refreshing the same spirit while another core spirit
        # is still unprotected.
        if (
            self._st_optional_energy_budget_ok(Armor_of_Unfeeling_ID, label="Armor_of_Unfeeling")
            and (yield from self.skills.Ritualist.Communing.Armor_of_Unfeeling())
        ):
            return True

        # Maintain general creation support only after the core package is
        # safe. Explosive Growth is intentionally NOT maintained here: it is
        # prepared only immediately before an already-due normal core-spirit
        # creation while an enemy is already on the backline ST.
        if (
            snapshot.player_energy_pct >= 0.35
            and self._st_optional_energy_budget_ok(Boon_of_Creation_ID, label="Boon_of_Creation")
            and (yield from self.skills.Ritualist.SpawningPower.Boon_of_Creation())
        ):
            return True
        if (
            snapshot.player_energy_pct >= 0.35
            and self._st_optional_energy_budget_ok(Spirits_Gift_ID, label="Spirits_Gift")
            and (yield from self.skills.Ritualist.SpawningPower.Spirits_Gift())
        ):
            return True

        # Stumpfe Waffe / Dulled Weapon: a 15e cluster hex, optional and
        # deliberately BEHIND Shelter, Union, Displacement, Spirit Light,
        # Armor of Unfeeling and creation support. Never create a spirit gap.
        if (yield from self._cast_dulled_weapon_cluster(
            player_energy_pct=float(snapshot.player_energy_pct),
        )):
            return True

        if (
            snapshot.player_energy_pct >= 0.50
            and (yield from self.skills.Monk.NoAttribute.Remove_Hex(
                min_priority=HexRemovalPriority.MEDIUM,
            ))
        ):
            return True

        if not snapshot.in_aggro:
            return False

        # EXP32 reactive support filler. Missing/rebuilding core spirits, Binding
        # Chains, Armor of Unfeeling, Boon and the important cleanse checks above
        # always win. Vital Weapon is therefore only used from true ST downtime.
        if (yield from self._cast_vital_weapon_pressure_filler(
            player_energy_pct=float(snapshot.player_energy_pct),
        )):
            return True

        if (yield from self._cast_evas_on_roj_focus(
            player_energy_pct=float(snapshot.player_energy_pct),
        )):
            return True

        if (yield from self._cast_wisdom_mesmer_core()):
            return True

        if (yield from self.skills.Any.NoAttribute.Breath_of_the_Great_Dwarf()):
            return True

        if snapshot.player_energy_pct >= 0.70 and (yield from self.skills.Monk.NoAttribute.Remove_Hex()):
            return True

        # Absolutely last priority: if every ST/protection/support action above
        # had nothing to do, use the currently equipped weapon for free pressure.
        # Staff is explicitly supported; an attack is never started instead of a
        # needed spirit/support cast because every such action is checked first.
        if (yield from self._idle_weapon_autoattack()):
            return True

        return False
