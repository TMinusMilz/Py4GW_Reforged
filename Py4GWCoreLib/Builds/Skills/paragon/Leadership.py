from __future__ import annotations

from typing import TYPE_CHECKING

from Py4GWCoreLib.BuildMgr import BuildCoroutine
from Py4GWCoreLib import Player, Routines
from Py4GWCoreLib.Skill import Skill

if TYPE_CHECKING:
    from Py4GWCoreLib.BuildMgr import BuildMgr

__all__ = ["Leadership"]


class Leadership:
    def __init__(self, build: BuildMgr) -> None:
        self.build: BuildMgr = build

    def _get_leadership_level(self) -> int:
        from Py4GWCoreLib import Agent

        player_agent_id = Player.GetAgentID()
        attributes = Agent.GetAttributes(player_agent_id)
        leadership = next((attribute for attribute in attributes if attribute.GetName() == "Leadership"), None)
        return int(getattr(leadership, "level", 0) or 0)

    def IsHeroicRefrainSelfReady(self) -> bool:
        """Return whether the self copy of Heroic Refrain is active.

        Older project code used a Leadership >=20 test that assumed the former
        +4 Heroic Refrain value.  The live skill no longer guarantees that
        number, so keeping the rank gate can make the Paragon recast HR on self
        forever instead of spreading it.  The effect itself is authoritative.
        """
        heroic_refrain_id = Skill.GetID("Heroic_Refrain")
        player_agent_id = Player.GetAgentID()
        return bool(Routines.Checks.Agents.HasEffect(player_agent_id, heroic_refrain_id))

    def _heroic_refrain_needs_self_bootstrap(self, heroic_refrain_id: int) -> bool:
        return not self.IsHeroicRefrainSelfReady()

    @staticmethod
    def _shared_skillbar_ids(account) -> set[int]:
        try:
            skillbar = getattr(getattr(account, "AgentData", None), "Skillbar", None)
            return {
                int(getattr(skill, "Id", 0) or 0)
                for skill in (getattr(skillbar, "Skills", ()) or ())
                if int(getattr(skill, "Id", 0) or 0) > 0
            }
        except Exception:
            return set()

    def _mending_refrain_owner_target(self, heroic_refrain_id: int) -> int:
        """Prioritize whichever account carries Mending Refrain immediately after self.

        Mending Refrain snapshots the caster's Motivation when applied. Giving
        the Mending owner Heroic Refrain before it starts its OOC spread makes
        those copies use the HR-boosted Motivation value.
        """
        try:
            from Py4GWCoreLib import Agent, GLOBAL_CACHE, Range, Utils
            from Py4GWCoreLib.HeroAI.utils import CheckForEffect, SameMapOrPartyAsAccount

            mending_refrain_id = int(Skill.GetID("Mending_Refrain") or 0)
            if mending_refrain_id <= 0:
                return 0

            own_party_id = int(GLOBAL_CACHE.Party.GetPartyID() or 0)
            player_id = int(Player.GetAgentID() or 0)
            player_xy = Player.GetXY()
            candidates: list[int] = []

            def consider(account) -> None:
                if not getattr(account, "IsSlotActive", False) or getattr(account, "IsIsolated", False):
                    return
                if not SameMapOrPartyAsAccount(account):
                    return
                party_id = int(getattr(getattr(account, "AgentPartyData", None), "PartyID", 0) or 0)
                if own_party_id > 0 and party_id != own_party_id:
                    return
                agent_data = getattr(account, "AgentData", None)
                agent_id = int(getattr(agent_data, "AgentID", 0) or 0)
                if agent_id <= 0 or agent_id == player_id:
                    return
                if mending_refrain_id not in self._shared_skillbar_ids(account):
                    return
                if not Agent.IsValid(agent_id) or not Agent.IsAlive(agent_id):
                    return
                if Utils.Distance(Agent.GetXY(agent_id), player_xy) > float(Range.Spellcast.value):
                    return
                if CheckForEffect(agent_id, int(heroic_refrain_id)):
                    return
                candidates.append(agent_id)

            for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
                consider(account)
                owner_id = int(getattr(getattr(account, "AgentData", None), "AgentID", 0) or 0)
                for hero_account in GLOBAL_CACHE.ShMem.GetHeroesFromPlayers(owner_id) or []:
                    consider(hero_account)

            return min(candidates) if candidates else 0
        except Exception:
            return 0

    @staticmethod
    def _log_hr_event(event: str, **fields) -> None:
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(str(event), **fields)
        except Exception:
            pass

    #region H
    def Heroic_Refrain(self) -> BuildCoroutine:
        heroic_refrain_id: int = Skill.GetID("Heroic_Refrain")
        heroic_refrain = self.build.GetCustomSkill(heroic_refrain_id)

        if not self.build.IsSkillEquipped(heroic_refrain_id):
            return False

        player_agent_id = Player.GetAgentID()
        if self._heroic_refrain_needs_self_bootstrap(heroic_refrain_id):
            did_cast = yield from self.build.CastSkillIDAndRestoreTarget(
                skill_id=heroic_refrain_id,
                target_agent_id=player_agent_id,
                log=False,
                aftercast_delay=250,
            )
            if did_cast:
                self._log_hr_event(
                    "HR_HEROIC_SELF_BOOTSTRAP",
                    target_id=int(player_agent_id),
                    policy="self_first_then_mending_owner_then_party",
                )
            return bool(did_cast)

        mending_owner = int(self._mending_refrain_owner_target(heroic_refrain_id) or 0)
        if mending_owner > 0:
            did_cast = yield from self.build.CastSkillIDAndRestoreTarget(
                skill_id=heroic_refrain_id,
                target_agent_id=mending_owner,
                log=False,
                aftercast_delay=250,
            )
            if did_cast:
                self._log_hr_event(
                    "HR_HEROIC_MENDING_OWNER_PRIORITY",
                    target_id=int(mending_owner),
                    policy="mending_owner_second_for_hr_boosted_motivation_snapshot",
                )
            return bool(did_cast)

        target_agent_id = self.build.ResolveAllyTarget(
            heroic_refrain_id,
            heroic_refrain,
        )
        if not target_agent_id:
            return False

        did_cast = yield from self.build.CastSkillIDAndRestoreTarget(
            skill_id=heroic_refrain_id,
            target_agent_id=target_agent_id,
            log=False,
            aftercast_delay=250,
        )
        if did_cast:
            self._log_hr_event(
                "HR_HEROIC_PARTY_SPREAD",
                target_id=int(target_agent_id),
                policy="after_self_and_mending_owner",
            )
        return bool(did_cast)
    #endregion

    #region A
    def Anthem_of_Flame(self) -> BuildCoroutine:
        anthem_of_flame_id: int = Skill.GetID("Anthem_of_Flame")
        player_agent_id = Player.GetAgentID()

        if not self.build.IsSkillEquipped(anthem_of_flame_id):
            return False
        if Routines.Checks.Agents.HasEffect(player_agent_id, anthem_of_flame_id):
            return False

        return (yield from self.build.CastSkillID(
            skill_id=anthem_of_flame_id,
            target_agent_id=player_agent_id,
            log=False,
            aftercast_delay=250,
        ))

    def Aggressive_Refrain(self) -> BuildCoroutine:
        """Apply the 25% IAS echo once; the bar's shouts keep it up from there.

        Not gated on aggro. Aggressive Refrain is reapplied every time a chant
        or shout ends on us, so on any bar carrying shouts it never drops once
        it lands - which means holding it back until combat buys nothing. The
        -20 armor is permanent either way, and waiting only costs us the attack
        speed for the opening of every fight.
        """
        aggressive_refrain_id: int = Skill.GetID("Aggressive_Refrain")
        player_agent_id = Player.GetAgentID()

        if not self.build.IsSkillEquipped(aggressive_refrain_id):
            return False
        if Routines.Checks.Agents.HasEffect(player_agent_id, aggressive_refrain_id):
            return False

        return (yield from self.build.CastSkillID(
            skill_id=aggressive_refrain_id,
            target_agent_id=player_agent_id,
            log=False,
            aftercast_delay=250,
        ))

    def Angelic_Protection(
        self,
        *,
        health_threshold: float | None = None,
    ) -> BuildCoroutine:
        from Py4GWCoreLib.Agent import Agent

        angelic_protection_id: int = Skill.GetID("Angelic_Protection")
        angelic_protection = self.build.GetCustomSkill(angelic_protection_id)

        if not self.build.IsSkillEquipped(angelic_protection_id):
            return False
        if not self.build.IsInAggro():
            return False

        threshold: float = (
            health_threshold
            if health_threshold is not None
            else float(angelic_protection.Conditions.LessLife or 0.75)
        )
        threshold = max(0.0, min(1.0, threshold))

        target_agent_id = self.build.ResolvePreferredAllyTarget(
            angelic_protection_id,
            angelic_protection,
            validator=lambda agent_id: Agent.IsAlive(agent_id) and Agent.GetHealth(agent_id) < threshold,
        )
        if not target_agent_id:
            return False

        return (yield from self.build.CastSkillIDAndRestoreTarget(
            skill_id=angelic_protection_id,
            target_agent_id=target_agent_id,
            log=False,
            aftercast_delay=250,
        ))
    #endregion

    #region M
    def Make_Your_Time(self) -> BuildCoroutine:
        from Py4GWCoreLib import Agent, AgentArray, Range
        from Py4GWCoreLib.HeroAI.utils import IsPartyMember

        make_your_time_id: int = Skill.GetID("Make_Your_Time")
        player_agent_id = Player.GetAgentID()

        if not self.build.IsSkillEquipped(make_your_time_id):
            return False

        if not (self.build.IsInAggro() or self.build.IsCloseToAggro()):
            return False

        candidates = AgentArray.GetAllyArray() + AgentArray.GetSpiritPetArray()
        candidates = AgentArray.Filter.ByDistance(candidates, Player.GetXY(), Range.Earshot.value)
        candidates = AgentArray.Filter.ByCondition(candidates, lambda a: Agent.IsAlive(a) and IsPartyMember(a))
        if len(candidates) < 3:
            return False

        return (yield from self.build.CastSkillID(
            skill_id=make_your_time_id,
            target_agent_id=player_agent_id,
            log=False,
            aftercast_delay=250,
        ))
    #endregion

    #region T
    def Theyre_on_Fire(self) -> BuildCoroutine:
        theyre_on_fire_id: int = Skill.GetID("Theyre_on_Fire")
        player_agent_id = Player.GetAgentID()

        if not self.build.IsSkillEquipped(theyre_on_fire_id):
            return False
        if Routines.Checks.Agents.HasEffect(player_agent_id, theyre_on_fire_id):
            return False

        return (yield from self.build.CastSkillID(
            skill_id=theyre_on_fire_id,
            target_agent_id=player_agent_id,
            log=False,
            aftercast_delay=250,
        ))
    #endregion
