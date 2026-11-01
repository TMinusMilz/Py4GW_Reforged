from dataclasses import dataclass
from math import hypot

from Py4GWCoreLib import Agent, AgentArray, Map, Player, Profession, Range, Routines, BuildMgr, GLOBAL_CACHE, SpiritModelID
from Py4GWCoreLib.Skill import Skill
from Py4GWCoreLib.Builds.Skills import LightbringerOptional as LBO
from Py4GWCoreLib.Builds.Any.HeroAI import HeroAI as HeroAIBuild
from Py4GWCoreLib.Builds.Skills import HexRemovalPriority, SkillsTemplate
from Py4GWCoreLib.Builds.Skills import MesmerwayCoordination as MWC
from Py4GWCoreLib.Builds.Skills import MesmerWeaponSetController as MWSC


Air_of_Superiority_ID = Skill.GetID("Air_of_Superiority")
Panic_ID = Skill.GetID("Panic")
Mistrust_ID = Skill.GetID("Mistrust")
Complicate_ID = Skill.GetID("Complicate")
Ebon_Vanguard_Assassin_Support_ID = Skill.GetID("Ebon_Vanguard_Assassin_Support")
Cry_of_Pain_ID = Skill.GetID("Cry_of_Pain")
Unnatural_Signet_ID = Skill.GetID("Unnatural_Signet")
Cry_of_Frustration_ID = Skill.GetID("Cry_of_Frustration")
Overload_ID = Skill.GetID("Overload")
Power_Drain_ID = Skill.GetID("Power_Drain")
Energy_Tap_ID = Skill.GetID("Energy_Tap")
Recuperation_ID = Skill.GetID("Recuperation")
Mend_Body_and_Soul_ID = Skill.GetID("Mend_Body_and_Soul")
Spirit_Light_ID = Skill.GetID("Spirit_Light")
Shatter_Hex_ID = Skill.GetID("Shatter_Hex")
Shatter_Enchantment_ID = Skill.GetID("Shatter_Enchantment")
Shatter_Delusions_ID = Skill.GetID("Shatter_Delusions")
Flesh_of_My_Flesh_ID = Skill.GetID("Flesh_of_My_Flesh")
Breath_of_the_Great_Dwarf_ID = Skill.GetID("Breath_of_the_Great_Dwarf")
Ebon_Battle_Standard_of_Courage_ID = Skill.GetID("Ebon_Battle_Standard_of_Courage")
Ebon_Battle_Standard_of_Honor_ID = Skill.GetID("Ebon_Battle_Standard_of_Honor")
Tryptophan_Signet_ID = Skill.GetID("Tryptophan_Signet")
Mending_Refrain_ID = Skill.GetID("Mending_Refrain")
Heroic_Refrain_ID = Skill.GetID("Heroic_Refrain")
Power_Spike_ID = Skill.GetID("Power_Spike")
Spiritual_Pain_ID = Skill.GetID("Spiritual_Pain")
Technobabble_ID = Skill.GetID("Technobabble")
Frustration_ID = Skill.GetID("Frustration")
Diversion_ID = Skill.GetID("Diversion")
Dazed_ID = Skill.GetID("Dazed")
Winter_ID = Skill.GetID("Winter")
Mantra_of_Frost_ID = Skill.GetID("Mantra_of_Frost")
Ghostmirror_Light_ID = Skill.GetID("Ghostmirror_Light")
Protective_Was_Kaolai_ID = Skill.GetID("Protective_Was_Kaolai")
Life_ID = Skill.GetID("Life")
Soothing_Memories_ID = Skill.GetID("Soothing_Memories")
Drain_Enchantment_ID = Skill.GetID("Drain_Enchantment")
Spirit_Siphon_ID = Skill.GetID("Spirit_Siphon")
Wielders_Boon_ID = Skill.GetID("Wielders_Boon")


@dataclass(slots=True)
class _PanicBarSnapshot:
    in_aggro: bool = False
    enemy_in_spellcast: bool = False
    enemy_casting: bool = False
    enemy_casting_spell: bool = False
    enemy_casting_spell_or_chant: bool = False
    player_energy_pct: float = 1.0


class Panic(BuildMgr):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Panic",
            required_primary=Profession.Mesmer,
            template_code="OQBDAssjJ0QOM9AAAAAAAAA",
            # Panic+Cry legacy bars remain supported.  A dedicated Panic/Rt
            # healer instead equips the eight-slot support bar below.
            required_skills=[Panic_ID],
            optional_skills=[
                Mend_Body_and_Soul_ID,
                Spirit_Light_ID,
                Ghostmirror_Light_ID,
                Shatter_Hex_ID,
                Wielders_Boon_ID,
                Drain_Enchantment_ID,
                Protective_Was_Kaolai_ID,
                Spirit_Siphon_ID,
                Life_ID,
                Soothing_Memories_ID,
                Frustration_ID,
                Unnatural_Signet_ID,
                Energy_Tap_ID, Power_Drain_ID,
                LBO.GAZE_ID, LBO.SIGNET_ID,  # Optional DoA skills.
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAIBuild(standalone_fallback=True))
        self.SetBlockedSkills([
            Ghostmirror_Light_ID,
            Wielders_Boon_ID,
            Protective_Was_Kaolai_ID,  # dedicated ash holder/drop controller
            Spirit_Siphon_ID,  # dedicated ST-nearest-spirit controller
            Life_ID,
            Soothing_Memories_ID,
            Drain_Enchantment_ID,
            Frustration_ID,
            Air_of_Superiority_ID,
            Panic_ID,
            Mistrust_ID,
            Complicate_ID,
            Ebon_Vanguard_Assassin_Support_ID,
            Cry_of_Pain_ID,
            Unnatural_Signet_ID,
            Cry_of_Frustration_ID,
            Overload_ID,
            Power_Drain_ID,
            Energy_Tap_ID,
            Recuperation_ID,
            Mend_Body_and_Soul_ID,
            Spirit_Light_ID,
            Shatter_Hex_ID,
            Shatter_Enchantment_ID,
            Shatter_Delusions_ID,
            Ebon_Battle_Standard_of_Courage_ID,
            Ebon_Battle_Standard_of_Honor_ID,
            Tryptophan_Signet_ID,
            Mending_Refrain_ID,
            Power_Spike_ID,
            Spiritual_Pain_ID,
            Diversion_ID,  # Stage 2.54 predictive shared selector, not native AI
            Technobabble_ID,
            Winter_ID,
            Mantra_of_Frost_ID,
        ])
        self.SetSkillCastingFn(self._run_local_skill_logic)
        self.skills: SkillsTemplate = SkillsTemplate(self)
        self._mesmerway_weapon_sets = MWSC.MesmerWeaponSetController(role="panic")
        self._panic_siphon_last_by_spirit: dict[int, float] = {}
        self._panic_kaolai_armed: bool = False
        self._panic_kaolai_requested: bool = False

    def _get_bar_snapshot(self) -> _PanicBarSnapshot:
        snapshot = _PanicBarSnapshot()
        snapshot.in_aggro = bool(self.IsInAggro())
        snapshot.player_energy_pct = float(Agent.GetEnergy(Player.GetAgentID()))

        if not snapshot.in_aggro:
            return snapshot

        snapshot.enemy_in_spellcast = bool(Routines.Agents.GetNearestEnemy(Range.Spellcast.value))
        if snapshot.enemy_in_spellcast:
            snapshot.enemy_casting = bool(Routines.Targeting.GetEnemyCasting(Range.Spellcast.value))
            snapshot.enemy_casting_spell = bool(Routines.Targeting.GetEnemyCastingSpell(Range.Spellcast.value))
            snapshot.enemy_casting_spell_or_chant = bool(Routines.Targeting.GetEnemyCastingSpellOrChant(Range.Spellcast.value))

        return snapshot

    @staticmethod
    def _combat_log(event: str, **fields) -> None:
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(str(event), **fields)
        except Exception:
            pass

    def _team_packet(self) -> tuple[int, list[int]]:
        """Return the canonical KeySoJway packet/cleanup target for this Mesmer.

        The Panic Mesmer deliberately consumes the same TeamCombatFocus resolver
        as Keystone, Fire Ele and MM.  It may choose a better *member* inside the
        packet for an AoE/interrupt cast, but it never peels onto another group.
        """
        try:
            from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import (
                get_team_cluster_anchor,
                get_team_cluster_members,
            )
            anchor = int(get_team_cluster_anchor(
                filter_range=float(Range.Spellcast.value),
                minimum_enemies=2,
                consumer_role="panic_mesmer",
            ) or 0)
            if anchor <= 0:
                return 0, []
            members = list(get_team_cluster_members(
                anchor,
                radius=float(Range.Adjacent.value),
                filter_range=float(Range.Spellcast.value),
            ) or [])
            if not members and Agent.IsValid(anchor) and Agent.IsAlive(anchor):
                members = [anchor]
            return anchor, [int(x) for x in members]
        except Exception:
            return 0, []

    @staticmethod
    def _packet_centrality(enemy_id: int) -> int:
        try:
            from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import count_adjacent_enemies
            return int(count_adjacent_enemies(
                int(enemy_id), radius=float(Range.Adjacent.value)
            ) or 0)
        except Exception:
            return 0

    @staticmethod
    def _is_support_or_caster(enemy_id: int) -> bool:
        """Use RC4 EnemyKnowledge first; many PvE NPCs expose profession=0 live."""
        try:
            from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
            scores = EnemyKnowledge.role_scores(int(enemy_id))
            return bool(
                int(scores.get("caster", 0)) >= 55
                or int(scores.get("support", 0)) >= 70
                or int(scores.get("healer", 0)) >= 70
            )
        except Exception:
            pass
        try:
            from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import is_support_or_caster
            return bool(is_support_or_caster(int(enemy_id)))
        except Exception:
            try:
                return bool(Agent.IsCaster(int(enemy_id)))
            except Exception:
                return False

    def _pick_packet_member(self, members: list[int], *, require_casting: bool = False,
                            spell_or_chant_only: bool = False, prefer_caster: bool = True) -> int:
        candidates: list[int] = []
        for enemy_id in members:
            try:
                enemy_id = int(enemy_id)
                if enemy_id <= 0 or not Agent.IsValid(enemy_id) or not Agent.IsAlive(enemy_id):
                    continue
                if require_casting and not Agent.IsCasting(enemy_id):
                    continue
                if spell_or_chant_only:
                    casting_skill_id = int(Agent.GetCastingSkillID(enemy_id) or 0)
                    if casting_skill_id <= 0:
                        continue
                    if not (GLOBAL_CACHE.Skill.Flags.IsSpell(casting_skill_id)
                            or GLOBAL_CACHE.Skill.Flags.IsChant(casting_skill_id)):
                        continue
                candidates.append(enemy_id)
            except Exception:
                continue
        if not candidates:
            return 0

        def rank(enemy_id: int):
            try:
                caster = bool(Agent.IsCaster(enemy_id))
            except Exception:
                caster = False
            try:
                hp = float(Agent.GetHealth(enemy_id))
            except Exception:
                hp = 1.0
            # Caster/support first, then the member covering the most Nearby foes.
            # HP is only a late tie-breaker; cluster efficiency wins.
            return (
                -int(caster if prefer_caster else False),
                -int(self._is_support_or_caster(enemy_id)),
                -int(self._packet_centrality(enemy_id)),
                hp,
                enemy_id,
            )

        candidates.sort(key=rank)
        return int(candidates[0])

    def _try_recuperation_pre_fight(self):
        """Pre-cast/refresh Recuperation for a real 3+ enemy packet.

        This is intentionally before the offensive opener.  BuildMgr's spirit
        duplicate guard prevents wasting the cast when a live Recuperation is
        already in earshot (including one supplied by another team member).
        """
        if not self.IsSkillEquipped(Recuperation_ID):
            return False
        if not (self.IsCloseToAggro() or self.IsInAggro()):
            return False

        anchor, members = self._team_packet()
        if anchor <= 0 or len(members) < 3:
            return False
        if not self.CanCastSkillID(Recuperation_ID):
            return False

        did_cast = yield from self.CastSpiritSkillID(
            skill_id=Recuperation_ID,
            log=False,
            aftercast_delay=250,
        )
        if did_cast:
            self._combat_log(
                "PANIC_RECUPERATION_PRE_FIGHT",
                target_id=int(anchor),
                packet_size=int(len(members)),
                policy="precast_or_refresh_before_3plus_packet",
            )
            return True
        return False

    def _try_mend_body_and_soul_heroai(self):
        """Run Mend Body and Soul with the original HeroAI-style priorities.

        The Panic rotation is intentionally busy. Leaving MBaS only to the fallback
        can starve healing while Panic/interrupt actions keep succeeding. Handle it
        locally with HeroAI's normal 70% heal threshold and its condition-cleanse
        behavior when a spirit is in earshot.
        """
        if not self.IsSkillEquipped(Mend_Body_and_Soul_ID):
            return False
        if not self.CanCastSkillID(Mend_Body_and_Soul_ID):
            return False

        try:
            from Py4GWCoreLib.HeroAI.targeting import TargetLowestAlly
            target_id = int(TargetLowestAlly(
                filter_skill_id=Mend_Body_and_Soul_ID,
                distance=Range.Spellcast.value,
            ) or 0)
        except Exception:
            target_id = 0

        if target_id <= 0 or not Agent.IsValid(target_id) or not Agent.IsAlive(target_id):
            return False

        try:
            target_hp = float(Agent.GetHealth(target_id))
        except Exception:
            target_hp = 1.0

        try:
            spirit_exists = bool(Routines.Agents.GetNearestSpirit(Range.Earshot.value))
        except Exception:
            spirit_exists = False
        try:
            conditioned = bool(Routines.Checks.Agents.IsConditioned(target_id))
        except Exception:
            conditioned = False

        # Stock HeroAI metadata for MBaS uses LessLife = 0.70. A nearby spirit
        # also enables the skill's condition removal, so conditioned allies are
        # valid even above the pure-healing threshold.
        reason = ""
        if target_hp < 0.70:
            reason = "heroai_heal_below_70"
        elif spirit_exists and conditioned:
            reason = "heroai_condition_cleanse_with_spirit"
        else:
            return False

        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Mend_Body_and_Soul_ID,
            target_agent_id=target_id,
            log=False,
            aftercast_delay=250,
        )
        if did_cast:
            self._combat_log(
                "PANIC_MEND_BODY_SOUL_HEROAI",
                target_id=int(target_id),
                target_hp=round(float(target_hp), 3),
                spirit_exists=bool(spirit_exists),
                conditioned=bool(conditioned),
                reason=reason,
                policy="heroai_70pct_or_condition_with_spirit",
            )
            return True
        return False


    def _try_spirit_light_heal(self, *, health_threshold: float) -> bool:
        """Use Spirit Light as a real team heal without starving Panic offense.

        The shared RestorationMagic helper already handles the Spirit Light
        sacrifice safety rule and normal ally targeting. We only control the
        urgency threshold here.
        """
        if not self.IsSkillEquipped(Spirit_Light_ID):
            return False
        if not self.CanCastSkillID(Spirit_Light_ID):
            return False
        did_cast = yield from self.skills.Ritualist.RestorationMagic.Spirit_Light(
            health_threshold=float(health_threshold),
        )
        if did_cast:
            self._combat_log(
                "PANIC_SPIRIT_LIGHT_HEAL",
                threshold=round(float(health_threshold), 3),
                policy="emergency_30_then_normal_68",
            )
            return True
        return False

    def _cast_mantra_of_frost(self, *, min_energy_pct: float = 0.40):
        """Maintain the Urgoz frost stance without pre-empting Panic control."""
        if not self.IsSkillEquipped(Mantra_of_Frost_ID):
            return False
        try:
            energy = float(Agent.GetEnergy(Player.GetAgentID()) or 0.0)
        except Exception:
            energy = 1.0
        if energy < float(min_energy_pct):
            return False
        did = bool((yield from self.skills.Mesmer.InspirationMagic.Mantra_of_Frost()))
        if did:
            self._combat_log(
                "MESMERWAY_MANTRA_OF_FROST", role="panic", energy=round(float(energy), 3),
                policy="stage2_03_after_panic_cry_power_drain",
            )
        return bool(did)

    def _cast_winter_backup(self):
        """Panic Mesmer backs up Winter only after the core control lane."""
        if not self.IsSkillEquipped(Winter_ID):
            return False
        if not self.CanCastSkillID(Winter_ID, allow_existing_spirit=True):
            return False
        if not MWC.winter_should_cast("backup"):
            return False
        if not MWC.reserve_winter_cast():
            return False
        try:
            winter_cast_xy = tuple(Player.GetXY())
        except Exception:
            winter_cast_xy = None
        did = bool((yield from self.CastSkillID(
            Winter_ID, log=False, aftercast_delay=250, allow_existing_spirit=True
        )))
        if not did:
            MWC.release_winter_pending(str(self.GetLastCastRejectReason() or "panic_winter_not_fired"))
            return False
        record = MWC.register_winter_cast(role="panic_backup", cast_xy=winter_cast_xy)
        self._combat_log(
            "MESMERWAY_WINTER_CAST", role="panic_backup",
            duration_ms=int(record.get("duration_ms", 0) or 0),
            policy="stage2_03_st_primary_panic_backup_party_coverage",
        )
        return True

    @staticmethod
    def _panic_effect_present(enemy_id: int) -> bool:
        """Read *observable* Panic coverage; fail open when enemy hex data is unavailable.

        This is deliberately not a generic IsHexed() check: other hexes must
        never suppress Panic.  The inability to observe an enemy's effects
        must not block combat.
        """
        try:
            return bool(Routines.Checks.Agents.HasEffect(int(enemy_id), int(Panic_ID)))
        except Exception:
            return False

    def _try_panic_team_packet(self):
        """Cast Panic only inside the authoritative KeySoJway Adjacent packet."""
        if not self.IsSkillEquipped(Panic_ID) or not self.CanCastSkillID(Panic_ID):
            return False
        anchor, members = self._team_packet()
        if anchor <= 0 or not members:
            return False

        # A stale/dead packet member cannot be useful Panic coverage. Keep the
        # shared anchor/cluster unchanged; never seek a different group.
        live_members = []
        for member in members:
            try:
                if Agent.IsValid(int(member)) and Agent.IsAlive(int(member)):
                    live_members.append(int(member))
            except Exception:
                continue
        if not live_members:
            return False
        members = live_members
        covered_members = {eid for eid in members if self._panic_effect_present(eid)}
        # Only suppress a redundant cast when the specific Panic effect can
        # already be seen on *every* living packet member. Other hexes do not
        # count; a recently stripped hex makes the target eligible again.
        if len(members) > 1 and len(covered_members) == len(members):
            self._combat_log(
                "PANIC_PACKET_COVERED_SKIP", anchor_id=int(anchor),
                packet_size=len(members), covered_count=len(covered_members),
                policy="observed_panic_effect_only_no_time_lock",
            )
            return False

        rows = []
        caster_count = 0
        observed_casting_count = 0
        for enemy_id in members:
            eid = int(enemy_id)
            try:
                from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
                scores = EnemyKnowledge.role_scores(eid)
                caster = int(scores.get("caster", 0))
                support = int(scores.get("support", 0))
                healer = int(scores.get("healer", 0))
                offense = int(scores.get("offense", 0))
            except Exception:
                caster = support = healer = offense = 0
            role_target = self._is_support_or_caster(eid)
            caster_count += int(role_target)
            try:
                is_casting = bool(Agent.IsCasting(eid))
            except Exception:
                is_casting = False
            observed_casting_count += int(is_casting)
            casting_bonus = 160 if is_casting else 0
            density = self._packet_centrality(eid)
            # Prefer an as-yet-unhexed centre of the same packet while still
            # valuing dangerous skill users, healers and centrality. No hard
            # caster gate: Panic also reacts to physical/other skill use.
            fresh_target_bonus = 95 if eid not in covered_members else 0
            score = (
                casting_bonus
                + caster
                + support
                + healer
                + int(offense * 0.45)
                + density * 35
                + fresh_target_bonus
            )
            rows.append((score, int(role_target), density, -eid, eid))

        if not rows:
            return False
        rows.sort(reverse=True)
        target_id = int(rows[0][-1])
        if not MWC.try_claim_panic_packet(int(anchor)):
            return False
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Panic_ID, target_agent_id=target_id, log=False, aftercast_delay=250,
        )
        if did_cast:
            self._combat_log(
                "PANIC_TEAM_PACKET_CAST", target_id=target_id, anchor_id=int(anchor),
                packet_size=int(len(members)), caster_count=int(caster_count),
                covered_before=int(len(covered_members)),
                uncovered_before=int(len(members)-len(covered_members)),
                active_casting_members=int(observed_casting_count),
                mode="canonical_cluster" if len(members) > 1 else "cleanup",
                policy="rc4_panic_fresh_target_exact_keysojway_adjacent_packet",
            )
            return True
        return False

    def _try_cry_of_frustration_team_packet(self):
        if not self.IsSkillEquipped(Cry_of_Frustration_ID) or not self.CanCastSkillID(Cry_of_Frustration_ID):
            return False
        reservation = MWC.reserve_cry_packet()
        if reservation is None:
            return False
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Cry_of_Frustration_ID, target_agent_id=int(reservation.target_id),
            log=False, aftercast_delay=250,
        )
        if did_cast:
            MWC.register_cry_interrupt_cast(reservation, Cry_of_Frustration_ID)
            self._combat_log(
                "PANIC_CRY_MESMERWAY_PACKET", target_id=int(reservation.target_id),
                covered_ids=",".join(str(x) for x in reservation.covered_enemy_ids),
                policy="shared_with_4_esurge_mistrust_and_interrupt_locks",
            )
            return True
        return False

    def _try_complicate_mesmerway(self, *, emergency_only: bool = False):
        """Player-account Complicate using the same exact-cast claim as the Mesmerway.

        Stage 2.17 gives the player Panic account the controller path that Stage 2.16
        accidentally added only to hero Panic.  Emergency casts (especially
        Splinter Weapon) get first refusal before Panic/Cry; lower-value duplicate
        skills can still be disabled later in the normal control rotation.
        """
        if not self.IsSkillEquipped(Complicate_ID) or not self.CanCastSkillID(Complicate_ID):
            return False
        target, enemy_skill_id = MWC.reserve_complicate_target(emergency_only=bool(emergency_only))
        target = int(target or 0)
        enemy_skill_id = int(enemy_skill_id or 0)
        if target <= 0 or enemy_skill_id <= 0:
            return False
        options = MWC.interrupt_dispatch_options(target, Complicate_ID)
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Complicate_ID, target_agent_id=target,
            log=False, aftercast_delay=200, **options,
        )
        if did_cast:
            self._combat_log(
                "PANIC_COMPLICATE_MESMERWAY",
                target_id=target, enemy_skill_id=enemy_skill_id,
                emergency=bool(emergency_only),
                splinter=bool(enemy_skill_id == int(MWC.Splinter_Weapon_ID or 0)),
                policy="stage2_17_player_panic_exact_interrupt_complicate_before_cry_for_emergency",
            )
            return True
        MWC.release_complicate_reservation(
            enemy_skill_id, target_id=target, reason="player_panic_complicate_not_fired"
        )
        return False

    def _try_diversion_mesmerway(self):
        """Predictive caster disable; run only after important interrupt lanes."""
        if not self.IsSkillEquipped(Diversion_ID) or not self.CanCastSkillID(Diversion_ID):
            return False
        target = int(MWC.reserve_diversion_target() or 0)
        if target <= 0:
            return False
        did = bool((yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Diversion_ID, target_agent_id=target,
            log=False, aftercast_delay=250,
        )))
        if did:
            self._combat_log('PANIC_SHARED_DIVERSION_CAST', target_id=target,
                             policy='predictive_healer_support_next_skill')
        return did

    def _energy_tap_diag(self, reason: str, **fields) -> None:
        try:
            now = int(self._get_game_tick() or 0) if hasattr(self, "_get_game_tick") else 0
        except Exception:
            now = 0
        try:
            last = int(getattr(self, "_last_energy_tap_diag_tick", 0) or 0)
            last_reason = str(getattr(self, "_last_energy_tap_diag_reason", "") or "")
            if reason != last_reason or now <= 0 or now - last >= 2500:
                self._combat_log("PANIC_ENERGY_TAP_DIAG", reason=str(reason), **fields)
                self._last_energy_tap_diag_tick = now
                self._last_energy_tap_diag_reason = str(reason)
        except Exception:
            pass

    def _try_energy_tap_team_packet(self, *, energy_threshold_pct: float = 0.90):
        """Use Energy Tap only below a caller-defined reserve threshold.

        This is a long-fight fallback on the *current* Keystone fight packet,
        never an excuse to cast for damage or to pull an unrelated opponent.
        """
        try:
            equipped = bool(self.IsSkillEquipped(Energy_Tap_ID))
        except Exception:
            equipped = False
        if not equipped:
            self._energy_tap_diag("not_equipped", skill_id=int(Energy_Tap_ID))
            return False
        try:
            can_cast = bool(self.CanCastSkillID(Energy_Tap_ID))
        except Exception:
            can_cast = False
        if not can_cast:
            # Only log this while the skill itself is ready; cooldown is normal.
            try:
                ready = bool(Routines.Checks.Skills.IsSkillIDReady(Energy_Tap_ID))
            except Exception:
                ready = False
            if ready:
                self._energy_tap_diag("ready_but_cannot_cast", skill_id=int(Energy_Tap_ID))
            return False
        try:
            energy_pct = float(Agent.GetEnergy(Player.GetAgentID()))
        except Exception:
            energy_pct = 1.0
        if energy_pct > float(energy_threshold_pct):
            return False

        anchor, members = self._team_packet()
        if anchor <= 0 or not members:
            self._energy_tap_diag(
                "no_team_packet", energy_pct=round(float(energy_pct), 3),
                threshold_pct=round(float(energy_threshold_pct), 3),
            )
            return False
        target_id = self._pick_packet_member(members, require_casting=False, prefer_caster=True)
        if target_id <= 0:
            self._energy_tap_diag(
                "no_target", anchor_id=int(anchor), packet_size=int(len(members)),
                energy_pct=round(float(energy_pct), 3),
            )
            return False
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Energy_Tap_ID, target_agent_id=int(target_id), log=False, aftercast_delay=250,
        )
        if did_cast:
            self._combat_log(
                "PANIC_ENERGY_TAP", target_id=int(target_id), anchor_id=int(anchor),
                packet_size=int(len(members)), energy_pct=round(float(energy_pct), 3),
                threshold_pct=round(float(energy_threshold_pct), 3),
                policy="rt8_after_heals_panic_strips_below_50_pct_only",
            )
            return True
        self._energy_tap_diag(
            "cast_attempt_failed", target_id=int(target_id), anchor_id=int(anchor),
            packet_size=int(len(members)), energy_pct=round(float(energy_pct), 3),
        )
        return False

    def _try_power_drain_team_packet(self, *, energy_threshold_pct: float = 0.70):
        if not self.IsSkillEquipped(Power_Drain_ID) or not self.CanCastSkillID(Power_Drain_ID):
            return False
        try:
            energy_pct = float(Agent.GetEnergy(Player.GetAgentID()))
        except Exception:
            energy_pct = 1.0
        if energy_pct > float(energy_threshold_pct):
            return False

        target_id = int(MWC.reserve_single_interrupt_target(Power_Drain_ID) or 0)
        if target_id <= 0:
            return False
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Power_Drain_ID, target_agent_id=target_id, log=False, aftercast_delay=250,
        )
        if did_cast:
            MWC.register_single_interrupt_cast(target_id, Power_Drain_ID)
            self._combat_log(
                "PANIC_POWER_DRAIN_ENERGY", target_id=target_id,
                energy_pct=round(float(energy_pct), 3),
                threshold_pct=round(float(energy_threshold_pct), 3),
                policy="shared_mesmerway_interrupt_lock_exact_packet",
            )
            return True
        return False

    def _try_unnatural_team_packet(self):
        """Spend Unnatural Signet only as a post-control damage filler.

        The Panic Mesmer is the team's secondary healer/control slot.  This helper
        therefore never competes with healing, Panic, Cry or Power Drain.  When it
        finally gets a free action, prefer a hexed/enchanted member of the shared
        team packet so Unnatural produces its adjacent AoE instead of wandering to
        an unrelated target.  A packet member/anchor fallback is still allowed so
        the filler does not idle in cleanup.
        """
        if not self.IsSkillEquipped(Unnatural_Signet_ID):
            return False
        if not self.CanCastSkillID(Unnatural_Signet_ID):
            return False

        anchor, members = self._team_packet()
        candidates = [int(x) for x in (members or []) if int(x or 0) > 0]
        if int(anchor or 0) > 0 and int(anchor) not in candidates:
            candidates.insert(0, int(anchor))
        if not candidates:
            try:
                nearest = int(Routines.Agents.GetNearestEnemy(Range.Spellcast.value) or 0)
            except Exception:
                nearest = 0
            if nearest > 0:
                candidates = [nearest]

        if not candidates:
            return False

        def _valid(aid: int) -> bool:
            try:
                return bool(Agent.IsValid(aid) and Agent.IsAlive(aid))
            except Exception:
                return False

        candidates = [aid for aid in candidates if _valid(aid)]
        if not candidates:
            return False

        # AoE condition first; then canonical anchor/packet order.
        aoe = []
        for aid in candidates:
            try:
                if Agent.IsHexed(aid) or Agent.IsEnchanted(aid):
                    aoe.append(aid)
            except Exception:
                pass
        target = int((aoe or candidates)[0])
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Unnatural_Signet_ID,
            target_agent_id=target,
            log=False,
            aftercast_delay=200,
        )
        if did_cast:
            self._combat_log(
                "PANIC_UNNATURAL_FILLER",
                target_id=int(target),
                packet_anchor=int(anchor or 0),
                packet_size=int(len(candidates)),
                aoe_condition=bool(target in aoe),
                policy="heal_control_first_then_packet_unnatural",
            )
            return True
        return False

    def _try_mending_refrain_upkeep(self):
        """Initial/OOC full-party Mending Refrain spread, then HR chants renew it."""
        if not self.IsSkillEquipped(Mending_Refrain_ID) or not self.CanCastSkillID(Mending_Refrain_ID):
            return False
        # Never steal a live combat action unless we are merely approaching.
        if self.IsInAggro():
            return False
        try:
            energy = float(Agent.GetEnergy(Player.GetAgentID()) or 0.0)
        except Exception:
            energy = 1.0
        if energy < 0.55:
            return False

        # Do not snapshot Mending Refrain at the unboosted Motivation rank.
        # HR now prioritizes this account second (after the Paragon itself).
        try:
            if not Routines.Checks.Agents.HasEffect(Player.GetAgentID(), Heroic_Refrain_ID):
                return False
        except Exception:
            return False

        did_cast = yield from self.SpreadEchoToAlly(
            Mending_Refrain_ID, max_range=float(Range.Earshot.value), aftercast_delay=250
        )
        if did_cast:
            self._combat_log(
                "PANIC_MENDING_REFRAIN_SPREAD", energy=f"{energy:.3f}",
                policy="ooc_only_after_self_has_hr_then_missing_ally_hr_chants_renew",
            )
            return True
        return False

    def _try_mistrust_mesmerway(self):
        if not self.IsSkillEquipped(Mistrust_ID) or not self.CanCastSkillID(Mistrust_ID):
            return False
        target = int(MWC.reserve_mistrust_target() or 0)
        if target <= 0:
            return False
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Mistrust_ID, target_agent_id=target, log=False, aftercast_delay=250
        )
        if did_cast:
            MWC.register_mistrust_cast(target)
            self._combat_log(
                "PANIC_MISTRUST_ASSIGNED", target_id=target,
                policy="shared_with_esurge_interrupt_suppression_until_consumed",
            )
            return True
        return False


    def _try_shatter_enchantment_mesmerway(self):
        if not self.IsSkillEquipped(Shatter_Enchantment_ID) or not self.CanCastSkillID(Shatter_Enchantment_ID):
            return False
        target = int(MWC.reserve_shatter_enchantment_target() or 0)
        if target <= 0:
            return False
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Shatter_Enchantment_ID, target_agent_id=target, log=False, aftercast_delay=250
        )
        if did_cast:
            self._combat_log(
                "PANIC_SHATTER_ENCHANTMENT", target_id=int(target),
                policy="exact_packet_enchanted_priority_shared_lease",
            )
        return bool(did_cast)

    def _try_shatter_delusions_mesmerway(self):
        """Use shared protected-hex selection and native lifecycle reconciliation."""
        if not self.IsSkillEquipped(Shatter_Delusions_ID) or not self.CanCastSkillID(Shatter_Delusions_ID):
            return False
        target = int(MWC.reserve_shatter_delusions_target() or 0)
        if target <= 0:
            return False
        options = {}
        receipt, options = MWC.coordinated_dispatch_options(
            int(Shatter_Delusions_ID), int(target), options, urgent=False
        )
        did_cast = bool((yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Shatter_Delusions_ID, target_agent_id=target, log=False,
            aftercast_delay=160, **options
        )))
        if not did_cast:
            receipt.queue_reject_reason = str(self.GetLastCastRejectReason() or "panic_shatter_queue_rejected")
        accepted = bool((yield from MWC.confirm_coordinated_cast(receipt, did_cast)))
        if accepted:
            self._combat_log(
                "PANIC_SHATTER_DELUSIONS_QUEUED", target_id=int(target),
                evidence=str(receipt.evidence or "queued_async"),
                policy="stage2_20_protected_hex_shared_native_completion",
            )
        return bool(accepted)


    def _try_overload_mesmerway(self):
        if not self.IsSkillEquipped(Overload_ID) or not self.CanCastSkillID(Overload_ID):
            return False
        target = int(MWC.reserve_overload_target() or 0)
        if target <= 0:
            return False
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Overload_ID, target_agent_id=target, log=False, aftercast_delay=200
        )
        if did_cast:
            self._combat_log(
                "PANIC_OVERLOAD_MESMERWAY",
                target_id=int(target),
                policy="shared_overload_target_lease_exact_packet",
            )
            return True
        return False

    def _try_cry_of_pain_mesmerway(self):
        if not self.IsSkillEquipped(Cry_of_Pain_ID) or not self.CanCastSkillID(Cry_of_Pain_ID):
            return False
        target = int(MWC.reserve_cry_of_pain_target() or 0)
        if target <= 0:
            return False
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Cry_of_Pain_ID, target_agent_id=target, log=False, aftercast_delay=200
        )
        if did_cast:
            MWC.register_single_interrupt_cast(target, Cry_of_Pain_ID)
            self._combat_log(
                "PANIC_CRY_OF_PAIN_MESMERWAY",
                target_id=int(target),
                policy="shared_mesmerway_interrupt_lock_real_mesmer_hex",
            )
            return True
        return False

    def _try_power_spike_mesmerway(self):
        if not self.IsSkillEquipped(Power_Spike_ID) or not self.CanCastSkillID(Power_Spike_ID):
            return False
        target = int(MWC.reserve_single_interrupt_target(Power_Spike_ID) or 0)
        if target <= 0:
            return False
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Power_Spike_ID, target_agent_id=target, log=False, aftercast_delay=250
        )
        if did_cast:
            MWC.register_single_interrupt_cast(target, Power_Spike_ID)
            return True
        return False

    def _try_cleanup_direct(self):
        ready = tuple(sid for sid in (Unnatural_Signet_ID, Spiritual_Pain_ID)
                      if self.IsSkillEquipped(sid) and self.CanCastSkillID(sid))
        if not ready:
            return False
        target = int(MWC.reserve_cleanup_target(hard_only=True) or 0)
        if target <= 0:
            return False
        accepted = False
        for sid in ready:
            accepted = bool((yield from self.CastSkillIDAndRestoreTarget(
                skill_id=sid, target_agent_id=target, log=False, aftercast_delay=200
            )))
            if accepted:
                break
        if not accepted:
            MWC.release_cleanup_reservation(target, reason="panic_direct_cleanup_not_fired")
        return accepted

    def _try_spiritual_pain_mesmerway(self):
        if not self.IsSkillEquipped(Spiritual_Pain_ID) or not self.CanCastSkillID(Spiritual_Pain_ID):
            return False
        target = int(MWC.reserve_spiritual_pain_target() or 0)
        if target <= 0:
            return False
        return bool((yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Spiritual_Pain_ID, target_agent_id=target, log=False, aftercast_delay=250
        )))

    def _try_unnatural_mesmerway(self):
        if not self.IsSkillEquipped(Unnatural_Signet_ID) or not self.CanCastSkillID(Unnatural_Signet_ID):
            return False
        target = int(MWC.reserve_unnatural_target() or 0)
        if target <= 0:
            return False
        return bool((yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Unnatural_Signet_ID, target_agent_id=target, log=False, aftercast_delay=200
        )))

    def _try_evas_mesmerway(self):
        """Use the global Mesmerway EVAS lease so copies spread across foes."""
        if not self.IsSkillEquipped(Ebon_Vanguard_Assassin_Support_ID):
            return False
        if not self.CanCastSkillID(Ebon_Vanguard_Assassin_Support_ID):
            return False
        if not self.IsInAggro():
            return False
        target = int(MWC.reserve_evas_target() or 0)
        if target <= 0:
            return False
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Ebon_Vanguard_Assassin_Support_ID,
            target_agent_id=target,
            log=False,
            aftercast_delay=250,
        )
        if did_cast:
            self._combat_log(
                "PANIC_EVAS_DISTRIBUTED",
                target_id=int(target),
                policy="shared_with_esurge_and_hr_evas_target_lease",
            )
        return bool(did_cast)

    @staticmethod
    def _daze_remaining_ms(agent_id: int) -> int:
        try:
            if int(Dazed_ID or 0) <= 0:
                return 0
            if not Routines.Checks.Agents.HasEffect(int(agent_id), int(Dazed_ID)):
                return 0
            return max(0, int(GLOBAL_CACHE.Effects.GetEffectTimeRemaining(int(agent_id), int(Dazed_ID)) or 0))
        except Exception:
            return 0

    @staticmethod
    def _is_boss(agent_id: int) -> bool:
        try:
            return bool(Agent.HasBossGlow(int(agent_id)))
        except Exception:
            return False

    def _try_technobabble_mesmerway(self):
        """Pre-emptively Daze a valuable caster packet without sabotaging Mistrust.

        Panic and direct interrupt lanes get first refusal.  Technobabble is only
        used afterwards, stays inside the exact KeySoJway Adjacent packet, refuses
        boss centres, refuses packets currently protected for Mistrust or already
        owned by another direct interrupt, and avoids refreshing healthy Daze.
        """
        if not self.IsSkillEquipped(Technobabble_ID):
            return False
        if not self.CanCastSkillID(Technobabble_ID) or not self.IsInAggro():
            return False
        try:
            if float(Agent.GetEnergy(Player.GetAgentID()) or 0.0) < 0.45:
                return False
        except Exception:
            pass

        anchor, members = self._team_packet()
        if anchor <= 0 or not members:
            return False

        try:
            from Py4GWCoreLib.Py4GWcorelib import Utils
        except Exception:
            return False

        ranked = []
        metrics_by_target = {}
        for candidate in members:
            candidate = int(candidate)
            if candidate <= 0 or self._is_boss(candidate):
                continue
            try:
                cxy = Agent.GetXY(candidate)
                affected = [
                    int(eid) for eid in members
                    if Utils.Distance(cxy, Agent.GetXY(int(eid))) <= float(Range.Adjacent.value)
                ]
            except Exception:
                affected = [candidate]

            # Do not make a Mistrust trigger unreliable, and do not duplicate a
            # direct interrupt another Mesmer has already committed to.
            if any(MWC.is_mistrust_protected(eid) for eid in affected):
                continue
            if any(MWC.is_interrupt_reserved(eid) for eid in affected):
                continue

            new_daze = 0
            caster_count = 0
            casting_count = 0
            for eid in affected:
                if self._daze_remaining_ms(eid) <= 1400:
                    new_daze += 1
                if self._is_support_or_caster(eid):
                    caster_count += 1
                try:
                    if Agent.IsCasting(eid):
                        casting_count += 1
                except Exception:
                    pass

            # On a real packet, require at least two useful fresh Daze targets.
            # Cleanup may use one copy only if that enemy is a relevant caster.
            if len(members) > 1:
                if new_daze < 2 or caster_count < 1:
                    continue
            elif new_daze < 1 or caster_count < 1:
                continue

            centrality = int(self._packet_centrality(candidate))
            metrics_by_target[candidate] = (len(affected), new_daze, caster_count, casting_count)
            ranked.append((
                int(caster_count),
                int(new_daze),
                int(casting_count),
                int(centrality),
                -int(candidate),
                int(candidate),
            ))

        if not ranked:
            return False
        ranked.sort(reverse=True)
        target = int(ranked[0][-1])
        affected_count, new_daze, caster_count, casting_count = metrics_by_target[target]
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Technobabble_ID,
            target_agent_id=target,
            extra_condition=lambda tid=target: bool(
                Agent.IsValid(tid) and Agent.IsAlive(tid) and not self._is_boss(tid)
                and not MWC.is_mistrust_protected(tid)
            ),
            log=False,
            aftercast_delay=200,
        )
        if did_cast:
            self._combat_log(
                "PANIC_TECHNOBABBLE_SMART",
                target_id=int(target),
                anchor_id=int(anchor),
                packet_size=int(len(members)),
                affected_count=int(affected_count),
                new_daze=int(new_daze),
                caster_count=int(caster_count),
                casting_count=int(casting_count),
                policy="after_panic_and_direct_interrupts_no_mistrust_or_interrupt_overlap",
            )
        return bool(did_cast)

    # -- DoA Keystone: dedicated Panic / Restoration support -----------------
    def _is_panic_healer_bar(self) -> bool:
        """Select the Me/Rt heal/control role WITHOUT requiring a held-item skill.

        The six signature skills distinguish the support bar from the legacy
        offensive Panic bar. Life / Soothing Memories are optional flex slots.
        Neither Protective Was Kaolai nor any other bundle skill is required.
        """
        signature = (
            Panic_ID, Mend_Body_and_Soul_ID, Spirit_Light_ID,
            Ghostmirror_Light_ID, Shatter_Hex_ID, Drain_Enchantment_ID,
        )
        return bool(all(self.IsSkillEquipped(sid) for sid in signature)
                    and any(self.IsSkillEquipped(sid) for sid in (Life_ID, Soothing_Memories_ID)))

    @staticmethod
    def _panic_healer_party_ids() -> set[int]:
        try:
            from Py4GWCoreLib import Party
        except Exception:
            return set()
        ids = set()
        try:
            for member in Party.GetPlayers() or []:
                login = int(getattr(member, 'login_number', 0) or 0)
                aid = int(Party.Players.GetAgentIDByLoginNumber(login) or 0)
                if aid > 0: ids.add(aid)
        except Exception:
            pass
        for getter in ('GetHeroes', 'GetHenchmen'):
            try:
                for member in getattr(Party, getter)() or []:
                    aid = int(getattr(member, 'agent_id', 0) or 0)
                    if aid > 0: ids.add(aid)
            except Exception:
                pass
        return ids

    def _panic_healer_wounded(self, *, within_spellcast=True) -> list[tuple[float, int]]:
        """Never triage minions/NPCs; read real party-health broadcast."""
        party_ids = self._panic_healer_party_ids()
        if not party_ids:
            return []
        try:
            from Py4GWCoreLib.HeroAI.targeting import GetAllAlliesArray
            nearby = set(int(x) for x in (GetAllAlliesArray(Range.Spellcast.value) or []))
        except Exception:
            nearby = set()
        if not within_spellcast:
            nearby = set(party_ids)
        me = int(Player.GetAgentID() or 0)
        nearby.add(me)
        candidates = []
        for aid in (nearby & party_ids):
            try:
                if Agent.IsValid(aid) and Agent.IsAlive(aid):
                    hp = float(Routines.Checks.Agents.GetHealth(aid))
                    if 0.0 < hp < 1.0:
                        candidates.append((hp, aid))
            except Exception:
                pass
        return sorted(candidates)

    def _panic_healer_soothing(self, hp_limit: float):
        """Efficient ally heal; item in hand grants the native energy rebate."""
        if not self.CanCastSkillID(Soothing_Memories_ID):
            return False
        damaged = self._panic_healer_wounded()
        if not damaged or float(damaged[0][0]) >= hp_limit:
            return False
        hp, ally = damaged[0]
        did = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Soothing_Memories_ID, target_agent_id=int(ally),
            log=False, aftercast_delay=250,
        )
        if did:
            self._combat_log('PANIC_HEAL_SOOTHING', ally_id=int(ally), hp=round(hp,3))
        return bool(did)

    def _panic_healer_life(self):
        """Optional non-bundle group healing; keep necromancer spear grip active.

        Life has a delayed heal when the spirit dies. It never preempts actual
        healing, grouped Panic, high-priority hex removal or emergency energy.
        Avoid resummoning an already active Life spirit or spending the last
        portion of the healer's energy reserve.
        """
        if not self.IsSkillEquipped(Life_ID) or not self.CanCastSkillID(Life_ID):
            return False
        try:
            if self.SpiritBuffExists(Life_ID):
                return False
        except Exception:
            # Unknown spirit state: fail safe rather than spam a 10E ritual.
            return False
        if not (self.IsInAggro() or self.IsCloseToAggro()):
            return False
        try:
            if float(Agent.GetEnergy(int(Player.GetAgentID() or 0))) < 0.55:
                return False
        except Exception:
            return False
        did = yield from self.skills.Ritualist.RestorationMagic.Life()
        if did:
            self._combat_log('PANIC_HEAL_LIFE_CAST', policy='support_filler_no_bundle_keep_soul_reaping_5')
        return bool(did)

    def _panic_healer_drain_enchantment(self, *, energy_threshold_pct: float = 1.0):
        """Energy/strip utility on an enchanted member of the shared packet."""
        if not self.IsSkillEquipped(Drain_Enchantment_ID) or not self.CanCastSkillID(Drain_Enchantment_ID):
            return False
        try:
            if float(Agent.GetEnergy(int(Player.GetAgentID() or 0))) > float(energy_threshold_pct):
                return False
        except Exception:
            return False
        anchor, members = self._team_packet()
        targets = [int(x) for x in (members or []) if int(x or 0) > 0]
        if int(anchor or 0) > 0 and int(anchor) not in targets:
            targets.append(int(anchor))
        enchanted = []
        for enemy_id in targets:
            try:
                if Agent.IsValid(enemy_id) and Agent.IsAlive(enemy_id) and Agent.IsEnchanted(enemy_id):
                    enchanted.append(enemy_id)
            except Exception:
                pass
        if not enchanted: return False
        enchanted.sort(key=lambda a: (
            -int(self._is_support_or_caster(a)),
            -int(self._packet_centrality(a)), a
        ))
        target = enchanted[0]
        did = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Drain_Enchantment_ID, target_agent_id=target,
            log=False, aftercast_delay=250,
        )
        if did:
            self._combat_log('PANIC_HEAL_DRAIN_ENCHANTMENT', target_id=target)
        return bool(did)

    def _run_panic_healer_logic(self):
        """Healing > grouped Panic > urgent hex strip > utility.

        No autonomous movement, interaction or quest execution is performed.
        This route deliberately does NOT inherit the legacy offensive Panic
        rotation and its optional damage skills.
        """
        if not Routines.Checks.Skills.CanCast():
            yield from Routines.Yield.wait(100)
            return False

        # Genuine life-saving heals and pressure cleansing always go first.
        if (yield from self.skills.Ritualist.RestorationMagic.Spirit_Light(health_threshold=0.37)):
            return True
        if (yield from self.skills.Ritualist.RestorationMagic.Mend_Body_and_Soul(health_threshold=0.60)):
            return True
        if (yield from self.skills.Ritualist.RestorationMagic.Ghostmirror_Light(health_threshold=0.52)):
            return True
        if (yield from self._panic_healer_soothing(0.56)):
            return True
        # Routine injured allies still preempt damage/control where possible.
        if (yield from self.skills.Ritualist.RestorationMagic.Mend_Body_and_Soul(health_threshold=0.75)):
            return True
        if (yield from self.skills.Ritualist.RestorationMagic.Ghostmirror_Light(health_threshold=0.72)):
            return True
        if (yield from self.skills.Ritualist.RestorationMagic.Spirit_Light(health_threshold=0.67)):
            return True
        if (yield from self._panic_healer_soothing(0.78)):
            return True

        # Exceptional energy pressure: drain an enemy enchantment before Panic,
        # but NEVER before a required heal (all heal tiers above this point).
        if self.IsInAggro():
            try:
                critical_energy = float(Agent.GetEnergy(int(Player.GetAgentID() or 0))) < 0.40
            except Exception:
                critical_energy = False
            if critical_energy and (yield from self._panic_healer_drain_enchantment()):
                return True

        # Panic is the offensive centerpiece, but never wasted on lone enemies.
        if self.IsInAggro():
            _, members = self._team_packet()
            if len(members) >= 2 and (yield from self._try_panic_team_packet()):
                self._combat_log('PANIC_HEAL_GROUP_CONTROL', packet_size=len(members))
                return True

        # Hex cleanup before strip/energy filler; no Panic if no actual cluster.
        if (yield from self.skills.Mesmer.DominationMagic.Shatter_Hex(
            min_priority=HexRemovalPriority.HIGH)):
            return True
        if (yield from self._panic_healer_life()):
            return True
        if self.IsInAggro() and (yield from self._panic_healer_drain_enchantment()):
            return True
        if (yield from self.skills.Mesmer.DominationMagic.Shatter_Hex(
            min_priority=HexRemovalPriority.MEDIUM)):
            return True
        return False

    # -- Keystone 2026: exactly the eight equipped Me/Rt support skills -------
    def _is_current_panic_rt8_bar(self) -> bool:
        """Recognize the equipped Keystone Me/Rt bar despite flex-slot swaps.

        The previous seven-mandatory-plus-one-flex signature failed as soon as
        Unnatural Signet or Energy Tap replaced Life/Frustration. Require both
        Panic and Drain Enchantment plus an actual Restoration support core;
        do not hijack legacy all-offense Panic bars. No skillbar is auto-loaded.
        """
        if not self.IsSkillEquipped(Panic_ID):
            return False
        # Legacy rt8: Drain Enchantment. New rt8: Kaolai + Spirit Siphon.
        if not (self.IsSkillEquipped(Drain_Enchantment_ID) or
                (self.IsSkillEquipped(Protective_Was_Kaolai_ID) and
                 self.IsSkillEquipped(Spirit_Siphon_ID))):
            return False
        heals = (
            Mend_Body_and_Soul_ID, Spirit_Light_ID, Ghostmirror_Light_ID,
            Wielders_Boon_ID, Soothing_Memories_ID,
        )
        if sum(bool(self.IsSkillEquipped(sid)) for sid in heals) < 2:
            return False
        support = (Life_ID, Unnatural_Signet_ID, Energy_Tap_ID, Power_Drain_ID,
                   Frustration_ID, Soothing_Memories_ID, Protective_Was_Kaolai_ID,
                   Spirit_Siphon_ID)
        return any(self.IsSkillEquipped(sid) for sid in support)

    def _panic_nearby_packet(self) -> tuple[int, list[int]]:
        """Use ONLY living members of the existing Keystone combat group.

        Panic's hex application radius is Nearby, NOT Adjacent. The parent
        resolver owns group engagement; this helper never chooses a fresh pull.
        """
        anchor, _ = self._team_packet()
        if anchor <= 0:
            return 0, []
        try:
            from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import get_team_cluster_members
            members = get_team_cluster_members(
                int(anchor), radius=float(Range.Nearby.value),
                filter_range=float(Range.Spellcast.value),
            ) or []
        except Exception:
            return 0, []
        result = []
        for raw in members:
            aid = int(raw or 0)
            try:
                if aid > 0 and Agent.IsValid(aid) and Agent.IsAlive(aid):
                    result.append(aid)
            except Exception:
                continue
        result = sorted(set(result))
        return (anchor, result) if len(result) >= 2 else (0, [])

    @staticmethod
    def _panic_enemy_importance(enemy_id: int) -> int:
        """Observed roles outrank mere profession guesses; zero means unknown."""
        try:
            from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
            scores = EnemyKnowledge.role_scores(int(enemy_id))
            healer = int(scores.get('healer', 0))
            support = int(scores.get('support', 0))
            caster = int(scores.get('caster', 0))
            offense = int(scores.get('offense', 0))
            return (6 if healer >= 70 else 0) + (4 if support >= 70 else 0) + (3 if caster >= 55 else 0) + (2 if offense >= 85 else 0)
        except Exception:
            return 0

    def _panic_rt8_select_target(self, members: list[int]) -> tuple[int, dict]:
        """Cover important *neighbors*, preferring a less important hex center.

        An enemy's own successful skill activation cannot interrupt itself via
        Panic; a nearby other hexed foe can. Thus prioritize coverage of healers,
        support and dangerous casters, rather than blindly clicking the healer.
        """
        unique = sorted({int(v) for v in members if int(v or 0) > 0})
        if len(unique) < 2:
            return 0, {}
        positions = {}
        importance = {}
        covered = {}
        for eid in unique:
            try:
                positions[eid] = tuple(Agent.GetXY(eid))
            except Exception:
                continue
            importance[eid] = self._panic_enemy_importance(eid)
            covered[eid] = self._panic_effect_present(eid)
        radius = float(Range.Nearby.value)
        ranked = []
        for eid, (x, y) in positions.items():
            hits = [other for other, (ox, oy) in positions.items()
                    if hypot(float(x)-float(ox), float(y)-float(oy)) <= radius]
            if len(hits) < 2:
                continue
            uncovered = [other for other in hits if not covered[other]]
            if not uncovered:
                continue
            # Stronger role coverage first; use one safe/noncritical center when
            # that covers the SAME dangerous enemies. Never sacrifice important
            # coverage solely to avoid selecting a healer directly.
            important_coverage = sum(importance[other] for other in uncovered)
            total_important = sum(importance[other] for other in hits)
            rank = (
                important_coverage, len(uncovered), total_important,
                len(hits), -importance[eid], -eid,
            )
            ranked.append((rank, eid, len(hits), important_coverage))
        if not ranked:
            return 0, {}
        ranked.sort(reverse=True)
        _, target, hit_count, important = ranked[0]
        return target, dict(nearby_count=int(hit_count),
                            uncovered_danger_score=int(important),
                            target_danger_score=int(importance[target]),
                            already_hexed_count=sum(bool(v) for v in covered.values()))

    def _rt8_known_engaged_enemies(self) -> tuple[int, ...]:
        """Canonical, already-engaged cohort only; fail closed on stale/empty plans."""
        try:
            from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import get_team_combat_plan
            plan = get_team_combat_plan(filter_range=float(Range.Spellcast.value),
                                        minimum_enemies=1, consumer_role="panic_rt8_utility")
            if str(getattr(plan, 'mode', 'none')) not in {'cluster', 'cleanup', 'low_hp_finish', 'st_defense'}:
                return ()
            return tuple(sorted({int(v) for v in (getattr(plan, 'member_ids', ()) or ())
                if int(v or 0) > 0 and Agent.IsValid(int(v)) and Agent.IsAlive(int(v))}))
        except Exception:
            return ()

    def _rt8_secondary_panic_packet(self, main_ids: list[int]) -> tuple[int, list[int]]:
        """Alternative caster packet from the SAME already-engaged cohort only.

        A merely nearby healer pack is NEVER eligible. A separated pair requires
        at least 2 members belonging to the authoritative fight cohort, plus
        observed healer/support/caster value. Never move or retarget the DPS team.
        """
        cohort = set(self._rt8_known_engaged_enemies())
        if len(cohort) < 3:
            return 0, []
        known = set(int(v) for v in main_ids)
        outside = sorted(cohort - known)
        if len(outside) < 2:
            return 0, []
        radius = float(Range.Nearby.value)
        best = None
        for aid in outside:
            try:
                xy = Agent.GetXY(aid)
                near = [int(v) for v in outside if hypot(float(xy[0])-float(Agent.GetXY(v)[0]),
                    float(xy[1])-float(Agent.GetXY(v)[1])) <= radius]
                if len(near) < 2:
                    continue
                importance = sum(self._panic_enemy_importance(v) for v in near)
                if importance < 4: # no separate packet of harmless physicals
                    continue
                if all(self._panic_effect_present(v) for v in near):
                    continue
                score = (importance, len(near), -aid)
                if best is None or score > best[0]:
                    best = (score, aid, sorted(set(near)))
            except Exception:
                continue
        return (best[1], best[2]) if best else (0, [])

    def _power_drain_diag(self, reason: str, **fields) -> None:
        """Throttled diagnostic when emergency energy recovery cannot fire."""
        try:
            from time import monotonic
            now = int(monotonic() * 1000)
            last = int(getattr(self, '_power_drain_last_diag_ms', 0) or 0)
            previous = str(getattr(self, '_power_drain_last_diag_reason', '') or '')
            if reason != previous or now - last >= 3500:
                self._combat_log('PANIC_POWER_DRAIN_GATE', reason=str(reason), **fields)
                self._power_drain_last_diag_ms = now
                self._power_drain_last_diag_reason = str(reason)
        except Exception:
            pass

    def _try_power_drain_emergency_rt8(self):
        """At <=40% energy, interrupt for energy before ROUTINE healing.

        Real emergency heals still come first. If a member is dangerously low
        and basic emergency spells are unavailable, do not preempt other heals.
        No cast, no available spell/chant or unknown combat cohort => fallback.
        """
        if not self.IsInAggro():
            return False
        try:
            energy_pct = float(Agent.GetEnergy(int(Player.GetAgentID() or 0)))
        except Exception:
            return False
        if energy_pct > 0.40:
            return False
        try:
            wounded = self._panic_healer_wounded()
        except Exception:
            # Unknown party health must never let an optional interrupt
            # preempt life-saving healing.
            self._power_drain_diag('party_health_unknown')
            return False
        if wounded and float(wounded[0][0]) < 0.56:
            self._power_drain_diag('life_threatening_heal_first', energy_pct=round(energy_pct, 3),
                                   lowest_ally_hp=round(float(wounded[0][0]), 3))
            return False
        return (yield from self._try_power_drain_rt8(
            energy_threshold_pct=0.80, emergency_priority=True))

    def _try_power_drain_rt8(self, *, energy_threshold_pct: float = 0.80,
                              emergency_priority: bool = False):
        """Take energy from ANY active spell/chant in an engaged enemy pack.

        No class, importance or Panic-hex restriction. However, require at
        least 5 absolute free-energy capacity before using a 20-second
        recharge for recovery. Actual gain STILL requires a landed interrupt.
        Never target an unengaged group, or a target whose cast has finished.
        """
        if not self.IsInAggro():
            return False
        if not self.IsSkillEquipped(Power_Drain_ID):
            if emergency_priority:
                self._power_drain_diag('not_equipped')
            return False
        if not self.CanCastSkillID(Power_Drain_ID):
            if emergency_priority:
                self._power_drain_diag('on_cooldown_or_unaffordable')
            return False
        cohort = set(self._rt8_known_engaged_enemies())
        if not cohort:
            if emergency_priority:
                self._power_drain_diag('no_verified_engaged_targets')
            return False
        try:
            self_id = int(Player.GetAgentID() or 0)
            pct = float(Agent.GetEnergy(self_id))
            maximum = float(Agent.GetMaxEnergy(self_id))
            if maximum <= 0.0 or pct < 0.0 or pct > 1.0:
                raise ValueError('invalid player energy')
            free_energy = (1.0 - pct) * maximum
        except Exception:
            if emergency_priority:
                self._power_drain_diag('energy_read_unavailable')
            return False
        # Power Drain costs 5 Energy and generally restores much more at the
        # current Inspiration attribute, but a full bar cannot gain energy.
        # A 5E headroom is the minimum meaningful net refill in this build.
        if free_energy + 0.01 < 5.0:
            if emergency_priority:
                self._power_drain_diag('insufficient_energy_headroom',
                    free_energy=round(free_energy, 2), energy_pct=round(pct, 3))
            return False
        # Do not use MWC._skill_cast_candidates here: it intentionally
        # prefers the main damage packet and can hide eligible casters in an
        # already-engaged secondary pocket. Scan the VERIFIED cohort instead.
        candidates = []
        for raw_id in cohort:
            eid = int(raw_id or 0)
            try:
                if not Agent.IsValid(eid) or not Agent.IsAlive(eid) or not Agent.IsCasting(eid):
                    continue
                sid = int(Agent.GetCastingSkillID(eid) or 0)
                if sid <= 0:
                    continue
                flags = GLOBAL_CACHE.Skill.Flags
                if not (flags.IsSpell(sid) or flags.IsChant(sid)):
                    continue
                # Respect spellcast range, target access and the time left to
                # complete a 1/4s interrupt; avoid late guaranteed misses.
                if not MWC._interrupt_feasible(eid, Power_Drain_ID):
                    continue
                remaining_ms = MWC._interrupt_remaining_ms(eid, sid)
                required_ms = MWC._interrupt_min_remaining_ms(
                    Power_Drain_ID, emergency=bool(emergency_priority), enemy_skill_id=sid)
                if remaining_ms is not None and int(remaining_ms) < max(300, int(required_ms)):
                    continue
                candidates.append((eid, sid, int(MWC._interrupt_priority(eid, sid))))
            except Exception:
                continue
        ranked = []
        for eid, sid, danger in candidates:
            eid, sid = int(eid), int(sid)
            if eid not in cohort or sid <= 0:
                continue
            try:
                if not (Agent.IsValid(eid) and Agent.IsAlive(eid) and Agent.IsCasting(eid)
                        and int(Agent.GetCastingSkillID(eid) or 0) == sid):
                    continue
                flags = GLOBAL_CACHE.Skill.Flags
                if not (flags.IsSpell(sid) or flags.IsChant(sid)):
                    continue
                # Everyone casting is now eligible, regardless of enemy role,
                # panic coverage, or current energy percentage.
                # Prefer threatening casts and uncovered targets only to sort.
                ranked.append(((int(danger), int(not self._panic_effect_present(eid)),
                    self._panic_enemy_importance(eid), -eid), eid, sid))
            except Exception:
                continue
        ranked.sort(reverse=True)
        if not ranked and emergency_priority:
            self._power_drain_diag('no_eligible_active_spell_or_chant',
                energy_pct=round(pct, 3), verified_targets=len(cohort),
                scanner_candidates=len(candidates))
        for _rank, eid, sid in ranked:
            try:
                if not MWC._claim_exact_interrupt(eid, sid, Power_Drain_ID):
                    continue
                if not MWC._try_lock(MWC._INTERRUPT_TARGET_LOCK, eid, MWC.INTERRUPT_RESERVATION_MS):
                    MWC._release_exact_interrupt(eid, Power_Drain_ID, reason='panic_rt8_target_lock_busy')
                    continue
            except Exception:
                return False
            if (eid not in self._rt8_known_engaged_enemies()
                    or not Agent.IsCasting(eid)
                    or int(Agent.GetCastingSkillID(eid) or 0) != sid):
                MWC.release_single_interrupt_reservation(eid, Power_Drain_ID, reason='cast_or_cohort_changed')
                continue
            did = yield from self.CastSkillIDAndRestoreTarget(
                skill_id=Power_Drain_ID, target_agent_id=eid,
                log=False, aftercast_delay=250)
            if did:
                MWC.register_single_interrupt_cast(eid, Power_Drain_ID)
                self._combat_log('PANIC_RT8_POWER_DRAIN', target_id=eid,
                    energy_pct=round(pct, 3), free_energy=round(free_energy, 2),
                    enemy_skill_id=sid,
                    priority=('emergency_below_40_percent' if emergency_priority else 'any_cast_energy_recovery'),
                    policy='all_engaged_casters_any_role_energy_headroom_5')
                return True
            MWC.release_single_interrupt_reservation(eid, Power_Drain_ID, reason='cast_not_fired')
        return False

    def _try_panic_rt8_cluster(self):
        """Apply Panic to >=2 members without changing player focus or movement."""
        if not self.IsSkillEquipped(Panic_ID) or not self.CanCastSkillID(Panic_ID):
            return False
        anchor, members = self._panic_nearby_packet()
        if anchor <= 0 or len(members) < 2:
            return False
        target, metrics = self._panic_rt8_select_target(members)
        secondary_anchor, secondary_members = self._rt8_secondary_panic_packet(members)
        if secondary_anchor > 0:
            main_danger = sum(self._panic_enemy_importance(v) for v in members)
            secondary_danger = sum(self._panic_enemy_importance(v) for v in secondary_members)
            # Move Panic ONLY when the separated, already-engaged caster pack
            # presents substantially more danger than the main packet.
            if secondary_danger >= max(8, main_danger + 4):
                sec_target, sec_metrics = self._panic_rt8_select_target(secondary_members)
                if sec_target > 0:
                    anchor, members = int(secondary_anchor), list(secondary_members)
                    target, metrics = int(sec_target), dict(sec_metrics)
                    metrics['secondary_engaged_packet'] = True
        if target <= 0:
            return False
        if not MWC.try_claim_panic_packet(int(anchor)):
            return False
        requested = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Panic_ID, target_agent_id=int(target),
            log=False, aftercast_delay=250,
        )
        if requested:
            self._combat_log('PANIC_RT8_REQUESTED', target_id=int(target),
                             anchor_id=int(anchor), packet_size=len(members),
                             policy='nearby_danger_coverage_no_extra_pull',
                             **metrics)
        return bool(requested)

    def _try_frustration_rt8_cluster(self):
        """Pressure a key healer/support ALREADY inside the active Panic packet.

        Panic controls the whole Nearby group; Frustration is a single-target
        payoff on a dangerous *neighbor*. Never pull a second pack, hex a lone
        target, or spend 10 energy without observable Panic coverage on a
        different nearby enemy (a potential interrupt trigger).
        """
        if not self.IsInAggro():
            return False
        if not (self.IsSkillEquipped(Frustration_ID)
                and self.CanCastSkillID(Frustration_ID)):
            return False
        try:
            energy = float(Agent.GetEnergy(int(Player.GetAgentID() or 0)))
        except Exception:
            return False
        # A costly optional hex never consumes the last healing reserve.
        if energy < 0.50:
            return False
        anchor, members = self._panic_nearby_packet()
        if anchor <= 0 or len(members) < 2:
            return False
        positions = {}
        for aid in members:
            try:
                if Agent.IsValid(aid) and Agent.IsAlive(aid):
                    positions[aid] = tuple(Agent.GetXY(aid))
            except Exception:
                continue
        if len(positions) < 2:
            return False
        nearby_radius = float(Range.Nearby.value)
        candidates = []
        for aid, (x, y) in positions.items():
            try:
                if Routines.Checks.Agents.HasEffect(aid, Frustration_ID):
                    continue
                hp = float(Agent.GetHealth(aid))
                if hp <= 0.30:  # will often die before the 1-second hex lands
                    continue
                from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
                scores = EnemyKnowledge.role_scores(aid)
                healer = int(scores.get('healer', 0))
                support = int(scores.get('support', 0))
                caster = int(scores.get('caster', 0))
                # No blind hexing of physical trash mobs: key role required.
                if healer < 70 and support < 70 and caster < 55:
                    continue
            except Exception:
                continue
            neighbors = [other for other, (ox, oy) in positions.items()
                         if other != aid and hypot(float(x)-float(ox), float(y)-float(oy)) <= nearby_radius]
            # Observable Panic on a DIFFERENT enemy is essential: only the
            # successful skill use of that enemy can interrupt this target.
            panic_sources = [other for other in neighbors if self._panic_effect_present(other)]
            if not panic_sources:
                continue
            try:
                casting = bool(Agent.IsCasting(aid))
            except Exception:
                casting = False
            # Healers first, then support, then dangerous caster. Prefer a
            # currently acting target and dense interrupt coverage.
            rank = (int(healer >= 70), int(support >= 70),
                    int(caster >= 55), int(casting), len(panic_sources),
                    len(neighbors), -int(hp*100), -aid)
            candidates.append((rank, aid, hp, len(panic_sources), len(neighbors)))
        if not candidates:
            return False
        candidates.sort(reverse=True)
        _, target, hp, sources, neighbors = candidates[0]
        # Keep the normal player/enemy focus untouched. 1s activation plus
        # normal aftercast: no restore-target/idle-attack before cast completes.
        requested = yield from self.CastSkillID(
            skill_id=Frustration_ID, target_agent_id=int(target),
            log=False, aftercast_delay=1750,
        )
        if requested:
            self._combat_log(
                'PANIC_FRUSTRATION_REQUESTED', target_id=int(target),
                anchor_id=int(anchor), packet_size=len(members),
                target_hp=round(float(hp), 3), panic_neighbor_count=int(sources),
                nearby_enemies=int(neighbors), energy_pct=round(float(energy), 3),
                policy='post_panic_healer_support_only_observed_neighbor_hex',
            )
        return bool(requested)

    def _panic_siphon_diag(self, reason: str, **fields) -> None:
        """One reasoned decision snapshot at most every 4 seconds."""
        import time
        now = time.monotonic()
        if now - float(getattr(self, '_panic_siphon_diag_last_s', -1e6)) < 4.0:
            return
        self._panic_siphon_diag_last_s = now
        self._combat_log('PANIC_SPIRIT_SIPHON_GATE', reason=str(reason), **fields)

    def _panic_rt8_siphon_from_st(self, *, emergency: bool = False):
        """Siphon ONLY the game's closest actual spirit, with reason logging.

        The game does not allow us to pick one of the ST's three spirits.
        Crucially, the spirit-pet array also contains pets; select only genuine
        spirits. Ownerless/foreign spirits are valid: Siphon is self-targeted,
        causes no enemy damage, and draining spirit Energy does not impair
        spirit attacks. Never move to a spirit or cast without one in range.
        """
        import time
        sid = int(Spirit_Siphon_ID or 0)
        if sid <= 0 or not self.IsSkillEquipped(sid):
            self._panic_siphon_diag('skill_not_equipped', skill_id=sid)
            return False
        if not self.CanCastSkillID(sid):
            self._panic_siphon_diag('skill_cannot_cast', skill_id=sid,
                reject_reason=str(self.GetLastCastRejectReason()))
            return False
        if not (self.IsInAggro() or self.IsCloseToAggro()):
            self._panic_siphon_diag('not_in_combat')
            return False
        try:
            me = int(Player.GetAgentID() or 0)
            maximum = float(Agent.GetMaxEnergy(me) or 0)
            fraction = float(Agent.GetEnergy(me))
            deficit = maximum * max(0.0, 1.0 - fraction)
            if maximum <= 0:
                self._panic_siphon_diag('invalid_energy_snapshot')
                return False
            if deficit < 12.0:
                self._panic_siphon_diag('energy_deficit_too_small',
                    energy_pct=round(fraction, 3), deficit=round(deficit, 1))
                return False
            threshold = 0.43 if emergency else 0.70
            if fraction > threshold:
                self._panic_siphon_diag('above_energy_threshold',
                    energy_pct=round(fraction, 3), threshold=threshold,
                    deficit=round(deficit, 1))
                return False
            xy = Player.GetXY()
            nearest = 0
            nearest_d2 = float('inf')
            visible_spirits = 0
            # GetSpiritPetArray() includes pets. IsSpirit() excludes them.
            for raw in AgentArray.GetSpiritPetArray() or []:
                spirit = int(raw or 0)
                if spirit <= 0 or not Agent.IsAlive(spirit) or not Agent.IsSpirit(spirit):
                    continue
                visible_spirits += 1
                sx, sy = Agent.GetXY(spirit)
                d2 = (float(sx) - float(xy[0]))**2 + (float(sy) - float(xy[1]))**2
                if d2 < nearest_d2:
                    nearest, nearest_d2 = spirit, d2
            if not nearest:
                self._panic_siphon_diag('no_living_spirit', observed_spirits=visible_spirits)
                return False
            distance = nearest_d2**0.5
            if nearest_d2 > float(Range.Spellcast.value)**2:
                self._panic_siphon_diag('nearest_spirit_out_of_range',
                    spirit_id=nearest, distance=round(distance, 1),
                    max_distance=float(Range.Spellcast.value),
                    observed_spirits=visible_spirits)
                return False
            now = time.monotonic()
            cooldown = 20.0 - (now - self._panic_siphon_last_by_spirit.get(nearest, -1e6))
            if cooldown > 0:
                self._panic_siphon_diag('nearest_spirit_recently_siphoned',
                    spirit_id=nearest, distance=round(distance, 1),
                    remaining_s=round(cooldown, 1))
                return False
            owner = int(Agent.GetOwnerID(nearest) or 0)
            model = int(Agent.GetPlayerNumber(nearest) or 0)
        except Exception as exc:
            self._panic_siphon_diag('snapshot_error', error_type=type(exc).__name__)
            return False
        self._panic_siphon_diag('ready_for_cast', spirit_id=nearest,
            distance=round(distance, 1), owner_id=owner, model_id=model,
            energy_pct=round(fraction, 3), deficit=round(deficit, 1))
        requested = yield from self.CastSkillID(
            skill_id=sid, log=False, aftercast_delay=250,
        )
        if requested:
            self._panic_siphon_last_by_spirit[nearest] = now
            self._combat_log('PANIC_SPIRIT_SIPHON_REQUESTED',
                spirit_id=nearest, spirit_owner_id=owner, spirit_model_id=model,
                distance=round(distance, 1), energy_pct_before=round(fraction, 3),
                energy_deficit_before=round(deficit, 1),
                spirit_energy_unobserved=True, nearest_only=True,
                cooldown_s=20, emergency=bool(emergency),
                actual_energy_gain_unconfirmed=True)
        else:
            self._combat_log('PANIC_SPIRIT_SIPHON_CAST_REJECTED',
                spirit_id=nearest, reject_reason=str(self.GetLastCastRejectReason()))
        return bool(requested)

    def _panic_rt8_kaolai_drop(self):
        '''Release Kaolai only for multiple injured REAL party members.'''
        if not self.IsSkillEquipped(Protective_Was_Kaolai_ID):
            return False
        try:
            holding = bool(Agent.IsHoldingItem(int(Player.GetAgentID() or 0)))
        except Exception:
            return False
        if not holding:
            self._panic_kaolai_armed = False
            self._panic_kaolai_requested = False
            return False
        if self._panic_kaolai_requested:
            self._panic_kaolai_armed = True
        if not self._panic_kaolai_armed:
            return False  # never drop an unknown bundle
        injured = 0
        critical = 0
        try:
            ids = self._panic_healer_party_ids()
            xy = Player.GetXY()
            limit_sq = float(Range.Spirit.value)**2
            for ally in ids:
                if not (Agent.IsValid(ally) and Agent.IsAlive(ally)):
                    continue
                ax, ay = Agent.GetXY(ally)
                if (float(ax)-float(xy[0]))**2 + (float(ay)-float(xy[1]))**2 > limit_sq:
                    continue
                hp = float(Routines.Checks.Agents.GetHealth(ally))
                if 0 < hp <= 0.75:
                    injured += 1
                if 0 < hp <= 0.48:
                    critical += 1
            if not (injured >= 3 or critical >= 2):
                return False
            from Py4GWCoreLib.FrameTree import FrameTree
            buttons = FrameTree.frames_at_path(5040781, [0, 0])
            if not buttons or not buttons[0].is_usable:
                return False
            buttons[0].click()
            self._mark_local_cast_pending(250)
            self._panic_kaolai_armed = False
            self._panic_kaolai_requested = False
            self._combat_log('PANIC_KAOLAI_DROP_REQUESTED',
                             injured_party=int(injured), critical_party=int(critical))
            yield  # UI drop action must be yielded to the next engine frame
            return True
        except Exception:
            return False

    def _panic_rt8_kaolai_arm(self):
        '''Precast Kaolai without stealing the urgent heal or Panic lane.'''
        if not self.IsSkillEquipped(Protective_Was_Kaolai_ID):
            return False
        if not (self.IsInAggro() or self.IsCloseToAggro()):
            return False
        try:
            if Agent.IsHoldingItem(int(Player.GetAgentID() or 0)):
                return False
            energy = float(Agent.GetEnergy(int(Player.GetAgentID() or 0)))
            max_energy = float(Agent.GetMaxEnergy(int(Player.GetAgentID() or 0)))
            # Item costs 10; keep 15 absolute Energy for actual heals/Panic.
            if energy * max_energy < 25.0:
                return False
        except Exception:
            return False
        did = yield from self.skills.Ritualist.RestorationMagic.Protective_Was_Kaolai()
        if did:
            self._panic_kaolai_requested = True
            self._combat_log('PANIC_KAOLAI_ARM_REQUESTED', policy='after_urgent_heals')
        return bool(did)

    def _run_panic_rt8_logic(self):
        """N/Rt-style emergency and sustained healing; Panic/Drain are filler.

        Cast requests aren't confirmed application. Keep game-side cast gates,
        aftercast delays, targets, movement and existing skill APIs untouched.
        """
        if not Routines.Checks.Skills.CanCast():
            yield from Routines.Yield.wait(100)
            return False
        # Same healing skill order/HP thresholds as our last N/Rt healer.
        if (yield from self.skills.Ritualist.RestorationMagic.Spirit_Light(health_threshold=0.35)):
            return True
        if (yield from self.skills.Ritualist.RestorationMagic.Mend_Body_and_Soul(health_threshold=0.52)):
            return True
        if (yield from self.skills.Ritualist.RestorationMagic.Ghostmirror_Light(health_threshold=0.47)):
            return True
        if (yield from self._panic_rt8_kaolai_drop()):
            return True
        # Critical energy recovery may preempt routine heals, but never the
        # life-saving spells above or a remaining target below 56% HP.
        if (yield from self._panic_rt8_siphon_from_st(emergency=True)):
            return True
        if (yield from self._try_power_drain_emergency_rt8()):
            return True
        if (yield from self.skills.Ritualist.RestorationMagic.Wielders_Boon()):
            return True
        if (yield from self._panic_healer_soothing(0.55)):
            return True
        if (yield from self.skills.Ritualist.RestorationMagic.Mend_Body_and_Soul(health_threshold=0.75)):
            return True
        if (yield from self.skills.Ritualist.RestorationMagic.Spirit_Light(health_threshold=0.67)):
            return True
        if (yield from self.skills.Ritualist.RestorationMagic.Ghostmirror_Light(health_threshold=0.70)):
            return True
        if (yield from self._panic_healer_soothing(0.77)):
            return True
        if not self.IsInAggro():
            if (yield from self._panic_rt8_kaolai_arm()):
                return True
            return False

        try:
            energy = float(Agent.GetEnergy(int(Player.GetAgentID() or 0)))
        except Exception:
            energy = 1.0
        # Every equipped heal above gets first refusal; then preserve Panic's
        # cluster control before spending the rotation on optional energy/DPS.
        # Important: do NOT make Drain Enchantment conditional on low energy.
        wounded = self._panic_healer_wounded()
        if wounded and wounded[0][0] < 0.68:
            # If the heals ran out of Energy, recovery must not deadlock.
            if energy <= 0.43 and (yield from self._panic_rt8_siphon_from_st(emergency=True)):
                return True
            return False
        # Below 40%: attempted before routine healing. Otherwise use any
        # engaged caster/chant with >=5 absolute energy headroom after heals.
        if self.IsSkillEquipped(Power_Drain_ID) and (yield from self._try_power_drain_rt8(energy_threshold_pct=1.0)):
            return True
        if (yield from self._try_panic_rt8_cluster()):
            return True
        if (yield from self._panic_rt8_siphon_from_st()):
            return True
        if (yield from self._panic_rt8_kaolai_arm()):
            return True
        # Optional stripping/energy recovery at <=80%; no naked casts.
        if (yield from self._panic_healer_drain_enchantment(energy_threshold_pct=0.80)):
            return True
        # Energy Tap is RETAINED and supported when manually equipped.
        # It is a fallback if Power Drain has no interruptible cast and no
        # enchantment is available; no bar change is performed in code.
        if self.IsSkillEquipped(Energy_Tap_ID) and (yield from self._try_energy_tap_team_packet(energy_threshold_pct=0.80)):
            return True
        # Life is an optional group-support spirit, never a Panic opener.
        if (yield from self._panic_healer_life()):
            return True
        # No healing/control/energy action needed -> free, direct signet DPS.
        if (yield from self._try_unnatural_team_packet()):
            return True
        # Only if manually equipped; no new pull and never above heal/Panic/energy.
        if (yield from LBO.try_signet(self, role='panic', energy_threshold=0.35)):
            return True
        if (yield from LBO.try_gaze(self, role='panic', min_enemies=2)):
            return True
        # Legacy/optional Frustration is always lower priority than the new
        # zero-energy Unnatural filler and both recovery options.
        if (yield from self._try_frustration_rt8_cluster()):
            return True
        return False

    def LoadSkillBar(self):
        """Preserve an already equipped current 8-skill Me/Rt support bar.

        Legacy Panic bars keep their original template-loading behavior.
        """
        if self._is_current_panic_rt8_bar():
            if False:
                yield
            return None
        yield from super().LoadSkillBar()

    def _run_local_skill_logic(self):
        if self._is_current_panic_rt8_bar():
            return (yield from self._run_panic_rt8_logic())
        if self._is_panic_healer_bar():
            return (yield from self._run_panic_healer_logic())
        # Panic owns coordinated async Shatter receipts too; reconcile them on
        # this client before issuing the next action.
        MWC.observe_pending_cast_receipts()
        if not Routines.Checks.Skills.CanCast():
            yield from Routines.Yield.wait(100)
            return False

        snapshot = self._get_bar_snapshot()
        # Only the account-local Panic controller owns this equipped weapon
        # setup. Native heroes never receive F1/F2 key inputs from this path.
        if not snapshot.enemy_casting and (yield from self._mesmerway_weapon_sets.maybe_switch(
            snapshot.player_energy_pct, in_combat=snapshot.in_aggro
        )):
            return True

        # Mending Refrain is an out-of-combat setup/repair layer. Once spread,
        # the HR Paragon's duration chants renew it together with Heroic Refrain.
        if (yield from self._try_mending_refrain_upkeep()):
            return True

        # Large-packet setup: Recuperation first, then Panic as the offensive opener.
        if (yield from self._try_recuperation_pre_fight()):
            return True

        # Hochnäsigkeit / Air of Superiority is a setup snowball buff, like on
        # the Keystone Mesmers.  Use it around contact when equipped; the shared
        # PvE helper handles effect/recharge gating, so it cannot be spammed.
        if (snapshot.in_aggro or self.IsCloseToAggro()) and self.IsSkillEquipped(Air_of_Superiority_ID):
            if (yield from self.skills.Any.PvE.Air_of_Superiority()):
                self._combat_log("PANIC_AIR_OF_SUPERIORITY_SETUP", policy="pre_burst_snowball")
                return True

        if (yield from self.skills.Any.NoAttribute.Breath_of_the_Great_Dwarf()):
            return True

        # Emergency direct heal first. Spirit Light is the fastest way to stop
        # an ally from dropping while the Panic bar is busy with interrupts.
        if (yield from self._try_spirit_light_heal(health_threshold=0.30)):
            return True

        # Keep MBaS on HeroAI-style healing/cleanse behavior, but execute it
        # inside the local Panic loop so the busy interrupt rotation cannot
        # starve team healing.
        if (yield from self._try_mend_body_and_soul_heroai()):
            return True

        # Normal Spirit Light pass. This is deliberately below MBaS so MBaS can
        # clean conditions while healing, but still above offensive Panic logic.
        if (yield from self._try_spirit_light_heal(health_threshold=0.68)):
            return True

        if self.IsSkillEquipped(Flesh_of_My_Flesh_ID):
            dead_ally_id = Routines.Agents.GetDeadAlly(Range.Spellcast.value) or 0
            if dead_ally_id and (yield from self.CastSkillIDAndRestoreTarget(
                skill_id=Flesh_of_My_Flesh_ID,
                target_agent_id=dead_ally_id,
                log=False,
                aftercast_delay=250,
            )):
                return True

        if self.IsSkillEquipped(Ebon_Battle_Standard_of_Courage_ID) and (yield from self.skills.Any.NoAttribute.Ebon_Battle_Standard_of_Courage()):
            return True

        if self.IsSkillEquipped(Ebon_Battle_Standard_of_Honor_ID) and (yield from self.skills.Any.NoAttribute.Ebon_Battle_Standard_of_Honor()):
            return True

        if not snapshot.in_aggro:
            # During approach there is no live Panic packet to protect.  Use the
            # idle action to repair Winter/Mantra before the next engagement.
            if self.IsCloseToAggro() and (yield from self._cast_winter_backup()):
                return True
            if (yield from self._cast_mantra_of_frost(min_energy_pct=0.40)):
                return True
            return False

        # Emergency energy floor is the only thing allowed to precede Panic.
        if snapshot.enemy_casting_spell_or_chant and (yield from self._try_power_drain_team_packet(energy_threshold_pct=0.30)):
            return True

        if (yield from self.skills.Mesmer.DominationMagic.Shatter_Hex(min_priority=HexRemovalPriority.HIGH)):
            return True

        # Tryptophan is an Urgoz/12-man RoJ synergy tool only.
        if (
            int(Map.GetMapID() or 0) == 266
            and self.IsSkillEquipped(Tryptophan_Signet_ID)
            and (yield from self.skills.Any.PvE.Tryptophan_Signet())
        ):
            return True

        # Stage 2.17: dangerous casts (notably Urgoz Splinter Weapon) get the
        # 1/4-second Complicate lane before Panic/Cry.  The shared exact-cast claim
        # prevents a second Mesmer from spending another interrupt on the same cast.
        if snapshot.enemy_in_spellcast and (yield from self._try_complicate_mesmerway(emergency_only=True)):
            return True

        # Core control rotation: Panic -> Cry of Frustration -> Power Drain.
        # Panic is retried whenever recharge is ready, refreshing/maintaining the
        # hex on the current packet instead of being treated as a one-shot opener.
        if snapshot.enemy_in_spellcast and (yield from self._try_panic_team_packet()):
            return True

        if snapshot.enemy_casting and (yield from self._try_cry_of_frustration_team_packet()):
            return True

        if snapshot.enemy_casting_spell_or_chant and (yield from self._try_power_drain_team_packet()):
            return True

        # Non-emergency Complicate is still valuable when several nearby foes are
        # known to share the same skill; keep it below the primary Panic/Cry lane.
        if snapshot.enemy_in_spellcast and (yield from self._try_complicate_mesmerway(emergency_only=False)):
            return True

        # Stage 2.57: remove the <=5% tail after keeping healer shutdown,
        # Panic and critical interrupts above this line authoritative.
        if (yield from self._try_cleanup_direct()):
            return True

        # Stage 2.03 frost support.  Panic/Cry/Power Drain always get first
        # refusal; only then may the Panic Mesmer repair lost Winter coverage or
        # refresh its own Mantra of Frost stance.
        if (yield from self._cast_winter_backup()):
            return True
        if (yield from self._cast_mantra_of_frost(min_energy_pct=0.40)):
            return True

        if snapshot.player_energy_pct >= 0.50 and (yield from self.skills.Mesmer.DominationMagic.Shatter_Hex(min_priority=HexRemovalPriority.MEDIUM)):
            return True

        if (yield from self._try_shatter_enchantment_mesmerway()):
            return True

        if (yield from self._try_shatter_delusions_mesmerway()):
            return True

        if snapshot.enemy_casting_spell and (yield from self._try_mistrust_mesmerway()):
            return True

        if snapshot.enemy_casting and (yield from self._try_power_spike_mesmerway()):
            return True

        if (yield from self._try_diversion_mesmerway()):
            return True

        # Optional PvE shutdown only after Panic and fast direct interrupts had
        # first refusal. It deliberately avoids Mistrust-protected packets.
        if (yield from self._try_technobabble_mesmerway()):
            return True

        if snapshot.enemy_casting and (yield from self._try_overload_mesmerway()):
            return True

        if snapshot.enemy_casting and (yield from self._try_cry_of_pain_mesmerway()):
            return True

        # EVAS is pressure/cleanup, not a Panic opener.  Use the same shared
        # target lease as the four eSurges so multiple copies spread naturally.
        if (yield from self._try_evas_mesmerway()):
            return True

        # Pure filler: only after healing and the complete Panic/Cry/Power-Drain
        # control lane had first refusal.
        if snapshot.enemy_in_spellcast and (yield from self._try_unnatural_mesmerway()):
            return True

        if snapshot.enemy_in_spellcast and (yield from self._try_spiritual_pain_mesmerway()):
            return True

        if snapshot.player_energy_pct >= 0.70 and (yield from self.skills.Mesmer.DominationMagic.Shatter_Hex()):
            return True

        yield
