from __future__ import annotations

from typing import TYPE_CHECKING

from Py4GWCoreLib.BuildMgr import BuildCoroutine
from Py4GWCoreLib.Skill import Skill

if TYPE_CHECKING:
    from Py4GWCoreLib.BuildMgr import BuildMgr

__all__ = ["Expertise"]


class Expertise:
    def __init__(self, build: BuildMgr) -> None:
        self.build: BuildMgr = build

    #region T
    def Together_as_One(self) -> BuildCoroutine:
        together_as_one_id: int = Skill.GetID("Together_as_one")

        if not self.build.IsSkillEquipped(together_as_one_id):
            return False

        return (yield from self.build.CastSkillID(
            skill_id=together_as_one_id,
            log=False,
            aftercast_delay=250,
        ))
    #endregion

    def Throw_Dirt(self) -> BuildCoroutine:
        from Py4GWCoreLib import Agent, Range, Routines

        throw_dirt_id: int = Skill.GetID("Throw_Dirt")
        if not self.build.IsSkillEquipped(throw_dirt_id):
            return False

        target_agent_id = getattr(Routines.Targeting, "PickClusteredTarget")(
            cluster_radius=Range.Adjacent.value,
            preferred_condition=Agent.IsMartial,
            filter_radius=Range.Touch.value,
        )
        if not target_agent_id:
            return False

        return (yield from self.build.CastSkillIDAndRestoreTarget(
            skill_id=throw_dirt_id,
            target_agent_id=target_agent_id,
            log=False,
            aftercast_delay=250,
        ))

    def Whirling_Defense(self, *, refresh_window_ms: int = 2000) -> BuildCoroutine:
        from Py4GWCoreLib import GLOBAL_CACHE, Player, Routines

        whirling_defense_id: int = Skill.GetID("Whirling_Defense")
        if not self.build.IsSkillEquipped(whirling_defense_id):
            return False

        player_agent_id = Player.GetAgentID()
        if getattr(Routines.Checks.Effects, "HasBuff")(player_agent_id, whirling_defense_id):
            remaining_ms = int(getattr(GLOBAL_CACHE.Effects, "GetEffectTimeRemaining")(
                player_agent_id,
                whirling_defense_id,
            ) or 0)
            if remaining_ms > refresh_window_ms:
                return False

        return (yield from self.build.CastSkillID(
            skill_id=whirling_defense_id,
            log=False,
            aftercast_delay=250,
        ))

    def Experts_Dexterity(self, *, refresh_window_ms: int = 2000) -> BuildCoroutine:
        from Py4GWCoreLib import GLOBAL_CACHE, Player, Routines

        experts_dexterity_id: int = Skill.GetID("Experts_Dexterity")
        if not self.build.IsSkillEquipped(experts_dexterity_id):
            return False

        player_agent_id = Player.GetAgentID()
        if Routines.Checks.Agents.HasEffect(player_agent_id, experts_dexterity_id):
            remaining_ms = int(GLOBAL_CACHE.Effects.GetEffectTimeRemaining(
                player_agent_id,
                experts_dexterity_id,
            ) or 0)
            if remaining_ms > refresh_window_ms:
                return False

        return (yield from self.build.CastSkillID(
            skill_id=experts_dexterity_id,
            log=False,
            aftercast_delay=250,
        ))
