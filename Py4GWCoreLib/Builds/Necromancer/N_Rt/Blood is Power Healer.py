from Py4GWCoreLib import Profession
from Py4GWCoreLib import Routines, Agent, Player
from Py4GWCoreLib.Builds.Any.HeroAI import HeroAI_Build
from Py4GWCoreLib import BuildMgr
from Py4GWCoreLib.Skill import Skill
from Py4GWCoreLib.Builds.Skills import SkillsTemplate

Blood_is_Power_ID = Skill.GetID("Blood_is_Power")
Signet_of_Lost_Souls_ID = Skill.GetID("Signet_of_Lost_Souls")
Mend_Body_and_Soul_ID = Skill.GetID("Mend_Body_and_Soul")
Spirit_Light_ID = Skill.GetID("Spirit_Light")
Ghostmirror_Light_ID = Skill.GetID("Ghostmirror_Light")
Protective_Was_Kaolai_ID = Skill.GetID("Protective_Was_Kaolai")
Vital_Weapon_ID = Skill.GetID("Vital_Weapon")
Wielders_Boon_ID = Skill.GetID("Wielders_Boon")
Mending_Grip_ID = Skill.GetID("Mending_Grip")
Spirit_Transfer_ID = Skill.GetID("Spirit_Transfer")
Life_ID = Skill.GetID("Life")
You_Are_All_Weaklings_ID = Skill.GetID("You_Are_All_Weaklings")
Enfeebling_Blood_ID = Skill.GetID("Enfeebling_Blood")
Recovery_ID = Skill.GetID("Recovery")
Breath_of_the_Great_Dwarf_ID = Skill.GetID("Breath_of_the_Great_Dwarf")
Recuperation_ID = Skill.GetID("Recuperation")
Blood_Bond_ID = Skill.GetID("Blood_Bond")
Ebon_Vanguard_Assassin_Support_ID = Skill.GetID("Ebon_Vanguard_Assassin_Support")


class Blood_is_Power_Healer(BuildMgr):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Blood is Power Healer",
            required_primary=Profession.Necromancer,
            required_secondary=Profession.Ritualist,
            template_code="OAhjQkGZIP3hqq0EAAAAAAAAAA",
            # Stage 2.40: the live BiP is a PLAYER ACCOUNT, not a hero.
            # Signet of Lost Souls is optional support only and must never be
            # part of the build identity / Required Skills UI.
            required_skills=[
                Blood_is_Power_ID,
                Mend_Body_and_Soul_ID,
            ],
            optional_skills=[
                Signet_of_Lost_Souls_ID,
                Spirit_Light_ID,
                Ghostmirror_Light_ID,
                Protective_Was_Kaolai_ID,
                Vital_Weapon_ID,
                Wielders_Boon_ID,
                Mending_Grip_ID,
                Spirit_Transfer_ID,
                Life_ID,
                You_Are_All_Weaklings_ID,
                Enfeebling_Blood_ID,
                Recovery_ID,
                Breath_of_the_Great_Dwarf_ID,
                Recuperation_ID,
                Blood_Bond_ID,
                Ebon_Vanguard_Assassin_Support_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAI_Build(standalone_fallback=True))
        self.SetSkillCastingFn(self._run_local_skill_logic)
        self.skills: SkillsTemplate = SkillsTemplate(self)

    @staticmethod
    def _log_bip_action(skill: str, purpose: str) -> None:
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "BIP_ACCOUNT_SKILL_CAST",
                skill=str(skill),
                purpose=str(purpose),
                policy="stage2_42_per_skill_account_usage_telemetry",
            )
        except Exception:
            pass

    def _recover_for_urgent_bip(self):
        target_id, required_hp = self.skills.Necromancer.BloodMagic.GetUrgentBiPRecoveryNeed()
        if not target_id:
            return False
        player_id = int(Player.GetAgentID() or 0)
        threshold = min(1.0, float(required_hp) + 0.006)
        # Do not consume protection-spirit health for this self-recovery tier.
        # Reserve enough energy to cast BiP after the healing spell.
        energy_cost = Routines.Checks.Skills.GetEnergyCostWithEffects
        for skill_id, heal in (
            (Mend_Body_and_Soul_ID, self.skills.Ritualist.RestorationMagic.Mend_Body_and_Soul),
            (Spirit_Light_ID, self.skills.Ritualist.RestorationMagic.Spirit_Light),
        ):
            if not self.IsSkillEquipped(skill_id) or not self.CanCastSkillID(skill_id):
                continue
            try:
                available = float(Agent.GetEnergy(player_id)) * float(Agent.GetMaxEnergy(player_id))
                heal_cost = float(energy_cost(skill_id, player_id))
                bip_cost = float(energy_cost(Blood_is_Power_ID, player_id))
                if available < heal_cost + bip_cost:
                    continue
            except Exception:
                # Unknown energy cost must never override a safety check.
                continue
            if (yield from heal(health_threshold=threshold, self_only=True)):
                purpose = "self_recovery_for_urgent_bip"
                self._log_bip_action(
                    "Mend_Body_and_Soul" if skill_id == Mend_Body_and_Soul_ID else "Spirit_Light",
                    purpose,
                )
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "BIP_SELF_RECOVERY_QUEUED",
                        required_caster_hp_pct=round(required_hp, 3),
                        recovery_skill_id=int(skill_id),
                        # Deliberately not calling this a confirmed heal.
                        policy="ur259_franky_targeted_recovery_preserve_75_80_85",
                    )
                except Exception:
                    pass
                return True
        return False

    def _run_local_skill_logic(self):
        if not Routines.Checks.Skills.CanCast():
            return False

        # emergency: Spirit Transfer when a spirit is in range and any ally is at or below 30% HP.
        if (yield from self.skills.Ritualist.RestorationMagic.Spirit_Transfer(health_threshold=0.30)):
            self._log_bip_action("Spirit_Transfer", "emergency_30")
            return True

        # emergency: spirit-gated burst heal when any ally is at or below 30% HP.
        if (yield from self.skills.Ritualist.RestorationMagic.Spirit_Light(health_threshold=0.30)):
            self._log_bip_action("Spirit_Light", "emergency_30")
            return True

        # Stage 2.42: Ghostmirror is a real live-bar heal, not a passive optional
        # slot.  Use it on a badly injured *other* party member; with a nearby
        # spirit it simultaneously repairs the BiP caster.
        if self.IsSkillEquipped(Ghostmirror_Light_ID) and (yield from self.skills.Ritualist.RestorationMagic.Ghostmirror_Light(health_threshold=0.42)):
            self._log_bip_action("Ghostmirror_Light", "emergency_other_42")
            return True

        # emergency: any ally at or below 40% HP preempts everything.
        if (yield from self.skills.Ritualist.RestorationMagic.Mend_Body_and_Soul(health_threshold=0.40)):
            self._log_bip_action("Mend_Body_and_Soul", "emergency_40")
            return True

        # Franky-inspired targeted self-recovery: only when an eligible ally
        # is urgently starved for Energy AND the unchanged BiP sacrifice floor
        # prevents safe support.  Always heal emergencies above this tier.
        if (yield from self._recover_for_urgent_bip()):
            return True

        # Protective Was Kaolai: drop the ashes to release the 85 HP party heal
        # when 4+ allies are damaged. Gated on equipped so we don't drop quest
        # items or other bundles the bot didn't put in our hand.
        if self.IsSkillEquipped(Protective_Was_Kaolai_ID) and (yield from self.skills.Ritualist.NoAttribute.Drop_Held_Bundle(health_threshold=0.75)):
            self._log_bip_action("Protective_Was_Kaolai", "drop_party_heal_75")
            return True

        # Signet of Lost Souls: emergency energy refill when caster < 30%.
        if (yield from self.skills.Necromancer.SoulReaping.Signet_of_Lost_Souls(max_self_energy_pct=0.30)):
            return True

        # Recuperation 6+ allies below 75% HP OR 6+ allies degenning.
        if self.IsSkillEquipped(Recuperation_ID) and (yield from self.skills.Ritualist.RestorationMagic.Recuperation(
            min_party_damaged_count=6,
        )):
            return True
        if self.IsSkillEquipped(Recuperation_ID) and (yield from self.skills.Ritualist.RestorationMagic.Recuperation(
            min_degen_count=6,
        )):
            return True

        if (yield from self.skills.Necromancer.BloodMagic.Blood_is_Power()):
            self._log_bip_action("Blood_is_Power", "energy_support")
            return True

        if self.IsSkillEquipped(Wielders_Boon_ID) and (yield from self.skills.Ritualist.RestorationMagic.Wielders_Boon()):
            return True

        if self.IsSkillEquipped(Mending_Grip_ID) and (yield from self.skills.Ritualist.RestorationMagic.Mending_Grip()):
            return True

        if self.IsSkillEquipped(Spirit_Transfer_ID) and (yield from self.skills.Ritualist.RestorationMagic.Spirit_Transfer()):
            self._log_bip_action("Spirit_Transfer", "normal_heal")
            return True

        # spirit-gated cleanse: blind on a martial (melee/ranger/paragon).
        if (yield from self.skills.Ritualist.RestorationMagic.Mend_Body_and_Soul(cleanse_blind_martial=True)):
            return True

        # spirit-gated cleanse: cripple on a melee ally.
        if (yield from self.skills.Ritualist.RestorationMagic.Mend_Body_and_Soul(cleanse_cripple_melee=True)):
            return True

        # Recuperation: 6+ allies below 75% HP OR 4+ allies degenning.
        if self.IsSkillEquipped(Recuperation_ID) and (yield from self.skills.Ritualist.RestorationMagic.Recuperation(
            min_party_damaged_count=6,
        )):
            return True
        if self.IsSkillEquipped(Recuperation_ID) and (yield from self.skills.Ritualist.RestorationMagic.Recuperation(
            min_degen_count=4,
        )):
            return True

        if self.IsSkillEquipped(Ebon_Vanguard_Assassin_Support_ID) and (yield from self.skills.Any.PvE.Ebon_Vanguard_Assassin_Support(min_self_energy_pct=0.40)):
            return True

        # Signet of Lost Souls : energy refill when caster < 60%.
        if (yield from self.skills.Necromancer.SoulReaping.Signet_of_Lost_Souls(max_self_energy_pct=0.60)):
            return True

        if self.IsSkillEquipped(Blood_Bond_ID) and (yield from self.skills.Necromancer.BloodMagic.Blood_Bond()):
            return True

        # Life: spirit prep. Self-gated on aggro/close-to-aggro and on
        # SpiritBuffExists, so safe to fire before the MBaS @75% tier and
        # before the IsInAggro build gate.
        if self.IsSkillEquipped(Life_ID) and (yield from self.skills.Ritualist.RestorationMagic.Life()):
            self._log_bip_action("Life", "spirit_prep")
            return True

        # Stage 2.42 sustain tier.  Put Ghostmirror ahead of the broad 75% MBaS
        # tier so the dedicated live-bar slot cannot be starved forever by MBaS.
        if self.IsSkillEquipped(Ghostmirror_Light_ID) and (yield from self.skills.Ritualist.RestorationMagic.Ghostmirror_Light(health_threshold=0.68)):
            self._log_bip_action("Ghostmirror_Light", "sustain_other_68")
            return True

        # damaged: any ally at or below 75% HP, before combat skills.
        if (yield from self.skills.Ritualist.RestorationMagic.Mend_Body_and_Soul(health_threshold=0.75)):
            self._log_bip_action("Mend_Body_and_Soul", "damaged_75")
            return True

        # Protective Was Kaolai: arm the next party-heal-on-drop. Cast on
        # cooldown when not already holding a bundle - keeps ashes ready so
        # Drop_Held_Bundle has something to release on the next damage spike.
        if self.IsSkillEquipped(Protective_Was_Kaolai_ID) and (yield from self.skills.Ritualist.RestorationMagic.Protective_Was_Kaolai()):
            self._log_bip_action("Protective_Was_Kaolai", "arm_ashes")
            return True

        if not self.IsInAggro():
            return False

        if self.IsSkillEquipped(Vital_Weapon_ID) and (yield from self.skills.Ritualist.Communing.Vital_Weapon()):
            return True

        if self.IsSkillEquipped(Recovery_ID) and (yield from self.skills.Ritualist.RestorationMagic.Recovery()):
            return True

        # Signet of Lost Souls: opportunistic refill, no caster energy gate.
        if (yield from self.skills.Necromancer.SoulReaping.Signet_of_Lost_Souls()):
            return True

        # Recuperation: 6+ allies below 75% HP OR 2+ allies degenning.
        if self.IsSkillEquipped(Recuperation_ID) and (yield from self.skills.Ritualist.RestorationMagic.Recuperation(
            min_party_damaged_count=6,
        )):
            return True
        if self.IsSkillEquipped(Recuperation_ID) and (yield from self.skills.Ritualist.RestorationMagic.Recuperation(
            min_degen_count=2,
        )):
            return True

        # preventive: any ally at or below 85% HP, after Recuperation.
        if (yield from self.skills.Ritualist.RestorationMagic.Mend_Body_and_Soul(health_threshold=0.85)):
            return True

        if self.IsSkillEquipped(Breath_of_the_Great_Dwarf_ID) and (yield from self.skills.Any.NoAttribute.Breath_of_the_Great_Dwarf()):
            return True

        if self.IsSkillEquipped(You_Are_All_Weaklings_ID) and (yield from self.skills.Any.NoAttribute.You_Are_All_Weaklings()):
            return True

        if self.IsSkillEquipped(Enfeebling_Blood_ID) and (yield from self.skills.Necromancer.Curses.Enfeebling_Blood()):
            self._log_bip_action("Enfeebling_Blood", "pressure_weakness")
            return True

        if (yield from self.skills.Ritualist.RestorationMagic.Spirit_Light()):
            self._log_bip_action("Spirit_Light", "normal_heal")
            return True

        return False
