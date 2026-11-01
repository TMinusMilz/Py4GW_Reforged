from __future__ import annotations

from Py4GWCoreLib import AgentArray, GLOBAL_CACHE, Profession, Range, Routines, Party
from Py4GWCoreLib import BuildMgr
from Py4GWCoreLib.Agent import Agent
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.Skill import Skill
from Py4GWCoreLib.Builds.Any.HeroAI import HeroAI_Build
from Py4GWCoreLib.Builds.Skills import HexRemovalPriority, SkillsTemplate
from Py4GWCoreLib.Builds.Skills.AoEDangerPrediction import (
    avoid_active_aoe_if_needed,
    refresh_aoe_danger_zones,
)
from Py4GWCoreLib.GlobalCache.HexRemovalPriority import (
    cast_hex_removal_and_track,
    get_hexed_ally_for_removal,
)
from Py4GWCoreLib.HeroAI.targeting import GetAllAlliesArray
from Py4GWCoreLib.Builds.Skills.DangerInterruptClaim import (
    claim_best_dangerous_cast,
    get_casting_skill_id as get_danger_casting_skill_id,
    is_dangerous_cast,
    release_interrupt_claim,
    target_still_casting_skill as danger_target_still_casting_skill,
)
from Py4GWCoreLib.Builds.Skills.ExecutionFocus import (
    is_execution_focus_target,
    pick_execution_focus_target,
)
from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import (
    get_team_cluster_anchor, get_team_cluster_members, is_support_or_caster, pick_lamentation_target,
)


Signet_of_Judgment_ID = Skill.GetID("Signet_of_Judgment")
Bane_Signet_ID = Skill.GetID("Bane_Signet")  # German: Siegel des Ruins
Castigation_Signet_ID = Skill.GetID("Castigation_Signet")
Splinter_Weapon_ID = Skill.GetID("Splinter_Weapon")
Soul_Twisting_ID = Skill.GetID("Soul_Twisting")
Lamentation_ID = Skill.GetID("Lamentation")
Spirit_Burn_ID = Skill.GetID("Spirit_Burn")
Burning_ID = Skill.GetID("Burning")
Air_of_Superiority_ID = Skill.GetID("Air_of_Superiority")
Symbol_of_Wrath_ID = Skill.GetID("Symbol_of_Wrath")
Reversal_of_Damage_ID = Skill.GetID("Reversal_of_Damage")
Smite_Condition_ID = Skill.GetID("Smite_Condition")  # current preferred Spirit-Burn replacement
Smite_Hex_ID = Skill.GetID("Smite_Hex")
Deny_Hexes_ID = Skill.GetID("Deny_Hexes")
Divine_Healing_ID = Skill.GetID("Divine_Healing")
Heavens_Delight_ID = Skill.GetID("Heavens_Delight")
Seed_of_Life_ID = Skill.GetID("Seed_of_Life")
Technobabble_ID = Skill.GetID("Technobabble")
Dazed_ID = Skill.GetID("Dazed")

# Match the Keystone/Mesmer SoJ behavior: do not waste the knockdown on a
# single target already on the floor, but allow AoE damage when a packet is big.
SIGNET_OF_JUDGMENT_DAMAGE_CLUSTER_MIN = 2

# Bane Signet is used as a control signet, not as random damage filler. It
# should not steal targets already reserved for the team SoJ chain.
BANE_SIGNET_AVOID_SOJ_CLAIMS = True

# Same Simple Power packet rules as the Keystone Mesmers. All offensive SoJ
# users should naturally spike the same enemy packet, preferably the player's
# current packet, while reserving exact SoJ targets so they do not all hit the
# same enemy inside the packet.
POWER_CLUSTER_MIN_ENEMIES = 2
POWER_CLUSTER_RADIUS = Range.Adjacent.value
POWER_CLUSTER_FILTER_RANGE = Range.Spellcast.value

# Elite-mission override: in Urgoz' Warren the German "Krummrinde" is
# the English "Twisted Bark". These enemies maintain room-wide effects,
# so they should be killed before the normal Simple-Power cluster logic.
ELITE_PRIORITY_TARGET_NAMES = ("twisted bark", "krummrinde", "crooked bark")


class Signet_of_Judgment_Support(BuildMgr):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Signet of Judgment Support",
            required_primary=Profession.Monk,
            template_code="Owgi4wi8YSQJ3DXCGjyZ9xrA",
            # Match the role by its two defining skills. Older project builds
            # required the entire historical bar; one changed support slot could
            # silently make the SoJ controller fail to match, which also disabled
            # our explicit Splinter logic. All other slots are feature-detected.
            required_skills=[
                Signet_of_Judgment_ID,
                Splinter_Weapon_ID,
            ],
            optional_skills=[
                Bane_Signet_ID,
                Symbol_of_Wrath_ID,
                Reversal_of_Damage_ID,
                Smite_Hex_ID,
                Smite_Condition_ID,
                Lamentation_ID,
                Spirit_Burn_ID,  # legacy compatibility; current bar uses Smite Condition
                # Energy-oriented alternative to Bane remains supported by the
                # local rotation when users choose to equip it.
                Castigation_Signet_ID,
                Air_of_Superiority_ID,
                # Old support variants remain match-compatible.
                Deny_Hexes_ID,
                Divine_Healing_ID,
                Heavens_Delight_ID,
                Seed_of_Life_ID,
                Technobabble_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAI_Build(standalone_fallback=True))
        self.SetSkillCastingFn(self._run_local_skill_logic)
        self.skills: SkillsTemplate = SkillsTemplate(self)
        # EXP35: Spirit Burn -> Epidemic is no longer published on dispatch.
        # The SoJ client proves recharge + newly-applied Burning first.
        self._pending_spirit_burn_confirmation: dict[str, object] | None = None

    # ---------------------------------------------------------------------
    # Shared SoJ target coordination.
    # ---------------------------------------------------------------------
    @staticmethod
    def _get_game_tick() -> int:
        try:
            import Py4GW
            return int(Py4GW.Game.get_tick_count64() or 0)
        except Exception:
            return 0

    @staticmethod
    def _slot_recharge_remaining_ms(slot: int) -> int:
        try:
            if not (1 <= int(slot) <= 8):
                return -1
            data = GLOBAL_CACHE.SkillBar.GetSkillData(int(slot))
            return max(0, int(getattr(data, "get_recharge", 0) or 0))
        except Exception:
            return -1

    def _arm_spirit_burn_confirmation(
        self, target_id: int, slot: int, *, already_burning: bool, spirit_near_at_cast: bool
    ) -> None:
        # EXP36/E11: only arm the combo proof when Spirit Burn actually had the
        # mechanical prerequisite for applying Burning. Spirit Burn may still
        # be used as direct damage without a nearby spirit, but that cast cannot
        # authorize Epidemic.
        if not bool(spirit_near_at_cast):
            self._pending_spirit_burn_confirmation = None
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "SOJ_SPIRIT_BURN_COMBO_NOT_ARMED", target_id=int(target_id),
                    reason="no_spirit_in_earshot_at_cast",
                    policy="exp36_e11_verified_burning_prerequisite",
                )
            except Exception:
                pass
            return
        # If the target was already Burning, a later Burning observation cannot
        # prove this specific Spirit Burn supplied the combo condition. Fail closed.
        if bool(already_burning):
            self._pending_spirit_burn_confirmation = None
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "SOJ_SPIRIT_BURN_COMBO_NOT_ARMED", target_id=int(target_id),
                    reason="target_already_burning_before_cast",
                    policy="exp35_confirmed_spirit_burn_only",
                )
            except Exception:
                pass
            return
        now = int(self._get_game_tick() or 0)
        self._pending_spirit_burn_confirmation = {
            "target_id": int(target_id),
            "slot": int(slot),
            "started_tick": int(now),
            "deadline_tick": int(now + 2200) if now > 0 else 0,
        }

    def _poll_spirit_burn_confirmation(self) -> None:
        pending = self._pending_spirit_burn_confirmation
        if not pending:
            return
        target_id = int(pending.get("target_id", 0) or 0)
        slot = int(pending.get("slot", 0) or 0)
        deadline = int(pending.get("deadline_tick", 0) or 0)
        now = int(self._get_game_tick() or 0)
        if target_id <= 0 or not Agent.IsValid(target_id) or not Agent.IsAlive(target_id):
            self._pending_spirit_burn_confirmation = None
            return
        recharge = int(self._slot_recharge_remaining_ms(slot))
        try:
            burning_now = bool(Routines.Checks.Agents.HasEffect(target_id, Burning_ID))
        except Exception:
            burning_now = False
        try:
            burning_remaining_ms = int(
                GLOBAL_CACHE.Effects.GetEffectTimeRemaining(int(target_id), int(Burning_ID)) or 0
            )
        except Exception:
            burning_remaining_ms = 0
        # Keep the shared lease inside the actually observed Burning lifetime.
        # The 250-ms reserve prevents a consumer from starting Epidemic on a
        # condition that is about to expire. A very short Burning observation
        # is not useful enough to publish as combo proof.
        usable_burning_ms = max(0, int(burning_remaining_ms) - 250)
        if recharge > 0 and burning_now and usable_burning_ms >= 1200:
            carrier_posted = 0
            try:
                from Py4GWCoreLib.Builds.Skills.BurningComboCoordination import publish_spirit_burn_carrier
                carrier_posted = int(publish_spirit_burn_carrier(
                    int(target_id), duration_ms=min(3400, int(usable_burning_ms))
                ) or 0)
            except Exception:
                carrier_posted = 0
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "SOJ_SPIRIT_BURN_EFFECT_CONFIRMED", target_id=int(target_id),
                    recharge_ms=int(recharge), burning_remaining_ms=int(burning_remaining_ms),
                    carrier_posted=int(carrier_posted),
                    policy="exp36_recharge_new_burning_and_remaining_lifetime",
                )
                if carrier_posted:
                    CombatDebug.log_event(
                        "SOJ_SPIRIT_BURN_CARRIER_PUBLISHED", target_id=int(target_id),
                        carrier_posted=int(carrier_posted),
                        policy="exp35_publish_only_after_effect_confirmation",
                    )
            except Exception:
                pass
            self._pending_spirit_burn_confirmation = None
            return
        if deadline > 0 and now > 0 and now >= deadline:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "SOJ_SPIRIT_BURN_EFFECT_NOT_CONFIRMED", target_id=int(target_id),
                    recharge_ms=int(recharge), burning_now=bool(burning_now),
                    burning_remaining_ms=int(burning_remaining_ms),
                    policy="no_epidemic_lease_on_failed_or_unproven_spirit_burn",
                )
            except Exception:
                pass
            self._pending_spirit_burn_confirmation = None

    def _get_signet_of_judgment_group_id(self) -> int:
        try:
            party_id = int(GLOBAL_CACHE.Party.GetPartyID() or 0)
            if party_id > 0:
                return party_id

            own_email = str(Player.GetAccountEmail() or "").strip()
            if own_email:
                for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
                    if str(getattr(account, "AccountEmail", "") or "").strip() != own_email:
                        continue
                    return int(getattr(account, "IsolationGroupID", 0) or 0)
        except Exception:
            pass
        return 0

    def _is_signet_of_judgment_target_claimed(self, target_agent_id: int) -> bool:
        if target_agent_id <= 0:
            return True

        try:
            from Py4GWCoreLib.enums_src.Whiteboard_enums import (
                WhiteboardClaimStrength,
                WhiteboardLockKind,
                WhiteboardLockMode,
                WhiteboardReentryPolicy,
            )

            shmem = GLOBAL_CACHE.ShMem
            now_tick = self._get_game_tick()
            if now_tick <= 0:
                return False

            if hasattr(shmem, "SweepExpiredIntents"):
                shmem.SweepExpiredIntents(now_tick)

            return bool(shmem.IsLockBlocked(
                int(WhiteboardLockKind.SKILL_TARGET),
                int(Signet_of_Judgment_ID),
                int(target_agent_id),
                self._get_signet_of_judgment_group_id(),
                str(Player.GetAccountEmail() or "").strip(),
                now_tick,
                int(WhiteboardLockMode.EXCLUSIVE),
                1,
                int(WhiteboardReentryPolicy.NON_REENTRANT),
                int(WhiteboardClaimStrength.HARD),
            ))
        except Exception:
            try:
                now_tick = self._get_game_tick()
                if now_tick <= 0 or not hasattr(GLOBAL_CACHE.ShMem, "IsIntentClaimed"):
                    return False
                return bool(GLOBAL_CACHE.ShMem.IsIntentClaimed(
                    Signet_of_Judgment_ID,
                    int(target_agent_id),
                    self._get_signet_of_judgment_group_id(),
                    str(Player.GetAccountEmail() or "").strip(),
                    now_tick,
                ))
            except Exception:
                return False

    def _claim_signet_of_judgment_target(self, target_agent_id: int) -> bool:
        if target_agent_id <= 0:
            return False
        if self._is_signet_of_judgment_target_claimed(target_agent_id):
            return False

        try:
            from Py4GWCoreLib.enums_src.Whiteboard_enums import (
                WhiteboardClaimStrength,
                WhiteboardLockKind,
                WhiteboardLockMode,
                WhiteboardReentryPolicy,
            )

            shmem = GLOBAL_CACHE.ShMem
            now_tick = self._get_game_tick()
            if now_tick <= 0:
                return True

            expires_at_tick = now_tick + 1750
            if hasattr(shmem, "PostLock"):
                return shmem.PostLock(
                    str(Player.GetAccountEmail() or "").strip(),
                    int(WhiteboardLockKind.SKILL_TARGET),
                    int(Signet_of_Judgment_ID),
                    int(target_agent_id),
                    expires_at_tick,
                    self._get_signet_of_judgment_group_id(),
                    int(WhiteboardLockMode.EXCLUSIVE),
                    1,
                    int(WhiteboardReentryPolicy.NON_REENTRANT),
                    int(WhiteboardClaimStrength.HARD),
                ) != -1

            if hasattr(shmem, "PostIntent"):
                return shmem.PostIntent(
                    str(Player.GetAccountEmail() or "").strip(),
                    int(Signet_of_Judgment_ID),
                    int(target_agent_id),
                    expires_at_tick,
                    self._get_signet_of_judgment_group_id(),
                ) != -1
        except Exception:
            return True

        return True

    @staticmethod
    def _is_enemy_alive_valid(target_agent_id: int) -> bool:
        if target_agent_id <= 0:
            return False
        try:
            return bool(Agent.IsValid(target_agent_id) and Agent.IsAlive(target_agent_id))
        except Exception:
            return False

    @staticmethod
    def _is_target_knocked_down(target_agent_id: int) -> bool:
        try:
            return bool(Routines.Checks.Agents.IsKnockedDown(target_agent_id))
        except Exception:
            return False

    @staticmethod
    def _is_target_attacking(target_agent_id: int) -> bool:
        try:
            return bool(Routines.Checks.Agents.IsAttacking(target_agent_id))
        except Exception:
            return False

    def _count_adjacent_enemies(self, target_agent_id: int) -> int:
        if target_agent_id <= 0:
            return 0
        try:
            target_xy = Agent.GetXY(target_agent_id)
            enemies = AgentArray.GetEnemyArray()
            enemies = AgentArray.Filter.ByDistance(enemies, target_xy, Range.Adjacent.value)
            enemies = AgentArray.Filter.ByCondition(
                enemies,
                lambda agent_id: Agent.IsValid(agent_id) and Agent.IsAlive(agent_id),
            )
            return int(len(enemies))
        except Exception:
            try:
                return int(Routines.Targeting.CountNearbyEnemies(target_agent_id, Range.Adjacent.value) or 0)
            except Exception:
                return 0

    def _distance_to_player(self, target_agent_id: int) -> float:
        try:
            from Py4GWCoreLib.Py4GWcorelib import Utils
            return float(Utils.Distance(Player.GetXY(), Agent.GetXY(target_agent_id)))
        except Exception:
            return 999999.0

    def _is_valid_power_cluster_enemy(self, target_agent_id: int) -> bool:
        try:
            target_agent_id = int(target_agent_id or 0)
            if target_agent_id <= 0:
                return False
            if not Agent.IsValid(target_agent_id) or not Agent.IsAlive(target_agent_id):
                return False
            return self._distance_to_player(target_agent_id) <= float(POWER_CLUSTER_FILTER_RANGE)
        except Exception:
            return False

    def _get_player_enemy_target(self) -> int:
        try:
            target_agent_id = int(Player.GetTargetID() or 0)
        except Exception:
            return 0
        if self._is_valid_power_cluster_enemy(target_agent_id):
            return target_agent_id
        return 0


    def _is_elite_priority_target(self, target_agent_id: int) -> bool:
        if not self._is_valid_power_cluster_enemy(target_agent_id):
            return False
        try:
            name = str(Agent.GetNameByID(int(target_agent_id)) or "").strip().lower()
        except Exception:
            return False
        return any(priority_name in name for priority_name in ELITE_PRIORITY_TARGET_NAMES)

    def _get_elite_priority_target(self) -> int:
        """Hard-focus mission objects like Urgoz Twisted Bark before packs.

        This is intentionally name-based and tiny: it only overrides the
        cluster target if the special target is alive and in normal offensive
        range. Otherwise the usual Simple-Power cluster spike is unchanged.
        """


        player_target = self._get_player_enemy_target()
        if player_target and self._is_elite_priority_target(player_target):
            return int(player_target)

        try:
            from Py4GWCoreLib import AgentArray
            enemies = AgentArray.GetEnemyArray()
            enemies = AgentArray.Filter.ByDistance(enemies, Player.GetXY(), POWER_CLUSTER_FILTER_RANGE)
            enemies = AgentArray.Filter.ByCondition(
                enemies,
                lambda agent_id: self._is_elite_priority_target(int(agent_id)),
            )
            candidates = [int(agent_id) for agent_id in enemies or []]
        except Exception:
            candidates = []

        if not candidates:
            return 0

        candidates.sort(key=lambda agent_id: (
            -self._count_signet_of_judgment_adjacent_enemies(agent_id) if hasattr(self, '_count_signet_of_judgment_adjacent_enemies') else -self._count_adjacent_enemies(agent_id) if hasattr(self, '_count_adjacent_enemies') else -self._count_nearby_alive_enemies(agent_id, POWER_CLUSTER_RADIUS),
            self._distance_to_player(agent_id),
            int(agent_id),
        ))
        return int(candidates[0])

    def _get_power_cluster_anchor(self, *, minimum_enemies: int = POWER_CLUSTER_MIN_ENEMIES) -> int:
        """Use the one authoritative KeySoJway packet/cleanup resolver."""
        try:
            from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import get_team_cluster_anchor
            return int(get_team_cluster_anchor(
                filter_range=POWER_CLUSTER_FILTER_RANGE,
                minimum_enemies=int(minimum_enemies),
                consumer_role="soj_monk",
            ) or 0)
        except Exception:
            return 0


    def _get_power_cluster_members(self, anchor_agent_id: int) -> list[int]:
        if not self._is_valid_power_cluster_enemy(anchor_agent_id):
            return []
        try:
            enemies = AgentArray.GetEnemyArray()
            enemies = AgentArray.Filter.ByDistance(enemies, Agent.GetXY(anchor_agent_id), POWER_CLUSTER_RADIUS)
            enemies = AgentArray.Filter.ByCondition(
                enemies,
                lambda agent_id: self._is_valid_power_cluster_enemy(int(agent_id)),
            )
            members = [int(agent_id) for agent_id in enemies or []]
        except Exception:
            members = [int(anchor_agent_id)]

        if int(anchor_agent_id) not in members:
            members.append(int(anchor_agent_id))

        members = [agent_id for agent_id in members if self._is_valid_power_cluster_enemy(agent_id)]
        members.sort(key=lambda agent_id: (self._distance_to_player(agent_id), int(agent_id)))
        return members

    @staticmethod
    def _get_safe_casting_skill_id(agent_id: int) -> int:
        return get_danger_casting_skill_id(agent_id)

    def _is_safe_dangerous_cast(self, agent_id: int) -> bool:
        return is_dangerous_cast(agent_id)

    def _target_still_casting_safe_skill(self, target_agent_id: int, casting_skill_id: int) -> bool:
        return danger_target_still_casting_skill(target_agent_id, casting_skill_id)

    def _is_high_value_soj_interrupt_enemy(self, agent_id: int) -> bool:
        """Prefer healers and dangerous caster professions for SoJ interrupts."""
        if not self._is_enemy_alive_valid(agent_id):
            return False
        try:
            from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import is_support_or_caster
            return bool(is_support_or_caster(int(agent_id)))
        except Exception:
            return self._is_monk_or_ritualist_enemy(int(agent_id))

    def _pick_dangerous_interrupt_target_for_signet(self, skill_id: int) -> tuple[int, int]:
        # First refusal goes to a dangerous cast from a healer/support/caster.
        # There is no reservation window: if no such cast exists *right now*,
        # _cast_signet_of_judgment immediately falls through to its normal
        # damage/control target in the same controller pass.
        target_id, cast_id = claim_best_dangerous_cast(
            range_value=Range.Spellcast.value,
            interrupter_skill_id=int(skill_id or 0),
            validator=lambda enemy_id, cast_id: (
                self._is_signet_of_judgment_control_target_usable(enemy_id, check_claimed=False)
                and self._is_high_value_soj_interrupt_enemy(enemy_id)
            ),
        )
        if int(target_id or 0) > 0 and int(cast_id or 0) > 0:
            return int(target_id), int(cast_id)

        # Fallback: still allow SoJ to stop another currently dangerous cast.
        return claim_best_dangerous_cast(
            range_value=Range.Spellcast.value,
            interrupter_skill_id=int(skill_id or 0),
            validator=lambda enemy_id, cast_id: self._is_signet_of_judgment_control_target_usable(enemy_id, check_claimed=False),
        )

    @staticmethod
    def _get_enemy_professions_safe(agent_id: int) -> tuple[int, int]:
        try:
            if agent_id <= 0 or not Agent.IsValid(agent_id) or not Agent.IsAlive(agent_id):
                return (0, 0)
            primary, secondary = Agent.GetProfessions(agent_id)
            primary_id = int(getattr(primary, 'value', primary) or 0)
            secondary_id = int(getattr(secondary, 'value', secondary) or 0)
            return (primary_id, secondary_id)
        except Exception:
            return (0, 0)

    def _is_monk_or_ritualist_enemy(self, agent_id: int) -> bool:
        primary, secondary = self._get_enemy_professions_safe(agent_id)
        try:
            monk_id = int(getattr(Profession.Monk, 'value', Profession.Monk))
            ritualist_id = int(getattr(Profession.Ritualist, 'value', Profession.Ritualist))
        except Exception:
            return False
        return primary in (monk_id, ritualist_id) or secondary in (monk_id, ritualist_id)

    def _score_enemy_packet(self, target_agent_id: int, player_pos: tuple[float, float]) -> tuple[int, float]:
        try:
            from Py4GWCoreLib.Py4GWcorelib import Utils
            distance = float(Utils.Distance(player_pos, Agent.GetXY(target_agent_id)))
        except Exception:
            distance = 0.0
        return (-self._count_adjacent_enemies(target_agent_id), distance)

    def _pick_enemy_target(self, validator, *, check_claimed_for_picker: bool = False) -> int:
        try:
            target_agent_id = Routines.Targeting.PickClusteredTarget(
                cluster_radius=Range.Adjacent.value,
                preferred_condition=lambda agent_id: validator(
                    int(agent_id),
                    check_claimed=check_claimed_for_picker,
                ),
                filter_radius=Range.Spellcast.value,
            )
            if target_agent_id:
                return int(target_agent_id)
        except Exception:
            pass

        try:
            player_pos = Player.GetXY()
            enemies = AgentArray.GetEnemyArray()
            enemies = AgentArray.Filter.ByDistance(enemies, player_pos, Range.Spellcast.value)
            enemies = AgentArray.Filter.ByCondition(
                enemies,
                lambda agent_id: validator(int(agent_id), check_claimed=check_claimed_for_picker),
            )
            if not enemies:
                return 0
            enemies = sorted(enemies, key=lambda agent_id: self._score_enemy_packet(int(agent_id), player_pos))
            return int(enemies[0])
        except Exception:
            return 0

    def _is_signet_of_judgment_control_target_usable(self, target_agent_id: int, *, check_claimed: bool = True) -> bool:
        if not self._is_enemy_alive_valid(target_agent_id):
            return False
        if check_claimed and self._is_signet_of_judgment_target_claimed(target_agent_id):
            return False
        return not self._is_target_knocked_down(target_agent_id)

    def _is_signet_of_judgment_damage_target_usable(self, target_agent_id: int, *, check_claimed: bool = False) -> bool:
        if not self._is_enemy_alive_valid(target_agent_id):
            return False
        if check_claimed and self._is_signet_of_judgment_target_claimed(target_agent_id):
            return False
        return self._count_adjacent_enemies(target_agent_id) >= SIGNET_OF_JUDGMENT_DAMAGE_CLUSTER_MIN

    def _is_signet_of_judgment_cast_target_usable(self, target_agent_id: int) -> bool:
        if not self._is_enemy_alive_valid(target_agent_id):
            return False
        if self._is_elite_priority_target(target_agent_id):
            return True
        if is_execution_focus_target(target_agent_id):
            return True
        if self._count_adjacent_enemies(target_agent_id) >= SIGNET_OF_JUDGMENT_DAMAGE_CLUSTER_MIN:
            return True
        return self._is_safe_dangerous_cast(target_agent_id)

    def _rank_signet_of_judgment_cluster_members(self, anchor_agent_id: int) -> list[int]:
        members = self._get_power_cluster_members(anchor_agent_id)
        if not members:
            return []

        def rank(agent_id: int) -> tuple[int, int, int, float, int]:
            claimed = 1 if self._is_signet_of_judgment_target_claimed(agent_id) else 0
            support = 1 if self._is_monk_or_ritualist_enemy(agent_id) else 0
            standing = 1 if not self._is_target_knocked_down(agent_id) else 0
            return (claimed, -support, -standing, self._distance_to_player(agent_id), int(agent_id))

        members.sort(key=rank)
        return members

    def _get_signet_of_judgment_target(self, *, claim_target: bool = False) -> int:
        priority_target = self._get_elite_priority_target()
        if priority_target and self._is_signet_of_judgment_cast_target_usable(priority_target):
            # Mission-priority targets should be deleted first; do not allow the
            # normal SoJ target spread to pull casts away from Twisted Bark.
            if claim_target:
                self._claim_signet_of_judgment_target(priority_target)
            return int(priority_target)

        anchor_agent_id = self._get_power_cluster_anchor()
        if anchor_agent_id:
            for target_agent_id in self._rank_signet_of_judgment_cluster_members(anchor_agent_id):
                if not self._is_signet_of_judgment_cast_target_usable(target_agent_id):
                    continue
                if claim_target and not self._claim_signet_of_judgment_target(target_agent_id):
                    continue
                return int(target_agent_id)

        return self._pick_enemy_target(
            self._is_signet_of_judgment_damage_target_usable,
            check_claimed_for_picker=False,
        )

    def _cast_signet_of_judgment_interrupt_only(self):
        """Use SoJ immediately for a valuable current cast, never reserve it."""
        if not self.IsSkillEquipped(Signet_of_Judgment_ID):
            return False
        if not self.CanCastSkillID(Signet_of_Judgment_ID):
            return False
        if not (self.IsInAggro() or self.IsCloseToAggro()):
            return False

        target_agent_id, casting_skill_id = self._pick_dangerous_interrupt_target_for_signet(
            Signet_of_Judgment_ID
        )
        target_agent_id = int(target_agent_id or 0)
        casting_skill_id = int(casting_skill_id or 0)
        if target_agent_id <= 0 or casting_skill_id <= 0:
            return False

        self._claim_signet_of_judgment_target(target_agent_id)
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Signet_of_Judgment_ID,
            target_agent_id=target_agent_id,
            extra_condition=lambda: (
                self._target_still_casting_safe_skill(target_agent_id, casting_skill_id)
                and self._is_signet_of_judgment_control_target_usable(
                    target_agent_id, check_claimed=False
                )
            ),
            log=False,
            aftercast_delay=250,
        )
        if did_cast:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.register_interrupt_fired(
                    target_agent_id, casting_skill_id, Signet_of_Judgment_ID
                )
                CombatDebug.log_event(
                    "SOJ_MONK_INTERRUPT_CAST",
                    target_id=int(target_agent_id),
                    enemy_skill_id=int(casting_skill_id),
                    high_value_caster=bool(
                        self._is_high_value_soj_interrupt_enemy(target_agent_id)
                    ),
                    policy="interrupt_now_never_reserved",
                )
            except Exception:
                pass
            return True

        release_interrupt_claim(
            target_agent_id,
            casting_skill_id,
            reason="soj_interrupt_only_not_fired",
        )
        return False

    @staticmethod
    def _is_boss_glow_safe(agent_id: int) -> bool:
        try:
            return bool(Agent.HasBossGlow(int(agent_id)))
        except Exception:
            return False

    @staticmethod
    def _daze_remaining_ms(agent_id: int) -> int:
        try:
            if int(Dazed_ID or 0) <= 0:
                return 0
            if not Routines.Checks.Agents.HasEffect(int(agent_id), int(Dazed_ID)):
                return 0
            return max(0, int(GLOBAL_CACHE.Effects.GetEffectTimeRemaining(int(agent_id), int(Dazed_ID)) or 0))
        except Exception:
            return 0

    def _technobabble_new_cc_metrics(self, target_id: int, members: list[int]) -> tuple[int, int, int, int, int, bool]:
        """Return (new_daze, priority_new, impacted, fresh, bosses_impacted, target_is_boss).

        Technobabble's Daze component only applies when the chosen *target* is
        not a boss.  A non-boss adjacent to a boss can therefore be the useful
        center.  Daze with >1.4 s remaining is treated as already covered; an
        expiring Daze may be refreshed because the spell itself takes ~1 s.
        """
        target_id = int(target_id or 0)
        target_is_boss = bool(self._is_boss_glow_safe(target_id))
        try:
            from Py4GWCoreLib.Py4GWcorelib import Utils
            target_xy = Agent.GetXY(int(target_id))
        except Exception:
            return 0, 0, 0, 0, 0, target_is_boss
        impacted: list[int] = []
        for enemy_id in members:
            enemy_id = int(enemy_id or 0)
            if enemy_id <= 0 or not self._is_enemy_alive_valid(enemy_id):
                continue
            try:
                if enemy_id == target_id or Utils.Distance(target_xy, Agent.GetXY(enemy_id)) <= float(Range.Adjacent.value):
                    impacted.append(enemy_id)
            except Exception:
                continue
        bosses_impacted = sum(1 for enemy_id in impacted if self._is_boss_glow_safe(int(enemy_id)))
        if target_is_boss:
            return 0, 0, len(impacted), 0, int(bosses_impacted), True
        new_daze = 0
        priority_new = 0
        already_fresh = 0
        for enemy_id in impacted:
            remaining = int(self._daze_remaining_ms(int(enemy_id)))
            if remaining > 1400:
                already_fresh += 1
                continue
            new_daze += 1
            casting = False
            try:
                casting = bool(Agent.IsCasting(int(enemy_id)))
            except Exception:
                pass
            try:
                from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import is_support_or_caster
                priority = bool(casting or is_dangerous_cast(int(enemy_id)) or is_support_or_caster(int(enemy_id)))
            except Exception:
                priority = bool(casting or is_dangerous_cast(int(enemy_id)))
            if priority:
                priority_new += 1
        return int(new_daze), int(priority_new), int(len(impacted)), int(already_fresh), int(bosses_impacted), False

    def _cast_technobabble_team_cluster(self):
        """Daze the main packet only when the cast adds real suppression."""
        if not self.IsSkillEquipped(Technobabble_ID):
            return False
        if not self.CanCastSkillID(Technobabble_ID):
            return False
        if not (self.IsInAggro() or self.IsCloseToAggro()):
            return False

        anchor_agent_id = int(get_team_cluster_anchor() or 0)
        if anchor_agent_id <= 0:
            return False
        members = [
            int(agent_id) for agent_id in (get_team_cluster_members(anchor_agent_id) or [])
            if self._is_enemy_alive_valid(int(agent_id))
        ]
        if not members:
            return False

        # EXP36 Stage 6 / Astra E12: select by *new* Daze value, not merely raw
        # geometry.  Boss targets are damage-only for Technobabble and therefore
        # never win while a non-boss packet center can create fresh Daze.
        ranked: list[tuple[tuple, int, tuple[int, int, int, int, int, bool]]] = []
        for candidate in members:
            metrics = self._technobabble_new_cc_metrics(int(candidate), list(members))
            new_daze, priority_new, impacted, _already_fresh, _bosses_impacted, target_is_boss = metrics
            if target_is_boss or new_daze < 1:
                continue
            ranked.append((
                (
                    -int(priority_new),
                    -int(new_daze),
                    -int(impacted),
                    -int(self._is_high_value_soj_interrupt_enemy(int(candidate))),
                    self._distance_to_player(int(candidate)),
                    int(candidate),
                ),
                int(candidate), metrics,
            ))
        ranked.sort(key=lambda row: row[0])
        if not ranked:
            return False

        _score, target_agent_id, metrics = ranked[0]
        new_daze, priority_new, impacted, already_fresh, bosses_impacted, target_is_boss = metrics

        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Technobabble_ID,
            target_agent_id=int(target_agent_id),
            extra_condition=lambda tid=int(target_agent_id): bool(
                self._is_enemy_alive_valid(tid)
                and int(get_team_cluster_anchor() or 0) > 0
                and not self._is_boss_glow_safe(tid)
                and self._technobabble_new_cc_metrics(tid, list(get_team_cluster_members(int(get_team_cluster_anchor() or 0)) or []))[0] >= 1
            ),
            log=False,
            aftercast_delay=200,
        )
        if did_cast:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "SOJ_TECHNOBABBLE_NEW_CC",
                    target_id=int(target_agent_id),
                    new_daze_targets=int(new_daze),
                    priority_new_daze_targets=int(priority_new),
                    impacted_targets=int(impacted),
                    already_fresh_daze=int(already_fresh),
                    target_is_boss=bool(target_is_boss),
                    packet_size=int(len(members)),
                    bosses_in_impact=int(bosses_impacted),
                    boss_neighbor_reroute=bool(int(bosses_impacted) > 0 and not target_is_boss),
                    policy="exp36_stage6_e12_new_daze_utility_nonboss_center",
                )
            except Exception:
                pass
            return True
        return False

    def _cast_symbol_of_wrath_team_cluster(self):
        """Drop Symbol on the same enemy packet the whole team is deleting."""
        if not self.IsSkillEquipped(Symbol_of_Wrath_ID):
            return False
        if not self.CanCastSkillID(Symbol_of_Wrath_ID):
            return False
        if not self.IsInAggro():
            return False

        anchor_agent_id = int(get_team_cluster_anchor() or 0)
        if anchor_agent_id <= 0:
            return False
        members = get_team_cluster_members(anchor_agent_id)
        if len(members) < 3:
            return False

        # Symbol is fixed at the target's initial location for 5 seconds, so use
        # the densest point in the SAME shared packet rather than wandering to a
        # different group.
        candidates = [
            int(agent_id)
            for agent_id in members
            if self._is_enemy_alive_valid(int(agent_id))
        ]
        if not candidates:
            return False
        candidates.sort(
            key=lambda agent_id: (
                -self._count_adjacent_enemies(agent_id),
                -int(self._is_high_value_soj_interrupt_enemy(agent_id)),
                self._distance_to_player(agent_id),
                int(agent_id),
            )
        )
        target_agent_id = int(candidates[0])
        hit_count = int(self._count_adjacent_enemies(target_agent_id))
        if hit_count < 3:
            return False

        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Symbol_of_Wrath_ID,
            target_agent_id=target_agent_id,
            extra_condition=lambda: (
                self._is_enemy_alive_valid(target_agent_id)
                and self._count_adjacent_enemies(target_agent_id) >= 3
            ),
            log=False,
            aftercast_delay=200,
        )
        if did_cast:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "SOJ_SYMBOL_OF_WRATH_CLUSTER",
                    target_id=int(target_agent_id),
                    cluster_hits=int(hit_count),
                    packet_size=int(len(members)),
                    policy="shared_team_packet_3plus",
                )
            except Exception:
                pass
            return True
        return False

    def _cast_signet_of_judgment(self):
        if not self.IsSkillEquipped(Signet_of_Judgment_ID):
            return False
        if not self.IsInAggro() and not self.IsCloseToAggro():
            return False

        # Emergency interrupt fallback: direct Mesmer interrupts and Keystone
        # proxy signets get first refusal through the quality-aware election.
        # SoJ claims only when the skill is actually ready and timing is feasible.
        if self.CanCastSkillID(Signet_of_Judgment_ID):
            target_agent_id, casting_skill_id = self._pick_dangerous_interrupt_target_for_signet(Signet_of_Judgment_ID)
        else:
            target_agent_id, casting_skill_id = (0, 0)
        if target_agent_id > 0 and casting_skill_id > 0:
            self._claim_signet_of_judgment_target(target_agent_id)
            did_cast = yield from self.CastSkillIDAndRestoreTarget(
                skill_id=Signet_of_Judgment_ID,
                target_agent_id=target_agent_id,
                extra_condition=lambda: self._target_still_casting_safe_skill(target_agent_id, casting_skill_id)
                and self._is_signet_of_judgment_control_target_usable(target_agent_id, check_claimed=False),
                log=False,
                aftercast_delay=250,
            )
            if did_cast:
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.register_interrupt_fired(
                        target_agent_id, casting_skill_id, Signet_of_Judgment_ID
                    )
                    CombatDebug.log_event(
                        "SOJ_MONK_INTERRUPT_CAST",
                        target_id=int(target_agent_id),
                        enemy_skill_id=int(casting_skill_id),
                        high_value_caster=bool(self._is_high_value_soj_interrupt_enemy(target_agent_id)),
                        policy="interrupt_if_available_now_otherwise_cast_normally",
                    )
                except Exception:
                    pass
                return True
            release_interrupt_claim(
                target_agent_id,
                casting_skill_id,
                reason="soj_not_fired",
            )

        target_agent_id = self._get_signet_of_judgment_target(claim_target=True)
        if not target_agent_id:
            return False

        return (yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Signet_of_Judgment_ID,
            target_agent_id=target_agent_id,
            extra_condition=lambda: self._is_signet_of_judgment_cast_target_usable(target_agent_id),
            log=False,
            aftercast_delay=250,
        ))

    def _is_bane_signet_target_usable(self, target_agent_id: int, *, check_claimed: bool = True) -> bool:
        if not self._is_enemy_alive_valid(target_agent_id):
            return False
        if self._is_target_knocked_down(target_agent_id):
            return False
        if not self._is_target_attacking(target_agent_id):
            return False
        if BANE_SIGNET_AVOID_SOJ_CLAIMS and check_claimed and self._is_signet_of_judgment_target_claimed(target_agent_id):
            return False
        return True

    def _get_bane_signet_target(self) -> int:
        anchor_agent_id = self._get_power_cluster_anchor()
        if anchor_agent_id:
            for target_agent_id in self._get_power_cluster_members(anchor_agent_id):
                if self._is_bane_signet_target_usable(target_agent_id, check_claimed=True):
                    return int(target_agent_id)
            for target_agent_id in self._get_power_cluster_members(anchor_agent_id):
                if self._is_bane_signet_target_usable(target_agent_id, check_claimed=False):
                    return int(target_agent_id)
        return self._pick_enemy_target(
            self._is_bane_signet_target_usable,
            check_claimed_for_picker=True,
        )

    def _cast_bane_signet(self):
        if not self.IsSkillEquipped(Bane_Signet_ID):
            return False
        if not self.IsInAggro() and not self.IsCloseToAggro():
            return False

        target_agent_id = self._get_bane_signet_target()
        if not target_agent_id:
            return False

        return (yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Bane_Signet_ID,
            target_agent_id=target_agent_id,
            extra_condition=lambda: self._is_bane_signet_target_usable(target_agent_id),
            log=False,
            aftercast_delay=250,
        ))

    # ---------------------------------------------------------------------
    # Support/heal helpers.
    # ---------------------------------------------------------------------
    def _has_combat_pressure(self) -> bool:
        """Cheap combat-pressure gate for support skills.

        The SoJ Monk should not spend party heals / cleanups during harmless
        travel or on single scratch damage. Offensive/control signets already
        have their own aggro gates; this helper is for support value decisions.
        """
        try:
            if self.IsInAggro() or self.IsCloseToAggro():
                return True
        except Exception:
            pass
        try:
            enemies = AgentArray.GetEnemyArray()
            enemies = AgentArray.Filter.ByDistance(enemies, Player.GetXY(), Range.Spellcast.value)
            enemies = AgentArray.Filter.ByCondition(
                enemies,
                lambda agent_id: Agent.IsValid(agent_id) and Agent.IsAlive(agent_id),
            )
            return bool(enemies)
        except Exception:
            return False

    @staticmethod
    def _is_alive_ally(agent_id: int) -> bool:
        try:
            return bool(agent_id and Routines.Checks.Agents.IsAlive(agent_id))
        except Exception:
            try:
                return bool(agent_id and Agent.IsAlive(agent_id))
            except Exception:
                return False

    @staticmethod
    def _ally_health(agent_id: int) -> float:
        try:
            return float(Routines.Checks.Agents.GetHealth(agent_id))
        except Exception:
            try:
                return float(Agent.GetHealth(agent_id))
            except Exception:
                return 1.0

    @staticmethod
    def _is_conditioned_ally(agent_id: int) -> bool:
        try:
            return bool(Routines.Checks.Agents.IsConditioned(agent_id))
        except Exception:
            try:
                return bool(Agent.IsConditioned(agent_id))
            except Exception:
                return False

    @staticmethod
    def _is_hexed_ally(agent_id: int) -> bool:
        try:
            return bool(Routines.Checks.Agents.IsHexed(agent_id))
        except Exception:
            try:
                return bool(Agent.IsHexed(agent_id))
            except Exception:
                return False

    def _allies_in_range(self, area: int = Range.Earshot.value) -> list[int]:
        try:
            return [int(agent_id) for agent_id in (GetAllAlliesArray(area) or []) if self._is_alive_ally(int(agent_id))]
        except Exception:
            return []

    def _enemies_near_ally(self, ally_id: int, area: int) -> int:
        if not ally_id:
            return 0
        try:
            ally_xy = Agent.GetXY(int(ally_id))
            enemies = AgentArray.GetEnemyArray()
            enemies = AgentArray.Filter.ByDistance(enemies, ally_xy, area)
            enemies = AgentArray.Filter.ByCondition(
                enemies,
                lambda enemy_id: Agent.IsValid(enemy_id) and Agent.IsAlive(enemy_id),
            )
            return int(len(enemies or []))
        except Exception:
            return 0

    def _party_health_stats(self, area: int = Range.Earshot.value) -> tuple[int, float, int, int, int, int, int]:
        allies = self._allies_in_range(area)
        alive_health: list[float] = [self._ally_health(agent_id) for agent_id in allies]

        if not alive_health:
            return 0, 1.0, 0, 0, 0, 0, 0

        alive_count = len(alive_health)
        average_health = sum(alive_health) / alive_count
        below_90 = sum(1 for health in alive_health if health <= 0.90)
        below_80 = sum(1 for health in alive_health if health <= 0.80)
        below_70 = sum(1 for health in alive_health if health <= 0.70)
        below_60 = sum(1 for health in alive_health if health <= 0.60)
        below_50 = sum(1 for health in alive_health if health <= 0.50)
        return alive_count, average_health, below_90, below_80, below_70, below_60, below_50

    def _party_heal_needed(
        self,
        *,
        average_threshold: float,
        injured_threshold_count: int,
        injured_health_threshold: float = 0.80,
        severe_threshold_count: int = 1,
        severe_health_threshold: float = 0.60,
        require_pressure: bool = True,
    ) -> bool:
        if require_pressure and not self._has_combat_pressure():
            return False

        allies = self._allies_in_range(Range.Earshot.value)
        if not allies:
            return False

        health_values = [self._ally_health(agent_id) for agent_id in allies]
        average_health = sum(health_values) / len(health_values)
        injured_count = sum(1 for health in health_values if health <= injured_health_threshold)
        severe_count = sum(1 for health in health_values if health <= severe_health_threshold)

        if severe_count >= severe_threshold_count:
            return True
        if injured_count >= injured_threshold_count:
            return True
        if average_health <= average_threshold and injured_count >= max(1, injured_threshold_count - 1):
            return True
        return False

    def _seed_of_life_needed(self) -> bool:
        # Seed is reserved for real spikes, not for normal chip damage. It is
        # still allowed early when a target is dropping fast, because catching a
        # spike before the bar hits red is exactly where Seed is strongest.
        if not self.IsSkillEquipped(Seed_of_Life_ID):
            return False
        if not self._has_combat_pressure():
            return False

        try:
            for agent_id in self._allies_in_range(Range.Spellcast.value):
                if int(agent_id) == int(Player.GetAgentID() or 0):
                    continue
                health = self._ally_health(agent_id)
                if health <= 0.60:
                    return True
                if health <= 0.82 and self.IsPartySpikeTarget(
                    int(agent_id),
                    drop_threshold=0.08,
                    sample_interval_ms=150,
                    window_ms=1000,
                ):
                    return True
        except Exception:
            return False
        return False

    def _cast_party_heal(
        self,
        skill_id: int,
        *,
        average_threshold: float,
        injured_threshold_count: int,
        injured_health_threshold: float = 0.80,
        severe_threshold_count: int = 1,
        severe_health_threshold: float = 0.60,
        require_pressure: bool = True,
    ):
        if not self.IsSkillEquipped(skill_id):
            return False
        if not self._party_heal_needed(
            average_threshold=average_threshold,
            injured_threshold_count=injured_threshold_count,
            injured_health_threshold=injured_health_threshold,
            severe_threshold_count=severe_threshold_count,
            severe_health_threshold=severe_health_threshold,
            require_pressure=require_pressure,
        ):
            return False
        return (yield from self.CastSkillID(
            skill_id=skill_id,
            target_agent_id=Player.GetAgentID(),
            log=False,
            aftercast_delay=250,
        ))

    def _conditioned_party_count(self) -> int:
        return sum(1 for agent_id in self._allies_in_range(Range.Spellcast.value) if self._is_conditioned_ally(agent_id))

    def _get_smite_condition_target(self, *, emergency_only: bool = False) -> int:
        if not self.IsSkillEquipped(Smite_Condition_ID):
            return 0

        try:
            aoe_range = GLOBAL_CACHE.Skill.Data.GetAoERange(Smite_Condition_ID) or Range.Area.value
        except Exception:
            aoe_range = Range.Area.value

        candidates: list[int] = []
        for agent_id in self._allies_in_range(Range.Spellcast.value):
            if not self._is_conditioned_ally(agent_id):
                continue
            health = self._ally_health(agent_id)
            enemy_count = self._enemies_near_ally(agent_id, aoe_range)

            if emergency_only:
                # Emergency pass is genuinely defensive: only a meaningfully
                # injured conditioned party member can pre-empt weapon/offense.
                if health <= 0.68:
                    candidates.append(agent_id)
                continue

            # Normal value pass: condition removal is especially attractive when
            # it also produces holy AoE around the ally.  Mass condition pressure
            # remains valid even when no enemy is currently in the damage area.
            if health <= 0.82 or enemy_count >= 1:
                candidates.append(agent_id)
                continue
            if self._conditioned_party_count() >= 3 and self._has_combat_pressure():
                candidates.append(agent_id)

        if not candidates:
            return 0

        candidates.sort(key=lambda agent_id: (
            -self._enemies_near_ally(agent_id, aoe_range),
            self._ally_health(agent_id),
            int(agent_id),
        ))
        return int(candidates[0])

    def _cast_smite_condition_smart(self, *, emergency_only: bool = False):
        target_agent_id = self._get_smite_condition_target(emergency_only=emergency_only)
        if not target_agent_id:
            return False
        try:
            aoe_range = GLOBAL_CACHE.Skill.Data.GetAoERange(Smite_Condition_ID) or Range.Area.value
        except Exception:
            aoe_range = Range.Area.value
        hp_before = float(self._ally_health(target_agent_id))
        enemy_count = int(self._enemies_near_ally(target_agent_id, aoe_range))
        did = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Smite_Condition_ID,
            target_agent_id=target_agent_id,
            extra_condition=lambda: self._is_alive_ally(target_agent_id) and self._is_conditioned_ally(target_agent_id),
            log=False,
            aftercast_delay=250,
        )
        if did:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "SOJ_SMITE_CONDITION_CAST",
                    target_id=int(target_agent_id),
                    target_hp=round(float(hp_before), 3),
                    enemies_in_damage_area=int(enemy_count),
                    emergency=bool(emergency_only),
                    policy="exp36_stage18_current_spirit_burn_replacement_condition_cleanse_plus_holy_aoe",
                )
            except Exception:
                pass
            return True
        return False

    def _hexed_party_count(self) -> int:
        return sum(1 for agent_id in self._allies_in_range(Range.Spellcast.value) if self._is_hexed_ally(agent_id))

    def _cast_deny_hexes(self, min_priority: int = HexRemovalPriority.LOW, *, require_multi_hex: bool = False):
        if not self.IsSkillEquipped(Deny_Hexes_ID):
            return False
        if require_multi_hex and self._hexed_party_count() < 2:
            return False

        target_agent_id = get_hexed_ally_for_removal(
            Range.Spellcast.value,
            reserve=True,
            skill_id=Deny_Hexes_ID,
            min_priority=min_priority,
        )
        if not target_agent_id:
            return False

        return (yield from cast_hex_removal_and_track(
            self,
            skill_id=Deny_Hexes_ID,
            target_agent_id=target_agent_id,
            aftercast_delay=250,
        ))


    def _party_and_nearby_ally_ids(self) -> list[int]:
        """Return real party-player agent IDs plus nearby allies.

        The old path relied only on HeroAI's nearby ally helper. That happened to
        work reliably with the previous spear setup, but it is the wrong place to
        encode the role. Splinter target eligibility is now profession/party based
        and completely independent of the equipped weapon type.
        """
        ids: set[int] = set()
        try:
            for member in Party.GetPlayers() or []:
                try:
                    aid = int(Party.Players.GetAgentIDByLoginNumber(member.login_number) or 0)
                except Exception:
                    aid = 0
                if aid > 0:
                    ids.add(aid)
        except Exception:
            pass
        try:
            for aid in GetAllAlliesArray(Range.Spellcast.value) or []:
                aid = int(aid or 0)
                if aid > 0:
                    ids.add(aid)
        except Exception:
            pass
        return list(ids)

    def _get_hr_paragon_agent_id(self) -> int:
        try:
            paragon_id = int(getattr(Profession.Paragon, "value", Profession.Paragon))
            candidates = []
            for aid in self._party_and_nearby_ally_ids():
                if aid <= 0 or not Agent.IsValid(aid) or not Agent.IsAlive(aid):
                    continue
                primary, _secondary = Agent.GetProfessions(aid)
                if int(getattr(primary, "value", primary) or 0) == paragon_id:
                    candidates.append(aid)
            candidates.sort(key=lambda aid: (self._distance_to_player(aid), aid))
            return candidates[0] if candidates else 0
        except Exception:
            return 0

    def _get_st_ritualist_agent_id(self) -> int:
        try:
            ritualist_id = int(getattr(Profession.Ritualist, "value", Profession.Ritualist))
            candidates = []
            for aid in self._party_and_nearby_ally_ids():
                if aid <= 0 or not Agent.IsValid(aid) or not Agent.IsAlive(aid):
                    continue
                primary, _secondary = Agent.GetProfessions(aid)
                if int(getattr(primary, "value", primary) or 0) != ritualist_id:
                    continue
                has_st = False
                try:
                    has_st = bool(Routines.Checks.Agents.HasEffect(aid, Soul_Twisting_ID))
                except Exception:
                    has_st = False
                candidates.append((0 if has_st else 1, self._distance_to_player(aid), aid))
            candidates.sort()
            return int(candidates[0][2]) if candidates else 0
        except Exception:
            return 0

    def _splinter_diag(self, reason: str, **fields) -> None:
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            now = int(self._get_game_tick() or 0)
            last = int(getattr(self, "_last_splinter_diag_tick", 0) or 0)
            last_reason = str(getattr(self, "_last_splinter_diag_reason", "") or "")
            if reason != last_reason or now - last >= 2000:
                CombatDebug.log_event("SPLINTER_DIAG", reason=str(reason), **fields)
                self._last_splinter_diag_tick = now
                self._last_splinter_diag_reason = str(reason)
        except Exception as exc:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_guarded_exception(
                    "soj_splinter_diag",
                    exc,
                    reason=str(reason),
                )
            except Exception:
                pass

    def _cast_splinter_on_hr(self):
        """Keep Splinter Weapon exclusively on the HR Paragon.

        EXP32 removes the former ST fallback completely. The ST may now carry
        Vital Weapon for defensive max-HP support, so Splinter must never occupy
        the ST weapon-spell slot. If HR already has any weapon spell, wait rather
        than redirect Splinter to another ally.
        """
        try:
            slot = int(GLOBAL_CACHE.SkillBar.GetSlotBySkillID(Splinter_Weapon_ID) or 0)
        except Exception:
            slot = 0
        if Splinter_Weapon_ID <= 0 or not (1 <= slot <= 8):
            self._splinter_diag("skill_not_equipped", skill_id=int(Splinter_Weapon_ID), slot=int(slot))
            return False

        try:
            global_can_cast = bool(Routines.Checks.Skills.CanCast())
        except Exception:
            global_can_cast = False
        if not global_can_cast:
            self._splinter_diag("global_cannot_cast", skill_id=int(Splinter_Weapon_ID), slot=int(slot))
            return False
        try:
            ready = bool(Routines.Checks.Skills.IsSkillIDReady(Splinter_Weapon_ID))
        except Exception:
            ready = bool(self.CanCastSkillID(Splinter_Weapon_ID))
        if not ready:
            return False
        try:
            enough_energy = bool(Routines.Checks.Skills.HasEnoughEnergy(Player.GetAgentID(), Splinter_Weapon_ID))
        except Exception:
            enough_energy = True
        if not enough_energy:
            self._splinter_diag("not_enough_energy", skill_id=int(Splinter_Weapon_ID), slot=int(slot))
            return False

        packet_anchor = int(get_team_cluster_anchor() or 0)
        if not self.IsInAggro() and packet_anchor <= 0:
            return False

        # EXP36/E13: a direct UseSkill request can be accepted before the
        # resulting weapon effect is visible. Avoid hammering the same request
        # into the queue while that short dispatch window is unresolved.
        now_tick = int(self._get_game_tick() or 0)
        last_request_tick = int(getattr(self, "_last_splinter_request_tick", 0) or 0)
        if now_tick > 0 and last_request_tick > 0 and now_tick - last_request_tick < 650:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "SPLINTER_REQUEST_DEDUPE_BLOCK",
                    ms_since_request=max(0, int(now_tick - last_request_tick)),
                    previous_target_id=int(getattr(self, "_last_splinter_request_target_id", 0) or 0),
                    policy="exp36_stage3_e13_dispatch_guard_observation",
                )
            except Exception:
                pass
            return False

        hr_id = int(self._get_hr_paragon_agent_id() or 0)
        if hr_id <= 0:
            self._splinter_diag(
                "hr_not_found",
                skill_id=int(Splinter_Weapon_ID), slot=int(slot),
                policy="hr_only_no_st_fallback",
            )
            return False
        if not Agent.IsValid(hr_id) or not Agent.IsAlive(hr_id):
            return False
        # Final dispatch-time target validation: Splinter is HR-only and is not
        # worth queuing when the HR has moved outside normal spellcast range.
        if self._distance_to_player(int(hr_id)) > float(Range.Spellcast.value):
            self._splinter_diag(
                "hr_out_of_range", target_id=int(hr_id),
                distance=round(float(self._distance_to_player(int(hr_id))), 1),
                policy="exp36_e13_final_dispatch_validation",
            )
            return False
        try:
            if Agent.IsWeaponSpelled(hr_id):
                return False
        except Exception:
            pass

        try:
            previous_enemy_target = int(Player.GetTargetID() or 0)
        except Exception:
            previous_enemy_target = 0
        try:
            GLOBAL_CACHE.SkillBar.UseSkill(int(slot), target_agent_id=int(hr_id), aftercast_delay=180)
            self._last_splinter_request_tick = int(self._get_game_tick() or now_tick or 0)
            self._last_splinter_request_target_id = int(hr_id)
            self._mark_local_cast_pending(180)
            self.SetTickSuccess()
            did_cast = True
        except Exception as exc:
            self._splinter_diag(
                "low_level_cast_exception",
                target_id=int(hr_id), target_role="hr", slot=int(slot), error=str(exc)[:120],
            )
            did_cast = False

        if did_cast:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                members = get_team_cluster_members(packet_anchor) if packet_anchor > 0 else []
                now = int(self._get_game_tick() or 0)
                last = int(getattr(self, "_last_splinter_apply_tick", 0) or 0)
                CombatDebug.log_event(
                    "SPLINTER_CAST_COMMIT",
                    target_id=int(hr_id), target_role="hr", slot=int(slot),
                    team_anchor=int(packet_anchor), packet_size=int(len(members)),
                    ms_since_previous=(int(now-last) if last > 0 and now > 0 else -1),
                    policy="hr_only_no_st_fallback", weapon_policy="target_weapon_type_ignored",
                )
                CombatDebug.log_event(
                    "SPLINTER_TARGET_CAST",
                    target_id=int(hr_id), target_role="hr",
                    is_st=False, is_hr=True,
                    policy="hr_only_no_st_fallback",
                )
                self._last_splinter_apply_tick = now
            except Exception as exc:
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_guarded_exception(
                        "soj_splinter_commit_diag",
                        exc,
                        target_id=int(hr_id),
                    )
                except Exception:
                    pass
            if previous_enemy_target > 0:
                try:
                    yield from self.RestoreEnemyTarget(previous_enemy_target)
                except Exception:
                    pass
            return True
        return False

    def _cast_spirit_burn_team_packet(self):
        """Burn a healthy central member of the canonical packet.

        The PI Mesmer can then spread Burning (and every other condition on the
        same carrier) with Epidemic.  Spirit Burn remains a useful single-target
        damage/Burning filler even when no Epidemic Mesmer is present.
        """
        if not self.IsSkillEquipped(Spirit_Burn_ID):
            return False
        if not self.CanCastSkillID(Spirit_Burn_ID):
            return False
        anchor = int(get_team_cluster_anchor() or 0)
        if anchor <= 0:
            return False
        members = [
            int(x) for x in (get_team_cluster_members(anchor, radius=Range.Adjacent.value) or [])
            if int(x or 0) > 0
        ]
        if anchor not in members:
            members.append(anchor)
        ranked: list[tuple[tuple, int, int]] = []
        for target_id in sorted(set(members)):
            if not self._is_enemy_alive_valid(target_id):
                continue
            try:
                already_burning = bool(Routines.Checks.Agents.HasEffect(target_id, Burning_ID))
            except Exception:
                already_burning = False
            try:
                hp = float(Agent.GetHealth(target_id))
            except Exception:
                hp = 0.0
            adjacent = int(self._count_adjacent_enemies(target_id))
            if adjacent < 1:
                continue
            # New Burning on the most central, healthiest carrier is ideal for
            # the following Epidemic. Existing Burning is still a fallback.
            ranked.append(((int(already_burning), -adjacent, -hp, target_id), target_id, adjacent))
        if not ranked:
            return False
        ranked.sort(key=lambda row: row[0])
        _rank, target_id, adjacent = ranked[0]
        try:
            already_burning_before = bool(Routines.Checks.Agents.HasEffect(int(target_id), Burning_ID))
        except Exception:
            already_burning_before = False
        slot = int(GLOBAL_CACHE.SkillBar.GetSlotBySkillID(Spirit_Burn_ID) or 0)
        try:
            spirit_near_at_cast = bool(Routines.Agents.GetNearestSpirit(Range.Earshot.value))
        except Exception:
            spirit_near_at_cast = False
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Spirit_Burn_ID,
            target_agent_id=int(target_id),
            extra_condition=lambda: self._is_enemy_alive_valid(int(target_id)),
            log=False,
            aftercast_delay=180,
        )
        if did_cast:
            self._arm_spirit_burn_confirmation(
                int(target_id), int(slot),
                already_burning=bool(already_burning_before),
                spirit_near_at_cast=bool(spirit_near_at_cast),
            )
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "SOJ_SPIRIT_BURN_CLUSTER",
                    target_id=int(target_id), team_anchor=int(anchor),
                    adjacent_enemies=int(adjacent),
                    already_burning_before=bool(already_burning_before),
                    carrier_published=False,
                    policy="exp35_dispatch_then_confirm_effect_before_epidemic_lease",
                )
            except Exception:
                pass
            return True
        return False

    def _cast_lamentation_team_packet(self):
        """Use Lamentation (Wehklage) on the current PI packet when useful.

        A fresh confirmed PI packet is first choice because it gives a stable
        balled target for Lamentation's nearby hex.  If no PI event is active,
        fall back immediately to the normal authoritative team cluster.  There
        is no wait for PI and no reservation: this is ordinary SoJ damage/hex
        filler after interrupt/Technobabble first refusal.
        """
        if not self.IsSkillEquipped(Lamentation_ID) or not self.CanCastSkillID(Lamentation_ID) or not self.IsInAggro():
            return False

        target_id = 0
        source = "team_cluster"
        packet_size = 0
        try:
            from Py4GWCoreLib.Builds.Skills.BurningComboCoordination import active_pi_knockdown_event
            pi_anchor, pi_members, _expires = active_pi_knockdown_event()
            live_members = [
                int(x) for x in (pi_members or ())
                if int(x or 0) > 0 and Agent.IsValid(int(x)) and Agent.IsAlive(int(x))
            ]
            if int(pi_anchor or 0) > 0 and int(pi_anchor) in live_members:
                target_id = int(pi_anchor)
                packet_size = len(live_members)
                source = "confirmed_pi_packet"
        except Exception:
            pass

        if target_id <= 0:
            target_id = int(pick_lamentation_target(cleanup_dangerous_only=True) or 0)
            if target_id > 0:
                try:
                    anchor = int(get_team_cluster_anchor() or target_id)
                    packet_size = len(get_team_cluster_members(anchor) or [target_id])
                except Exception:
                    packet_size = 1
        if target_id <= 0:
            return False

        # In a normal non-PI packet, require actual cluster value unless the
        # cleanup target is a dangerous/support caster (the picker enforces that
        # single-target exception).  PI packets are already confirmed team burst
        # opportunities and are allowed directly.
        if source != "confirmed_pi_packet" and packet_size < 2:
            try:
                if not is_dangerous_cast(int(target_id)) and not is_support_or_caster(int(target_id)):
                    return False
            except Exception:
                return False

        try:
            spirit_near = bool(Routines.Agents.GetNearestSpirit(Range.Earshot.value))
        except Exception:
            spirit_near = False
        try:
            corpse_near = bool(Routines.Agents.GetNearestCorpse(Range.Earshot.value))
        except Exception:
            corpse_near = False

        did = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Lamentation_ID,
            target_agent_id=int(target_id),
            extra_condition=lambda tid=int(target_id): bool(Agent.IsValid(tid) and Agent.IsAlive(tid)),
            log=False,
            aftercast_delay=200,
        )
        if did:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "SOJ_LAMENTATION_CAST",
                    target_id=int(target_id), packet_size=int(packet_size),
                    source=str(source), spirit_near=bool(spirit_near), corpse_near=bool(corpse_near),
                    policy="stage19_pi_packet_first_else_team_cluster_no_wait",
                )
            except Exception:
                pass
            return True
        return False


    def _cast_reversal_of_damage_pressure(self, *, emergency_only: bool = False):
        """Use Reversal of Damage as a real pressure-response enchantment.

        The old Scourge Healing slot was offensive but frequently spent casts on
        enemies that never received a heal. Reversal is instead tied to actual
        allied pressure: it prefers a party member being collapsed on by melee,
        then a party member with multiple enemies in touch range, and it also
        reacts to a sharp health drop.  Minions/pets/NPC allies are excluded by
        resolving only true party players, heroes and henchmen.

        emergency_only is used before the interrupt/offense chain and only fires
        on a severe health/spike situation.  The normal pass runs after critical
        interrupt handling and catches strong but non-emergency pressure.
        """
        if not self.IsSkillEquipped(Reversal_of_Damage_ID):
            return False
        if not self.CanCastSkillID(Reversal_of_Damage_ID):
            return False
        if not self._has_combat_pressure():
            return False

        try:
            party_ids: set[int] = set()
            for player in Party.GetPlayers() or []:
                login_number = int(getattr(player, "login_number", 0) or 0)
                if login_number > 0:
                    aid = int(Party.Players.GetAgentIDByLoginNumber(login_number) or 0)
                    if aid > 0:
                        party_ids.add(aid)
            for hero in Party.GetHeroes() or []:
                aid = int(getattr(hero, "agent_id", 0) or 0)
                if aid > 0:
                    party_ids.add(aid)
            for henchman in Party.GetHenchmen() or []:
                aid = int(getattr(henchman, "agent_id", 0) or 0)
                if aid > 0:
                    party_ids.add(aid)
        except Exception:
            party_ids = set()

        if not party_ids:
            return False

        try:
            self.UpdatePartyHealthMonitor(sample_interval_ms=120, window_ms=1000)
        except Exception:
            pass

        enemy_cache: dict[int, tuple[int, int]] = {}

        def _pressure_counts(ally_id: int) -> tuple[int, int]:
            cached = enemy_cache.get(int(ally_id))
            if cached is not None:
                return cached
            try:
                xy = Agent.GetXY(int(ally_id))
                enemies = AgentArray.GetEnemyArray()
                enemies = AgentArray.Filter.ByDistance(enemies, xy, Range.Touch.value)
                enemies = AgentArray.Filter.ByCondition(
                    enemies,
                    lambda enemy_id: Agent.IsValid(enemy_id) and Agent.IsAlive(enemy_id),
                )
                enemy_ids = [int(eid) for eid in (enemies or [])]
                melee = sum(1 for eid in enemy_ids if Routines.Checks.Agents.IsMelee(eid))
                result = (int(melee), int(len(enemy_ids)))
            except Exception:
                result = (0, 0)
            enemy_cache[int(ally_id)] = result
            return result

        candidates: list[tuple[tuple, int, dict]] = []
        for ally_id in party_ids:
            ally_id = int(ally_id)
            if not self._is_alive_ally(ally_id):
                continue
            try:
                if Routines.Checks.Agents.HasEffect(ally_id, Reversal_of_Damage_ID):
                    continue
            except Exception:
                pass

            melee_count, enemy_count = _pressure_counts(ally_id)
            if enemy_count <= 0:
                continue

            hp = self._ally_health(ally_id)
            try:
                health_drop = float(self.GetPartyHealthDelta(ally_id) or 0.0)
            except Exception:
                health_drop = 0.0
            spike = bool(health_drop >= 0.08)

            if emergency_only:
                # Do not steal a critical interrupt for ordinary chip damage.
                if not (hp <= 0.55 or (hp <= 0.78 and spike)):
                    continue
            else:
                # Normal value pass: cast pre-emptively only under substantial
                # pressure, otherwise require actual injury / a health drop.
                if not (
                    hp <= 0.82
                    or spike
                    or melee_count >= 2
                    or enemy_count >= 3
                ):
                    continue

            # Emergency and spike status first, then melee pressure (best RoD
            # return-damage trigger), then total body pressure and current HP.
            rank = (
                0 if hp <= 0.55 else 1,
                0 if spike else 1,
                -int(melee_count),
                -int(enemy_count),
                float(hp),
                -float(health_drop),
                ally_id,
            )
            candidates.append((rank, ally_id, {
                "hp": hp,
                "drop": health_drop,
                "melee": melee_count,
                "enemies": enemy_count,
                "spike": spike,
            }))

        if not candidates:
            return False

        candidates.sort(key=lambda row: row[0])
        _rank, target_id, meta = candidates[0]
        target_id = int(target_id)

        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Reversal_of_Damage_ID,
            target_agent_id=target_id,
            extra_condition=lambda: self._is_alive_ally(target_id),
            log=False,
            aftercast_delay=200,
        )
        if did_cast:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "SOJ_REVERSAL_OF_DAMAGE",
                    target_id=int(target_id),
                    target_hp=round(float(meta["hp"]), 3),
                    health_drop=round(float(meta["drop"]), 3),
                    melee_touch=int(meta["melee"]),
                    enemies_touch=int(meta["enemies"]),
                    spike=bool(meta["spike"]),
                    emergency=bool(emergency_only),
                    policy="party_only_pressure_melee_first_no_minions",
                )
            except Exception:
                pass
            return True
        return False

    def _cast_castigation_only_on_attacker(self):
        if not self.IsSkillEquipped(Castigation_Signet_ID) or not self.CanCastSkillID(Castigation_Signet_ID) or not self.IsInAggro():
            return False
        anchor = int(get_team_cluster_anchor() or 0)
        members = get_team_cluster_members(anchor) if anchor > 0 else []
        attackers = [aid for aid in members if self._is_target_attacking(aid)]
        if not attackers:
            return False
        attackers.sort(key=lambda aid: (-self._count_adjacent_enemies(aid), self._distance_to_player(aid), aid))
        target_id = int(attackers[0])
        return (yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Castigation_Signet_ID,
            target_agent_id=target_id,
            extra_condition=lambda: self._is_enemy_alive_valid(target_id) and self._is_target_attacking(target_id),
            log=False,
            aftercast_delay=200,
        ))

    def _run_local_skill_logic(self):
        # EXP35 telemetry/coordination poll is non-casting and must still run
        # while Spirit Burn itself is in activation.
        self._poll_spirit_burn_confirmation()
        refresh_aoe_danger_zones()
        if not Routines.Checks.Skills.CanCast():
            return False

        pressure = self._has_combat_pressure()
        close_pressure = self.IsInAggro() or self.IsCloseToAggro()
        player_energy_pct = float(Agent.GetEnergy(Player.GetAgentID()))

        # Hochnäsigkeit / Air of Superiority is treated exactly as a pre-burst
        # snowball setup, not as random filler.  The helper only casts when the
        # effect needs applying/refreshing.  It sits before normal offense but
        # does not replace the emergency Reversal path once an ally is collapsing.
        if close_pressure and self.IsSkillEquipped(Air_of_Superiority_ID):
            # If the team is already in acute danger, keep the action for Reversal.
            emergency_reversal_needed = False
            try:
                for ally_id in (GetAllAlliesArray() or []):
                    ally_id = int(ally_id or 0)
                    if ally_id > 0 and Agent.IsValid(ally_id) and Agent.IsAlive(ally_id):
                        if float(Agent.GetHealth(ally_id)) <= 0.55:
                            emergency_reversal_needed = True
                            break
            except Exception:
                emergency_reversal_needed = False
            if not emergency_reversal_needed and (yield from self.skills.Any.PvE.Air_of_Superiority()):
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event("SOJ_AIR_OF_SUPERIORITY_SETUP", policy="pre_burst_snowball")
                except Exception:
                    pass
                return True

        if self.IsSkillEquipped(Smite_Hex_ID) and (yield from self.skills.Monk.SmitingPrayers.Smite_Hex(min_priority=HexRemovalPriority.HIGH)):
            return True

        # EXP36/E13: a genuinely collapsing ally gets one rescue check before
        # weapon maintenance. Ordinary Reversal still stays later in the
        # offensive chain, so this does not turn SoJ into a general healer.
        if pressure and (yield from self._cast_reversal_of_damage_pressure(emergency_only=True)):
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "SOJ_EMERGENCY_SUPPORT_BEFORE_SPLINTER",
                    policy="exp36_e13_emergency_only",
                )
            except Exception:
                pass
            return True

        # Current SoJ bar: Smite Condition replaces Spirit Burn.  Emergency
        # Reversal keeps first refusal; after that, a genuinely injured
        # conditioned ally may be cleansed before Splinter maintenance.
        if (yield from self._cast_smite_condition_smart(emergency_only=True)):
            return True

        if (yield from self._cast_splinter_on_hr()):
            return True

        if not pressure:
            return False

        # Dangerous casts always beat setup/damage. SoJ is never reserved: if a
        # valid interrupt exists right now, take it; otherwise continue instantly.
        if (yield from self._cast_signet_of_judgment_interrupt_only()):
            return True

        # Technobabble belongs to the MAIN authoritative packet. It is independent
        # of the PI account's Epidemic logic and never waits for/coordinates with it.
        if (yield from self._cast_technobabble_team_cluster()):
            return True

        # Optional support variants remain feature-detected. Smite Condition is used
        # only when equipped; the current user bar may instead carry Lamentation.
        if (yield from self._cast_smite_condition_smart(emergency_only=False)):
            return True
        if (yield from self._cast_spirit_burn_team_packet()):
            return True
        if (yield from self._cast_lamentation_team_packet()):
            return True

        # Use the former Scourge-Healing slot defensively/offensively: negate
        # the next incoming hit on a pressured ally and reflect that damage.
        if (yield from self._cast_reversal_of_damage_pressure(emergency_only=False)):
            return True

        # If there is no interrupt to take, add persistent holy pressure to the
        # same 3+ enemy packet the rest of the team is focusing.
        if (yield from self._cast_symbol_of_wrath_team_cluster()):
            return True

        # Normal SoJ damage/control continues immediately afterwards.
        if (yield from self._cast_signet_of_judgment()):
            return True
        if (yield from self._cast_bane_signet()):
            return True
        if (yield from self._cast_castigation_only_on_attacker()):
            return True

        if player_energy_pct >= 0.45 and self.IsSkillEquipped(Smite_Hex_ID) and (yield from self.skills.Monk.SmitingPrayers.Smite_Hex(min_priority=HexRemovalPriority.MEDIUM)):
            return True

        return False

