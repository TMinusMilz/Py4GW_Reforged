from Py4GWCoreLib import BuildMgr, Profession
from Py4GWCoreLib.Skill import Skill
from Py4GWCoreLib.Builds.Any.HeroAI import HeroAI as HeroAIBuild

Aura_of_the_Lich_ID = Skill.GetID("Aura_of_the_Lich")
Animate_Bone_Minions_ID = Skill.GetID("Animate_Bone_Minions")
Death_Nova_ID = Skill.GetID("Death_Nova")
Putrid_Bile_ID = Skill.GetID("Putrid_Bile")
Aegis_ID = Skill.GetID("Aegis")
Spirit_Bond_ID = Skill.GetID("Spirit_Bond")
Reversal_of_Fortune_ID = Skill.GetID("Reversal_of_Fortune")
Shield_of_Absorption_ID = Skill.GetID("Shield_of_Absorption")


class MM_Hero_Minion_Bomber(BuildMgr):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="MM Hero - Aura Minion Bomber",
            required_primary=Profession.Necromancer,
            required_skills=[Aura_of_the_Lich_ID, Animate_Bone_Minions_ID, Death_Nova_ID, Putrid_Bile_ID],
            optional_skills=[Aegis_ID, Spirit_Bond_ID, Reversal_of_Fortune_ID, Shield_of_Absorption_ID],
        )
        if match_only:
            return
        self.SetFallback("HeroAI", HeroAIBuild(standalone_fallback=True))
        self.SetSkillCastingFn(self._generic)

    def GetBlockedSkills(self) -> list[int]:
        return []

    def _generic(self):
        if False:
            yield
        return False
