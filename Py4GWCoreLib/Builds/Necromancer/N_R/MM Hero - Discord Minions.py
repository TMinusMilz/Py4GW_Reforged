from Py4GWCoreLib import BuildMgr, Profession
from Py4GWCoreLib.Skill import Skill
from Py4GWCoreLib.Builds.Any.HeroAI import HeroAI as HeroAIBuild

Discord_ID = Skill.GetID("Discord")
Animate_Bone_Minions_ID = Skill.GetID("Animate_Bone_Minions")
Death_Nova_ID = Skill.GetID("Death_Nova")
Putrid_Bile_ID = Skill.GetID("Putrid_Bile")
Enfeebling_Blood_ID = Skill.GetID("Enfeebling_Blood")
Signet_of_Sorrow_ID = Skill.GetID("Signet_of_Sorrow")
Signet_of_Lost_Souls_ID = Skill.GetID("Signet_of_Lost_Souls")
Edge_of_Extinction_ID = Skill.GetID("Edge_of_Extinction")


class MM_Hero_Discord_Minions(BuildMgr):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="MM Hero - Discord Minions",
            required_primary=Profession.Necromancer,
            required_secondary=Profession.Ranger,
            template_code="OAJUUYzeErRxMVBoBKg2BTVVVQHA",
            required_skills=[
                Discord_ID,
                Animate_Bone_Minions_ID,
                Death_Nova_ID,
                Putrid_Bile_ID,
                Enfeebling_Blood_ID,
            ],
            optional_skills=[
                Signet_of_Sorrow_ID,
                Signet_of_Lost_Souls_ID,
                Edge_of_Extinction_ID,
            ],
        )
        if match_only:
            return
        self.SetFallback("HeroAI", HeroAIBuild(standalone_fallback=True))
        self.SetSkillCastingFn(self._use_generic_hero_ai)

    # This BuildMgr entry primarily describes the exact hero template for the
    # Build Browser. If it is ever used by a player account, generic HeroAI is
    # allowed to operate the entire bar instead of this metadata wrapper
    # suppressing its supported skills.
    def GetBlockedSkills(self) -> list[int]:
        return []

    def _use_generic_hero_ai(self):
        if False:
            yield
        return False
