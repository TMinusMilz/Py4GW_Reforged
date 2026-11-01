# EXP36 Stage 12 / E19 phase 2; legacy regression marker: exp36_stage5_e19_observation_only
from __future__ import annotations

import time

from Py4GWCoreLib import AgentArray, GLOBAL_CACHE, BuildMgr, Party, Profession, Range, Routines
from Py4GWCoreLib.Agent import Agent
from Py4GWCoreLib.Builds.Skills import SkillsTemplate
from Py4GWCoreLib.Builds.Skills.AoEDangerPrediction import (
    avoid_active_aoe_if_needed,
    refresh_aoe_danger_zones,
)
from Py4GWCoreLib.Builds.Skills.BurningComboCoordination import (
    active_bed_of_coals_members, active_bed_of_coals_targets,
    active_spirit_burn_carriers, active_spirit_burn_targets,
    publish_pi_knockdown_packet,
)
from Py4GWCoreLib.Builds.Skills.BindingChainsCoordination import (
    packet_has_binding_chains,
    register_binding_chains_fired,
    release_binding_chains_reservation,
    reserve_binding_chains,
)
from Py4GWCoreLib.Builds.Skills.DangerInterruptClaim import (
    claim_best_dangerous_cast,
    get_casting_skill_id,
    get_enemy_cast_remaining_ms,
    is_dangerous_cast,
    opportunistic_interrupt_is_feasible,
    post_interrupt_claim,
    rank_opportunistic_interrupt_options,
    release_interrupt_claim,
    target_still_casting_skill,
)
from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import (
    get_team_cluster_anchor,
    get_team_cluster_members,
    is_support_or_caster,
)
from Py4GWCoreLib.Builds.Skills.PIMesmerCoordination import (
    pi_packet_reserved,
    release_complicate_skill,
    release_cry_packet,
    release_pi_packet,
    reserve_complicate_skill,
    reserve_cry_packet,
    reserve_pi_packet,
)
from Py4GWCoreLib.HeroAI.targeting import GetAllAlliesArray
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.Py4GWcorelib import Utils
from Py4GWCoreLib.Skill import Skill

Psychic_Instability_ID = Skill.GetID("Psychic_Instability")
Cry_of_Frustration_ID = Skill.GetID("Cry_of_Frustration")
Power_Drain_ID = Skill.GetID("Power_Drain")
Energy_Tap_ID = Skill.GetID("Energy_Tap")
Splinter_Weapon_ID = Skill.GetID("Splinter_Weapon")
Binding_Chains_ID = Skill.GetID("Binding_Chains")
Air_of_Superiority_ID = Skill.GetID("Air_of_Superiority")
Technobabble_ID = Skill.GetID("Technobabble")
Epidemic_ID = Skill.GetID("Epidemic")
Signet_of_Disruption_ID = Skill.GetID("Signet_of_Disruption")
Unnatural_Signet_ID = Skill.GetID("Unnatural_Signet")
Fragility_ID = Skill.GetID("Fragility")
Burning_ID = Skill.GetID("Burning")
Complicate_ID = Skill.GetID("Complicate")
Arcane_Conundrum_ID = Skill.GetID("Arcane_Conundrum")
Spirit_Light_ID = Skill.GetID("Spirit_Light")
Mend_Body_and_Soul_ID = Skill.GetID("Mend_Body_and_Soul")
Ghostmirror_Light_ID = Skill.GetID("Ghostmirror_Light")
Life_ID = Skill.GetID("Life")

Heroic_Refrain_ID = Skill.GetID("Heroic_Refrain")
Soul_Twisting_ID = Skill.GetID("Soul_Twisting")

POWER_DRAIN_NORMAL_THRESHOLD = 1.01  # EXP35: Power Drain is control at any normal energy, not only recovery
POWER_DRAIN_CRITICAL_THRESHOLD = 0.30
TECHNOBABBLE_MIN_HEALTH = 0.10
PI_TECHNOBABBLE_DELAY_MS = 650.0
PI_CAST_CONFIRM_TIMEOUT_MS = 1400.0
PI_ACTIVATION_START_GRACE_MS = 320.0
PI_CAST_CONFIRM_POLL_MS = 40
# v33 final: the ordinary PI opener is opportunistic control, not an emergency.
# Only commit it when the observed enemy cast still has a generous live window;
# dangerous casts have already had first refusal through the ready-now interrupt pool.
PI_OPENER_MIN_REMAINING_MS = 120  # EXP13: PI is spam control on balled packs; only require enough cast time to dispatch
PI_FRAGILITY_MIN_ENERGY_FRACTION = 0.25  # Stage 20: Fragility filler never spends from the low-energy floor


class Psychic_Instability_Wastrels(BuildMgr):
    """PI shutdown Mesmer for the shared RoJway target packet."""

    def __init__(self, match_only: bool = False):
        super().__init__(
            name="PI Support - Heal / PI / Control / Hex",
            required_primary=Profession.Mesmer,
            template_code="OQhkAoC0wFyTIMDQ6a2Tucg00JA",
            # Keystone Poison PI support does NOT equip Cry of Frustration.
            # The PI elite is the unique role signature.  Other PI/Rt support
            # skills may change without falling back to the generic HeroAI.
            required_skills=[
                Psychic_Instability_ID,
            ],
            optional_skills=[
                Cry_of_Frustration_ID,
                Complicate_ID,
                Arcane_Conundrum_ID,
                Signet_of_Disruption_ID,
                Unnatural_Signet_ID,
                Fragility_ID,
                Epidemic_ID,
                Ghostmirror_Light_ID,
                Spirit_Light_ID,
                Life_ID,
                Mend_Body_and_Soul_ID,
                Power_Drain_ID,
                Energy_Tap_ID,
            ],
        )
        if match_only:
            return

        # This controller owns all supported slots so generic HeroAI cannot
        # peel an interrupt or offensive support cast away from team focus.
        self.SetSkillCastingFn(self._run_local_skill_logic)
        self.skills: SkillsTemplate = SkillsTemplate(self)
        self._last_pi_cast_ms: float = 0.0
        self._last_pi_anchor: int = 0
        # v23: PI confirmation must survive across BuildMgr ticks. A blocking
        # yield-loop here is not reliable because the local skill callback may
        # be recreated after the cast request. Keep the pending activation as
        # explicit state and poll it at the start of every combat tick.
        self._pending_pi_confirmation: dict[str, object] | None = None
        # v34 experimental: tiny local HP history for spike-aware healing.
        self._heal_hp_samples: dict[int, tuple[float, float]] = {}
        # EXP26 clean rebuild state. Previous PI-account targeting/rotation
        # experiments are intentionally not consulted by the fresh controller.
        self._pi_support_recent_heal_until: dict[int, float] = {}
        self._pi_support_last_heal_ms: float = 0.0
        # EXP27 telemetry-only: prove that the clean PI Support controller is
        # actually selected on the player account.  This does not influence
        # targeting, healing, interrupts or filler decisions.
        self._pi_support_controller_active_logged: bool = False
        self._pi_support_next_heartbeat_ms: float = 0.0
        # EXP33 diagnostics: observe actual player control casts independently
        # of whichever dispatcher initiated them. This is telemetry only.
        self._pi_support_observed_casting_skill_id: int = 0
        self._pi_support_control_recharge_prev: dict[int, int] = {}
        # EXP36 Stage 8 / E07c: a final PI pre-dispatch claim must survive a
        # BuildMgr callback restart until the queued cast either activates or is
        # explicitly rejected.  Stage 7 left one live Urgoz edge case where the
        # callback restarted after dispatch acknowledgement and the next pass
        # treated our own still-live NON_REENTRANT claim as foreign.
        self._pi_pre_dispatch_claim: dict[str, object] | None = None
        # EXP36 Stage 19: confirmed PI opens a short *opportunity*, not a lock,
        # for Fragility on the same packet. Healing and any live interrupt always
        # remain first refusal.
        self._pis_fragility_followup_until_ms: float = 0.0
        self._pis_fragility_followup_anchor: int = 0
        self._pis_fragility_followup_members: tuple[int, ...] = ()
        # Short predicted coverage while server effect propagation catches up.
        self._pis_fragility_recent_coverage_until_ms: float = 0.0
        self._pis_fragility_recent_coverage_ids: tuple[int, ...] = ()
        # EXP36 Stage 20.7: the formal Guild Wars party is always the primary
        # heal lane. Mission/summoned allied NPC discovery is cached locally so
        # we never reintroduce Stage-20's per-tick SharedMemory regression.
        self._pis_project_roster_cache_until_ms: float = 0.0
        self._pis_project_roster_cache_ids: set[int] = set()
        self._pis_external_ally_cache_until_ms: float = 0.0
        self._pis_external_ally_cache_ids: set[int] = set()
        self._pis_external_ally_diag_until_ms: float = 0.0

    @staticmethod
    def _now_ms() -> float:
        return time.monotonic() * 1000.0

    @staticmethod
    def _log_rotation_event(event: str, **fields: object) -> None:
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug

            CombatDebug.log_event(str(event), **fields)
        except Exception:
            pass

    def _remember_pi_pre_dispatch_claim(self, target_id: int, casting_skill_id: int) -> None:
        self._pi_pre_dispatch_claim = {
            "target_id": int(target_id or 0),
            "casting_skill_id": int(casting_skill_id or 0),
            "started_ms": float(self._now_ms()),
        }

    def _clear_pi_pre_dispatch_claim_local(self) -> None:
        self._pi_pre_dispatch_claim = None

    def _pi_pre_dispatch_claim_matches(self, target_id: int, casting_skill_id: int) -> bool:
        pending = self._pi_pre_dispatch_claim
        if not pending:
            return False
        return bool(
            int(pending.get("target_id", 0) or 0) == int(target_id or 0)
            and int(pending.get("casting_skill_id", 0) or 0) == int(casting_skill_id or 0)
        )

    def _release_stale_pi_pre_dispatch_claim(self) -> None:
        """Release an owned PI claim if its dispatch callback vanished.

        A healthy PI request reaches activation/recharge well inside this bound.
        This is only a recovery net for callback recreation; it never releases
        another account's claim because release_interrupt_claim is owner-scoped.
        """
        pending = self._pi_pre_dispatch_claim
        if not pending:
            return
        started_ms = float(pending.get("started_ms", 0.0) or 0.0)
        if started_ms <= 0.0 or self._now_ms() - started_ms < 900.0:
            return
        target_id = int(pending.get("target_id", 0) or 0)
        casting_skill_id = int(pending.get("casting_skill_id", 0) or 0)
        # If PI is already visibly active or on recharge, ownership succeeded;
        # only forget the local recovery marker and let the short shared lease
        # expire normally so no second controller jumps into the same cast.
        try:
            pi_slot = int(GLOBAL_CACHE.SkillBar.GetSlotBySkillID(Psychic_Instability_ID) or 0)
            if pi_slot > 0 and self._slot_recharge_remaining_ms(pi_slot) > 0:
                self._clear_pi_pre_dispatch_claim_local()
                return
            player_id = int(Player.GetAgentID() or 0)
            if (
                player_id > 0
                and Agent.IsCasting(player_id)
                and int(Agent.GetCastingSkillID(player_id) or 0) == int(Psychic_Instability_ID)
            ):
                self._clear_pi_pre_dispatch_claim_local()
                return
        except Exception:
            pass
        if target_id > 0:
            release_interrupt_claim(
                target_id, casting_skill_id, reason="stage8_e07c_stale_local_owner"
            )
        self._log_rotation_event(
            "PI_CONTROL_PRE_DISPATCH_CLAIM_STALE_RELEASE",
            target_id=target_id,
            enemy_skill_id=casting_skill_id,
            age_ms=round(max(0.0, self._now_ms() - started_ms), 1),
            policy="exp36_stage8_e07c_owned_claim_recovery",
        )
        self._clear_pi_pre_dispatch_claim_local()

    def _pis_log_controller_telemetry(self) -> None:
        """EXP27 diagnostics only; never changes combat decisions.

        A runtime heartbeat is emitted from the selected build itself.  If these
        events are absent while the account is visibly casting, the issue is
        build selection/log export rather than the PI Support rotation.
        """
        now = self._now_ms()
        if self._pi_support_controller_active_logged and now < self._pi_support_next_heartbeat_ms:
            return

        player_id = int(Player.GetAgentID() or 0)
        bar: list[int] = []
        try:
            for slot in range(1, 9):
                bar.append(int(GLOBAL_CACHE.SkillBar.GetSkillIDBySlot(slot) or 0))
        except Exception:
            bar = []

        # The old telemetry still required Cry of Frustration although the
        # actual PI controller no longer requires it. Report the real role
        # match and the preferred Arcane Conundrum + Energy Tap bar separately.
        required = [int(Psychic_Instability_ID)]
        supported = [
            int(Complicate_ID), int(Arcane_Conundrum_ID), int(Signet_of_Disruption_ID), int(Unnatural_Signet_ID),
            int(Fragility_ID), int(Epidemic_ID), int(Ghostmirror_Light_ID),
            int(Spirit_Light_ID), int(Life_ID), int(Mend_Body_and_Soul_ID),
            int(Power_Drain_ID), int(Energy_Tap_ID),
        ]
        live = set(int(x) for x in bar if int(x or 0) > 0)
        bar_match = bool(all(int(x) in live for x in required))
        preferred_control = bool(int(Arcane_Conundrum_ID) in live)
        preferred_sustain = bool(int(Energy_Tap_ID) in live)
        supported_hits = sum(1 for x in supported if int(x) in live)

        if not self._pi_support_controller_active_logged:
            self._log_rotation_event(
                "PI_SUPPORT_CONTROLLER_ACTIVE",
                player_id=int(player_id),
                build_name=str(self.build_name),
                policy="exp27_runtime_selected_build_proof",
            )
            self._log_rotation_event(
                "PI_SUPPORT_BAR_MATCHED",
                player_id=int(player_id),
                bar_match=bool(bar_match),
                bar_skill_ids=",".join(str(int(x)) for x in bar),
                required_skill_ids=",".join(str(int(x)) for x in required),
                supported_skill_ids=",".join(str(int(x)) for x in supported),
                supported_hits=int(supported_hits),
                preferred_arcane_conundrum_equipped=bool(preferred_control),
                preferred_energy_tap_equipped=bool(preferred_sustain),
                policy="pi_only_required_arcane_conundrum_energy_tap_preferred",
            )
            self._log_rotation_event(
                "HEROAI_CONTROLLER_MATCH_DIAG",
                owner_kind="account_player",
                owner_id=int(player_id),
                intended_role="pi_support",
                selected_controller="Psychic_Instability_Wastrels",
                matched=bool(bar_match),
                reject_reason=("none" if bar_match else "missing_required_pi"),
                bar_skill_ids=",".join(str(int(x)) for x in bar),
                required_skill_ids=",".join(str(int(x)) for x in required),
                supported_hits=int(supported_hits),
                policy="exp36_stage12_e19_phase2_controller_plus_session_observation",
            )
            self._pi_support_controller_active_logged = True

        # Low-frequency heartbeat keeps the account visible even during periods
        # with no heal/interrupt opportunity.  Five seconds is intentionally
        # sparse so CombatDebug stays event-oriented.
        self._log_rotation_event(
            "PI_SUPPORT_HEARTBEAT",
            player_id=int(player_id),
            bar_match=bool(bar_match),
            energy=round(float(self._current_energy_fraction()), 3),
            preferred_arcane_conundrum_equipped=bool(preferred_control),
            preferred_energy_tap_equipped=bool(preferred_sustain),
            in_aggro=bool(self.IsInAggro()),
            close_to_aggro=bool(self.IsCloseToAggro()),
            policy="exp27_logging_only_5s_heartbeat",
        )
        self._pi_support_next_heartbeat_ms = float(now + 5000.0)

    def _pis_observe_control_casts(self) -> None:
        """EXP33 telemetry-only proof of real PI-support control activations.

        EXP32 logs proved the custom PI Support controller and healing/filler path
        were active, while the user could visibly see PI/Cry/Complicate/Power Drain
        cast without the branch-local success events appearing. Observe both the
        player's live activation and ready->recharge transitions so a cast is
        recorded regardless of which internal dispatcher initiated it.
        """
        controls = (
            (int(Psychic_Instability_ID), "pi"),
            (int(Cry_of_Frustration_ID), "cry"),
            (int(Complicate_ID), "complicate"),
            (int(Signet_of_Disruption_ID), "signet_of_disruption"),
            (int(Power_Drain_ID), "power_drain"),
        )
        player_id = int(Player.GetAgentID() or 0)
        target_id = int(Player.GetTargetID() or 0)

        # Activation-edge observation catches skills while the client reports
        # the player as casting them. Never emit every tick of the same cast.
        live_skill = 0
        try:
            if player_id > 0 and Agent.IsCasting(player_id):
                live_skill = int(Agent.GetCastingSkillID(player_id) or 0)
        except Exception:
            live_skill = 0
        control_ids = {sid for sid, _name in controls if sid > 0}
        if live_skill in control_ids and live_skill != int(self._pi_support_observed_casting_skill_id):
            label = next((name for sid, name in controls if sid == live_skill), "unknown")
            self._log_rotation_event(
                "PI_SUPPORT_CONTROL_ACTIVATION_OBSERVED",
                our_skill_id=int(live_skill), skill=str(label), target_id=int(target_id),
                policy="exp33_runtime_observer_independent_of_dispatch_path",
            )
        self._pi_support_observed_casting_skill_id = int(live_skill if live_skill in control_ids else 0)

        # Recharge-edge observation is the stronger proof for very fast skills
        # whose activation can occur between controller ticks. The first sample
        # only establishes a baseline and is not counted as a cast.
        for skill_id, label in controls:
            if skill_id <= 0 or not self.IsSkillEquipped(skill_id):
                continue
            try:
                slot = int(GLOBAL_CACHE.SkillBar.GetSlotBySkillID(skill_id) or 0)
            except Exception:
                slot = 0
            if not (1 <= slot <= 8):
                continue
            recharge = int(self._slot_recharge_remaining_ms(slot))
            prev = self._pi_support_control_recharge_prev.get(skill_id)
            self._pi_support_control_recharge_prev[skill_id] = int(recharge)
            if prev is None:
                continue
            if int(prev) <= 0 and int(recharge) > 0:
                # Stage 20.1 performance hotfix: keep the robust real-PI
                # recharge-edge proof LOCAL to the PI account. Stage 20's extra
                # cross-account whiteboard lease regressed SharedMemory.Update
                # badly on Franky's A/B test. Fragility can still prefer the
                # exact PI target when that target remains inside the normal
                # authoritative team packet; no new shared-memory traffic is used.
                observed_target_id = int(target_id or 0)
                if int(skill_id) == int(Psychic_Instability_ID):
                    pending = self._pi_pre_dispatch_claim
                    try:
                        pending_age = float(self._now_ms()) - float((pending or {}).get("started_ms", 0.0) or 0.0)
                    except Exception:
                        pending_age = 999999.0
                    pending_target = int((pending or {}).get("target_id", 0) or 0) if pending else 0
                    if pending_target > 0 and 0.0 <= pending_age <= 1800.0:
                        observed_target_id = int(pending_target)
                    if observed_target_id > 0:
                        try:
                            usable = bool(Agent.IsValid(observed_target_id) and Agent.IsAlive(observed_target_id))
                        except Exception:
                            usable = False
                        if usable:
                            now_anchor = float(self._now_ms())
                            self._pis_fragility_followup_until_ms = float(now_anchor + 2800.0)
                            self._pis_fragility_followup_anchor = int(observed_target_id)
                            self._pis_fragility_followup_members = ()
                            self._log_rotation_event(
                                "PI_CAST_ANCHOR_LOCAL",
                                target_id=int(observed_target_id),
                                source=("pre_dispatch_claim" if pending_target > 0 and pending_age <= 1800.0 else "live_target"),
                                policy="stage20_1_local_only_no_shared_memory_preference",
                            )
                    self._clear_pi_pre_dispatch_claim_local()
                self._log_rotation_event(
                    "PI_SUPPORT_CONTROL_RECHARGE_STARTED",
                    our_skill_id=int(skill_id), skill=str(label), slot=int(slot),
                    recharge_ms=int(recharge), target_id=int(observed_target_id),
                    policy="exp33_actual_cast_proof_ready_to_recharge_edge",
                )

    @staticmethod
    def _distance_to_player(agent_id: int) -> float:
        try:
            return float(Utils.Distance(Player.GetXY(), Agent.GetXY(int(agent_id))))
        except Exception:
            return 999999.0

    @staticmethod
    def _current_energy_absolute() -> float:
        try:
            player_id = int(Player.GetAgentID() or 0)
            return float(Agent.GetEnergy(player_id)) * float(Agent.GetMaxEnergy(player_id))
        except Exception:
            return 0.0

    @staticmethod
    def _current_energy_fraction() -> float:
        try:
            return float(Agent.GetEnergy(Player.GetAgentID()))
        except Exception:
            return 0.0

    @staticmethod
    def _skill_energy_cost(skill_id: int) -> float:
        try:
            player_id = int(Player.GetAgentID() or 0)
            return max(
                0.0,
                float(Routines.Checks.Skills.GetEnergyCostWithEffects(int(skill_id), player_id)),
            )
        except Exception:
            return 0.0

    @staticmethod
    def _slot_recharge_remaining_ms(slot: int) -> int:
        """Return the live player-skill recharge in milliseconds.

        CastSkillSlot only proves that a command was queued. For PI -> RoJ
        coordination we need proof that Guild Wars actually consumed PI.
        """
        try:
            if not (1 <= int(slot) <= 8):
                return -1
            data = GLOBAL_CACHE.SkillBar.GetSkillData(int(slot))
            return max(0, int(getattr(data, "get_recharge", 0) or 0))
        except Exception:
            return -1

    @staticmethod
    def _packet_knockdown_targets(packet_members: list[int] | tuple[int, ...]) -> list[int]:
        knocked: list[int] = []
        for enemy_id in packet_members:
            enemy_id = int(enemy_id or 0)
            if enemy_id <= 0:
                continue
            try:
                if Agent.IsValid(enemy_id) and Agent.IsAlive(enemy_id) and Agent.IsKnockedDown(enemy_id):
                    knocked.append(enemy_id)
            except Exception:
                continue
        return knocked

    def _arm_pi_confirmation(
        self,
        *,
        slot: int,
        target_id: int,
        casting_skill_id: int,
        anchor: int,
        packet_members: list[int],
        publish_roj_burst: bool = True,
    ) -> None:
        now = self._now_ms()
        self._pending_pi_confirmation = {
            "started_ms": float(now),
            "slot": int(slot),
            "target_id": int(target_id),
            "casting_skill_id": int(casting_skill_id),
            "anchor": int(anchor),
            "packet_members": tuple(int(x) for x in packet_members if int(x or 0) > 0),
            "publish_roj_burst": bool(publish_roj_burst),
            "packet_reserved": False,
            "activation_seen": False,
            "saw_cast_animation": False,
        }
        self._log_rotation_event(
            "PI_REAL_KNOCKDOWN_PENDING",
            target_id=int(target_id),
            packet_size=len(packet_members),
            publish_roj_burst=bool(publish_roj_burst),
            policy="persistent_cross_tick_confirmation",
        )

    def _poll_pi_confirmation(self) -> bool:
        """Poll an already-issued PI request without blocking the build runner.

        Returns True while the PI transaction owns this tick. This prevents a
        second interrupt/heal command from racing the still-pending PI action.
        """
        pending = self._pending_pi_confirmation
        if not pending:
            return False

        now = self._now_ms()
        started = float(pending.get("started_ms", now) or now)
        elapsed = max(0.0, now - started)
        slot = int(pending.get("slot", 0) or 0)
        target_id = int(pending.get("target_id", 0) or 0)
        casting_skill_id = int(pending.get("casting_skill_id", 0) or 0)
        anchor = int(pending.get("anchor", 0) or 0)
        packet_members = [int(x) for x in pending.get("packet_members", ()) if int(x or 0) > 0]

        player_id = int(Player.GetAgentID() or 0)
        currently_casting_pi = False
        try:
            if (
                player_id > 0
                and Agent.IsCasting(player_id)
                and int(Agent.GetCastingSkillID(player_id) or 0) == int(Psychic_Instability_ID)
            ):
                currently_casting_pi = True
                pending["saw_cast_animation"] = True
        except Exception:
            pass

        recharge_ms = self._slot_recharge_remaining_ms(slot)
        if recharge_ms > 0:
            pending["activation_seen"] = True

        knocked = self._packet_knockdown_targets(packet_members)
        if bool(pending.get("activation_seen", False)) and knocked:
            self._last_pi_cast_ms = now
            self._last_pi_anchor = int(anchor)
            # Stage 19: remember the confirmed PI packet briefly so Fragility can
            # naturally follow the knockdown.  This is only target preference; it
            # never reserves a tick or delays a heal/interrupt.
            self._pis_fragility_followup_until_ms = float(now + 2600.0)
            self._pis_fragility_followup_anchor = int(target_id or anchor or 0)
            self._pis_fragility_followup_members = tuple(
                int(x) for x in packet_members if int(x or 0) > 0
            )
            publish_roj_burst = bool(pending.get("publish_roj_burst", True))
            posted = (
                int(publish_pi_knockdown_packet(packet_members, anchor_id=int(target_id)) or 0)
                if publish_roj_burst else 0
            )
            self._log_rotation_event(
                "PI_REAL_KNOCKDOWN_CONFIRMED",
                target_id=int(target_id),
                knocked_ids=",".join(map(str, knocked)),
                recharge_ms=int(recharge_ms),
                saw_cast_animation=bool(pending.get("saw_cast_animation", False)),
                confirm_delay_ms=round(float(elapsed), 1),
                publish_roj_burst=bool(publish_roj_burst),
            )
            self._log_rotation_event(
                "PI_PACKET_CLAIM_COMMITTED",
                target_id=int(target_id),
                enemy_skill_id=int(casting_skill_id),
                packet_size=len(packet_members),
                policy="one_real_pi_knockdown_per_packet_across_player_and_hero",
            )
            if publish_roj_burst:
                self._log_rotation_event(
                    "PI_KNOCKDOWN_PACKET_PUBLISHED",
                    target_id=int(target_id),
                    enemy_skill_id=int(casting_skill_id),
                    packet_size=len(packet_members),
                    leases_posted=int(posted),
                    policy="recharge_plus_actual_knockdown_confirmed_for_roj_burst",
                )
            else:
                self._log_rotation_event(
                    "PI_DANGER_PEELOFF_KNOCKDOWN_CONFIRMED",
                    target_id=int(target_id),
                    enemy_skill_id=int(casting_skill_id),
                    packet_size=len(packet_members),
                    policy="safety_interrupt_only_keep_roj_on_authoritative_focus",
                )
            self._pending_pi_confirmation = None
            return True

        # If no activation is visible and PI is no longer being cast, fail
        # early instead of freezing the hybrid support role for the full timeout.
        if elapsed >= float(PI_ACTIVATION_START_GRACE_MS) and not bool(pending.get("activation_seen", False)) and not bool(pending.get("saw_cast_animation", False)):
            self._log_rotation_event(
                "PI_REAL_KNOCKDOWN_NOT_CONFIRMED",
                target_id=int(target_id),
                activation_seen=False,
                saw_cast_animation=False,
                recharge_ms=int(recharge_ms),
                packet_size=len(packet_members),
                reason="activation_never_started",
                policy="do_not_publish_phantom_pi_event",
            )
            if bool(pending.get("packet_reserved", False)):
                release_pi_packet(packet_members, reason="player_pi_activation_not_observed")
            self._pending_pi_confirmation = None
            return False

        if elapsed > float(PI_CAST_CONFIRM_TIMEOUT_MS):
            self._log_rotation_event(
                "PI_REAL_KNOCKDOWN_NOT_CONFIRMED",
                target_id=int(target_id),
                activation_seen=bool(pending.get("activation_seen", False)),
                saw_cast_animation=bool(pending.get("saw_cast_animation", False)),
                recharge_ms=int(recharge_ms),
                packet_size=len(packet_members),
                reason="no_visible_packet_knockdown",
                policy="do_not_publish_phantom_pi_event",
            )
            if bool(pending.get("packet_reserved", False)):
                release_pi_packet(packet_members, reason="player_pi_no_confirmed_knockdown")
            self._pending_pi_confirmation = None
            return False

        # Once the PI command genuinely started, confirmation is telemetry only.
        # Do not freeze the hybrid Mesmer while waiting to *observe* knockdown.
        return bool(currently_casting_pi)

    def _get_native_pi_slot(self) -> int:
        try:
            return int(GLOBAL_CACHE.SkillBar.GetSlotBySkillID(Psychic_Instability_ID) or 0)
        except Exception:
            return 0

    def _local_pi_fallback_packet(self, role: str) -> tuple[int, list[int]]:
        """Small-party fail-open target packet for the player PI Mesmer.

        EXP2 fixed the equivalent wand-only failure for Keystone Mesmers.  The
        EXP2 logs show the PI account can hit the same underlying condition:
        shared TeamCombatFocus temporarily resolves to 0 while locally visible
        enemies are still alive and castable.  PI/Power Drain/Complicate then
        have no packet to inspect, while independent fillers such as Epidemic
        can still fire.

        Keep shared focus authoritative whenever it exists.  This local packet
        is used only for parties of eight or fewer and never wakes the combat
        rotation for a lone hostile spirit.
        """
        if not self._small_party_support_mode():
            return 0, []
        try:
            enemy_ids = AgentArray.GetEnemyArray() or []
            enemy_ids = AgentArray.Filter.ByDistance(
                enemy_ids, Player.GetXY(), float(Range.Spellcast.value)
            )
            enemy_ids = [
                int(enemy_id)
                for enemy_id in enemy_ids
                if int(enemy_id or 0) > 0
                and Agent.IsValid(int(enemy_id))
                and Agent.IsAlive(int(enemy_id))
            ]
        except Exception:
            return 0, []
        if not enemy_ids:
            return 0, []

        normal_enemies: list[int] = []
        spirit_enemies: list[int] = []
        for enemy_id in enemy_ids:
            try:
                is_spirit = bool(Agent.IsSpirit(int(enemy_id)))
            except Exception:
                is_spirit = False
            (spirit_enemies if is_spirit else normal_enemies).append(int(enemy_id))

        if normal_enemies:
            candidates = normal_enemies
        elif len(spirit_enemies) >= 2:
            candidates = spirit_enemies
        else:
            return 0, []

        # Preserve a valid manual/current enemy target when possible; otherwise
        # choose the most useful local control anchor. Casting and dangerous
        # enemies outrank support/caster identity, then local packet density.
        try:
            current_target = int(Player.GetTargetID() or 0)
        except Exception:
            current_target = 0
        def _rank(enemy_id: int):
            casting_skill_id = int(get_casting_skill_id(int(enemy_id)) or 0)
            try:
                xy = Agent.GetXY(int(enemy_id))
                near_count = sum(
                    1 for other_id in candidates
                    if Utils.Distance(xy, Agent.GetXY(int(other_id))) <= float(Range.Adjacent.value)
                )
            except Exception:
                near_count = 1
            return (
                -int(casting_skill_id > 0 and is_dangerous_cast(int(enemy_id))),
                -int(casting_skill_id > 0),
                -int(is_support_or_caster(int(enemy_id))),
                -int(int(enemy_id) == int(current_target)),
                -int(near_count),
                self._distance_to_player(int(enemy_id)),
                int(enemy_id),
            )

        anchor = int(sorted(candidates, key=_rank)[0])

        try:
            anchor_xy = Agent.GetXY(int(anchor))
            members = [
                int(enemy_id) for enemy_id in candidates
                if Utils.Distance(anchor_xy, Agent.GetXY(int(enemy_id))) <= float(Range.Adjacent.value)
            ]
        except Exception:
            members = [int(anchor)]
        if anchor not in members:
            members.append(int(anchor))
        return int(anchor), sorted(set(int(x) for x in members if int(x or 0) > 0))

    def _focus_packet(self, role: str) -> tuple[int, list[int]]:
        anchor = int(
            get_team_cluster_anchor(
                filter_range=Range.Spellcast.value,
                minimum_enemies=2,
                consumer_role=f"pi_{role}",
            )
            or 0
        )
        if anchor > 0:
            members = get_team_cluster_members(
                anchor,
                radius=Range.Adjacent.value,
                filter_range=Range.Spellcast.value,
            )
            members = [
                int(enemy_id)
                for enemy_id in members
                if int(enemy_id or 0) > 0 and Agent.IsValid(int(enemy_id)) and Agent.IsAlive(int(enemy_id))
            ]
            if anchor not in members and Agent.IsValid(anchor) and Agent.IsAlive(anchor):
                members.append(anchor)
            if members:
                return anchor, sorted(set(members))

        # EXP4: in <=8-man content, fail open locally when shared focus is
        # temporarily absent. This fixes the PI account losing all enemy-target
        # control while enemies are visibly present.
        fallback_anchor, fallback_members = self._local_pi_fallback_packet(role)
        if fallback_anchor > 0 and fallback_members:
            return int(fallback_anchor), list(fallback_members)
        return 0, []

    def _target_still_in_focus(
        self,
        target_id: int,
        expected_anchor: int,
        *,
        require_casting_skill_id: int = 0,
    ) -> bool:
        if target_id <= 0 or expected_anchor <= 0:
            return False
        anchor, members = self._focus_packet("final_cast_check")
        if anchor != int(expected_anchor) or int(target_id) not in members:
            return False
        if require_casting_skill_id > 0:
            return target_still_casting_skill(int(target_id), int(require_casting_skill_id))
        return bool(Agent.IsValid(target_id) and Agent.IsAlive(target_id))

    def _target_still_scattered_cleanup_cast(
        self,
        target_id: int,
        casting_skill_id: int,
    ) -> bool:
        """Permit PI off the damage anchor only while no real packet exists."""
        if int(target_id or 0) <= 0 or int(casting_skill_id or 0) <= 0:
            return False
        anchor, members = self._focus_packet("scattered_final_cast_check")
        if anchor <= 0 or len(members) > 1:
            return False
        try:
            if not Agent.IsValid(int(target_id)) or not Agent.IsAlive(int(target_id)):
                return False
        except Exception:
            return False
        if self._distance_to_player(int(target_id)) > float(Range.Spellcast.value):
            return False
        return target_still_casting_skill(int(target_id), int(casting_skill_id))

    def _pi_opener_dispatch_is_feasible(
        self,
        target_id: int,
        casting_skill_id: int,
    ) -> bool:
        """Conservative final gate for the non-emergency PI opener.

        The v32 live trace showed that almost every remaining PI command that
        never entered activation came from the ordinary opener/cleanup path.
        Those casts are optional control (dangerous casts were already offered
        to the ready-now interrupt pool), so prefer a missed PI opportunity over
        queuing the elite into an enemy cast that is already ending.
        """
        target_id = int(target_id or 0)
        casting_skill_id = int(casting_skill_id or 0)
        if target_id <= 0 or casting_skill_id <= 0:
            return False
        if not target_still_casting_skill(target_id, casting_skill_id):
            return False
        if not opportunistic_interrupt_is_feasible(
            target_id,
            int(Psychic_Instability_ID),
            origin_agent_id=int(Player.GetAgentID() or 0),
        ):
            return False
        remaining_ms = get_enemy_cast_remaining_ms(target_id, casting_skill_id)
        if remaining_ms is None:
            return False
        return int(remaining_ms) >= int(PI_OPENER_MIN_REMAINING_MS)

    def _target_still_dangerous_peel_cast(
        self,
        target_id: int,
        casting_skill_id: int,
    ) -> bool:
        """Validate an emergency interrupt without changing team damage focus."""
        if int(target_id or 0) <= 0 or int(casting_skill_id or 0) <= 0:
            return False
        try:
            if not Agent.IsValid(int(target_id)) or not Agent.IsAlive(int(target_id)):
                return False
            if Agent.IsKnockedDown(int(target_id)):
                return False
        except Exception:
            return False
        if self._distance_to_player(int(target_id)) > float(Range.Spellcast.value):
            return False
        return target_still_casting_skill(int(target_id), int(casting_skill_id))

    @staticmethod
    def _danger_packet_members(target_id: int) -> list[int]:
        """Local adjacent packet around an emergency PI target."""
        target_id = int(target_id or 0)
        if target_id <= 0:
            return []
        try:
            target_xy = Agent.GetXY(target_id)
            enemy_ids = AgentArray.GetEnemyArray() or []
            enemy_ids = AgentArray.Filter.ByDistance(
                enemy_ids, target_xy, float(Range.Adjacent.value)
            )
            out = [
                int(eid) for eid in enemy_ids
                if int(eid or 0) > 0 and Agent.IsValid(int(eid)) and Agent.IsAlive(int(eid))
            ]
        except Exception:
            out = [target_id]
        if target_id not in out:
            out.append(target_id)
        return sorted(set(out))

    @staticmethod
    def _packet_near_count(target_id: int, members: list[int]) -> int:
        try:
            target_xy = Agent.GetXY(int(target_id))
            return sum(
                1
                for enemy_id in members
                if Utils.Distance(target_xy, Agent.GetXY(int(enemy_id))) <= float(Range.Adjacent.value)
            )
        except Exception:
            return 1

    @staticmethod
    def _casting_count_near(
        target_id: int,
        candidates: list[tuple[int, int]],
    ) -> int:
        try:
            target_xy = Agent.GetXY(int(target_id))
            return sum(
                1
                for enemy_id, _skill_id in candidates
                if Utils.Distance(target_xy, Agent.GetXY(int(enemy_id))) <= float(Range.Nearby.value)
            )
        except Exception:
            return 0

    @staticmethod
    def _health_fraction(agent_id: int) -> float:
        """Shared-memory aware HP for multibox support decisions.

        Direct ``Agent.GetHealth`` can lag for sibling player clients.  The
        common Checks helper uses the same-party shared AgentData when present,
        which is the value the healer actually needs for cross-account triage.
        """
        try:
            return float(Routines.Checks.Agents.GetHealth(int(agent_id)))
        except Exception:
            try:
                return float(Agent.GetHealth(int(agent_id)))
            except Exception:
                return 0.0

    @staticmethod
    def _is_boss(agent_id: int) -> bool:
        try:
            return bool(Agent.HasBossGlow(int(agent_id)))
        except Exception:
            return False

    def _pi_ready_for_opening(self) -> bool:
        slot = self._get_native_pi_slot()
        if not (1 <= slot <= 8):
            return False
        try:
            return bool(self.CanCastSkillSlot(slot))
        except Exception:
            return False

    @staticmethod
    def _is_spell_for_disruption(skill_id: int) -> bool:
        """Stage 18: Signet of Disruption uses only its unconditional spell interrupt.

        The skill can interrupt any skill on a hexed foe, but this build deliberately
        ignores that conditional branch.  It is treated purely as a fast spell
        interrupt so attacks/attack skills/stances/etc. never become targets just
        because the foe happens to be hexed.
        """
        if int(skill_id or 0) <= 0:
            return False
        try:
            flags = GLOBAL_CACHE.Skill.Flags
            return bool(
                flags.IsSpell(skill_id)
                or flags.IsHex(skill_id)
                or flags.IsEnchantment(skill_id)
                or flags.IsWeaponSpell(skill_id)
                or flags.IsItemSpell(skill_id)
                or flags.IsWell(skill_id)
                or flags.IsWard(skill_id)
                or flags.IsGlyph(skill_id)
            )
        except Exception:
            return False

    @staticmethod
    def _is_spell_or_chant(skill_id: int) -> bool:
        if int(skill_id or 0) <= 0:
            return False
        try:
            flags = GLOBAL_CACHE.Skill.Flags
            # Spell subtypes are exposed separately by the native API.
            return bool(
                flags.IsSpell(skill_id)
                or flags.IsChant(skill_id)
                or flags.IsHex(skill_id)
                or flags.IsEnchantment(skill_id)
                or flags.IsWeaponSpell(skill_id)
                or flags.IsItemSpell(skill_id)
                or flags.IsWell(skill_id)
                or flags.IsWard(skill_id)
                or flags.IsGlyph(skill_id)
            )
        except Exception:
            return False

    def _casting_candidates(
        self,
        members: list[int],
        *,
        spell_or_chant_only: bool = False,
        include_dangerous: bool = True,
    ) -> list[tuple[int, int]]:
        candidates: list[tuple[int, int]] = []
        for enemy_id in members:
            casting_skill_id = int(get_casting_skill_id(int(enemy_id)) or 0)
            if casting_skill_id <= 0:
                continue
            if spell_or_chant_only and not self._is_spell_or_chant(casting_skill_id):
                continue
            if not include_dangerous and is_dangerous_cast(int(enemy_id)):
                continue
            candidates.append((int(enemy_id), casting_skill_id))

        candidates.sort(
            key=lambda item: (
                -int(is_dangerous_cast(int(item[0]))),
                -self._packet_near_count(int(item[0]), members),
                -int(is_support_or_caster(int(item[0]))),
                self._distance_to_player(int(item[0])),
                int(item[0]),
            )
        )
        return candidates

    def _claim_focus_dangerous_cast(
        self,
        *,
        anchor: int,
        members: list[int],
        interrupter_skill_id: int,
        spell_or_chant_only: bool = False,
    ) -> tuple[int, int, bool]:
        member_set = set(int(enemy_id) for enemy_id in members)

        def _validator(enemy_id: int, casting_skill_id: int) -> bool:
            if int(enemy_id) not in member_set:
                return False
            if spell_or_chant_only and not self._is_spell_or_chant(int(casting_skill_id)):
                return False
            return True

        target_id, casting_skill_id = claim_best_dangerous_cast(
            range_value=int(Range.Spellcast.value),
            interrupter_skill_id=int(interrupter_skill_id),
            validator=_validator,
        )
        target_id = int(target_id or 0)
        casting_skill_id = int(casting_skill_id or 0)
        if target_id <= 0 or casting_skill_id <= 0:
            return 0, 0, False
        if not self._target_still_in_focus(
            target_id,
            anchor,
            require_casting_skill_id=casting_skill_id,
        ):
            release_interrupt_claim(target_id, casting_skill_id, reason="pi_focus_changed")
            return 0, 0, False
        return target_id, casting_skill_id, True

    def _pick_interrupt_target(
        self,
        *,
        anchor: int,
        members: list[int],
        interrupter_skill_id: int,
        spell_or_chant_only: bool = False,
    ) -> tuple[int, int, bool]:
        target_id, casting_skill_id, claimed = self._claim_focus_dangerous_cast(
            anchor=anchor,
            members=members,
            interrupter_skill_id=interrupter_skill_id,
            spell_or_chant_only=spell_or_chant_only,
        )
        if target_id > 0:
            return target_id, casting_skill_id, claimed

        # Do not bypass another account's dangerous-cast claim.
        candidates = self._casting_candidates(
            members,
            spell_or_chant_only=spell_or_chant_only,
            include_dangerous=False,
        )
        if not candidates:
            return 0, 0, False
        target_id, casting_skill_id = candidates[0]
        return int(target_id), int(casting_skill_id), False


    def _pick_offfocus_priority_caster(
        self,
        *,
        focus_members: list[int],
        interrupter_skill_id: int,
        spell_or_chant_only: bool = False,
        require_priority_role: bool = True,
        claim: bool = False,
    ) -> tuple[int, int, bool]:
        """Pick a caster outside the primary shared damage packet.

        EXP19 control policy: the damage team already owns the authoritative
        Keystone/RoJ packet.  The PI support therefore peels healers/support and
        dangerous casters outside that packet first.  This also naturally covers
        a second cluster: its healer/dangerous caster is shut down while the main
        team melts cluster #1.  If no such off-packet threat exists, callers fall
        back to the primary packet.
        """
        focus_set = set(int(x) for x in (focus_members or []) if int(x or 0) > 0)
        try:
            enemy_ids = AgentArray.GetEnemyArray() or []
            enemy_ids = AgentArray.Filter.ByDistance(
                enemy_ids, Player.GetXY(), Range.Spellcast.value
            )
        except Exception:
            enemy_ids = []

        outside = [
            int(enemy_id) for enemy_id in enemy_ids
            if int(enemy_id or 0) > 0
            and int(enemy_id) not in focus_set
            and Agent.IsValid(int(enemy_id))
            and Agent.IsAlive(int(enemy_id))
        ]
        ranked = []
        for enemy_id in outside:
            casting_skill_id = int(get_casting_skill_id(int(enemy_id)) or 0)
            if casting_skill_id <= 0:
                continue
            if spell_or_chant_only and not self._is_spell_or_chant(int(casting_skill_id)):
                continue
            priority_role = bool(
                is_dangerous_cast(int(enemy_id))
                or is_support_or_caster(int(enemy_id))
            )
            if require_priority_role and not priority_role:
                continue
            if not opportunistic_interrupt_is_feasible(
                int(enemy_id), int(interrupter_skill_id),
                origin_agent_id=int(Player.GetAgentID() or 0),
            ):
                continue
            # Prefer a dangerous/support caster, then one embedded in the
            # largest secondary packet, then nearest.
            near_count = self._packet_near_count(int(enemy_id), outside)
            ranked.append((
                -int(is_dangerous_cast(int(enemy_id))),
                -int(is_support_or_caster(int(enemy_id))),
                -int(near_count),
                self._distance_to_player(int(enemy_id)),
                int(enemy_id),
                int(casting_skill_id),
            ))
        ranked.sort()
        for _danger, _support, _density, _distance, enemy_id, casting_skill_id in ranked:
            if not target_still_casting_skill(int(enemy_id), int(casting_skill_id)):
                continue
            if claim:
                if not post_interrupt_claim(
                    int(enemy_id), int(casting_skill_id), int(interrupter_skill_id)
                ):
                    continue
                return int(enemy_id), int(casting_skill_id), True
            return int(enemy_id), int(casting_skill_id), False
        return 0, 0, False

    def _pick_dense_cluster_pi_target(self, anchor: int, members: list[int]) -> tuple[int, int, bool]:
        """Pick a live activation that maximizes PI's adjacent knockdown packet.

        EXP6 makes packet size the primary score. Dangerous/support identity is
        only a tiebreaker because other interrupt skills own single dangerous
        casts. The cast is claimed only after it wins local density scoring.
        """
        ranked: list[tuple] = []
        for enemy_id in members:
            enemy_id = int(enemy_id or 0)
            skill_id = int(get_casting_skill_id(enemy_id) or 0)
            if enemy_id <= 0 or skill_id <= 0:
                continue
            if not self._target_still_in_focus(enemy_id, int(anchor), require_casting_skill_id=skill_id):
                continue
            if not opportunistic_interrupt_is_feasible(
                enemy_id, int(Psychic_Instability_ID),
                origin_agent_id=int(Player.GetAgentID() or 0),
            ):
                continue
            near_count = self._packet_near_count(enemy_id, members)
            # EXP14: clusters remain PI's first choice.  The isolated-caster
            # fallback is handled by _pick_scattered_cleanup_pi_target only when
            # no useful 2+ packet is available, so dense balls always win.
            ranked.append((
                -int(near_count),
                -int(is_dangerous_cast(enemy_id)),
                -int(is_support_or_caster(enemy_id)),
                self._distance_to_player(enemy_id),
                enemy_id,
                skill_id,
            ))
        ranked.sort()
        for _density, _danger, _support, _distance, enemy_id, skill_id in ranked:
            # EXP36 Stage 4 / E07: discovery itself remains claim-free.  The exact
            # cast is reserved only after this candidate wins locally, inside
            # _cast_interrupt_slot immediately before dispatch.  Every failed
            # acquire/request/activation path releases that short claim.
            if not target_still_casting_skill(int(enemy_id), int(skill_id)):
                continue
            return int(enemy_id), int(skill_id), False
        return 0, 0, False

    def _pick_scattered_cleanup_pi_target(self, anchor: int) -> tuple[int, int, bool]:
        """Pick a single caster for PI without pre-claiming the activation.

        EXP15 keeps PI aggressive when no 2+ ball remains: support/healer-like
        and dangerous casters win, then the shared cleanup target, then any live
        caster in range.  The important difference is that target discovery does
        not reserve the cast.  PI must actually start activating before it owns
        the shared interrupt claim.
        """
        try:
            enemy_ids = AgentArray.GetEnemyArray() or []
            enemy_ids = AgentArray.Filter.ByDistance(
                enemy_ids, Player.GetXY(), Range.Spellcast.value
            )
        except Exception:
            enemy_ids = []

        ranked: list[tuple[tuple[int, int, int, float, int], int, int]] = []
        for enemy_id in enemy_ids:
            enemy_id = int(enemy_id or 0)
            casting_skill_id = int(get_casting_skill_id(enemy_id) or 0)
            if enemy_id <= 0 or casting_skill_id <= 0:
                continue
            if not self._target_still_scattered_cleanup_cast(enemy_id, casting_skill_id):
                continue
            try:
                if Agent.IsKnockedDown(enemy_id):
                    continue
            except Exception:
                pass
            if not opportunistic_interrupt_is_feasible(
                enemy_id, int(Psychic_Instability_ID),
                origin_agent_id=int(Player.GetAgentID() or 0),
            ):
                continue
            ranked.append((
                (
                    -int(is_support_or_caster(enemy_id)),
                    -int(is_dangerous_cast(enemy_id)),
                    0 if enemy_id == int(anchor) else 1,
                    self._distance_to_player(enemy_id),
                    enemy_id,
                ),
                enemy_id,
                casting_skill_id,
            ))

        if not ranked:
            return 0, 0, False
        ranked.sort(key=lambda item: item[0])
        _rank, target_id, casting_skill_id = ranked[0]
        return int(target_id), int(casting_skill_id), False

    def _cast_interrupt_slot(
        self,
        *,
        slot: int,
        expected_skill_id: int,
        target_id: int,
        casting_skill_id: int,
        anchor: int,
        claimed: bool,
        source: str,
        scattered_cleanup: bool = False,
        dangerous_peel: bool = False,
    ):
        if not (1 <= int(slot) <= 8):
            if claimed:
                release_interrupt_claim(target_id, casting_skill_id, reason="invalid_skill_slot")
            return False
        try:
            visible_skill_id = int(GLOBAL_CACHE.SkillBar.GetSkillIDBySlot(int(slot)) or 0)
        except Exception:
            visible_skill_id = 0
        if visible_skill_id != int(expected_skill_id):
            if claimed:
                release_interrupt_claim(target_id, casting_skill_id, reason="slot_skill_changed")
            return False
        if not self.CanCastSkillSlot(int(slot)):
            if claimed:
                release_interrupt_claim(target_id, casting_skill_id, reason="slot_not_castable")
            return False
        if not opportunistic_interrupt_is_feasible(
            int(target_id),
            int(expected_skill_id),
            origin_agent_id=int(Player.GetAgentID() or 0),
        ):
            if claimed:
                release_interrupt_claim(target_id, casting_skill_id, reason="interrupt_not_feasible")
            return False
        opener_pi = bool(
            int(expected_skill_id) == int(Psychic_Instability_ID)
            and str(source).startswith("opener")
        )
        if opener_pi and not self._pi_opener_dispatch_is_feasible(
            int(target_id), int(casting_skill_id)
        ):
            if claimed:
                release_interrupt_claim(
                    target_id, casting_skill_id, reason="pi_opener_dispatch_window_too_short"
                )
            return False

        def _final_target_usable() -> bool:
            if bool(dangerous_peel):
                return self._target_still_dangerous_peel_cast(
                    int(target_id),
                    int(casting_skill_id),
                )
            if bool(scattered_cleanup):
                return self._target_still_scattered_cleanup_cast(
                    int(target_id),
                    int(casting_skill_id),
                )
            return self._target_still_in_focus(
                int(target_id),
                int(anchor),
                require_casting_skill_id=int(casting_skill_id),
            )

        def _dispatch_target_usable() -> bool:
            # EXP11: once PI has won target/packet arbitration, do not invalidate
            # the cast merely because the shared focus anchor moved while the
            # client was acquiring the enemy.  PI only needs the selected enemy
            # to still be alive and casting the exact activation we elected.
            # Other interrupts retain the stricter focus validation.
            if int(expected_skill_id) == int(Psychic_Instability_ID):
                try:
                    if not (Agent.IsValid(int(target_id)) and Agent.IsAlive(int(target_id))):
                        return False
                    if not Agent.IsCasting(int(target_id)):
                        return False
                    if int(Agent.GetCastingSkillID(int(target_id)) or 0) != int(casting_skill_id):
                        return False
                except Exception:
                    return False
                if opener_pi:
                    return self._pi_opener_dispatch_is_feasible(
                        int(target_id), int(casting_skill_id)
                    )
                return True
            if not _final_target_usable():
                return False
            if opener_pi:
                return self._pi_opener_dispatch_is_feasible(
                    int(target_id), int(casting_skill_id)
                )
            return True

        if not _dispatch_target_usable():
            if claimed:
                release_interrupt_claim(target_id, casting_skill_id, reason="final_focus_changed")
            return False

        pi_dispatch = bool(int(expected_skill_id) == int(Psychic_Instability_ID))
        previous_enemy_target = int(Player.GetTargetID() or 0)

        # EXP8: hard-acquire the selected interrupt target before dispatch.
        # CastSkillSlot(target_agent_id=...) is kept as a second guard, but the
        # game client now also has the actual enemy selected while the command
        # enters activation. This addresses the EXP6 logs where valid 2-4 enemy
        # PI packets were reserved but the queued PI never started.
        try:
            if int(previous_enemy_target) != int(target_id):
                Player.ChangeTarget(int(target_id))
                # EXP14: commit the chosen interrupt target before dispatch.
                # Tasty's EXP13 observation showed correct target switching but
                # occasional missing casts. Give the client a tiny acquisition
                # window, then verify/retry once instead of immediately scanning
                # another enemy.
                yield from Routines.Yield.wait(20)
                try:
                    if int(Player.GetTargetID() or 0) != int(target_id):
                        Player.ChangeTarget(int(target_id))
                        yield from Routines.Yield.wait(20)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if int(Player.GetTargetID() or 0) != int(target_id):
                if claimed:
                    release_interrupt_claim(target_id, casting_skill_id, reason="explicit_target_acquire_failed")
                return False
        except Exception:
            pass

        # Legacy regression signature retained: exp36_stage7_e07b_claim_after_target_acquire_before_request
        # EXP36 Stage 7 / E07b: reserve the exact enemy cast only after the
        # explicit target-acquisition yield has completed, but still immediately
        # before CastSkillSlot dispatch.  Build callbacks can be restarted at
        # yield boundaries; Stage 4 posted the lock before the 20 ms target
        # acquisition yield, so the next invocation could see its own fresh
        # NON_REENTRANT claim and suppress PI.  No lock is needed while merely
        # acquiring the target.  Once acquisition is confirmed, the claim still
        # precedes the actual skill request as required by E07.
        if pi_dispatch and not claimed:
            # E07c local-owner reentry: when BuildMgr recreates this callback
            # after the final claim but before activation confirmation, reuse
            # our exact local claim instead of asking the NON_REENTRANT shared
            # lock to admit the same owner a second time.
            if self._pi_pre_dispatch_claim_matches(target_id, casting_skill_id):
                claimed = True
                self._log_rotation_event(
                    "PI_CONTROL_PRE_DISPATCH_CLAIM_REENTRY",
                    target_id=int(target_id),
                    enemy_skill_id=int(casting_skill_id),
                    our_skill_id=int(expected_skill_id),
                    source=str(source),
                    policy="exp36_stage8_e07c_reuse_owned_claim_before_request",
                )
            else:
                claim_service_failed = False
                try:
                    claimed = bool(post_interrupt_claim(
                        int(target_id), int(casting_skill_id), int(expected_skill_id)
                    ))
                except Exception:
                    # Coordination must never become a single point of failure. If
                    # the service faults, preserve the pre-E07 combat path and make
                    # that fail-open explicit in telemetry.
                    claim_service_failed = True
                    claimed = False
                if not claimed and not claim_service_failed:
                    self._log_rotation_event(
                        "PI_CONTROL_PRE_DISPATCH_CLAIM_BLOCKED",
                        target_id=int(target_id),
                        enemy_skill_id=int(casting_skill_id),
                        our_skill_id=int(expected_skill_id),
                        source=str(source),
                        policy="exp36_stage8_e07c_claim_after_target_acquire_before_request",
                    )
                    return False
                if claimed:
                    self._remember_pi_pre_dispatch_claim(target_id, casting_skill_id)
                self._log_rotation_event(
                    "PI_CONTROL_PRE_DISPATCH_CLAIM",
                    target_id=int(target_id),
                    enemy_skill_id=int(casting_skill_id),
                    our_skill_id=int(expected_skill_id),
                    source=str(source),
                    claimed=bool(claimed),
                    fail_open=bool(claim_service_failed),
                    policy="exp36_stage8_e07c_claim_after_target_acquire_before_request",
                )

        did_cast = yield from self.CastSkillSlot(
            int(slot),
            extra_condition=_dispatch_target_usable,
            log=False,
            # EXP11: do not spend a large part of a short enemy cast inside the
            # generic aftercast acknowledgement path. PI confirmation is polled
            # explicitly below; other interrupts keep the proven delay.
            aftercast_delay=(60 if pi_dispatch else 180),
            target_agent_id=int(target_id),
        )
        if not did_cast:
            if claimed:
                release_interrupt_claim(target_id, casting_skill_id, reason="cast_command_rejected")
            if pi_dispatch:
                self._clear_pi_pre_dispatch_claim_local()
            return False

        # EXP5: CastSkillSlot(True) only confirms that the command was queued.
        # Franky's EXP4 logs showed 125 PI commands accepted but 100 of them
        # never entered activation/recharge. Restoring the old enemy target
        # immediately after that acknowledgement can invalidate a still-queued
        # targeted interrupt before the game actually starts it. Keep the
        # interrupt target until activation is observable, then restore focus.
        def _interrupt_activation_seen() -> bool:
            try:
                if self._slot_recharge_remaining_ms(int(slot)) > 0:
                    return True
            except Exception:
                pass
            try:
                player_id = int(Player.GetAgentID() or 0)
                return bool(
                    player_id > 0
                    and Agent.IsCasting(player_id)
                    and int(Agent.GetCastingSkillID(player_id) or 0) == int(expected_skill_id)
                )
            except Exception:
                return False

        activation_seen = bool(_interrupt_activation_seen())
        if not activation_seen:
            # One short scheduling grace is enough for a queued 1/4s-1s
            # interrupt to enter activation without stalling the support loop.
            yield from Routines.Yield.wait(140 if pi_dispatch else 160)
            activation_seen = bool(_interrupt_activation_seen())

        if not activation_seen:
            # Never leave the PI account stuck on the temporary interrupt target
            # after a false acknowledgement.  Release immediately and let the
            # normal heal/control loop continue on the very next controller tick.
            try:
                yield from self.RestoreEnemyTarget(previous_enemy_target)
            except Exception:
                pass
            if claimed:
                release_interrupt_claim(
                    target_id, casting_skill_id, reason="cast_ack_without_activation"
                )
            if pi_dispatch:
                self._clear_pi_pre_dispatch_claim_local()
            self._log_rotation_event(
                "PI_CONTROL_CAST_ACK_NOT_ACTIVATED",
                target_id=int(target_id),
                enemy_skill_id=int(casting_skill_id),
                our_skill_id=int(expected_skill_id),
                source=str(source),
                policy="keep_interrupt_target_until_real_activation_then_release_false_ack",
            )
            return False

        if pi_dispatch:
            self._clear_pi_pre_dispatch_claim_local()

        # EXP36 Stage 4 / E07: PI ownership was already decided immediately
        # before CastSkillSlot.  Do not post a second, late claim here; this was
        # the account-only gap that allowed another controller to commit to the
        # same enemy cast while PI was already entering activation.

        try:
            yield from self.RestoreEnemyTarget(previous_enemy_target)
        except Exception:
            pass
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug

            CombatDebug.register_interrupt_fired(int(target_id), int(casting_skill_id), int(expected_skill_id))
        except Exception:
            pass
        self._log_rotation_event(
            "PI_CONTROL_INTERRUPT_CAST",
            target_id=int(target_id),
            enemy_skill_id=int(casting_skill_id),
            our_skill_id=int(expected_skill_id),
            source=str(source),
            team_anchor=int(anchor),
            claimed=bool(claimed),
            scattered_cleanup=bool(scattered_cleanup),
            dangerous_peel=bool(dangerous_peel),
            policy=(
                "opportunistic_danger_peel_restore_cluster_focus"
                if dangerous_peel
                else (
                    "pi_scattered_cast_priority_without_damage_focus_change"
                    if scattered_cleanup
                    else "pi_first_shared_packet_control"
                )
            ),
        )
        # PI timing is committed only after the caller has observed the real
        # recharge + knockdown transition. CastSkillSlot returning True is only
        # a queued-command acknowledgement.
        return True

    def _cast_opportunistic_danger_interrupt(self):
        """EXP24: mirror the proven PI/Resto HERO ready-now danger resolver.

        PI is deliberately excluded from this pool, exactly like the hero.  Only
        Complicate / Power Drain / Cry compete here.  The resolver considers
        only skills that are ready now, lets the shared ranker choose the live
        cast, claims only the winning exact activation, fires once, and returns
        to the normal rotation.  The EXP23 player-only off-packet reordering is
        intentionally removed: target selection now follows the same ranker and
        cleanup semantics as PIRestorationHeroController.
        """
        anchor, focus_members = self._focus_packet("hero_mirror_danger_probe")
        cleanup_mode = bool(anchor > 0 and len(focus_members) <= 1)

        ready_ids: list[int] = []
        for skill_id in (Complicate_ID, Power_Drain_ID, Cry_of_Frustration_ID):
            if int(skill_id or 0) <= 0 or not self.IsSkillEquipped(int(skill_id)):
                continue
            try:
                if not self.CanCastSkillID(int(skill_id)):
                    continue
            except Exception:
                continue
            ready_ids.append(int(skill_id))
        if not ready_ids:
            return False

        opportunities = rank_opportunistic_interrupt_options(
            ready_ids,
            energy_ratio=float(self._current_energy_fraction()),
            range_value=int(Range.Spellcast.value),
            origin_agent_id=int(Player.GetAgentID() or 0),
            cleanup_mode=bool(cleanup_mode),
            focus_ids=tuple(focus_members),
        )
        if not opportunities:
            return False

        focus_set = set(int(x) for x in focus_members)
        for score, target_id, enemy_skill_id, our_skill_id, mode in opportunities:
            target_id = int(target_id)
            enemy_skill_id = int(enemy_skill_id)
            our_skill_id = int(our_skill_id)
            try:
                if not (Agent.IsValid(target_id) and Agent.IsAlive(target_id)):
                    continue
            except Exception:
                continue
            if get_casting_skill_id(target_id) != enemy_skill_id:
                continue
            if not opportunistic_interrupt_is_feasible(
                target_id, our_skill_id,
                origin_agent_id=int(Player.GetAgentID() or 0),
            ):
                continue
            if not post_interrupt_claim(target_id, enemy_skill_id, our_skill_id):
                continue

            complicate_reserved = False
            if our_skill_id == int(Complicate_ID):
                if not reserve_complicate_skill(enemy_skill_id):
                    release_interrupt_claim(
                        target_id, enemy_skill_id,
                        reason="player_hero_mirror_complicate_lease_lost",
                    )
                    continue
                complicate_reserved = True

            try:
                slot = int(GLOBAL_CACHE.SkillBar.GetSlotBySkillID(our_skill_id) or 0)
            except Exception:
                slot = 0

            did_cast = yield from self._cast_interrupt_slot(
                slot=slot,
                expected_skill_id=our_skill_id,
                target_id=target_id,
                casting_skill_id=enemy_skill_id,
                anchor=int(anchor or target_id),
                claimed=True,
                source=f"hero_mirror_opportunistic_{mode}",
                # Hero controller validates the chosen live cast rather than
                # requiring it to remain in a moving shared packet. Do the same
                # for this temporary control action on the player account.
                dangerous_peel=True,
            )
            if not did_cast:
                if complicate_reserved:
                    release_complicate_skill(
                        enemy_skill_id,
                        reason="player_hero_mirror_complicate_not_fired",
                    )
                continue

            self._log_rotation_event(
                "PI_HERO_MIRROR_OPPORTUNISTIC_DANGER_INTERRUPT",
                target_id=target_id,
                enemy_skill_id=enemy_skill_id,
                our_skill_id=our_skill_id,
                mode=str(mode),
                score=round(float(score), 1),
                off_focus=bool(target_id not in focus_set),
                cleanup=bool(cleanup_mode),
                policy="same_ready_now_ranker_as_pi_resto_hero",
            )
            return True

        return False

    def _cast_pi_from_slot(self, slot: int, *, source: str):
        """EXP24: player-account PI target choice mirrors PI/Resto hero behavior.

        Behavioural parity is intentional; the local player still keeps its
        cross-tick activation confirmation because player and hero cast APIs are
        different.  Removed from the player-only path: Bed-of-Coals preference,
        unconditional any-caster PI spam and EXP23-specific target reordering.
        """
        if not (1 <= int(slot) <= 8):
            return False
        try:
            if not Routines.Checks.Skills.IsSkillSlotReady(int(slot)):
                return False
        except Exception:
            return False
        if self._current_energy_fraction() < 0.20:
            return False

        anchor, members = self._focus_packet(f"{source}_hero_mirror_target")
        if not members:
            try:
                enemy_ids = AgentArray.GetEnemyArray() or []
                enemy_ids = AgentArray.Filter.ByDistance(
                    enemy_ids, Player.GetXY(), Range.Spellcast.value
                )
                members = [
                    int(e) for e in enemy_ids
                    if int(e or 0) > 0 and Agent.IsValid(int(e)) and Agent.IsAlive(int(e))
                ]
            except Exception:
                members = []
        if not members:
            return False
        if anchor <= 0:
            anchor = int(members[0])

        # EXP25: a real packet is PI's best use.  The cluster-first call never
        # peels away from a 2+ packet merely because a single external caster is
        # attractive.  Only the single/fallback lane may peel to a healer,
        # support or dangerous caster outside the shared packet.
        cluster_lane = bool("cluster_first" in str(source))
        if not (cluster_lane and len(members) >= 2):
            off_target, off_skill, _ = self._pick_offfocus_priority_caster(
                focus_members=list(members),
                interrupter_skill_id=Psychic_Instability_ID,
                require_priority_role=True,
                claim=False,
            )
            if off_target > 0:
                packet_members = self._danger_packet_members(int(off_target)) or [int(off_target)]
                did_cast = yield from self._cast_interrupt_slot(
                    slot=int(slot),
                    expected_skill_id=Psychic_Instability_ID,
                    target_id=int(off_target),
                    casting_skill_id=int(off_skill),
                    anchor=int(anchor),
                    claimed=False,
                    source="exp25_pi_offpacket_priority",
                    dangerous_peel=True,
                )
                if did_cast:
                    self._arm_pi_confirmation(
                        slot=int(slot),
                        target_id=int(off_target),
                        casting_skill_id=int(off_skill),
                        anchor=int(anchor),
                        packet_members=packet_members,
                        publish_roj_burst=False,
                    )
                    self._log_rotation_event(
                        "PI_EXP25_OFFPACKET_PI",
                        target_id=int(off_target), enemy_skill_id=int(off_skill),
                        packet_size=len(packet_members),
                        policy="cluster_first_else_healer_support_dangerous_caster",
                    )
                    return True

        casts: list[tuple[int, int]] = []
        for enemy_id in members:
            enemy_id = int(enemy_id or 0)
            sid = int(get_casting_skill_id(enemy_id) or 0)
            if enemy_id <= 0 or sid <= 0:
                continue
            try:
                if not (Agent.IsValid(enemy_id) and Agent.IsAlive(enemy_id)):
                    continue
                if Agent.IsKnockedDown(enemy_id):
                    continue
            except Exception:
                continue
            casts.append((enemy_id, sid))
        if not casts:
            return False

        def _packet_count(target_id: int) -> int:
            return int(self._packet_near_count(int(target_id), list(members)))

        casts.sort(key=lambda item: (
            -_packet_count(int(item[0])),
            -int(is_dangerous_cast(int(item[0]))),
            -int(is_support_or_caster(int(item[0]))),
            self._distance_to_player(int(item[0])),
            int(item[0]),
        ))

        for target_id, casting_skill_id in casts:
            count = _packet_count(int(target_id))
            # Exact hero rule: a 2+ packet is inherently PI-worthy; a single
            # target must at least be dangerous/support-like.
            if count < 2 and not (
                is_dangerous_cast(int(target_id))
                or is_support_or_caster(int(target_id))
            ):
                continue
            if not opportunistic_interrupt_is_feasible(
                int(target_id), int(Psychic_Instability_ID),
                origin_agent_id=int(Player.GetAgentID() or 0),
            ):
                continue
            if get_casting_skill_id(int(target_id)) != int(casting_skill_id):
                continue

            packet_members = [
                int(eid) for eid in members
                if int(eid or 0) > 0 and Agent.IsValid(int(eid)) and Agent.IsAlive(int(eid))
            ] or [int(target_id)]
            did_cast = yield from self._cast_interrupt_slot(
                slot=int(slot),
                expected_skill_id=Psychic_Instability_ID,
                target_id=int(target_id),
                casting_skill_id=int(casting_skill_id),
                anchor=int(anchor),
                claimed=False,
                source="hero_mirror_pi_packet_shutdown",
                # Hero does not invalidate a selected PI merely because the
                # shared focus moves during dispatch; validate the exact cast.
                dangerous_peel=True,
            )
            if not did_cast:
                continue

            self._arm_pi_confirmation(
                slot=int(slot),
                target_id=int(target_id),
                casting_skill_id=int(casting_skill_id),
                anchor=int(anchor),
                packet_members=packet_members,
                publish_roj_burst=True,
            )
            self._log_rotation_event(
                "PI_HERO_MIRROR_PACKET_PI",
                target_id=int(target_id), enemy_skill_id=int(casting_skill_id),
                packet_size=len(packet_members), packet_near_count=int(count),
                policy="same_packet_then_danger_support_rule_as_pi_resto_hero",
            )
            return True

        return False

    def _optional_energy_reserve(self) -> float:
        if self._pi_ready_for_opening():
            return self._skill_energy_cost(Psychic_Instability_ID)
        return 0.0

    def _can_spend_optional_energy(self, skill_id: int) -> bool:
        return bool(
            self._current_energy_absolute() + 0.01
            >= self._skill_energy_cost(skill_id) + self._optional_energy_reserve()
        )

    def _cast_power_drain(self, *, critical_only: bool = False):
        if not self.IsSkillEquipped(Power_Drain_ID):
            return False
        if not self.CanCastSkillID(Power_Drain_ID):
            return False
        threshold = POWER_DRAIN_CRITICAL_THRESHOLD if critical_only else POWER_DRAIN_NORMAL_THRESHOLD
        if self._current_energy_fraction() > threshold:
            return False

        anchor, members = self._focus_packet("power_drain")
        if anchor <= 0:
            return False
        target_id, casting_skill_id, claimed = self._pick_offfocus_priority_caster(
            focus_members=list(members),
            interrupter_skill_id=Power_Drain_ID,
            spell_or_chant_only=True,
            require_priority_role=True,
            claim=True,
        )
        if target_id <= 0:
            target_id, casting_skill_id, claimed = self._pick_interrupt_target(
                anchor=anchor,
                members=members,
                interrupter_skill_id=Power_Drain_ID,
                spell_or_chant_only=True,
            )
        if target_id <= 0:
            return False
        slot = int(GLOBAL_CACHE.SkillBar.GetSlotBySkillID(Power_Drain_ID) or 0)
        return (
            yield from self._cast_interrupt_slot(
                slot=slot,
                expected_skill_id=Power_Drain_ID,
                target_id=target_id,
                casting_skill_id=casting_skill_id,
                anchor=anchor,
                claimed=claimed,
                source="critical_energy" if critical_only else "energy_supply",
                dangerous_peel=bool(int(target_id) not in set(int(x) for x in members)),
            )
        )

    def _cast_cry_of_frustration(self, *, multi_only: bool):
        if not self.IsSkillEquipped(Cry_of_Frustration_ID):
            return False
        if not self.CanCastSkillID(Cry_of_Frustration_ID):
            return False

        anchor, members = self._focus_packet("cry")
        if anchor <= 0:
            return False
        all_candidates = self._casting_candidates(members)

        # EXP19: Cry may peel a healer/dangerous caster outside the primary
        # damage packet. With two clusters this makes the secondary cluster's
        # support/caster the preferred shutdown target; with only one cluster,
        # the normal packet logic below remains unchanged.
        target_id, casting_skill_id, claimed = self._pick_offfocus_priority_caster(
            focus_members=list(members),
            interrupter_skill_id=Cry_of_Frustration_ID,
            require_priority_role=True,
            claim=True,
        )
        casting_count = 0
        if target_id > 0:
            casting_count = 1
            # An external dangerous/support peel is intentionally allowed even
            # in the multi-first Cry pass; do not throw it away merely because
            # it is a single caster.
        else:
            if not all_candidates:
                return False
            target_id, casting_skill_id, claimed = self._claim_focus_dangerous_cast(
                anchor=anchor,
                members=members,
                interrupter_skill_id=Cry_of_Frustration_ID,
            )
        if target_id > 0 and int(target_id) in set(int(x) for x in members):
            casting_count = self._casting_count_near(int(target_id), all_candidates)
            if multi_only and casting_count < 2:
                release_interrupt_claim(
                    int(target_id),
                    int(casting_skill_id),
                    reason="reserve_single_danger_for_complicate",
                )
                target_id = 0
                casting_skill_id = 0
                claimed = False

        if target_id <= 0:
            # Never bypass another account's dangerous-cast claim. Ordinary
            # Cry is reserved for two or more simultaneous casts in its area.
            non_dangerous = self._casting_candidates(members, include_dangerous=False)
            ranked = sorted(
                non_dangerous,
                key=lambda item: (
                    -self._casting_count_near(int(item[0]), non_dangerous),
                    -self._packet_near_count(int(item[0]), members),
                    int(item[0]),
                ),
            )
            if not ranked:
                return False
            target_id, casting_skill_id = ranked[0]
            casting_count = self._casting_count_near(int(target_id), non_dangerous)
            if casting_count < 2:
                return False
            claimed = False

        if not self._can_spend_optional_energy(Cry_of_Frustration_ID):
            if claimed:
                release_interrupt_claim(
                    int(target_id),
                    int(casting_skill_id),
                    reason="pi_energy_reserved",
                )
            return False
        cry_packet_members = list(members)
        offpacket_cry = bool(int(target_id) not in set(int(x) for x in members))
        if offpacket_cry:
            cry_packet_members = self._danger_packet_members(int(target_id)) or [int(target_id)]
        if not reserve_cry_packet(cry_packet_members):
            if claimed:
                release_interrupt_claim(
                    int(target_id),
                    int(casting_skill_id),
                    reason="cry_packet_reserved_elsewhere",
                )
            return False
        slot = int(GLOBAL_CACHE.SkillBar.GetSlotBySkillID(Cry_of_Frustration_ID) or 0)
        did_cast = yield from self._cast_interrupt_slot(
            slot=slot,
            expected_skill_id=Cry_of_Frustration_ID,
            target_id=int(target_id),
            casting_skill_id=int(casting_skill_id),
            anchor=anchor,
            claimed=bool(claimed),
            source=("offpacket_priority_peel" if offpacket_cry else ("multi_cast_after_pi" if casting_count >= 2 else "dangerous_single_fallback")),
            dangerous_peel=bool(offpacket_cry),
        )
        if not did_cast:
            release_cry_packet(cry_packet_members, reason="player_cry_not_fired")
        return bool(did_cast)

    def _cast_complicate(self):
        if not self.IsSkillEquipped(Complicate_ID):
            return False
        if not self.CanCastSkillID(Complicate_ID):
            return False

        anchor, members = self._focus_packet("complicate")
        if anchor <= 0:
            return False

        # If the other PI Mesmer is already committing PI into this packet,
        # preserve Complicate. The incoming packet knockdown has first refusal.
        if pi_packet_reserved(members):
            return False

        target_id, casting_skill_id, claimed = self._pick_offfocus_priority_caster(
            focus_members=list(members),
            interrupter_skill_id=Complicate_ID,
            require_priority_role=True,
            claim=True,
        )
        source = "offpacket_priority_disable" if target_id > 0 else "dangerous_skill_area_disable"
        if target_id <= 0:
            target_id, casting_skill_id, claimed = self._claim_focus_dangerous_cast(
                anchor=anchor,
                members=members,
                interrupter_skill_id=Complicate_ID,
            )
        if target_id <= 0:
            # PI has already received first refusal. While PI itself is ready we
            # do not burn Complicate on ordinary filler. Once PI is recharging,
            # Complicate may disable a real caster/support skill in the packet.
            if self._pi_ready_for_opening():
                return False
            ordinary_candidates = [
                (int(enemy_id), int(skill_id))
                for enemy_id, skill_id in self._casting_candidates(
                    members,
                    include_dangerous=False,
                )
                if is_support_or_caster(int(enemy_id))
                and (
                    len(members) <= 1
                    or self._packet_near_count(int(enemy_id), members) >= 2
                )
            ]
            if not ordinary_candidates:
                return False
            target_id, casting_skill_id = ordinary_candidates[0]
            # Ordinary casts used to bypass the shared interrupt whiteboard.
            # Claim them as well so hero + player PI Mesmers cannot duplicate.
            claimed = bool(post_interrupt_claim(
                int(target_id), int(casting_skill_id), int(Complicate_ID)
            ))
            if not claimed:
                return False
            source = "ordinary_support_packet_disable"

        if not self._can_spend_optional_energy(Complicate_ID):
            if claimed:
                release_interrupt_claim(
                    int(target_id),
                    int(casting_skill_id),
                    reason="pi_energy_reserved",
                )
            return False

        # Complicate disables the interrupted skill beyond the single cast. A
        # short team-wide skill lease prevents the second PI Mesmer from using
        # Complicate on the same enemy skill a few milliseconds later.
        if not reserve_complicate_skill(int(casting_skill_id)):
            if claimed:
                release_interrupt_claim(
                    int(target_id), int(casting_skill_id),
                    reason="complicate_skill_owned_by_other_mesmer",
                )
            return False

        slot = int(GLOBAL_CACHE.SkillBar.GetSlotBySkillID(Complicate_ID) or 0)
        did_cast = yield from self._cast_interrupt_slot(
            slot=slot,
            expected_skill_id=Complicate_ID,
            target_id=int(target_id),
            casting_skill_id=int(casting_skill_id),
            anchor=anchor,
            claimed=bool(claimed),
            source=str(source),
            dangerous_peel=bool(int(target_id) not in set(int(x) for x in members)),
        )
        if not did_cast:
            release_complicate_skill(int(casting_skill_id), reason="player_complicate_not_fired")
        else:
            self._log_rotation_event(
                "PI_COMPLICATE_CLAIM_COMMITTED",
                target_id=int(target_id),
                enemy_skill_id=int(casting_skill_id),
                source=str(source),
                policy="one_complicate_disable_window_across_player_and_hero",
            )
        return bool(did_cast)

    def _cast_epidemic_burning_packet(self):
        """Spread conditions from the Spirit-Burn carrier or another central conditioned foe.

        Cross-client Burning effect visibility proved unreliable.  The SoJ now
        publishes its actual Spirit Burn carrier.  We prefer that carrier once
        it is conditioned, then fall back to any conditioned central packet
        member so Epidemic remains independently useful with Fire/other sources.
        """
        if not self.IsSkillEquipped(Epidemic_ID) or not self.CanCastSkillID(Epidemic_ID):
            return False
        if not self._can_spend_optional_energy(Epidemic_ID):
            return False
        anchor, members = self._focus_packet("epidemic")
        if anchor <= 0 or len(members) < 2:
            return False
        preferred=set(active_spirit_burn_carriers(members))
        preferred.update(active_spirit_burn_targets())
        # Include published Spirit-Burn carriers even if this client picked a
        # slightly different canonical packet. Local validity, focus and
        # condition checks below prevent stale/off-packet casts.
        candidate_pool = list(dict.fromkeys([int(x) for x in preferred] + [int(x) for x in members]))
        ranked=[]
        for candidate_id in candidate_pool:
            candidate_id=int(candidate_id)
            try:
                if not (Agent.IsValid(candidate_id) and Agent.IsAlive(candidate_id)):
                    continue
                if candidate_id not in members and not self._target_still_in_focus(candidate_id, int(anchor)):
                    continue
                # Epidemic only has value after at least one condition has landed.
                if not Routines.Checks.Agents.IsConditioned(candidate_id):
                    continue
            except Exception:
                continue
            spread_count=self._packet_near_count(candidate_id, members)
            if spread_count < 2:
                continue
            health=self._health_fraction(candidate_id)
            ranked.append(((0 if candidate_id in preferred else 1, -int(spread_count), -float(health), candidate_id), candidate_id, spread_count))
        if not ranked:
            return False
        ranked.sort(key=lambda row: row[0])
        _rank,target_id,spread_count=ranked[0]
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Epidemic_ID, target_agent_id=int(target_id),
            extra_condition=lambda: (self._target_still_in_focus(int(target_id), int(anchor)) and bool(Routines.Checks.Agents.IsConditioned(int(target_id)))),
            log=False, aftercast_delay=120,
        )
        if did_cast:
            self._log_rotation_event(
                "PI_EPIDEMIC_BURNING_PACKET", target_id=int(target_id), team_anchor=int(anchor),
                packet_size=int(len(members)), spread_count=int(spread_count),
                spirit_burn_carrier=bool(target_id in preferred),
                policy="prefer_published_spirit_burn_carrier_then_any_conditioned_packet",
            )
            return True
        return False

    def _cast_technobabble(self):
        if not self.IsSkillEquipped(Technobabble_ID):
            return False
        if not self.CanCastSkillID(Technobabble_ID):
            return False
        if not (self.IsInAggro() or self.IsCloseToAggro()):
            return False
        if self._pi_ready_for_opening():
            # Keep PI's first-refusal on an enemy that is already activating a
            # skill, but do not leave Technobabble dormant just because PI is
            # ready.  If the packet is not currently casting, Technobabble may
            # pre-emptively Daze the caster cluster; PI remains ready for the
            # first real activation after the one-second Technobabble cast.
            try:
                anchor_probe, members_probe = self._focus_packet("technobabble_precheck")
                active_casts = sum(
                    1 for enemy_id in members_probe
                    if int(get_casting_skill_id(int(enemy_id)) or 0) > 0
                )
            except Exception:
                active_casts = 1
            if active_casts > 0:
                return False
        if not self._can_spend_optional_energy(Technobabble_ID):
            return False

        anchor, members = self._focus_packet("technobabble")
        if anchor <= 0 or not members:
            return False

        elapsed_since_pi = self._now_ms() - float(self._last_pi_cast_ms or 0.0)
        if int(anchor) == int(self._last_pi_anchor) and 0.0 <= elapsed_since_pi < PI_TECHNOBABBLE_DELAY_MS:
            # Let most of PI's knockdown elapse before the one-second cast so
            # Dazed remains active when the packet stands up again.
            return False

        ranked: list[tuple[int, int, int, int, int, float, float, int]] = []
        target_metrics: dict[int, tuple[int, int, int, int]] = {}
        for candidate_id in members:
            candidate_id = int(candidate_id)
            if self._is_boss(candidate_id):
                continue
            health = self._health_fraction(candidate_id)
            if health <= TECHNOBABBLE_MIN_HEALTH:
                continue
            try:
                candidate_xy = Agent.GetXY(candidate_id)
                affected = [
                    int(enemy_id)
                    for enemy_id in members
                    if Utils.Distance(candidate_xy, Agent.GetXY(int(enemy_id))) <= float(Range.Adjacent.value)
                ]
            except Exception:
                affected = [candidate_id]

            caster_count = sum(1 for enemy_id in affected if is_support_or_caster(enemy_id))
            casting_count = sum(1 for enemy_id in affected if int(get_casting_skill_id(enemy_id) or 0) > 0)
            dangerous_count = sum(1 for enemy_id in affected if is_dangerous_cast(enemy_id))

            if len(members) <= 1:
                if dangerous_count <= 0 and not is_support_or_caster(candidate_id):
                    continue
            elif caster_count < 1 and casting_count < 2 and dangerous_count <= 0:
                continue

            affected_count = len(affected)
            target_metrics[candidate_id] = (
                affected_count,
                caster_count,
                casting_count,
                dangerous_count,
            )
            ranked.append(
                (
                    -dangerous_count,
                    -casting_count,
                    -caster_count,
                    -affected_count,
                    -int(is_support_or_caster(candidate_id)),
                    -health,
                    self._distance_to_player(candidate_id),
                    candidate_id,
                )
            )

        if not ranked:
            return False
        ranked.sort()
        target_id = int(ranked[0][7])
        affected_count, caster_count, casting_count, dangerous_count = target_metrics[target_id]

        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Technobabble_ID,
            target_agent_id=target_id,
            extra_condition=lambda: (
                self._target_still_in_focus(target_id, anchor)
                and not self._is_boss(target_id)
                and self._health_fraction(target_id) > TECHNOBABBLE_MIN_HEALTH
            ),
            log=False,
            aftercast_delay=180,
        )
        if did_cast:
            self._log_rotation_event(
                "PI_TECHNOBABBLE_CAST",
                target_id=int(target_id),
                team_anchor=int(anchor),
                packet_size=len(members),
                affected_count=int(affected_count),
                caster_count=int(caster_count),
                casting_count=int(casting_count),
                dangerous_count=int(dangerous_count),
                policy="after_pi_central_non_boss_daze_one_caster_minimum",
            )
            return True
        return False

    def _party_and_nearby_ally_ids(self) -> list[int]:
        ally_ids: set[int] = set()
        try:
            for member in Party.GetPlayers() or []:
                login_number = int(getattr(member, "login_number", 0) or 0)
                agent_id = int(Party.Players.GetAgentIDByLoginNumber(login_number) or 0)
                if agent_id > 0:
                    ally_ids.add(agent_id)
        except Exception:
            pass
        try:
            for agent_id in GetAllAlliesArray(Range.Spellcast.value) or []:
                agent_id = int(agent_id or 0)
                if agent_id > 0:
                    ally_ids.add(agent_id)
        except Exception:
            pass
        return sorted(ally_ids)

    @staticmethod
    def _shared_skill_ids_by_agent() -> dict[int, set[int]]:
        result: dict[int, set[int]] = {}
        try:
            for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
                agent_data = getattr(account, "AgentData", None)
                agent_id = int(getattr(agent_data, "AgentID", 0) or 0)
                if agent_id <= 0:
                    continue
                skillbar = getattr(agent_data, "Skillbar", None)
                result[agent_id] = {
                    int(getattr(skill, "Id", 0) or 0)
                    for skill in getattr(skillbar, "Skills", ()) or ()
                    if int(getattr(skill, "Id", 0) or 0) > 0
                }
        except Exception:
            pass
        return result

    def _get_hr_and_st_agent_ids(self) -> tuple[int, int]:
        shared_skills = self._shared_skill_ids_by_agent()
        try:
            paragon_id = int(getattr(Profession.Paragon, "value", Profession.Paragon))
            ritualist_id = int(getattr(Profession.Ritualist, "value", Profession.Ritualist))
        except Exception:
            return 0, 0

        hr_candidates: list[tuple[int, float, int]] = []
        st_candidates: list[tuple[int, float, int]] = []
        for agent_id in self._party_and_nearby_ally_ids():
            if not Agent.IsValid(agent_id) or not Agent.IsAlive(agent_id):
                continue
            try:
                primary, _secondary = Agent.GetProfessions(agent_id)
                primary_id = int(getattr(primary, "value", primary) or 0)
            except Exception:
                continue
            skill_ids = shared_skills.get(agent_id, set())
            if primary_id == paragon_id:
                hr_candidates.append(
                    (
                        0 if Heroic_Refrain_ID in skill_ids else 1,
                        self._distance_to_player(agent_id),
                        int(agent_id),
                    )
                )
            elif primary_id == ritualist_id:
                has_st_effect = False
                try:
                    has_st_effect = bool(Routines.Checks.Agents.HasEffect(int(agent_id), int(Soul_Twisting_ID)))
                except Exception:
                    pass
                st_candidates.append(
                    (
                        0 if Soul_Twisting_ID in skill_ids or has_st_effect else 1,
                        self._distance_to_player(agent_id),
                        int(agent_id),
                    )
                )

        hr_candidates.sort()
        st_candidates.sort()
        hr_id = int(hr_candidates[0][2]) if hr_candidates else 0
        st_id = int(st_candidates[0][2]) if st_candidates else 0
        return hr_id, st_id

    @staticmethod
    def _has_weapon_spell(agent_id: int) -> bool:
        try:
            return bool(Agent.IsWeaponSpelled(int(agent_id)))
        except Exception:
            return False

    def _cast_splinter_on_hr_then_st(self):
        if not self.IsSkillEquipped(Splinter_Weapon_ID):
            return False
        if not (self.IsInAggro() or self.IsCloseToAggro()):
            return False
        if not self._can_spend_optional_energy(Splinter_Weapon_ID):
            return False

        slot = int(GLOBAL_CACHE.SkillBar.GetSlotBySkillID(Splinter_Weapon_ID) or 0)
        if not (1 <= slot <= 8):
            return False
        try:
            if not self.IsSharedSkillToggleEnabled(slot):
                return False
        except Exception:
            pass
        if not Routines.Checks.Skills.IsSkillSlotReady(slot):
            return False
        if not Routines.Checks.Skills.HasEnoughEnergy(Player.GetAgentID(), Splinter_Weapon_ID):
            return False

        hr_id, st_id = self._get_hr_and_st_agent_ids()
        target_id = 0
        target_role = ""
        if hr_id > 0 and not self._has_weapon_spell(hr_id):
            target_id = hr_id
            target_role = "hr_player"
        elif st_id > 0 and not self._has_weapon_spell(st_id):
            target_id = st_id
            target_role = "soul_twisting"
        if target_id <= 0:
            return False
        if self._distance_to_player(target_id) > float(Range.Spellcast.value):
            return False

        # Direct slot use avoids HeroAI's incorrect AllyMartial weapon gate.
        previous_enemy_target = int(Player.GetTargetID() or 0)
        try:
            GLOBAL_CACHE.SkillBar.UseSkill(int(slot), target_agent_id=int(target_id), aftercast_delay=180)
            self._mark_local_cast_pending(180)
            self.SetTickSuccess()
        except Exception:
            return False

        try:
            yield from self.RestoreEnemyTarget(previous_enemy_target)
        except Exception:
            pass
        self._log_rotation_event(
            "PI_SPLINTER_CAST",
            target_id=int(target_id),
            target_role=str(target_role),
            hr_id=int(hr_id),
            st_id=int(st_id),
            policy="hr_first_then_st_support_window",
        )
        return True

    def _binding_chains_target_still_valid(self, expected_anchor_id: int) -> bool:
        anchor, members = self._focus_packet("binding_chains_final_check")
        return bool(
            int(anchor) == int(expected_anchor_id)
            and len(members) >= 2
            and Agent.IsValid(int(anchor))
            and Agent.IsAlive(int(anchor))
            and not packet_has_binding_chains(members)
        )

    def _cast_binding_chains_on_focus(self):
        """Fallback snare for the ST while preserving PI/interrupt priority."""
        if not self.IsSkillEquipped(Binding_Chains_ID):
            return False
        if not self.IsInAggro() or not self.CanCastSkillID(Binding_Chains_ID):
            return False
        if not self._can_spend_optional_energy(Binding_Chains_ID):
            return False

        anchor, members = self._focus_packet("binding_chains")
        if anchor <= 0 or len(members) < 2:
            return False
        if not any(self._health_fraction(enemy_id) > 0.10 for enemy_id in members):
            return False
        reservation = reserve_binding_chains(
            anchor_id=int(anchor),
            member_ids=members,
            role="pi",
        )
        if reservation is None:
            return False

        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Binding_Chains_ID,
            target_agent_id=int(anchor),
            extra_condition=lambda: self._binding_chains_target_still_valid(int(anchor)),
            log=False,
            aftercast_delay=250,
        )
        if did_cast:
            register_binding_chains_fired(
                reservation,
                source="pi_fallback_after_control",
            )
            self._log_rotation_event(
                "PI_BINDING_CHAINS_ROJ_PACKET",
                target_id=int(anchor),
                team_anchor=int(anchor),
                packet_size=len(members),
                packet_key=int(reservation.packet_key),
                policy="st_primary_pi_delayed_fallback_after_all_control",
            )
            return True
        release_binding_chains_reservation(
            reservation,
            reason="pi_cast_command_rejected_or_focus_changed",
        )
        return False

    @staticmethod
    def _small_party_support_mode() -> bool:
        try:
            party_size = int(Party.GetPartySize() or 0)
        except Exception:
            party_size = 0
        return 2 <= party_size <= 8

    def _healing_pressure(self) -> tuple[bool, bool, float, int]:
        """Return (urgent, pressure, lowest_hp, damaged_count) for support triage."""
        now = self._now_ms()
        try:
            allies = [int(aid) for aid in (GetAllAlliesArray(Range.Spellcast.value) or []) if int(aid or 0) > 0]
        except Exception:
            allies = []
        # EXP10: healing pressure is a party resource.  Ignore allied NPCs,
        # minions, pets and spirits so they cannot steal PI/Resto decision time.
        party_ids: set[int] = set()
        try:
            for member in Party.GetPlayers() or []:
                login = int(getattr(member, "login_number", 0) or 0)
                aid = int(Party.Players.GetAgentIDByLoginNumber(login) or 0)
                if aid > 0:
                    party_ids.add(aid)
            for getter_name in ("GetHeroes", "GetHenchmen"):
                getter = getattr(Party, getter_name, None)
                if getter is None:
                    continue
                for member in getter() or []:
                    aid = int(getattr(member, "agent_id", 0) or 0)
                    if aid > 0:
                        party_ids.add(aid)
        except Exception:
            pass
        if party_ids:
            allies = [aid for aid in allies if aid in party_ids]
        player_id = int(Player.GetAgentID() or 0)
        if player_id > 0 and player_id not in allies:
            allies.append(player_id)
        lowest = 1.0
        damaged = 0
        rapid_drop = False
        live: set[int] = set()
        for aid in allies:
            try:
                if not Agent.IsValid(aid) or not Agent.IsAlive(aid):
                    continue
            except Exception:
                continue
            hp = max(0.0, min(1.0, self._health_fraction(aid)))
            live.add(aid)
            lowest = min(lowest, hp)
            if hp <= 0.74:
                damaged += 1
            previous = self._heal_hp_samples.get(aid)
            if previous is None:
                self._heal_hp_samples[aid] = (hp, now)
                continue
            prev_hp, prev_ms = previous
            age = now - float(prev_ms)
            if 250.0 <= age <= 1100.0 and float(prev_hp) - hp >= 0.08:
                rapid_drop = True
            if age >= 350.0:
                self._heal_hp_samples[aid] = (hp, now)
        for aid in list(self._heal_hp_samples):
            if aid not in live:
                self._heal_hp_samples.pop(aid, None)
        urgent = bool(lowest <= 0.60 or rapid_drop)
        pressure = bool(urgent or lowest <= 0.68 or damaged >= 2)
        return urgent, pressure, float(lowest), int(damaged)

    def _lowest_shared_heal_candidate(
        self,
        health_threshold: float,
        *,
        exclude_self: bool = False,
    ) -> tuple[int, float]:
        """Return the lowest same-party ally HP visible through shared data.

        EXP3 keeps this telemetry aligned with the Spirit Light cast policy:
        when no spirit is in earshot the healer intentionally does not select
        itself, because the sacrifice would be paid by the same low-health
        caster.
        """
        threshold = max(0.0, min(1.0, float(health_threshold)))
        try:
            allies = [
                int(aid) for aid in (GetAllAlliesArray(Range.Spellcast.value) or [])
                if int(aid or 0) > 0
            ]
        except Exception:
            allies = []
        # EXP10: telemetry/triage must match the party-only Spirit Light policy.
        party_ids: set[int] = set()
        try:
            for member in Party.GetPlayers() or []:
                login = int(getattr(member, "login_number", 0) or 0)
                aid = int(Party.Players.GetAgentIDByLoginNumber(login) or 0)
                if aid > 0:
                    party_ids.add(aid)
            for getter_name in ("GetHeroes", "GetHenchmen"):
                getter = getattr(Party, getter_name, None)
                if getter is None:
                    continue
                for member in getter() or []:
                    aid = int(getattr(member, "agent_id", 0) or 0)
                    if aid > 0:
                        party_ids.add(aid)
        except Exception:
            pass
        if party_ids:
            allies = [aid for aid in allies if aid in party_ids]
        player_id = int(Player.GetAgentID() or 0)
        if not exclude_self and player_id > 0 and player_id not in allies:
            allies.append(player_id)
        best_id = 0
        best_hp = 1.0
        for aid in allies:
            if exclude_self and int(aid) == player_id:
                continue
            try:
                if not Routines.Checks.Agents.IsAlive(int(aid)):
                    continue
                hp = float(Routines.Checks.Agents.GetHealth(int(aid)))
            except Exception:
                continue
            if hp < best_hp:
                best_id, best_hp = int(aid), float(hp)
        if best_id > 0 and best_hp < threshold:
            return int(best_id), float(best_hp)
        return 0, float(best_hp)

    def _cast_spirit_light(self, *, health_threshold: float):
        if not self.IsSkillEquipped(Spirit_Light_ID):
            return False
        if not self.CanCastSkillID(Spirit_Light_ID):
            return False
        try:
            spirit_near = bool(Routines.Agents.GetNearestSpirit(Range.Earshot.value))
        except Exception:
            spirit_near = False
        candidate_id, candidate_hp = self._lowest_shared_heal_candidate(
            float(health_threshold),
            exclude_self=not spirit_near,
        )
        did_cast = yield from self.skills.Ritualist.RestorationMagic.Spirit_Light(
            health_threshold=float(health_threshold),
        )
        if did_cast:
            self._log_rotation_event(
                "PI_SPIRIT_LIGHT_HEAL",
                threshold=round(float(health_threshold), 3),
                candidate_id=int(candidate_id),
                candidate_hp=round(float(candidate_hp), 3),
                hp_source="same_party_shared_when_available",
                policy="small_party_predictive_shared_health_direct_target_exp3",
            )
            return True
        if candidate_id > 0:
            self._log_rotation_event(
                "PI_SPIRIT_LIGHT_READY_BUT_NO_CAST",
                threshold=round(float(health_threshold), 3),
                candidate_id=int(candidate_id),
                candidate_hp=round(float(candidate_hp), 3),
                policy="ready_shared_candidate_but_cast_not_dispatched_exp3",
            )
        return False

    def _cast_mend_body_and_soul(self, *, health_threshold: float):
        """EXP12 party-only secondary heal/cleanse.

        Mend Body and Soul is deliberately used as a real sustain lane instead
        of leaving Spirit Light to carry every damaged-party decision. The
        shared party candidate is telemetry/priority context; RestorationMagic
        performs the final party-only target validation and direct cast.
        """
        if not self.IsSkillEquipped(Mend_Body_and_Soul_ID):
            return False
        if not self.CanCastSkillID(Mend_Body_and_Soul_ID):
            return False
        candidate_id, candidate_hp = self._lowest_shared_heal_candidate(
            float(health_threshold),
            exclude_self=False,
        )
        if candidate_id <= 0:
            return False
        did_cast = yield from self.skills.Ritualist.RestorationMagic.Mend_Body_and_Soul(
            health_threshold=float(health_threshold),
        )
        if did_cast:
            self._log_rotation_event(
                "PI_MEND_BODY_SOUL_HEAL",
                threshold=round(float(health_threshold), 3),
                candidate_id=int(candidate_id),
                candidate_hp=round(float(candidate_hp), 3),
                hp_source="same_party_shared_when_available",
                policy="exp12_party_only_mend_before_optional_control",
            )
            return True
        return False

    def _cast_ghostmirror_light(self, *, health_threshold: float):
        """EXP25: direct Restoration heal for the simplified PI account bar.

        Match the hero controller's intent: Ghostmirror Light is used on an
        injured *other* party member, never as offensive/control filler.
        """
        if not self.IsSkillEquipped(Ghostmirror_Light_ID):
            return False
        if not self.CanCastSkillID(Ghostmirror_Light_ID):
            return False
        candidate_id, candidate_hp = self._lowest_shared_heal_candidate(
            float(health_threshold),
            exclude_self=True,
        )
        if candidate_id <= 0:
            return False
        did_cast = yield from self.CastSkillID(
            skill_id=Ghostmirror_Light_ID,
            target_agent_id=int(candidate_id),
            log=False,
            aftercast_delay=250,
        )
        if did_cast:
            self._log_rotation_event(
                "PI_GHOSTMIRROR_LIGHT_HEAL",
                threshold=round(float(health_threshold), 3),
                candidate_id=int(candidate_id),
                candidate_hp=round(float(candidate_hp), 3),
                policy="exp25_support_only_other_party_member",
            )
            return True
        return False

    def _cast_life_prep(self):
        """EXP25: keep Life pre-loaded only in a genuine control/heal idle gap.

        Life never gets to spend the energy needed for a ready PI and never
        outranks PI, Cry, Complicate or an actual healing decision.
        """
        if not self.IsSkillEquipped(Life_ID):
            return False
        if not self.CanCastSkillID(Life_ID):
            return False
        if not (self.IsInAggro() or self.IsCloseToAggro()):
            return False
        try:
            if self.SpiritBuffExists(Life_ID):
                return False
        except Exception:
            pass
        if not self._can_spend_optional_energy(Life_ID):
            return False
        did_cast = yield from self.skills.Ritualist.RestorationMagic.Life()
        if did_cast:
            self._log_rotation_event(
                "PI_LIFE_PREP",
                policy="after_control_and_heal_before_epidemic_pi_energy_reserved",
            )
            return True
        return False

    def _cast_air_of_superiority(self):
        if not self.IsSkillEquipped(Air_of_Superiority_ID):
            return False
        if not (self.IsInAggro() or self.IsCloseToAggro()):
            return False
        if not self._can_spend_optional_energy(Air_of_Superiority_ID):
            return False
        did_cast = yield from self.skills.Any.PvE.Air_of_Superiority()
        if did_cast:
            self._log_rotation_event(
                "PI_AIR_OF_SUPERIORITY_CAST",
                policy="supported_recharge_filler_only",
            )
            return True
        return False

    # ------------------------------------------------------------------
    # EXP26 CLEAN PI SUPPORT
    #
    # This block is deliberately self-contained.  It does not call the old
    # EXP8-EXP25 player targeting/ranking methods.  Generic cast plumbing,
    # canonical TeamCombatFocus and cross-client exact interrupt claims remain
    # reused because they are infrastructure, not rotation policy.
    # ------------------------------------------------------------------

    def _pis_party_ids(self, *, range_limit: float | None = None) -> list[int]:
        """Strict party-only roster: players + heroes + henchmen, never NPCs/pets/minions."""
        ids: set[int] = set()
        try:
            for member in Party.GetPlayers() or []:
                login = int(getattr(member, "login_number", 0) or 0)
                aid = int(Party.Players.GetAgentIDByLoginNumber(login) or 0)
                if aid > 0:
                    ids.add(aid)
            for getter_name in ("GetHeroes", "GetHenchmen"):
                getter = getattr(Party, getter_name, None)
                if getter is None:
                    continue
                for member in getter() or []:
                    aid = int(getattr(member, "agent_id", 0) or 0)
                    if aid > 0:
                        ids.add(aid)
        except Exception:
            pass
        me = int(Player.GetAgentID() or 0)
        if me > 0:
            ids.add(me)
        out: list[int] = []
        for aid in ids:
            try:
                if not (Agent.IsValid(aid) and Agent.IsAlive(aid)):
                    continue
                if range_limit is not None and self._distance_to_player(aid) > float(range_limit):
                    continue
                out.append(int(aid))
            except Exception:
                continue
        return sorted(out)

    def _pis_external_allied_npc_ids(self, *, range_limit: float | None = None) -> list[int]:
        """Nearby non-party allies worth considering for secondary healing.

        Stage 20.6 relied on the HeroAI combined-ally helper, which is ultimately the
        ordinary Ally array plus pets. Live logs showed that this did not prove
        mission/summoned NPC coverage and, worse, native party heroes were being
        mislabelled as "external" because project-account membership is not the
        same thing as Guild Wars party membership.

        Stage 20.7 therefore uses the native Ally array plus the NPC/Minipet
        array, removes the complete formal Party roster, and accepts only living
        Ally/NPC entities. NPC/Minipet entries must either be exposed in the
        party window or already have lost HP; this catches escort/mission
        creatures without turning merchants/minipets into a heal sink.
        """
        now = self._now_ms()
        area = float(range_limit) if range_limit is not None else float(Range.Spellcast.value)

        if float(now) < float(self._pis_external_ally_cache_until_ms):
            cached = list(int(x) for x in self._pis_external_ally_cache_ids)
            if range_limit is None:
                return sorted(cached)
            return sorted(
                aid for aid in cached
                if self._distance_to_player(int(aid)) <= float(area)
            )

        party_ids = set(int(x) for x in self._pis_party_ids())
        candidate_ids: set[int] = set()
        raw_ally_count = 0
        raw_npc_count = 0

        try:
            raw_allies = list(AgentArray.GetAllyArray() or [])
            raw_ally_count = len(raw_allies)
            candidate_ids.update(int(x) for x in raw_allies if int(x or 0) > 0)
        except Exception:
            pass
        try:
            raw_npcs = list(AgentArray.GetNPCMinipetArray() or [])
            raw_npc_count = len(raw_npcs)
            candidate_ids.update(int(x) for x in raw_npcs if int(x or 0) > 0)
        except Exception:
            pass

        accepted: list[int] = []
        for aid in sorted(candidate_ids):
            if int(aid) in party_ids:
                continue
            try:
                if not (
                    Agent.IsValid(int(aid))
                    and Agent.IsLiving(int(aid))
                    and Agent.IsAlive(int(aid))
                ):
                    continue
                if self._distance_to_player(int(aid)) > float(area):
                    continue
                if bool(Agent.IsSpirit(int(aid))) or bool(Agent.IsMinion(int(aid))):
                    continue

                _allegiance_id, allegiance_name = Agent.GetAllegiance(int(aid))
                allegiance_name = str(allegiance_name or "")
                if allegiance_name == "Ally":
                    accepted.append(int(aid))
                    continue
                if allegiance_name != "NPC/Minipet":
                    continue
                if not bool(Agent.IsNPC(int(aid))):
                    continue

                visible_in_party_window = False
                try:
                    visible_in_party_window = bool(Agent.CanBeViewedInPartyWindow(int(aid)))
                except Exception:
                    visible_in_party_window = False
                try:
                    hp = max(0.0, min(1.0, float(Agent.GetHealth(int(aid)))))
                except Exception:
                    hp = 1.0

                # Full-health generic NPC/minipet entries are not useful heal
                # targets. Mission/escort NPCs are retained when Guild Wars
                # exposes them in the party window, or immediately once hurt.
                if not visible_in_party_window and not (0.0 < float(hp) < 0.999):
                    continue
                accepted.append(int(aid))
            except Exception:
                continue

        self._pis_external_ally_cache_ids = set(int(x) for x in accepted)
        self._pis_external_ally_cache_until_ms = float(now + 600.0)

        # Sparse live diagnostic: enough to prove which NPC agents the runtime
        # actually exposes without creating another hot logging path.
        if float(now) >= float(self._pis_external_ally_diag_until_ms):
            try:
                details: list[str] = []
                for aid in accepted[:8]:
                    try:
                        _alg_id, alg_name = Agent.GetAllegiance(int(aid))
                    except Exception:
                        alg_name = "?"
                    try:
                        model_id = int(Agent.GetModelID(int(aid)) or 0)
                    except Exception:
                        model_id = 0
                    try:
                        hp = max(0.0, min(1.0, float(Agent.GetHealth(int(aid)))))
                    except Exception:
                        hp = 1.0
                    details.append(f"{int(aid)}:{int(model_id)}:{str(alg_name)}:{float(hp):.2f}")
                self._log_rotation_event(
                    "PI_EXTERNAL_ALLY_SCAN",
                    raw_ally_count=int(raw_ally_count),
                    raw_npc_count=int(raw_npc_count),
                    formal_party_count=int(len(party_ids)),
                    accepted_count=int(len(accepted)),
                    accepted=",".join(details),
                    policy="stage20_7_native_ally_plus_npc_local_cache_600ms",
                )
            except Exception:
                pass
            self._pis_external_ally_diag_until_ms = float(now + 5000.0)

        return sorted(accepted)

    def _pis_healable_ally_ids(self, *, range_limit: float | None = None) -> list[int]:
        """Formal party plus nearby mission/summoned allied NPCs.

        The full Guild Wars party (players, heroes and henchmen) is always the
        primary lane. Only non-party allied NPCs are classified as external.
        """
        ids: set[int] = set(
            int(x) for x in self._pis_party_ids(range_limit=range_limit)
            if int(x or 0) > 0
        )
        for aid in self._pis_external_allied_npc_ids(range_limit=range_limit):
            if int(aid or 0) > 0:
                ids.add(int(aid))
        return sorted(ids)

    def _pis_hp(self, aid: int) -> float:
        try:
            return max(0.0, min(1.0, float(Routines.Checks.Agents.GetHealth(int(aid)))))
        except Exception:
            return self._health_fraction(int(aid))

    def _pis_conditioned(self, aid: int) -> bool:
        try:
            return bool(Routines.Checks.Agents.IsConditioned(int(aid)))
        except Exception:
            return False

    def _pis_project_party_ids(self) -> set[int]:
        """Active project accounts in the current Guild Wars party.

        Same-party members not represented by an active shared account become
        the external support lane. The roster is cached for 2 seconds so this
        feature does not recreate Stage-20's per-tick SharedMemory regression.
        During a transient/empty shared roster we fail safe to the old behavior
        and treat the whole party as project-owned for priority purposes.
        """
        now = self._now_ms()
        if float(now) < float(self._pis_project_roster_cache_until_ms):
            return set(int(x) for x in self._pis_project_roster_cache_ids)

        party_ids = set(int(x) for x in self._pis_party_ids())
        project_ids: set[int] = set()
        try:
            for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
                if bool(getattr(account, "IsIsolated", False)):
                    continue
                if hasattr(account, "IsSlotActive") and not bool(getattr(account, "IsSlotActive", False)):
                    continue
                agent_data = getattr(account, "AgentData", None)
                aid = int(getattr(agent_data, "AgentID", 0) or 0)
                if aid > 0 and aid in party_ids:
                    project_ids.add(aid)
        except Exception:
            project_ids.clear()

        me = int(Player.GetAgentID() or 0)
        if me > 0 and me in party_ids:
            project_ids.add(me)

        if len(party_ids) > 1 and len(project_ids) < 2:
            project_ids = set(party_ids)

        self._pis_project_roster_cache_ids = set(project_ids)
        self._pis_project_roster_cache_until_ms = float(now + 2000.0)
        return set(project_ids)

    @staticmethod
    def _pis_summarize_heal_rows(
        rows: list[tuple[int, float, bool]],
        rapid_ids: set[int],
    ) -> dict[str, object]:
        lane_rows = sorted(list(rows), key=lambda row: (float(row[1]), int(row[0])))
        lane_ids = set(int(row[0]) for row in lane_rows)
        lane_rapid = set(int(aid) for aid in rapid_ids if int(aid) in lane_ids)
        lowest_id = int(lane_rows[0][0]) if lane_rows else 0
        lowest_hp = float(lane_rows[0][1]) if lane_rows else 1.0
        damaged_74 = sum(1 for _aid, hp, _cond in lane_rows if float(hp) <= 0.74)
        damaged_68 = sum(1 for _aid, hp, _cond in lane_rows if float(hp) <= 0.68)
        rapid_low = any(int(aid) in lane_rapid and float(hp) <= 0.78 for aid, hp, _c in lane_rows)
        urgent = bool(lowest_hp <= 0.55 or rapid_low)
        needed = bool(urgent or lowest_hp <= 0.68 or (damaged_74 >= 2 and lowest_hp <= 0.72))
        return {
            "rows": lane_rows,
            "lowest_id": lowest_id,
            "lowest_hp": lowest_hp,
            "damaged_74": int(damaged_74),
            "damaged_68": int(damaged_68),
            "rapid_ids": lane_rapid,
            "urgent": urgent,
            "needed": needed,
        }

    def _pis_heal_snapshot(self) -> dict[str, object]:
        """Predictive triage: project team first, then nearby external allies."""
        now = self._now_ms()
        rows: list[tuple[int, float, bool]] = []
        rapid_ids: set[int] = set()
        live: set[int] = set()
        for aid in self._pis_healable_ally_ids(range_limit=Range.Spellcast.value):
            hp = self._pis_hp(aid)
            conditioned = self._pis_conditioned(aid)
            rows.append((int(aid), float(hp), bool(conditioned)))
            live.add(int(aid))
            prev = self._heal_hp_samples.get(int(aid))
            if prev is None:
                self._heal_hp_samples[int(aid)] = (float(hp), float(now))
            else:
                prev_hp, prev_ms = prev
                age = float(now) - float(prev_ms)
                if 250.0 <= age <= 1200.0 and float(prev_hp) - float(hp) >= 0.08:
                    rapid_ids.add(int(aid))
                if age >= 350.0:
                    self._heal_hp_samples[int(aid)] = (float(hp), float(now))
        for aid in list(self._heal_hp_samples):
            if int(aid) not in live:
                self._heal_hp_samples.pop(int(aid), None)
        for aid in list(self._pi_support_recent_heal_until):
            if float(self._pi_support_recent_heal_until.get(aid, 0.0)) <= float(now):
                self._pi_support_recent_heal_until.pop(aid, None)

        combined = self._pis_summarize_heal_rows(rows, rapid_ids)
        # Stage 20.7: "project" remains the compatibility key name, but the
        # primary lane is deliberately the COMPLETE formal Guild Wars party.
        # Native heroes/henchmen must never be misclassified as external NPCs.
        project_ids = set(int(x) for x in self._pis_party_ids())
        project_rows = [row for row in rows if int(row[0]) in project_ids]
        external_rows = [row for row in rows if int(row[0]) not in project_ids]
        combined["project"] = self._pis_summarize_heal_rows(project_rows, rapid_ids)
        combined["external_allies"] = self._pis_summarize_heal_rows(external_rows, rapid_ids)
        # Compatibility alias for Stage-20.5 telemetry/tests; contents are now
        # broader than formal non-project party members by design.
        combined["external_party"] = combined["external_allies"]
        combined["project_ids"] = set(project_ids)
        return combined

    @staticmethod
    def _pis_known_team_heal_ids() -> set[int]:
        names = (
            "Spirit_Light", "Mend_Body_and_Soul", "Ghostmirror_Light",
            "Patient_Spirit", "Dwaynas_Kiss", "Orison_of_Healing",
            "Word_of_Healing", "Infuse_Health", "Healing_Burst",
            "Heal_Other", "Divine_Healing", "Heavens_Delight",
            "Soothing_Memories", "Protective_Was_Kaolai",
        )
        out: set[int] = set()
        for name in names:
            try:
                sid = int(Skill.GetID(name) or 0)
                if sid > 0:
                    out.add(sid)
            except Exception:
                pass
        return out

    def _pis_other_heal_in_flight(self, target_id: int = 0) -> tuple[int, int, int]:
        """Return a known allied heal only when its concrete target is observable.

        EXP36 Stage 5 / Astra E04a: EXP35 treated "some healer is casting a heal"
        as enough evidence to defer PI healing.  That can abandon ally B merely
        because another healer is actually healing ally A.  Unknown cast targets
        are intentionally *not* optimistic evidence.  Phase A changes target
        attribution only; no heal-size prediction or energy reserve is added here.
        """
        me = int(Player.GetAgentID() or 0)
        wanted = int(target_id or 0)
        heal_ids = self._pis_known_team_heal_ids()
        if not heal_ids:
            return 0, 0, 0
        party_ids = set(int(x) for x in self._pis_party_ids(range_limit=Range.Earshot.value))
        ally_getter = getattr(self, "_pis_healable_ally_ids", None)
        if callable(ally_getter):
            healable_ids = set(int(x) for x in ally_getter(range_limit=Range.Earshot.value))
        else:
            healable_ids = set(party_ids)
        for aid in sorted(party_ids):
            if int(aid) == me:
                continue
            try:
                if not Agent.IsCasting(int(aid)):
                    continue
                sid = int(Agent.GetCastingSkillID(int(aid)) or 0)
                if sid not in heal_ids:
                    continue
                heal_target = int(Agent.GetTarget(int(aid)) or 0)
                # Unknown/invalid target is not proof that our chosen ally is covered.
                if heal_target <= 0 or heal_target not in healable_ids:
                    continue
                if wanted > 0 and heal_target != wanted:
                    continue
                return int(aid), int(sid), int(heal_target)
            except Exception:
                continue
        return 0, 0, 0

    def _pis_should_defer_same_target_heal(self, target_id: int, hp: float, *, urgent: bool) -> bool:
        """One-tick duplicate-heal guard for a reliably identical target only."""
        target_id = int(target_id or 0)
        if target_id <= 0 or bool(urgent) or float(hp) <= 0.58:
            return False
        other_healer, other_skill, heal_target = self._pis_other_heal_in_flight(target_id)
        if other_healer <= 0 or int(heal_target) != target_id:
            return False
        self._log_rotation_event(
            "PI_SUPPORT_HEAL_DEFER_SAME_TARGET",
            target_id=int(target_id), target_hp=round(float(hp), 3),
            other_healer_id=int(other_healer), other_heal_skill_id=int(other_skill),
            observed_heal_target_id=int(heal_target),
            policy="exp36_stage5_e04a_exact_target_only_no_unknown_optimism",
        )
        return True

    def _pis_target_recently_healed(self, aid: int, hp: float, *, urgent: bool) -> bool:
        if urgent or float(hp) <= 0.55:
            return False
        return float(self._pi_support_recent_heal_until.get(int(aid), 0.0)) > self._now_ms()

    def _pis_mark_heal(self, aid: int) -> None:
        now = self._now_ms()
        # Short hysteresis lets shared HP / other heals land before we queue a duplicate.
        self._pi_support_recent_heal_until[int(aid)] = float(now + 650.0)
        self._pi_support_last_heal_ms = float(now)

    def _pis_cast_heal_id(
        self, skill_id: int, target_id: int, *, reason: str, hp: float,
        target_scope: str = "project_party",
    ):
        if int(skill_id or 0) <= 0 or int(target_id or 0) <= 0:
            return False
        if not self.IsSkillEquipped(int(skill_id)) or not self.CanCastSkillID(int(skill_id)):
            return False
        did_cast = yield from self.CastSkillID(
            skill_id=int(skill_id), target_agent_id=int(target_id),
            log=False, aftercast_delay=220,
        )
        if did_cast:
            self._pis_mark_heal(int(target_id))
            self._log_rotation_event(
                "PI_SUPPORT_HEAL_CAST",
                our_skill_id=int(skill_id), target_id=int(target_id),
                target_hp=round(float(hp), 3), reason=str(reason),
                target_scope=str(target_scope),
                policy="stage20_7_party_priority_external_allied_npc_fallback",
            )
            return True
        return False

    def _pis_try_needed_heal(self, snapshot: dict[str, object]):
        """One heal decision per tick with project emergency priority.

        Formal party members and nearby external allied NPCs use the exact same
        heal thresholds. An urgent party heal has absolute first refusal. Only
        when the party lane is not urgent may an urgent mission/summoned ally go
        first; normal party healing still precedes normal external healing.
        """
        target_scope = "project_party"
        if "project" in snapshot or "external_allies" in snapshot or "external_party" in snapshot:
            project = dict(snapshot.get("project", {}) or {})
            external = dict(snapshot.get("external_allies", snapshot.get("external_party", {})) or {})
            if bool(project.get("urgent", False)):
                snapshot = project
            elif bool(external.get("urgent", False)):
                snapshot = external
                target_scope = "external_ally"
            elif bool(project.get("needed", False)):
                snapshot = project
            elif bool(external.get("needed", False)):
                snapshot = external
                target_scope = "external_ally"
            else:
                return False

        if not bool(snapshot.get("needed", False)):
            return False
        rows = list(snapshot.get("rows", []) or [])
        if not rows:
            return False
        urgent = bool(snapshot.get("urgent", False))
        rapid_ids = set(snapshot.get("rapid_ids", set()) or set())

        me = int(Player.GetAgentID() or 0)
        spirit_near = False
        try:
            spirit_near = bool(Routines.Agents.GetNearestSpirit(Range.Earshot.value))
        except Exception:
            spirit_near = False

        # 1) Rescue the ally who is actually in immediate danger before using a
        #    heal primarily for condition removal.  The old ordering could heal a
        #    conditioned ally in the mid-70s while an unconditioned ally sat at
        #    critical HP.  Rapid-drop urgency is target-specific and is allowed
        #    up to the same 78% ceiling used by the snapshot detector.
        rescue_rows = [
            r
            for r in rows
            if float(r[1]) <= 0.55 or (int(r[0]) in rapid_ids and float(r[1]) <= 0.78)
        ]
        for aid, hp, _cond in rescue_rows:
            target_urgent = True
            if self._pis_should_defer_same_target_heal(int(aid), float(hp), urgent=target_urgent):
                continue
            if self._pis_target_recently_healed(int(aid), float(hp), urgent=target_urgent):
                continue
            reason = "rapid_hp_drop" if int(aid) in rapid_ids and float(hp) > 0.55 else "critical_low_hp"
            if self.IsSkillEquipped(Mend_Body_and_Soul_ID) and self.CanCastSkillID(Mend_Body_and_Soul_ID):
                if (yield from self._pis_cast_heal_id(
                    Mend_Body_and_Soul_ID, int(aid), reason=reason, hp=float(hp), target_scope=str(target_scope)
                )):
                    return True
            if int(aid) != me and self.IsSkillEquipped(Ghostmirror_Light_ID) and self.CanCastSkillID(Ghostmirror_Light_ID):
                if (yield from self._pis_cast_heal_id(
                    Ghostmirror_Light_ID, int(aid), reason=reason, hp=float(hp), target_scope=str(target_scope)
                )):
                    return True
            spirit_light_allowed = bool(spirit_near or (float(hp) <= 0.55 and int(aid) != me))
            if spirit_light_allowed and self.IsSkillEquipped(Spirit_Light_ID) and self.CanCastSkillID(Spirit_Light_ID):
                if (yield from self._pis_cast_heal_id(
                    Spirit_Light_ID, int(aid), reason=reason, hp=float(hp), target_scope=str(target_scope)
                )):
                    return True

        # 2) Mend Body and Soul: wounded + conditioned gets first refusal once
        #    no critical/rapid-drop rescue is pending. One cast heals and cleanses.
        if self.IsSkillEquipped(Mend_Body_and_Soul_ID) and self.CanCastSkillID(Mend_Body_and_Soul_ID):
            conditioned = [r for r in rows if bool(r[2]) and float(r[1]) <= 0.76]
            conditioned.sort(key=lambda r: (float(r[1]), int(r[0])))
            for aid, hp, _cond in conditioned:
                target_urgent = bool(float(hp) <= 0.55 or int(aid) in rapid_ids)
                if self._pis_should_defer_same_target_heal(int(aid), float(hp), urgent=target_urgent):
                    continue
                if self._pis_target_recently_healed(int(aid), float(hp), urgent=target_urgent):
                    continue
                if (yield from self._pis_cast_heal_id(
                    Mend_Body_and_Soul_ID, int(aid), reason="wounded_conditioned", hp=float(hp), target_scope=str(target_scope)
                )):
                    return True

        # 3) Deepest injured target. MBaS is efficient/safe and has no Spirit-Light sacrifice.
        for aid, hp, _cond in rows:
            if float(hp) > (0.70 if urgent or int(aid) in rapid_ids else 0.68):
                break
            target_urgent = bool(float(hp) <= 0.55 or int(aid) in rapid_ids)
            if self._pis_should_defer_same_target_heal(int(aid), float(hp), urgent=target_urgent):
                continue
            if self._pis_target_recently_healed(int(aid), float(hp), urgent=target_urgent):
                continue
            if self.IsSkillEquipped(Mend_Body_and_Soul_ID) and self.CanCastSkillID(Mend_Body_and_Soul_ID):
                if (yield from self._pis_cast_heal_id(
                    Mend_Body_and_Soul_ID, int(aid), reason="lowest_safe_heal", hp=float(hp), target_scope=str(target_scope)
                )):
                    return True

        # 4) Ghostmirror is reserved for an injured OTHER party member. It is not
        #    used to top someone in the 70s, which keeps the mirrored heal efficient.
        for aid, hp, _cond in rows:
            ghostmirror_threshold = 0.78 if int(aid) in rapid_ids else 0.66
            if int(aid) == me or float(hp) > float(ghostmirror_threshold):
                continue
            target_urgent = bool(float(hp) <= 0.55 or int(aid) in rapid_ids)
            if self._pis_should_defer_same_target_heal(int(aid), float(hp), urgent=target_urgent):
                continue
            if self._pis_target_recently_healed(int(aid), float(hp), urgent=target_urgent):
                continue
            if self.IsSkillEquipped(Ghostmirror_Light_ID) and self.CanCastSkillID(Ghostmirror_Light_ID):
                if (yield from self._pis_cast_heal_id(
                    Ghostmirror_Light_ID, int(aid), reason="injured_other_ally", hp=float(hp), target_scope=str(target_scope)
                )):
                    return True

        # 5) Spirit Light is the fast workhorse when a spirit makes it safe.
        #    Without a spirit, only use it for a genuine emergency and never on self.
        for aid, hp, _cond in rows:
            spirit_threshold = 0.78 if spirit_near and int(aid) in rapid_ids else (0.70 if spirit_near else 0.55)
            if float(hp) > float(spirit_threshold):
                continue
            if not spirit_near and int(aid) == me:
                continue
            target_urgent = bool(float(hp) <= 0.55 or int(aid) in rapid_ids)
            if self._pis_should_defer_same_target_heal(int(aid), float(hp), urgent=target_urgent):
                continue
            if self._pis_target_recently_healed(int(aid), float(hp), urgent=target_urgent):
                continue
            if self.IsSkillEquipped(Spirit_Light_ID) and self.CanCastSkillID(Spirit_Light_ID):
                if (yield from self._pis_cast_heal_id(
                    Spirit_Light_ID, int(aid),
                    reason=("spirit_light_safe" if spirit_near else "emergency_without_spirit"),
                    hp=float(hp), target_scope=str(target_scope),
                )):
                    return True

        return False

    def _pis_life_exists(self) -> bool:
        try:
            if self.SpiritBuffExists(Life_ID):
                return True
        except Exception:
            pass
        return False

    def _pis_dynamic_heal_energy_reserve(self) -> tuple[float, str, int, float]:
        """Small E04b reserve only when a plausible near-term heal is needed.

        This is deliberately not a permanent healer tax.  It never gates PI/Cry/
        Complicate/Power Drain or an actual heal; it is consulted only by filler
        skills after those opportunities have already had first refusal.  The
        reserve is the cheapest equipped emergency heal, capped at 10 Energy.
        """
        try:
            snapshot = self._pis_heal_snapshot()
        except Exception:
            return 0.0, "snapshot_unavailable", 0, 1.0
        rows = list(snapshot.get("rows", []) or [])
        if not rows:
            return 0.0, "no_party_snapshot", 0, 1.0
        lowest_id = int(snapshot.get("lowest_id", 0) or 0)
        lowest_hp = float(snapshot.get("lowest_hp", 1.0) or 1.0)
        rapid_ids = set(snapshot.get("rapid_ids", set()) or set())
        damaged_74 = int(snapshot.get("damaged_74", 0) or 0)
        plausible_need = bool(
            lowest_hp <= 0.72
            or lowest_id in rapid_ids
            or damaged_74 >= 2
        )
        if not plausible_need:
            return 0.0, "party_stable", lowest_id, lowest_hp

        candidates: list[float] = []
        for sid in (Mend_Body_and_Soul_ID, Ghostmirror_Light_ID, Spirit_Light_ID):
            try:
                if int(sid or 0) > 0 and self.IsSkillEquipped(int(sid)):
                    cost = float(self._skill_energy_cost(int(sid)) or 0.0)
                    if cost > 0.0:
                        candidates.append(cost)
            except Exception:
                continue
        if not candidates:
            return 0.0, "no_equipped_heal_cost", lowest_id, lowest_hp
        reserve = min(10.0, max(0.0, min(candidates)))
        reason = "rapid_or_critical_risk" if (lowest_id in rapid_ids or lowest_hp <= 0.60) else "multi_or_moderate_injury"
        return float(reserve), str(reason), lowest_id, lowest_hp

    def _pis_can_spend_filler(self, skill_id: int) -> bool:
        current = self._current_energy_absolute()
        cost = self._skill_energy_cost(int(skill_id))
        pi_reserve = 0.0
        pi_slot = self._get_native_pi_slot()
        try:
            if 1 <= int(pi_slot) <= 8 and Routines.Checks.Skills.IsSkillSlotReady(int(pi_slot)):
                pi_reserve = self._skill_energy_cost(Psychic_Instability_ID)
        except Exception:
            pass
        heal_reserve, heal_reason, heal_target, heal_hp = self._pis_dynamic_heal_energy_reserve()
        # Do not add two rigid reserves together.  Holding the larger of ready-PI
        # or plausible emergency-heal cost preserves one immediate response while
        # avoiding the CC/DPS starvation Astra warned about.
        reserve = max(float(pi_reserve), float(heal_reserve))
        allowed = bool(current + 0.01 >= cost + reserve)
        if not allowed and float(heal_reserve) > float(pi_reserve) and float(heal_reserve) > 0.0:
            now_ms = float(self._now_ms())
            last_ms = float(getattr(self, "_pis_e04b_last_log_ms", 0.0) or 0.0)
            signature = (int(skill_id or 0), int(heal_target), round(float(heal_hp), 2), str(heal_reason))
            last_signature = getattr(self, "_pis_e04b_last_signature", None)
            if signature != last_signature or now_ms - last_ms >= 1200.0:
                self._pis_e04b_last_log_ms = now_ms
                self._pis_e04b_last_signature = signature
                self._log_rotation_event(
                    "PI_SUPPORT_E04B_FILLER_RESERVED",
                    filler_skill_id=int(skill_id or 0), current_energy=round(float(current), 2),
                    filler_cost=round(float(cost), 2), heal_reserve=round(float(heal_reserve), 2),
                    pi_reserve=round(float(pi_reserve), 2), target_id=int(heal_target),
                    target_hp=round(float(heal_hp), 3), reserve_reason=str(heal_reason),
                    policy="exp36_stage15_e04b_dynamic_need_based_filler_only_max_not_sum",
                )
        return allowed

    def _pis_try_life(self, *, preengage: bool = False):
        if not self.IsSkillEquipped(Life_ID) or not self.CanCastSkillID(Life_ID):
            return False
        if self._pis_life_exists():
            return False
        if not self._pis_can_spend_filler(Life_ID):
            return False
        # Life is preparation/sustain: pre-engage is preferred; in combat it only
        # happens after all heal + interrupt opportunities failed for this tick.
        did_cast = yield from self.skills.Ritualist.RestorationMagic.Life()
        if did_cast:
            self._log_rotation_event(
                "PI_SUPPORT_LIFE_CAST", preengage=bool(preengage),
                policy="maintain_life_without_spending_ready_pi_energy",
            )
            return True
        return False

    def _pis_main_packet(self) -> tuple[int, list[int]]:
        anchor, members = self._focus_packet("exp26_clean_main")
        members = [
            int(e) for e in (members or [])
            if int(e or 0) > 0 and Agent.IsValid(int(e)) and Agent.IsAlive(int(e))
        ]
        return int(anchor or 0), sorted(set(members))

    def _pis_visible_casts(self) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        try:
            enemies = AgentArray.GetEnemyArray() or []
            enemies = AgentArray.Filter.ByDistance(enemies, Player.GetXY(), Range.Spellcast.value)
        except Exception:
            enemies = []
        for eid in enemies:
            eid = int(eid or 0)
            if eid <= 0:
                continue
            try:
                if not (Agent.IsValid(eid) and Agent.IsAlive(eid) and Agent.IsCasting(eid)):
                    continue
                if Agent.IsKnockedDown(eid):
                    continue
                sid = int(Agent.GetCastingSkillID(eid) or 0)
            except Exception:
                continue
            if sid > 0:
                out.append((eid, sid))
        return out

    @staticmethod
    def _pis_enemy_prof_priority(enemy_id: int) -> int:
        """Small cleanup/shutdown role hint: Monk > Ritualist > other support/caster."""
        try:
            primary, _secondary = Agent.GetProfessionIDs(int(enemy_id))
            if int(primary) == int(Profession.Monk):
                return 3
            if int(primary) == int(Profession.Ritualist):
                return 2
        except Exception:
            pass
        try:
            return 1 if is_support_or_caster(int(enemy_id)) else 0
        except Exception:
            return 0

    def _pis_secondary_cluster_pi_candidate(
        self, main_members: list[int], casts: list[tuple[int, int]],
        danger_scores: dict[tuple[int, int], float] | None = None,
    ) -> tuple[int, int, list[int]]:
        """Return a real 2+ off-packet PI ball when the team is already burning cluster #1.

        Keystone owns the authoritative main packet and supplies repeated packet interrupts.
        PI can therefore buy more safety by knocking down a separate nearby 2+ packet.
        This is deliberately simple: only a real adjacent 2+ packet qualifies; otherwise PI
        stays on the main cluster.
        """
        main_set = set(int(x) for x in (main_members or []) if int(x or 0) > 0)
        try:
            enemies = AgentArray.GetEnemyArray() or []
            enemies = AgentArray.Filter.ByDistance(enemies, Player.GetXY(), Range.Spellcast.value)
        except Exception:
            enemies = []
        outside = [
            int(eid) for eid in enemies
            if int(eid or 0) > 0 and int(eid) not in main_set
            and Agent.IsValid(int(eid)) and Agent.IsAlive(int(eid))
        ]
        if len(outside) < 2:
            return 0, 0, []

        cast_map = {int(eid): int(sid) for eid, sid in casts if int(eid) in set(outside)}
        danger_scores = dict(danger_scores or {})
        ranked: list[tuple[tuple, int, int, list[int]]] = []
        for eid, enemy_sid in cast_map.items():
            if not opportunistic_interrupt_is_feasible(
                int(eid), int(Psychic_Instability_ID),
                origin_agent_id=int(Player.GetAgentID() or 0),
            ):
                continue
            try:
                xy = Agent.GetXY(int(eid))
                packet = [
                    int(other) for other in outside
                    if Utils.Distance(xy, Agent.GetXY(int(other))) <= float(Range.Adjacent.value)
                ]
            except Exception:
                packet = [int(eid)]
            packet = sorted(set(packet))
            if len(packet) < 2:
                continue
            danger_score = float(danger_scores.get((int(eid), int(enemy_sid)), 0.0) or 0.0)
            ranked.append((
                (
                    # EXP36 Stage 6 / Astra E08: the central dangerous-skill
                    # scorer (rez/protection/shutdown/context) wins before raw
                    # packet size. A large harmless side ball cannot steal PI.
                    -int(danger_score > 0.0),
                    -float(danger_score),
                    -int(self._pis_enemy_prof_priority(int(eid))),
                    -len(packet),
                    self._distance_to_player(int(eid)),
                    int(eid),
                ),
                int(eid), int(enemy_sid), packet,
            ))
        ranked.sort(key=lambda row: row[0])
        if not ranked:
            return 0, 0, []
        _score, target_id, enemy_sid, packet = ranked[0]
        return int(target_id), int(enemy_sid), list(packet)

    def _pis_cast_pi(self):
        """Aggressive PI against live casts in adjacent 2+ packs, cleanup included."""
        slot = self._get_native_pi_slot()
        if not (1 <= int(slot) <= 8):
            return False
        try:
            if not Routines.Checks.Skills.IsSkillSlotReady(int(slot)):
                return False
        except Exception:
            return False
        if self._current_energy_absolute() + 0.01 < self._skill_energy_cost(Psychic_Instability_ID):
            return False

        anchor, members = self._pis_main_packet()
        casts = self._pis_visible_casts()
        # Reuse the existing central danger scorer rather than inventing a new
        # Stage-6 severity table.  It already distinguishes resurrection,
        # protection, shutdown and contextual packet value.
        try:
            danger_rows = rank_opportunistic_interrupt_options(
                (int(Psychic_Instability_ID),),
                energy_ratio=float(self._current_energy_fraction()),
                range_value=int(Range.Spellcast.value),
                origin_agent_id=int(Player.GetAgentID() or 0),
                cleanup_mode=False,
                focus_ids=tuple(int(x) for x in members),
            )
        except Exception:
            danger_rows = ()
        danger_scores: dict[tuple[int, int], float] = {
            (int(target_id), int(enemy_sid)): float(score)
            for score, target_id, enemy_sid, our_skill_id, mode in danger_rows
            if int(our_skill_id) == int(Psychic_Instability_ID) and str(mode) == "dangerous"
        }

        # EXP36 Stage 6: keep EXP31's useful second-packet suppression, but
        # only after current main-packet danger and exact-cast ownership are
        # evaluated. General main-packet PI remains the final cluster fallback.
        if len(members) >= 2:
            member_set = set(int(x) for x in members)

            # EXP36 Stage 6 / Astra E08: a dangerous activation in the team's
            # main damage packet gets first refusal.  Stage 4/E07 supplies the
            # real cross-controller answer: if the exact cast is already claimed,
            # _cast_interrupt_slot declines it and PI is free to cover a second
            # packet.  This avoids the EXP31 failure mode where any 2+ side ball
            # automatically won even while an unserved dangerous main cast ran.
            main_danger: list[tuple[tuple, int, int, int, float]] = []
            for eid, enemy_sid in casts:
                danger_score = float(danger_scores.get((int(eid), int(enemy_sid)), 0.0) or 0.0)
                if int(eid) not in member_set or danger_score <= 0.0:
                    continue
                if not opportunistic_interrupt_is_feasible(
                    int(eid), int(Psychic_Instability_ID),
                    origin_agent_id=int(Player.GetAgentID() or 0),
                ):
                    continue
                adjacent = int(self._packet_near_count(int(eid), members))
                # Actual adjacent 2+ ball required, including cleanup.  Merely
                # sharing the broad team packet is insufficient.
                if adjacent < 2:
                    continue
                main_danger.append((
                    (
                        -float(danger_score),
                        -int(self._pis_enemy_prof_priority(int(eid))),
                        -int(is_support_or_caster(int(eid))),
                        -int(adjacent),
                        0 if int(eid) == int(anchor) else 1,
                        self._distance_to_player(int(eid)), int(eid),
                    ),
                    int(eid), int(enemy_sid), int(adjacent), float(danger_score),
                ))
            main_danger.sort(key=lambda x: x[0])
            attempted_main_danger: set[tuple[int, int]] = set()
            for _rank, target_id, enemy_sid, adjacent, danger_score in main_danger:
                attempted_main_danger.add((int(target_id), int(enemy_sid)))
                did = yield from self._cast_interrupt_slot(
                    slot=int(slot), expected_skill_id=Psychic_Instability_ID,
                    target_id=int(target_id), casting_skill_id=int(enemy_sid),
                    anchor=int(anchor or target_id), claimed=False,
                    source="exp36_stage6_e08_main_danger_first", dangerous_peel=True,
                )
                if not did:
                    continue
                self._arm_pi_confirmation(
                    slot=int(slot), target_id=int(target_id), casting_skill_id=int(enemy_sid),
                    anchor=int(anchor or target_id), packet_members=list(members),
                    publish_roj_burst=True,
                )
                self._log_rotation_event(
                    "PI_SUPPORT_E08_MAIN_DANGER_FIRST",
                    target_id=int(target_id), enemy_skill_id=int(enemy_sid),
                    packet_size=len(members), adjacent_count=int(adjacent),
                    main_danger_candidates=len(main_danger),
                    danger_score=round(float(danger_score), 1),
                    policy="main_danger_before_secondary_unless_exact_cast_already_covered",
                )
                self._log_rotation_event(
                    "PI_SUPPORT_PI_CAST",
                    target_id=int(target_id), enemy_skill_id=int(enemy_sid),
                    mode="main_danger", packet_size=len(members),
                    policy="exp36_stage6_e08_danger_aware_packet_choice",
                )
                return True

            # Only after every feasible dangerous main cast was either no longer
            # valid or unavailable through the Stage-4 exact-cast claim may PI
            # buy safety in a distinct 2+ side packet.
            second_target, second_skill, second_packet = self._pis_secondary_cluster_pi_candidate(
                list(members), list(casts), danger_scores=dict(danger_scores)
            )
            if second_target > 0 and len(second_packet) >= 2:
                did = yield from self._cast_interrupt_slot(
                    slot=int(slot), expected_skill_id=Psychic_Instability_ID,
                    target_id=int(second_target), casting_skill_id=int(second_skill),
                    anchor=int(second_target), claimed=False,
                    source="exp36_stage6_e08_secondary_after_main", dangerous_peel=True,
                )
                if did:
                    self._arm_pi_confirmation(
                        slot=int(slot), target_id=int(second_target), casting_skill_id=int(second_skill),
                        anchor=int(second_target), packet_members=list(second_packet),
                        publish_roj_burst=False,
                    )
                    self._log_rotation_event(
                        "PI_SUPPORT_E08_SECONDARY_AFTER_MAIN",
                        target_id=int(second_target), enemy_skill_id=int(second_skill),
                        packet_size=len(second_packet),
                        secondary_dangerous=bool(float(danger_scores.get((int(second_target), int(second_skill)), 0.0) or 0.0) > 0.0),
                        secondary_danger_score=round(float(danger_scores.get((int(second_target), int(second_skill)), 0.0) or 0.0), 1),
                        main_danger_candidates=len(main_danger),
                        policy="secondary_only_after_main_danger_claim_or_availability_check",
                    )
                    self._log_rotation_event(
                        "PI_SUPPORT_PI_CAST",
                        target_id=int(second_target), enemy_skill_id=int(second_skill),
                        mode="second_cluster", packet_size=len(second_packet),
                        policy="exp36_stage6_e08_danger_aware_packet_choice",
                    )
                    return True

            ranked: list[tuple[tuple, int, int, int]] = []
            for eid, enemy_sid in casts:
                if int(eid) not in member_set:
                    continue
                if (int(eid), int(enemy_sid)) in attempted_main_danger:
                    continue
                if not opportunistic_interrupt_is_feasible(
                    int(eid), int(Psychic_Instability_ID),
                    origin_agent_id=int(Player.GetAgentID() or 0),
                ):
                    continue
                adjacent = int(self._packet_near_count(int(eid), members))
                if adjacent < 2:
                    continue
                ranked.append((
                    (-adjacent, -int(is_dangerous_cast(int(eid))),
                     -int(is_support_or_caster(int(eid))),
                     0 if int(eid) == int(anchor) else 1,
                     self._distance_to_player(int(eid)), int(eid)),
                    int(eid), int(enemy_sid), adjacent,
                ))
            ranked.sort(key=lambda x: x[0])
            for _rank, target_id, enemy_sid, adjacent in ranked:
                did = yield from self._cast_interrupt_slot(
                    slot=int(slot), expected_skill_id=Psychic_Instability_ID,
                    target_id=int(target_id), casting_skill_id=int(enemy_sid),
                    anchor=int(anchor or target_id), claimed=False,
                    source="exp36_stage6_e08_main_general_fallback", dangerous_peel=True,
                )
                if not did:
                    continue
                self._arm_pi_confirmation(
                    slot=int(slot), target_id=int(target_id), casting_skill_id=int(enemy_sid),
                    anchor=int(anchor or target_id), packet_members=list(members),
                    publish_roj_burst=True,
                )
                self._log_rotation_event(
                    "PI_SUPPORT_PI_MAIN_CLUSTER",
                    target_id=int(target_id), enemy_skill_id=int(enemy_sid),
                    packet_size=len(members), adjacent_count=int(adjacent),
                    policy="main_cluster_general_after_e08_danger_and_secondary_checks",
                )
                self._log_rotation_event(
                    "PI_SUPPORT_PI_CAST",
                    target_id=int(target_id), enemy_skill_id=int(enemy_sid),
                    mode="main_cluster", packet_size=len(members),
                    policy="exp36_stage6_e08_danger_aware_packet_choice",
                )
                return True
            return False

        # Cleanup without a real adjacent 2+ packet: do not burn PI on a lone
        # enemy.  This preserves PI for the next pack; heals can still run.
        return False

    def _pis_secondary_candidates(self, our_skill_id: int) -> list[tuple[int, int]]:
        """EXP35 aggressive idle shutdown while PI remains first refusal.

        Cry and Complicate may interrupt any observed skill with a feasible live
        window. Power Drain is valid on any feasible spell/chant regardless of
        current Energy. Priority roles/dangerous casts still sort first, so the
        extra volume comes from previously-unused idle windows rather than from
        stealing PI or healing.
        """
        anchor, members = self._pis_main_packet()
        member_set = set(int(x) for x in members)
        rows: list[tuple[tuple, int, int]] = []
        all_casts = self._pis_visible_casts()
        # Complicate can lock an enemy skill on more than one clustered enemy.
        # Prefer a repeated skill only after danger and healer/support priority.
        # This never delays casting to wait for a second enemy.
        repeated_skill_counts: dict[int, int] = {}
        if int(our_skill_id) == int(Complicate_ID):
            for _eid, _sid in all_casts:
                repeated_skill_counts[int(_sid)] = repeated_skill_counts.get(int(_sid), 0) + 1
        for eid, enemy_sid in all_casts:
            eid = int(eid)
            enemy_sid = int(enemy_sid)
            if enemy_sid <= 0:
                continue
            if int(our_skill_id) == int(Power_Drain_ID) and not self._is_spell_or_chant(enemy_sid):
                continue
            if int(our_skill_id) == int(Signet_of_Disruption_ID) and not self._is_spell_for_disruption(enemy_sid):
                continue
            if not opportunistic_interrupt_is_feasible(
                int(eid), int(our_skill_id),
                origin_agent_id=int(Player.GetAgentID() or 0),
            ):
                continue
            danger = bool(is_dangerous_cast(int(eid)))
            support = bool(is_support_or_caster(int(eid)))
            in_main = bool(int(eid) in member_set)
            nearby_casts = int(self._casting_count_near(int(eid), all_casts))
            role_prio = int(self._pis_enemy_prof_priority(int(eid)))
            # Cry gains extra value when several enemies are acting together;
            # Complicate/Power Drain retain role/danger priority first.
            aoe_bias = -int(nearby_casts) if int(our_skill_id) == int(Cry_of_Frustration_ID) else 0
            score = (
                -int(danger),
                int(aoe_bias),
                -int(role_prio),
                -int(support),
                -int(repeated_skill_counts.get(int(enemy_sid), 0)) if int(our_skill_id) == int(Complicate_ID) else 0,
                0 if (not in_main and (danger or support or role_prio > 0)) else 1,
                -int(nearby_casts),
                0 if int(eid) == int(anchor) else 1,
                self._distance_to_player(int(eid)),
                int(eid),
            )
            rows.append((score, int(eid), int(enemy_sid)))
        rows.sort(key=lambda x: x[0])
        return [(eid, sid) for _score, eid, sid in rows]

    def _pis_try_secondary_interrupt(self, our_skill_id: int, *, label: str):
        if not self.IsSkillEquipped(int(our_skill_id)) or not self.CanCastSkillID(int(our_skill_id)):
            return False
        # PI stays unequivocally #1: when PI itself is ready, Cry/Complicate may
        # only spend Energy that leaves the native PI cost in reserve. Power
        # Drain is exempt because a successful interrupt immediately restores Energy.
        if int(our_skill_id) not in (int(Power_Drain_ID), int(Signet_of_Disruption_ID)) and not self._pis_can_spend_filler(int(our_skill_id)):
            return False
        slot = int(GLOBAL_CACHE.SkillBar.GetSlotBySkillID(int(our_skill_id)) or 0)
        if not (1 <= slot <= 8):
            return False
        anchor, _members = self._pis_main_packet()
        for target_id, enemy_sid in self._pis_secondary_candidates(int(our_skill_id)):
            # One simple cross-team rule: exact cast claim only at commit time.
            claimed = False
            claim_service_failed = False
            try:
                claimed = bool(post_interrupt_claim(int(target_id), int(enemy_sid), int(our_skill_id)))
            except Exception:
                # Fail open without pretending a lock exists. This avoids a later
                # release call touching coordination state that this account never owned.
                claim_service_failed = True
            if not claimed and not claim_service_failed:
                continue
            complicate_reserved = False
            if int(our_skill_id) == int(Complicate_ID):
                # A different PI/controller must not spend another Complicate
                # on the same enemy skill while this one is being dispatched.
                if not reserve_complicate_skill(int(enemy_sid)):
                    if claimed:
                        release_interrupt_claim(
                            int(target_id), int(enemy_sid),
                            reason="pi_complicate_skill_reserved_elsewhere",
                        )
                    continue
                complicate_reserved = True
            did = yield from self._cast_interrupt_slot(
                slot=int(slot), expected_skill_id=int(our_skill_id),
                target_id=int(target_id), casting_skill_id=int(enemy_sid),
                anchor=int(anchor or target_id), claimed=bool(claimed),
                source=f"exp26_clean_{label}", dangerous_peel=True,
            )
            if did:
                self._log_rotation_event(
                    f"PI_SUPPORT_{label.upper()}",
                    target_id=int(target_id), enemy_skill_id=int(enemy_sid),
                    dangerous=bool(is_dangerous_cast(int(target_id))),
                    support_or_caster=bool(is_support_or_caster(int(target_id))),
                    policy="dangerous_then_medium_exact_claim_at_commit",
                )
                self._log_rotation_event(
                    f"PI_SUPPORT_{label.upper()}_CAST",
                    our_skill_id=int(our_skill_id), target_id=int(target_id),
                    enemy_skill_id=int(enemy_sid),
                    policy="exp27_uniform_success_cast_telemetry",
                )
                return True
            # _cast_interrupt_slot releases its own exact cast claim on failure;
            # release the extra skill-wide reservation as well.
            if complicate_reserved:
                release_complicate_skill(int(enemy_sid), reason="pi_complicate_dispatch_failed")
        return False

    def _pis_has_live_interrupt_opportunity(self) -> bool:
        slot = self._get_native_pi_slot()
        try:
            if 1 <= int(slot) <= 8 and Routines.Checks.Skills.IsSkillSlotReady(int(slot)):
                anchor, members = self._pis_main_packet()
                casts = self._pis_visible_casts()
                if len(members) >= 2:
                    mset = set(members)
                    if any(eid in mset and opportunistic_interrupt_is_feasible(
                        eid, Psychic_Instability_ID, origin_agent_id=int(Player.GetAgentID() or 0)
                    ) for eid, _sid in casts):
                        return True
                elif any((eid == anchor or is_dangerous_cast(eid) or is_support_or_caster(eid)) and
                         opportunistic_interrupt_is_feasible(
                             eid, Psychic_Instability_ID, origin_agent_id=int(Player.GetAgentID() or 0)
                         ) for eid, _sid in casts):
                    return True
        except Exception:
            pass
        if self.IsSkillEquipped(Cry_of_Frustration_ID) and self.CanCastSkillID(Cry_of_Frustration_ID):
            if self._pis_secondary_candidates(Cry_of_Frustration_ID):
                return True
        if self.IsSkillEquipped(Complicate_ID) and self.CanCastSkillID(Complicate_ID):
            if self._pis_secondary_candidates(Complicate_ID):
                return True
        if self.IsSkillEquipped(Signet_of_Disruption_ID) and self.CanCastSkillID(Signet_of_Disruption_ID):
            if self._pis_secondary_candidates(Signet_of_Disruption_ID):
                return True
        if self.IsSkillEquipped(Power_Drain_ID) and self.CanCastSkillID(Power_Drain_ID):
            if self._pis_secondary_candidates(Power_Drain_ID):
                return True
        return False

    def _pis_fragility_coverage(
        self, target_id: int, members: list[int]
    ) -> tuple[int, tuple[int, ...]]:
        """Return (adjacent enemies, newly affected IDs) for PvE Fragility.

        PvE Fragility hexes the target AND foes adjacent to that target.
        Adjacent uses the game's adjacency radius, not the much larger
        team's packet membership. Recent predicted coverage avoids recasting
        while the game client is still reporting the new hex effects.
        """
        target_id = int(target_id or 0)
        try:
            if target_id <= 0 or not (Agent.IsValid(target_id) and Agent.IsAlive(target_id)):
                return 0, ()
            target_xy = Agent.GetXY(target_id)
        except Exception:
            return 0, ()
        now = float(self._now_ms())
        recent = (
            set(int(eid) for eid in self._pis_fragility_recent_coverage_ids)
            if now <= float(self._pis_fragility_recent_coverage_until_ms or 0.0)
            else set()
        )
        adjacent_count = 0
        newly_affected: list[int] = []
        for enemy_id in members:
            enemy_id = int(enemy_id or 0)
            try:
                if enemy_id <= 0 or not (Agent.IsValid(enemy_id) and Agent.IsAlive(enemy_id)):
                    continue
                if Utils.Distance(target_xy, Agent.GetXY(enemy_id)) > float(Range.Adjacent.value):
                    continue
                adjacent_count += 1
                if enemy_id in recent or Routines.Checks.Agents.HasEffect(enemy_id, int(Fragility_ID)):
                    continue
                newly_affected.append(enemy_id)
            except Exception:
                continue
        return adjacent_count, tuple(newly_affected)

    def _pis_fragility_target(self, *, prefer_pi_followup: bool = False) -> tuple[int, int, str]:
        """Choose the PvE Fragility AoE center with the most NEW hex coverage.

        It is only worth an idle filler cast if there are at least two
        adjacent enemies who would newly receive Fragility. Already-hexed
        central targets remain legal when casting on them reaches several
        different uncovered neighbors.
        """
        if not self.IsSkillEquipped(Fragility_ID):
            return 0, 0, "none"

        now = float(self._now_ms())
        main_anchor, main_members = self._pis_main_packet()
        members = list(main_members or [])
        anchor = int(main_anchor or 0)
        source = "team_packet_aoe_filler"
        if prefer_pi_followup and now <= float(self._pis_fragility_followup_until_ms or 0.0):
            local_anchor = int(self._pis_fragility_followup_anchor or 0)
            if local_anchor > 0 and local_anchor in members:
                try:
                    if Agent.IsValid(local_anchor) and Agent.IsAlive(local_anchor):
                        anchor = local_anchor
                        source = "pi_cast_anchor_local_aoe"
                except Exception:
                    pass
        if len(members) < 2:
            return 0, 0, source

        ranked: list[tuple[tuple, int, int]] = []
        for enemy_id in members:
            enemy_id = int(enemy_id or 0)
            near, fresh_ids = self._pis_fragility_coverage(enemy_id, members)
            fresh_count = len(fresh_ids)
            # Never waste the hex on a lone foe or on a largely covered pack.
            if near < 2 or fresh_count < 2:
                continue
            try:
                target_already_hexed = bool(Routines.Checks.Agents.HasEffect(enemy_id, int(Fragility_ID)))
                ranked.append((
                    (-fresh_count, -near, int(target_already_hexed),
                     0 if enemy_id == anchor else 1,
                     -self._pis_enemy_prof_priority(enemy_id),
                     self._distance_to_player(enemy_id), enemy_id),
                    enemy_id, fresh_count,
                ))
            except Exception:
                continue
        if not ranked:
            return 0, 0, source
        ranked.sort(key=lambda row: row[0])
        _rank, best_target, new_count = ranked[0]
        return int(best_target), int(new_count), str(source)

    def _pis_fragility_cast_still_worthwhile(self, target_id: int) -> bool:
        """Revalidate 2+ real adjacent and NEW hex recipients at dispatch."""
        near, fresh_ids = self._pis_fragility_coverage(
            int(target_id), self._pis_main_packet()[1]
        )
        return near >= 2 and len(fresh_ids) >= 2

    def _pis_try_fragility(self, *, prefer_pi_followup: bool = False):
        """Cast PvE AoE Fragility only during real heal/control idle windows."""
        if not self.IsSkillEquipped(Fragility_ID) or not self.CanCastSkillID(Fragility_ID):
            return False
        if self._pis_has_live_interrupt_opportunity():
            return False
        snapshot = self._pis_heal_snapshot()
        if bool(snapshot.get("needed", False)):
            return False
        if self._current_energy_fraction() < float(PI_FRAGILITY_MIN_ENERGY_FRACTION):
            return False
        if not self._pis_can_spend_filler(Fragility_ID):
            return False
        target_id, newly_hexed, source = self._pis_fragility_target(
            prefer_pi_followup=bool(prefer_pi_followup)
        )
        if target_id <= 0:
            return False
        did = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Fragility_ID,
            target_agent_id=int(target_id),
            extra_condition=lambda tid=int(target_id): self._pis_fragility_cast_still_worthwhile(tid),
            log=False,
            aftercast_delay=120,
        )
        if did:
            adjacent, fresh_ids = self._pis_fragility_coverage(
                int(target_id), self._pis_main_packet()[1]
            )
            # Record only positively predicted coverage for a brief grace period.
            # On client delay, this stops an immediate second, redundant cast.
            if fresh_ids:
                self._pis_fragility_recent_coverage_ids = tuple(int(e) for e in fresh_ids)
                self._pis_fragility_recent_coverage_until_ms = float(self._now_ms() + 1500.0)
            self._log_rotation_event(
                "PI_SUPPORT_FRAGILITY_CAST",
                target_id=int(target_id),
                predicted_new_hexes=int(newly_hexed),
                predicted_affected_ids=tuple(int(e) for e in fresh_ids),
                adjacent_packet_targets=int(adjacent),
                source=str(source),
                after_pi=bool(str(source).startswith("pi_cast_anchor")),
                policy="pve_fragility_aoe_max_new_hex_coverage_2plus_no_heal_pi_delay",
            )
            return True
        return False

    def _pis_try_energy_tap(self, *, critical_priority: bool = False):
        """Energy Tap: idle filler below 80%; conditional priority below 40%.

        Required healing always wins.  At critical energy avoid spending the
        last reserve on PI/hex filler; however an imminent dangerous spell
        must be interrupted before draining.
        """
        if not self.IsSkillEquipped(Energy_Tap_ID) or not self.CanCastSkillID(Energy_Tap_ID):
            return False
        if self._current_energy_fraction() > 0.80:
            return False
        if bool(self._pis_heal_snapshot().get("needed", False)):
            return False
        if critical_priority:
            if self._current_energy_fraction() > 0.40:
                return False
            # Prevent a high-risk healer/rezz spell from being sacrificed for
            # Energy Tap even at low energy.
            if any(is_dangerous_cast(int(eid)) for eid, _ in self._pis_visible_casts()):
                return False
        elif self._pis_has_live_interrupt_opportunity():
            return False
        anchor, members = self._pis_main_packet()
        candidates = [int(x) for x in members if int(x or 0) > 0]
        if int(anchor or 0) > 0 and int(anchor) not in candidates:
            candidates.insert(0, int(anchor))
        candidates = [eid for eid in candidates if Agent.IsValid(eid) and Agent.IsAlive(eid)]
        if not candidates:
            return False
        candidates.sort(key=lambda eid: (
            0 if is_support_or_caster(int(eid)) else 1,
            0 if int(eid) == int(anchor or 0) else 1,
            self._distance_to_player(int(eid)),
            int(eid),
        ))
        for target_id in candidates:
            did = yield from self.CastSkillIDAndRestoreTarget(
                skill_id=Energy_Tap_ID,
                target_agent_id=int(target_id),
                extra_condition=lambda tid=int(target_id): bool(Agent.IsValid(tid) and Agent.IsAlive(tid)),
                log=False,
                aftercast_delay=120,
            )
            if did:
                self._log_rotation_event(
                    "PI_SUPPORT_ENERGY_TAP_CAST",
                    target_id=int(target_id),
                    energy_fraction=round(float(self._current_energy_fraction()), 3),
                    policy=("critical_below_40_after_heal_danger_guard" if critical_priority
                            else "optional_low_energy_idle_support"),
                )
                return True
        return False

    def _pis_try_arcane_conundrum(self):
        """Arcane Conundrum: slow enemy spellcasting inside the shared cluster.

        PI and necessary healing/interrupts always have priority.  Prefer the
        authoritative team packet, key healers/casters and adjacent coverage;
        avoid refreshing an active Arcane Conundrum on the same target.  This
        skill remains an idle/support spell, never a wait condition for PI.
        """
        if not self.IsSkillEquipped(Arcane_Conundrum_ID):
            return False
        if not self.CanCastSkillID(Arcane_Conundrum_ID):
            return False
        if self._pis_has_live_interrupt_opportunity():
            return False
        if bool(self._pis_heal_snapshot().get("needed", False)):
            return False
        if not self._pis_can_spend_filler(Arcane_Conundrum_ID):
            return False
        anchor, members = self._pis_main_packet()
        candidates = list(members)
        if int(anchor or 0) > 0 and int(anchor) not in candidates:
            candidates.insert(0, int(anchor))
        ranked = []
        for target_id in candidates:
            try:
                if not (Agent.IsValid(int(target_id)) and Agent.IsAlive(int(target_id))):
                    continue
                # Prefer a fresh hex; a separate Fragility hex does not prevent
                # Arcane Conundrum because it is a different effect.
                if Routines.Checks.Agents.HasEffect(int(target_id), int(Arcane_Conundrum_ID)):
                    continue
                caster = bool(is_support_or_caster(int(target_id)))
                neighbors = int(self._packet_near_count(int(target_id), list(candidates)))
                if neighbors < 2:
                    continue
                ranked.append(((0 if caster else 1,
                                -neighbors,
                                0 if int(target_id) == int(anchor or 0) else 1,
                                self._distance_to_player(int(target_id)),
                                int(target_id)), int(target_id), neighbors))
            except Exception:
                continue
        if not ranked:
            return False
        ranked.sort(key=lambda r: r[0])
        _rank, target_id, neighbors = ranked[0]
        did = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Arcane_Conundrum_ID,
            target_agent_id=int(target_id),
            extra_condition=lambda tid=int(target_id): bool(
                Agent.IsValid(tid) and Agent.IsAlive(tid)
                and not Routines.Checks.Agents.HasEffect(tid, Arcane_Conundrum_ID)
                and self._packet_near_count(tid, self._pis_main_packet()[1]) >= 2
            ),
            log=False,
            aftercast_delay=120,
        )
        if did:
            self._log_rotation_event(
                "PI_SUPPORT_ARCANE_CONUNDRUM_CAST",
                target_id=int(target_id),
                packet_size=int(len(candidates)),
                adjacent_packet_members=int(max(0, neighbors - 1)),
                energy_fraction=round(float(self._current_energy_fraction()), 3),
                policy="heals_pi_intercepts_energy_then_arcane_conundrum_idle_cluster",
            )
            return True
        return False

    def _pis_try_unnatural_signet(self):
        """Optional zero-energy damage filler for the alternative PI bar.

        This is never an interrupt.  It only runs after heal/control/Life/Energy Tap
        have all had first refusal, and it stays inside the authoritative shared
        packet.  Hexed/enchanted packet members are preferred because they enable
        Unnatural Signet's adjacent splash, but the controller never leaves the
        shared packet just to chase that bonus.
        """
        if not self.IsSkillEquipped(Unnatural_Signet_ID) or not self.CanCastSkillID(Unnatural_Signet_ID):
            return False
        if self._pis_has_live_interrupt_opportunity():
            return False
        snapshot = self._pis_heal_snapshot()
        if bool(snapshot.get("needed", False)):
            return False
        anchor, members = self._pis_main_packet()
        candidates = [int(x) for x in members if int(x or 0) > 0]
        if int(anchor or 0) > 0 and int(anchor) not in candidates:
            candidates.insert(0, int(anchor))
        rows: list[tuple[tuple, int, bool]] = []
        for eid in candidates:
            try:
                if not (Agent.IsValid(eid) and Agent.IsAlive(eid)):
                    continue
                boosted = bool(Agent.IsHexed(eid) or Agent.IsEnchanted(eid))
            except Exception:
                continue
            rows.append(((
                0 if boosted else 1,
                -int(self._packet_near_count(int(eid), list(candidates))),
                0 if int(eid) == int(anchor or 0) else 1,
                self._distance_to_player(int(eid)),
                int(eid),
            ), int(eid), bool(boosted)))
        if not rows:
            return False
        rows.sort(key=lambda row: row[0])
        _rank, target_id, boosted = rows[0]
        did = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Unnatural_Signet_ID,
            target_agent_id=int(target_id),
            extra_condition=lambda tid=int(target_id): bool(Agent.IsValid(tid) and Agent.IsAlive(tid)),
            log=False,
            aftercast_delay=120,
        )
        if did:
            self._log_rotation_event(
                "PI_SUPPORT_UNNATURAL_SIGNET_CAST",
                target_id=int(target_id),
                team_anchor=int(anchor or 0),
                packet_size=len(candidates),
                splash_bonus_expected=bool(boosted),
                policy="exp36_stage18_optional_packet_bound_zero_energy_damage_filler",
            )
            return True
        return False

    def _pis_try_epidemic(self):
        """EXP35: Epidemic is allowed only after a confirmed SoJ Spirit Burn.

        There is deliberately no generic "any conditioned enemy" fallback. The
        shared carrier lease is now published only after the SoJ client observed
        Spirit Burn recharge plus newly-applied Burning on that target.
        """
        if not self.IsSkillEquipped(Epidemic_ID) or not self.CanCastSkillID(Epidemic_ID):
            return False
        if not self._pis_can_spend_filler(Epidemic_ID):
            return False
        anchor, members = self._pis_main_packet()
        member_set = set(int(x) for x in members if int(x or 0) > 0)
        if int(anchor) <= 0 or len(member_set) < 2:
            return False
        confirmed: set[int] = set()
        try:
            confirmed.update(int(x) for x in active_spirit_burn_carriers(list(member_set)) if int(x or 0) > 0)
            confirmed.update(int(x) for x in active_spirit_burn_targets() if int(x or 0) > 0)
        except Exception:
            return False
        confirmed.intersection_update(member_set)
        if not confirmed:
            return False
        try:
            epidemic_activation_ms = max(
                0, int(float(Skill.Data.GetActivation(int(Epidemic_ID)) or 0.0) * 1000.0)
            )
        except Exception:
            epidemic_activation_ms = 1000
        min_burning_remaining_ms = max(900, int(epidemic_activation_ms) + 300)

        def _new_burning_targets(tid: int) -> int:
            try:
                txy = Agent.GetXY(int(tid))
            except Exception:
                return 0
            count = 0
            for enemy_id in member_set:
                enemy_id = int(enemy_id)
                if enemy_id == int(tid):
                    continue
                try:
                    if not (Agent.IsValid(enemy_id) and Agent.IsAlive(enemy_id)):
                        continue
                    if Utils.Distance(txy, Agent.GetXY(enemy_id)) > float(Range.Adjacent.value):
                        continue
                    if Routines.Checks.Agents.HasEffect(enemy_id, Burning_ID):
                        continue
                    count += 1
                except Exception:
                    continue
            return int(count)

        ranked: list[tuple[tuple, int, int, int]] = []
        # Stage 3 diagnostic only: explain why a confirmed Spirit-Burn carrier
        # did not become a legal Epidemic cast. This does not change ranking,
        # thresholds, leases, energy policy or dispatch.
        guard_not_burning = 0
        guard_burning_too_short = 0
        guard_spread_too_small = 0
        guard_no_new_targets = 0
        for target_id in sorted(confirmed):
            try:
                if not (Agent.IsValid(target_id) and Agent.IsAlive(target_id)):
                    continue
                # EXP36/E11 requires the actual combo condition at commit time,
                # not merely "some condition" plus an old lease.
                if not Routines.Checks.Agents.HasEffect(target_id, Burning_ID):
                    guard_not_burning += 1
                    continue
                burning_remaining_ms = int(
                    GLOBAL_CACHE.Effects.GetEffectTimeRemaining(int(target_id), int(Burning_ID)) or 0
                )
            except Exception:
                continue
            if burning_remaining_ms < int(min_burning_remaining_ms):
                guard_burning_too_short += 1
                continue
            spread = int(self._packet_near_count(target_id, list(member_set)))
            new_burning = int(_new_burning_targets(int(target_id)))
            if spread < 2:
                guard_spread_too_small += 1
                continue
            if new_burning < 1:
                guard_no_new_targets += 1
                continue
            ranked.append(((-new_burning, -spread, -burning_remaining_ms, target_id), target_id, spread, burning_remaining_ms))
        ranked.sort(key=lambda x: x[0])
        if confirmed and not ranked:
            now_ms = float(self._now_ms())
            signature = (
                int(guard_not_burning),
                int(guard_burning_too_short),
                int(guard_spread_too_small),
                int(guard_no_new_targets),
            )
            last_ms = float(getattr(self, "_pis_epidemic_guard_last_log_ms", 0.0) or 0.0)
            last_signature = getattr(self, "_pis_epidemic_guard_last_signature", None)
            if signature != last_signature or now_ms - last_ms >= 1200.0:
                self._pis_epidemic_guard_last_log_ms = now_ms
                self._pis_epidemic_guard_last_signature = signature
                self._log_rotation_event(
                    "PI_SUPPORT_EPIDEMIC_GUARD",
                    confirmed_carriers=int(len(confirmed)),
                    not_burning=int(guard_not_burning),
                    burning_too_short=int(guard_burning_too_short),
                    spread_too_small=int(guard_spread_too_small),
                    no_new_burning_targets=int(guard_no_new_targets),
                    min_burning_remaining_ms=int(min_burning_remaining_ms),
                    policy="exp36_stage3_e11_observe_why_valid_spread_missing",
                )
        for _rank, target_id, spread, burning_remaining_ms in ranked:
            did = yield from self.CastSkillIDAndRestoreTarget(
                skill_id=Epidemic_ID, target_agent_id=int(target_id),
                extra_condition=lambda tid=int(target_id), minimum=int(min_burning_remaining_ms): bool(
                    tid in confirmed and Agent.IsValid(tid) and Agent.IsAlive(tid)
                    and Routines.Checks.Agents.HasEffect(tid, Burning_ID)
                    and int(GLOBAL_CACHE.Effects.GetEffectTimeRemaining(tid, Burning_ID) or 0) >= minimum
                    and _new_burning_targets(tid) >= 1
                ),
                log=False, aftercast_delay=120,
            )
            if did:
                new_burning = int(_new_burning_targets(int(target_id)))
                self._log_rotation_event(
                    "PI_SUPPORT_EPIDEMIC", target_id=int(target_id),
                    packet_size=len(member_set), spread_count=int(spread),
                    new_burning_targets=int(new_burning),
                    burning_remaining_ms=int(burning_remaining_ms),
                    spirit_burn_carrier=True,
                    policy="exp36_e11_fresh_verified_burning_with_new_spread",
                )
                self._log_rotation_event(
                    "PI_SUPPORT_EPIDEMIC_CAST", target_id=int(target_id),
                    packet_size=len(member_set), spread_count=int(spread),
                    new_burning_targets=int(new_burning),
                    burning_remaining_ms=int(burning_remaining_ms),
                    spirit_burn_carrier=True,
                    policy="exp36_e11_no_generic_condition_no_zero_value_spread",
                )
                return True
        return False

    def _run_local_skill_logic(self):
        """Heal -> PI -> live interrupts -> low-energy Energy Tap -> Arcane Conundrum -> Fragility."""
        # EXP27 is telemetry-only.  This call proves the custom controller is
        # selected and periodically keeps the PI account visible in CombatDebug.
        self._pis_log_controller_telemetry()
        try:
            from Py4GWCoreLib.Builds.Skills import TeamMovementPolicy
            TeamMovementPolicy.register_local_role("pi_support")
        except Exception:
            pass
        # EXP33: telemetry only; does not select targets, reserve casts or issue commands.
        self._pis_observe_control_casts()
        # EXP36 Stage 8 / E07c: recover only our own abandoned final-dispatch
        # claim.  This is stateful across callback recreation and therefore
        # closes the one remaining Stage-7 self-stale lease.
        self._release_stale_pi_pre_dispatch_claim()

        # A PI already in activation cannot be interrupted by another command.
        # Once activation ended, the confirmation telemetry does not freeze heals.
        if self._poll_pi_confirmation():
            return True

        refresh_aoe_danger_zones()
        if avoid_active_aoe_if_needed(role="pi_support", allow_actions_at_safe_hold=True):
            return True
        if not Routines.Checks.Skills.CanCast():
            return False

        # 1) HEAL is the primary job. Party-only, need-based, predictive and with
        #    soft coordination against another currently-casting team healer.
        snapshot = self._pis_heal_snapshot()
        if bool(snapshot.get("needed", False)):
            if (yield from self._pis_try_needed_heal(snapshot)):
                return True

        close_pressure = bool(self.IsInAggro() or self.IsCloseToAggro())
        if not close_pressure:
            return False

        # Pre-engage Life is allowed only after the heal pass and before real aggro.
        if not self.IsInAggro() and (yield from self._pis_try_life(preengage=True)):
            return True

        # Critical energy is a priority AFTER necessary heals.  Never postpone
        # an urgent observed dangerous cast solely to drain energy.
        if self._current_energy_fraction() <= 0.40:
            if (yield from self._pis_try_energy_tap(critical_priority=True)):
                return True

        # 2) PI: use casting enemies in adjacent 2+ packs, including cleanup.
        # Single-target PI is intentionally disabled on this test variant.
        if (yield from self._pis_cast_pi()):
            return True

        # 3) Secondary shutdown after PI first refusal.  Stage 19 moves the
        #    optional zero-energy Signet of Disruption ahead of Cry/Complicate
        #    so it cannot starve for an entire HM run.  Its target filter remains
        #    strict spell-only; the hexed-foe "any skill" bonus is intentionally
        #    ignored per the user's controller contract.
        if (yield from self._pis_try_secondary_interrupt(
            Signet_of_Disruption_ID, label="signet_of_disruption"
        )):
            return True
        if (yield from self._pis_try_secondary_interrupt(
            Cry_of_Frustration_ID, label="cry"
        )):
            return True
        if (yield from self._pis_try_secondary_interrupt(
            Complicate_ID, label="complicate"
        )):
            return True
        if (yield from self._pis_try_secondary_interrupt(
            Power_Drain_ID, label="power_drain"
        )):
            return True

        # Re-check healing after the interrupt scan. This catches a spike that
        # arrived during target acquisition without turning every chip hit into overheal.
        snapshot2 = self._pis_heal_snapshot()
        if bool(snapshot2.get("needed", False)):
            if (yield from self._pis_try_needed_heal(snapshot2)):
                return True

        # EXP30 optional Energy Tap is low-energy idle support only. It cannot
        # steal a live PI/Cry/Complicate/Power Drain opportunity or a needed heal.
        if not self._pis_has_live_interrupt_opportunity():
            if (yield from self._pis_try_energy_tap()):
                return True

        # 4) Arcane Conundrum is a caster-control hex: slower spells create
        #    larger PI interrupt windows. It never delays heal/PI or an actual cast.
        if not self._pis_has_live_interrupt_opportunity():
            if (yield from self._pis_try_arcane_conundrum()):
                return True

        # 5) Fragility only in genuinely idle windows; preserve the old PI
        #    packet-followup preference without making it a forced combo.
        if not self._pis_has_live_interrupt_opportunity():
            if (yield from self._pis_try_fragility(prefer_pi_followup=True)):
                return True

        # 5) Life maintenance is real support, but never steals an interrupt window.
        if not self._pis_has_live_interrupt_opportunity():
            if (yield from self._pis_try_life(preengage=False)):
                return True

        # 6) Alternative optional slot: Unnatural Signet is a packet-bound
        #    zero-energy damage filler only.  It never competes with live control.
        if not self._pis_has_live_interrupt_opportunity():
            if (yield from self._pis_try_unnatural_signet()):
                return True

        # Legacy compatibility only.  The current preferred PI bar no longer
        # carries Epidemic, but old bars remain safe if a user swaps back.
        snapshot3 = self._pis_heal_snapshot()
        if not bool(snapshot3.get("needed", False)) and not self._pis_has_live_interrupt_opportunity():
            if (yield from self._pis_try_epidemic()):
                return True

        return False
