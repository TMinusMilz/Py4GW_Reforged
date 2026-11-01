from __future__ import annotations

from Py4GWCoreLib import GLOBAL_CACHE
from Py4GWCoreLib import Agent
from Py4GWCoreLib import BuildMgr
from Py4GWCoreLib import Player
from Py4GWCoreLib import Profession
from Py4GWCoreLib import Routines
from Py4GWCoreLib import Utils
from Py4GWCoreLib.BuildMgr import BuildCoroutine
from Py4GWCoreLib.Builds.Any.HeroAI import HeroAI_Build
from Py4GWCoreLib.Builds.Skills import SkillsTemplate
from Py4GWCoreLib.enums_src.GameData_enums import Attribute
from Py4GWCoreLib.Skill import Skill

Deadly_Paradox_ID = Skill.GetID("Deadly_Paradox")
Shadow_Form_ID = Skill.GetID("Shadow_Form")
Shroud_of_Distress_ID = Skill.GetID("Shroud_of_Distress")
Unseen_Fury_ID = Skill.GetID("Unseen_Fury")
I_Am_Unstoppable_ID = Skill.GetID("I_Am_Unstoppable")
Dwarven_Stability_ID = Skill.GetID("Dwarven_Stability")
Whirling_Defense_ID = Skill.GetID("Whirling_Defense")
Throw_Dirt_ID = Skill.GetID("Throw_Dirt")
Quickening_Zephyr_ID = Skill.GetID("Quickening_Zephyr")

_REQUIRED_SKILLS = [
    Deadly_Paradox_ID,
    Shadow_Form_ID,
    Shroud_of_Distress_ID,
    Unseen_Fury_ID,
    I_Am_Unstoppable_ID,
    Dwarven_Stability_ID,
    Whirling_Defense_ID,
]


def _generate_template_code() -> str:
    return Utils.GenerateSkillbarTemplateFrom(
        int(Profession.Ranger.value),
        int(Profession.Assassin.value),
        {
            int(Attribute.Expertise.value): 12,
            int(Attribute.ShadowArts.value): 12,
            int(Attribute.DeadlyArts.value): 3,
        },
        [*_REQUIRED_SKILLS, Throw_Dirt_ID],
    )


class Whirling_Defense_City_Farmer(BuildMgr):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Whirling Defense City Farmer",
            required_primary=Profession.Ranger,
            required_secondary=Profession.Assassin,
            template_code=_generate_template_code(),
            required_skills=_REQUIRED_SKILLS,
            optional_skills=[Throw_Dirt_ID],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAI_Build(standalone_fallback=True))
        self.SetSkillCastingFn(self._run_local_skill_logic)
        self.skills: SkillsTemplate = SkillsTemplate(self)
        self._deadly_paradox_used_for_shadow_form = False
        self._unseen_fury_used_for_whirling = False

    @staticmethod
    def _effect_remaining_ms(agent_id: int, skill_id: int) -> int:
        has_buff = getattr(Routines.Checks.Effects, "HasBuff")
        if not has_buff(agent_id, skill_id):
            return 0
        try:
            remaining_ms = getattr(GLOBAL_CACHE.Effects, "GetEffectTimeRemaining")(agent_id, skill_id)
            return max(0, int(remaining_ms or 0))
        except Exception:
            return 0

    def _run_local_skill_logic(self) -> BuildCoroutine:
        if not Routines.Checks.Skills.CanCast():
            return False
        if not (self.IsInAggro() or self.IsCloseToAggro()):
            return False

        player_agent_id = Player.GetAgentID()
        player_energy_pct = float(Agent.GetEnergy(player_agent_id) or 0.0)

        shadow_form_remaining_ms = self._effect_remaining_ms(player_agent_id, Shadow_Form_ID)
        if shadow_form_remaining_ms <= 2000 and self.CanCastSkillID(Shadow_Form_ID):
            has_buff = getattr(Routines.Checks.Effects, "HasBuff")
            deadly_paradox_active = self._deadly_paradox_used_for_shadow_form or bool(
                has_buff(player_agent_id, Deadly_Paradox_ID)
            )
            quickening_zephyr_active = self.SpiritBuffExists(Quickening_Zephyr_ID)
            if not quickening_zephyr_active and not deadly_paradox_active and self.CanCastSkillID(Deadly_Paradox_ID):
                if (yield from self.skills.Assassin.ShadowArts.Deadly_Paradox()):
                    self._deadly_paradox_used_for_shadow_form = True
                    return True
            if (yield from self.skills.Assassin.ShadowArts.Shadow_Form()):
                self._deadly_paradox_used_for_shadow_form = False
                return True

        if (yield from self.skills.Assassin.ShadowArts.Shroud_of_Distress()):
            return True
        if (yield from self.skills.Any.PvE.Dwarven_Stability()):
            return True
        if (yield from self.skills.Any.PvE.I_Am_Unstoppable()):
            return True

        whirling_remaining_ms = self._effect_remaining_ms(player_agent_id, Whirling_Defense_ID)
        if whirling_remaining_ms <= 2000 and self.CanCastSkillID(Whirling_Defense_ID):
            unseen_fury_remaining_ms = self._effect_remaining_ms(player_agent_id, Unseen_Fury_ID)
            if not self._unseen_fury_used_for_whirling and unseen_fury_remaining_ms <= 0 and player_energy_pct >= 0.45:
                if (yield from self.skills.Assassin.ShadowArts.Unseen_Fury()):
                    self._unseen_fury_used_for_whirling = True
                    return True
            if (yield from self.skills.Ranger.Expertise.Whirling_Defense()):
                self._unseen_fury_used_for_whirling = False
                return True

        if self.IsInAggro() and player_energy_pct >= 0.35:
            if (yield from self.skills.Ranger.Expertise.Throw_Dirt()):
                return True

        return False
