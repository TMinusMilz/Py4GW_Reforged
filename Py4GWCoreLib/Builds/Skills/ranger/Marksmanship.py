from __future__ import annotations

from typing import TYPE_CHECKING

from Py4GWCoreLib.BuildMgr import BuildCoroutine
from Py4GWCoreLib.Skill import Skill

if TYPE_CHECKING:
    from Py4GWCoreLib.BuildMgr import BuildMgr

class Marksmanship:
    def __init__(self, build: BuildMgr) -> None:
        self.build: BuildMgr = build

    def _resolve_bow_target(self, skill_id: int, *, prefer_cluster: bool = False) -> int:
        from Py4GWCoreLib import Range, Routines

        if not self.build.CanCastSkillID(skill_id):
            return 0

        if prefer_cluster:
            target_agent_id = Routines.Targeting.PickClusteredTarget(
                cluster_radius=Range.Adjacent.value,
                filter_radius=Range.Spellcast.value,
            )
            if target_agent_id:
                return int(target_agent_id)

        target_acquired, _ = self.build._resolve_target("EnemyInjured")
        if not target_acquired:
            return 0
        return int(self.build.current_target_id)

    def Volley(self) -> BuildCoroutine:
        volley_id: int = Skill.GetID("Volley")
        if not self.build.IsSkillEquipped(volley_id):
            return False

        target_agent_id = self._resolve_bow_target(volley_id, prefer_cluster=True)
        if not target_agent_id:
            return False

        return (yield from self.build.CastSkillIDAndRestoreTarget(
            skill_id=volley_id,
            target_agent_id=target_agent_id,
            log=False,
            aftercast_delay=250,
        ))

    def Penetrating_Attack(self) -> BuildCoroutine:
        penetrating_attack_id: int = Skill.GetID("Penetrating_Attack")
        if not self.build.IsSkillEquipped(penetrating_attack_id):
            return False

        target_agent_id = self._resolve_bow_target(penetrating_attack_id)
        if not target_agent_id:
            return False

        return (yield from self.build.CastSkillIDAndRestoreTarget(
            skill_id=penetrating_attack_id,
            target_agent_id=target_agent_id,
            log=False,
            aftercast_delay=250,
        ))

    def Sundering_Attack(self) -> BuildCoroutine:
        sundering_attack_id: int = Skill.GetID("Sundering_Attack")
        if not self.build.IsSkillEquipped(sundering_attack_id):
            return False

        target_agent_id = self._resolve_bow_target(sundering_attack_id)
        if not target_agent_id:
            return False

        return (yield from self.build.CastSkillIDAndRestoreTarget(
            skill_id=sundering_attack_id,
            target_agent_id=target_agent_id,
            log=False,
            aftercast_delay=250,
        ))
