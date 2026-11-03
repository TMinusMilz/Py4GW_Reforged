from __future__ import annotations

from Py4GWCoreLib import BuildMgr, Profession, Routines
from Py4GWCoreLib.BuildMgr import BuildCoroutine
from Py4GWCoreLib.Builds.Any.HeroAI import HeroAI_Build
from Py4GWCoreLib.Builds.Skills import SkillsTemplate
from Py4GWCoreLib.Skill import Skill


Experts_Dexterity_ID = Skill.GetID("Experts_Dexterity")
Ebon_Battle_Standard_of_Honor_ID = Skill.GetID("Ebon_Battle_Standard_of_Honor")
Volley_ID = Skill.GetID("Volley")
Sundering_Attack_ID = Skill.GetID("Sundering_Attack")
Penetrating_Attack_ID = Skill.GetID("Penetrating_Attack")
I_Am_the_Strongest_ID = Skill.GetID("I_Am_the_Strongest")
Mindbender_ID = Skill.GetID("Mindbender")

# Optional-slot variants from the PvX build. They are recognised (so HeroAI
# does not block them) but the rotation below stays focused on the core bar.
Way_of_the_Master_ID = Skill.GetID("Way_of_the_Master")
Heart_of_Holy_Flame_ID = Skill.GetID("Heart_of_Holy_Flame")
Ebon_Vanguard_Assassin_Support_ID = Skill.GetID("Ebon_Vanguard_Assassin_Support")
Pain_Inverter_ID = Skill.GetID("Pain_Inverter")
Technobabble_ID = Skill.GetID("Technobabble")
Asuran_Scan_ID = Skill.GetID("Asuran_Scan")
Dodge_This_ID = Skill.GetID("Dodge_This")
Distracting_Shot_ID = Skill.GetID("Distracting_Shot")
Comfort_Animal_ID = Skill.GetID("Comfort_Animal")


class Experts_Dexterity_Bow(BuildMgr):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Expert's Dexterity Bow",
            required_primary=Profession.Ranger,
            required_secondary=Profession._None,
            template_code="OgASc5MT8a5iAAghnSOGzkrlA",
            required_skills=[
                Experts_Dexterity_ID,
                Volley_ID,
                Sundering_Attack_ID,
                Penetrating_Attack_ID,
                I_Am_the_Strongest_ID,
            ],
            optional_skills=[
                Ebon_Battle_Standard_of_Honor_ID,
                Mindbender_ID,
                Way_of_the_Master_ID,
                Heart_of_Holy_Flame_ID,
                Ebon_Vanguard_Assassin_Support_ID,
                Pain_Inverter_ID,
                Technobabble_ID,
                Asuran_Scan_ID,
                Dodge_This_ID,
                Distracting_Shot_ID,
                Comfort_Animal_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAI_Build(standalone_fallback=True))
        self.SetSkillCastingFn(self._run_local_skill_logic)
        self.skills: SkillsTemplate = SkillsTemplate(self)

    def _run_local_skill_logic(self) -> BuildCoroutine:
        if not Routines.Checks.Skills.CanCast():
            return False

        # Mindbender is a mobility buff: keep it up in and out of combat so the
        # player can reposition between packs without waiting on the combat gate.
        if (yield from self.skills.Any.PvE.Mindbender()):
            return True

        if not (self.IsInAggro() or self.IsCloseToAggro()):
            return False

        # Expert's Dexterity is the point of the bar: +33% attack speed and
        # +2 Marksmanship. Refresh it before it falls off.
        if (yield from self.skills.Ranger.Expertise.Experts_Dexterity()):
            return True

        # "I Am the Strongest!" adds +20 damage to the next 8 attacks; the
        # helper only recasts once the effect has been consumed.
        if (yield from self.skills.Any.PvE.I_Am_the_Strongest()):
            return True

        # Ebon Battle Standard of Honor adds +15 damage to non-spirit allies'
        # attacks while we stand in it. The shared helper's >=2-martial gate
        # never fires for a solo bow ranger, hence the build-local resolver.
        if (yield from self._cast_ebon_battle_standard_of_honor()):
            return True

        # Chain bow attack skills to exploit their predefined attack speed:
        # Volley first for balled foes, then the single-target armor-piercing
        # attacks to keep the attack chain rolling.
        if (yield from self.skills.Ranger.Marksmanship.Volley()):
            return True
        if (yield from self.skills.Ranger.Marksmanship.Penetrating_Attack()):
            return True
        if (yield from self.skills.Ranger.Marksmanship.Sundering_Attack()):
            return True

        return False

    def _cast_ebon_battle_standard_of_honor(self) -> BuildCoroutine:
        from Py4GWCoreLib import Player, Routines

        if not self.IsSkillEquipped(Ebon_Battle_Standard_of_Honor_ID):
            return False
        if Routines.Checks.Agents.HasEffect(
            Player.GetAgentID(),
            Ebon_Battle_Standard_of_Honor_ID,
        ):
            return False

        # CastSkillID still enforces the CustomSkill's EnemyCount=3 earshot
        # gate, so this only fires when the ward is actually worth planting.
        return (yield from self.CastSkillID(
            skill_id=Ebon_Battle_Standard_of_Honor_ID,
            log=False,
            aftercast_delay=250,
        ))
