from dataclasses import dataclass
from collections.abc import Generator

from Py4GWCoreLib import Agent, Player, Profession, Range, Routines, BuildMgr, GLOBAL_CACHE
from Py4GWCoreLib.Skill import Skill
from Py4GWCoreLib.Builds.Any.HeroAI import HeroAI as HeroAIBuild
from Py4GWCoreLib.Builds.Skills import HexRemovalPriority, SkillsTemplate
from Py4GWCoreLib.Builds.Skills import MesmerwayCoordination as MWC
from Py4GWCoreLib.Builds.Skills import MesmerWeaponSetController as MWSC


Energy_Surge_ID = Skill.GetID("Energy_Surge")
Famine_ID = Skill.GetID("Famine")
Cry_of_Frustration_ID = Skill.GetID("Cry_of_Frustration")
Cry_of_Pain_ID = Skill.GetID("Cry_of_Pain")
Signet_of_Corruption_Kurzick_ID = Skill.GetID("Signet_of_Corruption_kurzick")
Signet_of_Corruption_Luxon_ID = Skill.GetID("Signet_of_Corruption_luxon")
Mistrust_ID = Skill.GetID("Mistrust")
Unnatural_Signet_ID = Skill.GetID("Unnatural_Signet")
Shatter_Hex_ID = Skill.GetID("Shatter_Hex")
Spiritual_Pain_ID = Skill.GetID("Spiritual_Pain")
Power_Spike_ID = Skill.GetID("Power_Spike")
Power_Drain_ID = Skill.GetID("Power_Drain")
Overload_ID = Skill.GetID("Overload")
Aneurysm_ID = Skill.GetID("Aneurysm")
Mind_Wrack_ID = Skill.GetID("Mind_Wrack")
Shatter_Delusions_ID = Skill.GetID("Shatter_Delusions")
Web_of_Disruption_ID = Skill.GetID("Web_of_Disruption")
Phantom_Pain_ID = Skill.GetID("Phantom_Pain")
Epidemic_ID = Skill.GetID("Epidemic")
Fragility_ID = Skill.GetID("Fragility")
Air_of_Superiority_ID = Skill.GetID("Air_of_Superiority")
Ebon_Vanguard_Assassin_Support_ID = Skill.GetID("Ebon_Vanguard_Assassin_Support")
Shatter_Enchantment_ID = Skill.GetID("Shatter_Enchantment")
Drain_Enchantment_ID = Skill.GetID("Drain_Enchantment")
Complicate_ID = Skill.GetID("Complicate")
Technobabble_ID = Skill.GetID("Technobabble")
Ebon_Battle_Standard_of_Wisdom_ID = Skill.GetID("Ebon_Battle_Standard_of_Wisdom")
Mending_Refrain_ID = Skill.GetID("Mending_Refrain")
Mantra_of_Frost_ID = Skill.GetID("Mantra_of_Frost")
Heroic_Refrain_ID = Skill.GetID("Heroic_Refrain")
Aegis_ID = Skill.GetID("Aegis")


@dataclass(slots=True)
class _EnergySurgeSnapshot:
    in_aggro: bool = False
    close_to_aggro: bool = False
    energy_pct: float = 1.0


class Energy_Surge(BuildMgr):
    """Fresh 4x eSurge controller for the HR Mesmerway project.

    Only Energy Surge is required for build matching.  The remaining skills are
    optional, so different eSurge bars may carry different interrupt/PvE/utility
    slots while still sharing the same cluster and whiteboard coordination.

    This is deliberately new Mesmerway logic.  Stable KeySoJway infrastructure
    is reused for sensing/team packet selection, not the old V28 eSurge policy.
    """

    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Energy Surge - HR Mesmerway",
            required_primary=Profession.Mesmer,
            template_code="OQBCAswEc5Jw0zuoNopTOggD",
            # Stage 2.11: one Mesmer may replace eSurge with Famine.
            required_skills=[Energy_Surge_ID, Famine_ID],
            optional_skills=[
                Cry_of_Frustration_ID,
                Cry_of_Pain_ID,
                Signet_of_Corruption_Kurzick_ID,
                Signet_of_Corruption_Luxon_ID,
                Mistrust_ID,
                Unnatural_Signet_ID,
                Shatter_Hex_ID,
                Spiritual_Pain_ID,
                Power_Spike_ID,
                Power_Drain_ID,
                Overload_ID,
                Aneurysm_ID,
                Mind_Wrack_ID,
                Shatter_Delusions_ID,
                Web_of_Disruption_ID,
                Phantom_Pain_ID,
                Epidemic_ID,
                Fragility_ID,
                Air_of_Superiority_ID,
                Ebon_Vanguard_Assassin_Support_ID,
                Shatter_Enchantment_ID,
                Drain_Enchantment_ID,
                Complicate_ID,
                Technobabble_ID,
                Ebon_Battle_Standard_of_Wisdom_ID,
                Mending_Refrain_ID,
                Mantra_of_Frost_ID,
                Aegis_ID,
            ],
        )
        self.minimum_required_match = 1
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAIBuild(standalone_fallback=True))
        self.SetBlockedSkills([
            Energy_Surge_ID,
            Famine_ID,
            Cry_of_Frustration_ID,
            Cry_of_Pain_ID,
            Signet_of_Corruption_Kurzick_ID,
            Signet_of_Corruption_Luxon_ID,
            Mistrust_ID,
            Unnatural_Signet_ID,
            Shatter_Hex_ID,
            Spiritual_Pain_ID,
            Power_Spike_ID,
            Power_Drain_ID,
            Overload_ID,
            Aneurysm_ID,
            Mind_Wrack_ID,
            Shatter_Delusions_ID,
            Web_of_Disruption_ID,
            Phantom_Pain_ID,
            Epidemic_ID,
            Fragility_ID,
            Air_of_Superiority_ID,
            Ebon_Vanguard_Assassin_Support_ID,
            Shatter_Enchantment_ID,
            Drain_Enchantment_ID,
            Complicate_ID,
            Technobabble_ID,
            Ebon_Battle_Standard_of_Wisdom_ID,
            Mending_Refrain_ID,
            Mantra_of_Frost_ID,
            Aegis_ID,
        ])
        self.SetSkillCastingFn(self._run_local_skill_logic)
        self.skills: SkillsTemplate = SkillsTemplate(self)
        self._mesmerway_combat_started_ms: int = 0
        self._mesmerway_was_in_aggro: bool = False
        # Local anti-double-dispatch guard. HeroAI/skillbar recharge snapshots can lag
        # a few frames after Complicate is accepted; this prevents a second request
        # during that stale-ready window without blocking legitimate HSR recharges.
        self._mesmerway_complicate_dispatch_until_ms: int = 0
        self._mesmerway_shatter_diag_last_ms: int = 0
        # Stage 1.40: local fail-safe against reusing the same Fragility feeder
        # after this exact client already shattered it. Shared-memory consumed
        # locks coordinate other clients; this short local cache closes the
        # same-owner/reentry visibility gap seen in Stage 1.39 logs.
        self._mesmerway_shatter_local_consumed_until: dict[int, int] = {}
        self._mesmerway_weapon_sets = MWSC.MesmerWeaponSetController(role="esurge")
        # Targets on which this exact client successfully placed Mind Wrack.
        # Ownership matters because only the caster's own non-hex Mesmer skills
        # trigger Wrack's extra 1-Energy loss/damage.
        self._mesmerway_mind_wrack_owned: set[int] = set()
        self._mesmerway_last_cast_evidence: str = "not_queued"
        self._mesmerway_optional_loadout_logged: bool = False
        self._mesmerway_epidemic_diag_last_ms: int = 0
        # Stage 1.63: each Aneurysm carrier may hold one short local setup
        # transaction while other Mesmers plant Wrack on zeroable neighbours.
        self._mesmerway_aneurysm_setup_target: int = 0
        self._mesmerway_aneurysm_setup_started_ms: int = 0
        # Stage 2.40: local diagnostics only. Cross-account Aegis ownership uses
        # the two-phase SharedMemory election in MesmerwayCoordination.
        self._mesmerway_aegis_diag_last_ms: int = 0

    def _snapshot(self) -> _EnergySurgeSnapshot:
        self._update_mesmerway_combat_clock()
        try:
            energy = float(Agent.GetEnergy(Player.GetAgentID()) or 0.0)
        except Exception:
            energy = 1.0
        return _EnergySurgeSnapshot(
            in_aggro=bool(self.IsInAggro()),
            close_to_aggro=bool(self.IsCloseToAggro()),
            energy_pct=max(0.0, min(1.0, energy)),
        )

    def _cast_targeted(
        self,
        skill_id: int,
        target_id: int,
        *,
        aftercast: int = 250,
        interrupt_packet: MWC.CryPacket | None = None,
        interrupt_claim: bool = False,
        urgent: bool = False,
        aneurysm_expected_gain: float = 0.0,
        aneurysm_affected_neighbors: tuple[int, ...] = (),
        aneurysm_prewrack_targets: tuple[int, ...] = (),
        epidemic_plan: dict[str, object] | None = None,
    ) -> Generator[object, None, bool]:
        if int(target_id or 0) <= 0 or not self.IsSkillEquipped(int(skill_id)):
            return False
        options: MWC.InterruptDispatchOptions = {}
        if interrupt_claim or int(skill_id) in (Cry_of_Frustration_ID, Complicate_ID, Power_Spike_ID, Power_Drain_ID):
            options = MWC.interrupt_dispatch_options(target_id, skill_id, interrupt_packet)
        receipt, options = MWC.coordinated_dispatch_options(skill_id, target_id, options, urgent=urgent)
        if int(skill_id) == int(Aneurysm_ID):
            receipt.aneurysm_expected_gain = float(aneurysm_expected_gain or 0.0)
            receipt.aneurysm_affected_neighbors = tuple(int(v) for v in aneurysm_affected_neighbors if int(v or 0) > 0)
            receipt.aneurysm_prewrack_targets = tuple(int(v) for v in aneurysm_prewrack_targets if int(v or 0) > 0)
        if int(skill_id) == int(Epidemic_ID) and epidemic_plan:
            receipt.epidemic_plan = dict(epidemic_plan)
        wrack_owner_trigger = False
        if int(target_id) in self._mesmerway_mind_wrack_owned and MWC.is_mind_wrack_active(int(target_id)):
            # Capture this *before* dispatch. Shatter Delusions or a zeroing
            # Energy Surge can end Mind Wrack during the cast, but the targeting
            # trigger happened first and still costs the foe 1 Energy.
            try:
                profession_id, _secondary = GLOBAL_CACHE.Skill.GetProfession(int(skill_id))
                is_mesmer = int(getattr(profession_id, "value", profession_id) or 0) == int(Profession.Mesmer.value)
                is_hex = bool(GLOBAL_CACHE.Skill.Flags.IsHex(int(skill_id)))
                wrack_owner_trigger = bool(is_mesmer and not is_hex)
            except Exception:
                wrack_owner_trigger = False

        did_cast = bool(
            (
                yield from self.CastSkillIDAndRestoreTarget(
                    skill_id=int(skill_id),
                    target_agent_id=int(target_id),
                    log=False,
                    aftercast_delay=max(0, int(aftercast)),
                    **options,
                )
            )
        )
        if not did_cast:
            receipt.queue_reject_reason = str(self.GetLastCastRejectReason() or "unknown_buildmgr_rejection")
        did_cast = yield from MWC.confirm_coordinated_cast(receipt, did_cast)
        self._mesmerway_last_cast_evidence = receipt.evidence
        if did_cast and wrack_owner_trigger:
            MWC.register_mind_wrack_nonhex_trigger(int(target_id))
        if int(target_id) in self._mesmerway_mind_wrack_owned and not MWC.is_mind_wrack_active(int(target_id)):
            self._mesmerway_mind_wrack_owned.discard(int(target_id))
        return bool(did_cast)

    def _cast_mending_refrain_upkeep(self) -> Generator[object, None, bool]:
        """OOC Mending Refrain spread for the eSurge that carries the slot.

        Wait until this account itself has Heroic Refrain so Mending is created
        at the boosted Motivation rank.  During combat the HR Paragon's chants
        maintain both refrains; this Mesmer never burns combat actions spreading it.
        """
        if not self.IsSkillEquipped(Mending_Refrain_ID) or not self.CanCastSkillID(Mending_Refrain_ID):
            return False
        if self.IsInAggro():
            return False
        try:
            energy = float(Agent.GetEnergy(Player.GetAgentID()) or 0.0)
        except Exception:
            energy = 1.0
        if energy < 0.55:
            return False
        try:
            if not Routines.Checks.Agents.HasEffect(Player.GetAgentID(), Heroic_Refrain_ID):
                return False
        except Exception:
            return False
        did_cast = yield from self.SpreadEchoToAlly(
            Mending_Refrain_ID, max_range=float(Range.Earshot.value), aftercast_delay=250
        )
        if did_cast:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_ESURGE_MENDING_REFRAIN_SPREAD",
                    energy=f"{energy:.3f}",
                    policy="ooc_after_self_hr_then_hr_chants_maintain",
                )
            except Exception:
                pass
        return bool(did_cast)

    def _cast_air_of_superiority(self) -> Generator[object, None, bool]:
        if not self.IsSkillEquipped(Air_of_Superiority_ID):
            return False
        return bool((yield from self.skills.Any.PvE.Air_of_Superiority()))

    def _cast_mantra_of_frost(self, *, min_energy_pct: float = 0.40) -> Generator[object, None, bool]:
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
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_MANTRA_OF_FROST", role="esurge", energy=round(float(energy), 3),
                    policy="stage2_02_ooc_first_combat_after_primary_damage",
                )
            except Exception:
                pass
        return bool(did)

    @staticmethod
    def _mesmerway_now_ms() -> int:
        try:
            import PySystem
            return int(PySystem.get_tick_count64() or 0)
        except Exception:
            return 0

    def _log_optional_loadout_once(self) -> None:
        if bool(self._mesmerway_optional_loadout_logged):
            return
        self._mesmerway_optional_loadout_logged = True
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_ESURGE_OPTIONAL_LOADOUT",
                epidemic_equipped=bool(self.IsSkillEquipped(Epidemic_ID)),
                fragility_equipped=bool(self.IsSkillEquipped(Fragility_ID)),
                phantom_pain_equipped=bool(self.IsSkillEquipped(Phantom_Pain_ID)),
                web_equipped=bool(self.IsSkillEquipped(Web_of_Disruption_ID)),
                shatter_delusions_equipped=bool(self.IsSkillEquipped(Shatter_Delusions_ID)),
                wisdom_equipped=bool(self.IsSkillEquipped(Ebon_Battle_Standard_of_Wisdom_ID)),
                mantra_of_frost_equipped=bool(self.IsSkillEquipped(Mantra_of_Frost_ID)),
                aegis_equipped=bool(self.IsSkillEquipped(Aegis_ID)),
                policy="stage2_02_frost_support_loadout_visibility",
            )
        except Exception:
            pass

    def _log_epidemic_no_plan(self, reason: str, **fields: object) -> None:
        now = int(self._mesmerway_now_ms() or 0)
        if now > 0 and now - int(self._mesmerway_epidemic_diag_last_ms or 0) < 1800:
            return
        self._mesmerway_epidemic_diag_last_ms = now
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_EPIDEMIC_NO_PLAN",
                reason=str(reason or "unknown"),
                **fields,
                policy="stage1_59_epidemic_observed_vs_phantom_expected_provenance",
            )
        except Exception:
            pass

    def _update_mesmerway_combat_clock(self) -> None:
        # Stage 1.29: authoritative packet contact + grace-backed shared clock.
        # Do not reset Wisdom timing on transient IsInAggro false frames.
        now = self._mesmerway_now_ms()
        elapsed = int(MWC.wisdom_combat_elapsed_ms(bool(self.IsInAggro())) or 0)
        self._mesmerway_combat_started_ms = int(now - elapsed) if now > 0 and elapsed > 0 else 0
        self._mesmerway_was_in_aggro = bool(self.IsInAggro())

    def _aegis_activation_ms(self) -> int:
        """Fast-Casting-adjusted local Aegis activation estimate."""
        try:
            base_s = max(0.0, float(Skill.Data.GetActivation(int(Aegis_ID)) or 2.0))
        except Exception:
            base_s = 2.0
        activation_s = float(base_s)
        try:
            from Py4GWCoreLib.HeroAI import interrupt as reforged_interrupt
            fast_casting = int(reforged_interrupt._get_player_fast_casting_level() or 0)
            adjusted, _recharge = Routines.Checks.Skills.apply_fast_casting(
                int(Aegis_ID), int(fast_casting)
            )
            if float(adjusted or 0.0) > 0.0:
                activation_s = float(adjusted)
        except Exception:
            pass
        return max(250, int(round(float(activation_s) * 1000.0)))

    def _cast_aegis_rotation(self, *, in_combat: bool) -> Generator[object, None, bool]:
        """Maintain one Aegis chain across three *player accounts*.

        Stage 2.40 uses a two-phase shared-memory election. Every locally ready
        Aegis carrier announces candidacy, waits a short settle window, and then
        every client independently derives the same winner. This avoids the
        Stage 2.38 race where three accounts could all append an "exclusive"
        PostLock row and cast together.
        """
        if not bool(in_combat) or not self.IsSkillEquipped(Aegis_ID):
            MWC.release_esurge_aegis_candidate("out_of_combat_or_not_equipped")
            MWC.release_esurge_aegis_pending("out_of_combat_or_not_equipped")
            return False

        status = MWC.esurge_aegis_party_status()
        activation_ms = int(self._aegis_activation_ms())
        due = bool(MWC.esurge_aegis_due(
            present=bool(status.get("present", False)),
            remaining_ms=int(status.get("remaining_ms", 0) or 0),
            activation_ms=int(activation_ms),
        ))
        if not due:
            # The fresh party layer is authoritative. Once visible, remove this
            # account's stale election/pending rows so the next cycle starts clean.
            MWC.release_esurge_aegis_candidate("layer_not_due")
            MWC.release_esurge_aegis_pending("layer_not_due")
            return False

        if not self.CanCastSkillID(Aegis_ID):
            MWC.release_esurge_aegis_candidate("local_aegis_not_ready")
            return False

        # Phase 1: advertise readiness. Do not cast on the same controller tick;
        # all accounts need a brief chance to publish before ownership is chosen.
        if not MWC.post_esurge_aegis_candidate():
            return False

        election = MWC.esurge_aegis_election_state()
        if not bool(election.get("ready", False)):
            now = int(self._mesmerway_now_ms() or 0)
            if now - int(self._mesmerway_aegis_diag_last_ms or 0) >= 700:
                self._mesmerway_aegis_diag_last_ms = int(now)
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "MESMERWAY_ESURGE_AEGIS_ELECTION_WAIT",
                        caster_id=int(Player.GetAgentID() or 0),
                        candidate_count=int(election.get("candidate_count", 0) or 0),
                        settle_remaining_ms=int(election.get("settle_remaining_ms", 0) or 0),
                        blocked_by_pending=bool(election.get("blocked_by_pending", False)),
                        local_is_winner=bool(election.get("local_is_winner", False)),
                        policy="stage2_40_cross_account_two_phase_election",
                    )
                except Exception:
                    pass
            return False

        if not bool(election.get("local_is_winner", False)):
            return False

        # Phase 2: only the deterministic winner may publish the cast-in-flight
        # lease. reserve_esurge_aegis_cast fails closed on shared-memory trouble.
        if not MWC.reserve_esurge_aegis_cast():
            return False

        queued_at = int(self._mesmerway_now_ms() or 0)

        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_ESURGE_AEGIS_ELECTED",
                caster_id=int(Player.GetAgentID() or 0),
                candidate_count=int(election.get("candidate_count", 0) or 0),
                winner_is_local=bool(election.get("local_is_winner", False)),
                prior_remaining_ms=int(status.get("remaining_ms", 0) or 0),
                policy="stage2_40_cross_account_two_phase_election",
            )
        except Exception:
            pass

        def _on_dispatched() -> None:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_ESURGE_AEGIS_DISPATCHED",
                    caster_id=int(Player.GetAgentID() or 0),
                    prior_remaining_ms=int(status.get("remaining_ms", 0) or 0),
                    prior_covered=int(status.get("covered", 0) or 0),
                    prior_living=int(status.get("living", 0) or 0),
                    activation_ms=int(activation_ms),
                    carrier_count=len(MWC.esurge_aegis_actual_carriers()),
                    policy="stage2_40_cross_account_two_phase_election",
                )
            except Exception:
                pass

        def _on_cancelled() -> None:
            MWC.release_esurge_aegis_pending("dispatch_cancelled")
            MWC.release_esurge_aegis_candidate("dispatch_cancelled")

        did_cast = bool((yield from self.CastSkillID(
            skill_id=Aegis_ID,
            target_agent_id=0,
            log=False,
            aftercast_delay=300,
            dispatch_guard=lambda: bool(self.IsInAggro()) and (
                int(self._mesmerway_now_ms() or 0) - int(queued_at or 0) < 3200
            ),
            on_dispatched=_on_dispatched,
            on_cancelled=_on_cancelled,
        )))
        if not did_cast:
            MWC.release_esurge_aegis_pending("queue_rejected")
            MWC.release_esurge_aegis_candidate("queue_rejected")
            return False

        # Candidacy is no longer needed after the winning queue was accepted.
        # Keep only the pending lease until the new party Aegis layer is observed.
        MWC.release_esurge_aegis_candidate("winning_queue_accepted")
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_ESURGE_AEGIS_QUEUED",
                caster_id=int(Player.GetAgentID() or 0),
                prior_present=bool(status.get("present", False)),
                prior_remaining_ms=int(status.get("remaining_ms", 0) or 0),
                coverage_ratio=round(float(status.get("coverage_ratio", 0.0) or 0.0), 3),
                activation_ms=int(activation_ms),
                pending_ms=int(MWC.ESURGE_AEGIS_PENDING_MS),
                policy="stage2_40_cross_account_two_phase_election",
            )
        except Exception:
            pass
        return True

    def _cast_wisdom_post_contact(self) -> Generator[object, None, bool]:
        if not self.IsSkillEquipped(Ebon_Battle_Standard_of_Wisdom_ID):
            return False
        if not self.CanCastSkillID(Ebon_Battle_Standard_of_Wisdom_ID):
            return False
        elapsed = int(MWC.wisdom_combat_elapsed_ms(bool(self.IsInAggro())) or 0)
        caster_id = int(Player.GetAgentID() or 0)
        plan = MWC.wisdom_cast_plan(caster_id, elapsed, carrier_role="esurge")
        MWC.log_wisdom_plan(plan, role="esurge")
        if not bool(plan.get("allowed", False)):
            return False
        if not MWC.reserve_wisdom_cast(caster_id):
            return False

        dispatch_tick = self._mesmerway_now_ms()
        receipt: dict = {}
        def wisdom_dispatched() -> None:
            receipt["tick"] = self._mesmerway_now_ms()
            receipt["xy"] = tuple(Player.GetXY())
        did_cast = yield from self.CastSkillID(
            skill_id=Ebon_Battle_Standard_of_Wisdom_ID,
            # Wisdom is untargeted / planted at the caster's current location.
            target_agent_id=0,
            log=False,
            aftercast_delay=750,
            dispatch_guard=lambda: self._mesmerway_now_ms() - dispatch_tick < 3000,
            on_dispatched=wisdom_dispatched,
        )
        if not did_cast:
            MWC.release_wisdom_pending("dispatch_failed")
            return False

        confirmed, confirmation = yield from MWC.confirm_local_wisdom_activation(dispatch_tick, receipt)
        if not confirmed:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_WISDOM_CAST_FAILED", role="esurge", reason=str(confirmation),
                    selected_reason=str(plan.get("reason", "")), elapsed_ms=int(elapsed),
                )
            except Exception:
                pass
            return False

        record = MWC.register_wisdom_cast(caster_id=caster_id, cast_xy=receipt.get("xy"),
                                         completed_tick=int(receipt.get("finished_ms", 0) or 0))
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_WISDOM_CAST",
                role="esurge", elapsed_ms=int(elapsed), plan_reason=str(plan.get("reason", "")),
                living_mesmers=int(plan.get("living_mesmers", 0) or 0),
                mesmers_in_area=int(plan.get("mesmers_in_area", 0) or 0),
                required_mesmers=int(plan.get("required_mesmers", 0) or 0),
                bip_in_area=int(plan.get("bip_in_area", 0) or 0),
                st_in_area=int(plan.get("st_in_area", 0) or 0),
                rank=int(record.get("rank", plan.get("rank", 0)) or 0),
                hsr_pct=int(record.get("hsr_pct", plan.get("hsr_pct", 0)) or 0),
                duration_ms=int(record.get("duration_ms", plan.get("duration_ms", 0)) or 0),
                center_x=round(float(record.get("xy", (0.0, 0.0))[0]), 1) if record else float(plan.get("center_x", 0.0) or 0.0),
                center_y=round(float(record.get("xy", (0.0, 0.0))[1]), 1) if record else float(plan.get("center_y", 0.0) or 0.0),
                confirmation=str(confirmation),
                effect_observation="estimated_spatial_lease_not_direct_hsr_proc",
            )
        except Exception:
            pass
        return True

    def _cast_complicate(self, *, emergency_only: bool = False) -> Generator[object, None, bool]:
        now = self._mesmerway_now_ms()
        if now > 0 and now < int(self._mesmerway_complicate_dispatch_until_ms or 0):
            return False
        if not self.IsSkillEquipped(Complicate_ID) or not self.CanCastSkillID(Complicate_ID):
            return False
        target, enemy_skill_id = MWC.reserve_complicate_target(emergency_only=bool(emergency_only))
        if int(target or 0) <= 0:
            return False
        # Short pre-dispatch guard covers same-frame coroutine re-entry.
        if now > 0:
            self._mesmerway_complicate_dispatch_until_ms = now + 900
        did_cast = bool((yield from self._cast_targeted(Complicate_ID, int(target))))
        if did_cast:
            # 3.0 s is deliberately far below the shortest realistic HSR recharge,
            # but long enough to absorb stale skillbar/recharge snapshots.
            now2 = self._mesmerway_now_ms()
            if now2 > 0:
                self._mesmerway_complicate_dispatch_until_ms = now2 + 3000
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_COMPLICATE",
                    target_id=int(target), enemy_skill_id=int(enemy_skill_id),
                    dispatch_guard_ms=3000,
                    frustration_focus=bool(MWC.is_frustration_focused(int(target))),
                    policy="stage1_23_emergency_first_then_post_esurge_generic_interrupt_spam",
                )
            except Exception:
                pass
        else:
            self._mesmerway_complicate_dispatch_until_ms = 0
            MWC.release_complicate_reservation(
                int(enemy_skill_id), target_id=int(target),
                reason="mesmerway_complicate_not_fired",
            )
        return did_cast

    def _cast_shatter_enchantment(self) -> Generator[object, None, bool]:
        if not self.IsSkillEquipped(Shatter_Enchantment_ID) or not self.CanCastSkillID(Shatter_Enchantment_ID):
            return False
        target = int(MWC.reserve_shatter_enchantment_target() or 0)
        if target <= 0:
            return False
        did_cast = bool((yield from self._cast_targeted(Shatter_Enchantment_ID, target)))
        if did_cast:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_SHATTER_ENCHANTMENT", target_id=int(target),
                    policy="stage2_47_shared_enchantment_strip_damage_after_esurge",
                )
            except Exception:
                pass
        return did_cast

    def _cast_drain_enchantment(self, *, recovery_only: bool = False) -> Generator[object, None, bool]:
        if not self.IsSkillEquipped(Drain_Enchantment_ID) or not self.CanCastSkillID(Drain_Enchantment_ID):
            return False
        try:
            energy_pct = float(Agent.GetEnergy(Player.GetAgentID()) or 0.0)
        except Exception:
            energy_pct = 1.0
        if recovery_only and energy_pct > 0.66:
            return False
        target = int(MWC.reserve_shatter_enchantment_target() or 0)
        if target <= 0:
            return False
        did_cast = bool((yield from self._cast_targeted(Drain_Enchantment_ID, target)))
        if did_cast:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_DRAIN_ENCHANTMENT", target_id=int(target),
                    energy_pct_before=round(float(energy_pct), 3), recovery_only=bool(recovery_only),
                    policy="stage2_47_shared_enchantment_strip_energy_recovery_after_esurge",
                )
            except Exception:
                pass
        return did_cast

    def _cast_technobabble(self) -> Generator[object, None, bool]:
        if not self.IsSkillEquipped(Technobabble_ID) or not self.CanCastSkillID(Technobabble_ID):
            return False
        if not self.IsInAggro():
            return False
        try:
            if float(Agent.GetEnergy(Player.GetAgentID()) or 0.0) < 0.45:
                return False
        except Exception:
            pass
        target = int(MWC.reserve_technobabble_target() or 0)
        if target <= 0:
            return False
        did_cast = bool((yield from self._cast_targeted(Technobabble_ID, target)))
        if did_cast:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_TECHNOBABBLE_SMART", target_id=int(target),
                    policy="preemptive_daze_after_direct_interrupts_no_mistrust_overlap_exact_packet",
                )
            except Exception:
                pass
        return did_cast

    def _cast_cry(self, *, mode: str = "offensive") -> Generator[object, None, bool]:
        if not self.IsSkillEquipped(Cry_of_Frustration_ID) or not self.CanCastSkillID(Cry_of_Frustration_ID):
            return False
        reservation = MWC.reserve_cry_packet(mode=str(mode))
        if reservation is None:
            return False
        did_cast = yield from self._cast_targeted(
            Cry_of_Frustration_ID, reservation.target_id, interrupt_packet=reservation
        )
        if did_cast:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_CRY_PACKET",
                    target_id=int(reservation.target_id),
                    covered_ids=",".join(str(x) for x in reservation.covered_enemy_ids),
                    damage_ids=",".join(str(x) for x in getattr(reservation, "damage_enemy_ids", ()) or ()),
                    damage_count=int(len(getattr(reservation, "damage_enemy_ids", ()) or ())),
                    frustration_focus=bool(getattr(reservation, "frustration_focus", False)),
                    danger_score=int(getattr(reservation, "danger_score", 0) or 0),
                    mode=str(getattr(reservation, "mode", mode)),
                    policy="stage1_23_emergency_before_esurge_offensive_after_esurge_single_target_allowed",
                )
            except Exception:
                pass
        else:
            MWC.release_cry_interrupt_reservation(
                reservation, reason="mesmerway_cry_not_fired"
            )
        return bool(did_cast)

    def _cast_cry_of_pain(self) -> Generator[object, None, bool]:
        if not self.IsSkillEquipped(Cry_of_Pain_ID) or not self.CanCastSkillID(Cry_of_Pain_ID):
            return False
        target = MWC.reserve_cry_of_pain_target()
        if not target:
            return False
        did_cast = bool((yield from self._cast_targeted(Cry_of_Pain_ID, target)))
        if did_cast:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_CRY_OF_PAIN",
                    target_id=int(target),
                    policy="stage1_44_merged_hexed_cluster_before_complicate",
                )
            except Exception:
                pass
        return bool(did_cast)

    def _cast_power_drain(self, *, max_energy_pct: float, tactical_only: bool = False, critical_only: bool = False) -> Generator[object, None, bool]:
        if not self.IsSkillEquipped(Power_Drain_ID) or not self.CanCastSkillID(Power_Drain_ID):
            return False
        try:
            current = float(Agent.GetEnergy(Player.GetAgentID()) or 0.0)
        except Exception:
            current = 1.0
        if current > float(max_energy_pct):
            return False
        # Tactical lane: only high-value active spells; normal recovery uses
        # the unchanged strict shared interrupt-reservation API.
        if tactical_only:
            picker = getattr(MWC, 'reserve_tactical_power_drain_target', None)
            target = int(picker(critical_only=bool(critical_only)) or 0) if callable(picker) else 0
        else:
            target = MWC.reserve_single_interrupt_target(Power_Drain_ID)
        if not target:
            return False
        did_cast = bool((yield from self._cast_targeted(Power_Drain_ID, target)))
        if not did_cast:
            MWC.release_single_interrupt_reservation(
                target, Power_Drain_ID, reason="mesmerway_power_drain_not_fired"
            )
        return did_cast

    def _cast_power_spike(self, *, emergency_only: bool = False) -> Generator[object, None, bool]:
        if not self.IsSkillEquipped(Power_Spike_ID) or not self.CanCastSkillID(Power_Spike_ID):
            return False
        target = MWC.reserve_single_interrupt_target(Power_Spike_ID, emergency_only=bool(emergency_only))
        if not target:
            return False
        before = MWC.interrupt_dispatch_snapshot(int(target), Power_Spike_ID)
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_POWER_SPIKE_DISPATCH",
                phase="reserved", target_id=int(target),
                enemy_skill_id=int(before.get("enemy_skill_id", 0) or 0),
                remaining_ms=int(before.get("remaining_ms", -1) if before.get("remaining_ms") is not None else -1),
                priority=int(before.get("priority", 0) or 0),
                frustration_focus=bool(before.get("frustration_focus", False)),
                emergency_only=bool(emergency_only),
                policy="stage1_26_live_dispatch_timing",
            )
        except Exception:
            pass
        did_cast = bool((yield from self._cast_targeted(Power_Spike_ID, target)))
        after = MWC.interrupt_dispatch_snapshot(int(target), Power_Spike_ID)
        if did_cast:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_POWER_SPIKE_DISPATCH",
                    phase="command_returned", target_id=int(target),
                    enemy_skill_id=int(before.get("enemy_skill_id", 0) or 0),
                    enemy_skill_after=int(after.get("enemy_skill_id", 0) or 0),
                    remaining_before_ms=int(before.get("remaining_ms", -1) if before.get("remaining_ms") is not None else -1),
                    remaining_after_ms=int(after.get("remaining_ms", -1) if after.get("remaining_ms") is not None else -1),
                    emergency_only=bool(emergency_only),
                    policy="stage1_26_live_dispatch_timing",
                )
            except Exception:
                pass
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_POWER_SPIKE", target_id=int(target),
                    frustration_focus=bool(MWC.is_frustration_focused(int(target))),
                    emergency_only=bool(emergency_only),
                    policy="stage1_29_1750ms_normal_1300ms_emergency_live_gate",
                )
            except Exception:
                pass
        else:
            MWC.release_single_interrupt_reservation(
                target, Power_Spike_ID, reason="mesmerway_power_spike_not_fired"
            )
        return did_cast

    def _cast_overload(self) -> Generator[object, None, bool]:
        equipped = bool(self.IsSkillEquipped(Overload_ID))
        can_cast = bool(self.CanCastSkillID(Overload_ID)) if equipped else False
        if not equipped or not can_cast:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_keyed_state_event(
                    "MESMERWAY_OVERLOAD_STATE",
                    (int(Player.GetAgentID() or 0),),
                    signature_fields=("equipped", "can_cast"),
                    min_interval_ms=2000, heartbeat_ms=8000,
                    equipped=bool(equipped), can_cast=bool(can_cast),
                    policy="stage1_26_overload_loadout_readiness_diag",
                )
            except Exception:
                pass
            return False
        target = int(MWC.reserve_overload_target() or 0)
        if target <= 0:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_keyed_state_event(
                    "MESMERWAY_OVERLOAD_STATE",
                    (int(Player.GetAgentID() or 0),),
                    signature_fields=("equipped", "can_cast", "target_found"),
                    min_interval_ms=2000, heartbeat_ms=6000,
                    equipped=True, can_cast=True, target_found=False,
                    policy="stage1_26_overload_no_eligible_live_cast_diag",
                )
            except Exception:
                pass
            return False
        did_cast = bool((yield from self._cast_targeted(Overload_ID, target)))
        if did_cast:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_OVERLOAD",
                    target_id=int(target),
                    frustration_focus=bool(MWC.is_frustration_focused(int(target))),
                    policy="stage1_22_long_non_emergency_cast_setup_then_interrupt",
                )
            except Exception:
                pass
        return did_cast

    def _cast_cleanup_finisher(self, *, hard_only: bool = False) -> Generator[object, None, bool]:
        """Finish <=10% foes before they escape or recover.

        Critical interrupts already had first refusal.  Stage 1.23 also lets Cry
        execute a lone <=10% caster when the interrupt can realistically land;
        otherwise fast Domination fillers use the same shared cleanup lease.
        <=5% is treated as hard execute.
        """
        ready_unnatural = bool(
            self.IsSkillEquipped(Unnatural_Signet_ID) and self.CanCastSkillID(Unnatural_Signet_ID)
        )
        ready_spiritual = bool(
            self.IsSkillEquipped(Spiritual_Pain_ID) and self.CanCastSkillID(Spiritual_Pain_ID)
        )
        ready_overload = bool(
            self.IsSkillEquipped(Overload_ID) and self.CanCastSkillID(Overload_ID)
        )
        ready_cry = bool(
            self.IsSkillEquipped(Cry_of_Frustration_ID) and self.CanCastSkillID(Cry_of_Frustration_ID)
        )
        ready_spike = bool(self.IsSkillEquipped(Power_Spike_ID) and self.CanCastSkillID(Power_Spike_ID))
        ready_shatter = bool(self.IsSkillEquipped(Shatter_Enchantment_ID)
                             and self.CanCastSkillID(Shatter_Enchantment_ID))
        if not (ready_unnatural or ready_spiritual or ready_overload or ready_cry
                or ready_spike or ready_shatter):
            return False

        target = int(MWC.reserve_cleanup_target(hard_only=hard_only) or 0)
        if target <= 0:
            return False
        info = MWC.cleanup_target_info(target)

        skill_id = 0
        label = ""
        cry_reserved = False
        # Stage 1.23: if the execute target is actively using a skill, Cry is a
        # valid single-target finisher.  The exact reservation deliberately uses
        # the normal interrupt protections, so Mistrust/Overload setup is never
        # sabotaged just to secure an execute.
        if ready_cry and int(info.get("casting_skill_id", 0) or 0) > 0:
            cry_reserved = bool(MWC.reserve_specific_interrupt_target(
                int(target), Cry_of_Frustration_ID
            ))
            if cry_reserved:
                skill_id, label = Cry_of_Frustration_ID, "Cry_of_Frustration"
        # Unnatural is energy-free and gains packet value on hexed/enchanted foes.
        if skill_id <= 0 and ready_unnatural:
            try:
                if Agent.IsHexed(target) or Agent.IsEnchanted(target):
                    skill_id, label = Unnatural_Signet_ID, "Unnatural_Signet"
            except Exception:
                pass
        if skill_id <= 0 and ready_spiritual:
            skill_id, label = Spiritual_Pain_ID, "Spiritual_Pain"
        if skill_id <= 0 and ready_unnatural:
            skill_id, label = Unnatural_Signet_ID, "Unnatural_Signet"
        # Conditional interrupts are only valid while a cast is actually active.
        if skill_id <= 0 and ready_spike and int(info.get("casting_skill_id", 0) or 0) > 0:
            if MWC.reserve_specific_interrupt_target(int(target), Power_Spike_ID):
                skill_id, label = Power_Spike_ID, "Power_Spike"
        if skill_id <= 0 and ready_overload and int(info.get("casting_skill_id", 0) or 0) > 0:
            skill_id, label = Overload_ID, "Overload"
        # Strip + damage is a genuine instant cleanup only on enchanted enemies.
        if skill_id <= 0 and ready_shatter:
            try:
                enchanted = bool(Agent.IsEnchanted(int(target)))
            except Exception:
                enchanted = False
            if enchanted and int(MWC.reserve_shatter_enchantment_target(cleanup_target_id=int(target)) or 0) == int(target):
                skill_id, label = Shatter_Enchantment_ID, "Shatter_Enchantment"
        if skill_id <= 0:
            MWC.release_cleanup_reservation(int(target), reason="no_ready_finisher_after_reservation")
            return False

        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_CLEANUP_TRIGGER",
                target_id=int(target),
                skill_id=int(skill_id),
                skill=str(label),
                target_hp=f"{float(info.get('hp', 0.0) or 0.0):.3f}",
                hard_execute=bool(info.get("hard", False)),
                policy="<=10pct_claimed_before_cast",
            )
        except Exception:
            pass

        did_cast = bool((yield from self._cast_targeted(int(skill_id), int(target), aftercast=180)))
        if int(skill_id) == int(Cry_of_Frustration_ID):
            if not did_cast and cry_reserved:
                MWC.release_single_interrupt_reservation(
                    int(target), Cry_of_Frustration_ID, reason="mesmerway_cleanup_cry_not_fired"
                )
        if not did_cast:
            if int(skill_id) == int(Power_Spike_ID):
                MWC.release_single_interrupt_reservation(int(target), Power_Spike_ID, reason="cleanup_spike_not_fired")
            MWC.release_cleanup_reservation(int(target), reason=str(self._mesmerway_last_cast_evidence or "dispatch_failed"))
        if did_cast:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_CLEANUP_FINISHER",
                    target_id=int(target),
                    skill_id=int(skill_id),
                    skill=str(label),
                    target_hp=f"{float(info.get('hp', 0.0) or 0.0):.3f}",
                    hard_execute=bool(info.get("hard", False)),
                    moving=bool(info.get("moving", False)),
                    casting_skill_id=int(info.get("casting_skill_id", 0) or 0),
                    policy="<=10pct_shared_finisher_<=5pct_hard_execute",
                )
            except Exception:
                pass
        return bool(did_cast)

    def _cast_energy_surge(self, *, cleanup_only: bool = False) -> Generator[object, None, bool]:
        if not self.IsSkillEquipped(Energy_Surge_ID) or not self.CanCastSkillID(Energy_Surge_ID):
            return False
        # Stage 2.42: eSurge may participate in cleanup when the dying primary
        # still anchors real packet damage or this exact account owns Mind Wrack
        # on it.  Otherwise keep the healthy-target battery model unchanged.
        ready_direct = any(
            self.IsSkillEquipped(sid) and self.CanCastSkillID(sid)
            for sid in (Unnatural_Signet_ID, Spiritual_Pain_ID)
        )
        cleanup_target = int(MWC.reserve_energy_surge_cleanup_target(
            tuple(sorted(int(v) for v in self._mesmerway_mind_wrack_owned if int(v or 0) > 0)),
            allow_hard_single_target=not ready_direct,
            hard_only=bool(cleanup_only),
        ) or 0)
        target = int(cleanup_target if cleanup_only else (cleanup_target or MWC.reserve_energy_surge_target() or 0))
        if not target:
            return False
        metrics = MWC.esurge_dispatch_metrics(int(target))
        did_cast = yield from self._cast_targeted(Energy_Surge_ID, target)
        if did_cast:
            MWC.register_energy_surge_cast(target)
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_ESURGE_DISTRIBUTED",
                    target_id=int(target),
                    domination=int(metrics.get("domination", 0) or 0),
                    drain_cap=f"{float(metrics.get('drain_cap', 0.0) or 0.0):.1f}",
                    estimated_max_energy=f"{MWC.estimate_enemy_max_energy(target):.1f}",
                    estimated_current_energy=f"{float(metrics.get('estimated_energy', 0.0) or 0.0):.1f}",
                    projected_energy_after_reservations=f"{float(metrics.get('projected_energy', 0.0) or 0.0):.1f}",
                    energy_before_own_reservation=f"{float(metrics.get('energy_before_own_reservation', 0.0) or 0.0):.1f}",
                    pending_esurges=int(metrics.get("pending_esurges", 0) or 0),
                    pending_before_this_cast=int(metrics.get("pending_before_this_cast", 0) or 0),
                    effective_drain=f"{float(metrics.get('effective_drain', 0.0) or 0.0):.1f}",
                    density=int(metrics.get("density", 1) or 1),
                    expected_total_damage=f"{float(metrics.get('expected_total_damage', 0.0) or 0.0):.1f}",
                    mind_wrack_active=bool(int(metrics.get("mind_wrack_active", 0) or 0)),
                    mind_wrack_zero_bonus=f"{float(metrics.get('mind_wrack_zero_bonus', 0.0) or 0.0):.1f}",
                    target_hp=f"{float(metrics.get('hp', 1.0) or 0.0):.3f}",
                    aneurysm_anchor=bool(int(metrics.get("aneurysm_anchor", 0) or 0)),
                    aneurysm_neighbor_drain=f"{float(metrics.get('aneurysm_neighbor_drain', 0.0) or 0.0):.1f}",
                    cleanup_cast=bool(cleanup_target > 0),
                    policy=(
                        "stage2_42_coordinated_packet_cleanup_esurge"
                        if cleanup_target > 0
                        else "stage1_49_primary_offense_aneurysm_anchor_plus_uncached_inflight_reservations"
                    ),
                )
                if cleanup_target > 0:
                    CombatDebug.log_event(
                        "MESMERWAY_ESURGE_CLEANUP_FINISHER",
                        target_id=int(target),
                        target_hp=f"{float(metrics.get('hp', 0.0) or 0.0):.3f}",
                        density=int(metrics.get("density", 1) or 1),
                        expected_total_damage=f"{float(metrics.get('expected_total_damage', 0.0) or 0.0):.1f}",
                        own_mind_wrack=bool(int(target) in self._mesmerway_mind_wrack_owned),
                        policy="stage2_42_elite_cleanup_only_when_packet_value_survives",
                    )
            except Exception:
                pass
        elif self._mesmerway_last_cast_evidence not in (
            "native_confirmation_unavailable", "completion_unconfirmed",
        ):
            MWC.release_energy_surge_reservation(
                int(target), reason="mesmerway_esurge_not_fired"
            )
            if cleanup_target > 0:
                MWC.release_cleanup_reservation(
                    int(target), reason="mesmerway_cleanup_esurge_not_fired"
                )
        return bool(did_cast)

    def _cast_famine(self, *, close_to_aggro: bool, in_aggro: bool) -> Generator[object, None, bool]:
        """Maintain the shared Famine nature spirit on the dedicated Me/R carrier."""
        if not self.IsSkillEquipped(Famine_ID) or not self.CanCastSkillID(Famine_ID):
            return False
        if not (bool(close_to_aggro) or bool(in_aggro)):
            return False
        if not MWC.famine_should_cast() or not MWC.reserve_famine_cast():
            return False
        try:
            cast_xy = tuple(Player.GetXY())
        except Exception:
            cast_xy = None
        did_cast = bool((yield from self.CastSkillID(
            skill_id=Famine_ID, target_agent_id=0, log=False, aftercast_delay=250,
        )))
        if not did_cast:
            MWC.release_famine_pending(str(self.GetLastCastRejectReason() or "famine_not_fired"))
            return False
        MWC.register_famine_cast(cast_xy=cast_xy)
        return True

    def _cast_mind_wrack(self) -> Generator[object, None, bool]:
        """Optional multi-copy Wrack setup for eSurge/Aneurysm battery cycles."""
        if not self.IsSkillEquipped(Mind_Wrack_ID) or not self.CanCastSkillID(Mind_Wrack_ID):
            return False
        target = MWC.reserve_mind_wrack_target()
        if not target:
            return False
        did_cast = yield from self._cast_targeted(Mind_Wrack_ID, target)
        if did_cast:
            self._mesmerway_mind_wrack_owned.add(int(target))
            MWC.register_mind_wrack_cast(int(target))
        return bool(did_cast)

    def _shatter_local_consumed_targets(self) -> tuple[int, ...]:
        """Return locally consumed feeder targets without any ShMem scan.

        The cache is deliberately tiny and time-bounded to the Fragility feeder
        lifetime. It exists only to stop the same eSurge client from selecting a
        target it successfully shattered a moment earlier.
        """
        try:
            now = int(MWC._now_ms())
        except Exception:
            now = 0
        if now <= 0:
            return ()
        expired = [
            int(target_id) for target_id, until_ms in self._mesmerway_shatter_local_consumed_until.items()
            if int(until_ms or 0) <= now
        ]
        for target_id in expired:
            self._mesmerway_shatter_local_consumed_until.pop(int(target_id), None)
        return tuple(sorted(
            int(target_id) for target_id, until_ms in self._mesmerway_shatter_local_consumed_until.items()
            if int(until_ms or 0) > now
        ))

    def _mark_shatter_local_consumed(self, target_id: int) -> None:
        aid = int(target_id or 0)
        if aid <= 0:
            return
        try:
            now = int(MWC._now_ms())
        except Exception:
            now = 0
        if now <= 0:
            return
        ttl = int(getattr(MWC, "FRAGILITY_FEEDER_CONSUMED_MS", 4750) or 4750)
        self._mesmerway_shatter_local_consumed_until[aid] = int(now + max(1000, ttl))

    def _cast_shatter_delusions(self, *, feeder_only: bool = False, end_of_life_only: bool = False) -> Generator[object, None, bool]:
        """Protected-hex-aware AoE damage conversion.

        Stage 1.37 treats the shared Fragility feeder lease as authoritative and
        logs the exact local blocker whenever a ready feeder opportunity is seen.
        """
        equipped = bool(self.IsSkillEquipped(Shatter_Delusions_ID))
        slot = int(self.GetEquippedSkillSlot(Shatter_Delusions_ID) or 0) if equipped else 0
        toggle_enabled = bool(self.IsSharedSkillToggleEnabled(slot)) if slot > 0 else False
        can_cast = bool(self.CanCastSkillID(Shatter_Delusions_ID)) if equipped else False
        feeder_targets = MWC.active_fragility_feeder_targets(require_spellcast_range=True) if feeder_only else ()
        if feeder_only and feeder_targets:
            try:
                now = int(MWC._now_ms())
            except Exception:
                now = 0
            if now <= 0 or now - int(self._mesmerway_shatter_diag_last_ms or 0) >= 3000:
                self._mesmerway_shatter_diag_last_ms = int(now)
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "MESMERWAY_SHATTER_LOCAL_STATE",
                        equipped=bool(equipped),
                        slot=int(slot),
                        toggle_enabled=bool(toggle_enabled),
                        can_cast=bool(can_cast),
                        feeder_targets=",".join(str(int(v)) for v in feeder_targets),
                        local_consumed=",".join(str(int(v)) for v in self._shatter_local_consumed_targets()),
                        local_cast_pending=bool(self._is_local_cast_pending()),
                        policy="stage1_40_live_blocker_diagnostic",
                    )
                except Exception:
                    pass
        if not equipped or not can_cast:
            return False
        local_consumed = self._shatter_local_consumed_targets()
        target = MWC.reserve_shatter_delusions_target(
            tuple(self._mesmerway_mind_wrack_owned),
            feeder_only=bool(feeder_only),
            end_of_life_only=bool(end_of_life_only),
            excluded_target_ids=tuple(local_consumed),
        )
        if not target:
            if feeder_only and feeder_targets:
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    summary = MWC.shatter_feeder_rejection_summary(tuple(local_consumed))
                    CombatDebug.log_event(
                        "MESMERWAY_SHATTER_FEEDER_NO_TARGET",
                        **{k: v for k, v in summary.items() if k != "target_ids"},
                        target_ids=",".join(str(int(v)) for v in summary.get("target_ids", ())),
                        policy="stage1_55_selection_rejection_breakdown_no_policy_relaxation",
                    )
                except Exception:
                    pass
            return False
        fragility_feeder = bool(MWC.is_fragility_shatter_feeder_target(int(target)))
        if end_of_life_only:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_SHATTER_END_OF_LIFE_SELECTED",
                    **MWC.shatter_end_of_life_snapshot(int(target)),
                    policy="stage1_59_single_visible_hex_residual_value_conversion",
                )
            except Exception:
                pass
        did_cast = yield from self._cast_targeted(Shatter_Delusions_ID, target, aftercast=160)
        if did_cast:
            # Stage 1.54: `did_cast` here means ACTION-queue acceptance, not native
            # skill completion. Do not consume the Fragility feeder yet; doing so
            # invalidates the same feeder before the queued dispatch guard runs.
            # MesmerwayCoordination consumes it only after native FINISHED.
            # Shattering Fragility/Overload does not remove our Mind Wrack.
            if (int(target) in self._mesmerway_mind_wrack_owned
                    and not MWC.is_mind_wrack_active(int(target))):
                self._mesmerway_mind_wrack_owned.discard(int(target))
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_SHATTER_DELUSIONS_QUEUED",
                    target_id=int(target),
                    fragility_feeder=bool(fragility_feeder),
                    feeder_only=bool(feeder_only),
                    end_of_life_only=bool(end_of_life_only),
                    policy="stage1_59_end_of_life_hex_conversion_queue_acceptance_not_completion",
                )
            except Exception:
                pass
        return bool(did_cast)

    def _cast_aneurysm(self) -> Generator[object, None, bool]:
        """Stage 1.63 coordinated Wrack -> Aneurysm -> eSurge battery reset.

        The first selector pass publishes a short shared setup transaction when
        one or two adjacent foes can be driven to zero Energy. Other eSurge
        Mesmers then plant Mind Wrack on those neighbours. The globally reserved Aneurysm carrier
        waits briefly for native/effect-confirmed Wrack, but Stage 2.12 treats
        that setup as opportunistic: stale/unconfirmed Wrack no longer cancels a
        valid Famine/Aneurysm pulse. The carrier fails open after a short bounded
        wait, refills the anchor, and lets eSurge consume it.
        """
        if not self.IsSkillEquipped(Aneurysm_ID) or not self.CanCastSkillID(Aneurysm_ID):
            if int(self._mesmerway_aneurysm_setup_target or 0) > 0:
                MWC.release_aneurysm_precast_setup(
                    int(self._mesmerway_aneurysm_setup_target), reason="aneurysm_unavailable"
                )
                self._mesmerway_aneurysm_setup_target = 0
                self._mesmerway_aneurysm_setup_started_ms = 0
            return False

        try:
            now = int(MWC._now_ms() or 0)
        except Exception:
            now = 0

        # Continue a previously published pre-Wrack transaction.
        pending_target = int(self._mesmerway_aneurysm_setup_target or 0)
        if pending_target > 0:
            started = int(self._mesmerway_aneurysm_setup_started_ms or now or 0)
            elapsed = max(0, int(now - started)) if now > 0 and started > 0 else 0
            status = MWC.aneurysm_precast_status(pending_target)
            desired = tuple(int(v) for v in tuple(status.get("desired_targets", ()) or ()))
            active = tuple(int(v) for v in tuple(status.get("active_targets", ()) or ()))
            setup_ready = bool(status.get("ready", False))
            timed_out = bool(elapsed >= int(MWC.ANEURYSM_PREWRACK_WAIT_MS))
            hard_expired = bool(elapsed >= int(MWC.ANEURYSM_PREWRACK_TRANSACTION_MAX_MS))

            # Stage 2.25 absolute transaction expiry. The controller can spend
            # time on higher-priority actions and revisit this method well after
            # the normal 700 ms opportunistic wait. Never let that stale local
            # setup survive for seconds: erase the shared/local setup first,
            # then either fail-open immediately on a still-valid battery or abort.
            if hard_expired:
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "MESMERWAY_ANEURYSM_PREWRACK_HARD_TIMEOUT",
                        target_id=int(pending_target), wait_ms=int(elapsed),
                        desired_targets=",".join(str(int(v)) for v in desired),
                        active_targets=",".join(str(int(v)) for v in active),
                        pending_targets=",".join(str(int(v)) for v in tuple(status.get("pending_targets", ()) or ())),
                        stale_targets=",".join(str(int(v)) for v in tuple(status.get("stale_targets", ()) or ())),
                        hard_limit_ms=int(MWC.ANEURYSM_PREWRACK_TRANSACTION_MAX_MS),
                        policy="stage2_25_absolute_prewrack_transaction_expiry",
                    )
                except Exception:
                    pass
                MWC.release_aneurysm_precast_setup(pending_target, reason="stage2_25_hard_timeout")
                self._mesmerway_aneurysm_setup_target = 0
                self._mesmerway_aneurysm_setup_started_ms = 0
                # Treat Wrack as absent after expiry. The direct battery validity
                # check below decides whether Aneurysm may still fail-open now.
                desired = ()
                active = ()
                setup_ready = False
                status = {
                    "desired_targets": (), "active_targets": (), "pending_targets": (),
                    "stale_targets": (), "ready": False,
                }

            fail_open = False
            if not desired:
                # Stage 2.12: if the optional Wrack targets went stale but the
                # anchor is still a real cluster battery, do not throw away the
                # Aneurysm/Famine opportunity. Revalidate the anchor and fire.
                current_metrics = MWC.aneurysm_target_metrics(pending_target)
                direct_ok = bool(
                    tuple(current_metrics.get("affected_neighbors", ()) or ())
                    and float(current_metrics.get("missing_energy", 0.0) or 0.0)
                        >= float(MWC.ANEURYSM_FAMINE_MIN_RECENT_DRAIN)
                )
                if not direct_ok:
                    try:
                        from Py4GWCoreLib.Builds.Skills import CombatDebug
                        CombatDebug.log_event(
                            "MESMERWAY_ANEURYSM_PREWRACK_ABORT",
                            target_id=int(pending_target), reason="stale_anchor_invalid",
                            active_targets=",".join(str(int(v)) for v in active),
                            pending_targets=",".join(str(int(v)) for v in tuple(status.get("pending_targets", ()) or ())),
                            stale_targets=",".join(str(int(v)) for v in tuple(status.get("stale_targets", ()) or ())),
                            wait_ms=int(elapsed),
                            policy="stage2_12_fail_open_only_if_anchor_still_valid",
                        )
                    except Exception:
                        pass
                    MWC.release_aneurysm_precast_setup(pending_target, reason="prewrack_stale_anchor_invalid")
                    self._mesmerway_aneurysm_setup_target = 0
                    self._mesmerway_aneurysm_setup_started_ms = 0
                    return False
                fail_open = True

            if desired and not setup_ready:
                if not timed_out and not hard_expired:
                    # Give cross-client Mind Wrack a short chance to land.
                    return True
                fail_open = True

            if fail_open:
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "MESMERWAY_ANEURYSM_PREWRACK_FAIL_OPEN",
                        target_id=int(pending_target),
                        desired_targets=",".join(str(int(v)) for v in desired),
                        active_targets=",".join(str(int(v)) for v in active),
                        pending_targets=",".join(str(int(v)) for v in tuple(status.get("pending_targets", ()) or ())),
                        stale_targets=",".join(str(int(v)) for v in tuple(status.get("stale_targets", ()) or ())),
                        wait_ms=int(elapsed),
                        policy="stage2_12_wrack_opportunistic_famine_pulse_authoritative",
                    )
                except Exception:
                    pass

            if not MWC.reserve_specific_aneurysm_target(pending_target):
                # Stage 2.24: once the 700 ms optional Wrack wait has expired,
                # "fail open" must actually release the bar. Stage 2.23 could
                # emit FAIL_OPEN and then keep returning True until the much
                # longer plan TTL, producing multi-second Aneurysm stalls.
                if not hard_expired and not timed_out and not fail_open and elapsed < int(MWC.ANEURYSM_PREWRACK_WAIT_MS):
                    return True
                MWC.release_aneurysm_precast_setup(
                    pending_target, reason="fail_open_reacquire_busy" if fail_open or timed_out else "reacquire_timeout"
                )
                self._mesmerway_aneurysm_setup_target = 0
                self._mesmerway_aneurysm_setup_started_ms = 0
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "MESMERWAY_ANEURYSM_PREWRACK_REACQUIRE_RELEASED",
                        target_id=int(pending_target), wait_ms=int(elapsed),
                        fail_open=bool(fail_open), timed_out=bool(timed_out),
                        policy="stage2_25_optional_prewrack_never_survives_absolute_transaction_cap",
                    )
                except Exception:
                    pass
                return False

            target = int(pending_target)
            metrics = MWC.aneurysm_target_metrics(target)
            expected_gain = float(metrics.get("missing_energy", 0.0) or 0.0)
            famine_state = MWC.famine_active_state()
            min_gain = float(
                MWC.ANEURYSM_FAMINE_MIN_RECENT_DRAIN
                if bool(famine_state.get("active", False) and famine_state.get("covered", False))
                else MWC.ANEURYSM_MIN_RECENT_DRAIN
            )
            # Stage 2.13: revalidate immediately before queueing. A second client
            # can change the shared energy model after this carrier reserved its
            # anchor. Live Stage 2.12 produced a 0-refill Aneurysm from exactly
            # that race. Do not spend the skill on a stale battery.
            if expected_gain + 0.01 < min_gain:
                MWC.release_aneurysm_reservation(target, reason="stale_refill_revalidation")
                MWC.release_aneurysm_precast_setup(target, reason="stale_refill_revalidation")
                self._mesmerway_aneurysm_setup_target = 0
                self._mesmerway_aneurysm_setup_started_ms = 0
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "MESMERWAY_ANEURYSM_STALE_ABORT",
                        target_id=int(target),
                        predicted_refill_energy=round(float(expected_gain), 1),
                        min_refill_gate=round(float(min_gain), 1),
                        prepared=True,
                        policy="stage2_13_prequeue_energy_revalidation",
                    )
                except Exception:
                    pass
                return False
            did_cast = yield from self._cast_targeted(
                Aneurysm_ID, target, aneurysm_expected_gain=expected_gain,
                aneurysm_affected_neighbors=tuple(
                    int(v) for v in metrics.get("affected_neighbors", ()) if int(v or 0) > 0
                ),
                aneurysm_prewrack_targets=tuple(active),
            )
            if did_cast:
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "MESMERWAY_ANEURYSM_PREWRACK_COMMIT",
                        target_id=int(target),
                        wrack_targets=",".join(str(int(v)) for v in active),
                        wrack_confirmed=len(active),
                        desired_count=len(desired),
                        fail_open=bool(fail_open),
                        wait_ms=int(elapsed),
                        predicted_refill_energy=round(float(expected_gain), 1),
                        policy="stage2_12_opportunistic_wrack_then_famine_aneurysm",
                    )
                except Exception:
                    pass
                MWC.release_aneurysm_precast_setup(target, reason="aneurysm_queued")
                self._mesmerway_aneurysm_setup_target = 0
                self._mesmerway_aneurysm_setup_started_ms = 0
            else:
                MWC.release_aneurysm_reservation(target, reason="prepared_aneurysm_not_queued")
                if timed_out:
                    MWC.release_aneurysm_precast_setup(target, reason="prepared_cast_failed")
                    self._mesmerway_aneurysm_setup_target = 0
                    self._mesmerway_aneurysm_setup_started_ms = 0
            return bool(did_cast) or bool(not timed_out)

        target = MWC.reserve_aneurysm_target()
        if not target:
            return False
        metrics = MWC.aneurysm_target_metrics(int(target))
        expected_gain = float(metrics.get("missing_energy", 0.0) or 0.0)
        famine_state = MWC.famine_active_state()
        min_gain = float(
            MWC.ANEURYSM_FAMINE_MIN_RECENT_DRAIN
            if bool(famine_state.get("active", False) and famine_state.get("covered", False))
            else MWC.ANEURYSM_MIN_RECENT_DRAIN
        )
        # Stage 2.13: reservation and queue are separate moments. If another
        # carrier already refilled/drained this packet, abandon and reselect
        # instead of queueing a stale/zero-value Aneurysm.
        if expected_gain + 0.01 < min_gain:
            MWC.release_aneurysm_reservation(int(target), reason="stale_refill_revalidation")
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_ANEURYSM_STALE_ABORT",
                    target_id=int(target),
                    predicted_refill_energy=round(float(expected_gain), 1),
                    min_refill_gate=round(float(min_gain), 1),
                    prepared=False,
                    policy="stage2_13_prequeue_energy_revalidation",
                )
            except Exception:
                pass
            return False

        plan = MWC.prepare_aneurysm_precast_setup(int(target))
        desired_targets = tuple(int(v) for v in tuple(plan.get("desired_targets", ()) or ()))
        if desired_targets:
            # Planning is not a cast. Release the short cast reservation while
            # preserving the separate pre-cast setup transaction.
            MWC.release_aneurysm_reservation(int(target), reason="prewrack_setup_wait")
            self._mesmerway_aneurysm_setup_target = int(target)
            self._mesmerway_aneurysm_setup_started_ms = int(now)
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_ANEURYSM_PREWRACK_WAIT",
                    target_id=int(target),
                    wrack_targets=",".join(str(int(v)) for v in desired_targets),
                    ready_wrack_holders=int(plan.get("ready_wrack_holders", 0) or 0),
                    wait_budget_ms=int(MWC.ANEURYSM_PREWRACK_WAIT_MS),
                    policy="stage2_12_short_opportunistic_prewrack_before_famine_pulse",
                )
            except Exception:
                pass
            return True

        did_cast = yield from self._cast_targeted(
            Aneurysm_ID, target, aneurysm_expected_gain=expected_gain,
            aneurysm_affected_neighbors=tuple(int(v) for v in metrics.get("affected_neighbors", ()) if int(v or 0) > 0),
        )
        if did_cast:
            # Queue admission is not an Energy refill. Keep the prediction local
            # to the receipt; MesmerwayCoordination promotes it to shared anchor
            # and neighbour-drain facts only after native FINISHED.
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_ANEURYSM_QUEUED", target_id=int(target),
                    predicted_refill_energy=round(float(expected_gain), 1),
                    authoritative=False,
                    policy="stage1_63_direct_no_prewrack_candidate_prediction_only_until_native_finish",
                )
            except Exception:
                pass
        return bool(did_cast)

    def _cast_mistrust(self) -> Generator[object, None, bool]:
        if not self.IsSkillEquipped(Mistrust_ID) or not self.CanCastSkillID(Mistrust_ID):
            return False
        target = MWC.reserve_mistrust_target()
        if not target:
            return False
        did_cast = yield from self._cast_targeted(Mistrust_ID, target)
        if did_cast:
            MWC.register_mistrust_cast(target)
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_MISTRUST_ASSIGNED",
                    target_id=int(target),
                    policy="spread_casters_exact_effect_release_before_interrupt_reuse",
                )
            except Exception:
                pass
        return bool(did_cast)

    def _cast_web_interrupt(self, *, emergency_only: bool = True) -> Generator[object, None, bool]:
        """Use Web as a real direct interrupt; hex application is still confirmed separately."""
        if not self.IsSkillEquipped(Web_of_Disruption_ID) or not self.CanCastSkillID(Web_of_Disruption_ID):
            return False
        target = int(MWC.reserve_single_interrupt_target(Web_of_Disruption_ID, emergency_only=bool(emergency_only)) or 0)
        if target <= 0:
            return False
        snapshot = MWC.interrupt_dispatch_snapshot(target, Web_of_Disruption_ID)
        did = yield from self._cast_targeted(
            Web_of_Disruption_ID, target, aftercast=120, interrupt_claim=True, urgent=True
        )
        if not did:
            MWC.release_single_interrupt_reservation(target, Web_of_Disruption_ID, reason="web_interrupt_not_fired")
            MWC.release_optional_setup_reservation(Web_of_Disruption_ID, target)
        else:
            MWC.release_optional_setup_reservation(Web_of_Disruption_ID, target)
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_WEB_CAST", target_id=target, mode="urgent_interrupt",
                    enemy_skill_id=int(snapshot.get("enemy_skill_id", 0) or 0),
                    priority=int(snapshot.get("priority", 0) or 0),
                    hex_confirmed=True, first_interrupt_success_assumed=False,
                    policy="stage1_45_interrupt_attempt_plus_confirmed_hex_no_success_inference",
                )
            except Exception:
                pass
        return bool(did)

    def _cast_web_setup(self) -> Generator[object, None, bool]:
        """Late Web->Shatter packet setup; no wait for Web's second interrupt."""
        if not self.IsSkillEquipped(Web_of_Disruption_ID) or not self.CanCastSkillID(Web_of_Disruption_ID):
            return False
        target = int(MWC.reserve_web_setup_target() or 0)
        if target <= 0:
            return False
        packet_damage, density = MWC.expected_shatter_packet_damage(target)
        did = yield from self._cast_targeted(Web_of_Disruption_ID, target, aftercast=120)
        MWC.release_optional_setup_reservation(Web_of_Disruption_ID, target)
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_WEB_CAST", target_id=target, mode="shatter_setup",
                density=int(density), expected_shatter_packet_damage=round(float(packet_damage), 1),
                hex_confirmed=bool(did), first_interrupt_success_assumed=False,
                abort_reason="" if did else str(self._mesmerway_last_cast_evidence),
                selection_reason="dense_clean_target_with_ready_shatter",
                policy="stage1_45_late_damage_setup_no_second_interrupt_wait",
            )
        except Exception:
            pass
        return bool(did)

    def _cast_phantom_pain(self) -> Generator[object, None, bool]:
        """Late durable-target setup; never treats <20% as a guaranteed execute."""
        if not self.IsSkillEquipped(Phantom_Pain_ID) or not self.CanCastSkillID(Phantom_Pain_ID):
            return False
        target = int(MWC.reserve_phantom_pain_target() or 0)
        if target <= 0:
            return False
        try:
            hp = max(0.0, float(Agent.GetHealth(target) or 0.0))
        except Exception:
            hp = -1.0
        did = yield from self._cast_targeted(Phantom_Pain_ID, target, aftercast=180)
        MWC.release_optional_setup_reservation(Phantom_Pain_ID, target)
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_PHANTOM_PAIN_QUEUED" if did else "MESMERWAY_PHANTOM_PAIN_REJECTED",
                target_id=target, target_hp_ratio=round(float(hp), 3),
                queue_admitted=bool(did), evidence=str(self._mesmerway_last_cast_evidence or ""),
                selection_reason="durable_or_important_clean_target_with_ready_shatter",
                policy="stage1_57_queue_admission_is_not_hex_confirmation",
            )
        except Exception:
            pass
        return bool(did)


    def _cast_fragility(self) -> Generator[object, None, bool]:
        """Optional eSurge-side Fragility carrier with full shared feeder publication."""
        if not self.IsSkillEquipped(Fragility_ID) or not self.CanCastSkillID(Fragility_ID):
            return False
        # Consume already-prepared feeder value before paying for another setup.
        if MWC.active_fragility_feeder_targets(require_spellcast_range=False):
            return False
        target = int(MWC.reserve_fragility_target() or 0)
        if target <= 0:
            return False
        did = yield from self._cast_targeted(Fragility_ID, target, aftercast=120)
        if not did:
            MWC.release_fragility_reservation(target, reason=str(self._mesmerway_last_cast_evidence or "not_queued"))
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_ESURGE_FRAGILITY_QUEUED" if did else "MESMERWAY_ESURGE_FRAGILITY_REJECTED",
                target_id=int(target), evidence=str(self._mesmerway_last_cast_evidence or ""),
                policy="stage1_57_esurge_full_fragility_feeder_async_confirmation",
            )
        except Exception:
            pass
        return bool(did)

    def _cast_epidemic(self, *, phantom_only: bool = False) -> Generator[object, None, bool]:
        """Spread useful conditions; optionally restrict to Phantom Deep Wound.

        The early Stage 1.61 pass is intentionally Phantom-only because that
        source is transient and often dies during cleanup. Ordinary Epidemic
        remains at its established late-offense priority.
        """
        if not self.IsSkillEquipped(Epidemic_ID):
            return False
        if not self.CanCastSkillID(Epidemic_ID):
            if not phantom_only:
                self._log_epidemic_no_plan(
                    "preflight_not_castable",
                    queue_reject_reason=str(self.GetLastCastRejectReason() or "unknown_preflight_rejection"),
                )
            return False
        plan = MWC.reserve_epidemic_target(phantom_only=bool(phantom_only))
        target = int(plan.get("source_id", 0) or 0)
        if target <= 0:
            if not phantom_only:
                diag = dict(MWC.diagnose_epidemic_target_selection() or {})
                self._log_epidemic_no_plan(str(diag.pop("reason", "no_selector_plan")), **diag)
            return False
        if not MWC.epidemic_plan_still_valid(plan):
            MWC.release_epidemic_reservation(plan, reason="stale_before_dispatch")
            self._log_epidemic_no_plan("stale_before_dispatch", source_id=int(target))
            return False
        did = yield from self._cast_targeted(Epidemic_ID, target, aftercast=100, epidemic_plan=plan)
        if not did:
            MWC.release_epidemic_reservation(plan, reason=str(self._mesmerway_last_cast_evidence or "cast_unconfirmed"))
            self._log_epidemic_no_plan(
                "dispatch_rejected_or_unconfirmed",
                source_id=int(target),
                evidence=str(self._mesmerway_last_cast_evidence or ""),
                queue_reject_reason=str(self.GetLastCastRejectReason() or ""),
            )
            return False
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_EPIDEMIC_QUEUED",
                source_id=int(target),
                expected_recipients=int(plan.get("relevant_recipient_count", 0) or 0),
                expected_score=round(float(plan.get("expected_score", 0.0) or 0.0), 2),
                source_hp=round(float(plan.get("source_hp", 0.0) or 0.0), 3),
                phantom_only=bool(phantom_only),
                policy="stage1_61_phantom_early_or_normal_epidemic_dispatch",
            )
        except Exception:
            pass
        # Queue admission only. Native FINISHED reconciliation owns recipient
        # observation and served-lock publication.
        return True

    def _cast_optional_combo_shatter(self) -> Generator[object, None, bool]:
        """Queue a safe Web/Phantom conversion; native FINISHED owns end-effect proof."""
        if not self.IsSkillEquipped(Shatter_Delusions_ID) or not self.CanCastSkillID(Shatter_Delusions_ID):
            return False
        target, feeder_sid = MWC.reserve_optional_combo_shatter_target()
        target, feeder_sid = int(target or 0), int(feeder_sid or 0)
        if target <= 0 or feeder_sid <= 0:
            return False
        did = yield from self._cast_targeted(Shatter_Delusions_ID, target, aftercast=120)
        if not did:
            return False
        # Queue admission is not cast completion. CastReceipt captured the feeder
        # identity plus any pre-cast Deep Wound/Web state. Only native FINISHED
        # may evaluate end effects and invalidate that feeder.
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_OPTIONAL_COMBO_SHATTER_QUEUED",
                target_id=target, feeder_skill_id=feeder_sid,
                policy="stage1_58_queue_only_native_finish_owns_end_effect",
            )
        except Exception:
            pass
        return True

    def _cast_signet_of_corruption(self) -> Generator[object, None, bool]:
        """Optional Kurzick/Luxon packet filler after the core Mesmerway actions.

        Require at least two Nearby enemies already carrying a hex/condition so
        the 1 s signet activation buys both real AoE pressure and energy return.
        It never preempts eSurge, Aneurysm/Wrack, Shatter, Cry, or Mistrust.
        """
        skill_id = 0
        for sid in (int(Signet_of_Corruption_Kurzick_ID or 0), int(Signet_of_Corruption_Luxon_ID or 0)):
            if sid > 0 and self.IsSkillEquipped(sid) and self.CanCastSkillID(sid):
                skill_id = int(sid)
                break
        if skill_id <= 0:
            return False
        target = int(MWC.reserve_signet_of_corruption_target(min_qualified=2) or 0)
        if target <= 0:
            return False
        metrics = MWC.signet_of_corruption_target_metrics(int(target))
        did_cast = yield from self._cast_targeted(int(skill_id), int(target), aftercast=180)
        if did_cast:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_SIGNET_OF_CORRUPTION",
                    target_id=int(target),
                    affected=int(metrics.get("affected", 0) or 0),
                    qualified=int(metrics.get("qualified", 0) or 0),
                    policy="stage1_41_late_nearby_hex_condition_packet_filler",
                )
            except Exception:
                pass
        return bool(did_cast)

    def _cast_evas(self, *, min_energy_pct: float = 0.45) -> Generator[object, None, bool]:
        if not self.IsSkillEquipped(Ebon_Vanguard_Assassin_Support_ID):
            return False
        try:
            current = float(Agent.GetEnergy(Player.GetAgentID()) or 0.0)
        except Exception:
            current = 1.0
        if current < float(min_energy_pct):
            return False
        target = MWC.reserve_evas_target()
        if not target:
            return False
        return bool((yield from self._cast_targeted(Ebon_Vanguard_Assassin_Support_ID, target)))

    def _cast_unnatural(self) -> Generator[object, None, bool]:
        if not self.IsSkillEquipped(Unnatural_Signet_ID) or not self.CanCastSkillID(Unnatural_Signet_ID):
            return False
        owned_wrack = tuple(sorted(int(v) for v in self._mesmerway_mind_wrack_owned if int(v or 0) > 0))
        target = int(MWC.reserve_unnatural_target(owned_wrack) or 0)
        if target <= 0:
            return False
        own_wrack = bool(target in self._mesmerway_mind_wrack_owned and MWC.is_mind_wrack_active(target))
        try:
            density = int(MWC.esurge_target_metrics(target).get("density", 1) or 1)
        except Exception:
            density = 1
        did_cast = bool((yield from self._cast_targeted(Unnatural_Signet_ID, target)))
        if did_cast:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_UNNATURAL_DAMAGE", target_id=int(target),
                    own_mind_wrack=bool(own_wrack), density=int(density),
                    prepared=bool(Agent.IsHexed(target) or Agent.IsEnchanted(target)),
                    policy="stage2_42_owned_wrack_then_prepared_dense_packet_filler",
                )
            except Exception:
                pass
        return bool(did_cast)

    def _cast_spiritual_pain(self) -> Generator[object, None, bool]:
        if not self.IsSkillEquipped(Spiritual_Pain_ID) or not self.CanCastSkillID(Spiritual_Pain_ID):
            return False
        owned_wrack = tuple(sorted(int(v) for v in self._mesmerway_mind_wrack_owned if int(v or 0) > 0))
        target = int(MWC.reserve_spiritual_pain_target(owned_wrack) or 0)
        if target <= 0:
            return False
        own_wrack = bool(target in self._mesmerway_mind_wrack_owned and MWC.is_mind_wrack_active(target))
        did_cast = bool((yield from self._cast_targeted(Spiritual_Pain_ID, target)))
        if did_cast:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_SPIRITUAL_PAIN_DAMAGE", target_id=int(target),
                    own_mind_wrack=bool(own_wrack),
                    policy="stage2_42_owned_wrack_preferred_then_normal_damage_filler",
                )
            except Exception:
                pass
        return bool(did_cast)

    def _run_local_skill_logic(self) -> Generator[object, None, bool | None]:
        # Stage 1.47: reconcile previously queued casts only on later ticks.
        # Never wait inside the same controller step for the ACTION queue that
        # this step itself just populated.
        MWC.observe_pending_cast_receipts()
        if not Routines.Checks.Skills.CanCast():
            yield from Routines.Yield.wait(100)
            return False

        snapshot = self._snapshot()
        self._log_optional_loadout_once()
        MWC.observe_wisdom_activity()

        # Adaptive F1/F2 weapon policy: F1=40/40, F2=SR5 staff. Low->SR5 is
        # combat-only at <=35%; return to 40/40 at >=75% with hard hysteresis.
        if (yield from self._mesmerway_weapon_sets.maybe_switch(
            snapshot.energy_pct, in_combat=snapshot.in_aggro
        )):
            return True

        # PvE utility is supported but must not delay a live elite/interrupt.
        # AoS therefore gets its cleanest window while approaching the packet.
        if not snapshot.in_aggro:
            if (yield from self._cast_mantra_of_frost(min_energy_pct=0.30)):
                return True
            if (yield from self._cast_mending_refrain_upkeep()):
                return True
            if snapshot.close_to_aggro and (yield from self._cast_famine(
                close_to_aggro=True, in_aggro=False
            )):
                return True
            if snapshot.close_to_aggro and (yield from self._cast_air_of_superiority()):
                return True
            return False

        # 1) Emergency ally hex removal above offense.
        if (yield from self.skills.Mesmer.DominationMagic.Shatter_Hex(
            min_priority=HexRemovalPriority.HIGH
        )):
            return True

        # 2) Emergency Complicate pass before elite damage.  Generic Complicate
        # spam is deliberately deferred until after eSurge.
        if (yield from self._cast_complicate(emergency_only=True)):
            return True

        # 3) Safety override. Cry is normally an offensive damage interrupt *after*
        # eSurge, but a truly dangerous cast may consume it immediately.
        if (yield from self._cast_cry(mode="emergency")):
            return True
        if (yield from self._cast_power_spike(emergency_only=True)):
            return True
        # Web is an additional defensive fallback.  It uses the same exact-cast
        # reservation and timing gates but success of the first interrupt is not
        # inferred merely because the hex landed.
        if (yield from self._cast_web_interrupt(emergency_only=True)):
            return True

        # Stage 2.38: shared three-carrier Aegis prevention lane.  It is strictly
        # combat-only and sits below true emergency interrupts but above ordinary
        # damage/recovery so a due layer is not starved by the eSurge pipeline.
        if (yield from self._cast_aegis_rotation(in_combat=True)):
            return True

        # 4) Power Drain remains the low-energy safety exception. It may preempt
        # elite damage only when the Mesmer is at/below the established 55%
        # recovery threshold.
        if snapshot.energy_pct <= 0.55 and (yield from self._cast_power_drain(max_energy_pct=0.55)):
            return True

        # Stage 2.59-PD experiment: an active healer/support/danger cast may
        # deserve a 1/4-second interrupt before ordinary eSurge damage. Do not
        # do this on generic casts or at near-full energy; do not bypass Cry,
        # Power Spike or the existing exact-cast ownership locks above.
        if (snapshot.energy_pct > 0.55 and
                (yield from self._cast_power_drain(
                    max_energy_pct=1.0, tactical_only=True,
                    critical_only=bool(snapshot.energy_pct > 0.85),
                ))):
            return True

        # Stage 2.23: TeamRecovery may steer movement/regroup/support, but it must
        # not hard-stop the Mesmer damage lane.  The Stage 2.18 early return here
        # created a feedback loop under pressure: only the emergency interrupt
        # passes above this point could fire, while eSurge/Famine/Mind Wrack/
        # Aneurysm/cleanup and normal damage were suppressed.  Restore the
        # Stage-1.73 action economy by allowing the offensive pipeline below to
        # continue; TeamRecovery remains authoritative for movement and support.
        # Stage 2.11: repair Famine before normal battery offense if the carrier
        # missed the pre-pull window. Emergency interrupts above still win.
        if (yield from self._cast_famine(close_to_aggro=False, in_aggro=True)):
            return True

        # Stage 2.57: finish an already-engaged <=5% foe with a fast direct
        # hit before spending time on battery preparation or the cluster elite.
        # Emergency interrupts and immediate defensive actions already won above.
        if (yield from self._cast_cleanup_finisher(hard_only=True)):
            return True
        # If direct finishers are absent, a damage-positive eSurge can dispatch
        # now on the dying target rather than waiting for an Aneurysm/Wrack setup.
        if (yield from self._cast_energy_surge(cleanup_only=True)):
            return True

        # 5) Direct eSurge battery preparation is the only normal offensive work
        # allowed ahead of Energy Surge. Mind Wrack may briefly defer the elite
        # only when the shared coordinator has selected a real zero-energy setup.
        if (yield from self._cast_mind_wrack()):
            return True

        # 6) Aneurysm is an eSurge enabler, not a competing filler: refill an
        # exhausted central battery before asking eSurge to consume it.
        if (yield from self._cast_aneurysm()):
            return True

        # 7) PRIMARY OFFENSIVE ACTION (Stage 1.49). If Energy Surge is ready and
        # the coordinator has a worthwhile target, no cleanup, Wisdom, Shatter
        # Enchantment, Fragility conversion, normal interrupt, setup or filler
        # is allowed to consume this controller tick first.
        if (yield from self._cast_energy_surge()):
            return True

        # Stage 2.02: refresh Frost only after the primary elite had first refusal.
        # Stance upkeep is instant but still costs Energy, so low-energy Mesmers
        # keep their existing Power Drain/recovery path instead of forcing it.
        if (yield from self._cast_mantra_of_frost(min_energy_pct=0.40)):
            return True

        # 8) Fast cleanup follows the elite. Stage 2.42 lets eSurge itself consume
        # a <=10% target only when that dying primary still provides real packet
        # value or this account can cash its own Mind Wrack; ordinary single-target
        # execute work remains here for Cry/Unnatural/Spiritual/Overload.
        if (yield from self._cast_cleanup_finisher()):
            return True

        # 9) Enchantment removal after the elite/cleanup lane. Drain gets first
        # refusal only when this account actually needs Energy; otherwise the
        # Domination damage strip is preferred. Both share the same cross-account
        # target reservation so Shatter + Drain do not waste themselves on one
        # enchantment in the same short window.
        if (yield from self._cast_drain_enchantment(recovery_only=True)):
            return True
        if (yield from self._cast_shatter_enchantment()):
            return True
        if (yield from self._cast_drain_enchantment(recovery_only=False)):
            return True
        if (yield from self._cast_wisdom_post_contact()):
            return True

        # 10) Fragility feeder conversion remains fast, but it no longer outranks
        # a ready/value-positive Energy Surge. This prevents the feeder package
        # from reducing the core four-eSurge battery cycle.
        if (yield from self._cast_shatter_delusions(feeder_only=True)):
            return True

        # 10b) Stage 1.62+: a completed Web/Phantom setup is already-paid burst.
        # Stage 1.63 deliberately removes Epidemic from active dispatch; optional
        # Shatter remains valuable on its own and must still beat lower filler.
        if (yield from self._cast_optional_combo_shatter()):
            return True

        # 10c) A sole Mesmer hex whose own payoff is no longer likely must not
        # simply expire while the controller spends ticks on lower-priority
        # filler.  This pass is time-sensitive but still stays behind eSurge,
        # cleanup and prepared Fragility conversion.  Trigger-ready Mistrust,
        # Frustration, attack punishers and near-zero Mind Wrack remain protected.
        if (yield from self._cast_shatter_delusions(end_of_life_only=True)):
            return True

        # 11) Overload is a setup hit on a long, non-emergency activation. The
        # coordinator grants only a ~360ms no-interrupt window so the conditional
        # damage can land; serious casts ignore that window entirely.
        if (yield from self._cast_overload()):
            return True

        # 12) Offensive Cry: every feasible activation is a damage opportunity,
        # including a lone target. Frustration targets are positively focused.
        if (yield from self._cast_cry(mode="offensive")):
            return True

        # 13) Power Spike is also spammed on every feasible spell cast after
        # eSurge, with the same Frustration focus and central no-interrupt guards.
        if (yield from self._cast_power_spike()):
            return True

        # 14) Cry of Pain keeps the Stage 1.43 packet-damage niche before
        # Complicate, restricted by the coordinator to a Mesmer-hexed 2+ packet.
        if (yield from self._cast_cry_of_pain()):
            return True

        # 15) Complicate remains the value-disable interrupt: heals/protection/
        # danger and repeated known skills, rather than generic no-damage spam.
        if (yield from self._cast_complicate(emergency_only=False)):
            return True

        # 17) Late generic Shatter gets the chance to convert any already-safe
        # expendable hex before paying for a new Web/Phantom setup.
        if (yield from self._cast_shatter_delusions()):
            return True

        # 18) New setup is allowed only after eSurge, interrupts and already-prepared
        # Shatter value declined.  This lets one eSurge carry Fragility without
        # turning it into mandatory preparation for every packet.
        if (yield from self._cast_fragility()):
            return True

        # 19) Web/Phantom remain optional late setups.
        if (yield from self._cast_web_setup()):
            return True
        if (yield from self._cast_phantom_pain()):
            return True

        # 20) Optional Technobabble and Mistrust setup. Direct interrupts now
        # protect only the concrete ally-targeting spell activation Mistrust can
        # actually consume; heals/self casts remain eligible for protection.
        if (yield from self._cast_technobabble()):
            return True
        if (yield from self._cast_mistrust()):
            return True

        # 21) Optional Signet of Corruption: late packet filler only after the
        # battery/interrupt/hex core has declined its action for this tick.
        if (yield from self._cast_signet_of_corruption()):
            return True

        # 22) Medium-priority ally hex removal in normal downtime.
        if snapshot.energy_pct >= 0.40 and (yield from self.skills.Mesmer.DominationMagic.Shatter_Hex(
            min_priority=HexRemovalPriority.MEDIUM
        )):
            return True

        # 23) Optional PvE pressure and coordinated damage fillers.
        if (yield from self._cast_evas(min_energy_pct=0.45)):
            return True
        if (yield from self._cast_unnatural()):
            return True
        if (yield from self._cast_spiritual_pain()):
            return True

        # 24) AoS / low-priority recovery and utility.
        if (yield from self._cast_air_of_superiority()):
            return True
        if snapshot.energy_pct <= 0.75 and (yield from self._cast_power_drain(max_energy_pct=0.75)):
            return True
        if snapshot.energy_pct >= 0.65 and (yield from self.skills.Mesmer.DominationMagic.Shatter_Hex(
            min_priority=HexRemovalPriority.LOW
        )):
            return True

        yield
