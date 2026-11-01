from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

from Py4GWCoreLib.BuildMgr import BuildCoroutine
from Py4GWCoreLib import Range, Routines
from Py4GWCoreLib.Agent import Agent
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.Skill import Skill
from Py4GWCoreLib.HeroAI.types import Skilltarget

if TYPE_CHECKING:
    from Py4GWCoreLib.HeroAI.custom_skill_src.skill_types import CustomSkill
    from Py4GWCoreLib.BuildMgr import BuildMgr

__all__ = ["RestorationMagic"]


class RestorationMagic:
    def __init__(self, build: BuildMgr) -> None:
        self.build: BuildMgr = build

    def _has_spirit_in_earshot(self) -> bool:
        return bool(Routines.Agents.GetNearestSpirit(Range.Earshot.value))

    @staticmethod
    def _actual_party_agent_ids() -> set[int]:
        """Return real party members only (players/heroes/henchmen).

        Allied NPCs, pets, spirits and especially minions are intentionally
        excluded. Restoration heals in this project are party resources and
        must never be spent keeping disposable minions alive.
        """
        try:
            from Py4GWCoreLib import Party
        except Exception:
            return set()

        result: set[int] = set()
        try:
            for player in Party.GetPlayers() or []:
                login_number = int(getattr(player, "login_number", 0) or 0)
                agent_id = int(Party.Players.GetAgentIDByLoginNumber(login_number) or 0)
                if agent_id > 0:
                    result.add(agent_id)
        except Exception:
            pass

        for getter_name in ("GetHeroes", "GetHenchmen"):
            try:
                getter = getattr(Party, getter_name)
                for member in getter() or []:
                    agent_id = int(getattr(member, "agent_id", 0) or 0)
                    if agent_id > 0:
                        result.add(agent_id)
            except Exception:
                continue

        return result

    #region B
    def Breath_of_the_Great_Dwarf(self) -> BuildCoroutine:
        if False:
            yield
        return False
    #endregion

    #region L
    def Life(self) -> BuildCoroutine:
        life_id: int = Skill.GetID("Life")

        if not self.build.IsSkillEquipped(life_id):
            return False

        # Life heals allies for N HP per second the spirit was alive when it
        # dies. Recasting kills the previous spirit (firing its heal-on-death
        # with the elapsed timer) and resets the duration counter. To maximise
        # the total heal, only summon when no Life spirit is up - let the
        # existing one run its full 20s timer (or until killed) before
        # refreshing. CanCastSkillID enforces the same gate downstream via
        # SPIRIT_BUFF_MAP, but checking explicitly here keeps intent visible
        # and short-circuits before entering the cast generator.
        if self.build.SpiritBuffExists(life_id):
            return False

        if not (self.build.IsInAggro() or self.build.IsCloseToAggro()):
            return False

        return (yield from self.build.CastSpiritSkillID(
            skill_id=life_id,
            log=False,
            aftercast_delay=250,
        ))
    #endregion

    #region G
    def Ghostmirror_Light(
        self,
        *,
        health_threshold: float = 0.66,
    ) -> BuildCoroutine:
        """Heal the lowest injured *other* formal party member.

        Stage 2.42 adds first-class support for the live N/Rt BiP account bar.
        Ghostmirror Light cannot target the caster; if a spirit is in earshot the
        game also heals the caster automatically, so target selection should stay
        focused on the most injured other party member rather than treating the
        self-heal as a separate action.  Pets/minions/NPCs remain excluded using
        the same formal-party filter as the other Restoration heals.
        """
        ghostmirror_id: int = Skill.GetID("Ghostmirror_Light")
        if not self.build.IsSkillEquipped(ghostmirror_id):
            return False
        if not self.build.CanCastSkillID(ghostmirror_id):
            return False

        threshold = max(0.0, min(1.0, float(health_threshold)))
        party_agent_ids = self._actual_party_agent_ids()
        if not party_agent_ids:
            return False
        try:
            from Py4GWCoreLib.HeroAI.targeting import GetAllAlliesArray
            ally_array = list(GetAllAlliesArray(Range.Spellcast.value) or [])
        except Exception:
            ally_array = []

        player_id = int(Player.GetAgentID() or 0)
        candidates: list[tuple[float, int]] = []
        for raw_agent_id in ally_array:
            agent_id = int(raw_agent_id or 0)
            if agent_id <= 0 or agent_id == player_id or agent_id not in party_agent_ids:
                continue
            try:
                if not Routines.Checks.Agents.IsAlive(agent_id):
                    continue
                hp = float(Routines.Checks.Agents.GetHealth(agent_id))
            except Exception:
                continue
            if hp < threshold:
                candidates.append((hp, agent_id))
        if not candidates:
            return False

        candidates.sort(key=lambda item: (item[0], item[1]))
        hp, target_id = candidates[0]
        did_cast = bool((yield from self.build.CastSkillID(
            skill_id=ghostmirror_id,
            target_agent_id=int(target_id),
            log=False,
            aftercast_delay=250,
        )))
        if did_cast:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "RESTORATION_GHOSTMIRROR_LIGHT_CAST",
                    target_id=int(target_id),
                    target_hp=f"{float(hp):.3f}",
                    threshold=f"{float(threshold):.3f}",
                    spirit_in_earshot=bool(self._has_spirit_in_earshot()),
                    policy="stage2_42_bip_other_party_heal_with_optional_self_heal",
                )
            except Exception:
                pass
        return bool(did_cast)
    #endregion

    #region M
    def Mend_Body_and_Soul(
        self,
        *,
        health_threshold: float | None = None,
        cleanse_blind_martial: bool = False,
        cleanse_cripple_melee: bool = False,
        self_only: bool = False,
    ) -> BuildCoroutine:
        from Py4GWCoreLib.HeroAI.targeting import GetAllAlliesArray

        mend_body_and_soul_id: int = Skill.GetID("Mend_Body_and_Soul")
        mend_body_and_soul: CustomSkill = self.build.GetCustomSkill(mend_body_and_soul_id)

        if not self.build.IsSkillEquipped(mend_body_and_soul_id):
            return False

        party_agent_ids = self._actual_party_agent_ids()
        if not party_agent_ids:
            # Fail closed: never fall back to the broad ally array here because
            # that array also contains minions/pets/NPCs.
            return False

        if self_only:
            caster_id = int(Player.GetAgentID() or 0)
            threshold = max(0.0, min(1.0, float(health_threshold if health_threshold is not None else 0.70)))
            def _self_heal_needed() -> bool:
                return bool(
                    caster_id > 0 and caster_id in party_agent_ids
                    and Agent.IsAlive(caster_id)
                    and float(Agent.GetHealth(caster_id)) < threshold
                )
            if cleanse_blind_martial or cleanse_cripple_melee or not _self_heal_needed():
                return False
            return (yield from self.build.CastSkillIDAndRestoreTarget(
                mend_body_and_soul_id, caster_id,
                extra_condition=_self_heal_needed, dispatch_guard=_self_heal_needed,
            ))

        # Cleanse-oriented tiers: MBaS removes one condition per cast only when a
        # spirit is in earshot, so gate the tier on the spirit + profession-specific
        # carriers of the targeted condition.
        if cleanse_blind_martial or cleanse_cripple_melee:
            if not self._has_spirit_in_earshot():
                return False

            if cleanse_blind_martial:
                blind_skill_id: int = Skill.GetID("Blind")
                profession_predicate = lambda aid: Routines.Checks.Agents.IsMartial(aid)
                condition_predicate = lambda aid: Routines.Checks.Agents.HasEffect(aid, blind_skill_id)
            else:
                profession_predicate = lambda aid: Routines.Checks.Agents.IsMelee(aid)
                condition_predicate = lambda aid: Agent.IsCrippled(aid)

            ally_array = GetAllAlliesArray(Range.Spellcast.value) or []
            candidates = [
                agent_id for agent_id in ally_array
                if int(agent_id) in party_agent_ids
                and Agent.IsValid(agent_id)
                and Agent.IsAlive(agent_id)
                and not Agent.IsMinion(agent_id)
                and profession_predicate(agent_id)
                and condition_predicate(agent_id)
            ]
            if not candidates:
                return False

            candidates.sort(key=lambda aid: Agent.GetHealth(aid))
            target_agent_id = candidates[0]
        else:
            # HP-threshold tier: caller overrides the metadata's LessLife when it
            # wants a specific tier (emergency, damaged, preventive) rather than
            # the bar-wide default. When None, fall back to metadata (0.70 default).
            threshold: float = (
                health_threshold
                if health_threshold is not None
                else float(mend_body_and_soul.Conditions.LessLife or 0.70)
            )
            threshold = max(0.0, min(1.0, threshold))

            def _resolve_mend_body_and_soul_target() -> int:
                variants: list = [None]
                if self._has_spirit_in_earshot():
                    variants = [
                        lambda custom_skill: setattr(custom_skill.Conditions, "HasCondition", True),
                        None,
                    ]

                return self.build.ResolvePreferredAllyTarget(
                    mend_body_and_soul_id,
                    mend_body_and_soul,
                    variants=variants,
                    validator=lambda aid: (
                        int(aid) in party_agent_ids
                        and Agent.IsAlive(aid)
                        and not Agent.IsMinion(aid)
                        and Agent.GetHealth(aid) < threshold
                    ),
                )

            target_agent_id = _resolve_mend_body_and_soul_target()

        if not target_agent_id:
            return False

        return (yield from self.build.CastSkillIDAndRestoreTarget(
            mend_body_and_soul_id,
            target_agent_id,
        ))

    def Mending_Grip(self) -> BuildCoroutine:
        mending_grip_id: int = Skill.GetID("Mending_Grip")
        mending_grip: CustomSkill = self.build.GetCustomSkill(mending_grip_id)
        health_threshold: float = max(0.0, min(1.0, float(mending_grip.Conditions.LessLife or 0.80)))

        def _resolve_mending_grip_target() -> int:
            def _clear_priority_conditions(custom_skill: CustomSkill) -> None:
                custom_skill.Conditions.HasWeaponSpell = False
                custom_skill.Conditions.HasCondition = False

            return self.build.ResolvePreferredAllyTarget(
                mending_grip_id,
                mending_grip,
                variants=[
                    None,
                    _clear_priority_conditions,
                ],
                validator=lambda agent_id: Agent.IsAlive(agent_id) and Agent.GetHealth(agent_id) < health_threshold,
            )

        if not self.build.IsSkillEquipped(mending_grip_id):
            return False

        target_agent_id = _resolve_mending_grip_target()
        if not target_agent_id:
            return False

        return (yield from self.build.CastSkillIDAndRestoreTarget(
            mending_grip_id,
            target_agent_id,
        ))
    #endregion

    #region P
    def Protective_Was_Kaolai(self) -> BuildCoroutine:
        """Cast Protective Was Kaolai on self (item-spell). Picks up Kaolai's
        ashes - grants +10 armor while held; the party heal triggers on drop.
        Pair with Drop_Held_Bundle in the rotation (drop check FIRST) so the
        heal actually releases when the party needs it."""
        protective_was_kaolai_id: int = Skill.GetID("Protective_Was_Kaolai")

        if not self.build.IsSkillEquipped(protective_was_kaolai_id):
            return False

        # Already holding any bundle (PwK ashes, or another item spell) -
        # recasting would either fail or replace the current bundle and waste
        # the un-released heal. Let Drop_Held_Bundle release first.
        if Agent.IsHoldingItem(Player.GetAgentID()):
            return False

        if not Routines.Checks.Skills.CanCast():
            return False

        return (yield from self.build.CastSkillID(
            skill_id=protective_was_kaolai_id,
            target_agent_id=Player.GetAgentID(),
            aftercast_delay=250,
            log=False,
        ))
    #endregion

    #region X
    def Xinraes_Weapon(self) -> BuildCoroutine:
        from Py4GWCoreLib.HeroAI.targeting import TargetAllyWeaponSpell

        xinraes_weapon_id: int = Skill.GetID("Xinraes_Weapon")

        if not self.build.IsSkillEquipped(xinraes_weapon_id):
            return False

        target_agent_id = TargetAllyWeaponSpell(xinraes_weapon_id, Range.Spellcast.value)
        if not target_agent_id:
            return False

        return (yield from self.build.CastSkillIDAndRestoreTarget(
            xinraes_weapon_id,
            target_agent_id,
        ))
    #endregion

    #region R
    def Recovery(self) -> BuildCoroutine:
        recovery_id: int = Skill.GetID("Recovery")

        if not self.build.IsSkillEquipped(recovery_id):
            return False

        return (yield from self.build.CastSpiritSkillID(
            skill_id=recovery_id,
            log=False,
            aftercast_delay=250,
        ))

    def Recuperation(
        self,
        *,
        min_degen_count: int = 0,
        min_party_damaged_count: int = 0,
    ) -> BuildCoroutine:
        recuperation_id: int = Skill.GetID("Recuperation")

        if not self.build.IsSkillEquipped(recuperation_id):
            return False

        # State gate: only fire during active combat or the approach phase - never
        # during pure downtime.
        if not (self.build.IsInAggro() or self.build.IsCloseToAggro()):
            return False

        # Independent situational gates. Callers that want OR semantics across
        # gates should call Recuperation twice (once per gate) so the "OR" is
        # explicit in the priority chain.
        #   `min_degen_count`         - N allies in Spirit range suffering any
        #                               health-degen source (poison / bleeding /
        #                               burning / degen hex).
        #   `min_party_damaged_count` - N allies in Spirit range below 75% HP.
        # HP-aware recast (spirit at < 20% HP) is enforced by
        # BuildMgr.SpiritBuffExists via the Recuperation custom-skill metadata
        # (Conditions.AllowRecastAtLife = 0.20).
        if min_degen_count > 0:
            if self._count_allies_suffering_degen() < min_degen_count:
                return False

        if min_party_damaged_count > 0:
            if not self._is_party_damaged(
                within_range=Range.Spirit.value,
                min_allies_count=min_party_damaged_count,
                less_health_than_percent=0.75,
            ):
                return False

        return (yield from self.build.CastSpiritSkillID(
            skill_id=recuperation_id,
            log=False,
            aftercast_delay=250,
        ))

    @staticmethod
    def _is_suffering_degen(agent_id: int, burning_skill_id: int) -> bool:
        """Check if an ally has any health-degen condition: poison (-4),
        bleeding (-3), burning (-7), or a degen hex."""
        from Py4GWCoreLib.Effect import Effects
        if Agent.IsPoisoned(agent_id):
            return True
        if Agent.IsBleeding(agent_id):
            return True
        if Agent.IsDegenHexed(agent_id):
            return True
        if Effects.HasEffect(agent_id, burning_skill_id):
            return True
        return False

    def _count_allies_suffering_degen(self) -> int:
        """Count party allies in Spirit range suffering any health-degen source."""
        from Py4GWCoreLib import AgentArray, GLOBAL_CACHE

        ally_ids = AgentArray.GetAllyArray()
        ally_ids = AgentArray.Filter.ByDistance(ally_ids, Player.GetXY(), Range.Spirit.value)
        ally_ids = AgentArray.Filter.ByCondition(ally_ids, lambda aid: Agent.IsAlive(aid))

        burning_skill_id = GLOBAL_CACHE.Skill.GetID("Burning")
        count = 0
        for aid in ally_ids:
            if self._is_suffering_degen(aid, burning_skill_id):
                count += 1
        return count

    @staticmethod
    def _is_party_damaged(
        within_range: float,
        min_allies_count: int,
        less_health_than_percent: float,
    ) -> bool:
        """Return True when at least `min_allies_count` allies within range are
        alive and currently below `less_health_than_percent` HP (0.0-1.0). Short
        -circuits as soon as the threshold is reached."""
        from Py4GWCoreLib import AgentArray

        ally_ids = AgentArray.GetAllyArray()
        ally_ids = AgentArray.Filter.ByDistance(ally_ids, Player.GetXY(), within_range)
        # Shared-memory aware liveness/health: in multibox, remote heroes' health
        # on this client can be stale; Routines.Checks.Agents.* falls back to
        # the broadcast AgentData from same-party shared-memory accounts.
        ally_ids = AgentArray.Filter.ByCondition(ally_ids, lambda aid: Routines.Checks.Agents.IsAlive(aid))

        count = 0
        for aid in ally_ids:
            if Routines.Checks.Agents.GetHealth(aid) < less_health_than_percent:
                count += 1
                if count >= min_allies_count:
                    return True
        return False
    #endregion

    #region S
    def Spirit_Light(
        self,
        *,
        health_threshold: float | None = None,
        self_only: bool = False,
    ) -> BuildCoroutine:
        spirit_light_id: int = Skill.GetID("Spirit_Light")
        spirit_light: CustomSkill = self.build.GetCustomSkill(spirit_light_id)
        threshold: float = (
            health_threshold
            if health_threshold is not None
            else float(spirit_light.Conditions.LessLife or 0.60)
        )
        threshold = max(0.0, min(1.0, threshold))

        def _can_safely_cast_spirit_light() -> bool:
            try:
                own_health = float(Routines.Checks.Agents.GetHealth(Player.GetAgentID()))
            except Exception:
                own_health = float(Agent.GetHealth(Player.GetAgentID()))
            return (
                self._has_spirit_in_earshot()
                or own_health > spirit_light.Conditions.SacrificeHealth
            )

        def _resolve_spirit_light_target() -> int:
            # EXP3: choose directly from same-party allies using shared-memory
            # health. ResolvePreferredAllyTarget first ranks with the local
            # TargetLowestAlly helper; in multibox runs that local HP snapshot
            # can disagree with shared AgentData. The EXP2 telemetry caught
            # exactly that case: a shared-health candidate below threshold was
            # known, yet the helper selected/rejected another ally and returned
            # no target. Direct shared-health ranking removes that split-brain
            # path while retaining spellcast range and party-only filtering.
            try:
                from Py4GWCoreLib.HeroAI.targeting import GetAllAlliesArray
                ally_array = list(GetAllAlliesArray(Range.Spellcast.value) or [])
            except Exception:
                ally_array = []

            party_agent_ids = self._actual_party_agent_ids()
            spirit_near = self._has_spirit_in_earshot()
            player_id = int(Player.GetAgentID() or 0)
            candidates: list[tuple[float, int]] = []

            for raw_agent_id in ally_array:
                agent_id = int(raw_agent_id or 0)
                if agent_id <= 0:
                    continue
                if party_agent_ids and agent_id not in party_agent_ids:
                    continue
                # Without a spirit, preserve the existing safety policy: do
                # not self-cast Spirit Light and pay the sacrifice into our own
                # heal. Other allies remain valid if the caster can safely pay.
                if not spirit_near and agent_id == player_id:
                    continue
                try:
                    if not Routines.Checks.Agents.IsAlive(agent_id):
                        continue
                    hp = float(Routines.Checks.Agents.GetHealth(agent_id))
                except Exception:
                    continue
                if hp < threshold:
                    candidates.append((hp, agent_id))

            if candidates:
                candidates.sort(key=lambda item: (item[0], item[1]))
                return int(candidates[0][1])

            # EXP10: never spend Spirit Light on non-party allies. If we can
            # resolve the party but no party member is below threshold, there
            # is intentionally no heal target (prevents NPC/minion overheal).
            if party_agent_ids:
                return 0

            # Conservative fallback only when the party API itself is temporarily
            # unavailable.
            if spirit_near:
                return self.build.ResolvePreferredAllyTarget(
                    spirit_light_id,
                    spirit_light,
                    validator=lambda agent_id: (
                        Routines.Checks.Agents.IsAlive(agent_id)
                        and Routines.Checks.Agents.GetHealth(agent_id) < threshold
                    ),
                )

            other_ally_skill = deepcopy(spirit_light)
            other_ally_skill.TargetAllegiance = Skilltarget.OtherAlly.value
            return self.build.ResolvePreferredAllyTarget(
                spirit_light_id,
                other_ally_skill,
                validator=lambda agent_id: (
                    Routines.Checks.Agents.IsAlive(agent_id)
                    and agent_id != player_id
                    and Routines.Checks.Agents.GetHealth(agent_id) < threshold
                ),
            )

        if not self.build.IsSkillEquipped(spirit_light_id):
            return False
        if not _can_safely_cast_spirit_light():
            return False

        if self_only:
            caster_id = int(Player.GetAgentID() or 0)
            party_ids = self._actual_party_agent_ids()
            def _safe_self_heal() -> bool:
                # Never self-cast Spirit Light without a nearby spirit: that
                # would sacrifice the BiP caster's HP rather than recover it.
                return bool(
                    caster_id > 0 and caster_id in party_ids
                    and Agent.IsAlive(caster_id)
                    and float(Agent.GetHealth(caster_id)) < threshold
                    and self._has_spirit_in_earshot()
                )
            if not _safe_self_heal():
                return False
            return (yield from self.build.CastSkillIDAndRestoreTarget(
                spirit_light_id, caster_id,
                extra_condition=_safe_self_heal, dispatch_guard=_safe_self_heal,
            ))

        target_agent_id = _resolve_spirit_light_target()
        if not target_agent_id:
            return False

        return (yield from self.build.CastSkillIDAndRestoreTarget(
            spirit_light_id,
            target_agent_id,
            extra_condition=_can_safely_cast_spirit_light,
        ))

    def Spirit_Transfer(
        self,
        *,
        health_threshold: float | None = None,
    ) -> BuildCoroutine:
        spirit_transfer_id: int = Skill.GetID("Spirit_Transfer")
        spirit_transfer: CustomSkill = self.build.GetCustomSkill(spirit_transfer_id)
        threshold: float = (
            health_threshold
            if health_threshold is not None
            else float(spirit_transfer.Conditions.LessLife or 0.50)
        )
        threshold = max(0.0, min(1.0, threshold))

        def _resolve_spirit_transfer_target() -> int:
            if not Routines.Agents.GetNearestSpirit(Range.Spellcast.value):
                return 0

            return self.build.ResolvePreferredAllyTarget(
                spirit_transfer_id,
                spirit_transfer,
                validator=lambda agent_id: Agent.IsAlive(agent_id) and Agent.GetHealth(agent_id) < threshold,
            )

        if not self.build.IsSkillEquipped(spirit_transfer_id):
            return False

        target_agent_id = _resolve_spirit_transfer_target()
        if not target_agent_id:
            return False

        return (yield from self.build.CastSkillIDAndRestoreTarget(
            spirit_transfer_id,
            target_agent_id,
        ))
    #endregion

    #region W
    def Wielders_Boon(self) -> BuildCoroutine:
        wielders_boon_id: int = Skill.GetID("Wielders_Boon")
        wielders_boon: CustomSkill = self.build.GetCustomSkill(wielders_boon_id)
        health_threshold: float = max(0.0, min(1.0, float(wielders_boon.Conditions.LessLife or 0.70)))

        def _resolve_wielders_boon_target() -> int:
            return self.build.ResolvePreferredAllyTarget(
                wielders_boon_id,
                wielders_boon,
                variants=[
                    None,
                    lambda custom_skill: setattr(custom_skill.Conditions, "HasWeaponSpell", False),
                ],
                validator=lambda agent_id: Agent.IsAlive(agent_id) and Agent.GetHealth(agent_id) < health_threshold,
            )

        if not self.build.IsSkillEquipped(wielders_boon_id):
            return False

        target_agent_id = _resolve_wielders_boon_target()
        if not target_agent_id:
            return False

        return (yield from self.build.CastSkillIDAndRestoreTarget(
            wielders_boon_id,
            target_agent_id,
        ))
    #endregion
