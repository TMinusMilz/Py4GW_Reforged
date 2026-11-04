from dataclasses import dataclass

from Py4GWCoreLib import Profession
from Py4GWCoreLib import Range
from Py4GWCoreLib import Routines
from Py4GWCoreLib import Party
from Py4GWCoreLib.Builds.Any.HeroAI import HeroAI_Build
from Py4GWCoreLib import BuildMgr
from Py4GWCoreLib.Agent import Agent
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.Skill import Skill
from Py4GWCoreLib.Builds.Skills import LightbringerOptional as LBO
from Py4GWCoreLib.Skillbar import SkillBar
from Py4GWCoreLib.Builds.Skills import HexRemovalPriority, SkillsTemplate
from Py4GWCoreLib.Builds.Skills.KeystoneSoJMimicry import KeystoneSoJMimicry
from Py4GWCoreLib.Builds.Skills.AoEDangerPrediction import (
    avoid_active_aoe_if_needed,
    is_aoe_escape_safe_hold_active,
    is_position_in_active_aoe,
    refresh_aoe_danger_zones,
)
from Py4GWCoreLib.Builds.Skills.DangerInterruptClaim import (
    claim_best_dangerous_cast,
    clear_interrupt_approach_movement,
    danger_sort_key,
    get_casting_skill_id as get_danger_casting_skill_id,
    get_dangerous_casts_in_range,
    get_enemy_cast_remaining_ms,
    get_game_tick,
    interrupt_is_feasible,
    is_dangerous_cast,
    is_lethal_aoe_skill,
    has_dangerous_cast_in_range,
    mark_interrupt_approach_movement_active,
    post_interrupt_claim,
    rank_opportunistic_interrupt_options,
    release_interrupt_claim,
    target_still_casting_skill as danger_target_still_casting_skill,
)
from Py4GWCoreLib.Builds.Skills.ExecutionFocus import (
    is_execution_focus_target,
    pick_execution_focus_target,
)
from Py4GWCoreLib.Builds.Skills.CryOfFrustrationCoordination import (
    reserve_best_cry_packet,
    release_cry_reservation,
    register_cry_fired,
)

Symbolic_Celerity_ID = Skill.GetID("Symbolic_Celerity")
Symbolic_Posture_ID = Skill.GetID("Symbolic_Posture")
Keystone_Signet_ID = Skill.GetID("Keystone_Signet")
Unnatural_Signet_ID = Skill.GetID("Unnatural_Signet")
Signet_of_Clumsiness_ID = Skill.GetID("Signet_of_Clumsiness")
Smite_Hex_ID = Skill.GetID("Smite_Hex")
Hex_Eater_Signet_ID = Skill.GetID("Hex_Eater_Signet")
Castigation_Signet_ID = Skill.GetID("Castigation_Signet")
Bane_Signet_ID = Skill.GetID("Bane_Signet")
Signet_of_Rage_ID = Skill.GetID("Signet_of_Rage")
Signet_of_Mystic_Wrath_ID = Skill.GetID("Signet_of_Mystic_Wrath")
Breath_of_the_Great_Dwarf_ID = Skill.GetID("Breath_of_the_Great_Dwarf")
Blood_Ritual_ID = Skill.GetID("Blood_Ritual")
Animate_Bone_Fiend_ID = Skill.GetID("Animate_Bone_Fiend")
Animate_Bone_Horror_ID = Skill.GetID("Animate_Bone_Horror")
Animate_Bone_Minions_ID = Skill.GetID("Animate_Bone_Minions")
Animate_Flesh_Golem_ID = Skill.GetID("Animate_Flesh_Golem")
Animate_Shambling_Horror_ID = Skill.GetID("Animate_Shambling_Horror")
Animate_Vampiric_Horror_ID = Skill.GetID("Animate_Vampiric_Horror")
Death_Nova_ID = Skill.GetID("Death_Nova")
Cry_of_Frustration_ID = Skill.GetID("Cry_of_Frustration")
Power_Drain_ID = Skill.GetID("Power_Drain")
Tryptophan_Signet_ID = Skill.GetID("Tryptophan_Signet")
Signet_of_Sorrow_ID = Skill.GetID("Signet_of_Sorrow")
Signet_of_Disruption_ID = Skill.GetID("Signet_of_Disruption")
Signet_of_Distraction_ID = Skill.GetID("Signet_of_Distraction")
Signet_of_Weariness_ID = Skill.GetID("Signet_of_Weariness")
Signet_of_Corruption_Kurzick_ID = Skill.GetID("Signet_of_Corruption_kurzick")
Signet_of_Corruption_Luxon_ID = Skill.GetID("Signet_of_Corruption_luxon")
Arcane_Mimicry_ID = Skill.GetID("Arcane_Mimicry")
Signet_of_Judgment_ID = Skill.GetID("Signet_of_Judgment")
Air_of_Superiority_ID = Skill.GetID("Air_of_Superiority")
Mantra_of_Inscriptions_ID = Skill.GetID("Mantra_of_Inscriptions")
Mantra_of_Signets_ID = Skill.GetID("Mantra_of_Signets")
Mistrust_ID = Skill.GetID("Mistrust")

# POISON_KS_V1: New build auto-detects by its skillbar, never by account/name.
Barbed_Signet_ID = Skill.GetID("Barbed_Signet")
Oppressive_Gaze_ID = Skill.GetID("Oppressive_Gaze")
Epidemic_ID = Skill.GetID("Epidemic")
Signet_of_Toxic_Shock_ID = Skill.GetID("Signet_of_Toxic_Shock")
Signet_of_Deadly_Corruption_ID = Skill.GetID("Signet_of_Deadly_Corruption")
Signet_of_Lost_Souls_ID = Skill.GetID("Signet_of_Lost_Souls")


# Keystone Signet does NOT interrupt the foe directly targeted by the next
# signet. It interrupts every *other* foe adjacent to that target. Therefore an
# emergency Keystone interrupt needs a living proxy foe adjacent to the real
# dangerous caster. These foe-targeted signets are ordered by practical value;
# dedicated direct interrupts are still attempted before this proxy layer.
KEYSTONE_PROXY_INTERRUPT_SIGNET_ORDER = (
    Signet_of_Clumsiness_ID,
    Unnatural_Signet_ID,
    Signet_of_Sorrow_ID,
    Signet_of_Corruption_Kurzick_ID,
    Signet_of_Corruption_Luxon_ID,
    Signet_of_Weariness_ID,
    Tryptophan_Signet_ID,
    Castigation_Signet_ID,
    Bane_Signet_ID,
    Signet_of_Rage_ID,
    Signet_of_Mystic_Wrath_ID,
)
# Stage 9 / E14b: Keystone's adjacent proc is not instantaneous.  Require
# enough enemy cast time for the signet activation, measured ping, the normal
# final interrupt margin, and a small extra proc/queue margin.  This prevents
# the Stage-8 Urgoz pattern where 625-1219 ms windows caused target peels but
# every proxy attempt still arrived after the enemy spell completed.
KEYSTONE_E14_EXTRA_PROC_MARGIN_MS = 600
# Legacy Stage-9 regression signatures retained for cumulative validation:
# KEYSTONE_E14_EXTRA_PROC_MARGIN_MS = 220
# exp36_stage9_e14b_timing_gated_proxy_interrupt
# exp36_stage8_e14_direct_cc_first_shared_claim_then_single_proxy

# Damage fallback for copied Signet of Judgment: if every valid target is already
# knocked down, still cast it when the target packet is valuable enough. This
# value counts total alive enemies in adjacent range, including the target.
SIGNET_OF_JUDGMENT_DAMAGE_CLUSTER_MIN = 2

# Signet of Sorrow becomes much stronger once enemies are standing near a
# corpse/dead pet.  Use a conservative nearby-radius check to prefer those
# targets first, but still allow Sorrow as fallback damage when Corruption and
# other packet signets are unavailable.
SORROW_CORPSE_SEARCH_RANGE = Range.Nearby.value
SORROW_CORPSE_TARGET_SCAN_THROTTLE_MS = 250

# Short per-skill/target locks reduce three Keystone Mesmers firing the same
# packet signet on the exact same enemy at the exact same moment.  This is
# intentionally short: it staggers casts without slowing the build down.
PACKET_SIGNET_TARGET_LOCK_MS = 900
# Shared cross-skill distribution key. All normal offensive Keystone signets
# use the same target lock so different signets from different Mesmers spread
# across members of the selected packet instead of piling onto one enemy.
# Emergency direct/proxy interrupts bypass this optimization.
KEYSTONE_SIGNET_DISTRIBUTION_LOCK_ID = 0x4B535447

# v28 cross-account packet cadence.  The target lock above handles spatial
# distribution; these values add a very small temporal phase shift so four
# Keystone Mesmers do not request their signets on the exact same frame.  This
# is deliberately an optimization only: cleanup/single-target casts never wait,
# and the normal rotation never reserves a signet for a future opportunity.
KEYSTONE_SIGNET_CADENCE_EVENT_ID = 0x4B534341  # "KSCA"
KEYSTONE_SIGNET_CADENCE_EVENT_TTL_MS = 320
KEYSTONE_SIGNET_CADENCE_GAP_MS = 90
KEYSTONE_SIGNET_INITIAL_LANE_STEP_MS = 85
KEYSTONE_SIGNET_READY_EVENT_ID = 0x4B535244  # "KSRD"
KEYSTONE_SIGNET_READY_EVENT_TTL_MS = 110
KEYSTONE_SIGNET_READY_GATHER_MS = 18
KEYSTONE_SIGNET_READY_FRESH_MS = 70
KEYSTONE_SIGNET_LOW_HP_AVOID_FRACTION = 0.05  # EXP14: keep hard focus until target is effectively dead
MISTRUST_DISTRIBUTION_LOCK_ID = 0x4D495354
MISTRUST_TARGET_LOCK_MS = 6200
# SoJ uses occupancy levels instead of a single exclusive claim. This lets the
# first wave cover every cluster member once, then distributes extra casts as
# evenly as possible (3 targets / 4 casts -> 2/1/1) without ever blocking.
SOJ_BALANCED_CLAIM_BASE_ID = 0x534F4A30
SOJ_BALANCED_CLAIM_LEVELS = 8
SOJ_BALANCED_CLAIM_MS = 1750
# Dedicated <=5% execution lane: normally two Keystone Mesmers, with a third
# backup only if the dying enemy survives for at least 900 ms.  Non-owners
# remain in the SAME live combat packet.  This is independent of eSurge.
KEYSTONE_EXECUTE_HP_FRACTION = 0.05
KEYSTONE_EXECUTE_PRIMARY_LIMIT = 2
KEYSTONE_EXECUTE_BACKUP_LIMIT = 3
KEYSTONE_EXECUTE_BACKUP_AFTER_MS = 900
EXECUTION_FINISHER_LOCK_ID = 0x4558464E
EXECUTION_FINISHER_LOCK_MS = 1100

# Signet of Disruption team coordination.
# The interrupt itself is never reserved: a dangerous valid activation gets
# first refusal, otherwise the signet can be spent as useful Keystone pressure.
# Shared skillbar readiness is used to let the next ready Mesmer go first.
DISRUPTION_AVAILABILITY_LOG_MS = 3000

# Setup ranges for Keystone Mesmers. Symbolic Celerity / Symbolic Posture / Air
# can be prepared a little earlier because their duration is not consumed by
# the short final approach into the pull. Arcane Mimicry starts its 20s copied
# SoJ window immediately after the copy succeeds, so it must not be cast from
# far away. It is now allowed a bit earlier than the previous very tight 0.65
# window, so the copied SoJ is already available when the Mesmers touch the
# enemy group, without wasting too much of the 20s timer while running in.
# Offensive signets still require normal spellcast-range targets.
SYMBOLIC_PRECAST_SETUP_RANGE = float(Range.Spellcast.value) * 0.90
KEYSTONE_PRECAST_SETUP_RANGE = float(Range.Spellcast.value) * 0.78
EARLY_CELERITY_RANGE = float(Range.Spellcast.value) * 1.50
EARLY_NATIVE_KEYSTONE_RANGE = float(Range.Spellcast.value) * 1.25

# Two-stance synchronization. A Mantra should not be paid/cast only to be
# cancelled by Symbolic Posture a fraction of a second later. Once the current
# signet packet is genuinely spent, hold the Mantra if the next
# Symbolic-Posture -> Keystone window is at most this far away.
MANTRA_SYMBOLIC_SYNC_WINDOW_MS = 450

# KAY 16.10: opportunistic buff upkeep: recast only at <=20s verified
# remaining duration. A missing/stripped Symbolic Celerity gets priority
# immediately. No timer reading means NO proactive recast (fail safe).
KEYSTONE_PREBUFF_RENEW_BELOW_MS = 20_000
# KAY 18.10: protect the final six seconds of the active stance. Unlike
# proactive 20s renewals, this ready-NOW cast takes priority over ordinary
# damage signets. Never wait if the stance is cooling down, the timer is
# unknown. The current build does not equip Symbolic Posture.
KEYSTONE_MANTRA_CRITICAL_RENEW_MS = 6_000
KEYSTONE_PREBUFF_RETRY_GUARD_MS = 2_000

# Health telemetry is deliberately asynchronous and read-only: it never waits
# inside the combat rotation. We sample shortly after the signet's nominal
# activation completes. The observed delta is useful for A/B comparison but is
# explicitly not source-attributed (incoming damage/external heals can overlap).
SIGNET_HEALTH_PROBE_GRACE_MS = 250
SIGNET_HEALTH_PROBE_MIN_DELAY_MS = 350
SIGNET_HEALTH_PROBE_MAX_QUEUE = 24

# Team-wide damage focus. The Simple Power version should make Keystone Mesmers,
# the SoJ Monk and the Ineptitude Mesmer prefer the same enemy packet instead
# of splitting between two nearby groups. SoJ is treated primarily as AoE packet
# damage; fresh knockdown is a bonus, not a reason to hold the skill forever.
POWER_CLUSTER_MIN_ENEMIES = 2
POWER_CLUSTER_RADIUS = Range.Adjacent.value
POWER_CLUSTER_FILTER_RANGE = Range.Spellcast.value
# A/B experiment B only. Experiment A restores the byte-identical previous patch.
KEYSTONE_CLUSTER_DPS_AB_ENABLED = True

# Tryptophan Signet is packet-control, not opening damage. Do not fire it
# into loose runners. First application requires a compact/settled packet;
# later applications may spread it to unhexed enemies inside the same packet.
TRYPTOPHAN_OPENING_MIN_CLUSTER_ENEMIES = 2
TRYPTOPHAN_REPEAT_MIN_CLUSTER_ENEMIES = 2
TRYPTOPHAN_SETTLED_MOVING_RATIO_MAX = 0.45
TRYPTOPHAN_CLOSE_CAST_DISTANCE = Range.Nearby.value

# Elite-mission override: in Urgoz' Warren the German "Krummrinde" is
# the English "Twisted Bark". These enemies maintain room-wide effects,
# so they should be killed before the normal Simple-Power cluster logic.
ELITE_PRIORITY_TARGET_NAMES = ("twisted bark", "krummrinde", "crooked bark")


# Bounded lethal-AoE interrupt approach.  A Mesmer may close only the small
# gap needed to enter casting range; it never chases deep into the enemy group.
INTERRUPT_APPROACH_DEFAULT_MAX_EXTRA = 420.0
INTERRUPT_APPROACH_DEFAULT_LEADER_TETHER = 1200.0
INTERRUPT_APPROACH_CAST_STANDOFF = float(Range.Spellcast.value) - 70.0
INTERRUPT_APPROACH_SCAN_RANGE = float(Range.Spellcast.value) + 500.0
INTERRUPT_APPROACH_MOVE_COOLDOWN_MS = 140
INTERRUPT_APPROACH_MOVEMENT_SPEED_GW_S = 288.0
INTERRUPT_APPROACH_PATH_FACTOR = 1.15
INTERRUPT_APPROACH_FINAL_MARGIN_MS = 120
INTERRUPT_APPROACH_ARRIVAL_PADDING = 25.0

# Crash-safe dangerous-skill priority. This intentionally avoids the heavy
# generic Skill.Data / profession / AoE metadata checks from the experimental
# dangerous-interrupt version. It only compares the enemy's current casting
# skill against a fixed allow-list of high-value skills. If any API lookup fails,
# this path simply returns False and falls back to the stable normal rotation.
_SAFE_DANGER_INTERRUPT_SKILL_NAMES = (
    # Elementalist / AoE pressure
    "Meteor_Shower", "Savannah_Heat", "Searing_Heat", "Rodgorts_Invocation",
    "Rodgort's_Invocation", "Deep_Freeze", "Maelstrom", "Earthquake",
    "Churning_Earth", "Sandstorm", "Fire_Storm", "Eruption",
    "Invoke_Lightning", "Chain_Lightning", "Obsidian_Flame",
    # Mesmer shutdown / pressure
    "Panic", "Energy_Surge", "Mistrust", "Cry_of_Frustration",
    "Visions_of_Regret", "Backfire", "Empathy", "Shame", "Diversion",
    # Necromancer pressure
    "Spiteful_Spirit", "Spoil_Victor", "Mark_of_Pain", "Barbs",
    "Feast_of_Corruption", "Rising_Bile", "Putrid_Explosion",
    # Monk / Ritualist control, prot, heal, resurrection
    "Ray_of_Judgment", "Signet_of_Judgment", "Shield_of_Judgment",
    "Aegis", "Protective_Spirit", "Spirit_Bond", "Word_of_Healing",
    "Heal_Area", "Heavens_Delight", "Divine_Healing",
    "Restore_Life", "Resurrection_Chant", "Flesh_of_My_Flesh",
    "Death_Pact_Signet", "Renew_Life",
)

_SAFE_DANGER_INTERRUPT_SKILL_IDS = frozenset(
    int(skill_id)
    for skill_id in (Skill.GetID(name) for name in _SAFE_DANGER_INTERRUPT_SKILL_NAMES)
    if int(skill_id or 0) > 0
)

_SAFE_DANGER_INTERRUPT_PRIORITY = {
    skill_id: index for index, skill_id in enumerate(_SAFE_DANGER_INTERRUPT_SKILL_IDS)
}


@dataclass(slots=True)
class _KeystoneBarSnapshot:
    in_aggro: bool = False
    close_to_aggro: bool = False
    precombat_setup: bool = False
    symbolic_setup: bool = False
    has_symbolic_celerity: bool = False
    has_symbolic_posture: bool = False
    has_keystone_signet: bool = False
    enemy_casting: bool = False
    enemy_in_spellcast: bool = False
    attacking_enemy_in_spellcast: bool = False

    @property
    def symbolic_celerity_needed(self) -> bool:
        return not self.has_symbolic_celerity

    @property
    def symbolic_posture_needed(self) -> bool:
        return self.has_symbolic_celerity and not self.has_symbolic_posture

    @property
    def keystone_signet_needed(self) -> bool:
        return self.has_symbolic_celerity and not self.has_keystone_signet


@dataclass(slots=True)
class _InterruptApproachState:
    active: bool = False
    target_agent_id: int = 0
    cast_target_id: int = 0
    enemy_skill_id: int = 0
    our_skill_id: int = 0
    mode: str = "direct"
    started_tick: int = 0
    last_move_tick: int = 0
    destination: tuple[float, float] | None = None


class KeystoneSignet(BuildMgr):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Keystone Signet",
            required_primary=Profession.Mesmer,
            template_code="OQRCA8wEPle2PgpzUlEQLYQA",
            # EXP34 reliability: Keystone Signet itself is the unique hard
            # contract for this controller. Symbolic Celerity / Unnatural are
            # supported bar skills, but a transient/missing slot publication
            # must never make one Keystone account fall back to generic HeroAI.
            required_skills=[
                Keystone_Signet_ID,
            ],
            optional_skills=[
                Symbolic_Celerity_ID,
                Mantra_of_Signets_ID,
                Unnatural_Signet_ID,
                Bane_Signet_ID,
                Castigation_Signet_ID,
                Signet_of_Rage_ID,
                Signet_of_Mystic_Wrath_ID,
                LBO.GAZE_ID, LBO.SIGNET_ID,  # Optional DoA support; no bar change.
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAI_Build(standalone_fallback=True))
        self.SetSkillCastingFn(self._run_local_skill_logic)
        self.skills: SkillsTemplate = SkillsTemplate(self)
        self._soj_mimicry = KeystoneSoJMimicry()

        # Rotation state: Keystone may be pre-cast once before the pull, but the
        # reset part of the cycle should only happen after at least one real
        # non-Keystone signet has been spent. This prevents pointless Keystone
        # spam near a group and avoids resetting after zero useful signets.
        self._precombat_keystone_primed: bool = False
        self._non_keystone_signet_cast_since_last_keystone: bool = False
        self._interrupt_approach = _InterruptApproachState()
        # Zero-idle target cache: only avoids repeating the same full enemy scan
        # several times inside the same short decision window. It never blocks a
        # cast; stale/invalid targets are revalidated before use.
        self._zero_idle_cache_tick: int = 0
        self._zero_idle_cache_targets: tuple[int, ...] = ()
        self._disruption_opening_spent: bool = False

        # Signet of Disruption telemetry only; never influences combat behavior.
        self._sod_ready_since_tick: int = 0
        self._sod_was_ready: bool = False
        self._sod_last_sample_tick: int = 0
        self._sod_last_team_ready_count: int = -1

        # Full signet telemetry. Read-only instrumentation: these values never
        # participate in target selection, claims, priorities or casting.
        self._signet_ready_since: dict[int, int] = {}
        self._signet_was_ready: dict[int, bool] = {}
        self._signet_last_sample: dict[int, int] = {}
        self._mimicry_soj_equipped_since: int = 0
        self._mimicry_soj_was_equipped: bool = False
        # Rotation-stall diagnostics only.
        self._stall_last_log_tick: int = 0
        # Sorrow can instant-recharge near a corpse. Keep that turbo behavior,
        # but do not let an endless Sorrow chain starve already-ready Unnatural
        # or Corruption for 10-20 seconds (seen in live logs).
        self._sorrow_turbo_streak: int = 0

        # v28 packet phase state.  A new/disjoint enemy packet gets one tiny
        # deterministic per-account phase offset; later casts only enforce the
        # short inter-account cadence when another Keystone has just fired.
        self._keystone_last_packet_members: tuple[int, ...] = ()
        self._keystone_distribution_cache_tick: int = 0
        self._keystone_distribution_cache_emails: tuple[str, ...] = ()

        # Keystone Mantra A/B telemetry. Both Mantra of Inscriptions and
        # Mantra of Signets are supported by the same controller. Only the
        # stance actually equipped on this account is used.
        self._mantra_active_id: int = 0
        self._mantra_active_since_tick: int = 0
        self._mantra_last_profile_signet_tick: int = 0
        self._mantra_profile_signet_count: int = 0
        self._mantra_conflict_logged: bool = False
        self._mantra_sync_last_log_tick: int = 0
        # EXP20: one-shot opportunity directly after a successful Keystone reset.
        # If the equipped Mantra is immediately usable on the next decision tick,
        # cast it before the offensive signet packet. If not, consume the window
        # and continue damage immediately; this flag can never become a DPS gate.
        self._post_keystone_mantra_opportunity: bool = False
        self._prebuff_last_request_ms: dict[int, int] = {}

        # Asynchronous health probes for the Mantra-of-Signets A/B test.
        # Entries are sampled later from _run_local_skill_logic; combat flow is
        # never blocked waiting for a heal measurement.
        self._signet_health_probe_seq: int = 0
        self._pending_signet_health_probes: list[dict] = []

        # EXP34 reliability telemetry. This is intentionally read-only: the
        # controller is selected by the unique Keystone elite and these markers
        # only prove that the runtime contract stayed active on every account.
        self._keystone_controller_active_logged: bool = False
        self._keystone_controller_last_heartbeat_tick: int = 0
        # Stage 20.9: diagnostic samplers are not combat state. Throttle their
        # native/shared-memory probes while leaving every gameplay gate untouched.
        self._mantra_telemetry_next_tick: int = 0
        self._mimicry_telemetry_next_tick: int = 0
        self._sod_telemetry_next_tick: int = 0

    def ScoreMatch(
        self,
        current_primary=None,
        current_secondary=None,
        current_skills: list[int] | None = None,
    ) -> int:
        """Make a Keystone bar win build selection whenever its elite is visible.

        The old three-skill hard match could strand a single account on generic
        HeroAI when shared/native skillbar publication briefly omitted one support
        skill. Keystone Signet is a unique elite signature for this project, so it
        is sufficient and receives an explicit score bonus. HeroAI already
        re-resolves the contract whenever the local skillbar signature changes.
        """
        if current_skills is None:
            current_skills = self._get_current_skills()
        current_skill_set = {int(skill_id) for skill_id in (current_skills or []) if int(skill_id or 0) > 0}
        # Variant 3: the fifth character is a PRIMARY Mesmer / secondary Monk
        # with native Signet of Judgment and Arcane Mimicry. Its Mimicry slot
        # temporarily turns into Keystone; continue to match after the copy.
        try:
            secondary_monk = int(getattr(current_secondary, "value", current_secondary)) == int(getattr(Profession.Monk, "value", Profession.Monk))
            primary_mesmer = int(getattr(current_primary, "value", current_primary)) == int(getattr(Profession.Mesmer, "value", Profession.Mesmer))
        except Exception:
            secondary_monk = primary_mesmer = False
        if primary_mesmer and secondary_monk and self._is_soj_keystone_donor_bar(current_skill_set):
            return 1100 + len(current_skill_set)
        if int(Keystone_Signet_ID or 0) <= 0 or int(Keystone_Signet_ID) not in current_skill_set:
            return -1
        score = int(super().ScoreMatch(
            current_primary=current_primary,
            current_secondary=current_secondary,
            current_skills=list(current_skills or []),
        ))
        if score < 0:
            return -1
        return 1000 + score

    def _log_keystone_controller_reliability(self) -> None:
        """Passive proof that the dedicated Keystone controller is active."""
        now = int(get_game_tick() or 0)
        if now <= 0:
            return
        heartbeat_due = bool(
            self._keystone_controller_last_heartbeat_tick <= 0
            or now - self._keystone_controller_last_heartbeat_tick >= 5000
        )
        # Previously all eight local skillbar slots were read every HeroAI tick
        # even though the proof heartbeat is only emitted every five seconds.
        if self._keystone_controller_active_logged and not heartbeat_due:
            return
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            bar_ids = [
                int(SkillBar.GetSkillIDBySlot(slot) or 0)
                for slot in range(1, 9)
            ]
            bar_ids = [skill_id for skill_id in bar_ids if skill_id > 0]
            if not self._keystone_controller_active_logged:
                self._keystone_controller_active_logged = True
                CombatDebug.log_event(
                    "KEYSTONE_CONTROLLER_ACTIVE",
                    match_policy="primary_mesmer_plus_keystone_elite",
                    keystone_id=int(Keystone_Signet_ID),
                    bar_skill_ids=",".join(str(skill_id) for skill_id in bar_ids),
                )
                CombatDebug.log_event(
                    "KEYSTONE_BAR_MATCHED",
                    matched=bool(int(Keystone_Signet_ID) in bar_ids),
                    has_symbolic_celerity=bool(int(Symbolic_Celerity_ID) in bar_ids),
                    has_unnatural_signet=bool(int(Unnatural_Signet_ID) in bar_ids),
                    visible_skill_count=int(len(bar_ids)),
                    match_policy="keystone_elite_only_support_skills_optional",
                )
            if heartbeat_due:
                self._keystone_controller_last_heartbeat_tick = now
                CombatDebug.log_event(
                    "KEYSTONE_CONTROLLER_HEARTBEAT",
                    keystone_equipped=bool(int(Keystone_Signet_ID) in bar_ids),
                    visible_skill_count=int(len(bar_ids)),
                    policy="exp34_runtime_contract_proof",
                )
        except Exception:
            pass

    def _has_enemy_near_position(self, position: tuple[float, float] | None, max_distance: float) -> bool:
        if not position:
            return False

        max_distance_sq = max_distance * max_distance
        px, py = position

        try:
            from Py4GWCoreLib import AgentArray

            for enemy_id in AgentArray.GetEnemyArray() or []:
                try:
                    if not Agent.IsValid(enemy_id) or not Agent.IsAlive(enemy_id):
                        continue

                    enemy_pos = Agent.GetXY(enemy_id)
                    if not enemy_pos:
                        continue

                    dx = px - enemy_pos[0]
                    dy = py - enemy_pos[1]
                    if (dx * dx + dy * dy) <= max_distance_sq:
                        return True
                except Exception:
                    continue
        except Exception:
            return False

        return False

    def _has_enemy_near_player_or_leader(self, max_distance: float) -> bool:
        if self._has_enemy_near_position(Player.GetXY(), max_distance):
            return True

        try:
            from Py4GWCoreLib.Party import Party

            leader_id = int(Party.GetPartyLeaderID() or 0)
            if leader_id > 0 and Agent.IsValid(leader_id):
                if self._has_enemy_near_position(Agent.GetXY(leader_id), max_distance):
                    return True
        except Exception:
            pass

        return False

    def _is_symbolic_setup_needed(self) -> bool:
        # Symbolic Celerity, Symbolic Posture and Air of Superiority should be
        # prepared shortly before a pull, but not maintained permanently while
        # walking around with no enemy pressure nearby.
        if self.IsInAggro() or self.IsCloseToAggro():
            return True
        return self._has_enemy_near_player_or_leader(SYMBOLIC_PRECAST_SETUP_RANGE)

    def _is_precombat_setup_needed(self) -> bool:
        # Arcane Mimicry and the first Keystone prime are deliberately later
        # than the Symbolic/Air setup stage, but not as late as the previous
        # ultra-tight window. Arcane Mimicry copies immediately and the 20s SoJ
        # timer starts right away, so this is a near-contact compromise: early
        # enough to have copied SoJ ready on first contact, late enough to avoid
        # burning the timer while still running across open space.
        if self.IsInAggro() or self.IsCloseToAggro():
            return True
        return self._has_enemy_near_player_or_leader(KEYSTONE_PRECAST_SETUP_RANGE)

    def _get_bar_snapshot(self) -> _KeystoneBarSnapshot:
        player_id = Player.GetAgentID()
        in_aggro = bool(self.IsInAggro())
        close_to_aggro = in_aggro or self.IsCloseToAggro()
        symbolic_setup = close_to_aggro or self._is_symbolic_setup_needed()
        precombat_setup = close_to_aggro or self._is_precombat_setup_needed()
        snapshot = _KeystoneBarSnapshot(
            in_aggro=in_aggro,
            close_to_aggro=close_to_aggro,
            precombat_setup=precombat_setup,
            symbolic_setup=symbolic_setup,
            has_symbolic_celerity=Routines.Checks.Effects.HasBuff(player_id, Symbolic_Celerity_ID),
            has_symbolic_posture=Routines.Checks.Effects.HasBuff(player_id, Symbolic_Posture_ID),
            has_keystone_signet=Routines.Checks.Effects.HasBuff(player_id, Keystone_Signet_ID),
        )

        # Offensive signets still need an enemy in normal spellcast range. The
        # wider precombat setup only prepares the bar; it does not make the
        # Mesmers blow signets from spirit range.
        if not precombat_setup:
            return snapshot

        snapshot.enemy_in_spellcast = bool(Routines.Agents.GetNearestEnemy(Range.Spellcast.value))
        # Use the shared native-event candidate view as well as the legacy live
        # targeting query. This lets the emergency path enter on the earliest
        # Reforged cast signal while final claim/cast checks still require the
        # enemy to be genuinely casting.
        snapshot.enemy_casting = bool(
            has_dangerous_cast_in_range(Range.Spellcast.value)
            or Routines.Targeting.GetEnemyCasting(Range.Spellcast.value)
        )
        snapshot.attacking_enemy_in_spellcast = bool(Routines.Targeting.GetEnemyAttacking(Range.Spellcast.value))
        return snapshot

    @staticmethod
    def _skillbar_skill_ids(account) -> list[int]:
        agent_data = getattr(account, "AgentData", None)
        skillbar = getattr(agent_data, "Skillbar", None)
        skills = getattr(skillbar, "Skills", []) if skillbar is not None else []
        result: list[int] = []
        for skill in skills:
            try:
                skill_id = int(getattr(skill, "Id", 0) or 0)
            except Exception:
                skill_id = 0
            if skill_id > 0:
                result.append(skill_id)
        return result

    @staticmethod
    def _skillbar_has_skill(account, skill_id: int) -> bool:
        return int(skill_id) in KeystoneSignet._skillbar_skill_ids(account)

    @staticmethod
    def _is_soj_keystone_donor_bar(skills) -> bool:
        """True only for the distinctive Me/Mo Signet-of-Judgment donor bar.

        We intentionally do not use the currently copied Keystone as a role
        signature, because it disappears after Arcane Mimicry's 20s window.
        """
        bar = {int(s) for s in skills if int(s or 0) > 0}
        required = (
            Signet_of_Judgment_ID,
            Symbolic_Celerity_ID,
            Symbolic_Posture_ID,
            Unnatural_Signet_ID,
            Bane_Signet_ID,
            Castigation_Signet_ID,
            Mantra_of_Signets_ID,
        )
        if not all(int(s or 0) > 0 and int(s) in bar for s in required):
            return False
        # Native Sorrow + Corruption identify Me/N, not our Me/Mo donor.
        if int(Signet_of_Sorrow_ID) in bar:
            return False
        # Mimicry is temporarily replaced with Keystone. Allow that ONLY if
        # this is already the uniquely identifiable SoJ/Bane/Castigation bar.
        return int(Arcane_Mimicry_ID) in bar or int(Keystone_Signet_ID) in bar

    def _is_soj_keystone_donor_mode(self) -> bool:
        try:
            player_id = int(Player.GetAgentID() or 0)
            primary, secondary = Agent.GetProfessions(player_id)
            if int(getattr(primary, "value", primary)) != int(getattr(Profession.Mesmer, "value", Profession.Mesmer)):
                return False
            if int(getattr(secondary, "value", secondary)) != int(getattr(Profession.Monk, "value", Profession.Monk)):
                return False
            return self._is_soj_keystone_donor_bar(self._get_current_skills())
        except Exception:
            return False

    def _find_verified_native_keystone_donor(self) -> int:
        """Select a native Keystone player, never somebody's temporary copy."""
        own_id = int(Player.GetAgentID() or 0)
        choices = []
        for account in self._candidate_signet_of_judgment_accounts():
            agent_data = getattr(account, "AgentData", None)
            agent_id = int(getattr(agent_data, "AgentID", 0) or 0)
            if agent_id <= 0 or agent_id == own_id:
                continue
            try:
                p = getattr(agent_data, "Profession", (0, 0))
                if int(p[0]) != int(getattr(Profession.Mesmer, "value", Profession.Mesmer)):
                    continue
                if int(p[1]) != int(getattr(Profession.Necromancer, "value", Profession.Necromancer)):
                    continue
            except Exception:
                continue
            ids = set(self._skillbar_skill_ids(account))
            # Sorrow is a unique bar-contract guard to avoid an unrelated
            # Keystone player becoming our Mimicry donor.
            if not {int(Keystone_Signet_ID), int(Signet_of_Sorrow_ID), int(Unnatural_Signet_ID)}.issubset(ids):
                continue
            if not Routines.Checks.Agents.IsAlive(agent_id):
                continue
            if not self._is_arcane_mimicry_donor_in_cast_range(agent_id):
                continue
            choices.append((float(self._distance_to_player(agent_id)), agent_id))
        choices.sort()
        return choices[0][1] if choices else 0

    def _cast_arcane_mimicry_for_keystone(self, snapshot: _KeystoneBarSnapshot):
        """Variant 3: Me/Mo copies native Keystone from a verified Me/N.

        Mimicry is an optional 20s burst; unavailable copies never block
        SoJ/Bane/Castigation from the normal zero-idle damage rotation.
        """
        if not snapshot.precombat_setup or not self.CanCastSkillID(Arcane_Mimicry_ID):
            return False
        donor = self._find_verified_native_keystone_donor()
        if donor <= 0:
            return False
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Arcane_Mimicry_ID,
            target_agent_id=int(donor),
            extra_condition=lambda aid=int(donor): (
                aid == self._find_verified_native_keystone_donor()
            ),
            log=False,
            aftercast_delay=120,
        )
        if did_cast:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event("KEYSTONE_VARIANT3_COPY_KEYSTONE", target_id=int(donor), policy="verified_me_n_native_elite")
            except Exception:
                pass
        return bool(did_cast)

    @staticmethod
    def _skillbar_has_other_elite_than_signet_of_judgment(account) -> bool:
        """Prevent Arcane Mimicry from targeting temporary SoJ copies.

        Shared memory may expose a copied Signet of Judgment on another
        Mesmer/ally. If that ally's real elite is Keystone or another elite,
        Arcane Mimicry would copy that real elite instead of the desired SoJ.
        Therefore a valid donor must show SoJ and no other elite skill.
        """
        for skill_id in KeystoneSignet._skillbar_skill_ids(account):
            if skill_id == Signet_of_Judgment_ID:
                continue
            if skill_id == Keystone_Signet_ID:
                return True
            try:
                if Skill.Flags.IsElite(skill_id):
                    return True
            except Exception:
                continue
        return False

    def _is_valid_signet_of_judgment_mimicry_account(self, account) -> bool:
        if not self._skillbar_has_skill(account, Signet_of_Judgment_ID):
            return False

        # Variant 3's dedicated Me/Mo donor has native Signet of Judgment
        # PLUS a temporary copied Keystone in its Mimicry slot. That copy must
        # not disqualify the NATIVE SoJ donor; the bar signature distinguishes
        # it from a native Keystone Mesmer temporarily displaying copied SoJ.
        agent_data = getattr(account, "AgentData", None)
        profs = getattr(agent_data, "Profession", (0, 0))
        try:
            me_mo = (
                int(profs[0]) == int(getattr(Profession.Mesmer, "value", Profession.Mesmer))
                and int(profs[1]) == int(getattr(Profession.Monk, "value", Profession.Monk))
            )
        except Exception:
            me_mo = False
        if me_mo and self._is_soj_keystone_donor_bar(self._skillbar_skill_ids(account)):
            return True
        # Outside the dedicated SoJ donor profile another visible elite is
        # unsafe: native Keystone users with copied SoJ must never donate SoJ.
        if self._skillbar_has_other_elite_than_signet_of_judgment(account):
            return False
        return True

    def _is_arcane_mimicry_donor_in_cast_range(self, target_agent_id: int) -> bool:
        try:
            return self._distance_to_player(int(target_agent_id)) <= float(Range.Spellcast.value)
        except Exception:
            return False

    def _is_valid_signet_of_judgment_mimicry_target(self, target_agent_id: int) -> bool:
        """Validate only the real-player SoJ account; heroes are never donors."""
        if target_agent_id <= 0 or target_agent_id == int(Player.GetAgentID() or 0):
            return False
        if not Routines.Checks.Agents.IsAlive(target_agent_id):
            return False
        if not self._is_arcane_mimicry_donor_in_cast_range(target_agent_id):
            return False
        for account in self._candidate_signet_of_judgment_accounts():
            agent_data = getattr(account, "AgentData", None)
            account_agent_id = int(getattr(agent_data, "AgentID", 0) or 0)
            if account_agent_id != int(target_agent_id):
                continue
            return bool(
                self._is_monk_profession_account(account)
                and self._is_valid_signet_of_judgment_mimicry_account(account)
            )
        return False


    def _is_monk_profession_account(self, account) -> bool:
        agent_data = getattr(account, "AgentData", None)
        professions = getattr(agent_data, "Profession", (0, 0))
        monk_value = int(getattr(Profession.Monk, "value", Profession.Monk))
        try:
            return int(professions[0] or 0) == monk_value or int(professions[1] or 0) == monk_value
        except Exception:
            return False

    def _candidate_signet_of_judgment_accounts(self):
        """Yield party players and their heroes that expose a shared skillbar.

        Arcane Mimicry must target the real Signet of Judgment holder. Shared
        memory can contain both account players and hero slots; this checks both.
        """
        from Py4GWCoreLib import GLOBAL_CACHE
        from Py4GWCoreLib.HeroAI.utils import SameMapOrPartyAsAccount

        own_party_id = int(GLOBAL_CACHE.Party.GetPartyID() or 0)
        seen_agent_ids: set[int] = set()

        for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
            if not getattr(account, "IsSlotActive", False) or getattr(account, "IsIsolated", False):
                continue
            if not SameMapOrPartyAsAccount(account):
                continue

            account_party_id = int(getattr(getattr(account, "AgentPartyData", None), "PartyID", 0) or 0)
            if own_party_id > 0 and account_party_id != own_party_id:
                continue

            agent_data = getattr(account, "AgentData", None)
            owner_agent_id = int(getattr(agent_data, "AgentID", 0) or 0)
            if owner_agent_id > 0 and owner_agent_id not in seen_agent_ids:
                seen_agent_ids.add(owner_agent_id)
                yield account

            for hero_account in GLOBAL_CACHE.ShMem.GetHeroesFromPlayers(owner_agent_id) or []:
                if not getattr(hero_account, "IsSlotActive", False) or getattr(hero_account, "IsIsolated", False):
                    continue
                if not SameMapOrPartyAsAccount(hero_account):
                    continue

                hero_agent_data = getattr(hero_account, "AgentData", None)
                hero_agent_id = int(getattr(hero_agent_data, "AgentID", 0) or 0)
                if hero_agent_id <= 0 or hero_agent_id in seen_agent_ids:
                    continue
                seen_agent_ids.add(hero_agent_id)
                yield hero_account

    @staticmethod
    def _local_hero_skill_ids(hero_position: int) -> list[int]:
        """Read a regular local hero bar directly from the current Reforged API."""
        result: list[int] = []
        try:
            for hero_skill in SkillBar.GetHeroSkillbar(int(hero_position)) or []:
                try:
                    skill_id = int(getattr(getattr(hero_skill, "id", None), "id", 0) or 0)
                except Exception:
                    skill_id = 0
                if skill_id > 0:
                    result.append(skill_id)
        except Exception:
            pass
        return result

    @classmethod
    def _local_hero_is_real_soj_donor(cls, hero_position: int) -> bool:
        skills = cls._local_hero_skill_ids(hero_position)
        if Signet_of_Judgment_ID not in skills:
            return False
        # Arcane Mimicry copies the target's elite. Reject bars with another elite
        # so a temporary/shared SoJ observation cannot make us copy the wrong elite.
        for skill_id in skills:
            if skill_id == Signet_of_Judgment_ID:
                continue
            try:
                if Skill.Flags.IsElite(skill_id):
                    return False
            except Exception:
                continue
        return True

    def _find_local_hero_with_signet_of_judgment(self) -> int:
        """Fallback/direct path for ordinary heroes owned by this game client.

        Shared-memory hero publication can lag or omit a hero depending on the
        multibox topology. Reading Party + GetHeroSkillbar makes Mimicry work with
        a normal hero as the SoJ donor as requested by testers.
        """
        best_agent_id = 0
        best_distance = float("inf")
        try:
            heroes = Party.GetHeroes() or []
        except Exception:
            heroes = []

        # Hero positions in the current API are 1..7.
        for hero_position, hero in enumerate(heroes, start=1):
            try:
                agent_id = int(getattr(hero, "agent_id", 0) or 0)
            except Exception:
                agent_id = 0
            if agent_id <= 0:
                continue
            if not Routines.Checks.Agents.IsAlive(agent_id):
                continue
            if not self._local_hero_is_real_soj_donor(hero_position):
                continue
            try:
                distance = float(self._distance_to_player(agent_id))
            except Exception:
                continue
            if distance > float(Range.Spellcast.value):
                continue
            if distance < best_distance:
                best_distance = distance
                best_agent_id = agent_id
        return best_agent_id

    def _find_ally_with_signet_of_judgment(self) -> int:
        """Find ONLY a verified real-player SoJ donor from shared account data.

        Local Monk heroes are intentionally excluded so Arcane Mimicry can never
        copy Ray of Judgment or another hero elite.
        """
        own_agent_id = int(Player.GetAgentID() or 0)
        candidates: list[tuple[float, int]] = []
        for account in self._candidate_signet_of_judgment_accounts():
            agent_data = getattr(account, "AgentData", None)
            target_agent_id = int(getattr(agent_data, "AgentID", 0) or 0)
            if target_agent_id <= 0 or target_agent_id == own_agent_id:
                continue
            if not self._is_monk_profession_account(account):
                continue
            if not Routines.Checks.Agents.IsAlive(target_agent_id):
                continue
            if not self._is_arcane_mimicry_donor_in_cast_range(target_agent_id):
                continue
            if not self._is_valid_signet_of_judgment_mimicry_account(account):
                continue
            try:
                distance = float(self._distance_to_player(target_agent_id))
            except Exception:
                distance = float(Range.Spellcast.value)
            candidates.append((distance, target_agent_id))
        if not candidates:
            return 0
        candidates.sort(key=lambda item: (item[0], item[1]))
        return int(candidates[0][1])


    def _cast_arcane_mimicry_for_signet_of_judgment(self, snapshot: _KeystoneBarSnapshot):
        """Arcane Mimicry is hard-wired to the verified real-player SoJ Monk.

        No random Monk probing and no hero fallback are allowed.
        """
        if self._is_soj_keystone_donor_mode():
            return (yield from self._cast_arcane_mimicry_for_keystone(snapshot))
        try:
            self._soj_mimicry._update_context_and_observe()
        except Exception:
            pass

        if not snapshot.precombat_setup:
            return False
        if not self.IsSkillEquipped(Arcane_Mimicry_ID):
            return False

        # Already copied: never overwrite the copied elite.
        try:
            tracked_slot = int(getattr(self._soj_mimicry.state, "mimicry_slot", 0) or 0)
            if tracked_slot > 0:
                current_id = int(SkillBar.GetSkillIDBySlot(tracked_slot) or 0)
                if current_id and current_id != Arcane_Mimicry_ID:
                    return False
        except Exception:
            pass

        target_id = int(self._find_ally_with_signet_of_judgment() or 0)
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MIMICRY_SOJ_GATE",
                donor_id=int(target_id),
                donor_found=bool(target_id > 0),
                arcane_ready=bool(self.CanCastSkillID(Arcane_Mimicry_ID)),
                precombat_setup=bool(snapshot.precombat_setup),
                policy="real_player_soj_only_no_hero_fallback",
            )
        except Exception:
            pass

        if target_id <= 0 or not self.CanCastSkillID(Arcane_Mimicry_ID):
            return False

        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Arcane_Mimicry_ID,
            target_agent_id=int(target_id),
            extra_condition=lambda tid=int(target_id): (
                self._is_valid_signet_of_judgment_mimicry_target(tid)
            ),
            log=False,
            aftercast_delay=250,
        )
        if not did_cast:
            return False

        try:
            from time import monotonic
            self._soj_mimicry.state.begin_probe(int(target_id), now=monotonic())
        except Exception:
            pass
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MIMICRY_SOJ_DONOR_CAST",
                target_id=int(target_id),
                policy="real_player_soj_only_no_hero_fallback",
            )
        except Exception:
            pass
        return True


    def _get_equipped_keystone_mantra_id(self) -> int:
        """Return the selected Mantra stance for this Keystone account.

        A character can only maintain one stance. If both Mantras are equipped
        accidentally, prefer Mantra of Inscriptions and emit a one-time debug
        marker so the bar configuration is visible in the combat log.
        """
        has_inscriptions = bool(
            Mantra_of_Inscriptions_ID > 0 and self.IsSkillEquipped(Mantra_of_Inscriptions_ID)
        )
        has_signets = bool(
            Mantra_of_Signets_ID > 0 and self.IsSkillEquipped(Mantra_of_Signets_ID)
        )
        if has_inscriptions and has_signets:
            if not self._mantra_conflict_logged:
                self._mantra_conflict_logged = True
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "KEYSTONE_MANTRA_CONFIG_CONFLICT",
                        selected_id=int(Mantra_of_Inscriptions_ID),
                        selected_name="Mantra_of_Inscriptions",
                        policy="one_stance_only_prefer_inscriptions",
                    )
                except Exception:
                    pass
            return int(Mantra_of_Inscriptions_ID)
        if has_inscriptions:
            return int(Mantra_of_Inscriptions_ID)
        if has_signets:
            return int(Mantra_of_Signets_ID)
        return 0

    def _active_keystone_mantra_id(self) -> int:
        player_id = int(Player.GetAgentID() or 0)
        if player_id <= 0:
            return 0
        for mantra_id in (Mantra_of_Inscriptions_ID, Mantra_of_Signets_ID):
            mantra_id = int(mantra_id or 0)
            if mantra_id <= 0:
                continue
            try:
                if Routines.Checks.Effects.HasBuff(player_id, mantra_id):
                    return mantra_id
            except Exception:
                continue
        return 0

    @staticmethod
    def _keystone_mantra_name(mantra_id: int) -> str:
        mantra_id = int(mantra_id or 0)
        if mantra_id == int(Mantra_of_Inscriptions_ID):
            return "Mantra_of_Inscriptions"
        if mantra_id == int(Mantra_of_Signets_ID):
            return "Mantra_of_Signets"
        return "none"

    def _track_keystone_mantra_state(self) -> None:
        """Read-only stance uptime telemetry for the A/B test."""
        now = int(get_game_tick() or 0)
        if now <= 0:
            return
        if now < int(self._mantra_telemetry_next_tick or 0):
            return
        self._mantra_telemetry_next_tick = int(now + 125)
        active_id = int(self._active_keystone_mantra_id() or 0)
        previous_id = int(self._mantra_active_id or 0)
        if active_id == previous_id:
            return
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            if previous_id > 0:
                CombatDebug.log_event(
                    "KEYSTONE_MANTRA_UPTIME_END",
                    mantra_id=previous_id,
                    mantra_name=self._keystone_mantra_name(previous_id),
                    uptime_ms=max(0, now - int(self._mantra_active_since_tick or now)),
                )
            if active_id > 0:
                CombatDebug.log_event(
                    "KEYSTONE_MANTRA_UPTIME_START",
                    mantra_id=active_id,
                    mantra_name=self._keystone_mantra_name(active_id),
                )
        except Exception:
            pass
        self._mantra_active_id = active_id
        self._mantra_active_since_tick = now if active_id > 0 else 0

    def _skill_recharge_remaining_ms(self, skill_id: int) -> int:
        """Return live remaining recharge in milliseconds, or -1 if unknown."""
        skill_id = int(skill_id or 0)
        if skill_id <= 0 or not self.IsSkillEquipped(skill_id):
            return -1
        try:
            slot = int(SkillBar.GetSlotBySkillID(skill_id) or 0)
            if not (1 <= slot <= 8):
                return -1
            skill_data = SkillBar.GetSkillData(slot)
            return max(0, int(getattr(skill_data, "get_recharge", 0) or 0))
        except Exception:
            return -1

    def _should_defer_mantra_for_symbolic_sync(
        self, snapshot: _KeystoneBarSnapshot
    ) -> tuple[bool, int, int, int]:
        """Avoid a short-lived Mantra immediately before the next Posture reset.

        The defer is intentionally narrow: it is allowed only after the current
        non-Keystone signet packet has actually been spent/unusable. Therefore a
        ready damage signet is never held just to wait for Symbolic Posture.
        """
        if not snapshot.symbolic_setup or snapshot.has_symbolic_posture:
            return False, -1, -1, -1
        if not self.IsSkillEquipped(Symbolic_Posture_ID):
            return False, -1, -1, -1
        if not self.IsSkillEquipped(Keystone_Signet_ID):
            return False, -1, -1, -1
        # EXP15: keep only a tiny anti-waste window.  Tasty's EXP14 test showed
        # Mantra was still skipped too often. Ready damage keeps absolute first
        # refusal, but a genuinely free window now casts Mantra unless Posture +
        # Keystone are essentially about to become available immediately.
        # No target scans, packet walks or asynchronous work are performed here.

        posture_ms = int(self._skill_recharge_remaining_ms(Symbolic_Posture_ID))
        keystone_ms = int(self._skill_recharge_remaining_ms(Keystone_Signet_ID))
        if posture_ms < 0 or keystone_ms < 0:
            return False, posture_ms, keystone_ms, -1

        combo_ready_ms = max(posture_ms, keystone_ms)
        should_defer = bool(combo_ready_ms <= int(MANTRA_SYMBOLIC_SYNC_WINDOW_MS))
        return should_defer, posture_ms, keystone_ms, combo_ready_ms

    def _log_mantra_symbolic_sync_defer(
        self, posture_ms: int, keystone_ms: int, combo_ready_ms: int
    ) -> None:
        """Throttle stance-sync telemetry so a 3s wait does not flood the log."""
        now = int(get_game_tick() or 0)
        if now <= 0:
            return
        if now - int(self._mantra_sync_last_log_tick or 0) < 900:
            return
        self._mantra_sync_last_log_tick = now
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "KEYSTONE_MANTRA_SYNC_DEFER",
                symbolic_posture_recharge_ms=int(posture_ms),
                keystone_recharge_ms=int(keystone_ms),
                combo_ready_ms=int(combo_ready_ms),
                sync_window_ms=int(MANTRA_SYMBOLIC_SYNC_WINDOW_MS),
                policy="do_not_waste_mantra_before_imminent_symbolic_keystone",
            )
        except Exception:
            pass

    @staticmethod
    def _player_health_snapshot() -> tuple[float, int, int]:
        """Return (health_fraction, max_hp, current_hp) for telemetry only."""
        player_id = int(Player.GetAgentID() or 0)
        if player_id <= 0:
            return 0.0, 0, 0
        try:
            health_fraction = float(Agent.GetHealth(player_id) or 0.0)
        except Exception:
            health_fraction = 0.0
        try:
            max_hp = int(Agent.GetMaxHealth(player_id) or 0)
        except Exception:
            max_hp = 0
        health_fraction = max(0.0, min(1.0, health_fraction))
        current_hp = int(round(health_fraction * max_hp)) if max_hp > 0 else 0
        return health_fraction, max_hp, current_hp

    def _queue_signet_health_probe(
        self,
        *,
        skill_id: int,
        target_id: int,
        mantra_id: int,
        reason: str,
        path: str,
        health_fraction_before: float,
        max_hp_before: int,
        hp_before: int,
    ) -> int:
        """Queue a post-activation HP sample without delaying combat decisions."""
        now = int(get_game_tick() or 0)
        if now <= 0:
            return 0
        try:
            activation_ms = int(round(max(0.0, float(Skill.Data.GetActivation(skill_id))) * 1000.0))
        except Exception:
            activation_ms = 0
        sample_delay_ms = max(
            int(SIGNET_HEALTH_PROBE_MIN_DELAY_MS),
            int(activation_ms + SIGNET_HEALTH_PROBE_GRACE_MS),
        )

        self._signet_health_probe_seq += 1
        probe_id = int(self._signet_health_probe_seq)
        self._pending_signet_health_probes.append(
            {
                "probe_id": probe_id,
                "skill_id": int(skill_id),
                "target_id": int(target_id),
                "mantra_id": int(mantra_id),
                "reason": str(reason),
                "path": str(path),
                "cast_tick": int(now),
                "activation_ms": int(activation_ms),
                "due_tick": int(now + sample_delay_ms),
                "health_fraction_before": float(health_fraction_before),
                "max_hp_before": int(max_hp_before),
                "hp_before": int(hp_before),
            }
        )
        if len(self._pending_signet_health_probes) > int(SIGNET_HEALTH_PROBE_MAX_QUEUE):
            self._pending_signet_health_probes = self._pending_signet_health_probes[
                -int(SIGNET_HEALTH_PROBE_MAX_QUEUE):
            ]
        return probe_id

    def _process_pending_signet_health_probes(self) -> None:
        """Emit due post-cast HP samples; never blocks or changes cast priority."""
        if not self._pending_signet_health_probes:
            return
        now = int(get_game_tick() or 0)
        if now <= 0:
            return

        remaining: list[dict] = []
        due: list[dict] = []
        for probe in self._pending_signet_health_probes:
            if now >= int(probe.get("due_tick", now + 1) or now + 1):
                due.append(probe)
            else:
                remaining.append(probe)
        self._pending_signet_health_probes = remaining
        if not due:
            return

        health_fraction_after, max_hp_after, hp_after = self._player_health_snapshot()
        active_mantra_after = int(self._active_keystone_mantra_id() or 0)
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
        except Exception:
            return

        for probe in due:
            hp_before = int(probe.get("hp_before", 0) or 0)
            max_hp_before = int(probe.get("max_hp_before", 0) or 0)
            observed_delta_hp = int(hp_after - hp_before) if max_hp_after > 0 and max_hp_before > 0 else 0
            missing_hp_before = max(0, max_hp_before - hp_before)
            mantra_id = int(probe.get("mantra_id", 0) or 0)
            CombatDebug.log_event(
                "SIGNET_HEALTH_PROBE_RESULT",
                probe_id=int(probe.get("probe_id", 0) or 0),
                skill_id=int(probe.get("skill_id", 0) or 0),
                skill_name=str(self._telemetry_signet_name(int(probe.get("skill_id", 0) or 0))),
                target_id=int(probe.get("target_id", 0) or 0),
                reason=str(probe.get("reason", "")),
                path=str(probe.get("path", "")),
                mantra_id=int(mantra_id),
                mantra_name=self._keystone_mantra_name(mantra_id),
                mantra_of_signets_at_cast=bool(mantra_id == int(Mantra_of_Signets_ID)),
                mantra_id_at_sample=int(active_mantra_after),
                mantra_name_at_sample=self._keystone_mantra_name(active_mantra_after),
                cast_to_sample_ms=max(0, now - int(probe.get("cast_tick", now) or now)),
                nominal_activation_ms=int(probe.get("activation_ms", 0) or 0),
                health_fraction_before=float(probe.get("health_fraction_before", 0.0) or 0.0),
                health_fraction_after=float(health_fraction_after),
                hp_before=int(hp_before),
                hp_after=int(hp_after),
                max_hp_before=int(max_hp_before),
                max_hp_after=int(max_hp_after),
                missing_hp_before=int(missing_hp_before),
                observed_delta_hp=int(observed_delta_hp),
                observed_positive_delta_hp=max(0, int(observed_delta_hp)),
                source_attribution="observed_delta_not_source_attributed",
            )

    def _should_gate_signet_spend_for_mantra(
        self,
        snapshot: _KeystoneBarSnapshot,
        should_reset_signets: bool,
        should_cast_keystone: bool,
    ) -> bool:
        """Hold the first signet for a ready Mantra setup, never for cooldown.

        Symbolic Posture is also a stance, so it and a Mantra cannot coexist.
        If a Keystone prime/reset is pending, let Symbolic Posture -> Keystone
        happen first. The Mantra is then applied immediately before the damage
        signet packet. If the Mantra itself is on recharge, normal signet DPS is
        never stalled waiting for it.
        """
        mantra_id = int(self._get_equipped_keystone_mantra_id() or 0)
        if mantra_id <= 0 or not snapshot.symbolic_setup:
            return False
        if self._active_keystone_mantra_id() == mantra_id:
            return False
        if snapshot.has_symbolic_posture or should_reset_signets or should_cast_keystone:
            return True
        return bool(self._is_skill_strictly_ready(mantra_id))

    def _keystone_effect_remaining_ms(self, skill_id: int) -> int | None:
        """0 = missing; positive milliseconds = known timer; None = unreadable.

        Do not infer 20s from buff age or a fixed 46/68s assumption: boon/
        rank/cons changes can affect actual duration. Unknown duration must
        not turn into a spam loop.
        """
        if skill_id <= 0:
            return None
        try:
            player_id = int(Player.GetAgentID())
            if not Routines.Checks.Effects.HasBuff(player_id, skill_id):
                return 0
            from Py4GWCoreLib import GLOBAL_CACHE
            remaining = int(GLOBAL_CACHE.Effects.GetEffectTimeRemaining(player_id, skill_id) or 0)
            return remaining if remaining > 0 else None
        except Exception:
            return None

    def _keystone_upkeep_due(self, skill_id: int) -> tuple[bool, int | None]:
        remaining = self._keystone_effect_remaining_ms(int(skill_id))
        return (remaining is not None and remaining <= KEYSTONE_PREBUFF_RENEW_BELOW_MS), remaining

    def _cast_keystone_buff_upkeep(self, skill_id: int, reason: str):
        """Direct BuildMgr cast: normal skill wrappers refuse active effects.

        This path is NOT an offensive DPS gate; callers place it either in a
        verified pre-pull window, on dispel recovery, or after offense probing.
        """
        if not self.IsSkillEquipped(skill_id):
            return False
        due, remaining = self._keystone_upkeep_due(skill_id)
        if not due or not self._is_skill_strictly_ready(skill_id):
            return False
        try:
            import PySystem
            now = int(PySystem.get_tick_count64())
        except Exception:
            now = 0
        previous = int(self._prebuff_last_request_ms.get(int(skill_id), -KEYSTONE_PREBUFF_RETRY_GUARD_MS))
        if now and now - previous < KEYSTONE_PREBUFF_RETRY_GUARD_MS:
            return False
        # Keep the two mutually exclusive stances in their valid order.
        if skill_id in (int(Mantra_of_Signets_ID), int(Mantra_of_Inscriptions_ID)):
            if Routines.Checks.Effects.HasBuff(Player.GetAgentID(), Symbolic_Posture_ID):
                return False
            snapshot = self._get_bar_snapshot()
            defer, _, _, _ = self._should_defer_mantra_for_symbolic_sync(snapshot)
            if defer:
                return False
        ok = yield from self.CastSkillID(skill_id=int(skill_id), log=False, aftercast_delay=250)
        if ok:
            if now:
                self._prebuff_last_request_ms[int(skill_id)] = now
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    'KEYSTONE_PREBUFF_UPKEEP', skill_id=int(skill_id),
                    remaining_ms=int(remaining), reason=str(reason),
                    threshold_ms=KEYSTONE_PREBUFF_RENEW_BELOW_MS,
                    policy='prepull_first_or_combat_free_window_no_offense_wait',
                )
            except Exception:
                pass
        return bool(ok)

    def _cast_early_native_keystone_setup(self, snapshot: _KeystoneBarSnapshot):
        """Stationary Me/Mo self-preparation outside offensive range only.

        Preserve the existing Mantra, Mimicry and combat rotations. Never
        move toward a foe or prime repeatedly while Keystone is active.
        """
        # Reset proof flag when the effect is removed/expired; a cast request
        # alone never arms the proof flag.
        if not snapshot.has_keystone_signet:
            self._early_keystone_effect_observed = False
        # Exact eight-skill Me/Mo bar only. Other Keystone variants retain
        # their original setup logic without inheriting this wider range.
        required = (Keystone_Signet_ID, Symbolic_Celerity_ID,
                    Bane_Signet_ID, Castigation_Signet_ID,
                    Signet_of_Rage_ID, Signet_of_Mystic_Wrath_ID,
                    Unnatural_Signet_ID, Mantra_of_Signets_ID)
        if (snapshot.in_aggro or snapshot.enemy_casting
                or not all(sid > 0 and self.IsSkillEquipped(sid) for sid in required)
                or self.IsSkillEquipped(Symbolic_Posture_ID)):
            return False
        try:
            player_id = int(Player.GetAgentID())
            if Agent.IsMoving(player_id):
                return False
            position = Player.GetXY()
            if self._has_enemy_near_position(position, float(Range.Spellcast.value)):
                return False
            if not self._has_enemy_near_position(position, EARLY_CELERITY_RANGE):
                return False
        except Exception:
            return False
        # In the expanded band, apply missing Celerity once; don't pull its
        # existing <=20s renewal forward ahead of the proven Mantra sequence.
        # The original updater retains its full normal-range renewal policy.
        if not snapshot.has_symbolic_celerity:
            if (yield from self._cast_keystone_buff_upkeep(
                    Symbolic_Celerity_ID, 'early_stationary_missing_celerity_150pct')):
                return True
            return False
        if not self._has_enemy_near_position(position, EARLY_NATIVE_KEYSTONE_RANGE):
            return False
        if snapshot.has_keystone_signet:
            # This event is stronger evidence than the preceding REQUEST: the
            # live effect is now visible BEFORE offensive spellcast range.
            if (not getattr(self, '_early_keystone_effect_observed', False)
                    and Routines.Checks.Effects.HasBuff(player_id, Mantra_of_Signets_ID)):
                self._early_keystone_effect_observed = True
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event('KEYSTONE_EARLY_EFFECT_OBSERVED',
                                         celerity_active=True, keystone_active=True,
                                         mantra_active=True,
                                         policy='observed_before_offensive_spellcast')
                except Exception:
                    pass
            return False
        # Do not let newly advanced Keystone jump ahead of the already tested
        # pre-pull Mantra. This is a ready-NOW condition, not a wait/sleep:
        # without Mantra the unchanged regular-range setup does its job.
        if not Routines.Checks.Effects.HasBuff(player_id, Mantra_of_Signets_ID):
            return False
        try:
            import PySystem
            now = int(PySystem.get_tick_count64() or 0)
        except Exception:
            now = 0
        # Requests are NOT buff confirmations. A retry guard prevents repeated
        # prime requests if the first request is interrupted/never observed.
        # The actual effect is still tested live in each new snapshot.
        last = int(getattr(self, '_early_keystone_request_ms', -KEYSTONE_PREBUFF_RETRY_GUARD_MS))
        if now and now - last < KEYSTONE_PREBUFF_RETRY_GUARD_MS:
            return False
        did = yield from self.skills.Mesmer.FastCasting.Keystone_Signet()
        if did:
            if now:
                self._early_keystone_request_ms = now
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event('KEYSTONE_EARLY_PRIME_REQUEST',
                                     mantra_present=True,
                                     policy='stationary_self_only_request_not_effect')
            except Exception:
                pass
        return bool(did)

    def _cast_keystone_precombat_buffs(self, snapshot: _KeystoneBarSnapshot):
        """Start Celerity + Mantra before the first Keystone near the pull.

        With Symbolic Posture equipped, it must be spent via Keystone BEFORE
        Mantra, because Posture overwrites Mantra. Never cast them both ahead
        of Keystone in that bar variant.
        """
        if not snapshot.symbolic_setup or snapshot.in_aggro or snapshot.enemy_casting:
            return False
        if (yield from self._cast_keystone_buff_upkeep(
            Symbolic_Celerity_ID, 'prepull_celerity_first'
        )):
            return True
        if self.IsSkillEquipped(Symbolic_Posture_ID):
            # Posture and Mantra are mutually exclusive. Once the initial
            # Posture -> Keystone prime is confirmed, add Mantra during the
            # approach BEFORE the first damage signet.
            if not snapshot.has_keystone_signet or snapshot.has_symbolic_posture:
                return False
            mantra_id = int(self._get_equipped_keystone_mantra_id() or 0)
            if mantra_id and (yield from self._cast_keystone_buff_upkeep(
                mantra_id, 'prepull_after_posture_and_keystone'
            )):
                return True
            return False
        mantra_id = int(self._get_equipped_keystone_mantra_id() or 0)
        if mantra_id and (yield from self._cast_keystone_buff_upkeep(
            mantra_id, 'prepull_mantra_before_keystone'
        )):
            return True
        return False

    def _cast_critical_keystone_mantra(self, snapshot: _KeystoneBarSnapshot):
        """Renew missing/<=6s Mantra ahead of ordinary offensive signets.

        This is a ready-now operation, not a queue or wait. The common upkeep
        caster handles recharge, energy, and the retry guard. Unknown effect
        duration is fail-open. The current build has NO Symbolic Posture.
        """
        if not snapshot.symbolic_setup:
            return False
        mantra_id = int(self._get_equipped_keystone_mantra_id() or 0)
        if mantra_id <= 0:
            return False
        remaining = self._keystone_effect_remaining_ms(mantra_id)
        if remaining is None or remaining > KEYSTONE_MANTRA_CRITICAL_RENEW_MS:
            return False
        if not self._is_skill_strictly_ready(mantra_id):
            return False
        if not (yield from self._cast_keystone_buff_upkeep(
            mantra_id, 'combat_critical_mantra_6s_before_ordinary_signets'
        )):
            return False
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                'KEYSTONE_MANTRA_CRITICAL_RENEW',
                mantra_id=int(mantra_id), remaining_ms=int(remaining),
                threshold_ms=int(KEYSTONE_MANTRA_CRITICAL_RENEW_MS),
                policy='ready_now_before_ordinary_offense_no_wait',
            )
        except Exception:
            pass
        return True

    def _cast_keystone_mantra_setup(
        self, snapshot: _KeystoneBarSnapshot, *, reason: str = "pre_signet_packet"
    ):
        """Apply the equipped Mantra immediately before the signet burst.

        This deliberately waits until Symbolic Posture has been consumed by the
        Keystone prime/reset so the two stances do not cancel each other.
        """
        if not snapshot.symbolic_setup:
            return False
        mantra_id = int(self._get_equipped_keystone_mantra_id() or 0)
        if mantra_id <= 0:
            return False
        due, _remaining = self._keystone_upkeep_due(mantra_id)
        if not due or snapshot.has_symbolic_posture:
            return False
        if not (yield from self._cast_keystone_buff_upkeep(mantra_id, reason)):
            return False

        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "KEYSTONE_MANTRA_CAST",
                mantra_id=int(mantra_id),
                mantra_name=self._keystone_mantra_name(mantra_id),
                reason=str(reason),
                symbolic_posture_equipped=bool(self.IsSkillEquipped(Symbolic_Posture_ID)),
                policy="symbolic_posture_then_keystone_then_mantra_then_signets",
            )
        except Exception:
            pass
        return True

    def _cast_post_keystone_mantra_if_immediate(self, snapshot: _KeystoneBarSnapshot):
        """EXP20: use Mantra once immediately after Keystone, never wait for it.

        This is deliberately one-shot. A successful Keystone reset arms the
        opportunity; on the very next decision tick we either cast a ready
        Mantra or consume the opportunity and let offensive signets fire.
        Recharge, lingering Symbolic Posture, or any other blocker therefore
        cannot create a Mantra wait state.
        """
        if not self._post_keystone_mantra_opportunity:
            return False

        # Consume first so every failure is fail-open to damage this same tick.
        self._post_keystone_mantra_opportunity = False

        if not snapshot.symbolic_setup:
            return False
        mantra_id = int(self._get_equipped_keystone_mantra_id() or 0)
        if mantra_id <= 0:
            return False
        if snapshot.has_symbolic_posture:
            return False
        # EXP20 is only the *missing* Mantra recovery; a live Mantra at 20s
        # gets its optional proactive renewal after damage has first refusal.
        if self._keystone_effect_remaining_ms(mantra_id) != 0:
            return False
        if not (yield from self._cast_keystone_buff_upkeep(
            mantra_id, 'post_keystone_missing_before_signet_packet'
        )):
            return False

        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "KEYSTONE_MANTRA_CAST",
                mantra_id=int(mantra_id),
                mantra_name=self._keystone_mantra_name(mantra_id),
                reason="exp20_immediate_post_keystone",
                symbolic_posture_equipped=bool(self.IsSkillEquipped(Symbolic_Posture_ID)),
                policy="keystone_then_ready_mantra_else_zero_wait_damage",
            )
        except Exception:
            pass
        return True

    def _cast_air_of_superiority_setup(self, snapshot: _KeystoneBarSnapshot):
        """Keep Air of Superiority active before the Keystone signet burst starts.

        Air is not treated as part of the signet spending cycle. It is a
        snowball setup buff: cast it in the Symbolic setup window shortly before the first offensive packet
        and refresh it only when the normal PvE helper says the effect is absent
        or about to expire. If Air randomly recharges the bar after kills, the
        existing Keystone rotation will naturally spend ready signets again
        before using Keystone as the next reset.
        """
        if not snapshot.symbolic_setup:
            return False
        if not self.IsSkillEquipped(Air_of_Superiority_ID):
            return False
        return (yield from self.skills.Any.PvE.Air_of_Superiority())


    def _distance_to_player(self, target_agent_id: int) -> float:
        try:
            from Py4GWCoreLib.Py4GWcorelib import Utils
            return float(Utils.Distance(Player.GetXY(), Agent.GetXY(target_agent_id)))
        except Exception:
            return 999999.0

    def _is_valid_power_cluster_enemy(self, target_agent_id: int) -> bool:
        try:
            target_agent_id = int(target_agent_id or 0)
            if target_agent_id <= 0:
                return False
            if not Agent.IsValid(target_agent_id) or not Agent.IsAlive(target_agent_id):
                return False
            return self._distance_to_player(target_agent_id) <= float(POWER_CLUSTER_FILTER_RANGE)
        except Exception:
            return False

    def _get_player_enemy_target(self) -> int:
        try:
            target_agent_id = int(Player.GetTargetID() or 0)
        except Exception:
            return 0
        if self._is_valid_power_cluster_enemy(target_agent_id):
            return target_agent_id
        return 0

    def _small_party_local_fallback_target(self, *, require_corpse: bool = False) -> int:
        """Fail-open damage target for scattered 8-man fights only.

        The shared team focus remains authoritative whenever it is locally
        usable.  This fallback exists for the live-log case where scattered
        enemies leave a Keystone Mesmer with ready signets but no locally
        castable authoritative target, causing wand-only behaviour while a
        sibling Mesmer keeps casting.

        Normal enemies are always preferred over spirits.  If the only nearby
        hostile is a single spirit, return 0 so the dedicated lone-spirit
        autoattack mode remains untouched.
        """
        if not self._small_party_focus_mode():
            return 0
        try:
            from Py4GWCoreLib import AgentArray
            enemies = AgentArray.GetEnemyArray() or []
            enemies = AgentArray.Filter.ByDistance(
                enemies, Player.GetXY(), float(Range.Spellcast.value)
            )
            enemies = [
                int(aid) for aid in enemies
                if int(aid or 0) > 0 and self._is_valid_power_cluster_enemy(int(aid))
            ]
        except Exception:
            return 0
        if not enemies:
            return 0

        normal_enemies = []
        spirit_enemies = []
        for aid in enemies:
            try:
                is_spirit = bool(Agent.IsSpirit(int(aid)))
            except Exception:
                is_spirit = False
            (spirit_enemies if is_spirit else normal_enemies).append(int(aid))

        # Never let a lone hostile spirit wake the full damage rotation.
        if normal_enemies:
            candidates = normal_enemies
        elif len(spirit_enemies) >= 2:
            candidates = spirit_enemies
        else:
            return 0

        if require_corpse:
            candidates = [
                aid for aid in candidates if self._has_corpse_near_agent(int(aid))
            ]
            if not candidates:
                return 0

        manual_target = int(self._get_player_enemy_target() or 0)
        if manual_target in candidates:
            return manual_target

        def rank(aid: int):
            try:
                hp = float(Agent.GetHealth(int(aid)) or 0.0)
            except Exception:
                hp = 1.0
            near_dead = 1 if 0.0 < hp <= float(KEYSTONE_SIGNET_LOW_HP_AVOID_FRACTION) else 0
            dangerous = 1 if self._is_safe_dangerous_cast(int(aid)) else 0
            support = 1 if self._is_monk_or_ritualist_enemy(int(aid)) else 0
            return (
                near_dead,
                -dangerous,
                -support,
                self._distance_to_player(int(aid)),
                int(aid),
            )

        candidates.sort(key=rank)
        return int(candidates[0]) if candidates else 0

    def _single_target_cleanup_target(self) -> int:
        """Return the sole relevant enemy in offensive range, otherwise 0.

        This is intentionally strict: cleanup mode activates only when exactly
        one valid enemy remains in the normal offensive scan. It removes AoE /
        enemy-casting gates from safe foe-targeted damage signets, but does not
        relax interrupt, heal, or conditional-control requirements.
        """
        try:
            from Py4GWCoreLib import AgentArray
            enemies = [
                int(agent_id)
                for agent_id in (AgentArray.GetEnemyArray() or [])
                if self._is_valid_power_cluster_enemy(int(agent_id))
            ]
        except Exception:
            enemies = []

        if len(enemies) != 1:
            return 0

        only_enemy = int(enemies[0])
        # Manual target receives preference only if it is that same valid
        # remaining enemy; there is no force-target behaviour here.
        player_target = self._get_player_enemy_target()
        if player_target == only_enemy:
            return int(player_target)
        return only_enemy


    def _get_zero_idle_enemy_targets(self, *, cache_ms: int = 140) -> tuple[int, ...]:
        """Return ONLY members of the authoritative team focus packet.

        In cleanup this is exactly one target. If the team resolver has no
        target, return an empty tuple; never fall back to another enemy.
        """
        try:
            from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import (
                get_team_cluster_anchor,
                get_team_cluster_members,
            )
            anchor = int(get_team_cluster_anchor(
                filter_range=Range.Spellcast.value,
                minimum_enemies=POWER_CLUSTER_MIN_ENEMIES,
                consumer_role="keystone_zero_idle_pool",
            ) or 0)
            if anchor <= 0:
                # Shared focus can be outside this account's spell range even
                # while other members of the SAME committed packet are usable.
                # Never choose a different pack, never break hard cleanup focus.
                from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import get_team_combat_plan
                shared_plan = get_team_combat_plan(
                    filter_range=Range.Spellcast.value,
                    minimum_enemies=POWER_CLUSTER_MIN_ENEMIES,
                    consumer_role="keystone_shared_packet_range_recovery",
                )
                if str(shared_plan.source) == "shared":
                    if str(shared_plan.mode) == "cluster":
                        in_range = tuple(sorted({
                            int(aid) for aid in shared_plan.member_ids
                            if int(aid or 0) > 0 and self._is_valid_power_cluster_enemy(int(aid))
                        }))
                        if in_range:
                            return in_range
                    # Do not mask an unreachable team focus with an unrelated
                    # local target. Keep explicit distance evidence for follow
                    # tuning rather than issuing an unsafe independent pull.
                    focus = int(shared_plan.focus_target or shared_plan.aoe_anchor or shared_plan.group_anchor or 0)
                    if focus > 0:
                        try:
                            from Py4GWCoreLib.Builds.Skills import CombatDebug
                            CombatDebug.log_state_event(
                                "KEYSTONE_SHARED_FOCUS_OUT_OF_RANGE",
                                signature_fields=("focus_id", "mode"),
                                min_interval_ms=2500, heartbeat_ms=5000,
                                focus_id=focus,
                                mode=str(shared_plan.mode),
                                distance=round(self._distance_to_player(focus), 1),
                                cast_range=round(float(Range.Spellcast.value), 1),
                                member_count=len(shared_plan.member_ids),
                                policy="same_committed_packet_only_no_external_pull",
                            )
                        except Exception:
                            pass
                        return ()
                fallback = int(self._small_party_local_fallback_target() or 0)
                if fallback > 0:
                    try:
                        from Py4GWCoreLib.Builds.Skills import CombatDebug
                        CombatDebug.log_event(
                            "KEYSTONE_SMALL_PARTY_LOCAL_FALLBACK",
                            target_id=int(fallback),
                            reason="no_locally_usable_authoritative_target",
                            policy="8man_scattered_enemy_fail_open_never_single_spirit",
                        )
                    except Exception:
                        pass
                    return (int(fallback),)
                return ()
            members = tuple(
                int(aid) for aid in get_team_cluster_members(
                    anchor,
                    filter_range=Range.Spellcast.value,
                )
                if int(aid or 0) > 0 and self._is_valid_power_cluster_enemy(int(aid))
            )
            # Keystone-only execute split. Non-executors target the surviving
            # packet, not the <=5% foe in the shared low_hp_finish plan.
            split = self._keystone_execute_split_plan()
            if split is not None:
                selected, packet = split
                if selected > 0:
                    return (int(selected),)
                if packet:
                    return tuple(sorted(set(int(a) for a in packet)))
            return tuple(sorted(set(members or (anchor,))))
        except Exception:
            return ()

    def _get_zero_idle_damage_target(self, skill_id: int) -> tuple[int, str]:
        """Immediate signet target inside the one authoritative team focus only."""
        targets = self._get_zero_idle_enemy_targets()
        if not targets:
            return 0, "no_authoritative_target"

        anchor = int(self._get_power_cluster_anchor() or 0)
        # Cleanup/single target: every Mesmer must hit the exact same enemy.
        if len(targets) <= 1:
            return int(targets[0]), "hard_cleanup_focus"

        # Packet mode: distribution is allowed ONLY among members of this packet.
        target_set = set(int(x) for x in targets)
        try:
            if int(skill_id) == int(Unnatural_Signet_ID):
                preferred = int(self._get_offensive_signet_target(claim_target=True) or 0)
            elif int(skill_id) in (int(Bane_Signet_ID), int(Castigation_Signet_ID), int(Signet_of_Rage_ID), int(Signet_of_Mystic_Wrath_ID)):
                preferred = int(self._get_monk_burst_signet_target(int(skill_id), claim_target=True) or 0)
            else:
                preferred = int(self._get_monk_damage_signet_target(int(skill_id), claim_target=True) or 0)
        except Exception:
            preferred = 0
        if preferred in target_set and self._is_valid_power_cluster_enemy(preferred):
            return int(preferred), "distributed_inside_authoritative_packet"

        # Claims may all be occupied. Do not leave the packet: use the canonical
        # anchor if valid, otherwise the lowest valid member.
        if anchor in target_set and self._is_valid_power_cluster_enemy(anchor):
            return int(anchor), "packet_claim_bypass_anchor"
        return int(min(targets)), "packet_claim_bypass_member"

    def _is_elite_priority_target(self, target_agent_id: int) -> bool:
        if not self._is_valid_power_cluster_enemy(target_agent_id):
            return False
        try:
            name = str(Agent.GetNameByID(int(target_agent_id)) or "").strip().lower()
        except Exception:
            return False
        return any(priority_name in name for priority_name in ELITE_PRIORITY_TARGET_NAMES)

    def _get_elite_priority_target(self) -> int:
        """Hard-focus mission objects like Urgoz Twisted Bark before packs.

        This is intentionally name-based and tiny: it only overrides the
        cluster target if the special target is alive and in normal offensive
        range. Otherwise the usual Simple-Power cluster spike is unchanged.
        """


        player_target = self._get_player_enemy_target()
        if player_target and self._is_elite_priority_target(player_target):
            return int(player_target)

        try:
            from Py4GWCoreLib import AgentArray
            enemies = AgentArray.GetEnemyArray()
            enemies = AgentArray.Filter.ByDistance(enemies, Player.GetXY(), POWER_CLUSTER_FILTER_RANGE)
            enemies = AgentArray.Filter.ByCondition(
                enemies,
                lambda agent_id: self._is_elite_priority_target(int(agent_id)),
            )
            candidates = [int(agent_id) for agent_id in enemies or []]
        except Exception:
            candidates = []

        if not candidates:
            return 0

        candidates.sort(key=lambda agent_id: (
            -self._count_signet_of_judgment_adjacent_enemies(agent_id) if hasattr(self, '_count_signet_of_judgment_adjacent_enemies') else -self._count_adjacent_enemies(agent_id) if hasattr(self, '_count_adjacent_enemies') else -self._count_nearby_alive_enemies(agent_id, POWER_CLUSTER_RADIUS),
            self._distance_to_player(agent_id),
            int(agent_id),
        ))
        return int(candidates[0])

    def _get_power_cluster_anchor(self, *, minimum_enemies: int = POWER_CLUSTER_MIN_ENEMIES) -> int:
        """Use the one authoritative KeySoJway packet/cleanup resolver."""
        try:
            from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import get_team_cluster_anchor
            return int(get_team_cluster_anchor(
                filter_range=POWER_CLUSTER_FILTER_RANGE,
                minimum_enemies=int(minimum_enemies),
                consumer_role="keystone_mesmer",
            ) or 0)
        except Exception:
            return 0


    def _get_power_cluster_members(self, anchor_agent_id: int) -> list[int]:
        if not self._is_valid_power_cluster_enemy(anchor_agent_id):
            return []
        # Prevent the current AoE radius from silently importing enemies from
        # a completely different cluster when the original cohort scatters.
        # Ordinary dense packet geometry stays unchanged outside this mode.
        try:
            from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import get_team_combat_plan
            from Py4GWCoreLib.Py4GWcorelib import Utils
            plan = get_team_combat_plan(
                filter_range=float(POWER_CLUSTER_FILTER_RANGE), minimum_enemies=1,
                consumer_role="keystone_engagement_members",
            )
            if str(plan.reason).startswith("engagement_"):
                members = [int(a) for a in plan.member_ids
                           if self._is_valid_power_cluster_enemy(int(a))]
                if str(plan.mode) == "cluster":
                    origin = Agent.GetXY(int(anchor_agent_id))
                    members = [a for a in members if float(Utils.Distance(origin, Agent.GetXY(int(a))))
                               <= float(POWER_CLUSTER_RADIUS)]
                return sorted(set(members), key=lambda a: (self._distance_to_player(a), a))
        except Exception:
            pass
        try:
            from Py4GWCoreLib import AgentArray
            enemies = AgentArray.GetEnemyArray()
            enemies = AgentArray.Filter.ByDistance(enemies, Agent.GetXY(anchor_agent_id), POWER_CLUSTER_RADIUS)
            enemies = AgentArray.Filter.ByCondition(
                enemies,
                lambda agent_id: self._is_valid_power_cluster_enemy(int(agent_id)),
            )
            members = [int(agent_id) for agent_id in enemies or []]
        except Exception:
            members = [int(anchor_agent_id)]

        if int(anchor_agent_id) not in members:
            members.append(int(anchor_agent_id))

        members = [agent_id for agent_id in members if self._is_valid_power_cluster_enemy(agent_id)]
        members.sort(key=lambda agent_id: (
            self._distance_to_player(agent_id),
            int(agent_id),
        ))
        return members

    @staticmethod
    def _get_enemy_professions_safe(agent_id: int) -> tuple[int, int]:
        try:
            if agent_id <= 0 or not Agent.IsValid(agent_id) or not Agent.IsAlive(agent_id):
                return (0, 0)
            primary, secondary = Agent.GetProfessions(agent_id)
            primary_id = int(getattr(primary, 'value', primary) or 0)
            secondary_id = int(getattr(secondary, 'value', secondary) or 0)
            return (primary_id, secondary_id)
        except Exception:
            return (0, 0)

    def _is_monk_or_ritualist_enemy(self, agent_id: int) -> bool:
        primary, secondary = self._get_enemy_professions_safe(agent_id)
        try:
            monk_id = int(getattr(Profession.Monk, 'value', Profession.Monk))
            ritualist_id = int(getattr(Profession.Ritualist, 'value', Profession.Ritualist))
        except Exception:
            return False
        return primary in (monk_id, ritualist_id) or secondary in (monk_id, ritualist_id)

    def _is_attacking_enemy_safe(self, agent_id: int) -> bool:
        try:
            return bool(Routines.Checks.Agents.IsAttacking(agent_id))
        except Exception:
            try:
                return bool(Agent.IsAttacking(agent_id))
            except Exception:
                return False

    @staticmethod
    def _keystone_execute_tick() -> int:
        """Use the whiteboard's monotonic clock for the 5% finisher leases.

        The generic Keystone _get_game_tick() currently uses a different API,
        which may produce zero when that API is unavailable.  Do not alter the
        generic rotation clock as part of this narrowly scoped execute fix.
        """
        try:
            import PySystem
            return max(0, int(PySystem.get_tick_count64() or 0))
        except Exception:
            return 0

    def _keystone_execute_split_plan(self) -> tuple[int, tuple[int, ...]] | None:
        """Select an <=5%-HP execute lane without abandoning the shared packet.

        The two first finishers take short *team-wide* whiteboard leases (not
        one lease per target). A third backup may join after 900 ms if the foe
        is still alive. Non-owners receive alternative live packet targets.
        Special-priority missions, interrupts, lone targets, and other builds
        remain under their existing rules.
        """
        try:
            from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import (
                get_team_combat_plan, get_team_cluster_members,
            )
            plan = get_team_combat_plan(
                filter_range=float(POWER_CLUSTER_FILTER_RANGE),
                minimum_enemies=POWER_CLUSTER_MIN_ENEMIES,
                consumer_role='keystone_execute_split',
            )
            if str(plan.mode) not in ('cluster', 'low_hp_finish', 'cleanup'):
                return None
            group = tuple(int(a) for a in plan.member_ids
                          if self._is_valid_power_cluster_enemy(int(a)))
            if len(group) < 2:
                # One remaining opponent: ordinary unified cleanup is correct.
                return None
            low = sorted(
                (int(a) for a in group
                 if 0.0 < float(Agent.GetHealth(int(a))) <= KEYSTONE_EXECUTE_HP_FRACTION + 1e-7),
                key=lambda a: (float(Agent.GetHealth(int(a))), int(a)),
            )
            if not low:
                return None
            # Keep the committed team focus for ordinary cleanup, but in a
            # multi-target packet a <=5% target receives no more than 2-3
            # finishers. 'cleanup' mode is common at 5% and must be eligible.
            victim = int(plan.focus_target) if int(plan.focus_target or 0) in low else int(low[0])
            center = int(plan.aoe_anchor or plan.group_anchor or victim)
            dying = set(low)
            packet = tuple(int(a) for a in get_team_cluster_members(
                center, filter_range=float(POWER_CLUSTER_FILTER_RANGE)
            ) if int(a) in group and int(a) not in dying
                  and self._is_valid_power_cluster_enemy(int(a)))
            if not packet:
                packet = tuple(int(a) for a in group if int(a) not in dying)
            if not packet:
                # All targets in the packet are dying: normal cleanup is valid.
                return None
            if not packet:
                return None
        except Exception:
            # No fabricated team split without a valid authoritative packet.
            return None

        # Lease is per account, against the entire combat team, not per foe:
        # even when several targets are dying no more than 2-3 Mesmers peel off.
        now = int(self._keystone_execute_tick() or 0)
        try:
            email = str(Player.GetAccountEmail() or '').strip()
            group_id = int(self._get_signet_of_judgment_group_id() or 0)
            if not email or now <= 0 or group_id <= 0:
                self._keystone_execute_diag('missing_team_group', victim, packet,
                                           mode=str(plan.mode), group_id=group_id,
                                           valid_tick=bool(now > 0),
                                           account_available=bool(email))
                return (0, packet)
            # Do not occupy a precious team-wide finisher lease when this
            # Mesmer cannot actually fire any ordinary damage signet right now.
            # No artificial waiting: normal packet offense still executes.
            finisher_ready = any(
                self._is_skill_strictly_ready(sid)
                for sid in (
                    Unnatural_Signet_ID, Signet_of_Sorrow_ID,
                    Bane_Signet_ID, Castigation_Signet_ID,
                    Signet_of_Rage_ID, Signet_of_Mystic_Wrath_ID,
                    Signet_of_Corruption_Kurzick_ID,
                    Signet_of_Corruption_Luxon_ID,
                ) if int(sid or 0) > 0
            )
            if not finisher_ready:
                self._keystone_execute_diag('no_ready_damage_signet', victim, packet,
                                           mode=str(plan.mode))
                return (0, packet)
            from Py4GWCoreLib import GLOBAL_CACHE
            from Py4GWCoreLib.enums_src.Whiteboard_enums import (
                WhiteboardClaimStrength, WhiteboardLockKind, WhiteboardLockMode,
                WhiteboardReentryPolicy,
            )
            shmem = GLOBAL_CACHE.ShMem
            lease = getattr(self, '_keystone_execute_split_lease', None)
            if lease and int(lease[0]) == victim and int(lease[1]) > now and int(lease[2]) == group_id:
                return (victim, packet)

            # A newly detected target starts with two lanes.  Only surviving
            # targets permit the third lane after the backup delay.
            seen = getattr(self, '_keystone_execute_first_seen', None)
            if not seen or int(seen[0]) != victim or now < int(seen[1]):
                self._keystone_execute_first_seen = (victim, now)
                seen = (victim, now)
            elapsed = max(0, now - int(seen[1]))
            max_holders = (KEYSTONE_EXECUTE_BACKUP_LIMIT
                           if elapsed >= KEYSTONE_EXECUTE_BACKUP_AFTER_MS
                           else KEYSTONE_EXECUTE_PRIMARY_LIMIT)
            if hasattr(shmem, 'SweepExpiredIntents'):
                shmem.SweepExpiredIntents(now)
            total = int(shmem.CountLocks(
                int(WhiteboardLockKind.SKILL_TARGET), int(EXECUTION_FINISHER_LOCK_ID),
                0, group_id, '', now,
                int(WhiteboardReentryPolicy.NON_REENTRANT),
                int(WhiteboardClaimStrength.HARD),
            ) or 0)
            if total >= int(max_holders):
                self._keystone_execute_diag('lane_limit_reached', victim, packet,
                                           mode=str(plan.mode), holders=total, limit=max_holders)
                return (0, packet)
            slot = shmem.PostLock(
                email, int(WhiteboardLockKind.SKILL_TARGET),
                int(EXECUTION_FINISHER_LOCK_ID), 0,
                now + int(EXECUTION_FINISHER_LOCK_MS), group_id,
                int(WhiteboardLockMode.SEMAPHORE), int(max_holders),
                int(WhiteboardReentryPolicy.NON_REENTRANT),
                int(WhiteboardClaimStrength.HARD),
            )
            if int(slot) == -1:
                self._keystone_execute_diag('lease_rejected', victim, packet,
                                           mode=str(plan.mode), holders=total, limit=max_holders)
                return (0, packet)
            self._keystone_execute_split_lease = (
                victim, now + int(EXECUTION_FINISHER_LOCK_MS), group_id,
            )
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    'KEYSTONE_EXECUTE_5PCT_CLAIM', target_id=victim,
                    target_hp=round(float(Agent.GetHealth(victim)), 4),
                    active_before=total, max_finishers=max_holders,
                    packet_members=len(packet),
                    policy='two_primary_third_backup_cluster_retained',
                )
            except Exception:
                pass
            return (victim, packet)
        except Exception as error:
            # Shared coordination missing: do not peel every Mesmer away.
            self._keystone_execute_diag('coordination_exception', victim, packet,
                                       error=type(error).__name__)
            return (0, packet)

    def _keystone_execute_diag(self, reason: str, victim: int, packet: tuple[int, ...], **data) -> None:
        """Throttled reason code for genuinely detected <=5% multi-target situations."""
        try:
            now = int(self._keystone_execute_tick() or 0)
            token = (str(reason), int(victim))
            seen = getattr(self, '_keystone_execute_diag_seen', None)
            if seen is None:
                seen = {}
                self._keystone_execute_diag_seen = seen
            if now and now - int(seen.get(token, 0) or 0) < 1500:
                return
            seen[token] = now
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                'KEYSTONE_EXECUTE_5PCT_DECISION', reason=str(reason),
                target_id=int(victim), packet_members=len(packet), **data,
            )
        except Exception:
            pass

    def _get_execution_finisher_target(self) -> int:
        split = self._keystone_execute_split_plan()
        if split is not None:
            return int(split[0] or 0)
        # One genuinely isolated foe is already handled by normal cleanup.
        return 0

    def _get_offensive_signet_target(self, *, claim_target: bool = True) -> int:
        split = self._keystone_execute_split_plan()
        if split is not None and int(split[0]) > 0:
            return int(split[0])
        # Otherwise use the shared packet distribution (never a <=15% peel).
        target_agent_id = self._get_packet_signet_target(
            Unnatural_Signet_ID, claim_target=bool(claim_target),
        )
        if target_agent_id:
            return int(target_agent_id)
        if self._get_power_cluster_anchor():
            return 0
        return 0

    def _get_attacking_power_cluster_target(self, *, claim_target: bool = True) -> int:
        anchor_agent_id = self._get_power_cluster_anchor()
        if anchor_agent_id:
            attacking = [
                int(enemy_id) for enemy_id in self._get_power_cluster_members(anchor_agent_id)
                if self._is_attacking_enemy_safe(enemy_id)
            ]
            attacking.sort(key=lambda enemy_id: (
                1 if self._is_packet_signet_target_claimed(Unnatural_Signet_ID, enemy_id) else 0,
                -self._count_signet_of_judgment_adjacent_enemies(enemy_id),
                self._distance_to_player(enemy_id),
                enemy_id,
            ))
            for enemy_id in attacking:
                if self._is_packet_signet_target_claimed(Unnatural_Signet_ID, enemy_id):
                    continue
                if claim_target and not self._claim_packet_signet_target(Unnatural_Signet_ID, enemy_id):
                    continue
                return int(enemy_id)
            # Keep distribution strict inside a real cluster. A short retry is
            # better than bypassing the lock and duplicating another signet.
            return 0
        try:
            fallback = int(Routines.Targeting.GetEnemyAttacking(Range.Spellcast.value) or 0)
        except Exception:
            fallback = 0
        if fallback and (not claim_target or self._claim_packet_signet_target(Unnatural_Signet_ID, fallback)):
            return fallback
        return 0

    @staticmethod
    def _is_moving_enemy_safe(agent_id: int) -> bool:
        try:
            return bool(Agent.IsMoving(int(agent_id)))
        except Exception:
            return False

    @staticmethod
    def _has_tryptophan_effect(agent_id: int) -> bool:
        try:
            return bool(Routines.Checks.Agents.HasEffect(int(agent_id), Tryptophan_Signet_ID))
        except Exception:
            return False

    def _is_tryptophan_opening_window(self, anchor_agent_id: int, members: list[int]) -> bool:
        """Return True only when the first Tryptophan cast will hit a real packet.

        This prevents the Ebon slow hex from being spent on one or two early
        runners and splitting the pull. A compact packet is accepted when it is
        already fighting/settled, or when it is still moving but close enough to
        the party to be treated as one incoming group.
        """
        if not anchor_agent_id or len(members) < TRYPTOPHAN_OPENING_MIN_CLUSTER_ENEMIES:
            return False

        # If the packet already has Tryptophan, allow spreading/refreshing it.
        if any(self._has_tryptophan_effect(agent_id) for agent_id in members):
            return True

        if any(self._is_attacking_enemy_safe(agent_id) for agent_id in members):
            return True

        moving_count = sum(1 for agent_id in members if self._is_moving_enemy_safe(agent_id))
        moving_ratio = float(moving_count) / max(1.0, float(len(members)))
        if moving_ratio <= TRYPTOPHAN_SETTLED_MOVING_RATIO_MAX:
            return True

        # Accept a fully moving packet only once it is already close to the team.
        # This covers enemies running into the ball without tagging only the
        # first straggler at the edge of spellcast range.
        return self._distance_to_player(anchor_agent_id) <= float(TRYPTOPHAN_CLOSE_CAST_DISTANCE)

    def _count_tryptophan_adjacent_enemies(self, target_agent_id: int) -> int:
        if target_agent_id <= 0:
            return 0
        try:
            from Py4GWCoreLib import AgentArray

            enemies = AgentArray.GetEnemyArray()
            enemies = AgentArray.Filter.ByDistance(enemies, Agent.GetXY(target_agent_id), Range.Adjacent.value)
            enemies = AgentArray.Filter.ByCondition(
                enemies,
                lambda agent_id: self._is_valid_power_cluster_enemy(int(agent_id)),
            )
            return int(len(enemies or []))
        except Exception:
            try:
                return int(Routines.Targeting.CountNearbyEnemies(target_agent_id, Range.Adjacent.value) or 0)
            except Exception:
                return 0

    def _get_tryptophan_signet_target(self) -> int:
        if not self.IsSkillEquipped(Tryptophan_Signet_ID):
            return 0
        if not self.CanCastSkillID(Tryptophan_Signet_ID):
            return 0

        anchor_agent_id = self._get_power_cluster_anchor(minimum_enemies=POWER_CLUSTER_MIN_ENEMIES)
        if not anchor_agent_id:
            return 0

        members = self._get_power_cluster_members(anchor_agent_id)
        if not members:
            return 0

        affected_members = [agent_id for agent_id in members if self._has_tryptophan_effect(agent_id)]
        affected_count = len(affected_members)

        if affected_count <= 0 and not self._is_tryptophan_opening_window(anchor_agent_id, members):
            return 0

        minimum_cluster = (
            TRYPTOPHAN_REPEAT_MIN_CLUSTER_ENEMIES
            if affected_count > 0
            else TRYPTOPHAN_OPENING_MIN_CLUSTER_ENEMIES
        )
        candidates = [
            agent_id for agent_id in members
            if self._count_tryptophan_adjacent_enemies(agent_id) >= minimum_cluster
        ]
        if not candidates:
            return 0

        # Once the packet is already tagged, spread the hex to an untagged
        # member inside the same adjacent group instead of refreshing the same
        # target immediately. If every member is tagged, refresh on the best
        # packet center.
        if affected_count > 0:
            untagged = [agent_id for agent_id in candidates if not self._has_tryptophan_effect(agent_id)]
            if untagged:
                candidates = untagged

        def rank(agent_id: int) -> tuple[int, int, int, float, int]:
            has_tryptophan = 1 if self._has_tryptophan_effect(agent_id) else 0
            moving = 1 if self._is_moving_enemy_safe(agent_id) else 0
            return (
                has_tryptophan,
                -self._count_tryptophan_adjacent_enemies(agent_id),
                moving,
                self._distance_to_player(agent_id),
                int(agent_id),
            )

        candidates.sort(key=rank)
        return int(candidates[0])

    @staticmethod
    def _get_game_tick() -> int:
        try:
            import Py4GW
            return int(Py4GW.Game.get_tick_count64() or 0)
        except Exception:
            return 0

    def _get_signet_of_judgment_group_id(self) -> int:
        """Return the shared multibox isolation group used by Keystone claims.

        v28 prefers the ShMem account group over the client-local PartyID.  The
        combat logs showed that target claims could otherwise be invisible to a
        sibling client even though both were in the same in-game party.  PartyID
        remains a compatibility fallback only.
        """
        try:
            from Py4GWCoreLib import GLOBAL_CACHE
            own_email = str(Player.GetAccountEmail() or '').strip()
            if own_email:
                try:
                    group_id = int(GLOBAL_CACHE.ShMem.GetAccountGroupByEmail(own_email) or 0)
                except Exception:
                    group_id = 0
                if group_id > 0:
                    return int(group_id)

                for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
                    if str(getattr(account, 'AccountEmail', '') or '').strip() != own_email:
                        continue
                    group_id = int(getattr(account, 'IsolationGroupID', 0) or 0)
                    if group_id > 0:
                        return int(group_id)
                    break

            party_id = int(GLOBAL_CACHE.Party.GetPartyID() or 0)
            if party_id > 0:
                return int(party_id)
        except Exception:
            pass
        return 0

    @staticmethod
    def _packet_signet_skill_ids() -> tuple[int, ...]:
        """Offensive signets that participate in the v28 packet cadence."""
        return tuple(
            int(skill_id)
            for skill_id in (
                Signet_of_Judgment_ID,
                Unnatural_Signet_ID,
                Signet_of_Corruption_Kurzick_ID,
                Signet_of_Corruption_Luxon_ID,
                Signet_of_Sorrow_ID,
                Signet_of_Disruption_ID,
                Bane_Signet_ID,
                Castigation_Signet_ID,
                Signet_of_Rage_ID,
                Signet_of_Mystic_Wrath_ID,
                Signet_of_Weariness_ID,
                Tryptophan_Signet_ID,
            )
            if int(skill_id or 0) > 0
        )

    def _active_keystone_account_emails(self, *, ready_now: bool = False) -> tuple[str, ...]:
        """Return same-group Keystone accounts in deterministic email order.

        ``ready_now`` compresses the initial temporal lanes to only accounts that
        currently expose at least one ready packet signet.  Target lanes use all
        active Keystone accounts so 4 targets -> 1/1/1/1 and 2 targets -> 2/2
        even when the clients evaluate on the same frame.
        """
        now = int(self._get_game_tick() or 0)
        if not ready_now and now > 0:
            cached_tick = int(getattr(self, '_keystone_distribution_cache_tick', 0) or 0)
            cached = tuple(getattr(self, '_keystone_distribution_cache_emails', ()) or ())
            if cached and now - cached_tick <= 250:
                return cached

        try:
            from Py4GWCoreLib import GLOBAL_CACHE
            own_email = str(Player.GetAccountEmail() or '').strip()
            try:
                own_isolation_group = int(GLOBAL_CACHE.ShMem.GetAccountGroupByEmail(own_email) or 0)
            except Exception:
                own_isolation_group = 0
            own_party_id = int(GLOBAL_CACHE.Party.GetPartyID() or 0)
            emails: list[str] = []
            packet_ids = self._packet_signet_skill_ids()
            for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
                if not bool(getattr(account, 'IsSlotActive', False)):
                    continue
                if bool(getattr(account, 'IsIsolated', False)):
                    continue
                account_group_id = int(getattr(account, 'IsolationGroupID', 0) or 0)
                party_id = int(getattr(getattr(account, 'AgentPartyData', None), 'PartyID', 0) or 0)
                if own_isolation_group > 0:
                    if account_group_id > 0 and account_group_id != own_isolation_group:
                        continue
                elif own_party_id > 0 and party_id > 0 and party_id != own_party_id:
                    continue
                if not self._shared_skillbar_contains(account, int(Keystone_Signet_ID)):
                    continue
                if ready_now and not self._shared_skillbar_has_ready_skill(account, packet_ids):
                    continue
                email = str(getattr(account, 'AccountEmail', '') or '').strip()
                if email:
                    emails.append(email)

            if own_email and own_email not in emails:
                # Fail-open for the first shared-memory frames after zoning.
                if (not ready_now) or any(
                    self.IsSkillEquipped(sid) and self._is_skill_strictly_ready(sid)
                    for sid in packet_ids
                ):
                    emails.append(own_email)
            result = tuple(sorted(set(emails)))
        except Exception:
            own_email = str(Player.GetAccountEmail() or '').strip()
            result = (own_email,) if own_email else ()

        if not ready_now and now > 0:
            self._keystone_distribution_cache_tick = now
            self._keystone_distribution_cache_emails = tuple(result)
        return tuple(result)

    def _keystone_distribution_lane(self, *, ready_now: bool = False) -> tuple[int, int]:
        emails = self._active_keystone_account_emails(ready_now=bool(ready_now))
        own_email = str(Player.GetAccountEmail() or '').strip()
        if not emails:
            return 0, 1
        try:
            return int(emails.index(own_email)), int(len(emails))
        except ValueError:
            return 0, int(max(1, len(emails)))

    def _lane_rotate_targets(self, targets: list[int]) -> list[int]:
        """Rotate equal-quality packet targets by this Keystone account's lane."""
        if len(targets) <= 1:
            return list(targets)
        lane, count = self._keystone_distribution_lane(ready_now=False)
        if count <= 1:
            return list(targets)
        offset = int(lane) % int(len(targets))
        if offset <= 0:
            return list(targets)
        return list(targets[offset:] + targets[:offset])

    @staticmethod
    def _small_party_focus_mode() -> bool:
        """Use a tighter spike profile in 8-man-and-smaller parties only."""
        try:
            party_size = int(Party.GetPartySize() or 0)
        except Exception:
            party_size = 0
        return 2 <= party_size <= 8

    def _small_party_focus_order(self, anchor_agent_id: int, candidates: list[int]) -> list[int]:
        """Bias 8-man Keystone lanes toward one kill target without idling.

        v33 intentionally maximizes packet coverage. That is excellent in the
        12-man Urgoz team, but the A/B logs show it can soften kill spikes in an
        8-man team. In small-party mode we keep at most two focus lanes and use
        an approximate 3:1 / 2:1 primary-secondary split. Claims are best-effort
        only in this mode, so no account waits merely because another account
        picked the same kill target.
        """
        ordered = [int(x) for x in candidates if int(x or 0) > 0]
        if not self._small_party_focus_mode() or len(ordered) <= 1:
            return ordered

        anchor = int(anchor_agent_id or 0)
        # Do not hard-focus a near-dead anchor if another healthy packet member
        # exists; the existing finisher logic can clean that target separately.
        primary = 0
        if anchor in ordered:
            try:
                anchor_hp = float(Agent.GetHealth(anchor) or 0.0)
            except Exception:
                anchor_hp = 1.0
            if anchor_hp > float(KEYSTONE_SIGNET_LOW_HP_AVOID_FRACTION):
                primary = anchor
        if primary <= 0:
            primary = int(ordered[0])
        secondary = next((int(x) for x in ordered if int(x) != primary), 0)
        if secondary <= 0:
            return [primary]

        lane, count = self._keystone_distribution_lane(ready_now=False)
        if len(ordered) >= 3:
            # Each player account gets a stable target lane in one packet.
            # A 5-account squad gets five distinct targets when available;
            # with fewer targets, overlap is allowed instead of waiting.
            stable = [primary] + sorted(int(x) for x in ordered if int(x) != primary)
            preferred = int(stable[int(lane) % len(stable)]) if count > 1 else int(primary)
        elif count == 5:
            # Exactly two opponents: 3 Mesmers on primary, 2 on secondary.
            preferred = int(primary) if int(lane) % 5 < 3 else int(secondary)
        elif count <= 1:
            preferred = int(primary)
        elif count == 2:
            preferred = int(primary) if int(lane) % 2 == 0 else int(secondary)
        else:
            preferred = int(secondary) if int(lane) % 4 == 2 else int(primary)

        rest = [int(x) for x in ordered if int(x) not in {preferred}]
        # Put the other focus lane second, then any remaining packet members.
        if preferred == primary and secondary in rest:
            rest.remove(secondary)
            rest.insert(0, secondary)
        elif preferred == secondary and primary in rest:
            rest.remove(primary)
            rest.insert(0, primary)
        return [int(preferred)] + rest

    def _recent_keystone_packet_casts(self, packet_members: set[int]) -> list[tuple[int, str, int]]:
        """Read short-lived cross-account packet-cast events from the Whiteboard."""
        return self._recent_keystone_packet_events(
            packet_members, int(KEYSTONE_SIGNET_CADENCE_EVENT_ID)
        )

    def _recent_keystone_packet_ready_events(self, packet_members: set[int]) -> list[tuple[int, str, int]]:
        """Read v30 ready pulses used to elect only clients ready right now."""
        return self._recent_keystone_packet_events(
            packet_members, int(KEYSTONE_SIGNET_READY_EVENT_ID)
        )

    def _recent_keystone_packet_events(
        self,
        packet_members: set[int],
        event_skill_id: int,
    ) -> list[tuple[int, str, int]]:
        """Read one short-lived Keystone packet event type from the Whiteboard."""
        if not packet_members:
            return []
        try:
            from Py4GWCoreLib import GLOBAL_CACHE
            from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
            now = int(self._get_game_tick() or 0)
            group_id = int(self._get_signet_of_judgment_group_id() or 0)
            if now <= 0:
                return []
            if hasattr(GLOBAL_CACHE.ShMem, 'SweepExpiredIntents'):
                GLOBAL_CACHE.ShMem.SweepExpiredIntents(now)
            out: list[tuple[int, str, int]] = []
            all_accounts = GLOBAL_CACHE.ShMem.GetAllAccounts()
            for _slot, intent in all_accounts.GetAllIntents() or []:
                if not bool(getattr(intent, 'Active', False)):
                    continue
                if int(getattr(intent, 'KindID', 0) or 0) != int(WhiteboardLockKind.SKILL_TARGET):
                    continue
                if int(getattr(intent, 'SkillID', 0) or 0) != int(event_skill_id):
                    continue
                if int(getattr(intent, 'IsolationGroupID', 0) or 0) != int(group_id):
                    continue
                target_id = int(getattr(intent, 'TargetAgentID', 0) or 0)
                if target_id not in packet_members:
                    continue
                posted = int(getattr(intent, 'PostedAtTick', 0) or 0)
                expires = int(getattr(intent, 'ExpiresAtTick', 0) or 0)
                if posted <= 0 or expires <= now:
                    continue
                owner = str(getattr(intent, 'OwnerEmail', '') or '').strip()
                out.append((posted, owner, target_id))
            out.sort(key=lambda item: int(item[0]), reverse=True)
            return out
        except Exception:
            return []

    def _post_keystone_packet_ready_event(self, target_id: int) -> None:
        """Publish a tiny v30 ready-now pulse before temporal lane election."""
        target_id = int(target_id or 0)
        if target_id <= 0:
            return
        try:
            from Py4GWCoreLib import GLOBAL_CACHE
            from Py4GWCoreLib.enums_src.Whiteboard_enums import (
                WhiteboardClaimStrength, WhiteboardLockKind, WhiteboardLockMode,
                WhiteboardReentryPolicy,
            )
            now = int(self._get_game_tick() or 0)
            email = str(Player.GetAccountEmail() or '').strip()
            if now <= 0 or not email:
                return
            if hasattr(GLOBAL_CACHE.ShMem, 'SweepExpiredIntents'):
                GLOBAL_CACHE.ShMem.SweepExpiredIntents(now)
            slot = GLOBAL_CACHE.ShMem.PostLock(
                email,
                int(WhiteboardLockKind.SKILL_TARGET),
                int(KEYSTONE_SIGNET_READY_EVENT_ID),
                int(target_id),
                now + int(KEYSTONE_SIGNET_READY_EVENT_TTL_MS),
                int(self._get_signet_of_judgment_group_id() or 0),
                int(WhiteboardLockMode.BARRIER),
                1,
                int(WhiteboardReentryPolicy.OWNER_REENTRANT),
                int(WhiteboardClaimStrength.SOFT),
            )
            if int(slot) == -1:
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        'KEYSTONE_PACKET_READY_PULSE_POST_FAILED',
                        target_id=int(target_id),
                        policy='fail_open_cadence_only_no_damage_block',
                    )
                except Exception:
                    pass
        except Exception:
            pass

    def _post_keystone_packet_cast_event(self, target_id: int) -> None:
        """Publish a short telemetry/cadence event after a real signet cast."""
        target_id = int(target_id or 0)
        if target_id <= 0:
            return
        try:
            from Py4GWCoreLib import GLOBAL_CACHE
            from Py4GWCoreLib.enums_src.Whiteboard_enums import (
                WhiteboardClaimStrength, WhiteboardLockKind, WhiteboardLockMode,
                WhiteboardReentryPolicy,
            )
            now = int(self._get_game_tick() or 0)
            email = str(Player.GetAccountEmail() or '').strip()
            if now <= 0 or not email:
                return
            if hasattr(GLOBAL_CACHE.ShMem, 'SweepExpiredIntents'):
                GLOBAL_CACHE.ShMem.SweepExpiredIntents(now)
            slot = GLOBAL_CACHE.ShMem.PostLock(
                email,
                int(WhiteboardLockKind.SKILL_TARGET),
                int(KEYSTONE_SIGNET_CADENCE_EVENT_ID),
                int(target_id),
                now + int(KEYSTONE_SIGNET_CADENCE_EVENT_TTL_MS),
                int(self._get_signet_of_judgment_group_id() or 0),
                int(WhiteboardLockMode.BARRIER),
                1,
                int(WhiteboardReentryPolicy.OWNER_REENTRANT),
                int(WhiteboardClaimStrength.SOFT),
            )
            if int(slot) == -1:
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        'KEYSTONE_PACKET_CAST_EVENT_POST_FAILED',
                        target_id=int(target_id),
                        policy='fail_open_cadence_only_no_damage_block',
                    )
                except Exception:
                    pass
        except Exception:
            pass

    def _coordinate_keystone_packet_cadence(self):
        """Apply a tiny v30 cross-account phase shift to packet signet spam.

        Spatial target claims remain the primary anti-overlap mechanism. For
        timing, every Keystone client that has a packet signet ready *now* posts
        a short shared-memory ready pulse, then gives sibling clients one tiny
        election frame to do the same. Only those fresh pulses receive lanes.
        A recent real signet cast is the stronger phase anchor, so later-ready
        clients queue roughly 90 ms behind the actual cast instead of trusting
        stale shared recharge snapshots.

        No signet is reserved for the future. Single-target cleanup bypasses the
        helper entirely, and a lone ready Keystone pays only the 18 ms election
        frame before firing.
        """
        packet = tuple(
            int(x) for x in self._get_zero_idle_enemy_targets()
            if int(x or 0) > 0
        )
        if len(packet) <= 1:
            self._keystone_last_packet_members = tuple(packet)
            return 0

        packet_set = set(packet)
        previous = set(
            int(x) for x in (getattr(self, '_keystone_last_packet_members', ()) or ())
        )
        new_packet = bool(not previous or not previous.intersection(packet_set))
        self._keystone_last_packet_members = tuple(packet)

        own_email = str(Player.GetAccountEmail() or '').strip()
        all_emails = tuple(self._active_keystone_account_emails(ready_now=False))
        if own_email and own_email not in all_emails:
            all_emails = tuple(sorted(set(all_emails + (own_email,))))
        if len(all_emails) <= 1:
            return 0

        # Publish a ready-now pulse on a canonical member of this exact packet.
        # The short gather is the only unconditional delay in multi-target mode.
        self._post_keystone_packet_ready_event(int(packet[0]))
        gather_ms = int(KEYSTONE_SIGNET_READY_GATHER_MS)
        if gather_ms > 0:
            yield from Routines.Yield.wait(gather_ms)

        now = int(self._get_game_tick() or 0)
        ready_events = self._recent_keystone_packet_ready_events(packet_set)
        latest_ready_by_owner: dict[str, int] = {}
        for posted, owner, _target_id in ready_events:
            owner = str(owner or '').strip()
            posted = int(posted or 0)
            if not owner or owner not in all_emails or posted <= 0:
                continue
            if now > 0 and now - posted > int(KEYSTONE_SIGNET_READY_FRESH_MS):
                continue
            latest_ready_by_owner[owner] = max(
                int(latest_ready_by_owner.get(owner, 0) or 0), posted
            )
        if own_email and own_email not in latest_ready_by_owner:
            # Fail open if our just-posted pulse is not visible to our own read.
            latest_ready_by_owner[own_email] = max(1, now - gather_ms)

        ready_emails = tuple(
            email for email in all_emails if email in latest_ready_by_owner
        )
        ready_count = max(1, len(ready_emails))
        if ready_count <= 1:
            # Still log the election path. This distinguishes "the cadence
            # helper did not run" from "only this Keystone exposed a fresh
            # ready pulse during the gather window" in the next live log.
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    'KEYSTONE_PACKET_MICRO_STAGGER',
                    wait_ms=int(gather_ms),
                    gather_ms=int(gather_ms),
                    phase_wait_ms=0,
                    lane=0,
                    ready_keystone_count=1,
                    packet_size=int(len(packet)),
                    reason='single_fresh_ready_pulse',
                    policy='fresh_ready_pulse_plus_real_cast_anchor_no_skill_reservation',
                )
            except Exception:
                pass
            return int(gather_ms)

        lane = 0
        phase_wait_ms = 0
        reason = 'new_packet_ready_pulse' if new_packet else 'simultaneous_ready_pulse'

        # Real casts beat ready pulses as the timing origin. This also handles
        # clients that become ready just after the first 18 ms election window.
        recent_casts = self._recent_keystone_packet_casts(packet_set)
        latest_cast = recent_casts[0] if recent_casts else None
        if latest_cast is not None and now > 0:
            posted, owner_email, _target_id = latest_cast
            owner_email = str(owner_email or '').strip()
            elapsed = max(0, now - int(posted or 0))
            ready_set = set(ready_emails)

            if owner_email in all_emails:
                owner_index = int(all_emails.index(owner_email))
                cyclic = list(all_emails[owner_index + 1:] + all_emails[:owner_index])
                ordered_ready = [email for email in cyclic if email in ready_set and email != owner_email]
                # If the owner is already ready again (e.g. Sorrow instant
                # recharge), let the other ready Keystone clients go first.
                if owner_email in ready_set:
                    ordered_ready.append(owner_email)
            else:
                ordered_ready = list(ready_emails)

            if own_email in ordered_ready:
                phase_step = int(ordered_ready.index(own_email)) + 1
                lane = max(0, phase_step - 1)
            else:
                phase_step = 1
                lane = 0
            required_elapsed = int(phase_step) * int(KEYSTONE_SIGNET_CADENCE_GAP_MS)
            phase_wait_ms = max(0, int(required_elapsed) - int(elapsed))
            reason = 'recent_real_cast_plus_ready_pulse_queue'
        else:
            try:
                lane = int(ready_emails.index(own_email))
            except ValueError:
                lane = 0
            earliest_ready = min(
                (int(latest_ready_by_owner.get(email, now) or now) for email in ready_emails),
                default=now,
            )
            elapsed_from_ready_wave = max(0, now - int(earliest_ready or now)) if now > 0 else gather_ms
            desired_offset = int(max(0, lane) * int(KEYSTONE_SIGNET_INITIAL_LANE_STEP_MS))
            phase_wait_ms = max(0, desired_offset - elapsed_from_ready_wave)

        if phase_wait_ms > 0:
            yield from Routines.Yield.wait(int(phase_wait_ms))

        # Log the election even for lane 0. v29 only logged when an additional
        # wait happened, which made a healthy spatial spread look as if the
        # cadence path had never executed at all.
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                'KEYSTONE_PACKET_MICRO_STAGGER',
                wait_ms=int(gather_ms + phase_wait_ms),
                gather_ms=int(gather_ms),
                phase_wait_ms=int(phase_wait_ms),
                lane=int(lane),
                ready_keystone_count=int(ready_count),
                packet_size=int(len(packet)),
                reason=str(reason),
                policy='fresh_ready_pulse_plus_real_cast_anchor_no_skill_reservation',
            )
        except Exception:
            pass
        return int(gather_ms + phase_wait_ms)

    @staticmethod
    def _soj_balanced_lock_id(level: int) -> int:
        return int(SOJ_BALANCED_CLAIM_BASE_ID) + max(0, int(level))

    def _get_signet_of_judgment_claim_count(self, target_agent_id: int) -> int:
        """Return the number of short active SoJ reservations on a target.

        Each occupancy level has its own lock id.  This stays compatible with
        the existing shared-memory API while allowing balanced duplicate casts
        instead of a binary claimed/unclaimed decision.
        """
        if int(target_agent_id or 0) <= 0:
            return SOJ_BALANCED_CLAIM_LEVELS
        try:
            from Py4GWCoreLib import GLOBAL_CACHE
            from Py4GWCoreLib.enums_src.Whiteboard_enums import (
                WhiteboardClaimStrength, WhiteboardLockKind, WhiteboardLockMode,
                WhiteboardReentryPolicy,
            )
            shmem = GLOBAL_CACHE.ShMem
            now_tick = self._get_game_tick()
            if now_tick <= 0:
                return 0
            if hasattr(shmem, 'SweepExpiredIntents'):
                shmem.SweepExpiredIntents(now_tick)
            count = 0
            for level in range(SOJ_BALANCED_CLAIM_LEVELS):
                if shmem.IsLockBlocked(
                    int(WhiteboardLockKind.SKILL_TARGET),
                    self._soj_balanced_lock_id(level),
                    int(target_agent_id),
                    self._get_signet_of_judgment_group_id(),
                    str(Player.GetAccountEmail() or '').strip(),
                    now_tick,
                    int(WhiteboardLockMode.EXCLUSIVE),
                    1,
                    int(WhiteboardReentryPolicy.NON_REENTRANT),
                    int(WhiteboardClaimStrength.HARD),
                ):
                    count += 1
            return count
        except Exception:
            return 0

    def _is_signet_of_judgment_target_claimed(self, target_agent_id: int) -> bool:
        return self._get_signet_of_judgment_claim_count(target_agent_id) > 0

    def _claim_signet_of_judgment_target(self, target_agent_id: int) -> bool:
        """Reserve the next free occupancy level; never block a valid SoJ cast."""
        if int(target_agent_id or 0) <= 0:
            return False
        try:
            from Py4GWCoreLib import GLOBAL_CACHE
            from Py4GWCoreLib.enums_src.Whiteboard_enums import (
                WhiteboardClaimStrength, WhiteboardLockKind, WhiteboardLockMode,
                WhiteboardReentryPolicy,
            )
            shmem = GLOBAL_CACHE.ShMem
            now_tick = self._get_game_tick()
            if now_tick <= 0:
                return True
            if hasattr(shmem, 'SweepExpiredIntents'):
                shmem.SweepExpiredIntents(now_tick)
            for level in range(SOJ_BALANCED_CLAIM_LEVELS):
                lock_id = self._soj_balanced_lock_id(level)
                blocked = shmem.IsLockBlocked(
                    int(WhiteboardLockKind.SKILL_TARGET), lock_id, int(target_agent_id),
                    self._get_signet_of_judgment_group_id(),
                    str(Player.GetAccountEmail() or '').strip(), now_tick,
                    int(WhiteboardLockMode.EXCLUSIVE), 1,
                    int(WhiteboardReentryPolicy.NON_REENTRANT),
                    int(WhiteboardClaimStrength.HARD),
                )
                if blocked:
                    continue
                if hasattr(shmem, 'PostLock'):
                    posted = shmem.PostLock(
                        str(Player.GetAccountEmail() or '').strip(),
                        int(WhiteboardLockKind.SKILL_TARGET), lock_id, int(target_agent_id),
                        now_tick + int(SOJ_BALANCED_CLAIM_MS),
                        self._get_signet_of_judgment_group_id(),
                        int(WhiteboardLockMode.EXCLUSIVE), 1,
                        int(WhiteboardReentryPolicy.NON_REENTRANT),
                        int(WhiteboardClaimStrength.HARD),
                    )
                    if posted != -1:
                        return True
            # More simultaneous casts than tracked levels is extremely unlikely;
            # damage uptime still wins, so allow the cast without a reservation.
            return True
        except Exception:
            return True

    def _is_packet_signet_target_claimed(self, skill_id: int, target_agent_id: int) -> bool:
        if int(skill_id or 0) <= 0 or int(target_agent_id or 0) <= 0:
            return False

        try:
            from Py4GWCoreLib import GLOBAL_CACHE
            from Py4GWCoreLib.enums_src.Whiteboard_enums import (
                WhiteboardClaimStrength,
                WhiteboardLockKind,
                WhiteboardLockMode,
                WhiteboardReentryPolicy,
            )

            now_tick = self._get_game_tick()
            if now_tick <= 0:
                return False
            shmem = GLOBAL_CACHE.ShMem
            if hasattr(shmem, 'SweepExpiredIntents'):
                shmem.SweepExpiredIntents(now_tick)
            return bool(shmem.IsLockBlocked(
                int(WhiteboardLockKind.SKILL_TARGET),
                int(KEYSTONE_SIGNET_DISTRIBUTION_LOCK_ID),
                int(target_agent_id),
                self._get_signet_of_judgment_group_id(),
                str(Player.GetAccountEmail() or '').strip(),
                now_tick,
                int(WhiteboardLockMode.EXCLUSIVE),
                1,
                int(WhiteboardReentryPolicy.NON_REENTRANT),
                int(WhiteboardClaimStrength.HARD),
            ))
        except Exception:
            return False

    def _claim_packet_signet_target(self, skill_id: int, target_agent_id: int, *, lock_ms: int = PACKET_SIGNET_TARGET_LOCK_MS) -> bool:
        if int(skill_id or 0) <= 0 or int(target_agent_id or 0) <= 0:
            return False
        if self._is_packet_signet_target_claimed(skill_id, target_agent_id):
            return False

        try:
            from Py4GWCoreLib import GLOBAL_CACHE
            from Py4GWCoreLib.enums_src.Whiteboard_enums import (
                WhiteboardClaimStrength,
                WhiteboardLockKind,
                WhiteboardLockMode,
                WhiteboardReentryPolicy,
            )

            now_tick = self._get_game_tick()
            if now_tick <= 0:
                return True
            return GLOBAL_CACHE.ShMem.PostLock(
                str(Player.GetAccountEmail() or '').strip(),
                int(WhiteboardLockKind.SKILL_TARGET),
                int(KEYSTONE_SIGNET_DISTRIBUTION_LOCK_ID),
                int(target_agent_id),
                now_tick + int(lock_ms),
                self._get_signet_of_judgment_group_id(),
                int(WhiteboardLockMode.EXCLUSIVE),
                1,
                int(WhiteboardReentryPolicy.NON_REENTRANT),
                int(WhiteboardClaimStrength.HARD),
            ) != -1
        except Exception:
            # Fail-open: target staggering is optimization only.  If shared memory
            # is unavailable, do not block the damage rotation.
            return True

    def _is_mistrust_target_claimed(self, target_agent_id: int) -> bool:
        if int(target_agent_id or 0) <= 0:
            return False
        try:
            from Py4GWCoreLib import GLOBAL_CACHE
            from Py4GWCoreLib.enums_src.Whiteboard_enums import (
                WhiteboardClaimStrength, WhiteboardLockKind, WhiteboardLockMode,
                WhiteboardReentryPolicy,
            )
            now_tick = self._get_game_tick()
            if now_tick <= 0:
                return False
            shmem = GLOBAL_CACHE.ShMem
            if hasattr(shmem, 'SweepExpiredIntents'):
                shmem.SweepExpiredIntents(now_tick)
            return bool(shmem.IsLockBlocked(
                int(WhiteboardLockKind.SKILL_TARGET),
                int(MISTRUST_DISTRIBUTION_LOCK_ID),
                int(target_agent_id),
                self._get_signet_of_judgment_group_id(),
                str(Player.GetAccountEmail() or '').strip(),
                now_tick,
                int(WhiteboardLockMode.EXCLUSIVE),
                1,
                int(WhiteboardReentryPolicy.NON_REENTRANT),
                int(WhiteboardClaimStrength.HARD),
            ))
        except Exception:
            return False

    def _claim_mistrust_target(self, target_agent_id: int) -> bool:
        if int(target_agent_id or 0) <= 0:
            return False
        if self._is_mistrust_target_claimed(target_agent_id):
            return False
        try:
            from Py4GWCoreLib import GLOBAL_CACHE
            from Py4GWCoreLib.enums_src.Whiteboard_enums import (
                WhiteboardClaimStrength, WhiteboardLockKind, WhiteboardLockMode,
                WhiteboardReentryPolicy,
            )
            now_tick = self._get_game_tick()
            if now_tick <= 0:
                return True
            return GLOBAL_CACHE.ShMem.PostLock(
                str(Player.GetAccountEmail() or '').strip(),
                int(WhiteboardLockKind.SKILL_TARGET),
                int(MISTRUST_DISTRIBUTION_LOCK_ID),
                int(target_agent_id),
                now_tick + int(MISTRUST_TARGET_LOCK_MS),
                self._get_signet_of_judgment_group_id(),
                int(WhiteboardLockMode.EXCLUSIVE),
                1,
                int(WhiteboardReentryPolicy.NON_REENTRANT),
                int(WhiteboardClaimStrength.HARD),
            ) != -1
        except Exception:
            return True

    def _get_mistrust_target(self) -> tuple[int, str]:
        """Select a useful, non-duplicated Mistrust target.

        Mistrust is deliberately *not* a zero-idle filler.  It is only spent on
        a foe that is currently casting or on a high-confidence caster profile
        likely to cast soon.  Teamwide claims and the local tracker remain hard
        for Mistrust, because duplicating the hex on one foe usually wastes a
        slot while another caster remains uncovered.
        """
        anchor = int(self._get_power_cluster_anchor() or 0)
        if anchor <= 0:
            return 0, "no_cluster"

        active_candidates = []
        predictive_candidates = []
        for enemy_id in self._get_power_cluster_members(anchor):
            try:
                health = float(Agent.GetHealth(enemy_id))
                if health <= 0.15:
                    continue
            except Exception:
                health = 1.0

            # Unlike damage signets, Mistrust claims are intentionally hard:
            # never stack it just to avoid idle time.
            if self._is_mistrust_target_claimed(enemy_id):
                continue
            try:
                from Py4GWCoreLib.Builds.Skills import MistrustTracker
                if MistrustTracker.is_target_tracked(enemy_id):
                    continue
            except Exception:
                pass

            try:
                casting = bool(Agent.IsCasting(enemy_id))
            except Exception:
                casting = False

            primary, secondary = self._get_enemy_professions_safe(enemy_id)
            try:
                caster_prof_ids = {
                    int(getattr(Profession.Monk, 'value', Profession.Monk)),
                    int(getattr(Profession.Mesmer, 'value', Profession.Mesmer)),
                    int(getattr(Profession.Necromancer, 'value', Profession.Necromancer)),
                    int(getattr(Profession.Elementalist, 'value', Profession.Elementalist)),
                    int(getattr(Profession.Ritualist, 'value', Profession.Ritualist)),
                }
                caster_prof = primary in caster_prof_ids or secondary in caster_prof_ids
            except Exception:
                caster_prof = False

            try:
                from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
                knowledge_priority = int(EnemyKnowledge.mistrust_priority(enemy_id))
            except Exception:
                knowledge_priority = 0

            adjacent = int(self._count_signet_of_judgment_adjacent_enemies(enemy_id))
            distance = float(self._distance_to_player(enemy_id))
            score = (
                -knowledge_priority,
                -adjacent,
                distance,
                -health,
                int(enemy_id),
            )

            if casting:
                active_candidates.append((score, int(enemy_id)))
            elif caster_prof and knowledge_priority >= 20:
                # Predictive opening/uptime cast: only known, worthwhile casters.
                # This allows early coverage without throwing Mistrust onto melee
                # or unknown enemies where it is likely to expire unused.
                predictive_candidates.append((score, int(enemy_id)))

        selected_pool = active_candidates if active_candidates else predictive_candidates
        reason = "active_caster" if active_candidates else "predictive_known_caster"
        selected_pool.sort(key=lambda item: item[0])
        for _, enemy_id in selected_pool:
            if self._claim_mistrust_target(enemy_id):
                return int(enemy_id), reason
        return 0, "no_useful_unclaimed_target"


    def _has_corpse_near_agent(self, target_agent_id: int, *, radius: float = SORROW_CORPSE_SEARCH_RANGE) -> bool:
        if int(target_agent_id or 0) <= 0:
            return False
        try:
            from Py4GWCoreLib.Py4GWcorelib import Utils

            target_xy = Agent.GetXY(int(target_agent_id))
            for corpse_id in Routines.Agents.GetCorpses(float(POWER_CLUSTER_FILTER_RANGE) + float(radius)) or []:
                try:
                    if int(corpse_id or 0) <= 0 or not Agent.IsValid(int(corpse_id)):
                        continue
                    if Utils.Distance(target_xy, Agent.GetXY(int(corpse_id))) <= float(radius):
                        return True
                except Exception:
                    continue
        except Exception:
            return False
        return False

    def _rank_packet_signet_targets(self, anchor_agent_id: int, *, skill_id: int, require_corpse: bool = False) -> list[int]:
        members = self._get_power_cluster_members(anchor_agent_id) if anchor_agent_id else []
        if not members and anchor_agent_id:
            members = [int(anchor_agent_id)]
        members = [agent_id for agent_id in members if self._is_valid_power_cluster_enemy(agent_id)]
        if not members:
            return []

        if require_corpse:
            members = [agent_id for agent_id in members if self._has_corpse_near_agent(agent_id)]
            if not members:
                return []

        def rank(agent_id: int) -> tuple[int, int, int, int, int, int, float, int]:
            claimed = 1 if self._is_packet_signet_target_claimed(int(skill_id), int(agent_id)) else 0
            corpse = 1 if self._has_corpse_near_agent(int(agent_id)) else 0
            dangerous = 1 if self._is_safe_dangerous_cast(int(agent_id)) else 0
            support = 1 if self._is_monk_or_ritualist_enemy(int(agent_id)) else 0
            try:
                hp = float(Agent.GetHealth(int(agent_id)) or 0.0)
            except Exception:
                hp = 1.0
            near_dead = 1 if hp > 0.0 and hp <= float(KEYSTONE_SIGNET_LOW_HP_AVOID_FRACTION) and not dangerous else 0
            adjacent_count = self._count_signet_of_judgment_adjacent_enemies(int(agent_id))
            return (
                claimed,            # prefer an unclaimed member for short stagger
                near_dead,          # do not spend a dedicated packet lane on a 3-10% foe if alternatives live
                -corpse,            # corpse-adjacent first for Sorrow instant recharge chance
                -dangerous,         # then dangerous casts/support inside the packet
                -support,
                -adjacent_count,    # then best packet center / AoE value
                self._distance_to_player(int(agent_id)),
                int(agent_id),
            )

        members.sort(key=rank)
        # Deterministic per-account rotation fixes the same-frame race where all
        # four clients saw every claim as free and selected the same first member.
        # Rotate only the best claim/near-death bucket so quality still wins.
        if len(members) > 1:
            best = rank(int(members[0]))
            bucket = [m for m in members if rank(int(m))[:2] == best[:2]]
            bucket_set = set(bucket)
            rest = [m for m in members if m not in bucket_set]
            bucket = self._lane_rotate_targets(bucket)
            members = bucket + rest
        return members

    def _get_packet_signet_target(self, skill_id: int, *, require_corpse: bool = False, claim_target: bool = False) -> int:
        split = self._keystone_execute_split_plan()
        if split is not None:
            execute_target, cluster_targets = split
            if execute_target > 0 and not require_corpse:
                return int(execute_target)
            # Keep conditional signets honest: a Sorrow cast requiring a corpse
            # may not pretend a low-HP target has one.
            if cluster_targets:
                available = [int(a) for a in cluster_targets
                             if (not require_corpse or self._has_corpse_near_agent(int(a)))]
                if available:
                    available.sort(key=lambda aid: (
                        self._is_packet_signet_target_claimed(int(skill_id), int(aid)),
                        -self._count_signet_of_judgment_adjacent_enemies(int(aid)),
                        self._distance_to_player(int(aid)), int(aid),
                    ))
                    for aid in available:
                        if self._is_packet_signet_target_claimed(int(skill_id), aid):
                            continue
                        if claim_target and not self._claim_packet_signet_target(int(skill_id), aid):
                            continue
                        return aid
                    # Do not idle merely because packet-target locks overlap.
                    return int(available[0])
                return 0
        # When the original engaged packet scatters, never pick a newly dense
        # but unrelated cluster around the old AoE center.  Use the publisher's
        # recorded victim cohort.  5%-execute leases above retain precedence.
        try:
            from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import get_team_combat_plan
            engaged_plan = get_team_combat_plan(
                filter_range=float(POWER_CLUSTER_FILTER_RANGE),
                minimum_enemies=1, consumer_role="keystone_engaged_cleanup",
            )
            if str(engaged_plan.reason).startswith("engagement_") and str(engaged_plan.mode) == "cleanup":
                members = [int(a) for a in engaged_plan.member_ids
                           if self._is_valid_power_cluster_enemy(int(a))
                           and (not require_corpse or self._has_corpse_near_agent(int(a)))]
                if not members:
                    return 0
                target = int(engaged_plan.focus_target)
                if target not in members:
                    target = min(members, key=lambda aid: (float(Agent.GetHealth(int(aid))), int(aid)))
                # Same enemy until finished. A best-effort claim must not cause
                # another delay or override the protected engagement target.
                if claim_target:
                    self._claim_packet_signet_target(int(skill_id), int(target))
                return int(target)
        except Exception:
            pass
        anchor_agent_id = self._get_power_cluster_anchor()
        if not anchor_agent_id:
            fallback = int(self._small_party_local_fallback_target(require_corpse=bool(require_corpse)) or 0)
            if fallback > 0:
                if claim_target:
                    # Best-effort telemetry/distribution hint only. Never let a
                    # stale claim recreate the wand-only failure in fallback mode.
                    self._claim_packet_signet_target(int(skill_id), int(fallback))
                return int(fallback)
            return 0

        ranked = self._rank_packet_signet_targets(anchor_agent_id, skill_id=int(skill_id), require_corpse=require_corpse)
        if not ranked:
            return 0

        # In real packets, spread all normal offensive signets across
        # different members using one shared cross-skill lock.  In cleanup/single-target mode, allow the same
        # target so the team keeps finishing instead of waiting for no reason.
        packet_size = self._count_signet_of_judgment_adjacent_enemies(anchor_agent_id)
        if packet_size >= POWER_CLUSTER_MIN_ENEMIES:
            if self._small_party_focus_mode():
                focused = self._small_party_focus_order(int(anchor_agent_id), list(ranked))
                if focused:
                    target_agent_id = int(focused[0])
                    if claim_target:
                        # In 8-man focus mode overlap is intentional; publish the
                        # claim when possible but never idle because it is busy.
                        self._claim_packet_signet_target(int(skill_id), target_agent_id)
                    return target_agent_id
            for target_agent_id in ranked:
                if self._is_packet_signet_target_claimed(int(skill_id), int(target_agent_id)):
                    continue
                if claim_target and not self._claim_packet_signet_target(int(skill_id), int(target_agent_id)):
                    continue
                return int(target_agent_id)
            return 0

        target_agent_id = int(ranked[0])
        if claim_target:
            self._claim_packet_signet_target(int(skill_id), target_agent_id)
        return target_agent_id

    def _get_sorrow_signet_target(self, *, require_corpse: bool, claim_target: bool) -> int:
        if not self.IsSkillEquipped(Signet_of_Sorrow_ID):
            return 0
        return self._get_packet_signet_target(
            Signet_of_Sorrow_ID,
            require_corpse=bool(require_corpse),
            claim_target=bool(claim_target),
        )

    def _monk_self_enchantment_count(self) -> int:
        """Count known enchantments, not HR echoes or chants.

        Prefer the effect cache. Agent.IsEnchanted is a conservative one-effect
        fallback when an effect entry cannot be inspected. Never treat HR as an
        enchantment for Signet of Mystic Wrath's damage multiplier.
        """
        player_id = int(Player.GetAgentID() or 0)
        found = set()
        try:
            from Py4GWCoreLib import GLOBAL_CACHE
            for get_list in (GLOBAL_CACHE.Effects.GetEffects, GLOBAL_CACHE.Effects.GetBuffs):
                for effect in get_list(player_id) or []:
                    sid = int(getattr(effect, "skill_id", 0) or 0)
                    if sid > 0 and Skill.Flags.IsEnchantment(sid):
                        found.add(sid)
        except Exception:
            pass
        if found:
            return len(found)
        try:
            return 1 if Agent.IsEnchanted(player_id) else 0
        except Exception:
            return 0

    @staticmethod
    def _monk_known_enemy_adrenaline_skills(agent_id: int) -> int:
        """Use observed skills only; never invent an enemy skillbar.

        The APO Agent.GetObservedSkillbar API may be empty in a given version.
        In that case Rage still deals its unconditional base damage.
        """
        try:
            skill_ids = Agent.GetObservedSkillbar(int(agent_id)) or []
            return sum(1 for sid in set(int(x or 0) for x in skill_ids)
                       if sid > 0 and int(Skill.Data.GetAdrenaline(sid) or 0) > 0)
        except Exception:
            return 0

    def _get_monk_burst_signet_target(self, skill_id: int, *, claim_target: bool = True) -> int:
        """Conditional bonus preferred, unconditional cast never blocked.

        Uses the authoritative shared packet. Only hard mission priority may
        force everyone onto one target. With a single foe/execute, all damage
        signets may converge; with larger groups, packet claims spread casts.
        """
        bark = int(self._get_elite_priority_target() or 0)
        if bark > 0 and self._is_valid_power_cluster_enemy(bark):
            return bark
        pool = list(self._get_zero_idle_enemy_targets())
        if not pool:
            return 0
        if len(pool) == 1:
            return int(pool[0])

        anchor = int(self._get_power_cluster_anchor() or 0)
        ordered = self._rank_packet_signet_targets(anchor, skill_id=int(skill_id)) if anchor else pool
        valid_set = set(int(x) for x in pool)
        ordered = [int(x) for x in ordered if int(x) in valid_set]
        if not ordered:
            ordered = list(pool)

        # Two targets / five Keystone Mesmers: intentional 3:2 split.
        # Larger groups: cover as many different members as possible. The
        # existing high-priority Bark override above remains absolute.
        if self._small_party_focus_mode() and len(ordered) >= 2:
            focus = self._small_party_focus_order(int(anchor or ordered[0]), list(ordered))
            if focus:
                chosen = int(focus[0])
                if not claim_target or self._claim_packet_signet_target(int(skill_id), chosen):
                    return chosen
                # Never stall when another Mesmer has a temporary target claim.
                return chosen

        def bonus(tid):
            if int(skill_id) in (int(Bane_Signet_ID), int(Castigation_Signet_ID)):
                try:
                    return 1 if Agent.IsAttacking(int(tid)) else 0
                except Exception:
                    return 0
            if int(skill_id) == int(Signet_of_Rage_ID):
                return self._monk_known_enemy_adrenaline_skills(int(tid))
            return 0

        # Keep high-value enemy/support preferences already encoded in the
        # shared packet order, and only apply bonus within the same packet.
        choices = list(enumerate(ordered))
        choices.sort(key=lambda row: (-int(bonus(int(row[1]))), row[0]))
        # A claimed target is a soft hint: never idle if all are claimed.
        for _, tid in choices:
            if self._is_packet_signet_target_claimed(int(skill_id), int(tid)):
                continue
            if not claim_target or self._claim_packet_signet_target(int(skill_id), int(tid)):
                return int(tid)
        # Fallback rather than reserving a ready damage signet indefinitely.
        return int(choices[0][1])

    def _get_monk_damage_signet_target(self, skill_id: int, *, claim_target: bool = True) -> int:
        """Fast target selection for Castigation/Bane Signet.

        Prefer an attacking member of the shared cluster because both Monk
        signets gain their full conditional value there.  Never idle waiting
        for an attacker: if none is available, immediately spend the signet on
        the best free member of the same packet.  The existing cross-skill
        packet claim spreads Castigation and Bane across different foes.
        """
        # Apply the same 5%-execute allocation to Monk signet builds.
        split = self._keystone_execute_split_plan()
        if split is not None:
            execute_target, cluster_targets = split
            if int(execute_target) > 0:
                return int(execute_target)
            candidates = [int(a) for a in cluster_targets]
            if candidates:
                candidates.sort(key=lambda a: (
                    1 if self._is_packet_signet_target_claimed(int(skill_id), int(a)) else 0,
                    -int(self._is_attacking_enemy_safe(int(a))),
                    -int(self._count_signet_of_judgment_adjacent_enemies(int(a))),
                    self._distance_to_player(int(a)), int(a),
                ))
                for aid in candidates:
                    if self._is_packet_signet_target_claimed(int(skill_id), int(aid)):
                        continue
                    if claim_target and not self._claim_packet_signet_target(int(skill_id), int(aid)):
                        continue
                    return int(aid)
                return int(candidates[0])
        anchor_agent_id = int(self._get_power_cluster_anchor() or 0)
        if anchor_agent_id <= 0:
            fallback = int(self._small_party_local_fallback_target() or 0)
            if fallback > 0:
                if claim_target:
                    self._claim_packet_signet_target(int(skill_id), int(fallback))
                return int(fallback)
            return 0
        ranked = self._rank_packet_signet_targets(anchor_agent_id, skill_id=int(skill_id))
        if not ranked:
            return 0

        attacking = []
        fallback = []
        for target_agent_id in ranked:
            try:
                is_attacking = bool(Agent.IsAttacking(int(target_agent_id)))
            except Exception:
                is_attacking = False
            (attacking if is_attacking else fallback).append(int(target_agent_id))

        packet_size = self._count_signet_of_judgment_adjacent_enemies(anchor_agent_id)
        ordered = attacking + fallback
        if packet_size >= POWER_CLUSTER_MIN_ENEMIES:
            if self._small_party_focus_mode():
                focused = self._small_party_focus_order(int(anchor_agent_id), list(ordered))
                if focused:
                    target_agent_id = int(focused[0])
                    if claim_target:
                        self._claim_packet_signet_target(int(skill_id), target_agent_id)
                    return target_agent_id
            for target_agent_id in ordered:
                if self._is_packet_signet_target_claimed(int(skill_id), int(target_agent_id)):
                    continue
                if claim_target and not self._claim_packet_signet_target(int(skill_id), int(target_agent_id)):
                    continue
                return int(target_agent_id)
            return 0

        target_agent_id = int(ordered[0])
        if claim_target:
            self._claim_packet_signet_target(int(skill_id), target_agent_id)
        return target_agent_id

    def _is_signet_of_judgment_base_target_usable(self, target_agent_id: int, *, check_claimed: bool = True) -> bool:
        if target_agent_id <= 0:
            return False

        try:
            if not Agent.IsValid(target_agent_id):
                return False
            if not Agent.IsAlive(target_agent_id):
                return False
        except Exception:
            return False

        if check_claimed and self._is_signet_of_judgment_target_claimed(target_agent_id):
            return False

        return True

    @staticmethod
    def _is_signet_of_judgment_target_knocked_down(target_agent_id: int) -> bool:
        try:
            return bool(Routines.Checks.Agents.IsKnockedDown(target_agent_id))
        except Exception:
            return False

    def _count_signet_of_judgment_adjacent_enemies(self, target_agent_id: int) -> int:
        if target_agent_id <= 0:
            return 0

        try:
            from Py4GWCoreLib import AgentArray

            target_xy = Agent.GetXY(target_agent_id)
            enemies = AgentArray.GetEnemyArray()
            enemies = AgentArray.Filter.ByDistance(enemies, target_xy, Range.Adjacent.value)
            enemies = AgentArray.Filter.ByCondition(
                enemies,
                lambda agent_id: Agent.IsValid(agent_id) and Agent.IsAlive(agent_id),
            )
            return int(len(enemies))
        except Exception:
            try:
                return int(Routines.Targeting.CountNearbyEnemies(target_agent_id, Range.Adjacent.value) or 0)
            except Exception:
                return 0

    def _is_signet_of_judgment_control_target_usable(self, target_agent_id: int, *, check_claimed: bool = True) -> bool:
        if not self._is_signet_of_judgment_base_target_usable(target_agent_id, check_claimed=check_claimed):
            return False
        return not self._is_signet_of_judgment_target_knocked_down(target_agent_id)

    def _is_signet_of_judgment_damage_target_usable(self, target_agent_id: int, *, check_claimed: bool = False) -> bool:
        if not self._is_signet_of_judgment_base_target_usable(target_agent_id, check_claimed=check_claimed):
            return False
        return self._count_signet_of_judgment_adjacent_enemies(target_agent_id) >= SIGNET_OF_JUDGMENT_DAMAGE_CLUSTER_MIN

    def _is_signet_of_judgment_cast_target_usable(self, target_agent_id: int) -> bool:
        if not self._is_signet_of_judgment_base_target_usable(target_agent_id, check_claimed=False):
            return False
        if self._distance_to_player(target_agent_id) > float(POWER_CLUSTER_FILTER_RANGE):
            return False
        if self._is_elite_priority_target(target_agent_id):
            return True
        if is_execution_focus_target(target_agent_id):
            return True

        # Simple Power behavior: SoJ is primarily an AoE packet damage skill,
        # but copied SoJ should still be spent on cleanup/single targets when
        # no real packet is available. In single-target mode the Whiteboard
        # claim staggers multiple Keystone Mesmers onto the same enemy instead
        # of wasting all casts in the exact same moment.
        return True

    def _score_signet_of_judgment_target(self, target_agent_id: int, player_pos):
        try:
            from Py4GWCoreLib.Py4GWcorelib import Utils
            distance = Utils.Distance(player_pos, Agent.GetXY(target_agent_id))
        except Exception:
            distance = 0
        nearby_count = self._count_signet_of_judgment_adjacent_enemies(target_agent_id)
        try:
            from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
            threat = int(EnemyKnowledge.threat_bonus(target_agent_id))
        except Exception:
            threat = 0
        return (-nearby_count, -threat, distance)

    def _pick_signet_of_judgment_target(self, validator, *, check_claimed_for_picker: bool) -> int:
        try:
            target_agent_id = Routines.Targeting.PickClusteredTarget(
                cluster_radius=Range.Adjacent.value,
                preferred_condition=lambda agent_id: validator(
                    int(agent_id),
                    check_claimed=check_claimed_for_picker,
                ),
                filter_radius=Range.Spellcast.value,
            )
            if target_agent_id:
                return int(target_agent_id)
        except Exception:
            pass

        try:
            from Py4GWCoreLib import AgentArray

            player_pos = Player.GetXY()
            enemies = AgentArray.GetEnemyArray()
            enemies = AgentArray.Filter.ByDistance(enemies, player_pos, Range.Spellcast.value)
            enemies = AgentArray.Filter.ByCondition(
                enemies,
                lambda agent_id: validator(
                    int(agent_id),
                    check_claimed=check_claimed_for_picker,
                ),
            )
            if not enemies:
                return 0

            return int(sorted(enemies, key=lambda agent_id: self._score_signet_of_judgment_target(int(agent_id), player_pos))[0])
        except Exception:
            return 0

    def _rank_signet_of_judgment_cluster_members(self, anchor_agent_id: int) -> list[int]:
        members = self._get_power_cluster_members(anchor_agent_id)
        if not members:
            return []

        def rank(agent_id: int) -> tuple[int, int, int, int, int, float, int]:
            claimed = self._get_signet_of_judgment_claim_count(agent_id)
            dangerous = 1 if self._is_safe_dangerous_cast(agent_id) else 0
            support = 1 if self._is_monk_or_ritualist_enemy(agent_id) else 0
            standing = 1 if not self._is_signet_of_judgment_target_knocked_down(agent_id) else 0
            try:
                hp = float(Agent.GetHealth(int(agent_id)) or 0.0)
            except Exception:
                hp = 1.0
            near_dead = 1 if hp > 0.0 and hp <= float(KEYSTONE_SIGNET_LOW_HP_AVOID_FRACTION) and not dangerous else 0
            return (
                claimed,          # lowest occupancy first, then balanced duplicate casts
                near_dead,        # avoid overkilling a nearly dead packet member when alternatives exist
                -dangerous,       # dangerous cast inside the packet first
                -support,         # then Monk/Ritu inside the packet
                -standing,        # then fresh KD if available
                self._distance_to_player(agent_id),
                int(agent_id),
            )

        members.sort(key=rank)
        if len(members) > 1:
            best = rank(int(members[0]))
            # Preserve occupancy/near-death balancing, rotate equal-quality first
            # choices by account lane to survive simultaneous cross-client reads.
            bucket = [m for m in members if rank(int(m))[:2] == best[:2]]
            bucket_set = set(bucket)
            rest = [m for m in members if m not in bucket_set]
            bucket = self._lane_rotate_targets(bucket)
            members = bucket + rest
        return members

    def _get_signet_of_judgment_target(self, *, claim_target: bool = False) -> int:
        def claim_or_zero(target_agent_id: int) -> int:
            if not target_agent_id:
                return 0
            if claim_target and not self._claim_signet_of_judgment_target(target_agent_id):
                return 0
            return int(target_agent_id)

        priority_target = self._get_elite_priority_target()
        if priority_target and self._is_signet_of_judgment_cast_target_usable(priority_target):
            # Mission-priority targets should be deleted first. These are treated
            # like shared single-target mode: everyone stays on the priority
            # target, but the claim staggers copied SoJ casts.
            return claim_or_zero(priority_target)

        # Copied SoJ must not bypass the two/three-Mesmer execute assignment.
        split = self._keystone_execute_split_plan()
        if split is not None:
            execute_target, cluster_targets = split
            if execute_target > 0 and self._is_signet_of_judgment_cast_target_usable(int(execute_target)):
                return claim_or_zero(int(execute_target))
            candidates = [int(a) for a in cluster_targets
                          if self._is_signet_of_judgment_cast_target_usable(int(a))]
            candidates.sort(key=lambda aid: (
                int(self._get_signet_of_judgment_claim_count(int(aid))),
                -int(self._count_signet_of_judgment_adjacent_enemies(int(aid))),
                self._distance_to_player(int(aid)), int(aid),
            ))
            for aid in candidates:
                picked = claim_or_zero(int(aid))
                if picked:
                    return picked
            return int(candidates[0]) if candidates else 0

        # Team packet mode: choose the same enemy group as the team. If there is
        # a real packet, reserve different members inside the packet so copied
        # SoJ pressure is spread across the ball. If only one/scattered target is
        # available, all Keystone Mesmers use the same anchor and the Whiteboard
        # claim simply staggers the casts instead of blocking SoJ completely.
        anchor_agent_id = self._get_power_cluster_anchor()
        if anchor_agent_id and self._is_signet_of_judgment_cast_target_usable(anchor_agent_id):
            members = self._rank_signet_of_judgment_cluster_members(anchor_agent_id)
            packet_size = self._count_signet_of_judgment_adjacent_enemies(anchor_agent_id)

            if packet_size < SIGNET_OF_JUDGMENT_DAMAGE_CLUSTER_MIN or len(members) <= 1:
                return claim_or_zero(anchor_agent_id)

            if self._small_party_focus_mode():
                members = self._small_party_focus_order(int(anchor_agent_id), list(members))
                if members:
                    # Occupancy claims already allow duplicate SoJ casts. In
                    # 8-man mode the primary-heavy order deliberately converts
                    # that flexibility into a harder kill spike.
                    return claim_or_zero(int(members[0]))

            for target_agent_id in members:
                if not self._is_signet_of_judgment_cast_target_usable(target_agent_id):
                    continue
                claimed_target = claim_or_zero(target_agent_id)
                if claimed_target:
                    return claimed_target

            # Every useful member is already claimed. Damage uptime is more
            # important than a perfect stagger: immediately fall back to the
            # best valid member instead of idling until the short claim expires.
            for target_agent_id in members:
                if self._is_signet_of_judgment_cast_target_usable(target_agent_id):
                    return int(target_agent_id)
            return int(anchor_agent_id)

        # In 8-man scattered fights, fail open to one locally castable normal
        # enemy rather than leaving a ready copied SoJ unused while wanding.
        # 9-12 man remains strict-authoritative, and a single hostile spirit is
        # explicitly excluded by the fallback helper.
        fallback = int(self._small_party_local_fallback_target() or 0)
        if fallback > 0 and self._is_signet_of_judgment_cast_target_usable(fallback):
            if claim_target:
                self._claim_signet_of_judgment_target(int(fallback))
            return int(fallback)
        return 0

    def _cast_copied_soj_opportunistic_interrupt(self, snapshot: _KeystoneBarSnapshot):
        """Use a currently copied SoJ as the slow emergency interrupt fallback.

        PI Mesmer direct interrupts get first refusal through the shared claim
        election.  This method never waits for SoJ to recharge and never moves
        the team's damage focus: it fires only when SoJ is ready right now, then
        RestoreTarget returns this Keystone Mesmer to the canonical packet.
        """
        skill_id = int(Signet_of_Judgment_ID or 0)
        if skill_id <= 0 or not self.IsSkillEquipped(skill_id):
            return False
        if not self._is_skill_strictly_ready(skill_id):
            return False
        if not snapshot.enemy_casting:
            return False

        anchor = int(self._get_power_cluster_anchor() or 0)
        focus_members = self._get_power_cluster_members(anchor) if anchor > 0 else []
        cleanup_mode = bool(anchor > 0 and len(focus_members) <= 1)
        try:
            energy_ratio = float(Agent.GetEnergy(Player.GetAgentID()))
        except Exception:
            energy_ratio = 1.0

        opportunities = rank_opportunistic_interrupt_options(
            [skill_id],
            energy_ratio=energy_ratio,
            range_value=int(Range.Spellcast.value),
            origin_agent_id=int(Player.GetAgentID() or 0),
            cleanup_mode=bool(cleanup_mode),
            focus_ids=tuple(focus_members),
        )
        if not opportunities:
            return False

        focus_set = set(int(x) for x in focus_members)
        for score, target_id, casting_skill_id, our_skill_id, mode in opportunities:
            target_id = int(target_id)
            casting_skill_id = int(casting_skill_id)
            if int(our_skill_id) != skill_id:
                continue
            if not self._is_signet_of_judgment_control_target_usable(
                target_id, check_claimed=False
            ):
                continue
            if not post_interrupt_claim(target_id, casting_skill_id, skill_id):
                continue

            keystone_covered_casts = (
                self._keystone_adjacent_danger_casts(target_id)
                if snapshot.has_keystone_signet else []
            )
            did_cast = yield from self.CastSkillIDAndRestoreTarget(
                skill_id=skill_id,
                target_agent_id=target_id,
                extra_condition=lambda tid=target_id, sid=casting_skill_id: (
                    self._target_still_casting_safe_skill(tid, sid)
                    and self._is_signet_of_judgment_control_target_usable(
                        tid, check_claimed=False
                    )
                ),
                log=False,
                aftercast_delay=250,
            )
            if not did_cast:
                release_interrupt_claim(
                    target_id, casting_skill_id, reason="copied_soj_danger_peel_not_fired"
                )
                continue

            self._note_non_keystone_signet_cast()
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.register_interrupt_fired(target_id, casting_skill_id, skill_id)
                CombatDebug.log_event(
                    "KEYSTONE_COPIED_SOJ_OPPORTUNISTIC_INTERRUPT",
                    target_id=target_id, enemy_skill_id=casting_skill_id,
                    our_skill_id=skill_id, mode=str(mode), score=round(float(score), 1),
                    off_focus=bool(target_id not in focus_set), cleanup=bool(cleanup_mode),
                    policy="slow_fallback_ready_now_then_restore_cluster_focus",
                )
            except Exception:
                pass
            if keystone_covered_casts:
                self._register_keystone_coverage(
                    target_id, skill_id, keystone_covered_casts
                )
            return True

        return False

    def _cast_copied_signet_of_judgment(self, snapshot: _KeystoneBarSnapshot):
        if not self.IsSkillEquipped(Signet_of_Judgment_ID):
            return False
        if not snapshot.enemy_in_spellcast:
            return False

        target_agent_id = self._get_signet_of_judgment_target(claim_target=True)
        if not target_agent_id:
            return False

        cast_condition = lambda: self._is_signet_of_judgment_cast_target_usable(target_agent_id)

        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Signet_of_Judgment_ID,
            target_agent_id=target_agent_id,
            extra_condition=cast_condition,
            log=False,
            aftercast_delay=250,
        )
        if did_cast:
            self._note_non_keystone_signet_cast()
            self._log_primary_signet_cast(
                Signet_of_Judgment_ID,
                target_agent_id,
                reason="copied_soj_aggressive_cluster_or_cleanup",
                path="mimicry_copied_soj",
            )
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                current_target = int(Player.GetTargetID() or 0)
                CombatDebug.log_event(
                    "MIMICRY_SOJ_CAST",
                    target_id=int(target_agent_id),
                    current_target_after_cast=int(current_target),
                    target_is_valid_enemy=bool(target_agent_id > 0 and Routines.Checks.Agents.IsAlive(target_agent_id)),
                    copied_window_age_ms=(
                        max(0, int(get_game_tick() or 0) - int(self._mimicry_soj_equipped_since))
                        if self._mimicry_soj_equipped_since > 0 else -1
                    ),
                )
            except Exception:
                pass
            try:
                from Py4GWCoreLib.Builds.Skills import Telemetry
                Telemetry.count("keystone.copied_soj_cast")
            except Exception:
                pass
        return did_cast

    @staticmethod
    def _get_safe_casting_skill_id(target_agent_id: int) -> int:
        return get_danger_casting_skill_id(target_agent_id)

    def _is_safe_dangerous_cast(self, target_agent_id: int) -> bool:
        return is_dangerous_cast(target_agent_id)

    def _safe_danger_interrupt_sort_key(self, target_agent_id: int, player_pos):
        return danger_sort_key(target_agent_id, player_pos)

    def _pick_safe_dangerous_interrupt_target(self, *, interrupter_skill_id: int = 0, validator=None) -> tuple[int, int]:
        return claim_best_dangerous_cast(
            range_value=Range.Spellcast.value,
            interrupter_skill_id=int(interrupter_skill_id or 0),
            validator=validator,
        )

    def _target_still_casting_safe_skill(self, target_agent_id: int, casting_skill_id: int) -> bool:
        return danger_target_still_casting_skill(target_agent_id, casting_skill_id)

    def _is_keystone_proxy_target_usable(
        self,
        proxy_agent_id: int,
        dangerous_caster_id: int,
        signet_skill_id: int,
    ) -> bool:
        """True when a foe-targeted signet will proc Keystone onto the caster."""
        proxy_agent_id = int(proxy_agent_id or 0)
        dangerous_caster_id = int(dangerous_caster_id or 0)
        signet_skill_id = int(signet_skill_id or 0)
        if proxy_agent_id <= 0 or dangerous_caster_id <= 0 or signet_skill_id <= 0:
            return False
        if proxy_agent_id == dangerous_caster_id:
            # Keystone affects all *other* adjacent foes, not the signet target.
            return False
        if not self._is_valid_power_cluster_enemy(proxy_agent_id):
            return False
        try:
            if not Agent.IsValid(dangerous_caster_id) or not Agent.IsAlive(dangerous_caster_id):
                return False
            from Py4GWCoreLib.Py4GWcorelib import Utils
            if Utils.Distance(Agent.GetXY(proxy_agent_id), Agent.GetXY(dangerous_caster_id)) > float(Range.Adjacent.value):
                return False
        except Exception:
            return False
        # The signet's own conditional rider may be weak on this proxy, but
        # Keystone still triggers because the signet targets a foe. Emergency
        # interrupt value therefore overrides normal damage/attack conditions.
        return True

    def _keystone_adjacent_danger_casts(self, signet_target_id: int) -> list[tuple[int, int]]:
        """Return dangerous casts Keystone would interrupt around this target.

        Keystone affects every *other* foe adjacent to the signet target.  The
        target itself is intentionally excluded, even when it is casting.
        """
        signet_target_id = int(signet_target_id or 0)
        if signet_target_id <= 0:
            return []
        try:
            from Py4GWCoreLib import AgentArray
            from Py4GWCoreLib.Py4GWcorelib import Utils

            target_xy = Agent.GetXY(signet_target_id)
            covered: list[tuple[int, int]] = []
            for enemy_id in AgentArray.GetEnemyArray() or []:
                enemy_id = int(enemy_id or 0)
                if enemy_id <= 0 or enemy_id == signet_target_id:
                    continue
                if not Agent.IsValid(enemy_id) or not Agent.IsAlive(enemy_id):
                    continue
                if Utils.Distance(target_xy, Agent.GetXY(enemy_id)) > float(Range.Adjacent.value):
                    continue
                casting_skill_id = int(get_danger_casting_skill_id(enemy_id) or 0)
                if casting_skill_id <= 0 or not is_dangerous_cast(enemy_id):
                    continue
                covered.append((enemy_id, casting_skill_id))
            covered.sort(key=lambda item: danger_sort_key(int(item[0]), Player.GetXY()))
            return covered
        except Exception:
            return []

    @staticmethod
    def _register_keystone_coverage(
        signet_target_id: int,
        our_skill_id: int,
        covered_casts: list[tuple[int, int]],
    ) -> None:
        """Track every dangerous cast covered by one Keystone signet proc."""
        unique: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for caster_id, enemy_skill_id in covered_casts or []:
            key = (int(caster_id or 0), int(enemy_skill_id or 0))
            if key[0] <= 0 or key[1] <= 0 or key in seen:
                continue
            seen.add(key)
            unique.append(key)
        if not unique:
            return
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "KEYSTONE_AOE_INTERRUPT_COVERAGE",
                signet_target_id=int(signet_target_id),
                our_skill_id=int(our_skill_id),
                covered_count=len(unique),
                covered=";".join(f"{caster_id}:{skill_id}" for caster_id, skill_id in unique),
            )
            for caster_id, enemy_skill_id in unique:
                CombatDebug.register_interrupt_fired(
                    int(caster_id), int(enemy_skill_id), int(our_skill_id)
                )
        except Exception:
            pass

    def _pick_keystone_proxy_target(self, dangerous_caster_id: int, signet_skill_id: int) -> int:
        """Pick the best signet target adjacent to, but different from, caster."""
        try:
            from Py4GWCoreLib import AgentArray
            enemies = AgentArray.GetEnemyArray()
            enemies = AgentArray.Filter.ByDistance(
                enemies,
                Player.GetXY(),
                float(Range.Spellcast.value),
            )
            candidates = [
                int(agent_id)
                for agent_id in enemies or []
                if self._is_keystone_proxy_target_usable(
                    int(agent_id), int(dangerous_caster_id), int(signet_skill_id)
                )
            ]
        except Exception:
            candidates = []
        if not candidates:
            return 0
        def proxy_sort_key(agent_id: int):
            covered = self._keystone_adjacent_danger_casts(int(agent_id))
            best_priority = min(
                (danger_sort_key(int(caster_id), Player.GetXY())[0] for caster_id, _ in covered),
                default=9999,
            )
            return (
                -len(covered),
                int(best_priority),
                -self._count_signet_of_judgment_adjacent_enemies(int(agent_id)),
                self._distance_to_player(int(agent_id)),
                int(agent_id),
            )

        candidates.sort(key=proxy_sort_key)
        return int(candidates[0])

    def _cast_keystone_proxy_interrupt(self, snapshot: _KeystoneBarSnapshot):
        """Spend a ready foe-targeted signet to trigger Keystone AoE interrupt.

        The global claim is made against the real dangerous caster, while the
        actual signet is cast on an adjacent proxy. Outcome logging also tracks
        the real caster, so native `interrupted/stopped/finished` results remain
        meaningful.
        """
        if not snapshot.has_keystone_signet or not snapshot.enemy_casting:
            return False

        for signet_skill_id in KEYSTONE_PROXY_INTERRUPT_SIGNET_ORDER:
            signet_skill_id = int(signet_skill_id or 0)
            if signet_skill_id <= 0 or not self.IsSkillEquipped(signet_skill_id):
                continue
            if not self.CanCastSkillID(signet_skill_id):
                continue

            proxy_cache: dict[int, int] = {}

            def has_proxy(enemy_id: int, cast_id: int) -> bool:
                remaining_ms = get_enemy_cast_remaining_ms(int(enemy_id), int(cast_id))
                timing_budget_ms = (
                    int(self._approach_interrupt_budget_ms(int(signet_skill_id)))
                    + int(KEYSTONE_E14_EXTRA_PROC_MARGIN_MS)
                )
                if remaining_ms is not None and float(remaining_ms) <= float(timing_budget_ms):
                    return False
                proxy_id = self._pick_keystone_proxy_target(int(enemy_id), signet_skill_id)
                if proxy_id > 0:
                    proxy_cache[int(enemy_id)] = int(proxy_id)
                    return True
                return False

            dangerous_caster_id, casting_skill_id = self._pick_safe_dangerous_interrupt_target(
                interrupter_skill_id=signet_skill_id,
                validator=has_proxy,
            )
            if dangerous_caster_id <= 0 or casting_skill_id <= 0:
                continue

            proxy_agent_id = int(proxy_cache.get(int(dangerous_caster_id), 0) or 0)
            if proxy_agent_id <= 0:
                proxy_agent_id = self._pick_keystone_proxy_target(
                    int(dangerous_caster_id), signet_skill_id
                )
            if proxy_agent_id <= 0:
                release_interrupt_claim(
                    dangerous_caster_id,
                    casting_skill_id,
                    reason="keystone_proxy_missing",
                )
                continue

            remaining_ms = get_enemy_cast_remaining_ms(
                int(dangerous_caster_id), int(casting_skill_id)
            )
            timing_budget_ms = (
                int(self._approach_interrupt_budget_ms(int(signet_skill_id)))
                + int(KEYSTONE_E14_EXTRA_PROC_MARGIN_MS)
            )
            if remaining_ms is not None and float(remaining_ms) <= float(timing_budget_ms):
                release_interrupt_claim(
                    dangerous_caster_id,
                    casting_skill_id,
                    reason="stage9_e14b_timing_window_closed",
                )
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "KEYSTONE_E14_TIMING_REJECT",
                        caster_id=int(dangerous_caster_id),
                        enemy_skill_id=int(casting_skill_id),
                        proxy_id=int(proxy_agent_id),
                        our_skill_id=int(signet_skill_id),
                        remaining_ms=round(float(remaining_ms), 1),
                        required_ms=int(timing_budget_ms),
                        policy="exp36_stage17_e14c_conservative_activation_ping_proc_budget",
                    )
                except Exception:
                    pass
                continue

            keystone_covered_casts = self._keystone_adjacent_danger_casts(proxy_agent_id)
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "KEYSTONE_E14_PROXY_INTERRUPT_SELECTED",
                    caster_id=int(dangerous_caster_id),
                    enemy_skill_id=int(casting_skill_id),
                    proxy_id=int(proxy_agent_id),
                    our_skill_id=int(signet_skill_id),
                    covered_count=int(len(keystone_covered_casts)),
                    remaining_ms=(round(float(remaining_ms), 1) if remaining_ms is not None else -1.0),
                    required_ms=int(timing_budget_ms),
                    policy="exp36_stage17_e14c_conservative_timing_gated_proxy_interrupt",
                )
            except Exception:
                pass
            did_cast = yield from self.CastSkillIDAndRestoreTarget(
                skill_id=signet_skill_id,
                target_agent_id=proxy_agent_id,
                extra_condition=lambda: (
                    self._target_still_casting_safe_skill(
                        dangerous_caster_id, casting_skill_id
                    )
                    and self._is_keystone_proxy_target_usable(
                        proxy_agent_id, dangerous_caster_id, signet_skill_id
                    )
                    and Routines.Checks.Effects.HasBuff(
                        Player.GetAgentID(), Keystone_Signet_ID
                    )
                ),
                log=False,
                aftercast_delay=250,
            )
            if did_cast:
                self._note_non_keystone_signet_cast()
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "KEYSTONE_PROXY_INTERRUPT_FIRED",
                        caster_id=int(dangerous_caster_id),
                        enemy_skill_id=int(casting_skill_id),
                        proxy_id=int(proxy_agent_id),
                        our_skill_id=int(signet_skill_id),
                        covered_count=int(len(keystone_covered_casts)),
                        policy="exp36_stage17_e14c_conservative_timing_gated_proxy_interrupt",
                    )
                except Exception:
                    pass
                # The proc can stop several dangerous casts at once. Register
                # every caster that was adjacent to the proxy when the command
                # was sent, not only the primary claimed caster.
                self._register_keystone_coverage(
                    proxy_agent_id, signet_skill_id, keystone_covered_casts
                )
                return True

            release_interrupt_claim(
                dangerous_caster_id,
                casting_skill_id,
                reason="keystone_proxy_not_fired",
            )

        return False

    @staticmethod
    def _approach_direct_interrupt_ids() -> tuple[int, ...]:
        return tuple(
            int(skill_id)
            for skill_id in (Power_Drain_ID, Cry_of_Frustration_ID, Signet_of_Disruption_ID)
            if int(skill_id or 0) > 0
        )

    def _ready_direct_approach_interrupt_skill(self) -> int:
        for skill_id in self._approach_direct_interrupt_ids():
            try:
                if self.IsSkillEquipped(int(skill_id)) and self.CanCastSkillID(int(skill_id)):
                    return int(skill_id)
            except Exception:
                continue
        return 0

    @staticmethod
    def _shared_skillbar_has_ready_skill(account, skill_ids: tuple[int, ...]) -> bool:
        try:
            agent_data = getattr(account, "AgentData", None)
            skillbar = getattr(agent_data, "Skillbar", None)
            if skillbar is None or int(getattr(skillbar, "CastingSkillID", 0) or 0) > 0:
                return False
            wanted = set(int(x) for x in skill_ids if int(x or 0) > 0)
            for skill in getattr(skillbar, "Skills", ()) or ():
                if int(getattr(skill, "Id", 0) or 0) not in wanted:
                    continue
                if float(getattr(skill, "Recharge", 0.0) or 0.0) <= 0.0:
                    return True
            return False
        except Exception:
            return False

    @staticmethod
    def _shared_skillbar_contains(account, skill_id: int) -> bool:
        try:
            skillbar = getattr(getattr(account, "AgentData", None), "Skillbar", None)
            return any(
                int(getattr(skill, "Id", 0) or 0) == int(skill_id)
                for skill in (getattr(skillbar, "Skills", ()) or ())
            )
        except Exception:
            return False

    def _is_selected_approach_mesmer(
        self,
        target_agent_id: int,
        cast_target_id: int,
        our_skill_id: int,
        *,
        require_keystone: bool,
    ) -> bool:
        """Elect the closest approach-capable account deterministically.

        Any account with a ready direct interrupt already inside normal range
        gets first refusal.  Otherwise only accounts that can execute this exact
        approach plan (direct skill or Keystone proxy signet) are ranked.
        """
        try:
            from Py4GWCoreLib import GLOBAL_CACHE
            from Py4GWCoreLib.Py4GWcorelib import Utils

            own_email = str(Player.GetAccountEmail() or "").strip()
            own_party_id = int(GLOBAL_CACHE.Party.GetPartyID() or 0)
            caster_xy = Agent.GetXY(int(target_agent_id))
            cast_target_xy = Agent.GetXY(int(cast_target_id))
            direct_ids = self._approach_direct_interrupt_ids()
            approach_candidates: list[tuple[float, str]] = []

            for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
                if not bool(getattr(account, "IsSlotActive", False)):
                    continue
                if bool(getattr(account, "IsIsolated", False)):
                    continue
                party_id = int(getattr(getattr(account, "AgentPartyData", None), "PartyID", 0) or 0)
                if own_party_id > 0 and party_id > 0 and party_id != own_party_id:
                    continue
                agent_data = getattr(account, "AgentData", None)
                agent_id = int(getattr(agent_data, "AgentID", 0) or 0)
                if agent_id <= 0 or not Agent.IsValid(agent_id) or not Agent.IsAlive(agent_id):
                    continue

                # A ready direct interrupt in normal caster range always wins.
                if self._shared_skillbar_has_ready_skill(account, direct_ids):
                    if float(Utils.Distance(Agent.GetXY(agent_id), caster_xy)) <= float(Range.Spellcast.value):
                        return False

                if not self._shared_skillbar_has_ready_skill(account, (int(our_skill_id),)):
                    continue
                if require_keystone:
                    if not self._shared_skillbar_contains(account, Keystone_Signet_ID):
                        continue
                    try:
                        if not Routines.Checks.Effects.HasBuff(agent_id, Keystone_Signet_ID):
                            continue
                    except Exception:
                        continue
                distance = float(Utils.Distance(Agent.GetXY(agent_id), cast_target_xy))
                if distance <= float(Range.Spellcast.value):
                    return False
                if distance > float(INTERRUPT_APPROACH_SCAN_RANGE):
                    continue
                email = str(getattr(account, "AccountEmail", "") or "").strip()
                if email:
                    approach_candidates.append((distance, email))

            if not approach_candidates or not own_email:
                return False
            approach_candidates.sort(key=lambda item: (float(item[0]), str(item[1])))
            return str(approach_candidates[0][1]) == own_email
        except Exception:
            return False

    def _pick_keystone_proxy_target_for_approach(
        self,
        dangerous_caster_id: int,
        signet_skill_id: int,
    ) -> int:
        """Find an adjacent proxy even while it is just outside our cast range."""
        try:
            from Py4GWCoreLib import AgentArray
            from Py4GWCoreLib.Py4GWcorelib import Utils
            caster_xy = Agent.GetXY(int(dangerous_caster_id))
            candidates = []
            for enemy_id in AgentArray.GetEnemyArray() or []:
                enemy_id = int(enemy_id or 0)
                if enemy_id <= 0 or enemy_id == int(dangerous_caster_id):
                    continue
                if not Agent.IsValid(enemy_id) or not Agent.IsAlive(enemy_id):
                    continue
                enemy_xy = Agent.GetXY(enemy_id)
                if Utils.Distance(enemy_xy, caster_xy) > float(Range.Adjacent.value):
                    continue
                player_distance = float(Utils.Distance(Player.GetXY(), enemy_xy))
                if player_distance > float(INTERRUPT_APPROACH_SCAN_RANGE):
                    continue
                covered = self._keystone_adjacent_danger_casts(enemy_id)
                candidates.append((
                    -len(covered),
                    player_distance,
                    int(enemy_id),
                ))
            if not candidates:
                return 0
            candidates.sort()
            return int(candidates[0][2])
        except Exception:
            return 0

    def _ready_keystone_proxy_approach_plan(self, dangerous_caster_id: int) -> tuple[int, int]:
        try:
            if not Routines.Checks.Effects.HasBuff(Player.GetAgentID(), Keystone_Signet_ID):
                return (0, 0)
        except Exception:
            return (0, 0)
        for signet_skill_id in KEYSTONE_PROXY_INTERRUPT_SIGNET_ORDER:
            signet_skill_id = int(signet_skill_id or 0)
            if signet_skill_id <= 0:
                continue
            try:
                if not self.IsSkillEquipped(signet_skill_id) or not self.CanCastSkillID(signet_skill_id):
                    continue
            except Exception:
                continue
            proxy_id = self._pick_keystone_proxy_target_for_approach(
                int(dangerous_caster_id), signet_skill_id
            )
            if proxy_id > 0:
                return (signet_skill_id, int(proxy_id))
        return (0, 0)

    @staticmethod
    def _approach_settings() -> tuple[float, float, int]:
        max_extra = float(INTERRUPT_APPROACH_DEFAULT_MAX_EXTRA)
        leader_tether = float(INTERRUPT_APPROACH_DEFAULT_LEADER_TETHER)
        move_cooldown = int(INTERRUPT_APPROACH_MOVE_COOLDOWN_MS)
        try:
            from Py4GWCoreLib.Builds.Skills import SimplePowerSettings
            max_extra = float(SimplePowerSettings.get_value(
                "interrupt_approach_max_extra_distance", max_extra
            ))
            leader_tether = float(SimplePowerSettings.get_value(
                "interrupt_approach_leader_tether", leader_tether
            ))
            move_cooldown = int(SimplePowerSettings.get_value(
                "interrupt_approach_move_cooldown_ms", move_cooldown
            ))
        except Exception:
            pass
        return (
            max(120.0, min(650.0, max_extra)),
            max(700.0, min(1800.0, leader_tether)),
            max(80, min(350, move_cooldown)),
        )

    @staticmethod
    def _approach_destination(target_agent_id: int) -> tuple[tuple[float, float], float, float] | None:
        try:
            from Py4GWCoreLib.Py4GWcorelib import Utils
            player_xy = Player.GetXY()
            target_xy = Agent.GetXY(int(target_agent_id))
            distance = float(Utils.Distance(player_xy, target_xy))
            if distance <= 0.001:
                return None
            advance = max(0.0, distance - float(INTERRUPT_APPROACH_CAST_STANDOFF))
            ux = (float(target_xy[0]) - float(player_xy[0])) / distance
            uy = (float(target_xy[1]) - float(player_xy[1])) / distance
            destination = (
                float(player_xy[0]) + ux * (advance + float(INTERRUPT_APPROACH_ARRIVAL_PADDING)),
                float(player_xy[1]) + uy * (advance + float(INTERRUPT_APPROACH_ARRIVAL_PADDING)),
            )
            return destination, distance, advance
        except Exception:
            return None

    @staticmethod
    def _approach_within_leader_tether(destination: tuple[float, float], leader_tether: float) -> bool:
        try:
            from Py4GWCoreLib.Party import Party
            from Py4GWCoreLib.Py4GWcorelib import Utils
            leader_id = int(Party.GetPartyLeaderID() or 0)
            if leader_id <= 0 or not Agent.IsValid(leader_id):
                return True
            leader_xy = Agent.GetXY(leader_id)
            # Do not let an already separated account chase farther away.
            if Utils.Distance(Player.GetXY(), leader_xy) > float(leader_tether) + 180.0:
                return False
            return Utils.Distance(destination, leader_xy) <= float(leader_tether)
        except Exception:
            return False

    @staticmethod
    def _approach_interrupt_budget_ms(our_skill_id: int) -> int:
        activation_ms = 750
        ping_ms = 100
        try:
            from Py4GWCoreLib.HeroAI import interrupt as reforged_interrupt
            fast_casting_level = int(reforged_interrupt._get_player_fast_casting_level() or 0)
            try:
                activation_s, _ = Routines.Checks.Skills.apply_fast_casting(
                    int(our_skill_id), int(fast_casting_level)
                )
                activation_ms = max(0, int(float(activation_s) * 1000.0))
            except Exception:
                activation_ms = max(0, int(float(Skill.Data.GetActivation(int(our_skill_id)) or 0.0) * 1000.0))
            try:
                ping_ms = int(reforged_interrupt._PING_HANDLER.GetCurrentPing() or 100)
            except Exception:
                ping_ms = 100
        except Exception:
            try:
                activation_ms = max(0, int(float(Skill.Data.GetActivation(int(our_skill_id)) or 0.0) * 1000.0))
            except Exception:
                pass
        if ping_ms <= 0:
            ping_ms = 100
        return int(activation_ms) + int(float(ping_ms) * 1.2) + int(INTERRUPT_APPROACH_FINAL_MARGIN_MS)

    def _find_lethal_aoe_approach_candidate(self):
        try:
            from Py4GWCoreLib.Builds.Skills import SimplePowerSettings
            if not SimplePowerSettings.is_feature_enabled("lethal_aoe_interrupt_approach", True):
                return None
        except Exception:
            pass

        direct_skill_id = self._ready_direct_approach_interrupt_skill()
        max_extra, leader_tether, _ = self._approach_settings()
        candidates = []
        for target_agent_id, enemy_skill_id in get_dangerous_casts_in_range(
            int(INTERRUPT_APPROACH_SCAN_RANGE)
        ):
            try:
                target_agent_id = int(target_agent_id)
                enemy_skill_id = int(enemy_skill_id)
                if not is_lethal_aoe_skill(enemy_skill_id):
                    continue
                if not danger_target_still_casting_skill(target_agent_id, enemy_skill_id):
                    continue

                mode = "direct"
                our_skill_id = int(direct_skill_id or 0)
                cast_target_id = int(target_agent_id)
                if our_skill_id <= 0:
                    our_skill_id, cast_target_id = self._ready_keystone_proxy_approach_plan(
                        int(target_agent_id)
                    )
                    mode = "keystone_proxy"
                if our_skill_id <= 0 or cast_target_id <= 0:
                    continue

                info = self._approach_destination(cast_target_id)
                if info is None:
                    continue
                destination, distance, advance = info
                if distance <= float(Range.Spellcast.value) - 15.0:
                    continue
                if advance > float(max_extra):
                    continue
                if not self._approach_within_leader_tether(destination, leader_tether):
                    continue
                if is_position_in_active_aoe(destination, padding=45.0, critical_only=False):
                    continue
                remaining_ms = get_enemy_cast_remaining_ms(target_agent_id, enemy_skill_id)
                if remaining_ms is None:
                    continue
                run_ms = int(
                    (float(advance) / float(INTERRUPT_APPROACH_MOVEMENT_SPEED_GW_S))
                    * 1000.0
                    * float(INTERRUPT_APPROACH_PATH_FACTOR)
                )
                total_budget = run_ms + self._approach_interrupt_budget_ms(our_skill_id)
                if int(remaining_ms) < int(total_budget):
                    continue
                if not self._is_selected_approach_mesmer(
                    target_agent_id,
                    cast_target_id,
                    our_skill_id,
                    require_keystone=(mode == "keystone_proxy"),
                ):
                    continue
                candidates.append((
                    int(remaining_ms),
                    float(distance),
                    int(target_agent_id),
                    int(cast_target_id),
                    int(enemy_skill_id),
                    int(our_skill_id),
                    str(mode),
                    destination,
                    int(total_budget),
                ))
            except Exception:
                continue

        if not candidates:
            return None
        # Urgent feasible AoE first; nearest caster is the stable tie-breaker.
        candidates.sort(key=lambda item: (int(item[0]), float(item[1]), int(item[2])))
        return candidates[0]

    def _clear_interrupt_approach(
        self,
        *,
        reason: str = "",
        release_claim: bool = True,
        log_abort: bool = True,
    ) -> None:
        state = self._interrupt_approach
        if state.active and release_claim:
            release_interrupt_claim(
                int(state.target_agent_id),
                int(state.enemy_skill_id),
                reason=str(reason or "approach_released"),
            )
        if state.active and reason and log_abort:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "INTERRUPT_APPROACH_ABORT",
                    caster_id=int(state.target_agent_id),
                    enemy_skill_id=int(state.enemy_skill_id),
                    our_skill_id=int(state.our_skill_id),
                    reason=str(reason),
                )
            except Exception:
                pass
        self._interrupt_approach = _InterruptApproachState()
        # Zero-idle target cache: only avoids repeating the same full enemy scan
        # several times inside the same short decision window. It never blocks a
        # cast; stale/invalid targets are revalidated before use.
        self._zero_idle_cache_tick: int = 0
        self._zero_idle_cache_targets: tuple[int, ...] = ()
        clear_interrupt_approach_movement()

    def _start_interrupt_approach(self, candidate) -> bool:
        (
            remaining_ms,
            _distance,
            target_agent_id,
            cast_target_id,
            enemy_skill_id,
            our_skill_id,
            mode,
            destination,
            total_budget,
        ) = candidate
        if not post_interrupt_claim(
            int(target_agent_id),
            int(enemy_skill_id),
            int(our_skill_id),
            claim_duration_ms=max(600, int(remaining_ms) + 320),
        ):
            return False
        now = int(get_game_tick() or 0)
        self._interrupt_approach = _InterruptApproachState(
            active=True,
            target_agent_id=int(target_agent_id),
            cast_target_id=int(cast_target_id),
            enemy_skill_id=int(enemy_skill_id),
            our_skill_id=int(our_skill_id),
            mode=str(mode),
            started_tick=now,
            last_move_tick=0,
            destination=(float(destination[0]), float(destination[1])),
        )
        mark_interrupt_approach_movement_active(target_agent_id, enemy_skill_id)
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "INTERRUPT_APPROACH_START",
                caster_id=int(target_agent_id),
                enemy_skill_id=int(enemy_skill_id),
                our_skill_id=int(our_skill_id),
                mode=str(mode),
                cast_target_id=int(cast_target_id),
                remaining_ms=int(remaining_ms),
                budget_ms=int(total_budget),
                destination=f"{float(destination[0]):.1f},{float(destination[1]):.1f}",
            )
        except Exception:
            pass
        return True

    def _process_lethal_aoe_interrupt_approach(self):
        state = self._interrupt_approach
        if not state.active:
            candidate = self._find_lethal_aoe_approach_candidate()
            if candidate is None or not self._start_interrupt_approach(candidate):
                clear_interrupt_approach_movement()
                return False
            state = self._interrupt_approach

        target_agent_id = int(state.target_agent_id)
        cast_target_id = int(state.cast_target_id or state.target_agent_id)
        enemy_skill_id = int(state.enemy_skill_id)
        our_skill_id = int(state.our_skill_id)
        mode = str(state.mode or "direct")
        if not danger_target_still_casting_skill(target_agent_id, enemy_skill_id):
            self._clear_interrupt_approach(reason="cast_ended")
            return False
        if not is_lethal_aoe_skill(enemy_skill_id):
            self._clear_interrupt_approach(reason="no_longer_lethal")
            return False
        if cast_target_id <= 0 or not Agent.IsValid(cast_target_id) or not Agent.IsAlive(cast_target_id):
            self._clear_interrupt_approach(reason="cast_target_invalid")
            return False
        if mode == "keystone_proxy":
            try:
                from Py4GWCoreLib.Py4GWcorelib import Utils
                if not Routines.Checks.Effects.HasBuff(Player.GetAgentID(), Keystone_Signet_ID):
                    self._clear_interrupt_approach(reason="keystone_expired")
                    return False
                if Utils.Distance(Agent.GetXY(cast_target_id), Agent.GetXY(target_agent_id)) > float(Range.Adjacent.value):
                    self._clear_interrupt_approach(reason="proxy_moved")
                    return False
            except Exception:
                self._clear_interrupt_approach(reason="proxy_validation_failed")
                return False
        if not self.IsSkillEquipped(our_skill_id) or not self.CanCastSkillID(our_skill_id):
            self._clear_interrupt_approach(reason="interrupt_not_ready")
            return False

        max_extra, leader_tether, move_cooldown = self._approach_settings()
        info = self._approach_destination(cast_target_id)
        if info is None:
            self._clear_interrupt_approach(reason="missing_target_position")
            return False
        destination, distance, advance = info
        state.destination = destination
        if advance > float(max_extra):
            self._clear_interrupt_approach(reason="caster_moved_too_far")
            return False
        if not self._approach_within_leader_tether(destination, leader_tether):
            self._clear_interrupt_approach(reason="leader_tether")
            return False
        if is_position_in_active_aoe(destination, padding=45.0, critical_only=False):
            self._clear_interrupt_approach(reason="approach_destination_unsafe")
            return False

        remaining_ms = get_enemy_cast_remaining_ms(target_agent_id, enemy_skill_id)
        if remaining_ms is None:
            self._clear_interrupt_approach(reason="missing_cast_timer")
            return False
        run_ms = int(
            (max(0.0, float(advance)) / float(INTERRUPT_APPROACH_MOVEMENT_SPEED_GW_S))
            * 1000.0
            * float(INTERRUPT_APPROACH_PATH_FACTOR)
        )
        if int(remaining_ms) < run_ms + self._approach_interrupt_budget_ms(our_skill_id):
            self._clear_interrupt_approach(reason="no_time_remaining")
            return False

        mark_interrupt_approach_movement_active(target_agent_id, enemy_skill_id)

        if float(distance) <= float(Range.Spellcast.value) - 10.0:
            timing_feasible = (
                int(remaining_ms) >= self._approach_interrupt_budget_ms(our_skill_id)
                if mode == "keystone_proxy"
                else interrupt_is_feasible(target_agent_id, our_skill_id)
            )
            if not Routines.Checks.Skills.CanCast() or not timing_feasible:
                # Keep the claim only while there is still a realistic window.
                if int(remaining_ms) <= self._approach_interrupt_budget_ms(our_skill_id):
                    self._clear_interrupt_approach(reason="in_range_but_too_late")
                    return False
                return True

            keystone_covered_casts = (
                self._keystone_adjacent_danger_casts(cast_target_id)
                if (
                    mode == "keystone_proxy"
                    or our_skill_id == int(Signet_of_Disruption_ID)
                )
                and Routines.Checks.Effects.HasBuff(Player.GetAgentID(), Keystone_Signet_ID)
                else []
            )
            did_cast = yield from self.CastSkillIDAndRestoreTarget(
                skill_id=our_skill_id,
                target_agent_id=cast_target_id,
                extra_condition=lambda: (
                    danger_target_still_casting_skill(target_agent_id, enemy_skill_id)
                    and self._distance_to_player(cast_target_id) <= float(Range.Spellcast.value)
                    and (
                        mode != "keystone_proxy"
                        or Routines.Checks.Effects.HasBuff(Player.GetAgentID(), Keystone_Signet_ID)
                    )
                ),
                log=False,
                aftercast_delay=250,
            )
            if did_cast:
                if mode == "keystone_proxy" or our_skill_id == int(Signet_of_Disruption_ID):
                    self._note_non_keystone_signet_cast()
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.register_interrupt_fired(
                        target_agent_id, enemy_skill_id, our_skill_id
                    )
                    CombatDebug.log_event(
                        "INTERRUPT_APPROACH_FIRED",
                        caster_id=int(target_agent_id),
                        enemy_skill_id=int(enemy_skill_id),
                        our_skill_id=int(our_skill_id),
                        mode=str(mode),
                        cast_target_id=int(cast_target_id),
                        approach_ms=max(0, int(get_game_tick()) - int(state.started_tick)),
                    )
                except Exception:
                    pass
                if mode == "keystone_proxy":
                    try:
                        from Py4GWCoreLib.Builds.Skills import CombatDebug
                        CombatDebug.log_event(
                            "KEYSTONE_PROXY_INTERRUPT_FIRED",
                            caster_id=int(target_agent_id),
                            enemy_skill_id=int(enemy_skill_id),
                            proxy_id=int(cast_target_id),
                            our_skill_id=int(our_skill_id),
                            source="interrupt_approach",
                        )
                    except Exception:
                        pass
                if keystone_covered_casts:
                    self._register_keystone_coverage(
                        cast_target_id, our_skill_id, keystone_covered_casts
                    )
                # Keep the shared claim until its normal expiry/outcome window.
                self._clear_interrupt_approach(
                    reason="",
                    release_claim=False,
                    log_abort=False,
                )
                return True

            self._clear_interrupt_approach(reason="cast_command_failed")
            return False

        try:
            if Routines.Checks.Agents.IsKnockedDown(Player.GetAgentID()):
                return True
        except Exception:
            pass
        try:
            if Agent.IsCasting(Player.GetAgentID()):
                return True
        except Exception:
            pass

        now = int(get_game_tick() or 0)
        if int(state.last_move_tick or 0) <= 0 or now - int(state.last_move_tick) >= int(move_cooldown):
            Player.Move(float(destination[0]), float(destination[1]))
            state.last_move_tick = int(now)
        return True


    @staticmethod
    def _signet_of_disruption_can_interrupt(target_agent_id: int, enemy_skill_id: int) -> bool:
        """Signet of Disruption always interrupts spells; any skill if foe is hexed."""
        target_agent_id = int(target_agent_id or 0)
        enemy_skill_id = int(enemy_skill_id or 0)
        if target_agent_id <= 0 or enemy_skill_id <= 0:
            return False
        try:
            if Skill.Flags.IsSpell(enemy_skill_id):
                return True
        except Exception:
            pass
        try:
            return bool(Agent.IsHexed(target_agent_id))
        except Exception:
            return False

    @staticmethod
    def _telemetry_signet_name(skill_id: int) -> str:
        names = {
            int(Signet_of_Judgment_ID): "copied_soj",
            int(Unnatural_Signet_ID): "unnatural",
            int(Signet_of_Sorrow_ID): "sorrow",
            int(Signet_of_Corruption_Kurzick_ID): "corruption_kurzick",
            int(Signet_of_Corruption_Luxon_ID): "corruption_luxon",
            int(Bane_Signet_ID): "bane_ruins",
            int(Signet_of_Disruption_ID): "disruption",
            int(Castigation_Signet_ID): "castigation",
        }
        return names.get(int(skill_id or 0), f"skill_{int(skill_id or 0)}")

    def _telemetry_skill_target_state(self, skill_id: int) -> tuple[int, str]:
        """Read-only best-effort explanation for a ready signet not firing yet.

        This intentionally avoids claims and never mutates combat state.
        """
        skill_id = int(skill_id or 0)
        if skill_id <= 0:
            return 0, "invalid_skill"

        try:
            if not self._sod_combat_active():
                return 0, "no_combat"
        except Exception:
            return 0, "no_combat"

        try:
            if skill_id == int(Signet_of_Judgment_ID):
                target_id = int(self._get_signet_of_judgment_target(claim_target=False) or 0)
                if target_id > 0:
                    return target_id, "target_available"
                return 0, "no_valid_soj_target"

            if skill_id == int(Unnatural_Signet_ID):
                target_id, reason = self._get_unnatural_signet_fast_target()
                target_id = int(target_id or 0)
                if target_id > 0:
                    return target_id, f"target_available:{reason}"
                return 0, f"no_valid_unnatural_target:{reason}"

            if skill_id == int(Signet_of_Sorrow_ID):
                target_id = int(self._get_sorrow_signet_target(require_corpse=True, claim_target=False) or 0)
                if target_id > 0:
                    return target_id, "target_available:corpse_turbo"
                target_id, reason = self._get_zero_idle_damage_target(skill_id)
                target_id = int(target_id or 0)
                if target_id > 0:
                    return target_id, f"target_available:normal:{reason}"
                return 0, "no_valid_sorrow_target"

            if skill_id in (int(Signet_of_Corruption_Kurzick_ID), int(Signet_of_Corruption_Luxon_ID)):
                target_id, reason = self._get_zero_idle_damage_target(skill_id)
                target_id = int(target_id or 0)
                if target_id > 0:
                    return target_id, f"target_available:{reason}"
                return 0, "no_valid_corruption_target"

            if skill_id == int(Bane_Signet_ID):
                target_id, reason = self._get_zero_idle_damage_target(skill_id)
                target_id = int(target_id or 0)
                if target_id > 0:
                    return target_id, f"target_available:{reason}"
                return 0, f"no_valid_bane_target:{reason}"

            if skill_id == int(Castigation_Signet_ID):
                target_id, reason = self._get_zero_idle_damage_target(skill_id)
                target_id = int(target_id or 0)
                if target_id > 0:
                    return target_id, f"target_available:{reason}"
                return 0, f"no_valid_castigation_target:{reason}"

            if skill_id == int(Signet_of_Disruption_ID):
                # Do not invoke the interrupt claim path from telemetry.  Only
                # record whether a normal filler/damage target exists.
                snapshot = self._get_bar_snapshot()
                target_id, reason = self._get_signet_of_disruption_filler_target(snapshot)
                target_id = int(target_id or 0)
                if target_id > 0:
                    return target_id, f"target_available:{reason}"
                if snapshot.enemy_casting:
                    return 0, "enemy_casting_but_no_legal_or_feasible_target"
                return 0, "no_interrupt_or_useful_filler_target"
        except Exception as exc:
            return 0, f"telemetry_probe_error:{type(exc).__name__}"

        return 0, "unsupported"

    def _track_primary_signet_idle(self) -> None:
        """Low-overhead ready/idle telemetry for all Keystone offensive signets.

        One sample per ready skill per second.  The function is deliberately
        read-only and may be removed without changing combat behavior.
        """
        now = int(get_game_tick() or 0)
        if now <= 0:
            return

        combat_active = bool(self._sod_combat_active())
        tracked = (
            int(Signet_of_Judgment_ID),
            int(Unnatural_Signet_ID),
            int(Bane_Signet_ID),
            int(Signet_of_Disruption_ID),
                Signet_of_Sorrow_ID,
                Signet_of_Corruption_Kurzick_ID,
            Signet_of_Corruption_Luxon_ID,
            int(Castigation_Signet_ID),
        )

        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
        except Exception:
            return

        for skill_id in tracked:
            if skill_id <= 0:
                continue

            equipped = bool(self.IsSkillEquipped(skill_id))
            ready_now = bool(equipped and self._is_skill_strictly_ready(skill_id))
            was_ready = bool(self._signet_was_ready.get(skill_id, False))

            if not combat_active or not equipped:
                self._signet_ready_since.pop(skill_id, None)
                self._signet_last_sample.pop(skill_id, None)
                self._signet_was_ready[skill_id] = ready_now
                continue

            skill_name = self._telemetry_signet_name(skill_id)

            if ready_now and not was_ready:
                self._signet_ready_since[skill_id] = now
                self._signet_last_sample[skill_id] = now
                target_id, block_reason = self._telemetry_skill_target_state(skill_id)
                CombatDebug.log_event(
                    "SIGNET_READY_ENTER",
                    skill_id=int(skill_id),
                    skill_name=str(skill_name),
                    target_id=int(target_id),
                    state=str(block_reason),
                )

            elif ready_now:
                ready_since = int(self._signet_ready_since.get(skill_id, now) or now)
                last_sample = int(self._signet_last_sample.get(skill_id, 0) or 0)
                if now - last_sample >= 1000:
                    self._signet_last_sample[skill_id] = now
                    target_id, block_reason = self._telemetry_skill_target_state(skill_id)
                    CombatDebug.log_event(
                        "SIGNET_READY_SAMPLE",
                        skill_id=int(skill_id),
                        skill_name=str(skill_name),
                        idle_ms=max(0, now - ready_since),
                        target_id=int(target_id),
                        state=str(block_reason),
                    )

            elif was_ready:
                ready_since = int(self._signet_ready_since.get(skill_id, now) or now)
                CombatDebug.log_event(
                    "SIGNET_READY_EXIT",
                    skill_id=int(skill_id),
                    skill_name=str(skill_name),
                    idle_ms=max(0, now - ready_since),
                )
                self._signet_ready_since.pop(skill_id, None)
                self._signet_last_sample.pop(skill_id, None)

            self._signet_was_ready[skill_id] = ready_now

    def _log_primary_signet_cast(
        self,
        skill_id: int,
        target_id: int,
        reason: str,
        path: str,
    ) -> None:
        """Uniform cast event for post-run DPS/idle analysis."""
        skill_id = int(skill_id or 0)
        target_id = int(target_id or 0)
        if skill_id <= 0:
            return
        if skill_id != int(Signet_of_Sorrow_ID):
            self._sorrow_turbo_streak = 0
        # Publish only after a successful real signet activation reaches this
        # uniform cast logger.  Other clients use the short event to keep the
        # next packet signet roughly 90 ms behind without reserving anything.
        self._post_keystone_packet_cast_event(int(target_id))
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            now = int(get_game_tick() or 0)
            ready_since = int(self._signet_ready_since.get(skill_id, 0) or 0)
            idle_ms = max(0, now - ready_since) if now > 0 and ready_since > 0 else 0
            active_mantra_id = int(self._active_keystone_mantra_id() or 0)
            previous_signet_tick = int(self._mantra_last_profile_signet_tick or 0)
            signet_gap_ms = (
                max(0, now - previous_signet_tick)
                if now > 0 and previous_signet_tick > 0
                else 0
            )
            self._mantra_last_profile_signet_tick = now
            self._mantra_profile_signet_count += 1
            health_fraction_before, max_hp_before, hp_before = self._player_health_snapshot()
            missing_hp_before = max(0, int(max_hp_before - hp_before))
            mantra_signets_active = bool(active_mantra_id == int(Mantra_of_Signets_ID))
            CombatDebug.log_event(
                "SIGNET_CAST_PROFILE",
                skill_id=int(skill_id),
                skill_name=str(self._telemetry_signet_name(skill_id)),
                target_id=int(target_id),
                ready_idle_ms=int(idle_ms),
                reason=str(reason),
                path=str(path),
                mantra_id=int(active_mantra_id),
                mantra_name=self._keystone_mantra_name(active_mantra_id),
                mantra_active=bool(active_mantra_id > 0),
                signet_gap_ms=int(signet_gap_ms),
                signet_cast_index=int(self._mantra_profile_signet_count),
                health_probe_id=0,
                telemetry_mode="passive_precast_only",
                mantra_signets_heal_opportunity=bool(mantra_signets_active and missing_hp_before > 0),
                mantra_signets_missing_hp_before=int(missing_hp_before) if mantra_signets_active else 0,
                player_health_fraction_at_cast_request=float(health_fraction_before),
                player_hp_at_cast_request=int(hp_before),
                player_max_hp_at_cast_request=int(max_hp_before),
                missing_hp_at_cast_request=int(missing_hp_before),
                # Backward-compatible legacy field. It was historically named
                # "after" even though the sample is taken at cast request.
                player_health_pct_after=float(health_fraction_before),
            )
        except Exception:
            pass

    def _track_mimicry_soj_window(self) -> None:
        """Track the copied SoJ lifetime separately from ordinary signet timing."""
        now = int(get_game_tick() or 0)
        if now <= 0:
            return
        if now < int(self._mimicry_telemetry_next_tick or 0):
            return
        self._mimicry_telemetry_next_tick = int(now + 100)
        equipped_now = bool(self.IsSkillEquipped(Signet_of_Judgment_ID))
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
        except Exception:
            return

        if equipped_now and not self._mimicry_soj_was_equipped:
            self._mimicry_soj_equipped_since = now
            current_target = int(Player.GetTargetID() or 0)
            CombatDebug.log_event(
                "MIMICRY_SOJ_WINDOW_START",
                current_target_id=int(current_target),
                target_is_valid_enemy=bool(current_target > 0 and Routines.Checks.Agents.IsAlive(current_target)),
            )

        elif not equipped_now and self._mimicry_soj_was_equipped:
            lived_ms = (
                max(0, now - int(self._mimicry_soj_equipped_since))
                if self._mimicry_soj_equipped_since > 0
                else 0
            )
            CombatDebug.log_event(
                "MIMICRY_SOJ_WINDOW_END",
                window_ms=int(lived_ms),
            )
            self._mimicry_soj_equipped_since = 0

        self._mimicry_soj_was_equipped = equipped_now

    def _log_rotation_stall(self, gate: str, snapshot=None) -> None:
        """Read-only diagnostic for visible Mesmer idle windows."""
        try:
            if not self._sod_combat_active():
                return
            now = int(get_game_tick() or 0)
            if now <= 0:
                return
            if self._stall_last_log_tick > 0 and now - int(self._stall_last_log_tick) < 750:
                return
            tracked = (
                int(Signet_of_Judgment_ID),
                int(Unnatural_Signet_ID),
                int(Bane_Signet_ID),
                int(Signet_of_Disruption_ID),
                int(Castigation_Signet_ID),
            )
            ready, usable, states = [], [], []
            for skill_id in tracked:
                if skill_id <= 0 or not self.IsSkillEquipped(skill_id):
                    continue
                if not self._is_skill_strictly_ready(skill_id):
                    continue
                ready.append(int(skill_id))
                target_id, state = self._telemetry_skill_target_state(skill_id)
                states.append(f"{self._telemetry_signet_name(skill_id)}:{state}:{int(target_id or 0)}")
                if int(target_id or 0) > 0 and str(state).startswith("target_available"):
                    usable.append(int(skill_id))
            if not usable:
                return
            self._stall_last_log_tick = now
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMER_ROTATION_STALL",
                gate=str(gate),
                ready_skills=",".join(str(x) for x in ready),
                usable_skills=",".join(str(x) for x in usable),
                states="|".join(states),
                current_target_id=int(Player.GetTargetID() or 0),
                global_can_cast=bool(Routines.Checks.Skills.CanCast()),
                enemy_casting=bool(getattr(snapshot, "enemy_casting", False)) if snapshot is not None else False,
                enemy_in_spellcast=bool(getattr(snapshot, "enemy_in_spellcast", False)) if snapshot is not None else False,
                precombat_setup=bool(getattr(snapshot, "precombat_setup", False)) if snapshot is not None else False,
            )
        except Exception:
            pass

    def _sod_combat_active(self) -> bool:
        try:
            if self.IsInAggro() or self.IsCloseToAggro():
                return True
        except Exception:
            pass
        try:
            from Py4GWCoreLib import AgentArray
            for enemy_id in AgentArray.GetEnemyArray() or []:
                enemy_id = int(enemy_id or 0)
                if enemy_id <= 0:
                    continue
                if not Agent.IsValid(enemy_id) or not Agent.IsAlive(enemy_id):
                    continue
                if self._distance_to_player(enemy_id) <= float(Range.Spellcast.value):
                    return True
        except Exception:
            pass
        return False

    def _track_sod_ready_idle(self) -> None:
        """Telemetry only: measure ready-but-unused Signet of Disruption time."""
        if not self.IsSkillEquipped(Signet_of_Disruption_ID):
            self._sod_ready_since_tick = 0
            self._sod_was_ready = False
            return

        now = int(get_game_tick() or 0)
        if now <= 0:
            return
        if now < int(self._sod_telemetry_next_tick or 0):
            return
        self._sod_telemetry_next_tick = int(now + 250)

        ready_now = bool(self._is_skill_strictly_ready(Signet_of_Disruption_ID))
        if not self._sod_combat_active():
            self._sod_ready_since_tick = 0
            self._sod_was_ready = ready_now
            self._sod_last_sample_tick = 0
            self._sod_last_team_ready_count = -1
            return

        team_ready_count = len(self._disruption_ready_accounts())
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
        except Exception:
            return

        if ready_now and not self._sod_was_ready:
            self._sod_ready_since_tick = now
            self._sod_last_sample_tick = now
            self._sod_last_team_ready_count = int(team_ready_count)
            CombatDebug.log_event(
                "SOD_READY_ENTER",
                ready_count=int(team_ready_count),
                combat_active=True,
            )

        elif ready_now:
            if self._sod_ready_since_tick <= 0:
                self._sod_ready_since_tick = now
            idle_ms = max(0, now - int(self._sod_ready_since_tick))
            if (
                self._sod_last_sample_tick <= 0
                or now - int(self._sod_last_sample_tick) >= 1000
                or int(team_ready_count) != int(self._sod_last_team_ready_count)
            ):
                self._sod_last_sample_tick = now
                self._sod_last_team_ready_count = int(team_ready_count)
                CombatDebug.log_event(
                    "SOD_READY_SAMPLE",
                    idle_ms=int(idle_ms),
                    ready_count=int(team_ready_count),
                    combat_active=True,
                )

        elif self._sod_was_ready:
            idle_ms = max(0, now - int(self._sod_ready_since_tick)) if self._sod_ready_since_tick > 0 else 0
            CombatDebug.log_event(
                "SOD_READY_EXIT",
                idle_ms=int(idle_ms),
                ready_count=int(team_ready_count),
                combat_active=True,
            )
            self._sod_ready_since_tick = 0
            self._sod_last_sample_tick = 0
            self._sod_last_team_ready_count = int(team_ready_count)

        self._sod_was_ready = ready_now

    def _log_sod_cast_consumed(self, reason: str, target_id: int = 0, enemy_skill_id: int = 0) -> None:
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            now = int(get_game_tick() or 0)
            idle_ms = max(0, now - int(self._sod_ready_since_tick)) if now > 0 and self._sod_ready_since_tick > 0 else 0
            CombatDebug.log_event(
                "SOD_CAST_CONSUMED",
                reason=str(reason),
                target_id=int(target_id or 0),
                enemy_skill_id=int(enemy_skill_id or 0),
                ready_idle_ms=int(idle_ms),
                ready_count_before=int(len(self._disruption_ready_accounts())),
            )
        except Exception:
            pass

    def _disruption_ready_accounts(self):
        """Return active party accounts with a ready Signet of Disruption."""
        ready = []
        try:
            from Py4GWCoreLib import GLOBAL_CACHE
            own_party_id = int(GLOBAL_CACHE.Party.GetPartyID() or 0)
            for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
                if not bool(getattr(account, "IsSlotActive", False)):
                    continue
                if bool(getattr(account, "IsIsolated", False)):
                    continue
                party_id = int(getattr(getattr(account, "AgentPartyData", None), "PartyID", 0) or 0)
                if own_party_id > 0 and party_id > 0 and party_id != own_party_id:
                    continue
                email = str(getattr(account, "AccountEmail", "") or "").strip()
                if not email:
                    continue
                if self._shared_skillbar_has_ready_skill(account, (int(Signet_of_Disruption_ID),)):
                    ready.append((email, account))
        except Exception:
            return []
        ready.sort(key=lambda item: str(item[0]))
        return ready

    def _disruption_filler_turn_ready(self) -> tuple[bool, int, int]:
        """No-reserve coordination.

        Ready Signets of Disruption are never held back for round-robin timing.
        Shared readiness is still reported for logs, but every ready Mesmer may
        act immediately. Cross-account interrupt claims prevent duplicate
        interrupts on the same activation.
        """
        own_email = str(Player.GetAccountEmail() or "").strip()
        ready = self._disruption_ready_accounts()
        emails = [str(item[0]) for item in ready]
        try:
            rank = emails.index(own_email)
        except ValueError:
            rank = 0
        self._disruption_filler_wait_since = 0
        return True, max(0, rank), len(emails)

    def _cast_signet_of_disruption_interrupt_first(self, snapshot: _KeystoneBarSnapshot):
        """Interrupt any feasible legal activation before all offensive rotation.

        Dangerous casts are ranked first, but an otherwise ordinary spell is
        still interrupted if the signet is ready and the timing is feasible.
        Non-spell skills are eligible only while the foe is hexed.
        """
        if not self.IsSkillEquipped(Signet_of_Disruption_ID):
            return False
        if not self._is_skill_strictly_ready(Signet_of_Disruption_ID):
            return False

        try:
            from Py4GWCoreLib import AgentArray
            candidates = []
            player_xy = Player.GetXY()
            for enemy_id in AgentArray.GetEnemyArray() or []:
                enemy_id = int(enemy_id or 0)
                if enemy_id <= 0:
                    continue
                try:
                    if not Agent.IsValid(enemy_id) or not Agent.IsAlive(enemy_id):
                        continue
                except Exception:
                    continue
                if self._distance_to_player(enemy_id) > float(Range.Spellcast.value):
                    continue

                enemy_skill_id = int(get_danger_casting_skill_id(enemy_id) or 0)
                if enemy_skill_id <= 0:
                    continue
                if not self._signet_of_disruption_can_interrupt(enemy_id, enemy_skill_id):
                    continue
                if not interrupt_is_feasible(enemy_id, Signet_of_Disruption_ID):
                    continue

                dangerous = bool(is_dangerous_cast(enemy_id))
                remaining = get_enemy_cast_remaining_ms(enemy_id, enemy_skill_id)
                remaining_sort = int(remaining) if remaining is not None else 999999
                candidates.append((
                    0 if dangerous else 1,
                    remaining_sort,
                    danger_sort_key(enemy_id, player_xy),
                    enemy_id,
                    enemy_skill_id,
                ))
        except Exception:
            return False

        if not candidates:
            return False

        candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
        for _danger_rank, _remaining, _sort_key, target_id, enemy_skill_id in candidates:
            target_id = int(target_id)
            enemy_skill_id = int(enemy_skill_id)

            # Shared claim is the only coordination gate. There is no timer,
            # reservation or round-robin wait.
            try:
                if not post_interrupt_claim(
                    target_id, enemy_skill_id, Signet_of_Disruption_ID
                ):
                    continue
            except Exception:
                # Claim system is fail-open only when unavailable; the local
                # cast condition below still protects against stale activations.
                pass

            did_cast = yield from self.CastSkillIDAndRestoreTarget(
                skill_id=Signet_of_Disruption_ID,
                target_agent_id=target_id,
                extra_condition=lambda tid=target_id, sid=enemy_skill_id: (
                    self._target_still_casting_safe_skill(tid, sid)
                    and self._signet_of_disruption_can_interrupt(tid, sid)
                    and interrupt_is_feasible(tid, Signet_of_Disruption_ID)
                ),
                log=False,
                aftercast_delay=40,
            )
            if not did_cast:
                try:
                    release_interrupt_claim(target_id, enemy_skill_id)
                except Exception:
                    pass
                continue

            self._note_non_keystone_signet_cast()
            self._log_sod_cast_consumed("interrupt_first", target_id, enemy_skill_id)
            self._log_primary_signet_cast(
                Signet_of_Disruption_ID,
                target_id,
                reason=f"interrupt_skill_{int(enemy_skill_id)}",
                path="disruption_interrupt_first",
            )
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "KEYSTONE_DISRUPTION_INTERRUPT_FIRST",
                    target_id=int(target_id),
                    enemy_skill_id=int(enemy_skill_id),
                    dangerous=bool(_danger_rank == 0),
                    remaining_ms=int(_remaining),
                    policy="any_feasible_interrupt_before_all_no_reserve",
                )
                CombatDebug.register_interrupt_fired(
                    int(target_id),
                    int(enemy_skill_id),
                    int(Signet_of_Disruption_ID),
                )
            except Exception:
                pass
            return True

        return False

    def _cast_signet_of_disruption_opening(self, snapshot: _KeystoneBarSnapshot):
        """Open a fresh enemy packet with Signet of Disruption."""
        if self._disruption_opening_spent:
            return False
        if not self.IsSkillEquipped(Signet_of_Disruption_ID):
            return False
        if not self._is_skill_strictly_ready(Signet_of_Disruption_ID):
            return False

        target_id, reason = self._get_zero_idle_damage_target(int(Signet_of_Disruption_ID))
        target_id = int(target_id or 0)
        if target_id <= 0:
            return False

        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Signet_of_Disruption_ID,
            target_agent_id=target_id,
            log=False,
            aftercast_delay=40,
        )
        if not did_cast:
            return False

        self._disruption_opening_spent = True
        self._note_non_keystone_signet_cast()
        self._log_sod_cast_consumed("opening", target_id, 0)
        self._log_primary_signet_cast(
            Signet_of_Disruption_ID,
            target_id,
            reason="opening",
            path="disruption_opening",
        )
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "KEYSTONE_DISRUPTION_OPENING_CAST",
                target_id=int(target_id),
                reason=str(reason),
                policy="open_each_packet_distributed_no_reserve",
            )
        except Exception:
            pass
        return True

    def _get_signet_of_disruption_filler_target(self, snapshot: _KeystoneBarSnapshot) -> tuple[int, str]:
        """Useful non-emergency use: interrupt any legal activation, else Keystone AoE."""
        # First use it on any current legal activation, even if it is below the
        # reserved-danger threshold. This converts filler casts into real control.
        try:
            from Py4GWCoreLib import AgentArray
            candidates = []
            for enemy_id in AgentArray.GetEnemyArray() or []:
                enemy_id = int(enemy_id or 0)
                if enemy_id <= 0 or not Agent.IsValid(enemy_id) or not Agent.IsAlive(enemy_id):
                    continue
                if self._distance_to_player(enemy_id) > float(Range.Spellcast.value):
                    continue
                enemy_skill_id = int(get_danger_casting_skill_id(enemy_id) or 0)
                if enemy_skill_id <= 0:
                    continue
                if not self._signet_of_disruption_can_interrupt(enemy_id, enemy_skill_id):
                    continue
                candidates.append((
                    0 if is_dangerous_cast(enemy_id) else 1,
                    danger_sort_key(enemy_id, Player.GetXY()),
                    enemy_id,
                ))
            if candidates:
                candidates.sort(key=lambda item: (item[0], item[1], item[2]))
                return int(candidates[0][2]), "active_interruptable_skill"
        except Exception:
            pass

        # FULL-SPAM policy: once no legal interrupt exists, Signet of Disruption
        # is pure pressure. Spend it on any valid zero-idle damage target.
        # Interrupt-first remains absolute above this branch.
        target_id, reason = self._get_zero_idle_damage_target(int(Signet_of_Disruption_ID))
        target_id = int(target_id or 0)
        if target_id > 0:
            return target_id, f"full_spam_{reason}"
        return 0, ""

    def _cast_signet_of_disruption_filler(self, snapshot: _KeystoneBarSnapshot):
        """Spend Signet of Disruption immediately when no interruptable activation exists."""
        if not self._is_skill_strictly_ready(Signet_of_Disruption_ID):
            self._disruption_filler_wait_since = 0
            return False

        target_id, reason = self._get_signet_of_disruption_filler_target(snapshot)
        target_id = int(target_id or 0)
        if target_id <= 0:
            self._disruption_filler_wait_since = 0
            return False

        # No round-robin hold: every Mesmer may spend a ready SoD immediately
        # when the interrupt-first scan found nothing legal.
        rank, ready_count = 0, 1

        enemy_skill_id = int(get_danger_casting_skill_id(target_id) or 0)
        direct_interrupt = bool(
            enemy_skill_id > 0
            and self._signet_of_disruption_can_interrupt(target_id, enemy_skill_id)
        )
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Signet_of_Disruption_ID,
            target_agent_id=target_id,
            extra_condition=(
                (lambda: self._target_still_casting_safe_skill(target_id, enemy_skill_id))
                if direct_interrupt else None
            ),
            log=False,
            aftercast_delay=40,
        )
        if not did_cast and direct_interrupt:
            # The activation disappeared between target selection and the real
            # signet fire window. Do not burn a controller pass waiting for the
            # next tick: immediately fall back to a normal full-spam damage
            # target while SoD is still ready.
            fallback_target, fallback_reason = self._get_zero_idle_damage_target(
                int(Signet_of_Disruption_ID)
            )
            fallback_target = int(fallback_target or 0)
            if fallback_target > 0 and self._is_skill_strictly_ready(Signet_of_Disruption_ID):
                did_cast = yield from self.CastSkillIDAndRestoreTarget(
                    skill_id=Signet_of_Disruption_ID,
                    target_agent_id=fallback_target,
                    log=False,
                    aftercast_delay=40,
                )
                if did_cast:
                    target_id = int(fallback_target)
                    enemy_skill_id = 0
                    direct_interrupt = False
                    reason = f"same_pass_stale_interrupt_fallback_{fallback_reason}"
                    try:
                        from Py4GWCoreLib.Builds.Skills import CombatDebug
                        CombatDebug.log_event(
                            "SOD_STALE_INTERRUPT_SAME_PASS_FALLBACK",
                            target_id=int(target_id),
                            reason=str(reason),
                        )
                    except Exception:
                        pass

        if not did_cast:
            return False

        self._note_non_keystone_signet_cast()
        self._log_sod_cast_consumed("rotation_filler", target_id, enemy_skill_id)
        self._log_primary_signet_cast(
            Signet_of_Disruption_ID,
            target_id,
            reason=str(reason),
            path="disruption_rotation_filler",
        )
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "KEYSTONE_DISRUPTION_ROTATION_CAST",
                target_id=int(target_id),
                enemy_skill_id=int(enemy_skill_id),
                direct_interrupt=bool(direct_interrupt),
                reason=str(reason),
                ready_rank=int(rank),
                ready_count=int(ready_count),
                policy="no_reserve_immediate_filler",
            )
            if direct_interrupt:
                CombatDebug.register_interrupt_fired(
                    int(target_id), int(enemy_skill_id), int(Signet_of_Disruption_ID)
                )
        except Exception:
            pass
        return True

    def _log_disruption_team_availability(self) -> None:
        """Low-rate one-account log for the key metric: at least one interrupt ready."""
        if not self.IsSkillEquipped(Signet_of_Disruption_ID):
            return
        now = int(get_game_tick() or 0)
        if now <= 0:
            return
        last = int(getattr(self, "_disruption_availability_log_tick", 0) or 0)
        if last > 0 and now - last < int(DISRUPTION_AVAILABILITY_LOG_MS):
            return
        try:
            ready = self._disruption_ready_accounts()
            from Py4GWCoreLib import GLOBAL_CACHE
            own_email = str(Player.GetAccountEmail() or "").strip()
            all_users = []
            own_party_id = int(GLOBAL_CACHE.Party.GetPartyID() or 0)
            for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
                if not bool(getattr(account, "IsSlotActive", False)) or bool(getattr(account, "IsIsolated", False)):
                    continue
                party_id = int(getattr(getattr(account, "AgentPartyData", None), "PartyID", 0) or 0)
                if own_party_id > 0 and party_id > 0 and party_id != own_party_id:
                    continue
                if not self._shared_skillbar_contains(account, Signet_of_Disruption_ID):
                    continue
                email = str(getattr(account, "AccountEmail", "") or "").strip()
                if email:
                    all_users.append(email)
            all_users = sorted(set(all_users))
            if not all_users or own_email != all_users[0]:
                return
            self._disruption_availability_log_tick = now
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "KEYSTONE_DISRUPTION_AVAILABILITY",
                ready_count=len(ready),
                total_count=len(all_users),
                any_ready=bool(ready),
            )
        except Exception:
            pass

    def _current_shared_cluster_members(self) -> set[int]:
        """Return the single current team packet. Empty means cleanup/no packet."""
        try:
            anchor = int(self._get_power_cluster_anchor() or 0)
            if anchor <= 0:
                return set()
            from Py4GWCoreLib import AgentArray
            arr = AgentArray.GetEnemyArray()
            arr = AgentArray.Filter.ByDistance(
                arr, Agent.GetXY(anchor), Range.Adjacent.value
            )
            return {
                int(aid) for aid in (arr or [])
                if int(aid or 0) > 0
                and Agent.IsValid(int(aid))
                and Agent.IsAlive(int(aid))
            }
        except Exception:
            return set()

    def _cast_opportunistic_offcluster_interrupt(self, snapshot: _KeystoneBarSnapshot):
        """No reserve, no claim, no approach.

        Only detour from the shared damage packet when a skill is ALREADY ready
        and an off-packet enemy is currently casting:
          - an offensive/dangerous caster skill, or
          - a resurrection.
        Heal/protection casts do NOT pull the Mesmers off the packet.
        If nothing can be fired immediately, normal packet/cleanup damage
        continues in the same controller pass.
        """
        if not snapshot.enemy_casting:
            return False

        try:
            from Py4GWCoreLib.Builds.Skills import CombatSense
            enemies = CombatSense.refresh(
                range_value=Range.Spellcast.value,
                throttle_ms=45,
            )
            cluster_members = self._current_shared_cluster_members()
            candidates = []
            for e in enemies:
                aid = int(e.agent_id)
                sid = int(e.casting_skill_id or 0)
                if aid <= 0 or sid <= 0 or aid in cluster_members:
                    continue

                # Never detour for normal heal/prot. Resurrection is explicitly
                # allowed; offensive shutdown/damage is allowed.
                if sid in CombatSense.PROT_HEAL_SKILL_IDS and sid not in CombatSense.REZ_SKILL_IDS:
                    continue
                is_rez = sid in CombatSense.REZ_SKILL_IDS
                is_offense = sid in CombatSense.AOE_SHUTDOWN_SKILL_IDS
                if not (is_rez or is_offense):
                    continue
                candidates.append((
                    0 if is_rez else 1,
                    -int(e.threat_score),
                    int(aid),
                    int(sid),
                ))
        except Exception:
            return False

        if not candidates:
            return False
        candidates.sort()

        for _kind, _threat, caster_id, enemy_skill_id in candidates:
            # 1) SoD direct interrupt if it is already ready.
            if (
                self._is_skill_strictly_ready(Signet_of_Disruption_ID)
                and self._signet_of_disruption_can_interrupt(caster_id, enemy_skill_id)
                and interrupt_is_feasible(caster_id, Signet_of_Disruption_ID)
            ):
                did_cast = yield from self.CastSkillIDAndRestoreTarget(
                    skill_id=Signet_of_Disruption_ID,
                    target_agent_id=int(caster_id),
                    extra_condition=lambda cid=int(caster_id), sid=int(enemy_skill_id): (
                        self._target_still_casting_safe_skill(cid, sid)
                        and self._signet_of_disruption_can_interrupt(cid, sid)
                    ),
                    log=False,
                    aftercast_delay=40,
                )
                if did_cast:
                    self._note_non_keystone_signet_cast()
                    try:
                        from Py4GWCoreLib.Builds.Skills import CombatDebug
                        CombatDebug.register_interrupt_fired(
                            int(caster_id), int(enemy_skill_id), int(Signet_of_Disruption_ID)
                        )
                        CombatDebug.log_event(
                            "KEYSTONE_OPPORTUNISTIC_OFFCLUSTER_INTERRUPT",
                            mode="sod_direct",
                            target_id=int(caster_id),
                            enemy_skill_id=int(enemy_skill_id),
                            policy="ready_now_no_reserve_no_claim_no_approach",
                        )
                    except Exception:
                        pass
                    return True

            # 2) Copied SoJ may directly KD the current offensive/rez caster.
            if (
                self._is_skill_strictly_ready(Signet_of_Judgment_ID)
                and self._is_signet_of_judgment_control_target_usable(
                    int(caster_id), check_claimed=False
                )
            ):
                did_cast = yield from self.CastSkillIDAndRestoreTarget(
                    skill_id=Signet_of_Judgment_ID,
                    target_agent_id=int(caster_id),
                    extra_condition=lambda cid=int(caster_id), sid=int(enemy_skill_id): (
                        self._target_still_casting_safe_skill(cid, sid)
                        and self._is_signet_of_judgment_control_target_usable(
                            cid, check_claimed=False
                        )
                    ),
                    log=False,
                    aftercast_delay=40,
                )
                if did_cast:
                    self._note_non_keystone_signet_cast()
                    try:
                        from Py4GWCoreLib.Builds.Skills import CombatDebug
                        CombatDebug.register_interrupt_fired(
                            int(caster_id), int(enemy_skill_id), int(Signet_of_Judgment_ID)
                        )
                        CombatDebug.log_event(
                            "KEYSTONE_OPPORTUNISTIC_OFFCLUSTER_INTERRUPT",
                            mode="copied_soj_direct",
                            target_id=int(caster_id),
                            enemy_skill_id=int(enemy_skill_id),
                            policy="ready_now_no_reserve_no_claim_no_approach",
                        )
                    except Exception:
                        pass
                    return True

            # 3) Bane/Ruins can opportunistically trigger Keystone through an
            # adjacent proxy if Keystone is active. No waiting if no proxy exists.
            if snapshot.has_keystone_signet and self._is_skill_strictly_ready(Bane_Signet_ID):
                proxy_id = int(self._pick_keystone_proxy_target(
                    int(caster_id), int(Bane_Signet_ID)
                ) or 0)
                if proxy_id > 0:
                    did_cast = yield from self.CastSkillIDAndRestoreTarget(
                        skill_id=Bane_Signet_ID,
                        target_agent_id=int(proxy_id),
                        extra_condition=lambda cid=int(caster_id), sid=int(enemy_skill_id), pid=int(proxy_id): (
                            self._target_still_casting_safe_skill(cid, sid)
                            and self._is_keystone_proxy_target_usable(
                                pid, cid, Bane_Signet_ID
                            )
                        ),
                        log=False,
                        aftercast_delay=40,
                    )
                    if did_cast:
                        self._note_non_keystone_signet_cast()
                        try:
                            from Py4GWCoreLib.Builds.Skills import CombatDebug
                            CombatDebug.register_interrupt_fired(
                                int(caster_id), int(enemy_skill_id), int(Bane_Signet_ID)
                            )
                            CombatDebug.log_event(
                                "KEYSTONE_OPPORTUNISTIC_OFFCLUSTER_INTERRUPT",
                                mode="bane_keystone_proxy",
                                target_id=int(caster_id),
                                proxy_id=int(proxy_id),
                                enemy_skill_id=int(enemy_skill_id),
                                policy="ready_now_no_reserve_no_claim_no_approach",
                            )
                        except Exception:
                            pass
                        return True

        return False

    def _cast_coordinated_signet_of_distraction(self, snapshot: _KeystoneBarSnapshot):
        """Variant 2: disable enemy healer/prot/AoE spells, one cast per claim.

        Works only when the observed skill is a SPELL. Signets of Distraction
        on all five Me/N characters are otherwise reserved for real casts; no
        periodic hold, no movement/chase, and no unconditional random use.
        """
        if not snapshot.enemy_casting:
            return False
        if not self.IsSkillEquipped(Signet_of_Distraction_ID) or not self.CanCastSkillID(Signet_of_Distraction_ID):
            return False
        def valid_spell(caster_id, enemy_skill_id):
            try:
                return bool(
                    int(enemy_skill_id or 0) > 0
                    and Skill.Flags.IsSpell(int(enemy_skill_id))
                    and interrupt_is_feasible(int(caster_id), int(Signet_of_Distraction_ID))
                )
            except Exception:
                return False

        target_id, enemy_skill_id = self._pick_safe_dangerous_interrupt_target(
            interrupter_skill_id=int(Signet_of_Distraction_ID),
            validator=valid_spell,
        )
        if not target_id or not enemy_skill_id:
            return False
        casted = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Signet_of_Distraction_ID,
            target_agent_id=int(target_id),
            extra_condition=lambda tid=int(target_id), sid=int(enemy_skill_id): (
                self._target_still_casting_safe_skill(tid, sid)
                and valid_spell(tid, sid)
            ),
            log=False,
            aftercast_delay=40,
        )
        if casted:
            self._note_non_keystone_signet_cast()
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.register_interrupt_fired(
                    int(target_id), int(enemy_skill_id), int(Signet_of_Distraction_ID)
                )
                CombatDebug.log_event(
                    "KEYSTONE_VARIANT2_DISTRACTION_CAST",
                    target_id=int(target_id),
                    enemy_skill_id=int(enemy_skill_id),
                    policy="only_verified_spell_coordinated_cast_claim",
                )
            except Exception:
                pass
            return True
        release_interrupt_claim(int(target_id), int(enemy_skill_id), reason="distraction_dispatch_failed")
        return False

    def _cast_coordinated_cry_of_frustration(self):
        """Use optional Cry as a high-value packet interrupt without duplicate Cry casts.

        This path is active only when Cry of Frustration is actually equipped, so
        the normal Corruption/Sorrow/Unnatural Keystone bar is unchanged. A Cry
        may briefly cast at a dangerous off-focus packet; CastSkillIDAndRestoreTarget
        returns the Mesmer to the team's canonical focus immediately afterwards.
        """
        if int(Cry_of_Frustration_ID or 0) <= 0:
            return False
        if not self.IsSkillEquipped(Cry_of_Frustration_ID):
            return False
        if not self.CanCastSkillID(Cry_of_Frustration_ID):
            return False

        reservation = reserve_best_cry_packet()
        if reservation is None:
            return False

        target_id = int(reservation.target_id)
        enemy_skill_id = int(reservation.enemy_skill_id)
        did_cast = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=Cry_of_Frustration_ID,
            target_agent_id=target_id,
            extra_condition=lambda: (
                int(get_danger_casting_skill_id(target_id) or 0) == enemy_skill_id
                and interrupt_is_feasible(target_id, Cry_of_Frustration_ID)
            ),
            log=False,
            aftercast_delay=40,
        )
        if did_cast:
            register_cry_fired(reservation, source="keystone")
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "KEYSTONE_CRY_PACKET_INTERRUPT",
                    target_id=int(target_id),
                    enemy_skill_id=int(enemy_skill_id),
                    packet_key=int(reservation.packet_key),
                    covered_count=int(len(reservation.covered_casts)),
                    policy="dangerous_packet_first_then_restore_canonical_focus",
                )
            except Exception:
                pass
            return True

        release_cry_reservation(reservation, reason="keystone_cry_not_fired")
        return False

    def _cast_safe_dangerous_interrupt(self, snapshot: _KeystoneBarSnapshot):
        """Coordinated direct, Keystone-proxy and SoJ emergency interrupts."""
        if not snapshot.enemy_casting:
            return False

        # Dedicated direct interrupts get first refusal.
        for skill_id, mark_signet_spent in (
            (Power_Drain_ID, False),
            (Cry_of_Frustration_ID, False),
            (Signet_of_Disruption_ID, True),
        ):
            if skill_id <= 0 or not self.IsSkillEquipped(skill_id):
                continue
            if not self.CanCastSkillID(skill_id):
                continue

            disruption_validator = (
                (lambda enemy_id, enemy_skill_id: self._signet_of_disruption_can_interrupt(
                    int(enemy_id), int(enemy_skill_id)
                ))
                if int(skill_id) == int(Signet_of_Disruption_ID)
                else None
            )
            target_agent_id, casting_skill_id = self._pick_safe_dangerous_interrupt_target(
                interrupter_skill_id=skill_id,
                validator=disruption_validator,
            )
            if target_agent_id <= 0 or casting_skill_id <= 0:
                continue

            keystone_covered_casts = (
                self._keystone_adjacent_danger_casts(target_agent_id)
                if mark_signet_spent and snapshot.has_keystone_signet
                else []
            )
            did_cast = yield from self.CastSkillIDAndRestoreTarget(
                skill_id=skill_id,
                target_agent_id=target_agent_id,
                extra_condition=lambda: self._target_still_casting_safe_skill(
                    target_agent_id, casting_skill_id
                ),
                log=False,
                aftercast_delay=250,
            )
            if did_cast:
                if mark_signet_spent:
                    self._note_non_keystone_signet_cast()
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.register_interrupt_fired(
                        target_agent_id, casting_skill_id, skill_id
                    )
                except Exception:
                    pass
                if keystone_covered_casts:
                    self._register_keystone_coverage(
                        target_agent_id, skill_id, keystone_covered_casts
                    )
                return True

            release_interrupt_claim(
                target_agent_id,
                casting_skill_id,
                reason="keystone_direct_not_fired",
            )

        # While Keystone is active, an ordinary signet on an adjacent proxy can
        # interrupt the dangerous caster. This was missing from the old planner.
        if (yield from self._cast_keystone_proxy_interrupt(snapshot)):
            return True

        # Slow control fallback only after direct/proxy options had their chance.
        skill_id = int(Signet_of_Judgment_ID or 0)
        if (
            skill_id > 0
            and self.IsSkillEquipped(skill_id)
            and snapshot.enemy_in_spellcast
            and self.CanCastSkillID(skill_id)
        ):
            target_agent_id, casting_skill_id = self._pick_safe_dangerous_interrupt_target(
                interrupter_skill_id=skill_id,
                validator=lambda enemy_id, cast_id: self._is_signet_of_judgment_control_target_usable(
                    enemy_id, check_claimed=False
                ),
            )
            if target_agent_id > 0 and casting_skill_id > 0:
                self._claim_signet_of_judgment_target(target_agent_id)
                keystone_covered_casts = (
                    self._keystone_adjacent_danger_casts(target_agent_id)
                    if snapshot.has_keystone_signet
                    else []
                )
                did_cast = yield from self.CastSkillIDAndRestoreTarget(
                    skill_id=skill_id,
                    target_agent_id=target_agent_id,
                    extra_condition=lambda: (
                        self._target_still_casting_safe_skill(
                            target_agent_id, casting_skill_id
                        )
                        and self._is_signet_of_judgment_control_target_usable(
                            target_agent_id, check_claimed=False
                        )
                    ),
                    log=False,
                    aftercast_delay=250,
                )
                if did_cast:
                    self._note_non_keystone_signet_cast()
                    try:
                        from Py4GWCoreLib.Builds.Skills import CombatDebug
                        CombatDebug.register_interrupt_fired(
                            target_agent_id, casting_skill_id, skill_id
                        )
                    except Exception:
                        pass
                    if keystone_covered_casts:
                        self._register_keystone_coverage(
                            target_agent_id, skill_id, keystone_covered_casts
                        )
                    return True
                release_interrupt_claim(
                    target_agent_id,
                    casting_skill_id,
                    reason="keystone_soj_not_fired",
                )

        return False

    def _has_hexed_ally_for_hex_eater(self) -> bool:
        """A ready Hex Eater should not block Keystone if nobody is hexed."""
        try:
            from Py4GWCoreLib import AgentArray
            for ally_id in AgentArray.GetAllyArray():
                if Routines.Checks.Agents.IsAlive(ally_id) and Routines.Checks.Agents.IsHexed(ally_id):
                    return True
        except Exception:
            return False
        return False

    def _ready_signet_should_block_keystone_reset(self, skill_id: int, snapshot: _KeystoneBarSnapshot) -> bool:
        """A ready signet blocks Keystone reset only if it is usable now."""
        if skill_id == Hex_Eater_Signet_ID:
            return self._has_hexed_ally_for_hex_eater()

        if skill_id == Signet_of_Disruption_ID:
            # Do not hold Keystone hostage for an idle isolated foe. Disruption
            # blocks reset only when it can interrupt something now or can
            # generate useful Keystone packet pressure.
            target_id, _reason = self._get_signet_of_disruption_filler_target(snapshot)
            return int(target_id or 0) > 0

        if skill_id in (Signet_of_Clumsiness_ID, Castigation_Signet_ID, Bane_Signet_ID,
                        Signet_of_Rage_ID, Signet_of_Mystic_Wrath_ID):
            # FULL-SPAM damage signets (including Bane/Ruins): no reserve.
            # A ready damage signet is spent on any valid zero-idle target before
            # Keystone is allowed to reset the bar.
            return self._get_zero_idle_damage_target(int(skill_id))[0] > 0

        if skill_id == Signet_of_Judgment_ID:
            return self._get_signet_of_judgment_target(claim_target=False) > 0

        if skill_id == Tryptophan_Signet_ID:
            return snapshot.enemy_in_spellcast and self._get_tryptophan_signet_target() > 0

        if skill_id in (
            Unnatural_Signet_ID,
            Signet_of_Sorrow_ID,
            Signet_of_Weariness_ID,
            Signet_of_Corruption_Kurzick_ID,
            Signet_of_Corruption_Luxon_ID,
        ):
            return self._get_zero_idle_damage_target(int(skill_id))[0] > 0

        # Unknown optional signets are treated conservatively: if they are ready,
        # try to spend them before Keystone resets.
        return True

    def _all_other_equipped_signets_spent_or_unusable(self, snapshot: _KeystoneBarSnapshot) -> bool:
        other_signet_found = False
        for slot in range(1, 9):
            skill_id = int(SkillBar.GetSkillIDBySlot(slot) or 0)
            if skill_id == 0 or skill_id == Keystone_Signet_ID:
                continue
            if not Skill.Flags.IsSignet(skill_id):
                continue

            other_signet_found = True

            if not Routines.Checks.Skills.IsSkillSlotReady(slot):
                continue

            if self._ready_signet_should_block_keystone_reset(skill_id, snapshot):
                return False

        return other_signet_found

    def _note_non_keystone_signet_cast(self) -> None:
        self._non_keystone_signet_cast_since_last_keystone = True

    def _note_keystone_reset_cast(self) -> None:
        # After Keystone is used as the reset, start a fresh spending cycle.
        self._non_keystone_signet_cast_since_last_keystone = False
        self._precombat_keystone_primed = False
        # EXP20: grant exactly one immediate post-Keystone Mantra attempt.
        # The next combat tick either casts a ready Mantra or consumes this
        # opportunity and proceeds straight to signet damage with zero waiting.
        self._post_keystone_mantra_opportunity = True

    def _note_keystone_prime_cast(self, snapshot: _KeystoneBarSnapshot) -> None:
        # Outside combat, allow only one pre-pull prime while close to the group.
        # In combat, Keystone may still be maintained for its offensive effect,
        # but this must not clear the spent-signet flag used for reset timing.
        if not snapshot.enemy_in_spellcast:
            self._precombat_keystone_primed = True

    def _has_spent_non_keystone_signet(self) -> bool:
        if self._non_keystone_signet_cast_since_last_keystone:
            return True

        # Fallback for cases where this build logic started mid-combat or a
        # signet was cast by another path: if any equipped non-Keystone signet
        # is on recharge, consider the current cycle as spent.
        for slot in range(1, 9):
            skill_id = int(SkillBar.GetSkillIDBySlot(slot) or 0)
            if skill_id == 0 or skill_id == Keystone_Signet_ID:
                continue
            if not Skill.Flags.IsSignet(skill_id):
                continue
            if not Routines.Checks.Skills.IsSkillSlotReady(slot):
                return True

        return False

    def _should_cast_keystone_signet(self, snapshot: _KeystoneBarSnapshot) -> bool:
        if not snapshot.precombat_setup or not snapshot.has_symbolic_celerity or snapshot.has_keystone_signet:
            return False

        # Once an enemy is in spellcast range, keep Keystone ready for the
        # offensive signet package.
        if snapshot.enemy_in_spellcast:
            return snapshot.keystone_signet_needed

        # Before the pull: prime Keystone once in the expanded setup radius,
        # but do not spam it while waiting near an enemy group.
        return snapshot.keystone_signet_needed and not self._precombat_keystone_primed

    def _should_reset_signets_with_keystone(self, snapshot: _KeystoneBarSnapshot) -> bool:
        if not snapshot.close_to_aggro or not snapshot.has_symbolic_celerity:
            return False
        # Never reset over a genuinely ready primary damage signet.
        if self._has_ready_primary_damage_signet():
            return False
        if not self._has_spent_non_keystone_signet():
            return False
        return self._all_other_equipped_signets_spent_or_unusable(snapshot)

    def _is_skill_strictly_ready(self, skill_id: int) -> bool:
        """Check the exact equipped slot, recharge and normal cast gates.

        This avoids decision/log attempts for a skill whose ID exists but whose
        actual slot is still recharging (important around Arcane Mimicry slot
        replacement and Keystone resets).
        """
        skill_id = int(skill_id or 0)
        if skill_id <= 0 or not self.IsSkillEquipped(skill_id):
            return False
        try:
            slot = int(SkillBar.GetSlotBySkillID(skill_id) or 0)
            if not (1 <= slot <= 8):
                return False
            if not Routines.Checks.Skills.IsSkillSlotReady(slot):
                return False
        except Exception:
            return False
        return bool(self.CanCastSkillID(skill_id))

    def _cluster_dps_unnatural_select(self, packet: list[int]) -> tuple[int, str]:
        """A/B test: favor verifiable adjacent splash inside the committed fight.

        Entirely advisory: never adds a cast gate, never modifies team focus,
        interrupt routing, execute leases or the shared engagement plan.  A
        fallback to the unchanged selector is mandatory on missing geometry.
        """
        if not KEYSTONE_CLUSTER_DPS_AB_ENABLED:
            return 0, "ab_disabled"
        try:
            from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import get_team_combat_plan
            from Py4GWCoreLib.Py4GWcorelib import Utils
            plan = get_team_combat_plan(
                filter_range=float(POWER_CLUSTER_FILTER_RANGE),
                minimum_enemies=POWER_CLUSTER_MIN_ENEMIES,
                consumer_role="keystone_cluster_dps_ab",
            )
            if str(plan.mode) not in ("cluster", "cleanup"):
                return 0, "non_cluster_mode"
            members = tuple(sorted(set(int(x) for x in plan.member_ids if int(x or 0) > 0)))
            # Never extend into another packet. Every alternate target must be
            # in the same authoritative fight AND locally in casting range.
            usable = [aid for aid in members if self._is_valid_power_cluster_enemy(aid)]
            packet_set = set(int(x) for x in packet)
            cleanup_test = False
            focus = int(plan.focus_target or 0)
            if str(plan.mode) == "cleanup":
                if str(plan.reason) != "engagement_wounded_cleanup":
                    return 0, "hard_cleanup_preserved"
                if focus not in packet_set or len(packet_set) != 1:
                    return 0, "cleanup_no_unique_focus"
                if len(usable) < 3:
                    return 0, "cleanup_not_dense"
                try:
                    focus_hp = float(Agent.GetHealth(focus))
                    if not (0.15 < focus_hp <= 0.35):
                        return 0, "cleanup_hp_guard"
                    # Never steal the designated <=5% finisher or a critical kill.
                    if any(0 < float(Agent.GetHealth(x)) <= 0.05 for x in usable):
                        return 0, "execute_guard"
                except Exception:
                    return 0, "cleanup_hp_unknown"
                cleanup_test = True
                candidates = usable
            else:
                candidates = [aid for aid in usable if aid in packet_set]
            if len(candidates) < 2:
                return 0, "too_few_targets"

            # Use exact cohort geometry, not a world-wide enemy scan: an enemy
            # outside the committed fight is never considered a splash target
            # for the experiment's score.
            positions = {}
            for aid in usable:
                try:
                    positions[int(aid)] = Agent.GetXY(int(aid))
                except Exception:
                    continue
            if len(positions) < 2:
                return 0, "missing_geometry"
            scored = []
            for aid in candidates:
                if aid not in positions:
                    continue
                try:
                    hexed = bool(Agent.IsHexed(aid))
                    enchanted = bool(Agent.IsEnchanted(aid))
                    hp = float(Agent.GetHealth(aid))
                except Exception:
                    # No conditional-effect evidence -> conservative fallback.
                    continue
                adjacent = sum(
                    1 for other, xy in positions.items()
                    if int(other) != int(aid)
                    and float(Utils.Distance(positions[aid], xy)) <= float(POWER_CLUSTER_RADIUS)
                )
                conditional = bool(hexed or enchanted)
                # Heuristic only (NOT measured damage): Keystone adds adjacent
                # pressure while Unnatural gains a second splash component when
                # the direct target is hexed/enhanced. Ignore nearly dead lanes.
                score = int(adjacent) * (2 if conditional else 1)
                if hp > 0.0 and hp <= float(KEYSTONE_EXECUTE_HP_FRACTION):
                    score = -1
                scored.append((int(score), int(adjacent), int(hexed), int(conditional), int(aid)))
            if not scored:
                return 0, "no_geometry_scores"
            best_score = max(row[0] for row in scored)
            if best_score <= 0:
                return 0, "no_splash_gain"
            if cleanup_test:
                focus_score = next((x[0] for x in scored if x[4] == focus), -1)
                # Only one offensive signet may break a wounded focus if a
                # *conditional* target reaches >=2 other packet members and
                # improves the estimated splash score by at least 2 points.
                if not any(row[0] >= focus_score + 2 and row[1] >= 2 and row[3] for row in scored):
                    return 0, "cleanup_no_clear_aoe_gain"
                scored = [row for row in scored if row[1] >= 2 and row[3] and row[0] >= focus_score + 2]
                best_score = max(row[0] for row in scored)
            best = [row for row in scored if row[0] == best_score]
            # Equal-scoring centers may be distributed across accounts. Claims
            # are a preference only and must NEVER postpone a ready signet.
            best.sort(key=lambda r: (-r[2], -r[3], -r[1], r[4]))
            ordered = self._lane_rotate_targets([row[4] for row in best])
            chosen = 0
            for aid in ordered:
                if self._is_packet_signet_target_claimed(Unnatural_Signet_ID, int(aid)):
                    continue
                if self._claim_packet_signet_target(Unnatural_Signet_ID, int(aid)):
                    chosen = int(aid)
                    break
            if chosen <= 0:
                chosen = int(ordered[0])
            selected = next(row for row in best if row[4] == chosen)
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_state_event(
                    "KEYSTONE_CLUSTER_DPS_AB_TARGET",
                    signature_fields=("mode", "target_id"),
                    min_interval_ms=1500, heartbeat_ms=3000,
                    mode="wounded_cleanup_hybrid" if cleanup_test else "cluster",
                    target_id=int(chosen), focus_id=int(focus),
                    splash_members=int(selected[1]),
                    effect_confirmed=bool(selected[3]),
                    rank_score=int(selected[0]),
                    packet_size=len(members),
                    policy="cohort_only_unnatural_no_cast_delay",
                )
            except Exception:
                pass
            return int(chosen), (
                ("cluster_ab_cleanup_" if cleanup_test else "cluster_ab_")
                + ("hexed_aoe" if selected[2] else "enchanted_aoe" if selected[3]
                   else "keystone_splash")
            )
        except Exception:
            return 0, "ab_geometry_failed_open"

    def _get_unnatural_signet_fast_target(self) -> tuple[int, str]:
        """Hex-first Unnatural targeting inside the authoritative packet.

        Stage 19 contract:
        * if one or more packet enemies are hexed, Unnatural prefers those foes;
        * different Keystone accounts try to spread over different hexed foes via
          the existing short packet-target claims / deterministic account lane;
        * if all preferred claims are occupied, cast immediately on the lane's
          best hexed foe anyway (claims are preference only, never a wait gate);
        * if no foe is hexed, fall straight through to the previous behavior.

        Enchantments remain a secondary legacy enhancement only when no hexed
        target exists, because Unnatural mechanically benefits from either.
        """
        enemies = [int(x) for x in self._get_zero_idle_enemy_targets() if int(x or 0) > 0]
        if not enemies:
            return 0, "no_authoritative_target"

        # Experiment B: cluster-aware selection with immediate fallback to
        # the frozen, proven rotation whenever no safe improvement is found.
        cluster_target, cluster_reason = self._cluster_dps_unnatural_select(enemies)
        if cluster_target > 0:
            return int(cluster_target), str(cluster_reason)

        def rank(enemy_id: int) -> tuple[int, int, float, int]:
            try:
                hp = float(Agent.GetHealth(int(enemy_id)) or 0.0)
            except Exception:
                hp = 1.0
            near_dead = 1 if hp > 0.0 and hp <= float(KEYSTONE_SIGNET_LOW_HP_AVOID_FRACTION) else 0
            return (
                near_dead,
                -self._count_signet_of_judgment_adjacent_enemies(int(enemy_id)),
                self._distance_to_player(int(enemy_id)),
                int(enemy_id),
            )

        hexed: list[int] = []
        enchanted: list[int] = []
        for enemy_id in enemies:
            try:
                if Agent.IsHexed(int(enemy_id)):
                    hexed.append(int(enemy_id))
                elif Agent.IsEnchanted(int(enemy_id)):
                    enchanted.append(int(enemy_id))
            except Exception:
                continue

        preferred = hexed if hexed else enchanted
        if preferred:
            preferred.sort(key=rank)
            # Rotate the equally useful set by account lane first, then use the
            # existing short packet claim to spread simultaneous Unnatural casts.
            if len(preferred) > 1:
                best_dead = int(rank(int(preferred[0]))[0])
                bucket = [x for x in preferred if int(rank(int(x))[0]) == best_dead]
                rest = [x for x in preferred if x not in set(bucket)]
                preferred = self._lane_rotate_targets(bucket) + rest
            for enemy_id in preferred:
                if self._is_packet_signet_target_claimed(Unnatural_Signet_ID, int(enemy_id)):
                    continue
                if self._claim_packet_signet_target(Unnatural_Signet_ID, int(enemy_id)):
                    return int(enemy_id), (
                        "hexed_aoe_claim_spread" if hexed else "enchanted_aoe_claim_spread"
                    )
            # Strict no-wait rule: claims are only distribution hints.  If all
            # are occupied, use our deterministic lane target immediately.
            return int(preferred[0]), (
                "hexed_aoe_fail_open_no_wait" if hexed else "enchanted_aoe_fail_open_no_wait"
            )

        # No hexed/enhanced foe: unchanged zero-idle packet behavior.
        return self._get_zero_idle_damage_target(int(Unnatural_Signet_ID))

    def _cast_immediate_primary_damage_signet(self, snapshot: _KeystoneBarSnapshot):
        """Hard fast-path: SoJ -> Unnatural -> Bane -> Castigation.

        Castigation is optional and is included only when it is equipped. The
        path runs before Keystone reset logic, so Keystone cannot reset while an
        equipped offensive signet is genuinely ready and a valid enemy exists.
        Claims only influence target preference; they never block this path.
        """
        # Dynamic throughput order:
        #   SoJ first.  Sorrow gets a special corpse-confirmed turbo lane because
        #   the nearby-corpse condition can instantly recharge it; otherwise
        #   Unnatural is the reliable 10s-recharge damage signet and must not be
        #   starved behind ordinary Sorrow/Corruption filler casts.
        #
        # The corpse-Sorrow attempt is handled before this loop.
        if self._is_skill_strictly_ready(int(Signet_of_Judgment_ID or 0)):
            target_id = int(self._get_signet_of_judgment_target(claim_target=True) or 0)
            if target_id > 0 and Routines.Checks.Agents.IsAlive(target_id):
                did_cast = yield from self.CastSkillIDAndRestoreTarget(
                    skill_id=int(Signet_of_Judgment_ID),
                    target_agent_id=target_id,
                    log=False,
                    aftercast_delay=40,
                )
                if did_cast:
                    self._note_non_keystone_signet_cast()
                    self._log_primary_signet_cast(
                        int(Signet_of_Judgment_ID), target_id,
                        reason="balanced_soj_first", path="strict_primary_damage",
                    )
                    return True

        if self._is_skill_strictly_ready(int(Signet_of_Sorrow_ID or 0)):
            sorrow_target = int(self._get_sorrow_signet_target(require_corpse=True, claim_target=True) or 0)
            if sorrow_target > 0 and Routines.Checks.Agents.IsAlive(sorrow_target):
                did_cast = yield from self.CastSkillIDAndRestoreTarget(
                    skill_id=int(Signet_of_Sorrow_ID),
                    target_agent_id=sorrow_target,
                    log=False,
                    aftercast_delay=40,
                )
                if did_cast:
                    self._note_non_keystone_signet_cast()
                    self._log_primary_signet_cast(
                        int(Signet_of_Sorrow_ID), sorrow_target,
                        reason="corpse_instant_recharge_turbo", path="strict_primary_damage",
                    )
                    try:
                        from Py4GWCoreLib.Builds.Skills import CombatDebug
                        CombatDebug.log_event(
                            "KEYSTONE_SORROW_TURBO_CAST",
                            skill_id=int(Signet_of_Sorrow_ID),
                            target_id=int(sorrow_target),
                            policy="corpse_sorrow_before_unnatural",
                        )
                    except Exception:
                        pass
                    return True

        for skill_id in (
            Unnatural_Signet_ID,
            Signet_of_Corruption_Kurzick_ID,
            Signet_of_Corruption_Luxon_ID,
            Signet_of_Sorrow_ID,
            Bane_Signet_ID,
            Castigation_Signet_ID,
            Signet_of_Rage_ID,
            Signet_of_Mystic_Wrath_ID,
        ):
            skill_id = int(skill_id or 0)
            if not self._is_skill_strictly_ready(skill_id):
                continue

            if skill_id == int(Signet_of_Judgment_ID):
                target_id = int(self._get_signet_of_judgment_target(claim_target=True) or 0)
                reason = "balanced_soj_first"
            elif skill_id == int(Unnatural_Signet_ID):
                target_id, reason = self._get_unnatural_signet_fast_target()
                target_id = int(target_id or 0)
            else:
                target_id, reason = self._get_zero_idle_damage_target(skill_id)
                target_id = int(target_id or 0)

            if target_id <= 0 or not Routines.Checks.Agents.IsAlive(target_id):
                continue

            did_cast = yield from self.CastSkillIDAndRestoreTarget(
                skill_id=skill_id,
                target_agent_id=target_id,
                log=False,
                aftercast_delay=40,
            )
            if did_cast:
                self._note_non_keystone_signet_cast()
                self._log_primary_signet_cast(
                    skill_id,
                    target_id,
                    reason=str(reason),
                    path="strict_primary_damage",
                )
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "KEYSTONE_STRICT_DAMAGE_CAST",
                        skill_id=skill_id,
                        target_id=target_id,
                        reason=str(reason),
                        policy="zero_idle_soj_unnatural_bane_castigation_before_keystone",
                    )
                except Exception:
                    pass
                return True
        return False

    def _should_defer_sorrow_turbo(self) -> tuple[bool, str]:
        """Bound corpse-Sorrow turbo so other high-value ready signets still fire."""
        if int(getattr(self, "_sorrow_turbo_streak", 0) or 0) < 1:
            return False, "first_turbo_allowed"

        # A real Unnatural AoE is worth interleaving immediately.
        if self._is_skill_strictly_ready(int(Unnatural_Signet_ID or 0)):
            try:
                target_id, reason = self._get_unnatural_signet_fast_target()
                if int(target_id or 0) > 0 and ("hexed_aoe" in str(reason or "") or "enchanted_aoe" in str(reason or "")):
                    return True, "ready_unnatural_aoe"
            except Exception:
                pass

        # Corruption may not sit ready indefinitely behind an instant-recharging
        # Sorrow loop. After ~1.0s ready, interleave it once, then turbo can resume.
        now = int(get_game_tick() or 0)
        for cid in (int(Signet_of_Corruption_Kurzick_ID or 0), int(Signet_of_Corruption_Luxon_ID or 0)):
            if cid <= 0 or not self.IsSkillEquipped(cid) or not self._is_skill_strictly_ready(cid):
                continue
            since = int(self._signet_ready_since.get(cid, 0) or 0)
            if since > 0 and now - since >= 1000:
                return True, "corruption_ready_1000ms"

        # Even without the two cases above, never allow more than two corpse turbo
        # casts in a row while another damage signet is ready.
        if int(getattr(self, "_sorrow_turbo_streak", 0) or 0) >= 2:
            for sid in (int(Unnatural_Signet_ID or 0), int(Signet_of_Corruption_Kurzick_ID or 0), int(Signet_of_Corruption_Luxon_ID or 0)):
                if sid > 0 and self.IsSkillEquipped(sid) and self._is_skill_strictly_ready(sid):
                    return True, "max_two_turbo_then_interleave"
        return False, "turbo_continue"

    def _cast_full_throughput_signet_spam(self, snapshot: _KeystoneBarSnapshot):
        """Maximum-throughput foe-signet rotation.

        Priority:
            copied Signet of Judgment
            -> Signet of Sorrow when a corpse can trigger instant recharge
            -> Unnatural Signet on a real enhanced AoE packet
            -> otherwise Signet of Corruption before single-target Unnatural
            -> ordinary Signet of Sorrow
            -> backward-compatible filler signets

        There is deliberately NO interrupt planning, reservation, claim wait,
        dangerous-cast detour, round-robin hold, or "wait for a cast" gate here.
        If a signet is ready and a legal enemy target exists, fire it now.
        """
        # v31 hotfix: packet target lanes/claims still provide the useful
        # cross-account spatial spread, but temporal cadence is deliberately
        # non-blocking. Do not yield before a ready offensive signet: the v30
        # ready-pulse/gather path caused visible idle time in live play.
        dangerous_caster_packet = False
        soj_ready = self._is_skill_strictly_ready(int(Signet_of_Judgment_ID or 0))
        # The packet-danger scan is diagnostic ONLY for copied SoJ. Avoid
        # repeating this spatial query on every full-spam tick of regular
        # Keystone accounts which do not even have a ready SoJ.
        if soj_ready:
            try:
                from Py4GWCoreLib.Builds.Skills.CombatSense import dangerous_aoe_caster_cluster
                dangerous_caster_packet, _danger_anchor, _danger_members = dangerous_aoe_caster_cluster(
                    range_value=Range.Spellcast.value,
                    minimum_dangerous_casters=2,
                )
            except Exception:
                dangerous_caster_packet = False

        # SoJ remains absolute priority.  After SoJ, corpse-confirmed Sorrow
        # is allowed to turbo-loop; without that instant-recharge condition,
        # Unnatural is intentionally ahead of Corruption and ordinary Sorrow.
        if soj_ready:
            target_id = int(self._get_signet_of_judgment_target(claim_target=True) or 0)
            if target_id > 0 and Routines.Checks.Agents.IsAlive(target_id):
                did_cast = yield from self.CastSkillIDAndRestoreTarget(
                    skill_id=int(Signet_of_Judgment_ID), target_agent_id=target_id,
                    log=False, aftercast_delay=40,
                )
                if did_cast:
                    self._note_non_keystone_signet_cast()
                    self._log_primary_signet_cast(
                        int(Signet_of_Judgment_ID), target_id,
                        reason="full_spam_soj", path="full_throughput",
                    )
                    return True

        if self._is_skill_strictly_ready(int(Signet_of_Sorrow_ID or 0)):
            defer_turbo, defer_reason = self._should_defer_sorrow_turbo()
            if not defer_turbo:
                sorrow_target = int(self._get_sorrow_signet_target(require_corpse=True, claim_target=True) or 0)
                if sorrow_target > 0 and Routines.Checks.Agents.IsAlive(sorrow_target):
                    did_cast = yield from self.CastSkillIDAndRestoreTarget(
                        skill_id=int(Signet_of_Sorrow_ID), target_agent_id=sorrow_target,
                        log=False, aftercast_delay=40,
                    )
                    if did_cast:
                        self._note_non_keystone_signet_cast()
                        self._sorrow_turbo_streak = int(getattr(self, "_sorrow_turbo_streak", 0) or 0) + 1
                        self._log_primary_signet_cast(
                            int(Signet_of_Sorrow_ID), int(sorrow_target),
                            reason="corpse_instant_recharge_turbo", path="sorrow_turbo",
                        )
                        try:
                            from Py4GWCoreLib.Builds.Skills import CombatDebug
                            CombatDebug.log_event(
                                "KEYSTONE_SORROW_TURBO_CAST",
                                skill_id=int(Signet_of_Sorrow_ID),
                                target_id=int(sorrow_target),
                                turbo_streak=int(self._sorrow_turbo_streak),
                                policy="bounded_turbo_interleave_unnatural_corruption",
                            )
                        except Exception:
                            pass
                        return True
            else:
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "KEYSTONE_SORROW_TURBO_DEFER",
                        reason=str(defer_reason),
                        turbo_streak=int(getattr(self, "_sorrow_turbo_streak", 0) or 0),
                        policy="interleave_ready_high_value_signet",
                    )
                except Exception:
                    pass

        # Adaptive Corruption/Unnatural lane.  Unnatural keeps priority when it
        # has a real hexed/enchanted AoE packet (its best use case).  Otherwise
        # Corruption goes first so it cannot sit ready for 5-7 seconds during
        # small packets/cleanup while Unnatural is only providing single-target
        # pressure.  This preserves the strong Unnatural cluster spam measured
        # in the current logs while recovering otherwise wasted Corruption casts.
        unnatural_probe_target = 0
        unnatural_probe_reason = ""
        if self._is_skill_strictly_ready(int(Unnatural_Signet_ID or 0)):
            try:
                unnatural_probe_target, unnatural_probe_reason = self._get_unnatural_signet_fast_target()
                unnatural_probe_target = int(unnatural_probe_target or 0)
                unnatural_probe_reason = str(unnatural_probe_reason or "")
            except Exception:
                unnatural_probe_target, unnatural_probe_reason = 0, ""

        unnatural_has_real_aoe = False
        if unnatural_probe_target > 0 and ("hexed_aoe" in unnatural_probe_reason or "enchanted_aoe" in unnatural_probe_reason):
            try:
                unnatural_has_real_aoe = (
                    int(self._count_signet_of_judgment_adjacent_enemies(unnatural_probe_target) or 0) >= 1
                )
            except Exception:
                unnatural_has_real_aoe = True

        corruption_ids = (
            Signet_of_Corruption_Kurzick_ID,
            Signet_of_Corruption_Luxon_ID,
        )
        # Moderate anti-starvation: preserve fresh Unnatural AoE priority, but if
        # Corruption has already been ready for 2.5s, spend it before another AoE
        # Unnatural. This targets the long live-log outliers without disturbing
        # the excellent median/zero-idle Unnatural behavior.
        now_tick = int(get_game_tick() or 0)
        aged_corruption = False
        for cid in corruption_ids:
            cid = int(cid or 0)
            if cid <= 0 or not self.IsSkillEquipped(cid) or not self._is_skill_strictly_ready(cid):
                continue
            since = int(self._signet_ready_since.get(cid, 0) or 0)
            if since > 0 and now_tick - since >= 2500:
                aged_corruption = True
                break
        if unnatural_has_real_aoe and not aged_corruption:
            adaptive_damage_order = (Unnatural_Signet_ID,) + corruption_ids
            adaptive_policy = "unnatural_aoe_before_fresh_corruption"
        else:
            adaptive_damage_order = corruption_ids + (Unnatural_Signet_ID,)
            adaptive_policy = (
                "aged_corruption_2500ms_before_unnatural"
                if aged_corruption else "corruption_before_single_target_unnatural"
            )

        for skill_id in adaptive_damage_order + (
            Signet_of_Sorrow_ID,
            # Backward-compatible fallbacks: if an older bar still equips one
            # of these, it is also allowed to spam immediately.
            Signet_of_Disruption_ID,
            Bane_Signet_ID,
            Castigation_Signet_ID,
            Signet_of_Rage_ID,
            Signet_of_Mystic_Wrath_ID,
        ):
            skill_id = int(skill_id or 0)
            if skill_id <= 0 or not self.IsSkillEquipped(skill_id):
                continue
            if not self._is_skill_strictly_ready(skill_id):
                continue

            if skill_id == int(Signet_of_Judgment_ID):
                target_id = int(self._get_signet_of_judgment_target(claim_target=True) or 0)
                reason = "full_spam_soj"
            elif skill_id == int(Signet_of_Sorrow_ID):
                # Me/N MAX-SPAM: no waiting for a corpse condition. Cast as soon
                # as the signet is ready. If a nearby corpse causes the skill to
                # recharge instantly, it returns to the top of this priority
                # list on the next controller pass and is fired again.
                target_id, sorrow_target_reason = self._get_zero_idle_damage_target(skill_id)
                target_id = int(target_id or 0)
                reason = f"full_spam_sorrow_{sorrow_target_reason}"
            elif skill_id in (
                int(Signet_of_Corruption_Kurzick_ID),
                int(Signet_of_Corruption_Luxon_ID),
            ):
                # Do not wait for the 'perfect' hex/condition packet. The base
                # signet + Keystone pressure is valuable immediately; existing
                # conditions/hexes only improve the result.
                target_id, corruption_target_reason = self._get_zero_idle_damage_target(skill_id)
                target_id = int(target_id or 0)
                reason = f"full_spam_corruption_{corruption_target_reason}_{adaptive_policy}"
            elif skill_id == int(Signet_of_Disruption_ID):
                # Backward-compatible SoD: unconditional pressure if still equipped.
                target_id, sod_target_reason = self._get_zero_idle_damage_target(skill_id)
                target_id = int(target_id or 0)
                reason = f"full_spam_sod_unconditional_{sod_target_reason}"
            elif skill_id == int(Unnatural_Signet_ID):
                if unnatural_probe_target > 0:
                    target_id = int(unnatural_probe_target)
                    reason = str(unnatural_probe_reason or "zero_idle")
                else:
                    target_id, reason = self._get_unnatural_signet_fast_target()
                    target_id = int(target_id or 0)
                reason = f"full_spam_{reason}_{adaptive_policy}"
            else:
                target_id, reason = self._get_zero_idle_damage_target(skill_id)
                target_id = int(target_id or 0)
                reason = f"full_spam_{reason}"

            if target_id <= 0 or not Routines.Checks.Agents.IsAlive(target_id):
                continue

            enemy_skill_before = 0
            try:
                if Agent.IsCasting(int(target_id)):
                    enemy_skill_before = int(Agent.GetCastingSkillID(int(target_id)) or 0)
            except Exception:
                enemy_skill_before = 0

            did_cast = yield from self.CastSkillIDAndRestoreTarget(
                skill_id=skill_id,
                target_agent_id=target_id,
                log=False,
                aftercast_delay=40,
            )
            if not did_cast:
                continue

            self._note_non_keystone_signet_cast()

            if skill_id in (int(Bane_Signet_ID), int(Castigation_Signet_ID), int(Signet_of_Rage_ID), int(Signet_of_Mystic_Wrath_ID)):
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "KEYSTONE_MONK_SIGNET_CAST",
                        skill_id=int(skill_id), target_id=int(target_id),
                        self_enchantments=int(self._monk_self_enchantment_count()) if skill_id == int(Signet_of_Mystic_Wrath_ID) else -1,
                        known_enemy_adrenaline_skills=int(self._monk_known_enemy_adrenaline_skills(target_id)) if skill_id == int(Signet_of_Rage_ID) else -1,
                        reason=str(reason), policy="bonus_preferred_unconditional_damage_cleanup_execute",
                    )
                except Exception:
                    pass

            # Diagnostic only: if the enemy was casting when this MAX-SPAM
            # signet was fired, follow that activation for a short window and
            # classify whether the cast stopped after our hit.
            if enemy_skill_before > 0:
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.register_interrupt_fired(
                        int(target_id),
                        int(enemy_skill_before),
                        int(skill_id),
                    )
                    CombatDebug.log_event(
                        "INTERRUPT_CANDIDATE_FIRED",
                        target_id=int(target_id),
                        enemy_skill_id=int(enemy_skill_before),
                        source_skill_id=int(skill_id),
                        policy="max_spam_no_reservation_measure_only",
                    )
                except Exception:
                    pass

            if dangerous_caster_packet and int(skill_id) == int(Signet_of_Judgment_ID):
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "DANGEROUS_CASTER_SOJ_OPENER",
                        target_id=int(target_id),
                        policy="soj_first_no_team_offense_gate",
                    )
                except Exception:
                    pass

            self._log_primary_signet_cast(
                skill_id,
                target_id,
                reason=str(reason),
                path="full_throughput_spam",
            )
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "KEYSTONE_FULL_SPAM_CAST",
                    skill_id=int(skill_id),
                    target_id=int(target_id),
                    reason=str(reason),
                    policy="soj_sod_ruin_unnatural_immediate_no_interrupt_logic",
                )
            except Exception:
                pass
            return True

        return False

    def _has_ready_primary_damage_signet(self) -> bool:
        """Return True while a high-value offensive signet can be fired now.

        With four Keystone Mesmers, reserving a ready offensive signet for a
        hypothetical future interrupt costs more pressure than it saves. Claims
        and interrupt planning therefore must not delay SoJ, Unnatural, Bane, or
        an equipped Castigation Signet.
        """
        for skill_id in (
            Signet_of_Judgment_ID,
            Signet_of_Sorrow_ID,
            Signet_of_Corruption_Kurzick_ID,
            Signet_of_Corruption_Luxon_ID,
            Unnatural_Signet_ID,
            Bane_Signet_ID,
            Castigation_Signet_ID,
            Signet_of_Rage_ID,
            Signet_of_Mystic_Wrath_ID,
        ):
            skill_id = int(skill_id or 0)
            if skill_id <= 0 or not self.IsSkillEquipped(skill_id):
                continue
            if self._is_skill_strictly_ready(skill_id):
                return True
        return False


    # -------- POISON_KS_V2: exact 2 Me/N + 3 Me/A bar-aware profile ----
    # The historical SoJ/Arcane-Mimicry code deliberately remains untouched.
    # Only the two explicit poison bars enter this branch; no player names,
    # party positions, bots, movement routines or account settings are used.
    def _poison_ks_role(self) -> str:
        common = (Keystone_Signet_ID, Symbolic_Celerity_ID, Mantra_of_Inscriptions_ID)
        if not all(int(sid or 0) > 0 and self.IsSkillEquipped(sid) for sid in common):
            return ""
        if self.IsSkillEquipped(Arcane_Mimicry_ID):
            return ""  # existing Mimicry logic stays available on old bars
        if (self.IsSkillEquipped(Barbed_Signet_ID)
                and self.IsSkillEquipped(Oppressive_Gaze_ID)):
            return "condition_seeder"
        if (self.IsSkillEquipped(Signet_of_Toxic_Shock_ID)
                and self.IsSkillEquipped(Signet_of_Deadly_Corruption_ID)):
            return "assassin_spreader" if self.IsSkillEquipped(Epidemic_ID) else "assassin_damage"
        return ""

    @staticmethod
    def _poison_ks_status(agent_id: int) -> tuple[bool, bool, bool]:
        # Values are observed game state, not an optimistic cast request.
        try:
            poison = bool(Agent.IsPoisoned(int(agent_id)))
            bleed = bool(Agent.IsBleeding(int(agent_id)))
            conditioned = bool(Agent.IsConditioned(int(agent_id)))
            return poison, bleed, conditioned
        except Exception:
            return False, False, False

    def _poison_ks_targets(self) -> list[int]:
        # Urgoz hard override MUST precede poison/cluster/execute selection.
        # TeamCombatFocus detects Twisted Bark, but the poison subclass builds
        # a separate packet and was ignoring that priority in real logs.
        bark = int(self._get_elite_priority_target() or 0)
        if bark > 0:
            return [bark]
        # Reuse shared healer/support/cluster anchor and small-party fallback.
        anchor = int(self._get_power_cluster_anchor(minimum_enemies=1) or 0)
        members = list(self._get_power_cluster_members(anchor)) if anchor else []
        if not members:
            fallback = int(self._small_party_local_fallback_target() or 0)
            if fallback:
                members = [fallback]
        # Use the same <=5% split in poison-key variants too.  Nobody
        # outside the claimed 2-3 Mesmers peels from the cluster.
        split = self._keystone_execute_split_plan()
        if split is not None:
            assigned, retained = split
            members = [int(assigned)] if int(assigned) > 0 else list(retained)
        unique = list(dict.fromkeys(int(a) for a in members if a))
        def score(a):
            try:
                dangerous = bool(self._is_safe_dangerous_cast(a))
                healer = bool(self._is_monk_or_ritualist_enemy(a))
                execute = bool(0.0 < float(Agent.GetHealth(int(a))) <= KEYSTONE_EXECUTE_HP_FRACTION + 1e-7)
                nearby = int(self._count_signet_of_judgment_adjacent_enemies(a) or 0)
                hp = float(Agent.GetHealth(a) or 0)
                return (-int(dangerous), -int(healer), -int(execute), -nearby, -hp, self._distance_to_player(a), a)
            except Exception:
                return (0,0,0,0,0,999999,a)
        return sorted((a for a in unique if self._is_valid_power_cluster_enemy(a)), key=score)

    def _poison_ks_neighbours(self, target: int) -> list[int]:
        return [a for a in self._get_power_cluster_members(int(target)) if a != int(target)]

    def _poison_ks_best(self, targets, predicate, extra_score=None, skill_id=0, *, spread_anchor=False) -> int:
        """Preferred condition/hex + clustered priority + shared signet distribution.

        Claims are a SOFT penalty, not a waiting gate.  A high-quality packet
        target is never abandoned when all targets are temporarily claimed.
        """
        eligible = [int(a) for a in targets if predicate(int(a))]
        if not eligible:
            return 0
        def rank(a):
            score = 0.0
            if extra_score is not None:
                try:
                    score = float(extra_score(a))
                except Exception:
                    pass
            # Shared execute target gets priority for genuine damage signets,
            # not for poison preparation / distribution spells.
            if skill_id and int(skill_id or 0) in (
                int(Unnatural_Signet_ID), int(Signet_of_Sorrow_ID),
                int(Signet_of_Toxic_Shock_ID), int(Signet_of_Deadly_Corruption_ID),
            ):
                try:
                    if 0.0 < float(Agent.GetHealth(int(a))) <= KEYSTONE_EXECUTE_HP_FRACTION + 1e-7:
                        score += 12.0
                except Exception:
                    pass
            claimed = bool(skill_id and self._is_packet_signet_target_claimed(int(skill_id), a))
            # Condition setup and Epidemic should avoid overlap when two real
            # packet anchors exist.  Other damage signets keep the established
            # execute/damage-first ranking.
            if spread_anchor and len(eligible) > 1:
                return (int(claimed), -score, targets.index(a))
            return (-int(score // 8), int(claimed), -score, targets.index(a))
        eligible.sort(key=rank)
        if spread_anchor and len(eligible) > 1:
            # Best-effort atomic cross-account reservation. Claim the whole
            # adjacent coverage, not just the selected centre.  The other Me/N
            # then prefers a *different uncovered packet*, rather than a
            # different target inside the very same packet. Never wait when
            # all targets are claimed (cleanup/focus must still fire).
            for candidate in eligible:
                if self._is_packet_signet_target_claimed(int(skill_id), candidate):
                    continue
                if self._claim_packet_signet_target(int(skill_id), candidate, lock_ms=1800):
                    for neighbor in self._poison_ks_neighbours(candidate):
                        self._claim_packet_signet_target(int(skill_id), int(neighbor), lock_ms=1800)
                    return int(candidate)
        chosen = int(eligible[0])
        if skill_id:
            # One target, special-focus case or no available unclaimed anchor:
            # fire without introducing any artificial idle time.
            self._claim_packet_signet_target(int(skill_id), chosen)
        return chosen

    def _poison_ks_proc_active(self) -> bool:
        """Only a live Keystone effect justifies a conditionless signet cast."""
        try:
            return bool(Routines.Checks.Effects.HasBuff(
                int(Player.GetAgentID()), int(Keystone_Signet_ID)))
        except Exception:
            return False

    @staticmethod
    def _poison_ks_afflicted(agent_id: int) -> bool:
        try:
            return bool(Agent.IsConditioned(int(agent_id)) or Agent.IsHexed(int(agent_id)))
        except Exception:
            return False

    @staticmethod
    def _poison_ks_unnatural_bonus(agent_id: int) -> int:
        try:
            return 2 if Agent.IsHexed(int(agent_id)) else (1 if Agent.IsEnchanted(int(agent_id)) else 0)
        except Exception:
            return 0

    def _poison_ks_log(self, label: str, skill_id: int, target: int, **details) -> None:
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                'POISON_KEYSTONE_' + str(label), skill_id=int(skill_id),
                target_id=int(target), **details,
            )
        except Exception:
            pass

    def _poison_ks_cast(self, skill_id: int, target: int, label: str, *, signet: bool = False, condition=None):
        if not target or not self._is_skill_strictly_ready(skill_id):
            return False
        # Revalidate mission hard-focus directly before issuing a cast.
        # The target may have switched since the poison packet was constructed.
        bark = int(self._get_elite_priority_target() or 0)
        if bark > 0 and int(target) != bark:
            return False
        success = yield from self.CastSkillIDAndRestoreTarget(
            skill_id=int(skill_id), target_agent_id=int(target),
            extra_condition=(lambda tid=int(target): bool(condition(tid))) if condition else None,
            log=False, aftercast_delay=40,
        )
        if success:
            if signet:
                self._note_non_keystone_signet_cast()
            self._poison_ks_log(label, int(skill_id), int(target))
            return True
        return False

    def _run_poison_keystone_profile(self, snapshot: _KeystoneBarSnapshot, role: str):
        # Combat-only skill AI; party movement and normal PI/HR/ST actors are
        # untouched. Safety/interrupt preemption has already run outside.
        if snapshot.symbolic_setup and snapshot.symbolic_celerity_needed:
            if (yield from self.skills.Mesmer.FastCasting.Symbolic_Celerity()):
                return True
        # With no Symbolic Posture there is ONE stance; apply before Keystone.
        if snapshot.symbolic_setup and not self._active_keystone_mantra_id():
            if self._is_skill_strictly_ready(Mantra_of_Inscriptions_ID):
                if (yield from self.skills.Mesmer.InspirationMagic.Mantra_of_Inscriptions()):
                    self._poison_ks_log('INSCRIPTIONS', Mantra_of_Inscriptions_ID, 0)
                    return True
        if not snapshot.precombat_setup or not snapshot.has_symbolic_celerity:
            return False
        targets = self._poison_ks_targets()
        if not targets:
            return False

        # Raise the Keystone proc on approach/at first engagement, but never
        # cast it blindly on every ready tick and overwrite unspent signets.
        if (not snapshot.has_keystone_signet
                and self._is_skill_strictly_ready(Keystone_Signet_ID)):
            ok = yield from self.skills.Mesmer.FastCasting.Keystone_Signet(allow_existing_effect=True)
            if ok:
                self._note_keystone_reset_cast()
                self._poison_ks_log('PRIME', Keystone_Signet_ID, int(targets[0]), role=role)
                return True

        # Finish before preparing fresh conditions.  On an isolated target
        # or an authoritative <=5% Execute focus, unconditional damage signets
        # are more valuable than first investing in Bleeding + Poison.  This
        # applies equally to a last surviving single foe and group cleanup.
        # For surviving high-HP solo targets the normal seed -> gaze -> poison
        # sequence below still runs. Twisted Bark overrides all of this.
        if role == 'condition_seeder' and not self._get_elite_priority_target():
            split = self._keystone_execute_split_plan()
            finishing = []
            for enemy in targets:
                try:
                    hp = float(Agent.GetHealth(int(enemy)))
                    eligible_finish = (hp <= KEYSTONE_EXECUTE_HP_FRACTION + 1e-7 and (
                        len(targets) == 1 or (split is not None and int(split[0]) == int(enemy))))
                except Exception:
                    eligible_finish = False
                if eligible_finish:
                    finishing.append(int(enemy))
            if finishing:
                for finish_skill in (Unnatural_Signet_ID, Signet_of_Sorrow_ID):
                    if not self._is_skill_strictly_ready(finish_skill):
                        continue
                    if (yield from self._poison_ks_cast(
                            finish_skill, finishing[0], 'SINGLE_OR_EXECUTE_FINISH',
                            signet=True)):
                        return True

        if role == 'condition_seeder':
            # Barbed Signet hits adjacent foes with Bleeding; avoid needlessly
            # sacrificing health when under pressure, and do not target an
            # already fully conditioned cluster while Assassins can DPS.
            if self._is_skill_strictly_ready(Barbed_Signet_ID):
                try:
                    hp = float(Agent.GetHealth(Player.GetAgentID()))
                except Exception:
                    hp = 0
                if hp >= 0.48:
                    target = self._poison_ks_best(
                        targets,
                        lambda a: not self._poison_ks_status(a)[1],
                        extra_score=lambda a: sum(
                            not self._poison_ks_status(x)[1]
                            for x in [a] + self._poison_ks_neighbours(a)
                        ),
                        skill_id=Barbed_Signet_ID,
                        spread_anchor=True,
                    )
                    if (yield from self._poison_ks_cast(
                        Barbed_Signet_ID, target, 'BLEED_SEED', signet=True,
                        condition=lambda aid: not self._poison_ks_status(aid)[1],
                    )):
                        return True

            # Oppressive Gaze only applies poison/weakness to already
            # conditioned enemies. Do not spend 10 Energy on a failed opener.
            if (self._is_skill_strictly_ready(Oppressive_Gaze_ID)
                    and float(Agent.GetEnergy(Player.GetAgentID())) >= 0.24):
                target = self._poison_ks_best(
                    targets,
                    lambda a: self._poison_ks_status(a)[2] and not self._poison_ks_status(a)[0],
                    extra_score=lambda a: sum(
                        self._poison_ks_status(x)[2] and not self._poison_ks_status(x)[0]
                        for x in [a] + self._poison_ks_neighbours(a)
                    ),
                    skill_id=Oppressive_Gaze_ID,
                    spread_anchor=True,
                )
                if (yield from self._poison_ks_cast(Oppressive_Gaze_ID, target, 'POISON_WEAKNESS',
                    condition=lambda aid: self._poison_ks_status(aid)[2] and not self._poison_ks_status(aid)[0])):
                    return True

            # Sustain is not restricted by character HP: the foe must be
            # under half HP. Use if a heal, Energy or useful proc is valuable.
            if self._is_skill_strictly_ready(Signet_of_Lost_Souls_ID):
                pid = int(Player.GetAgentID() or 0)
                try:
                    need = (float(Agent.GetHealth(pid)) < .92
                            or float(Agent.GetEnergy(pid)) < .85)
                except Exception:
                    need = False
                target = self._poison_ks_best(
                    targets,
                    lambda a: float(Agent.GetHealth(a)) < .50,
                    extra_score=lambda a: len(self._poison_ks_neighbours(a)),
                ) if need else 0
                if (yield from self._poison_ks_cast(Signet_of_Lost_Souls_ID, target, 'SUSTAIN', signet=True,
                    condition=lambda aid: float(Agent.GetHealth(aid)) < .50)):
                    return True

            # Existing corpse-aware Signet of Sorrow optimization survives.
            if self._is_skill_strictly_ready(Signet_of_Sorrow_ID):
                corpse_target = int(self._get_sorrow_signet_target(require_corpse=True, claim_target=True) or 0)
                if (yield from self._poison_ks_cast(Signet_of_Sorrow_ID, corpse_target, 'SORROW_CORPSE', signet=True)):
                    return True

            # Signet of Corruption: unconditional AoE. Hex OR CONDITION on
            # any affected foe yields Energy; prioritize the richer packet,
            # but never wait for a hex if energy is comfortable.
            for cid in (Signet_of_Corruption_Kurzick_ID, Signet_of_Corruption_Luxon_ID):
                if int(cid or 0) <= 0 or not self._is_skill_strictly_ready(cid):
                    continue
                target = self._poison_ks_best(
                    targets, lambda a: True,
                    extra_score=lambda a: (
                        12 * sum(self._poison_ks_afflicted(x)
                                for x in [a] + self._poison_ks_neighbours(a))
                        + len(self._poison_ks_neighbours(a))
                    ),
                    skill_id=cid,
                )
                if (yield from self._poison_ks_cast(
                    cid, target, 'CORRUPTION_AOE_ENERGY', signet=True)):
                    return True


        else:
            # Epidemic spreads observed conditions, ideally 3-condition poison
            # clusters. Bleeding-only spread is useful as a secondary primer.
            # Never spend it on one already fully conditioned group.
            if role == 'assassin_spreader' and self._is_skill_strictly_ready(Epidemic_ID):
                def spread_value(a):
                    poisoned, bleeding, conditioned = self._poison_ks_status(a)
                    if not conditioned:
                        return 0
                    receivers = self._poison_ks_neighbours(a)
                    gain = sum(
                        int(poisoned and not self._poison_ks_status(b)[0])
                        + int(bleeding and not self._poison_ks_status(b)[1])
                        for b in receivers
                    )
                    # Status API does not expose all types (e.g. Weakness).
                    # Do not invent those status flags: score only observed gains.
                    return int(gain)
                source = self._poison_ks_best(
                    targets, lambda a: spread_value(a) > 0,
                    extra_score=lambda a: (10 * spread_value(a)
                                            + 3 * int(self._poison_ks_status(a)[0])
                                            + len(self._poison_ks_neighbours(a))),
                    skill_id=Epidemic_ID,
                    spread_anchor=True,
                )
                if (yield from self._poison_ks_cast(Epidemic_ID, source, 'EPIDEMIC',
                        condition=lambda aid: spread_value(aid) > 0)):
                    return True

            # Prefer poison/three conditions for peak own damage.  If no
            # suitable target exists, spend a non-benefiting signet ONLY while
            # a live Keystone proc gives adjacent AoE/interrupt value.
            if self._is_skill_strictly_ready(Signet_of_Toxic_Shock_ID):
                poisoned = self._poison_ks_best(
                    targets, lambda a: self._poison_ks_status(a)[0],
                    extra_score=lambda a: (
                        5 * int(self._is_monk_or_ritualist_enemy(a))
                        + 3 * int(self._is_safe_dangerous_cast(a))
                        + len(self._poison_ks_neighbours(a))
                    ), skill_id=Signet_of_Toxic_Shock_ID)
                target = poisoned
                if not target and self._poison_ks_proc_active():
                    target = self._poison_ks_best(
                        targets, lambda a: len(self._poison_ks_neighbours(a)) > 0,
                        extra_score=lambda a: len(self._poison_ks_neighbours(a)),
                        skill_id=Signet_of_Toxic_Shock_ID)
                if (yield from self._poison_ks_cast(
                    Signet_of_Toxic_Shock_ID, target,
                    'TOXIC_SHOCK' if poisoned else 'TOXIC_KEYSTONE_PROXY',
                    signet=True,
                    condition=lambda aid: self._poison_ks_status(aid)[0]
                    or (self._poison_ks_proc_active() and len(self._poison_ks_neighbours(aid)) > 0),
                )):
                    return True

            if self._is_skill_strictly_ready(Signet_of_Deadly_Corruption_ID):
                conditioned = self._poison_ks_best(
                    targets, lambda a: self._poison_ks_status(a)[2],
                    extra_score=lambda a: (
                        4 * int(self._poison_ks_status(a)[0])
                        + 4 * int(self._poison_ks_status(a)[1])
                        + 4 * int(self._poison_ks_status(a)[2])
                        + 3 * int(self._is_monk_or_ritualist_enemy(a))
                        + len(self._poison_ks_neighbours(a))
                    ), skill_id=Signet_of_Deadly_Corruption_ID)
                target = conditioned
                if not target and self._poison_ks_proc_active():
                    target = self._poison_ks_best(
                        targets, lambda a: len(self._poison_ks_neighbours(a)) > 0,
                        extra_score=lambda a: len(self._poison_ks_neighbours(a)),
                        skill_id=Signet_of_Deadly_Corruption_ID)
                if (yield from self._poison_ks_cast(
                    Signet_of_Deadly_Corruption_ID, target,
                    'DEADLY_CORRUPTION' if conditioned else 'DEADLY_KEYSTONE_PROXY',
                    signet=True,
                    condition=lambda aid: self._poison_ks_status(aid)[2]
                    or (self._poison_ks_proc_active() and len(self._poison_ks_neighbours(aid)) > 0),
                )):
                    return True

            # Weakness/energy-drain primer is also legitimate Keystone AoE
            # pressure on non-poisonable opponents. Keep it ahead of idle.
            if self._is_skill_strictly_ready(Signet_of_Weariness_ID):
                target = self._poison_ks_best(
                    targets, lambda a: True,
                    extra_score=lambda a: len(self._poison_ks_neighbours(a)),
                    skill_id=Signet_of_Weariness_ID,
                )
                if (yield from self._poison_ks_cast(Signet_of_Weariness_ID, target, 'WEARINESS', signet=True)):
                    return True

        # Unnatural: hexed preferred, enchanted next; if neither, best packet
        # member is still a valid unconditional 95-damage signet target.
        if self._is_skill_strictly_ready(Unnatural_Signet_ID):
            target = self._poison_ks_best(
                targets, lambda a: True,
                extra_score=lambda a: (
                    12 * self._poison_ks_unnatural_bonus(a)
                    + len(self._poison_ks_neighbours(a))
                ), skill_id=Unnatural_Signet_ID)
            if (yield from self._poison_ks_cast(
                Unnatural_Signet_ID, target, 'UNNATURAL', signet=True)):
                return True

        if role == 'condition_seeder' and self._is_skill_strictly_ready(Signet_of_Sorrow_ID):
            # Do not override the Bark hard focus with the corpse/execute pool.
            bark = int(self._get_elite_priority_target() or 0)
            target = (bark if bark else int(
                self._get_sorrow_signet_target(require_corpse=False, claim_target=True) or targets[0]
            ))
            if (yield from self._poison_ks_cast(Signet_of_Sorrow_ID, target, 'SORROW', signet=True)):
                return True

        # If no enemy is casting, disruption signets can still proc Keystone
        # on *other* adjacent foes. Never waste one without a live Keystone
        # packet or overlapping enemies. Normal interrupt-first is above us.
        if not snapshot.enemy_casting and self._poison_ks_proc_active():
            for sid in (Signet_of_Distraction_ID, Signet_of_Disruption_ID):
                if not self._is_skill_strictly_ready(sid):
                    continue
                proxy = self._poison_ks_best(
                    targets, lambda a: len(self._poison_ks_neighbours(a)) > 0,
                    extra_score=lambda a: len(self._poison_ks_neighbours(a)),
                    skill_id=sid,
                )
                if (yield from self._poison_ks_cast(
                    sid, proxy, 'INTERRUPT_KEYSTONE_PROXY', signet=True,
                    condition=lambda a: self._poison_ks_proc_active()
                    and len(self._poison_ks_neighbours(a)) > 0)):
                    return True

        # Existing coordinated interrupt handlers run in the parent before
        # this method. If every meaningful signet is spent/unusable, a ready
        # Keystone resets them; conditional Toxic Shock cannot stall resets.
        if self._is_skill_strictly_ready(Keystone_Signet_ID):
            any_recharging = False
            for slot in range(1, 9):
                sid = int(SkillBar.GetSkillIDBySlot(slot) or 0)
                if sid <= 0 or sid == int(Keystone_Signet_ID):
                    continue
                try:
                    if Skill.Flags.IsSignet(sid) and not Routines.Checks.Skills.IsSkillSlotReady(slot):
                        any_recharging = True
                        break
                except Exception:
                    continue
            if any_recharging:
                success = yield from self.skills.Mesmer.FastCasting.Keystone_Signet(allow_existing_effect=True)
                if success:
                    self._note_keystone_reset_cast()
                    self._poison_ks_log('RESET', Keystone_Signet_ID, int(targets[0]), role=role)
                    return True
        # Poison profile: all its offensive signets and Keystone reset had
        # first refusal; renew long-lived buffs only in the remaining gap.
        if snapshot.symbolic_setup:
            if (yield from self._cast_keystone_buff_upkeep(
                Symbolic_Celerity_ID, 'poison_free_window_celerity_20s'
            )):
                return True
            mantra_id = int(self._get_equipped_keystone_mantra_id() or 0)
            if mantra_id and (yield from self._cast_keystone_buff_upkeep(
                mantra_id, 'poison_free_window_mantra_20s'
            )):
                return True
        return False

    def _run_local_skill_logic(self):
        self._log_keystone_controller_reliability()
        refresh_aoe_danger_zones()
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.verify_interrupt_outcomes()
        except Exception:
            pass

        # Read-only profiling. No combat decision depends on these calls.
        self._track_sod_ready_idle()
        self._track_primary_signet_idle()
        self._track_mimicry_soj_window()
        self._track_keystone_mantra_state()
        # No asynchronous HP probe polling here. Mantra telemetry is passive and
        # piggybacks on the existing signet cast profile only.
        # Confirmed/provisionally committed danger always beats an interrupt
        # approach.  Normally the approach claim exists before the 90% pre-arm
        # point, so this branch is reached only when the interrupt path failed.
        if avoid_active_aoe_if_needed(role="keystone", allow_actions_at_safe_hold=True):
            self._clear_interrupt_approach(
                reason="aoe_escape_override", release_claim=True, log_abort=True
            )
            return True

        # No interrupt-approach movement/reservation in this build. Interrupts
        # are purely opportunistic: the signet must already be ready and the
        # caster must already be in usable range.
        safe_aoe_hold = is_aoe_escape_safe_hold_active(role="keystone")
        self._clear_interrupt_approach(
            reason="opportunistic_only_no_approach",
            release_claim=True,
            log_abort=False,
        )

        if not Routines.Checks.Skills.CanCast():
            self._log_rotation_stall("global_can_cast_false")
            return False

        snapshot = self._get_bar_snapshot()
        # Pre-pull: Celerity -> (Mantra where no Posture) -> Keystone.
        # This must happen before any precombat Keystone prime.
        if (yield from self._cast_early_native_keystone_setup(snapshot)):
            return True
        if (yield from self._cast_keystone_precombat_buffs(snapshot)):
            return True
        if not snapshot.precombat_setup:
            self._precombat_keystone_primed = False
            self._non_keystone_signet_cast_since_last_keystone = False

        # POISON_KS_V1: separate profile, native Keystone, no Mimicry casts.
        # The old eSurge / KeySoJWay controller stays intact for other bars.
        poison_role = self._poison_ks_role()
        if poison_role:
            # Missing/removed Celerity is core damage dependency, not a
            # 20-second optional refresh. This recovery is ready-now only.
            if (snapshot.symbolic_setup and snapshot.symbolic_celerity_needed
                and (yield from self._cast_keystone_buff_upkeep(
                    Symbolic_Celerity_ID, 'poison_missing_enchantment'
                ))):
                return True
            # Bark always outranks unrelated caster interrupts, otherwise the
            # interrupt-first path would bypass the poison hard-target list.
            # Non-bark combat still uses all existing interrupt coordination.
            bark_is_priority = bool(self._get_elite_priority_target())
            if not bark_is_priority and snapshot.enemy_casting and (yield from self._cast_coordinated_signet_of_distraction(snapshot)):
                return True
            if not bark_is_priority and snapshot.enemy_casting and (yield from self._cast_signet_of_disruption_interrupt_first(snapshot)):
                return True
            return (yield from self._run_poison_keystone_profile(snapshot, poison_role))

        player_energy_pct = float(Agent.GetEnergy(Player.GetAgentID()))
        should_reset_signets = self._should_reset_signets_with_keystone(snapshot)
        should_cast_keystone = self._should_cast_keystone_signet(snapshot)
        # Aggressive packet-pressure policy:
        # Do not require an enemy to already be inside the old spellcast snapshot
        # before spending ready signets.  A real nearby fight is enough.  This
        # removes the 2-3s ready-idle windows seen in HM Urgoz while preserving
        # the absolute interrupt-first branch above all damage use.
        combat_pressure = bool(
            snapshot.enemy_in_spellcast
            or snapshot.enemy_casting
            or self.IsInAggro()
            or self.IsCloseToAggro()
        )
        mantra_gate = bool(self._should_gate_signet_spend_for_mantra(
            snapshot, should_reset_signets, should_cast_keystone
        ))
        # EXP36 Stage 3 / K01: ready offensive signets must never be parked
        # behind a missing Mantra in real combat, including 9-12 player teams.
        # Stage 2 still retained the old large-party mantra_gate here, which
        # produced a live Urgoz HM case where copied SoJ/Unnatural were ready
        # with legal targets for several seconds but could not enter the damage
        # lane until Mantra/setup recovered. Mantra remains fully supported:
        # the full-throughput lane gets first refusal, then the existing free-
        # window Mantra recovery runs only when no offensive signet can fire.
        # This changes no out-of-combat behavior and does not bypass E06.
        mantra_zero_wait_override = bool(
            combat_pressure
            and mantra_gate
            and not self._small_party_focus_mode()
        )
        can_spend_non_keystone_signets = bool(combat_pressure)
        if not combat_pressure:
            self._disruption_opening_spent = False
        single_cleanup_target = self._single_target_cleanup_target()
        single_target_cleanup = bool(single_cleanup_target)

        # Variant 2: 5 native Me/N Keystone accounts coordinate Signet of
        # Distraction. It remains unused if no genuine interrupt is available.
        if snapshot.enemy_casting and (yield from self._cast_coordinated_signet_of_distraction(snapshot)):
            return True

        # v27: if Arcane Mimicry currently left this Mesmer with a ready SoJ,
        # it may briefly peel to a live dangerous/cleanup-healer cast. Faster PI
        # Mesmer interrupts still win the shared election; nobody waits for SoJ.
        if snapshot.enemy_casting and (yield from self._cast_copied_soj_opportunistic_interrupt(snapshot)):
            return True

        # Optional Cry of Frustration variant. When a Keystone Mesmer equips Cry
        # instead of Corruption, high-value enemy casts get first refusal before
        # the normal signet throughput lane. Cross-account packet reservations
        # prevent two Cry holders from firing into the same AoE packet, while a
        # second spatially separate dangerous packet can be interrupted in parallel.
        if snapshot.enemy_casting and (yield from self._cast_coordinated_cry_of_frustration()):
            return True

        # EXP36 Stage 8 / Astra E14: the proxy planner already existed in the
        # codebase but the full-throughput runtime never called it.  Activate it
        # only while the Keystone effect is live and a registered dangerous cast
        # is currently observable. Dedicated direct CC keeps first refusal:
        # local Cry/SoJ paths above run first, and the shared interrupt election
        # gives PI/Cry/Power-Drain class skills a shorter claim delay than proxy
        # signets. If no legal adjacent proxy exists, this returns immediately
        # and normal signet throughput continues with zero reservation/wait.
        if (
            snapshot.enemy_casting
            and snapshot.has_keystone_signet
            and (yield from self._cast_keystone_proxy_interrupt(snapshot))
        ):
            return True

        # EXP20: after a successful Keystone reset, Mantra gets exactly one
        # immediate chance before the offensive signet packet. If it is not
        # ready right now, this returns False and damage continues this tick.
        if can_spend_non_keystone_signets:
            if (yield from self._cast_post_keystone_mantra_if_immediate(snapshot)):
                return True

        # EXP36/E06: immediate recovery for the two effects that make the
        # Keystone packet materially stronger. This is deliberately a
        # *ready-now* fast path: it never waits for a setup skill and therefore
        # cannot recreate the old long ready-idle problem. Arcane Mimicry is
        # skipped here because copied SoJ is a bonus, not a prerequisite for
        # restoring the core Celerity/Keystone engine.
        if combat_pressure:
            if (
                snapshot.symbolic_celerity_needed
                and snapshot.symbolic_setup
                and self.IsSkillEquipped(Symbolic_Celerity_ID)
                and self.CanCastSkillID(Symbolic_Celerity_ID)
            ):
                if (yield from self._cast_keystone_buff_upkeep(
                    Symbolic_Celerity_ID, 'combat_missing_enchantment'
                )):
                    try:
                        from Py4GWCoreLib.Builds.Skills import CombatDebug
                        CombatDebug.log_event(
                            "KEYSTONE_REQUIRED_EFFECT_RECOVERY",
                            effect="symbolic_celerity",
                            policy="exp36_e06_ready_now_no_wait",
                        )
                    except Exception:
                        pass
                    return True

            if snapshot.keystone_signet_needed and snapshot.precombat_setup:
                # Preserve Posture -> Keystone when Posture can be applied right
                # now. If Posture is not equipped, Keystone itself may recover
                # immediately. If either action is unavailable, fall through to
                # normal signet pressure rather than waiting.
                if (
                    self.IsSkillEquipped(Symbolic_Posture_ID)
                    and snapshot.symbolic_posture_needed
                    and self.CanCastSkillID(Symbolic_Posture_ID)
                    and self.CanCastSkillID(Keystone_Signet_ID)
                ):
                    if (yield from self.skills.Mesmer.FastCasting.Symbolic_Posture()):
                        try:
                            from Py4GWCoreLib.Builds.Skills import CombatDebug
                            CombatDebug.log_event(
                                "KEYSTONE_REQUIRED_EFFECT_RECOVERY",
                                effect="symbolic_posture_before_keystone",
                                policy="exp36_e06_ready_now_no_wait",
                            )
                        except Exception:
                            pass
                        return True
                elif (
                    not self.IsSkillEquipped(Symbolic_Posture_ID)
                    and self.CanCastSkillID(Keystone_Signet_ID)
                ):
                    if (yield from self.skills.Mesmer.FastCasting.Keystone_Signet(allow_existing_effect=True)):
                        self._note_keystone_reset_cast()
                        try:
                            from Py4GWCoreLib.Builds.Skills import CombatDebug
                            CombatDebug.log_event(
                                "KEYSTONE_REQUIRED_EFFECT_RECOVERY",
                                effect="keystone_without_posture",
                                policy="exp36_e06_ready_now_no_wait",
                            )
                        except Exception:
                            pass
                        return True
                elif snapshot.has_symbolic_posture and self.CanCastSkillID(Keystone_Signet_ID):
                    if (yield from self.skills.Mesmer.FastCasting.Keystone_Signet(allow_existing_effect=True)):
                        self._note_keystone_reset_cast()
                        try:
                            from Py4GWCoreLib.Builds.Skills import CombatDebug
                            CombatDebug.log_event(
                                "KEYSTONE_REQUIRED_EFFECT_RECOVERY",
                                effect="keystone_after_existing_posture",
                                policy="exp36_e06_ready_now_no_wait",
                            )
                        except Exception:
                            pass
                        return True

        # FULL-THROUGHPUT TEST:
        # Signet of Disruption interrupt intelligence is intentionally disabled.
        # is treated as ordinary pressure and is never held for an enemy cast.
        # The first ready foe-targeted signet in the fixed priority is fired now.
        self._log_disruption_team_availability()
        # The 20s window remains opportunistic. Once the verified remaining
        # Mantra is <=6s (or it was removed), use a currently READY stance now,
        # before the next ordinary damage signet. Do not reserve/queue it and
        # never make damage wait for its recharge. Important interrupts and
        # recovery of the Keystone/Celerity engine already ran above.
        if can_spend_non_keystone_signets:
            if (yield from self._cast_critical_keystone_mantra(snapshot)):
                return True
            if (yield from self._cast_full_throughput_signet_spam(snapshot)):
                if mantra_zero_wait_override:
                    try:
                        from Py4GWCoreLib.Builds.Skills import CombatDebug
                        CombatDebug.log_event(
                            "KEYSTONE_MANTRA_ZERO_WAIT_OFFENSE",
                            mantra_gate_would_have_blocked_stage2=True,
                            party_mode="large_party_9_12",
                            policy="exp36_stage3_ready_offense_before_missing_mantra",
                        )
                    except Exception:
                        pass
                return True

            # KAY: already-readied damage had first refusal. Renew Celerity in
            # a free gap once its VERIFIED timer reaches <=20s (or if stripped).
            if snapshot.symbolic_setup and (yield from self._cast_keystone_buff_upkeep(
                Symbolic_Celerity_ID, 'combat_free_window_celerity_20s'
            )):
                return True

            # EXP11: free-window Mantra recovery.  The complete EXP2 throughput
            # lane above has already inspected every ready offensive signet and
            # found nothing it can legally fire this tick.  Only in that truly
            # free window may Mantra be refreshed.  It is never a prerequisite
            # for damage, never creates a wait state, and is skipped whenever a
            # Symbolic Posture -> Keystone action is due.
            if not should_reset_signets and not should_cast_keystone:
                if (yield from self._cast_keystone_mantra_setup(
                    snapshot, reason="exp11_free_window_after_damage_probe"
                )):
                    return True


        # DoA-only optional lightbringer bar support. Existing signet offense,
        # interrupts, critical upkeep and Mantra have already had first refusal.
        # Gaze is a skill, not a signet: it cannot consume a Keystone AoE proc.
        # Lightbringer Signet targets self: it also cannot trigger Keystone AoE.
        if not safe_aoe_hold and combat_pressure:
            if (yield from LBO.try_gaze(self, role='keystone', min_enemies=2)):
                return True
            if (yield from LBO.try_signet(self, role='keystone', energy_threshold=0.35)):
                return True

        if self.IsSkillEquipped(Animate_Flesh_Golem_ID) and (yield from self.skills.Necromancer.DeathMagic.Animate_Flesh_Golem()):
            return True

        if self.IsSkillEquipped(Animate_Bone_Fiend_ID) and (yield from self.skills.Necromancer.DeathMagic.Animate_Bone_Fiend()):
            return True

        if self.IsSkillEquipped(Animate_Bone_Horror_ID) and (yield from self.skills.Necromancer.DeathMagic.Animate_Bone_Horror()):
            return True

        if self.IsSkillEquipped(Animate_Bone_Minions_ID) and (yield from self.skills.Necromancer.DeathMagic.Animate_Bone_Minions()):
            return True

        if self.IsSkillEquipped(Animate_Shambling_Horror_ID) and (yield from self.skills.Necromancer.DeathMagic.Animate_Shambling_Horror()):
            return True

        if self.IsSkillEquipped(Animate_Vampiric_Horror_ID) and (yield from self.skills.Necromancer.DeathMagic.Animate_Vampiric_Horror()):
            return True

        if (yield from self.skills.Monk.SmitingPrayers.Smite_Hex(min_priority=HexRemovalPriority.HIGH)):
            return True

        if (
            snapshot.symbolic_setup
            and snapshot.symbolic_celerity_needed
            and (yield from self.skills.Mesmer.FastCasting.Symbolic_Celerity())
        ):
            return True

        # Air of Superiority is pre-burst setup for Keystone Mesmers. Cast it
        # before Arcane Mimicry / Keystone / offensive signets, but do not make
        # it part of the signet-reset accounting.
        if (yield from self._cast_air_of_superiority_setup(snapshot)):
            return True

        # Symbolic Posture is a setup stance and does not end until a signet is
        # successfully activated. Cast it shortly before the Arcane Mimicry /
        # Keystone window so the first Keystone Signet benefits without wasting
        # the copied SoJ timer while running in.
        if (
            snapshot.precombat_setup
            and self.IsSkillEquipped(Symbolic_Posture_ID)
            and snapshot.symbolic_posture_needed
            and (
                self.CanCastSkillID(Keystone_Signet_ID)
                or (self._is_soj_keystone_donor_mode() and self.CanCastSkillID(Arcane_Mimicry_ID))
            )
        ):
            if (yield from self.skills.Mesmer.FastCasting.Symbolic_Posture()):
                return True

        if (yield from self._cast_arcane_mimicry_for_signet_of_judgment(snapshot)):
            return True

        if should_reset_signets and snapshot.has_symbolic_posture:
            if (yield from self.skills.Mesmer.FastCasting.Keystone_Signet(allow_existing_effect=True)):
                self._note_keystone_reset_cast()
                return True

        if (
            should_reset_signets
            and self.IsSkillEquipped(Symbolic_Posture_ID)
            and snapshot.symbolic_posture_needed
            and self.CanCastSkillID(Keystone_Signet_ID)
        ):
            if (yield from self.skills.Mesmer.FastCasting.Symbolic_Posture()):
                return True

        if should_reset_signets and (
            yield from self.skills.Mesmer.FastCasting.Keystone_Signet(allow_existing_effect=True)
        ):
            self._note_keystone_reset_cast()
            return True

        if snapshot.has_symbolic_posture and should_cast_keystone:
            if (yield from self.skills.Mesmer.FastCasting.Keystone_Signet()):
                self._note_keystone_reset_cast()
                return True

        # Mantra A/B setup. Do this only after any pending Symbolic Posture ->
        # Keystone action. Both Mantras are stances, so casting one before
        # Symbolic Posture would immediately throw the Mantra away.
        if not should_reset_signets and not should_cast_keystone:
            if (yield from self._cast_keystone_mantra_setup(snapshot)):
                return True

        # Hex Eater Signet is ally-targeted, so it is reserved for real hex removal.
        # It must not be used as a Keystone filler: Keystone's AoE damage/interrupt
        # only applies to signets that target a foe, and a ready Hex Eater with no
        # hexed ally should not delay the Keystone reset cycle.
        if (
            can_spend_non_keystone_signets
            and self.IsSkillEquipped(Hex_Eater_Signet_ID)
            and self._has_hexed_ally_for_hex_eater()
        ):
            if (yield from self.skills.Mesmer.InspirationMagic.Hex_Eater_Signet()):
                self._note_non_keystone_signet_cast()
                return True

        if self.IsSkillEquipped(Breath_of_the_Great_Dwarf_ID) and (yield from self.skills.Any.NoAttribute.Breath_of_the_Great_Dwarf()):
            return True

        if self.IsSkillEquipped(Blood_Ritual_ID) and (yield from self.skills.Necromancer.BloodMagic.Blood_Ritual()):
            return True

        if not snapshot.precombat_setup:
            self._log_rotation_stall("end_of_combat_rotation_no_action", snapshot)
            return False

        if (
            should_cast_keystone
            and self.IsSkillEquipped(Symbolic_Posture_ID)
            and snapshot.symbolic_posture_needed
            and self.CanCastSkillID(Keystone_Signet_ID)
        ):
            if (yield from self.skills.Mesmer.FastCasting.Symbolic_Posture()):
                return True

        if should_cast_keystone and (yield from self.skills.Mesmer.FastCasting.Keystone_Signet()):
            self._note_keystone_prime_cast(snapshot)
            return True

        if self.IsSkillEquipped(Death_Nova_ID) and (yield from self.skills.Necromancer.DeathMagic.Death_Nova()):
            return True

        if (
            can_spend_non_keystone_signets
            and (yield from self._cast_copied_signet_of_judgment(snapshot))
        ):
            return True

        # Aggressive damage-signets: once setup, emergency interrupts and copied
        # SoJ have been handled, spend every ready foe-targeted damage signet
        # immediately. Do not wait for a caster, an attacker, a perfect packet,
        # or a free distribution claim. Claims remain a preferred target hint,
        # but they are never allowed to create combat idle time.
        if can_spend_non_keystone_signets:
            aggressive_signet_order = (
                Unnatural_Signet_ID,
                Bane_Signet_ID,
                Castigation_Signet_ID,
                Signet_of_Rage_ID,
                Signet_of_Mystic_Wrath_ID,
            )
            for aggressive_skill_id in aggressive_signet_order:
                if aggressive_skill_id <= 0:
                    continue
                if not self.IsSkillEquipped(aggressive_skill_id):
                    continue
                if not self._is_skill_strictly_ready(aggressive_skill_id):
                    continue

                aggressive_target_id, zero_idle_reason = self._get_zero_idle_damage_target(
                    int(aggressive_skill_id)
                )
                if aggressive_target_id <= 0:
                    continue

                did_cast = yield from self.CastSkillIDAndRestoreTarget(
                    skill_id=int(aggressive_skill_id),
                    target_agent_id=int(aggressive_target_id),
                    log=False,
                    aftercast_delay=40,
                )
                if did_cast:
                    self._note_non_keystone_signet_cast()
                    try:
                        from Py4GWCoreLib.Builds.Skills import CombatDebug
                        CombatDebug.log_event(
                            "KEYSTONE_SIGNET_FAST_CAST",
                            skill_id=int(aggressive_skill_id),
                            target_id=int(aggressive_target_id),
                            reason=str(zero_idle_reason),
                            enemy_count=int(len(self._get_zero_idle_enemy_targets())),
                            policy="damage_first_no_reserve",
                        )
                    except Exception:
                        pass
                    return True

        # Exactly the Keystone Mesmers that equip Mistrust use this path.
        # A dedicated team lock spreads the two casts across different offensive
        # casters inside the same selected cluster. It does not consume the
        # Keystone signet-reset cycle because Mistrust is a spell, not a signet.
        if (
            self.IsSkillEquipped(Mistrust_ID)
            and self._is_skill_strictly_ready(Mistrust_ID)
        ):
            # Damage signets above are never delayed for Mistrust. Once they are
            # spent/unavailable, use Mistrust intelligently: active caster first,
            # otherwise a known high-value caster likely to cast soon.
            mistrust_target, mistrust_reason = self._get_mistrust_target()
            if mistrust_target and (yield from self.CastSkillIDAndRestoreTarget(
                skill_id=Mistrust_ID,
                target_agent_id=mistrust_target,
                log=False,
                aftercast_delay=120,
            )):
                try:
                    from Py4GWCoreLib.Builds.Skills import MistrustTracker
                    MistrustTracker.register_cast(
                        source_id=int(Player.GetAgentID() or 0),
                        target_id=int(mistrust_target),
                        duration_ms=int(MISTRUST_TARGET_LOCK_MS),
                    )
                except Exception:
                    pass
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "KEYSTONE_MISTRUST_CAST",
                        target_id=int(mistrust_target),
                        reason=str(mistrust_reason),
                        policy="distributed_meaningful_only",
                    )
                except Exception:
                    pass
                return True

        # V26.5 single-target cleanup:
        # When exactly one relevant foe remains, do not wait for AoE packet or
        # enemy-casting conditions before spending safe foe-targeted damage
        # signets. Conditional control signets still keep their own requirements.
        if can_spend_non_keystone_signets and single_target_cleanup:
            cleanup_skill_order = (
                Unnatural_Signet_ID,
                Signet_of_Sorrow_ID,
                Signet_of_Corruption_Kurzick_ID,
                Signet_of_Corruption_Luxon_ID,
                Signet_of_Weariness_ID,
            )
            for cleanup_skill_id in cleanup_skill_order:
                if cleanup_skill_id <= 0:
                    continue
                if not self.IsSkillEquipped(cleanup_skill_id):
                    continue
                if not self.CanCastSkillID(cleanup_skill_id):
                    continue
                did_cast = yield from self.CastSkillIDAndRestoreTarget(
                    skill_id=cleanup_skill_id,
                    target_agent_id=int(single_cleanup_target),
                    log=False,
                    aftercast_delay=40,
                )
                if did_cast:
                    self._note_non_keystone_signet_cast()
                    try:
                        from Py4GWCoreLib.Builds.Skills import CombatDebug
                        CombatDebug.log_event(
                            "KEYSTONE_SINGLE_TARGET_CLEANUP",
                            target_id=int(single_cleanup_target),
                            skill_id=int(cleanup_skill_id),
                        )
                    except Exception:
                        pass
                    return True

        if (
            can_spend_non_keystone_signets
            and self.IsSkillEquipped(Tryptophan_Signet_ID)
            and snapshot.enemy_in_spellcast
        ):
            target_agent_id = self._get_tryptophan_signet_target()
            if target_agent_id and (yield from self.CastSkillIDAndRestoreTarget(skill_id=Tryptophan_Signet_ID, target_agent_id=target_agent_id, log=False, aftercast_delay=250)):
                self._note_non_keystone_signet_cast()
                return True

        # Signet of Sorrow is strongest when the target is near a corpse/dead pet,
        # because it can instantly recharge.  Prefer corpse-boosted packet targets
        # before Corruption; if no corpse target exists, spend Corruption first
        # for reliable opening AoE, then use Sorrow as fallback filler later.
        if (
            can_spend_non_keystone_signets
            and snapshot.enemy_in_spellcast
            and self.IsSkillEquipped(Signet_of_Sorrow_ID)
        ):
            target_agent_id = self._get_sorrow_signet_target(require_corpse=True, claim_target=True)
            if target_agent_id and (yield from self.CastSkillIDAndRestoreTarget(skill_id=Signet_of_Sorrow_ID, target_agent_id=target_agent_id, log=False, aftercast_delay=250)):
                self._note_non_keystone_signet_cast()
                try:
                    from Py4GWCoreLib.Builds.Skills import Telemetry
                    Telemetry.count("keystone.sorrow_cast")
                except Exception:
                    pass
                return True

        # Optional tuning: aggressive Sorrow mode spends Sorrow as general
        # Keystone filler before Corruption even when no corpse is confirmed.
        # Default remains normal to preserve reliable Corruption opening AoE.
        try:
            from Py4GWCoreLib.Builds.Skills import SimplePowerSettings
            _sorrow_aggressive = str(SimplePowerSettings.get_value("keystone_sorrow_priority", "normal")).lower() == "aggressive"
        except Exception:
            _sorrow_aggressive = False
        if (
            _sorrow_aggressive
            and can_spend_non_keystone_signets
            and snapshot.enemy_in_spellcast
            and self.IsSkillEquipped(Signet_of_Sorrow_ID)
        ):
            target_agent_id = self._get_sorrow_signet_target(require_corpse=False, claim_target=True)
            if target_agent_id and (yield from self.CastSkillIDAndRestoreTarget(skill_id=Signet_of_Sorrow_ID, target_agent_id=target_agent_id, log=False, aftercast_delay=250)):
                self._note_non_keystone_signet_cast()
                try:
                    from Py4GWCoreLib.Builds.Skills import Telemetry
                    Telemetry.count("keystone.sorrow_cast")
                    Telemetry.count("keystone.sorrow_aggressive_cast")
                except Exception:
                    pass
                return True

        if (
            can_spend_non_keystone_signets
            and snapshot.enemy_in_spellcast
            and self.IsSkillEquipped(Signet_of_Corruption_Kurzick_ID)
        ):
            target_agent_id = self._get_packet_signet_target(Signet_of_Corruption_Kurzick_ID, claim_target=True)
            if target_agent_id and (yield from self.CastSkillIDAndRestoreTarget(skill_id=Signet_of_Corruption_Kurzick_ID, target_agent_id=target_agent_id, log=False, aftercast_delay=250)):
                self._note_non_keystone_signet_cast()
                try:
                    from Py4GWCoreLib.Builds.Skills import Telemetry
                    Telemetry.count("keystone.corruption_cast")
                except Exception:
                    pass
                return True

        if (
            can_spend_non_keystone_signets
            and snapshot.enemy_in_spellcast
            and self.IsSkillEquipped(Signet_of_Corruption_Luxon_ID)
        ):
            target_agent_id = self._get_packet_signet_target(Signet_of_Corruption_Luxon_ID, claim_target=True)
            if target_agent_id and (yield from self.CastSkillIDAndRestoreTarget(skill_id=Signet_of_Corruption_Luxon_ID, target_agent_id=target_agent_id, log=False, aftercast_delay=250)):
                self._note_non_keystone_signet_cast()
                try:
                    from Py4GWCoreLib.Builds.Skills import Telemetry
                    Telemetry.count("keystone.corruption_cast")
                except Exception:
                    pass
                return True

        if (
            can_spend_non_keystone_signets
            and snapshot.enemy_in_spellcast
            and self.IsSkillEquipped(Signet_of_Weariness_ID)
        ):
            target_agent_id = self._get_packet_signet_target(Signet_of_Weariness_ID, claim_target=True) or self._get_offensive_signet_target()
            if target_agent_id and (yield from self.CastSkillIDAndRestoreTarget(skill_id=Signet_of_Weariness_ID, target_agent_id=target_agent_id, log=False, aftercast_delay=250)):
                self._note_non_keystone_signet_cast()
                return True

        if (
            can_spend_non_keystone_signets
            and snapshot.enemy_in_spellcast
            and self.IsSkillEquipped(Signet_of_Sorrow_ID)
        ):
            target_agent_id = self._get_sorrow_signet_target(require_corpse=False, claim_target=True)
            if target_agent_id and (yield from self.CastSkillIDAndRestoreTarget(skill_id=Signet_of_Sorrow_ID, target_agent_id=target_agent_id, log=False, aftercast_delay=250)):
                self._note_non_keystone_signet_cast()
                try:
                    from Py4GWCoreLib.Builds.Skills import Telemetry
                    Telemetry.count("keystone.sorrow_cast")
                except Exception:
                    pass
                return True

        if player_energy_pct >= 0.50 and (yield from self.skills.Monk.SmitingPrayers.Smite_Hex(min_priority=HexRemovalPriority.MEDIUM)):
            return True

        if (
            can_spend_non_keystone_signets
            and snapshot.enemy_in_spellcast
        ):
            target_agent_id = self._get_offensive_signet_target()
            if target_agent_id and (yield from self.CastSkillIDAndRestoreTarget(skill_id=Unnatural_Signet_ID, target_agent_id=target_agent_id, log=False, aftercast_delay=250)):
                self._note_non_keystone_signet_cast()
                return True

        if (
            can_spend_non_keystone_signets
            and self.IsSkillEquipped(Signet_of_Clumsiness_ID)
            and snapshot.attacking_enemy_in_spellcast
        ):
            target_agent_id = self._get_attacking_power_cluster_target()
            if target_agent_id and (yield from self.CastSkillIDAndRestoreTarget(skill_id=Signet_of_Clumsiness_ID, target_agent_id=target_agent_id, log=False, aftercast_delay=250)):
                self._note_non_keystone_signet_cast()
                return True

        if (
            can_spend_non_keystone_signets
            and snapshot.enemy_in_spellcast
            and self.IsSkillEquipped(Castigation_Signet_ID)
            and self.CanCastSkillID(Castigation_Signet_ID)
        ):
            target_agent_id = self._get_monk_damage_signet_target(Castigation_Signet_ID, claim_target=True)
            if target_agent_id and (yield from self.CastSkillIDAndRestoreTarget(skill_id=Castigation_Signet_ID, target_agent_id=target_agent_id, log=False, aftercast_delay=250)):
                self._note_non_keystone_signet_cast()
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "KEYSTONE_MONK_SIGNET_CAST",
                        skill_id=int(Castigation_Signet_ID),
                        target_id=int(target_agent_id),
                        target_attacking=bool(Agent.IsAttacking(int(target_agent_id))),
                        fallback_non_attacker=not bool(Agent.IsAttacking(int(target_agent_id))),
                    )
                except Exception:
                    pass
                return True

        if (
            can_spend_non_keystone_signets
            and snapshot.enemy_in_spellcast
            and self.IsSkillEquipped(Bane_Signet_ID)
            and self.CanCastSkillID(Bane_Signet_ID)
        ):
            target_agent_id = self._get_monk_damage_signet_target(Bane_Signet_ID, claim_target=True)
            if target_agent_id and (yield from self.CastSkillIDAndRestoreTarget(skill_id=Bane_Signet_ID, target_agent_id=target_agent_id, log=False, aftercast_delay=250)):
                self._note_non_keystone_signet_cast()
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "KEYSTONE_MONK_SIGNET_CAST",
                        skill_id=int(Bane_Signet_ID),
                        target_id=int(target_agent_id),
                        target_attacking=bool(Agent.IsAttacking(int(target_agent_id))),
                        fallback_non_attacker=not bool(Agent.IsAttacking(int(target_agent_id))),
                    )
                except Exception:
                    pass
                return True

        return False
