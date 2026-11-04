from __future__ import annotations

from Py4GWCoreLib import BuildMgr, Profession, Routines, ThrottledTimer
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
            ],
            optional_skills=[
                I_Am_the_Strongest_ID,
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
        self._diag_timer = ThrottledTimer(2000)
        self._log_diag(
            "BOW_BUILD_INSTANTIATED",
            required=",".join(str(s) for s in self.required_skills),
            optional=",".join(str(s) for s in self.optional_skills),
        )

    def _log_diag(self, event: str, **fields: object) -> None:
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug

            CombatDebug.log_event(event, **fields)
        except Exception:
            pass

    def _run_local_skill_logic(self) -> BuildCoroutine:
        if not Routines.Checks.Skills.CanCast():
            if self._diag_timer.IsExpired():
                self._diag_timer.Reset()
                self._log_diag("BOW_BUILD_TICK", event="blocked_can_cast")
            return False

        # Self buffs are maintained in and out of combat, mirroring the TaO
        # dagger build: they cast while the player moves/positions, before the
        # aggro gate below. Each helper only fires when its effect is missing
        # or about to expire.
        if (yield from self.skills.Any.PvE.Mindbender()):
            self._diag_timer.Reset()
            self._log_diag("BOW_BUILD_TICK", event="cast_mindbender")
            return True

        if (yield from self.skills.Ranger.Expertise.Experts_Dexterity()):
            self._diag_timer.Reset()
            self._log_diag("BOW_BUILD_TICK", event="cast_experts_dexterity")
            return True

        if (yield from self.skills.Any.PvE.I_Am_the_Strongest()):
            self._diag_timer.Reset()
            self._log_diag("BOW_BUILD_TICK", event="cast_i_am_the_strongest")
            return True

        # The ward's CustomSkill gate (3+ enemies in earshot) still applies
        # inside CastSkillID, so this stays quiet outside real fights.
        if (yield from self._cast_ebon_battle_standard_of_honor()):
            self._diag_timer.Reset()
            self._log_diag("BOW_BUILD_TICK", event="cast_ebsoh")
            return True

        if not (self.IsInAggro() or self.IsCloseToAggro()):
            if self._diag_timer.IsExpired():
                self._diag_timer.Reset()
                self._log_diag(
                    "BOW_BUILD_TICK",
                    event="no_aggro",
                    in_aggro=bool(self.IsInAggro()),
                    close_to_aggro=bool(self.IsCloseToAggro()),
                )
            return False

        # Chain bow attack skills to exploit their predefined attack speed:
        # Volley first for balled foes, then the single-target armor-piercing
        # attacks to keep the attack chain rolling.
        if (yield from self.skills.Ranger.Marksmanship.Volley()):
            self._diag_timer.Reset()
            self._log_diag("BOW_BUILD_TICK", event="cast_volley")
            return True

        if (yield from self.skills.Ranger.Marksmanship.Penetrating_Attack()):
            self._diag_timer.Reset()
            self._log_diag("BOW_BUILD_TICK", event="cast_penetrating_attack")
            return True

        if (yield from self.skills.Ranger.Marksmanship.Sundering_Attack()):
            self._diag_timer.Reset()
            self._log_diag("BOW_BUILD_TICK", event="cast_sundering_attack")
            return True

        if self._diag_timer.IsExpired():
            self._diag_timer.Reset()
            self._log_diag(
                "BOW_BUILD_TICK",
                event="no_cast_ready",
                in_aggro=bool(self.IsInAggro()),
            )
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
